"""Guard the unchanged stock validation after an authenticated layout handoff."""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import traceback

import numpy as np

import run_sift1b_threeway as shared


HERE = Path(__file__).resolve().parent
PURPOSE = "guarded-original-diskann-build-validation"


def import_file(name, path):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def copy_exclusive(source, destination):
    source, destination = Path(source), Path(destination)
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
    shutil.copystat(source, destination)
    shared.require(shared.sha256_file(source) == shared.sha256_file(destination), "Snapshot copy changed")


def prepare(config_path, layout, output, acceptance_path, loader_policy=None):
    shared.reject_benchmark_environment()
    shared.require(not output.exists() and not output.is_symlink(), "Refusing an existing validation directory")
    context = shared.DiskANNBuildValidation(config_path, loader_policy)
    shared.require(layout.parent == context.root and output.parent == layout,
                   "Guarded validation must be a fresh child of this original build's layout handoff")
    acceptance = shared.read_json(acceptance_path)
    shared.require(acceptance["status"] == "accepted" and acceptance["fixture_only"] is True
                   and acceptance["production_index_loaded"] is False
                   and acceptance["standalone_search_invocations"] == 0,
                   "Standalone admission has no authenticated fixture acceptance")
    shared.verify_records(acceptance["protected"])
    binding = context.loader["binding"] if context.loader is not None else None
    shared.require(acceptance.get("loader_policy") == binding,
                   "Accepted native delivery does not bind the requested loader policy")
    if binding is not None:
        shared.require(
            acceptance["builds"]["diskannBuildAdmission"]["native_artifacts"][0]["path"] == binding["library"],
            "Accepted admission delivery links another loader library")
    status = shared.read_json(layout / "status.json")
    controller = shared.process_identity(status["pid"])
    shared.require(not (layout / "failure.json").exists()
                   and (controller is not None or (layout / "layout_completion.json").is_file()),
                   "Layout handoff is failed or has no live controller/completion")
    shared.require(shared.sha256_file(layout / "config.ini") == context.manifest["config_sha256"],
                   "Layout handoff uses a different original INI")
    resource_policy = shared.layout_disk_reserve_policy(context.config, layout)
    originals = [layout / "code" / name for name in
                 ("finalize_diskann_layout.py", "hold_diskann_controller.py", "build_categorical_from_pq.py")]
    shared.require(shared.sha256_file(originals[-1]) == context.manifest["runner_sha256"],
                   "Layout handoff changed the original validation caller")
    binary = Path(acceptance["builds"]["diskannBuildAdmission"]["binary"]["path"])
    output.mkdir()
    (output / "code").mkdir()
    (output / "runtime").mkdir()
    copy_exclusive(context.config.path, output / "config.ini")
    copy_exclusive(acceptance_path, output / "admission-acceptance.json")
    for name in (*shared.CODE_FILES, "finish_diskann_build.py"):
        copy_exclusive(HERE / name, output / "code" / name)
    for source in originals:
        copy_exclusive(source, output / "code" / source.name)
    runtime = output / "runtime" / binary.name
    copy_exclusive(binary, runtime)
    copy_exclusive(binary.with_name(binary.name + ".build.json"),
                   runtime.with_name(runtime.name + ".build.json"))
    dependencies = shared.native_dependencies(runtime, output)
    shared.require(dependencies == acceptance["dependency_resolution"]["diskannBuildAdmission"],
                   "Copied all-label admission tool resolves different native libraries")
    stock_binary = context.stock_binary()
    stock_dependencies = shared.native_dependencies(stock_binary, output)
    protected = {record["path"]: record for record in acceptance["protected"]}
    for path in stock_dependencies.values():
        record = shared.file_record(path, True)
        shared.require(path not in protected or protected[path] == record,
                       "Stock caller and admission tool resolve conflicting native libraries")
        protected[path] = record
    shared.verify_snapshot_imports(output / "code")
    frozen = [shared.identity(path, True) for directory in ("code", "runtime")
              for path in sorted((output / directory).rglob("*")) if path.is_file()]
    frozen += [shared.identity(output / name, True) for name in ("config.ini", "admission-acceptance.json")]
    registration = {
        "schema_version": 1, "purpose": PURPOSE, "config": shared.identity(context.config.path, True),
        "layout_directory": str(layout), "layout_controller": controller,
        "layout_sources": [shared.identity(path, True) for path in originals],
        "admission_binary": shared.identity(runtime, True),
        "library": acceptance["builds"]["diskannBuildAdmission"]["native_artifacts"][0]["path"],
        "dependency_resolution": dependencies,
        "stock_binary": str(stock_binary), "stock_dependency_resolution": stock_dependencies,
        "loader_environment": {"LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH")},
        "protected": list(protected.values()), "frozen": frozen,
        "resource_policy": resource_policy,
        "loader_policy": binding,
        "loader_identities": context.loader["identities"] if context.loader is not None else [],
    }
    if resource_policy is not None:
        registration["layout_sources"] += [resource_policy["record"], resource_policy["configuration"]]
    shared.write_json(output / "registration.json", registration)
    shared.write_json(output / "status.json", {"state": "prepared", "purpose": PURPOSE})
    return output


