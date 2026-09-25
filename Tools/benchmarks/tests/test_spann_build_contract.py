"""Preflight and launcher contracts; all build processes use a fixture stub."""

import configparser
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest import mock
import uuid


ROOT = Path(__file__).resolve().parents[3]
BENCHMARKS = ROOT / "Tools" / "benchmarks"
SPEC = importlib.util.spec_from_file_location(
    "validate_spann_hierarchy_config", BENCHMARKS / "validate_spann_hierarchy_config.py"
)
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class NativePreflightContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.options = (ROOT / "AnnService/inc/Core/SPANN/Options.h").read_text()

    def test_canonical_h5_config(self):
        VALIDATOR.validate(VALIDATOR.read_config(
            BENCHMARKS / "build_spann_attr_sift1b_zipf200_limited_tag_h5.ini"
        ))

    def test_portable_adaptive_gettingstart_config(self):
        config = VALIDATOR.read_config(ROOT / "docs/AdaptiveSpann.ini")
        VALIDATOR.validate(config)
        self.assertEqual(config["base"]["indexalgotype"], "BKT")
        self.assertEqual(config["buildssdindex"]["storage"], "STATIC")
        self.assertEqual(config["tags"]["columntypes"], "categorical,numeric")
        self.assertEqual(config["selecthead"]["hierarchyenabled"], "true")
        self.assertEqual(config["build"]["buildsignatures"], "true")
        search = config["searchssdindex"]
        self.assertEqual(search["enablepostingnavigation"], "true")
        self.assertEqual(search["maxcheck"], "2048")
        self.assertEqual(search["postingadditionalmaxcheck"], "2048")
        self.assertEqual(search["postinganchorcount"], "8")
        self.assertEqual(search["disablecrossedges"], "true")

        removed_body = self.options.split("static bool IsRemovedParameter(", 1)[1].split(
            "static bool IsRemovedSectionAlias(", 1
        )[0]
        removed = {name.lower() for name in re.findall(r'"([A-Za-z0-9_]+)"', removed_body)}
        for section, values in config.items():
            with self.subTest(section=section):
                self.assertFalse(removed.intersection(values))

        text = (ROOT / "docs/GettingStart.md").read_text()
        match = re.search(r"```ini\n(\[SearchSSDIndex\]\n.*?)\n```", text, re.DOTALL)
        self.assertIsNotNone(match)
        documented = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
        documented.read_string(match.group(1))
        self.assertEqual(dict(documented["SearchSSDIndex"]), search)

    def test_every_native_removed_parameter_rejects_explicit_defaults(self):
        body = self.options.split("static bool IsRemovedParameter(", 1)[1].split(
            "static bool IsRemovedSectionAlias(", 1
        )[0]
        names = re.findall(r'"([A-Za-z0-9_]+)"', body)
        self.assertTrue(names)
        for name in names:
            for section in ("base", "selecthead", "buildhead", "buildssdindex",
                            "searchssdindex", "multitenant"):
                for value in ("", "false", "0"):
                    with self.subTest(name=name, section=section, value=value):
                        with self.assertRaisesRegex(ValueError, name.lower()):
                            VALIDATOR.validate({section: {name.lower(): value}})

    def test_every_native_removed_environment_rejects_presence(self):
        body = self.options.split("static bool ValidatePostingRuntimeEnvironment(", 1)[1].split(
            "ErrorCode SetParameter(", 1
        )[0]
        names = re.findall(r'"(SPTAG_[A-Z0-9_]+)"', body)
        self.assertTrue(names)
        for name in names:
            for value in ("", "false", "0"):
                with self.subTest(name=name, value=value), mock.patch.dict(
                    os.environ, {name: value}, clear=True
                ):
                    with self.assertRaisesRegex(ValueError, name):
                        VALIDATOR.validate({})

    def test_bulk_and_section_aliases_remain_rejected(self):
        keys = {
            "base": ("vectoroffset", "vectorcount", "withmetaindex", "sharebuildownership"),
            "multitenant": ("crossedges", "crossextraedges", "dualpoolaugment",
                            "dualpoolextraratio", "uextraidfile"),
            "searchssdindex": ("enableorderedpagestart", "orderedpagestartattrs"),
        }
        for section, names in keys.items():
            for name in names:
                for value in ("", "false", "0"):
                    with self.subTest(section=section, name=name, value=value):
                        with self.assertRaisesRegex(ValueError, name):
                            VALIDATOR.validate({section: {name: value}})
        with mock.patch.dict(os.environ, {"SPTAG_BUILD_SHARE_OWNERSHIP": ""}, clear=True):
            with self.assertRaisesRegex(ValueError, "SPTAG_BUILD_SHARE_OWNERSHIP"):
                VALIDATOR.validate({})

    def test_construction_layout_controls_remain_valid(self):
        VALIDATOR.validate({
            "selecthead": {"dualpoolaugment": "true"},
            "buildssdindex": {
                "tailreplicacount": "1", "unfiltertailbufferlength": "8",
                "enableorderedpagestart": "true", "orderedpagestartattrs": "0",
                "crossedges": "true", "crossextraedges": "10",
            },
            "searchssdindex": {"searchpostingpagelimit": "1"},
        })


