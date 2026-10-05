"""Redo only a failed native disk layout under the explicitly approved disk policy."""

import argparse
from concurrent.futures import ThreadPoolExecutor, wait
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import struct
import time
import traceback

import finish_diskann_build as finish


shared = finish.shared
HERE = Path(__file__).resolve().parent
PURPOSE = "saved-original-graph-layout-continuation"


def saved_graph(context, handoff):
    shared.require(handoff["user_authorized"] is True and handoff["native_builder_exit_status"] == 0
                   and handoff["graph_rebuilt"] is False
                   and handoff["saved_vectors"]["exact_original_vector_samples"] is True
                   and handoff["saved_vectors"]["sample_count"] == min(context.rows, 4096),
                   "Continuation requires the authenticated successful original graph")
    records = handoff["graph_inputs"]
    names = {"graph", "graph_labels.txt", "graph_labels_map.txt", "graph_labels_to_medoids.txt"}
    shared.require(len(records) == len(names) and {Path(item["path"]).name for item in records} == names,
                   "Saved original graph inventory is incomplete")
    for item in records:
        shared.require(shared.original_build_identity(item["path"]) == item, "Saved original graph changed")
    graph = next(Path(item["path"]) for item in records if Path(item["path"]).name == "graph")
    shared.require(all(Path(item["path"]).parent == graph.parent for item in records),
                   "Saved graph sidecars do not share their authenticated directory")
    with graph.open("rb") as stream:
        size, width, medoid, frozen = struct.unpack("<QIIQ", stream.read(24))
    shared.require(size == graph.stat().st_size and 0 < width <= context.config.section("DiskANN").getint("R")
                   and medoid < context.rows and frozen == 0, "Invalid saved original graph header")
    return graph.parent, width


def expected_layout_bytes(context, width):
    nodes = 4096 // (context.dimension + 4 * (width + 1))
    shared.require(nodes > 0, "Invalid native layout geometry")
    return 4096 * (1 + (context.rows + nodes - 1) // nodes)


def partial_record(context, width):
    path = Path(str(context.prefix) + "_disk.index")
    before = path.lstat()
    shared.require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                   and 4096 <= before.st_size < expected_layout_bytes(context, width),
                   "Only a singly owned incomplete layout may be discarded")
    record = shared.file_record(path)
    with path.open("rb") as stream:
        first = stream.read(4096)
        stream.seek(before.st_size - 4096)
        last = stream.read(4096)
    shared.require(first == bytes(4096), "Refusing to discard an initialized native index header")
    after = path.lstat()
    shared.require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                   == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                   "Partial layout changed during authentication")
    shared.verify_records([record])
    return {
        "identity": record, "allocated_bytes": before.st_blocks * 512, "links": before.st_nlink,
        "expected_complete_bytes": expected_layout_bytes(context, width),
        "zero_header": True, "last_4096_sha256": hashlib.sha256(last).hexdigest(),
    }