def verify_registration(root, registration):
    shared.reject_benchmark_environment()
    shared.require(registration["purpose"] == PURPOSE
                   and registration["loader_environment"] == {"LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH")},
                   "Guarded validation registration/loader environment changed")
    shared.verify_identities([registration["config"], *registration["frozen"], *registration["layout_sources"]])
    shared.verify_records(registration["protected"])
    shared.verify_identities(registration.get("loader_identities", []))
    shared.require(shared.native_dependencies(registration["admission_binary"]["path"], root)
                   == registration["dependency_resolution"], "Guarded validation native loader changed")
    shared.require(shared.native_dependencies(registration["stock_binary"], root)
                   == registration["stock_dependency_resolution"], "Original stock validation loader changed")
    policy = shared.layout_disk_reserve_policy(
        shared.Config(registration["config"]["path"]), Path(registration["layout_directory"]))
    shared.require(registration.get("resource_policy") == policy, "Guarded build resource policy changed")
    if registration.get("loader_policy") is not None:
        config = shared.Config(registration["config"]["path"])
        loader = shared.native_loader_provenance.validate(
            registration["loader_policy"]["path"], config.path_value("DiskANN", "SourceDirectory"),
            config.section("DiskANN")["SourceRevision"],
            config.path_value("DiskANN", "BinaryDirectory") / "search_disk_index")
        shared.require(loader["binding"] == registration["loader_policy"],
                       "Guarded native loader policy changed")


def check_resources(root, config, resource_policy=None):
    run = shared.build_resource_parser(config, resource_policy)["Run"]
    resources = shared.resources()
    shared.require(resources["memory_available_bytes"] >= run.getint("MinimumFreeMemoryGiB") * shared.GIB,
                   "Original build memory reserve reached before native validation")
    shared.require(shutil.disk_usage(root).free >= run.getint("MinimumFreeDiskGiB") * shared.GIB,
                   "Original build disk reserve reached before native validation")
    shared.require(resources["aio_nr"] + 1024 * run.getint("ValidationThreads") <= resources["aio_max_nr"],
                   "Original validation threads do not fit the current host AIO limit")
    return resources


def wait_for_layout(runner, registration):
    layout = Path(registration["layout_directory"])
    while not (layout / "layout_completion.json").is_file():
        shared.require(not (layout / "failure.json").exists(), "Layout handoff failed; never launch validation")
        status = shared.read_json(layout / "status.json")
        expected = registration["layout_controller"]
        alive = expected is not None and shared.process_identity(expected["pid"]) == expected
        if not alive and (layout / "layout_completion.json").is_file():
            break
        shared.require(alive and status["state"] != "failed"
                       and time.time() - status["updated_unix"] <= 90,
                       "Layout handoff lost its authenticated responsive controller")
        check_resources(runner.root, runner.context.config, registration.get("resource_policy"))
        runner.update(state="waiting_for_layout", phase=status.get("phase"), child_pid=None,
                      layout_pid=status["pid"], layout_status=str(layout / "status.json"))
        time.sleep(15)
    expected = registration["layout_controller"]
    while expected is not None and shared.process_identity(expected["pid"]) == expected:
        shared.require(not (layout / "failure.json").exists(), "Layout completion has a failure marker")
        runner.update(state="waiting_for_layout_exit", child_pid=None, layout_pid=expected["pid"])
        time.sleep(1)
    shared.require(not (layout / "failure.json").exists(), "Layout completion has a failure marker")
    status = shared.read_json(layout / "status.json")
    shared.require(status["state"] == "awaiting_guarded_validation" and status["child_pid"] is None,
                   "Layout controller did not finish its no-search handoff")


