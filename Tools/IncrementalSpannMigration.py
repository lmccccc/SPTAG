#!/usr/bin/env python3
"""Stage native preserved-head migration; never construct metadata/postings in Python."""
import argparse
import configparser
import ctypes
import datetime
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import struct
import subprocess
import sys
import time


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ini(path):
    result = configparser.ConfigParser(interpolation=None)
    result.optionxform = str
    with path.open() as stream:
        result.read_file(stream)
    return result


def write_ini(path, config):
    with path.open("w") as stream:
        config.write(stream, space_around_delimiters=False)


def identity(path):
    s = path.stat()
    return dict(path=str(path.resolve()), device=s.st_dev, inode=s.st_ino,
                size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns)


def inventory(root):
    return {str(p.relative_to(root)): identity(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def matrix(path, width, dimension=None):
    with path.open("rb") as stream:
        count, columns = struct.unpack("<ii", stream.read(8))
    require(count > 0 and columns > 0 and
            (dimension is None or columns == dimension), f"Invalid matrix: {path}")
    require(path.stat().st_size == 8 + count * columns * width,
            f"Invalid matrix byte extent: {path}")
    return count, columns


def confine(root):
    # ABI >= 3 covers cross-directory hard links and truncate as well as writes.
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(444, 0, 0, 1)
    require(abi >= 3, f"Landlock ABI >= 3 required, got {abi}")
    access = sum(1 << bit for bit in [1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
    ruleset = ctypes.c_uint64(access)
    descriptor = libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0)
    require(descriptor >= 0, f"Landlock create: errno={ctypes.get_errno()}")

    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("access", ctypes.c_uint64), ("parent", ctypes.c_int32)]

    directory = os.open(root, os.O_PATH | os.O_DIRECTORY)
    try:
        rule = Beneath(access, directory)
        require(libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0) == 0,
                f"Landlock rule: errno={ctypes.get_errno()}")
        require(libc.prctl(38, 1, 0, 0, 0) == 0, "Cannot set no_new_privs")
        require(libc.syscall(446, descriptor, 0) == 0,
                f"Landlock restrict: errno={ctypes.get_errno()}")
    finally:
        os.close(directory)
        os.close(descriptor)
    return abi


def safety(plan):
    for scope in ("preflight", "production"):
        root = Path(plan[scope]["source"])
        actual = inventory(root)
        expected = json.loads((Path(plan["run"]) / f"{scope}-source.json").read_text())
        require(actual == expected, f"Source identity changed: {scope}")
        root_manifest = Path(plan["run"]) / f"{scope}-root-manifest.json"
        if root_manifest.exists():
            require(identity(root.parent / "manifest.txt") ==
                    json.loads(root_manifest.read_text()),
                    f"Source wrapper manifest changed: {scope}")
    for relative, expected in plan["runtime_hashes"].items():
        require(digest(Path(plan["run"]) / relative) == expected,
                f"Frozen runtime changed: {relative}")


def protected_probe(run):
    protected = run.parent / (run.name + "-write-probe")
    protected.mkdir()
    target = protected / "sentinel"
    target.write_bytes(b"immutable-probe")
    link = run / "probe-link"
    link.symlink_to(target)
    pid = os.fork()
    if pid == 0:
        try:
            confine(run)
            for operation in (
                lambda: open(link, "wb"),
                lambda: os.truncate(link, 0),
                lambda: os.link(target, run / "forbidden-hard-link"),
            ):
                try:
                    operation()
                except OSError as error:
                    require(error.errno in (errno.EACCES, errno.EPERM, errno.EXDEV),
                            f"Unexpected confinement failure: {error}")
                else:
                    raise RuntimeError("Source-link protection failed")
            (run / "allowed-probe").write_bytes(b"allowed")
            os._exit(0)
        except BaseException:
            os._exit(1)
    _, status = os.waitpid(pid, 0)
    require(os.waitstatus_to_exitcode(status) == 0, "Write-confinement probe failed")
    require(target.read_bytes() == b"immutable-probe", "Probe source was changed")
    link.unlink()
    (run / "allowed-probe").unlink()
    target.unlink()
    protected.rmdir()
    save(run / "write-confinement.json", dict(passed=True, tested=[
        "write through symbolic link", "truncate through symbolic link",
        "cross-boundary hard link", "allowed private output write"]))


def prepare(args):
    run = args.run.resolve()
    require(not run.exists(), "Run directory must be new")
    run.mkdir(parents=True)
    repo = args.repo.resolve()
    runtime = run / "runtime"
    runtime.mkdir()
    accepted = args.runtime.resolve()
    names = ["indexbuilder", "compactspannindex", "nativestorageload", "spannaclbench",
             "libSPTAGLibStatic.a", "libDistanceUtils.a", "libRaBitQ2Lib.a", "libzstd.a"]
    for name in names:
        shutil.copy2(accepted / name, runtime / name)
    snapshot = run / "source"
    shutil.copytree(repo / "AnnService/inc", snapshot / "AnnService/inc")
    (snapshot / "AnnService/src/IndexBuilder").mkdir(parents=True)
    shutil.copy2(repo / "AnnService/src/IndexBuilder/main.cpp",
                 snapshot / "AnnService/src/IndexBuilder/main.cpp")
    shutil.copytree(repo / "Tools/incremental_h1", snapshot / "builder-cmake")
    shutil.copy2(Path(__file__), run / "controller.py")
    options = (repo / "AnnService/inc/Core/SPANN/Options.h").read_text()
    removed_block = options.split("static bool IsRemovedParameter", 1)[1].split(
        "static bool IsRemovedSectionAlias", 1)[0]
    removed = sorted(set(re.findall(r'"([A-Za-z0-9]+)"', removed_block)))
    plan = dict(run=str(run), created_at=stamp(), accepted_runtime=str(accepted),
                acceptance="User start_incremental; unresolved timing windows retained, not proven noise",
                no_selection=True, no_ssd_rebuild=True, native_threads=args.threads,
                cpus=args.cpus, memory_node=args.node, address_space_limit_gib=256,
                query_directory=str(args.queries.resolve()), recipe=str(args.recipe.resolve()),
                removed_parameters=removed, runtime_hashes={})
    shutil.copy2(args.recipe, run / "authoritative-build.ini")
    for scope, source in (("preflight", args.preflight_source), ("production", args.source)):
        source = source.resolve()
        scope_root = run / scope
        scope_root.mkdir()
        count, dim = matrix(source / "SPTAGHeadVectors.bin", 1, 128)
        require(matrix(source / "SPTAGHeadVectorIDs.bin", 8, 1)[0] == count,
                "Head ID count mismatch")
        require(matrix(source / "HeadIndex/vectors.bin", 1, 128)[0] == 1,
                "Expected authentic graphless dummy input")
        save(run / f"{scope}-source.json", inventory(source))
        shutil.copy2(source / "indexloader.ini", scope_root / "source-indexloader.ini")
        graph = ini(args.recipe)
        settings = dict(graph["BuildHead"])
        settings.pop("isExecute")
        require(not any(key in removed for key in settings),
                "Authoritative graph recipe contains retired settings")
        settings["NumberOfThreads"] = str(args.threads)
        settings["DistCalcMethod"] = "L2"
        graph_ini = configparser.ConfigParser(interpolation=None)
        graph_ini.optionxform = str
        graph_ini["Index"] = settings
        graph_ini["Input"] = dict(Vectors=str(source / "SPTAGHeadVectors.bin"),
                                 ValueType="UInt8", FileType="DEFAULT", Dim="128",
                                 Algorithm="BKT", Output=str(scope_root / "h1"))
        write_ini(scope_root / "h1.ini", graph_ini)
        products = dict(rows=count, dimension=dim, final_graph_bytes=8 + count * 32 * 4,
                        scaled_graph_payload=count * 64 * 4,
                        neighborhood_distance_payload=count * 64 * 4,
                        tpt_index_payload=count * 32 * 4,
                        vector_payload=count * dim, compact_metadata_payload=count * 176,
                        legacy_metadata_payload=count * 320)
        require(all(value < (1 << 63) for value in products.values()),
                "64-bit extent overflow")
        save(scope_root / "extent-audit.json", products)
        plan[scope] = dict(source=str(source), rows=count,
                           truth=str(args.prefix_truth.resolve()) if scope == "preflight" else None)
    plan["runtime_hashes"] = {str(p.relative_to(run)): digest(p) for p in runtime.iterdir()}
    save(run / "plan.json", plan)
    save(run / "resources-before.json", dict(
        timestamp=stamp(), disk_free_bytes=shutil.disk_usage(run).free,
        meminfo=Path("/proc/meminfo").read_text(),
        node_meminfo=Path(f"/sys/devices/system/node/node{args.node}/meminfo").read_text(),
        cpu_affinity=sorted(os.sched_getaffinity(0)),
        resource_change=f"Graph workers 45 -> {args.threads}; graph quality settings unchanged",
        memory_estimate="Principal build arrays ~106 GiB including a possible vector copy; tree/workspaces/allocator additional",
        address_space_limit_gib=256, required_free_disk_bytes=200 << 30))
    require(shutil.disk_usage(run).free >= 200 << 30, "Insufficient migration disk space")
    protected_probe(run)
    save(run / "status.json", dict(state="prepared", timestamp=stamp(), run=str(run)))
    print(run)


def heartbeat(process, command, log):
    children_path = Path(f"/proc/{process.pid}/task/{process.pid}/children")
    children = children_path.read_text().split() if children_path.exists() else []
    pids = [int(p) for p in children]
    usage = {}
    for pid in pids:
        path = Path(f"/proc/{pid}/status")
        if path.exists():
            usage[str(pid)] = [line for line in path.read_text().splitlines()
                              if line.startswith(("Name:", "State:", "VmRSS:", "VmHWM:", "Threads:"))]
    return dict(command=command, time_pid=process.pid, native_pids=pids,
                process_status=usage, log_bytes=log.stat().st_size,
                updated_at=stamp())


def execute(plan, scope, name, command, expected=0):
    run = Path(plan["run"])
    folder = run / scope
    record = folder / f"{name}.stage.json"
    if record.exists():
        prior = json.loads(record.read_text())
        require(prior["state"] == "complete", f"Partial stage retained; resolve before restart: {record}")
        safety(plan)
        return
    safety(plan)
    log = folder / f"{name}.log"
    resources = folder / f"{name}.resources.txt"
    full = ["/usr/bin/time", "-v", "-o", str(resources), "numactl",
            f"--physcpubind={plan['cpus']}", f"--membind={plan['memory_node']}", *command]
    begun = time.monotonic()
    state = dict(state="running", stage=name, scope=scope, started_at=stamp(), command=full)
    save(record, state)
    with log.open("xb") as output:
        process = subprocess.Popen(full, cwd=folder, stdout=output, stderr=subprocess.STDOUT,
                                   stdin=subprocess.PIPE)
        process.stdin.close()
        try:
            while True:
                state.update(heartbeat(process, full, log))
                save(record, state)
                save(run / "status.json", dict(state, controller_pid=os.getpid()))
                try:
                    code = process.wait(timeout=15)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            # The controller is attached; terminate only its exact owned child tree.
            children = heartbeat(process, full, log)["native_pids"]
            for child in children:
                try:
                    os.kill(child, 15)
                except ProcessLookupError:
                    pass
            process.terminate()
            process.wait()
            raise
    state.update(returncode=code, elapsed_seconds=time.monotonic() - begun,
                 finished_at=stamp(), state="validating")
    measurement = resources.read_text()
    peak = re.search(r"Maximum resident set size \(kbytes\): (\d+)", measurement)
    require(peak is not None, "Missing end-of-process native RSS measurement")
    state["endprocess_peak_rss_bytes"] = int(peak.group(1)) * 1024
    state["peak_source"] = "GNU time child wait4 after native process exit, not loader's earlier sample"
    save(record, state)
    safety(plan)
    require(code == expected, f"{scope}/{name} failed with {code}; see {log}")
    state["state"] = "complete"
    state["resources"] = str(resources)
    save(record, state)


def compare_vectors(source, saved):
    require(source.stat().st_size == saved.stat().st_size, "Preserved vector size mismatch")
    h = hashlib.sha256()
    with source.open("rb") as left, saved.open("rb") as right:
        for block in iter(lambda: left.read(8 << 20), b""):
            require(block == right.read(len(block)), "Selected H1 vector/order mismatch")
            h.update(block)
        require(not right.read(1), "Trailing H1 vector data")
    return dict(bytes=source.stat().st_size, sha256=h.hexdigest(), byte_exact=True)


def migrated_config(plan, scope, bridge):
    folder = Path(plan["run"]) / scope
    config = ini(folder / "source-indexloader.ini")
    original = {section: dict(config[section]) for section in config.sections()}
    aliases = {"SelectSecondLevel": "HierarchyEnabled",
               "SecondLevelHierarchyLevels": "HierarchyLevels",
               "SecondLevelHeadVectors": "HierarchyHeadVectors",
               "SecondLevelHeadVectorIDs": "HierarchyHeadVectorIDs",
               "SecondLevelReplicaCount": "HierarchyReplicaCount",
               "SecondLevelPostingFile": "HierarchyPostingFile",
               "SecondLevelGenerationFingerprint": "HierarchyGenerationFingerprint"}
    for section in config.sections():
        for key in list(config[section]):
            if key in plan["removed_parameters"] or key == "SecondLevelRatio":
                del config[section][key]
            elif key in aliases:
                require(aliases[key] not in config[section], "Conflicting hierarchy aliases")
                config[section][aliases[key]] = config[section].pop(key)
    config["Base"]["IndexDirectory"] = str(bridge)
    config["Base"]["IndexAlgoType"] = "BKT"
    config["Base"]["DeleteHeadVectors"] = "false"
    config["SelectHead"]["isExecute"] = "false"
    config["SelectHead"]["BuildH1Graph"] = "true"
    config["BuildHead"] = dict(ini(folder / "h1" / "indexloader.ini")["Index"])
    config["BuildHead"].pop("IndexAlgoType", None)
    config["BuildHead"].pop("ValueType", None)
    config["BuildHead"]["isExecute"] = "false"
    ssd = config["BuildSSDIndex"]
    ssd["isExecute"] = "false"
    ssd["BuildSsdIndex"] = "false"
    ssd["NumberOfThreads"] = "1"
    ssd["TmpDir"] = str(folder / "tmp")
    ssd["Update"] = "false"
    if "ColumnTypes" not in ssd:
        legacy = ini(Path(plan[scope]["source"]).parents[1] / "build.ini")
        require(legacy["Tags"]["NumTagsPerVec"] == "2" and
                legacy["MultiTenant"]["NumericCols"] == "1" and
                legacy["BuildSSDIndex"]["StaticACLTagCols"] == "1",
                "Cannot authenticate legacy original-column schema")
        ssd["ColumnTypes"] = "categorical,numeric"
        reference = ini(Path(plan["production"]["source"]) / "indexloader.ini")["BuildSSDIndex"]
        require(reference["ColumnTypes"] == ssd["ColumnTypes"] and
                reference["TagSchemaVersion"] == "1",
                "No matching persisted native schema descriptor")
        ssd["TagSchemaVersion"] = reference["TagSchemaVersion"]
        ssd["TagSchemaFingerprint"] = reference["TagSchemaFingerprint"]
    require(ssd["ColumnTypes"] == "categorical,numeric", "Unexpected preserved source schema")
    config["SearchSSDIndex"] = dict(
        isExecute="true", BuildSsdIndex="false", InternalResultNum="96",
        NumberOfThreads="1", HashTableExponent="4", ResultNum="10", MaxCheck="2048",
        MaxDistRatio="8", SearchPostingPageLimit="3", DisableCrossEdges="true",
        LogPhaseTime="false", LogPathStats="false", DumpHeads="0",
        EnablePostingNavigation="true", PostingAnchorCount="8",
        PostingAdditionalMaxCheck="2048")
    for section in config.sections():
        for key, value in config[section].items():
            if value.startswith("/"):
                require(Path(value).is_relative_to(Path(plan["run"])),
                        f"External stored path requires explicit migration: [{section}]{key}={value}")
    changes = []
    after = {section: dict(config[section]) for section in config.sections()}
    for section in sorted(set(original) | set(after)):
        for key in sorted(set(original.get(section, {})) | set(after.get(section, {}))):
            before = original.get(section, {}).get(key)
            value = after.get(section, {}).get(key)
            if before != value:
                changes.append(dict(section=section, key=key, before=before, after=value))
    save(folder / "config-migration.json", dict(
        changes=changes, policy="Current H1 post-filter plus one optional posting supplement; not legacy upper ANN",
        query_settings="Explicit 2048 graph + 2048 additional, 8 anchors, nprobe 96, page limit 3",
        schema_provenance="Original explicit ColumnTypes or prefix Tags.NumTagsPerVec=2, MultiTenant.NumericCols=1, StaticACLTagCols=1; matching native schema version/fingerprint copied from the approved source and checked by native load"))
    return config


def bridge_view(plan, scope):
    folder = Path(plan["run"]) / scope
    target = folder / "graphful" / "tenant_0"
    source = Path(plan[scope]["source"])
    manifest = source.parent / "manifest.txt"
    recorded = Path(plan["run"]) / f"{scope}-root-manifest.json"
    if not recorded.exists():
        save(recorded, identity(manifest))
    target.parent.mkdir(exist_ok=True)
    wrapper_manifest = target.parent / "manifest.txt"
    if not wrapper_manifest.exists():
        wrapper_manifest.symlink_to(manifest)
    if (folder / "bridge-complete.json").exists():
        return target
    require(not target.exists(), "Partial graphful view retained")
    target.mkdir(parents=True)
    for item in source.iterdir():
        if item.name in ("HeadIndex", "indexloader.ini"):
            continue
        (target / item.name).symlink_to(item.resolve(), target_is_directory=item.is_dir())
    (target / "HeadIndex").mkdir()
    for item in (folder / "h1").iterdir():
        (target / "HeadIndex" / item.name).symlink_to(item.resolve())
    (target / "HeadIndex/head_node_meta.bin").symlink_to(
        source / "HeadIndex/head_node_meta.bin")
    top = target / "SPTAGSecondLevelHeadVectors.bin.level4"
    if not top.exists():
        top.symlink_to(source / "SecondLevelHeadIndex/vectors.bin")
    config = migrated_config(plan, scope, target)
    write_ini(target / "indexloader.ini", config)
    (folder / "tmp").mkdir()
    search = configparser.ConfigParser(interpolation=None)
    search.optionxform = str
    search["SearchSSDIndex"] = dict(config["SearchSSDIndex"])
    search["SearchSweep"] = dict(NProbe="[96]")
    search["Benchmark"] = dict(Queries=str(Path(plan["query_directory"]) / "query_vectors.npy"),
                               Warmup="4", MaxQueries="16", TopK="10", ValueType="UInt8")
    write_ini(folder / "search.ini", search)
    save(folder / "bridge-complete.json", dict(path=str(target), timestamp=stamp(),
        identities=inventory(target), explicit_h5_alias=str(top.resolve())))
    return target


def query_command(plan, scope, index, scenario):
    folder = Path(plan["run"]) / scope
    query = Path(plan["query_directory"])
    benchmark = ini(folder / "search.ini")["Benchmark"]
    truth = Path(plan[scope]["truth"]) if scope == "preflight" else (
        query / f"groundtruth_{scenario}_local_ids.npy")
    command = [str(Path(plan["run"]) / "runtime/spannaclbench"),
               "--index", str(index.parent), "--queries", benchmark["Queries"],
               "--truth", str(truth), "--value-type", benchmark["ValueType"],
               "--search-ini", str(folder / "search.ini"), "--warmup", benchmark["Warmup"],
               "--max-queries", benchmark["MaxQueries"], "--topk", benchmark["TopK"], "--dump-results"]
    if scenario in ("numeric", "mixed_dnf"):
        suffix = "numeric" if scenario == "numeric" else "mixed"
        command += ["--query-dnf", str(query / f"query_dnf_{suffix}.npy")]
    elif scenario != "unfilter":
        suffix = scenario.removesuffix("_tag")
        command += ["--query-tags", str(query / f"query_tags_{suffix}.npy"), "--tag-column", "0"]
    return command


def results(path):
    values = [json.loads(line) for line in path.read_text().splitlines()
              if line.startswith('{"engine":')]
    require(len(values) == 1 and values[0]["failed_queries"] == 0,
            f"Missing/failed native query output: {path}")
    require(len(values[0]["result_ids"]) == values[0]["queries"] * 10,
            "Incomplete native result payload")
    return values[0]


def verify_completion(plan, scope):
    folder = Path(plan["run"]) / scope
    completed = json.loads((folder / "completion.json").read_text())
    require(completed["state"] == "complete" and completed["head_count"] == plan[scope]["rows"],
            "Completion marker does not match the declared native input")
    target = folder / "compact/tenant_0"
    actual = inventory(target)
    actual["../manifest.txt"] = identity(target.parent / "manifest.txt")
    require(actual == json.loads((folder / "dependencies.json").read_text()),
            "Completed runtime dependency identity changed")
    require(inventory(folder / "h1") == json.loads((folder / "graph-output.json").read_text()),
            "Completed H1 geometry identity changed")
    safety(plan)


def stages(plan, scope):
    run = Path(plan["run"])
    folder = run / scope
    config = ini(folder / "h1.ini")
    source = Path(plan[scope]["source"])
    complete = folder / "completion.json"
    if complete.exists():
        verify_completion(plan, scope)
        return
    graph_command = [str(run / "builder/bin/indexbuilder"),
                     "-i", config["Input"]["Vectors"], "-o", config["Input"]["Output"],
                     "-a", config["Input"]["Algorithm"], "-v", config["Input"]["ValueType"],
                     "-d", config["Input"]["Dim"], "-f", config["Input"]["FileType"],
                     "-t", config["Index"]["NumberOfThreads"], "-c", str(folder / "h1.ini")]
    if not (folder / "graph.stage.json").exists():
        require(not (folder / "h1").exists(), "Partial graph output already exists")
    execute(plan, scope, "graph", graph_command)
    graph = folder / "h1"
    count = plan[scope]["rows"]
    require(matrix(graph / "vectors.bin", 1, 128)[0] == count, "Built vector count differs")
    require(matrix(graph / "graph.bin", 4, 32)[0] == count, "Built graph count differs")
    verified = folder / "preserved-order.json"
    if not verified.exists():
        save(verified, compare_vectors(source / "SPTAGHeadVectors.bin", graph / "vectors.bin"))
    geometry = folder / "graph-output.json"
    if geometry.exists():
        require(json.loads(geometry.read_text()) == inventory(graph),
                "Completed H1 graph output identity changed")
    else:
        save(geometry, inventory(graph))
    bridge = bridge_view(plan, scope)
    target = folder / "compact" / "tenant_0"
    target.parent.mkdir(exist_ok=True)
    wrapper_manifest = target.parent / "manifest.txt"
    if not wrapper_manifest.exists():
        wrapper_manifest.symlink_to(source.parent / "manifest.txt")
    if not (folder / "convert.stage.json").exists():
        require(not target.exists(), "Partial compact output retained")
    execute(plan, scope, "convert", [str(run / "runtime/compactspannindex"), str(bridge), str(target)])
    report = json.loads((target / "compact-conversion.json").read_text())
    require(report["head_count"] == count and report["metadata_version"] == 9 and
            report["head_stride"] == 176 and report["metadata_input_chunk_rows"] == 4096,
            "Native compact accounting differs from expected authenticated layout")
    execute(plan, scope, "load", [str(run / "runtime/nativestorageload"), str(target)])
    scenarios = ["unfilter"] if scope == "preflight" else [
        "unfilter", "broad_tag", "medium_tag", "extreme_tag", "numeric", "mixed_dnf"]
    if scope == "preflight":
        execute(plan, scope, "v8-query", query_command(plan, scope, bridge, "unfilter"))
    smoke = {}
    for scenario in scenarios:
        name = f"query-{scenario}"
        execute(plan, scope, name, query_command(plan, scope, target, scenario))
        smoke[scenario] = results(folder / f"{name}.log")
        if scenario == "unfilter":
            require(any(value >= 0 for value in smoke[scenario]["result_ids"]),
                    "Unfiltered native smoke returned no results")
    if scope == "preflight":
        before = results(folder / "v8-query.log")
        after = smoke["unfilter"]
        excluded = {"qps", "mean_latency_ms"}
        require({k: v for k, v in before.items() if k not in excluded} ==
                {k: v for k, v in after.items() if k not in excluded},
                "Graphful V8/V9 native output or work parity failed")
        save(folder / "native-parity.json", dict(passed=True, before=before, after=after,
             checked="All reported fields except ordinary latency/QPS, including exact final IDs and SSD counters"))
    safety(plan)
    dependencies = inventory(target)
    dependencies["../manifest.txt"] = identity(target.parent / "manifest.txt")
    unique = {(value["device"], value["inode"]): value for value in dependencies.values()}
    private = {key: value for key, value in unique.items()
               if Path(value["path"]).is_relative_to(run)}
    save(folder / "dependencies.json", dependencies)
    save(complete, dict(state="complete", scope=scope, completed_at=stamp(),
         head_count=count, native_conversion=report, smoke=smoke,
         stages={p.stem: json.loads(p.read_text()) for p in sorted(folder.glob("*.stage.json"))},
         unique_runtime_dependency_bytes=sum(v["size"] for v in unique.values()),
         new_runtime_dependency_bytes=sum(v["size"] for v in private.values()),
         source_unchanged=True, selection_reused=True, ssd_rebuilt=False,
         performance_scope="16-query smoke only; no full 1B performance/scalability acceptance"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    prepare_parser = sub.add_parser("prepare")
    for name in ("run", "repo", "runtime", "source", "preflight-source",
                 "recipe", "queries", "prefix-truth"):
        prepare_parser.add_argument("--" + name, type=Path, required=True)
    prepare_parser.add_argument("--threads", type=int, default=16)
    prepare_parser.add_argument("--cpus", default="48-63")
    prepare_parser.add_argument("--node", type=int, default=2)
    for operation in ("preflight", "run"):
        child = sub.add_parser(operation)
        child.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "prepare":
        prepare(args)
        return
    run = args.run.resolve()
    lock = (run / "controller.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    plan = json.loads((run / "plan.json").read_text())
    require(str(run) == plan["run"], "Run identity mismatch")
    require(digest(run / "builder/bin/indexbuilder") ==
            json.loads((run / "builder-runtime.json").read_text())["sha256"],
            "Builder identity mismatch")
    safety(plan)
    confine(run)
    resource.setrlimit(resource.RLIMIT_AS, (256 << 30, 256 << 30))
    try:
        if args.operation == "run":
            require((run / "preflight/completion.json").exists(), "Complete UInt8 preflight required")
            verify_completion(plan, "preflight")
            stages(plan, "production")
        else:
            stages(plan, "preflight")
        save(run / "status.json", dict(state="complete" if args.operation == "run" else "preflight_complete",
             controller_pid=os.getpid(), updated_at=stamp()))
    except BaseException as error:
        save(run / "status.json", dict(state="failed", error=str(error),
             controller_pid=os.getpid(), updated_at=stamp(), partial_outputs_retained=True))
        raise


if __name__ == "__main__":
    main()