def validate_previous(config, context):
    previous = config.path_value("Continuation", "PreviousLayoutDirectory")
    campaign = config.path_value("Continuation", "PreviousCampaignDirectory")
    shared.require(previous.parent == context.root, "Previous layout belongs to another original build")
    campaign_config = shared.Config(campaign / "config/benchmark.ini")
    guarded = campaign_config.path_value("Run", "BuildDirectory")
    shared.require(guarded.parent == previous, "Failed campaign and guarded layout authorities differ")
    registration = shared.read_json(guarded / "registration.json")
    shared.require(registration["layout_directory"] == str(previous)
                   and registration["config"] == shared.identity(context.config.path, True),
                   "Previous guarded registration changed")
    shared.verify_identities(registration["frozen"] + registration["layout_sources"])
    shared.verify_records(registration["protected"])
    for directory in (previous, guarded, campaign):
        status = shared.read_json(directory / "status.json")
        shared.require(status["state"] == "failed" and (directory / "failure.json").is_file(),
                       "Every previous continuation controller must have stopped")
    expected = registration["layout_controller"]
    shared.require(shared.process_identity(expected["pid"]) != expected,
                   "Previous layout controller is still running")
    campaign_registration = shared.read_json(campaign / "manifest.json")
    expected = campaign_registration["controller"]
    shared.require(shared.process_identity(expected["pid"]) != expected,
                   "Previous guarded controller is still running")
    failure = shared.read_json(previous / "failure.json")
    status = shared.read_json(previous / "status.json")
    shared.require(failure["error"] == "RuntimeError('Stopping native child: shared-disk reserve reached')"
                   and status["phase"] == "create-disk-layout" and status["child_pid"] is None
                   and status["resources"]["disk_free_bytes"]
                   < context.config.section("Run").getint("MinimumFreeDiskGiB") * shared.GIB,
                   "Only the registered disk-reserve layout failure may be continued")
    shared.require(not (previous / "layout_completion.json").exists()
                   and not (guarded / "admission.command.json").exists()
                   and not (guarded / "validate-search.command.json").exists()
                   and not (campaign / "native").exists(),
                   "The failed attempt already advanced beyond layout")
    handoff = shared.read_json(previous / "saved-graph-handoff.json")
    preserved, width = saved_graph(context, handoff)
    retirement = shared.read_json(previous / "controller-retirement.command.json")
    shared.require(retirement["user_authorized"] is True
                   and retirement["native_builder"]["state"] == "Z"
                   and retirement["native_builder"]["exit_status"] == 0
                   and retirement["signals"] == ["SIGTERM", "SIGCONT"]
                   and "Received signal 15" in shared.read_json(context.root / "failure.json")["error"]
                   and not (context.root / "completion.json").exists()
                   and not (context.root / "validate-search.command.json").exists(),
                   "The old controller was not safely retired after original graph success")
    argv = [str(context.config.path_value("DiskANN", "BinaryDirectory") / "create_disk_layout"),
            "uint8", str(context.config.path_value("Inputs", "Vectors")),
            str(preserved / "graph"), str(context.prefix) + "_disk.index"]
    shared.require(shared.read_json(previous / "create-disk-layout.command.json") == argv,
                   "Previous layout used a different native command")
    for name in ("finalize_diskann_layout.py", "hold_diskann_controller.py"):
        entry = next(item for item in handoff["finalizer_sources"] if Path(item["copy"]).name == name)
        shared.require(Path(entry["copy"]) == previous / "code" / name
                       and shared.sha256_file(entry["copy"]) == entry["sha256"],
                       "Original handoff helper snapshot changed")
    paths = [context.root / "failure.json", context.root / "manifest.json",
             previous / "saved-graph-handoff.json", previous / "controller-retirement.command.json",
             previous / "create-disk-layout.command.json", previous / "create-disk-layout.log",
             guarded / "registration.json", campaign / "manifest.json", campaign / "config/benchmark.ini"]
    paths += [directory / name for directory in (previous, guarded, campaign)
              for name in ("status.json", "failure.json")]
    paths += [previous / "code" / name for name in
              ("finalize_diskann_layout.py", "hold_diskann_controller.py", "build_categorical_from_pq.py")]
    return previous, handoff, preserved, width, argv, [shared.identity(path, True) for path in paths]


def prepare(config_path):
    shared.reject_benchmark_environment()
    config = shared.load_layout_continuation_config(config_path)
    output = config.path_value("Continuation", "OutputDirectory")
    shared.require(not output.exists() and not output.is_symlink(), "Continuation output must be fresh")
    context = shared.DiskANNBuildValidation(config.path_value("Continuation", "OriginalConfig"))
    shared.require(output.parent == context.root, "Continuation must be a fresh child of the original build")
    previous, _, _, width, argv, previous_records = validate_previous(config, context)
    partial = partial_record(context, width)
    for suffix in shared.DISKANN_REQUIRED_SOURCES:
        if suffix != "_disk.index":
            path = Path(str(context.prefix) + suffix)
            shared.require(not path.exists() and not path.is_symlink(), "Do not replace existing final sidecars")
    output.mkdir()
    (output / "code").mkdir()
    finish.copy_exclusive(config.path, output / "continuation.ini")
    finish.copy_exclusive(context.config.path, output / "config.ini")
    for name in (*shared.CODE_FILES, "finish_diskann_build.py", "continue_diskann_layout.py"):
        finish.copy_exclusive(HERE / name, output / "code" / name)
    for name in ("finalize_diskann_layout.py", "hold_diskann_controller.py", "build_categorical_from_pq.py"):
        finish.copy_exclusive(previous / "code" / name, output / "code" / name)
    for name in ("saved-graph-handoff.json", "controller-retirement.command.json"):
        finish.copy_exclusive(previous / name, output / name)
    shared.write_json(output / "resource-policy.json", {
        "schema_version": 1, "purpose": "authorized-post-build-disk-reserve",
        "authorization": shared.DISK_RESERVE_AUTHORIZATION,
        "configuration": shared.identity(output / "continuation.ini", True),
        "original_config": shared.identity(context.config.path, True),
        "original_minimum_free_disk_gib": context.config.section("Run").getint("MinimumFreeDiskGiB"),
        "minimum_free_disk_gib": config.section("Run").getint("MinimumFreeDiskGiB"),
    })
    policy = shared.layout_disk_reserve_policy(context.config, output)
    dependencies = shared.native_dependencies(argv[0], output)
    shared.verify_snapshot_imports(output / "code")
    frozen = [shared.identity(path, True) for path in sorted((output / "code").iterdir()) if path.is_file()]
    frozen += [shared.identity(output / name, True) for name in (
        "continuation.ini", "config.ini", "resource-policy.json",
        "saved-graph-handoff.json", "controller-retirement.command.json")]
    registration = {
        "purpose": PURPOSE, "original_config": shared.identity(context.config.path, True),
        "resource_policy": policy, "previous_records": previous_records, "frozen": frozen,
        "partial": partial, "argv": argv, "dependency_resolution": dependencies,
        "loader_environment": {"LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH")},
        "protected": [shared.file_record(path, True) for path in sorted(set(dependencies.values()))],
    }
    shared.write_json(output / "layout-continuation-registration.json", registration)
    shared.write_json(output / "status.json", {"state": "prepared", "purpose": PURPOSE})
    return output