def authenticate_layout(context, layout, helper):
    completion = shared.read_json(layout / "layout_completion.json")
    source = shared.read_json(layout / "manifest.json")
    handoff = shared.read_json(layout / "saved-graph-handoff.json")
    retirement = shared.read_json(layout / "controller-retirement.command.json")
    shared.require(completion["state"] == "layout_completed"
                   and completion["production_search_invocations"] == 0 and completion["graph_rebuilt"] is False
                   and completion["original_inputs_unchanged"] is True
                   and completion["index_prefix"] == str(context.prefix)
                   and handoff["user_authorized"] is True and handoff["native_builder_exit_status"] == 0
                   and handoff["graph_rebuilt"] is False and source["safe_handoff"] == handoff
                   and source["original_build_directory"] == str(context.root),
                   "Layout completion does not authenticate this original saved graph")
    shared.require(retirement["user_authorized"] is True
                   and retirement["native_builder"]["state"] == "Z"
                   and retirement["native_builder"]["exit_status"] == 0
                   and retirement["signals"] == ["SIGTERM", "SIGCONT"],
                   "Original controller was not retired after successful native graph exit")
    failure = shared.read_json(context.root / "failure.json")
    shared.require("Received signal 15" in failure["error"]
                   and not (context.root / "completion.json").exists()
                   and not (context.root / "validate-search.command.json").exists()
                   and not (context.root / "create-disk-layout.command.json").exists(),
                   "Old controller did unguarded post-build work")
    shared.require(source["config_sha256"] == context.manifest["config_sha256"]
                   and source["runner_sha256"] == context.manifest["runner_sha256"]
                   and source["inputs"] == context.manifest["inputs"]
                   and source["binaries"] == context.manifest["binaries"],
                   "Layout handoff changed original input/controller/native identities")
    for record in completion["artifacts"] + handoff["graph_inputs"]:
        shared.require(shared.original_build_identity(record["path"]) == record,
                       "Saved original graph/layout artifact changed")
    shared.require(handoff["saved_vectors"]["exact_original_vector_samples"] is True
                   and handoff["saved_vectors"]["sample_count"] == min(context.rows, 4096),
                   "Saved graph was not checked against original vectors before retiring its controller")
    header = helper.validate_disk_header(
        Path(str(context.prefix) + "_disk.index"), context.rows, context.dimension, source["graph_degree_on_disk"])
    shared.require(header == completion["header"], "Native layout header changed after finalization")
    layout_argv = shared.read_json(layout / "create-disk-layout.command.json")
    shared.require(layout_argv == list(map(str, [
        context.config.path_value("DiskANN", "BinaryDirectory") / "create_disk_layout",
        "uint8", context.config.path_value("Inputs", "Vectors"),
        next(Path(item["path"]) for item in handoff["graph_inputs"] if Path(item["path"]).name == "graph"),
        Path(str(context.prefix) + "_disk.index"),
    ])), "Layout did not use the unchanged original native command")
    records = [shared.identity(layout / name, True) for name in (
        "layout_completion.json", "manifest.json", "saved-graph-handoff.json",
        "controller-retirement.command.json", "create-disk-layout.command.json", "create-disk-layout.log")]
    records += [shared.identity(context.root / "failure.json", True)]
    policy = shared.layout_disk_reserve_policy(context.config, layout)
    shared.require(source.get("resource_policy") == completion.get("resource_policy") == policy,
                   "Layout completion changed its resource authority")
    if policy is not None:
        shared.validate_layout_continuation(context, layout, policy)
        records += [shared.identity(layout / name, True) for name in shared.LAYOUT_CONTINUATION_RECORDS]
    return source, {
        "state": "layout_completed", "native_builder_exit_status": 0, "graph_rebuilt": False,
        "production_search_invocations": 0, "index_prefix": str(context.prefix),
        "header": header, "records": records, "resource_policy": policy,
    }