BUILDER_STUB = """#!/usr/bin/env python3
import configparser
import json
import os
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
config_path = Path(sys.argv[sys.argv.index("-c") + 1])
parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",))
parser.read(config_path)
config = {section.lower(): dict(parser[section]) for section in parser.sections()}
signatures_only = "--build-signatures-only" in sys.argv
with (root / "builder-calls.jsonl").open("a") as calls:
    calls.write(json.dumps({
        "config": str(config_path),
        "signatures_only": signatures_only,
        "build_signatures": config.get("build", {}).get("buildsignatures"),
        "environment": {key: os.environ.get(key) for key in (
            "SPTAG_RESUME_BUILD", "SPTAG_PERSIST_SELECTHEAD", "SPTAG_SPANN_INPLACE_DIR")},
    }) + "\\n")
status = int(os.environ.get("STUB_BUILD_EXIT", "0"))
if status:
    sys.exit(status)
output = Path(config["base"]["indexdirectory"])
tenant = output / "tenant_0"
(tenant / "HeadIndex").mkdir(parents=True, exist_ok=True)
if signatures_only:
    (tenant / "HeadIndex/head_node_meta.bin").write_bytes(b"stub")
    (tenant / "signatures_bitmask.bin").write_bytes(b"stub")
else:
    shutil.copyfile(config_path, tenant / "indexloader.ini")
    (tenant / "SPTAGFullList.bin").write_bytes(b"stub")
    (output / "manifest.txt").write_text("tenant 0 1\\n")
    if config.get("selecthead", {}).get("dualpoolaugment", "false").lower() in ("true", "1"):
        (tenant / "head_role.bin").write_bytes(b"\\x00\\x01")
"""