def verify_registration(root, registration, context):
    shared.reject_benchmark_environment()
    shared.require(registration["purpose"] == PURPOSE
                   and registration["original_config"] == shared.identity(context.config.path, True)
                   and registration["resource_policy"] == shared.layout_disk_reserve_policy(context.config, root)
                   and registration["loader_environment"] == {"LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH")},
                   "Continuation authority or loader environment changed")
    shared.verify_identities(registration["frozen"] + registration["previous_records"])
    shared.verify_records(registration["protected"])
    shared.require(shared.native_dependencies(registration["argv"][0], root)
                   == registration["dependency_resolution"], "Original layout native loader changed")
    saved_graph(context, shared.read_json(root / "saved-graph-handoff.json"))


def unlink_with_heartbeat(path, heartbeat):
    heartbeat()
    with ThreadPoolExecutor(max_workers=1) as executor:
        deletion = executor.submit(path.unlink)
        while not wait([deletion], timeout=15).done:
            heartbeat()
        deletion.result()
    heartbeat()


def discard_partial(root, context, registration, preserved, width, heartbeat=None):
    partial = partial_record(context, width)
    shared.require(partial == registration["partial"], "Incomplete layout changed since continuation preparation")
    sidecars = sum((preserved / name).stat().st_size
                   for name in ("graph_labels.txt", "graph_labels_map.txt", "graph_labels_to_medoids.txt"))
    pivots = Path(context.config.section("Inputs")["PQPrefix"] + "_pq_pivots.bin")
    required = (expected_layout_bytes(context, width) + sidecars + pivots.stat().st_size
                + registration["resource_policy"]["minimum_free_disk_gib"] * shared.GIB)
    shared.require(shutil.disk_usage(context.root).free + partial["allocated_bytes"] >= required,
                   "Insufficient capacity even after reclaiming this failed output")
    finish.check_resources(root, context.config, registration["resource_policy"])
    path = Path(str(context.prefix) + "_disk.index")
    shared.write_json(root / "partial-removal-intent.json", {
        "authorization": shared.DISK_RESERVE_AUTHORIZATION, "partial": partial, "remove_only": str(path),
    })
    shared.require(partial_record(context, width) == partial, "Partial layout changed before authorized unlink")
    if heartbeat is None:
        path.unlink()
    else:
        unlink_with_heartbeat(path, heartbeat)
    discarded = {
        "action": "unlink-authenticated-incomplete-layout", "authorization": shared.DISK_RESERVE_AUTHORIZATION,
        "partial": partial, "removed_paths": [str(path)], "removed_unix": time.time(),
        "native_search_invocations": 0,
    }
    shared.write_json(root / "discarded-partial-layout.json", discarded)