def native_process_snapshot(pid):
    directory = Path("/proc") / str(pid)
    status = (directory / "status").read_text()
    command = (directory / "cmdline").read_bytes()
    stat = (directory / "stat").read_text()
    fields = stat[stat.rfind(")") + 2:].split()
    rss = [line.split() for line in status.splitlines() if line.startswith("VmRSS:")]
    shared.require(len(rss) <= 1 and (not rss or (len(rss[0]) == 3 and rss[0][2] == "kB")),
                   "Native child has malformed VmRSS accounting")
    return {
        "pid": pid, "parent_pid": int(fields[1]), "start_ticks": int(fields[19]),
        "state": fields[0], "flags": int(fields[6]),
        "command_sha256": hashlib.sha256(command).hexdigest(),
        "rss_bytes": int(rss[0][1]) * 1024 if rss else None,
    }


def observe_native_child(child, expected, argv):
    try:
        snapshot = native_process_snapshot(child.pid)
    except (FileNotFoundError, ProcessLookupError):
        if child.poll() is not None:
            return None
        raise
    current = {key: snapshot[key] for key in ("pid", "parent_pid", "start_ticks")}
    shared.require(current["parent_pid"] == os.getpid() and current["start_ticks"] > 0
                   and (expected is None or current == expected), "Native child process identity changed")
    exiting = bool(snapshot["flags"] & 0x4)
    command_hash = hashlib.sha256(b"\0".join(str(arg).encode() for arg in argv) + b"\0").hexdigest()
    shared.require(snapshot["command_sha256"] == command_hash or
                   (exiting and snapshot["command_sha256"] == hashlib.sha256(b"").hexdigest()),
                   "Native child command changed outside authenticated kernel exit")
    # exit_mm can remove VmRSS/cmdline before waitpid can observe a zombie.
    shared.require(snapshot["rss_bytes"] is not None or exiting,
                   "Running native child has no VmRSS accounting outside kernel exit")
    snapshot["kernel_exiting"] = exiting
    return snapshot


def run_native_command(runner, name, arguments):
    argv = list(map(str, arguments))
    shared.write_json(runner.root / f"{name}.command.json", argv)
    log = runner.root / f"{name}.log"
    print(f"Starting {name}: {log}", flush=True)
    with log.open("x") as stream:
        child = subprocess.Popen(argv, cwd=runner.root, stdout=stream, stderr=subprocess.STDOUT)
        runner.update(state="running", phase=name, child_pid=child.pid, log=str(log))
        try:
            initial = observe_native_child(child, None, argv)
            expected = ({key: initial[key] for key in ("pid", "parent_pid", "start_ticks")}
                        if initial is not None else None)
            runner.active_execution["process_identity"] = expected
            while True:
                try:
                    result = child.wait(timeout=15)
                    runner.active_execution["exit_code"] = result
                    break
                except subprocess.TimeoutExpired:
                    shared.require(expected is not None, "Live native child lost its launch identity")
                    snapshot = observe_native_child(child, expected, argv)
                    if snapshot is None:
                        continue
                    available = shared.resources()["memory_available_bytes"]
                    free_disk = shutil.disk_usage(runner.root).free
                    rss = snapshot["rss_bytes"]
                    resources = {
                        "time_unix": time.time(), "phase": name, "pid": child.pid,
                        "rss_bytes": rss, "mem_available_bytes": available, "disk_free_bytes": free_disk,
                        "process": snapshot,
                    }
                    if runner.status.get("scratch"):
                        resources["scratch_free_bytes"] = shutil.disk_usage(runner.status["scratch"]).free
                    with (runner.root / "resources.jsonl").open("a") as resource_log:
                        resource_log.write(json.dumps(resources) + "\n")
                    runner.update(resources=resources)
                    shared.require(available >= runner.cfg.getint("Run", "MinimumFreeMemoryGiB") * shared.GIB,
                                   "Stopping native child: shared-host memory reserve reached")
                    shared.require(rss is None or rss <= runner.cfg.getint("Run", "MaxBuilderRSSGiB") * shared.GIB,
                                   "Stopping native child: configured RSS ceiling reached")
                    shared.require(free_disk >= runner.cfg.getint("Run", "MinimumFreeDiskGiB") * shared.GIB,
                                   "Stopping native child: shared-disk reserve reached")
                    shared.require("scratch_free_bytes" not in resources
                                   or resources["scratch_free_bytes"] >= 16 * shared.GIB,
                                   "Stopping native child: RAM-staging reserve reached")
        except BaseException:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            runner.active_execution["exit_code"] = child.returncode
            raise
    shared.require(result == 0, f"{name} exited {result}; see {log}")
    runner.update(child_pid=None)
    return log


