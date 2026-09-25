"""Verify exclusive shared-API placement and legacy rejection before native loading."""
import configparser
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent


def main():
    config = configparser.ConfigParser()
    config.read(HERE / "experiment.ini")
    root = Path(config["Experiment"]["OutputDirectory"])
    directory = root / "api_failure_fixtures"
    directory.mkdir()
    active = root / "snapshot/experiment/configs/h1_fixture_asc.ini"
    cases = [
        ("legacy_array", ["--search-sweep-ini", str(HERE / "invalid_legacy_array.ini")],
         "Arrays belong only in [SearchSweep] NProbe"),
        ("unknown_key", ["--search-sweep-ini", str(HERE / "invalid_extra_key.ini")],
         "[SearchSweep] must contain only NProbe"),
        ("base_array", ["--search-ini", str(active), "--search-sweep-ini", str(active)],
         "Put [SearchSweep] in the active --search-sweep-ini, not the base INI"),
    ]
    evidence = []
    for name, args, error in cases:
        command = [str(root / "snapshot/spannaclbench"), "--index", "must-not-load",
                   "--queries", "must-not-read.npy", "--truth", "must-not-read.npy", *args]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        (directory / f"{name}.stdout.log").write_text(result.stdout)
        (directory / f"{name}.stderr.log").write_text(result.stderr)
        assert result.returncode == 2 and error in result.stderr, (name, result.stderr)
        assert "NATIVE_INDEX_LOAD" not in result.stdout + result.stderr
        evidence.append({"case": name, "command": command, "returncode": result.returncode,
                         "expected_error": error, "native_load_calls": 0})
    (directory / "validation.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print("PASS: legacy array, unknown SearchSweep key and misplaced base array reject before loading.")


if __name__ == "__main__":
    main()
