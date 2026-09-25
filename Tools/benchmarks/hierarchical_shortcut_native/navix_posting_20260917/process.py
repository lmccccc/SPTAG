"""Single native process, buffered-FD proof and persistent failure evidence."""
import json
import os
from pathlib import Path
import subprocess
import time
from prepare import write


def require(value, message):
    if not value:
        raise RuntimeError(message)


def process(command, directory):
    write(directory / "command.json", {"command": command, "cwd": str(directory)})
    start = time.monotonic()
    observed = {}

    def save_io():
        with (directory / "io.json").open("w") as stream:
            json.dump({"buffered": bool(observed), "observations": [
                {"target": target, "flags": flags, "samples": samples}
                for (target, flags), samples in observed.items()]}, stream, indent=2)

    with (directory / "stdout.log").open("x") as stdout, (directory / "stderr.log").open("x") as stderr:
        child = subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        try:
            while child.poll() is None:
                fdroot = Path("/proc") / str(child.pid)
                try:
                    descriptors = list((fdroot / "fd").iterdir())
                except (FileNotFoundError, ProcessLookupError, PermissionError) as error:
                    child.wait(timeout=5)
                    require(child.returncode == 0 and observed, "Procfs failure without validated native exit")
                    write(directory / "sampling_exit_race.json", {"error": str(error), "returncode": 0})
                    break
                for fd in descriptors:
                    try:
                        target = os.readlink(fd)
                        if not target.endswith("/SPTAGFullList.bin"):
                            continue
                        fields = dict(line.split(":", 1) for line in
                                      (fdroot / "fdinfo" / fd.name).read_text().splitlines() if ":" in line)
                        flags = int(fields["flags"].strip(), 8)
                        require(not flags & os.O_DIRECT, "SSD handle unexpectedly uses O_DIRECT")
                        observed[(target, flags)] = observed.get((target, flags), 0) + 1
                        save_io()
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    except PermissionError as error:
                        child.wait(timeout=5)
                        require(child.returncode == 0 and observed, "Descriptor denied while native remained active")
                        write(directory / "sampling_exit_race.json", {"error": str(error), "returncode": 0})
                        break
                time.sleep(.02)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=30)
            write(directory / "exit.json", {"returncode": child.returncode})
    save_io()
    require(child.returncode == 0 and observed, "Native failure or unverified buffered IO: " + str(directory))
    return time.monotonic() - start