def guarded_runner_class(original, command_runner=run_native_command):
    class GuardedRunner(original.Runner):
        def __init__(self, root, context, registration, verify):
            super().__init__(root, shared.build_resource_parser(context.config, registration.get("resource_policy")))
            self.context, self.registration, self.verify = context, registration, verify
            self.executions = {}
            self.active_execution = None
            self.admitted = None

        def update(self, **changes):
            super().update(**changes)
            if self.active_execution is not None and changes.get("child_pid") is not None:
                self.active_execution["pid"] = changes["child_pid"]
                shared.write_json(self.root / (self.active_execution["name"] + ".execution.json"),
                                  self.active_execution)

        def command(self, name, arguments):
            argv = list(map(str, arguments))
            shared.require(name not in self.executions, "Never repeat a completed/failed validation command")
            if name == "admission":
                expected = self.context.admission_command(self.registration["admission_binary"]["path"], self.root)
                shared.require(not self.executions, "All-label admission must be the first native command")
            elif name == "validate-search":
                shared.require(self.admitted is not None, "Stock search is forbidden before admitted preflight")
                shared.verify_identities([self.admitted])
                shared.validate_build_admission(
                    self.context, self.root / "admission.json", self.executions["admission"]["pid"],
                    self.registration["library"])
                expected = self.context.stock_command(self.root)
                shared.require(shared.native_header(self.root / "validation_queries.u8bin", 1) ==
                               (self.context.query_calls, self.context.dimension),
                               "Stock validation query extent changed")
                filters = "".join(f"{label}\n" * count for label, count in self.context.groups)
                shared.require((self.root / "validation_filters.txt").read_text() == filters,
                               "Stock validation did not use the admitted all-label request set")
            else:
                raise ValueError(f"Unregistered native validation command: {name}")
            shared.require(argv == expected, "Native validation argv differs from the original INI/caller")
            self.verify()
            check_resources(self.root, self.context.config, self.registration.get("resource_policy"))
            execution = {"name": name, "argv": argv, "started_unix": time.time(),
                         "cwd": str(self.root), "pid": None, "status": "running", "exit_code": None}
            self.executions[name] = self.active_execution = execution
            try:
                log = command_runner(self, name, argv)
                shared.require(type(execution["pid"]) is int and execution["pid"] > 0,
                               "Native child launch did not preserve its actual PID")
                execution.update(status="completed", exit_code=0)
                return log
            except BaseException as error:
                execution.update(status="failed", error=repr(error))
                raise
            finally:
                execution["ended_unix"] = time.time()
                shared.write_json(self.root / f"{name}.execution.json", execution)
                self.active_execution = None

        def authorize_stock_validation(self):
            self.admitted = None
            execution = self.executions.get("admission")
            shared.require(execution is not None and execution["status"] == "completed",
                           "Stock search is forbidden without successful admission execution")
            path = self.root / "admission.json"
            checked = shared.validate_build_admission(
                self.context, path, execution["pid"], self.registration["library"])
            prefix = "DISKANN_BUILD_ADMISSION "
            lines = (self.root / "admission.log").read_text().splitlines()
            markers = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
            shared.require(markers == [shared.read_json(path)]
                           and not any(line.startswith(("THREEWAY_READY ", "THREEWAY_RESULT ")) for line in lines),
                           "Standalone admission certificate does not match its zero-search native output")
            self.admitted = checked

    return GuardedRunner


