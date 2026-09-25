"""Native launcher with explicit exit-status handling for a dying-process procfs race."""
import json
from pathlib import Path
import resource
import subprocess
import time

from official_benchmark_config import require, write_json
from run_spann_memory_diagnostics import process_snapshot
from run_vanilla_spann_comparison import observe_posting_io


def run_native(command, output, name, interval, expected_direct):
    prefix = output / name
    write_json(output / f"{name}.command.json", {"command": command, "cwd": str(output)})
    logs = [Path(str(prefix) + suffix) for suffix in (".stdout.log", ".stderr.log")]
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    sampling_exit_race = None
    with logs[0].open("xb") as stdout, logs[1].open("xb") as stderr, (
            output / f"{name}.resources.jsonl").open("x") as samples:
        child = subprocess.Popen(command, cwd=output, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        try:
            observe_posting_io(child.pid, output / f"{name}.io.json", expected_direct)
            while child.poll() is None:
                try:
                    sample = process_snapshot(child.pid)
                except (FileNotFoundError, ProcessLookupError, PermissionError) as error:
                    # Do not retry/reopen a denied procfs file. Accept only a
                    # promptly exited child, and independently require exit0.
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        raise error
                    sampling_exit_race = str(error)
                    break
                samples.write(json.dumps(sample) + "\n")
                samples.flush()
                time.sleep(interval)
            write_json(output / f"{name}.exit.json", {
                "returncode": child.returncode, "sampling_exit_race": sampling_exit_race})
            require(child.returncode == 0, f"Native query failed; see {logs[1]}")
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    write_json(output / f"{name}.usage.json", {
        k: getattr(after, k) - getattr(before, k) for k in
        ("ru_utime", "ru_stime", "ru_minflt", "ru_majflt", "ru_nvcsw", "ru_nivcsw", "ru_inblock", "ru_oublock")})
    return logs