def run(root):
    shared.require(Path(__file__).resolve() == root / "code/continue_diskann_layout.py",
                   "Run the frozen layout continuation")
    shared.require(shared.read_json(root / "status.json")["state"] == "prepared",
                   "Continuation already started; never overwrite or resume its outputs")
    with (root / "run.lock").open("x") as stream:
        json.dump({"pid": os.getpid(), "started_unix": time.time()}, stream)
    runner = None
    try:
        os.chdir(root)
        registration = shared.read_json(root / "layout-continuation-registration.json")
        context = shared.DiskANNBuildValidation(registration["original_config"]["path"])
        verify_registration(root, registration, context)
        helper = finish.import_file("original_layout_helper", root / "code/finalize_diskann_layout.py")
        original, _, _, manifest = helper.load_context(context.config.path)
        handoff = shared.read_json(root / "saved-graph-handoff.json")
        preserved, width = saved_graph(context, handoff)
        policy = registration["resource_policy"]
        execution = {"status": "running", "argv": registration["argv"], "pid": None, "exit_code": None,
                     "cwd": str(root), "minimum_free_disk_gib": policy["minimum_free_disk_gib"]}

        class Runner(original.Runner):
            def update(self, **changes):
                super().update(**changes)
                if changes.get("child_pid") is not None:
                    execution["pid"] = changes["child_pid"]
                    shared.write_json(root / "create-disk-layout.execution.json", execution)

        runtime_config = shared.build_resource_parser(context.config, policy)
        runner = Runner(root, runtime_config)
        runner.update(state="running", phase="authenticating_saved_graph", child_pid=None,
                      original_build_directory=str(context.root),
                      minimum_free_disk_gib=policy["minimum_free_disk_gib"], scratch=str(preserved))
        discard_partial(root, context, registration, preserved, width,
                        lambda: runner.update(phase="discarding_incomplete_layout", child_pid=None))
        verify_registration(root, registration, context)
        execution["started_unix"] = time.time()
        try:
            prefix, header, artifacts = helper.create_layout(
                original, runner, runtime_config, preserved, context.rows, context.dimension, width)
            shared.require(type(execution["pid"]) is int and execution["pid"] > 0,
                           "Native layout did not record its actual child PID")
            execution.update(status="completed", exit_code=0)
        except BaseException as error:
            execution.update(status="failed", error=repr(error))
            raise
        finally:
            execution["ended_unix"] = time.time()
            shared.write_json(root / "create-disk-layout.execution.json", execution)
        verify_registration(root, registration, context)
        helper.verify_original_inputs(original, manifest)
        helper.verify_original_binaries(original, context.config.parser, manifest)
        shared.require(shared.read_json(root / "create-disk-layout.command.json") == registration["argv"],
                       "Native layout argv changed")
        finish.check_resources(root, context.config, policy)
        evidence = {
            "status": "completed", "graph_rebuilt": False, "native_search_invocations": 0,
            "registration": shared.identity(root / "layout-continuation-registration.json", True),
            "discarded_partial": shared.identity(root / "discarded-partial-layout.json", True),
            "execution": shared.identity(root / "create-disk-layout.execution.json", True),
            "resource_policy": policy,
        }
        shared.write_json(root / "layout-continuation-evidence.json", evidence)
        shared.validate_layout_continuation(context, root, policy)
        manifest.update(index_prefix=str(prefix), graph_degree_on_disk=width, disk_layout_bytes=header["bytes"],
                        safe_handoff=handoff, original_build_directory=str(context.root),
                        original_controller_failure=original.identity(context.root / "failure.json"),
                        resource_policy=policy)
        shared.write_json(root / "manifest.json", manifest)
        shared.write_json(root / "layout_completion.json", {
            "state": "layout_completed", "production_search_invocations": 0, "graph_rebuilt": False,
            "index_prefix": str(prefix), "header": header, "artifacts": artifacts,
            "original_inputs_unchanged": True, "awaiting": "all-label admission and original stock validation",
            "resource_policy": policy,
        })
        runner.update(state="awaiting_guarded_validation", phase="layout_completed", child_pid=None)
        print(json.dumps({"state": "layout_completed", "directory": str(root),
                          "production_search_invocations": 0, "minimum_free_disk_gib": 64}), flush=True)
    except BaseException as error:
        if runner is not None:
            runner.update(state="failed", child_pid=None, error=repr(error))
        else:
            shared.write_json(root / "status.json", {
                "state": "failed", "pid": os.getpid(), "child_pid": None,
                "updated_unix": time.time(), "error": repr(error),
            })
        shared.write_json(root / "failure.json", {"error": repr(error), "traceback": traceback.format_exc()})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="stage", required=True)
    preparation = subparsers.add_parser("prepare")
    preparation.add_argument("--config", required=True, type=Path)
    execution = subparsers.add_parser("run")
    execution.add_argument("--directory", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.stage == "prepare":
        print(prepare(arguments.config.resolve(strict=True)))
    else:
        def interrupted(signum, _frame):
            raise InterruptedError(f"Native layout continuation received signal {signum}")
        signal.signal(signal.SIGTERM, interrupted)
        run(arguments.directory.resolve(strict=True))


if __name__ == "__main__":
    main()