def run(root):
    shared.require(Path(__file__).resolve() == root / "code/finish_diskann_build.py",
                   "Run the frozen guarded-validation script")
    shared.require(shared.read_json(root / "status.json")["state"] == "prepared",
                   "Guarded validation already started; no implicit retry/overwrite")
    with (root / "run.lock").open("x") as stream:
        stream.write(json.dumps({"pid": os.getpid(), "started_unix": time.time()}) + "\n")
    runner = None
    try:
        os.chdir(root)
        registration = shared.read_json(root / "registration.json")
        verify_registration(root, registration)
        loader_binding = registration.get("loader_policy")
        context = shared.DiskANNBuildValidation(
            registration["config"]["path"], loader_binding["path"] if loader_binding is not None else None)
        original = import_file("original_diskann_validation", root / "code/build_categorical_from_pq.py")
        helper = import_file("original_diskann_layout_contract", root / "code/finalize_diskann_layout.py")
        runner = guarded_runner_class(original)(
            root, context, registration, lambda: verify_registration(root, registration))
        runner.verify()
        runner.update(state="waiting_for_layout", phase="layout_handoff", child_pid=None)
        wait_for_layout(runner, registration)
        runner.verify()
        source, layout_evidence = authenticate_layout(context, Path(registration["layout_directory"]), helper)
        shared.write_json(root / "layout-evidence.json", layout_evidence)
        runner.command("admission", context.admission_command(registration["admission_binary"]["path"], root))
        runner.authorize_stock_validation()
        cfg = context.config
        base = original.matrix(cfg.path_value("Inputs", "Vectors"), "u1")
        queries = original.matrix(cfg.path_value("Inputs", "Queries"), "u1")
        attributes = np.memmap(cfg.path_value("Inputs", "Attributes"), mode="r",
                               dtype="<u4", shape=(context.rows, context.columns))
        validation = original.validate_results(
            runner, context.validation_parser(), context.prefix, base, attributes, queries, context.counts, False)
        del attributes, queries, base
        runner.verify()
        shared.DiskANNBuildValidation(
            context.config.path, loader_binding["path"] if loader_binding is not None else None)
        shared.validate_build_admission(
            context, root / "admission.json", runner.executions["admission"]["pid"], registration["library"])
        authenticate_layout(context, Path(registration["layout_directory"]), helper)
        shared.require(validation["queries"] == context.query_calls
                       and validation["distinct_labels_checked"] == len(context.groups)
                       and all(validation[field] is True for field in
                               ("exact_result_distances", "all_labels_match", "no_duplicate_ids")),
                       "Original stock validation did not cover every admitted label")
        outputs = [
            "admission.command.json", "admission.log", "validate-search.command.json", "validate-search.log",
            "validation_queries.u8bin", "validation_filters.txt",
            f"validation_{context.search_l}_idx_uint32.bin", f"validation_{context.search_l}_dists_float.bin",
        ]
        evidence = {
            "schema_version": 1, "status": "completed", "config": registration["config"],
            "registration": shared.identity(root / "registration.json", True),
            "layout": shared.identity(root / "layout-evidence.json", True),
            "admission": runner.admitted,
            "admission_execution": shared.identity(root / "admission.execution.json", True),
            "stock_execution": shared.identity(root / "validate-search.execution.json", True),
            "validation": shared.identity(root / "validation.json", True),
            "outputs": [shared.identity(root / name, True) for name in outputs],
        }
        shared.write_json(root / "guarded-validation.json", evidence)
        source["guarded_validation"] = shared.identity(root / "guarded-validation.json", True)
        source["loader_policy"] = loader_binding
        shared.write_json(root / "manifest.json", source)
        completion = {
            "state": "completed", "index_prefix": str(context.prefix), "vectors": context.rows,
            "elapsed_seconds": time.time() - source["safe_handoff"]["original_build_started_unix"],
            "validation": validation, "original_inputs_unchanged": True, "smoke_only": False,
            "guarded_validation": source["guarded_validation"],
        }
        shared.write_json(root / "completion.json", completion)
        runner.update(state="completed", phase="completed", child_pid=None)
        print(json.dumps(completion, allow_nan=False), flush=True)
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
    for name in ("config", "layout-directory", "output", "acceptance"):
        preparation.add_argument("--" + name, required=True, type=Path)
    preparation.add_argument("--loader-policy", type=Path)
    execution = subparsers.add_parser("run")
    execution.add_argument("--directory", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.stage == "prepare":
        print(prepare(arguments.config.resolve(strict=True), arguments.layout_directory.resolve(strict=True),
                      arguments.output.absolute(), arguments.acceptance.resolve(strict=True),
                      arguments.loader_policy.resolve(strict=True) if arguments.loader_policy is not None else None))
    else:
        def interrupted(signum, _frame):
            raise InterruptedError(f"Guarded stock validation received signal {signum}")
        signal.signal(signal.SIGTERM, interrupted)
        run(arguments.directory.resolve(strict=True))


if __name__ == "__main__":
    main()