class BuildLauncherContractTest(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "build" / f"spann_launcher_contract_{uuid.uuid4().hex}"
        self.scripts = self.root / "Tools/benchmarks"
        self.scripts.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        for name in ("run_spann_attr_build.sh", "validate_spann_hierarchy_config.py"):
            shutil.copyfile(BENCHMARKS / name, self.scripts / name)
        (self.root / "Release").mkdir()
        builder = self.root / "Release/spannbuilder"
        builder.write_text(BUILDER_STUB)
        builder.chmod(0o755)
        self.config_path = self.root / "build.ini"
        self.output = self.root / "index"
        self.work = self.root / "build-work"
        self.calls_path = self.root / "builder-calls.jsonl"

    def write_config(self, *, signatures=True, resume=False, output=None,
                     storage="STATIC", extra="", work=True, mixed_case=False):
        self.output = output or self.output
        text = (
            f"[Base]\nValueType=Float\nVectorType=DEFAULT\n"
            f"IndexDirectory={self.output}\n"
            f"[Build]\n; BuildSignatures=true is deferred only in this section.\n"
            f"BuildSignatures={str(signatures).lower()}\n"
            f"[BuildSSDIndex]\nStorage={storage}\n"
        )
        if work:
            text += f"TmpDir={self.work}\n"
        text += (
            f"[MultiTenant]\nResumeBuild={str(resume).lower()}\n"
            f"PersistSelectHead={str(resume).lower()}\nInPlaceBuild=false\n{extra}"
        )
        if mixed_case:
            text = text.replace("[Build]", "[bUiLd] ").replace(
                "\nBuildSignatures=true\n", "\nbUiLdSiGnAtUrEs = true\n"
            ).replace("\n", "\r\n")
        self.config_path.write_bytes(text.encode())
        return self.config_path.read_bytes()

    def run_launcher(self, **environment):
        env = os.environ.copy()
        env.update({
            "JEMALLOC_SO": str(self.root / "absent-jemalloc.so"),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        env.update(environment)
        return subprocess.run(
            ["bash", str(self.scripts / "run_spann_attr_build.sh"), str(self.config_path)],
            cwd=self.root, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=30, check=False,
        )

    def calls(self):
        return [json.loads(line) for line in self.calls_path.read_text().splitlines()]

    def test_removed_interfaces_fail_before_creating_output_or_calling_builder(self):
        for name in ("ForceDenseTagSearch", "DirectSparseMaxPostings",
                     "HeadNavigationMode", "HierarchyGraphSignaturePruning",
                     "EnableUnfilterTail"):
            with self.subTest(name=name):
                self.write_config(extra=f"[SearchSSDIndex]\n{name}=0\n")
                result = self.run_launcher()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(name.lower(), result.stdout)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.work.exists())
                self.assertFalse(self.calls_path.exists())

    def test_existing_fresh_output_is_never_wiped_or_implicitly_resumed(self):
        directory = self.root / "existing-directory"
        directory.mkdir()
        marker = directory / "keep"
        marker.write_text("preserve")
        empty_directory = self.root / "existing-empty-directory"
        empty_directory.mkdir()
        regular_file = self.root / "existing-file"
        regular_file.write_text("preserve")
        dangling = self.root / "existing-dangling-link"
        dangling.symlink_to(self.root / "absent-target")
        for output in (directory, empty_directory, regular_file, dangling):
            with self.subTest(output=output):
                self.write_config(output=output)
                result = self.run_launcher(SPTAG_RESUME_BUILD="1")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("refusing fresh build", result.stdout)
                self.assertTrue(os.path.lexists(output))
                self.assertFalse(self.calls_path.exists())
                self.assertFalse(self.work.exists())
        self.assertEqual(marker.read_text(), "preserve")
        self.assertEqual(regular_file.read_text(), "preserve")

    def test_resume_requires_an_existing_directory(self):
        self.write_config(resume=True)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("ResumeBuild requires an existing IndexDirectory", result.stdout)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.calls_path.exists())

    def test_explicit_resume_keeps_existing_files(self):
        self.output.mkdir()
        marker = self.output / "keep"
        marker.write_text("preserve")
        self.write_config(resume=True)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(marker.read_text(), "preserve")
        for call in self.calls():
            self.assertEqual(call["environment"]["SPTAG_RESUME_BUILD"], "1")
            self.assertEqual(call["environment"]["SPTAG_PERSIST_SELECTHEAD"], "1")

    def test_primary_config_is_preserved_after_success(self):
        original = self.write_config()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        primary = Path(calls[0]["config"])
        self.assertNotEqual(primary, self.config_path)
        self.assertEqual(primary.parent, self.work)
        self.assertIn(f"primary build config (preserved): {primary}", result.stdout)
        self.assertEqual(primary.read_bytes(), original.replace(
            b"\nBuildSignatures=true\n", b"\nBuildSignatures=false\n"
        ))
        self.assertEqual(primary.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertFalse(calls[0]["signatures_only"])
        self.assertEqual(calls[0]["build_signatures"], "false")
        self.assertEqual(calls[1]["config"], str(self.config_path))
        self.assertTrue(calls[1]["signatures_only"])
        self.assertEqual(calls[1]["build_signatures"], "true")

    def test_failed_primary_configs_remain_unique_and_preserve_native_text(self):
        primary_paths = []
        for run in range(2):
            original = self.write_config(
                output=self.root / f"failed-index-{run}", mixed_case=True
            )
            self.assertEqual(
                VALIDATOR.read_config(self.config_path)["build"]["buildsignatures"], "true"
            )
            result = self.run_launcher(STUB_BUILD_EXIT="27")
            self.assertEqual(result.returncode, 27, result.stdout)
            primary = Path(self.calls()[-1]["config"])
            self.assertIn(str(primary), result.stdout)
            self.assertEqual(primary.read_bytes(), original.replace(
                b"\r\nbUiLdSiGnAtUrEs = true\r\n", b"\r\nbUiLdSiGnAtUrEs =false\r\n"
            ))
            self.assertEqual(self.config_path.read_bytes(), original)
            self.assertEqual(VALIDATOR.read_config(primary)["build"]["buildsignatures"], "false")
            primary_paths.append(primary)
        self.assertNotEqual(*primary_paths)
        self.assertTrue(all(path.exists() for path in primary_paths))

    def test_native_ini_clears_inherited_build_environment(self):
        self.write_config(signatures=False)
        result = self.run_launcher(
            SPTAG_RESUME_BUILD="1", SPTAG_PERSIST_SELECTHEAD="1",
            SPTAG_SPANN_INPLACE_DIR=str(self.root / "wrong-index"),
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        calls = self.calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["config"], str(self.config_path))
        self.assertTrue(all(value is None for value in calls[0]["environment"].values()))
        self.assertFalse((self.root / "wrong-index").exists())

    def test_primary_config_default_location_is_project_local(self):
        self.write_config(work=False)
        result = self.run_launcher(TMPDIR=str(self.root / "ignored-environment-path"))
        self.assertEqual(result.returncode, 0, result.stdout)
        primary = Path(self.calls()[0]["config"])
        self.assertEqual(primary.parent, self.root / "build/primary-build-configs")
        self.assertTrue(primary.exists())
        self.assertFalse((self.root / "ignored-environment-path").exists())

    def test_uextra_validation_uses_native_selecthead_section(self):
        self.write_config(
            signatures=False, storage="FILEIO",
            extra="[SelectHead]\nDualPoolAugment=true\n",
        )
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue((self.output / "tenant_0/head_role.bin").exists())


if __name__ == "__main__":
    unittest.main()
