"""Adapt the frozen full-query capture harness to MAIN's shared native INI API."""
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "predicate_degree_batch"


def replace(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError(f"Expected one integration site: {before[:100]}")
    return text.replace(before, after)


def main():
    for name in ("SpannAclBench.cpp", "run.py", "verify.py"):
        if (HERE / name).exists():
            raise RuntimeError("Never overwrite an existing integration")
    text = (OLD / "SpannAclBench.cpp").read_text()
    text = replace(text, '#include "NativeBatch.h"',
                   '#include "NativeBatch.h"\n#include "NativeNProbeSweep.h"')
    site = "std::vector<SearchPoint> ReadSearchIni(const std::string& p_path)"
    text = replace(text, site, """int ScalarSearchCount(const std::string& value, int minimum)
{
    if (value.find_first_of("[,]") != std::string::npos)
        throw std::runtime_error("Arrays belong only in [SearchSweep] NProbe");
    const auto values = NativeNProbeSweep::Parse(value, minimum);
    if (values.size() != 1) throw std::runtime_error("Expected scalar native search count");
    return values.front();
}

""" + site)
    text = replace(text, """    const auto probes = NativeBatch::Probes(probeField->second, NativeBatch::Positive(resultField->second));
    const bool array = probeField->second.find_first_of("[,") != std::string::npos;""",
    """    const int resultNum = ScalarSearchCount(resultField->second, 1);
    const int scalarProbe = ScalarSearchCount(probeField->second, resultNum);
    const bool array = searchIni.DoesSectionExist("SearchSweep");
    if (array && (searchIni.GetParameters("SearchSweep").size() != 1 ||
                  !searchIni.DoesParameterExist("SearchSweep", "NProbe")))
        throw std::runtime_error("[SearchSweep] must contain only NProbe");
    const auto probes = array
        ? NativeNProbeSweep::Parse(
              searchIni.GetParameter<std::string>("SearchSweep", "NProbe", ""), resultNum)
        : std::vector<int>{scalarProbe};""")
    text = replace(text, "if (!options.searchSweepInis.empty() && defaults.size() > 1)",
                   "if (!options.searchSweepInis.empty() && !defaults.empty() && defaults.front().array)")
    text = replace(text, 'throw std::runtime_error("Array --search-ini cannot also specify --search-sweep-ini");',
                   'throw std::runtime_error("Put [SearchSweep] in the active --search-sweep-ini, not the base INI");')
    text = replace(text, 'NativeBatch::Positive(point.parameters.at("resultnum"))',
                   'ScalarSearchCount(point.parameters.at("resultnum"), 1)')
    text = replace(text, '<< "\\"native_index_load_calls\\":" << NativeBatch::indexLoadCalls << ","',
                   '<< "\\"index_load_count\\":" << NativeBatch::indexLoadCalls << ","\n'
                   '                      << "\\"sweep_execution\\":\\"" << (searchPoint.array ? '
                   '"single_load_nprobe_array" : "single_probe") << "\\","\n'
                   '                      << "\\"native_index_load_calls\\":" << NativeBatch::indexLoadCalls << ","')
    (HERE / "SpannAclBench.cpp").write_text(text)
    text = (OLD / "run.py").read_text()
    text = replace(text, 'import argparse\n', 'import argparse\nimport copy\nimport hashlib\n')
    text = replace(text, '"native_workspace_resets", "capture_file"}',
                   '"native_workspace_resets", "capture_file", "index_load_count", "sweep_execution"}')
    text = replace(text, 'row["native_index_load_calls"] == 1 and row["native_workspace_resets"] == i + 1,',
                   'row["native_index_load_calls"] == row["index_load_count"] == 1 and\n'
                   '                row["native_workspace_resets"] == i + 1,')
    before = '''        self.references = {(r["job"]["scenario"], r["job"]["case"], r["job"]["nprobe"]): r
                           for r in json.loads((self.previous / "runs.json").read_text())
                           if r["job"]["kind"] == "plain" and r["job"]["repeat"] == 1}'''
    after = '''        self.references = {
            (point["scenario"], point["case"], point["nprobe"]):
                {**point, "directory": run["directory"]}
            for run in json.loads((self.previous / "runs.json").read_text()) if run["repeat"] == 1
            for point in run["points"]}'''
    text = replace(text, before, after)
    text = replace(text, 'self.previous / "plain_r1_unfilter_h1_p24/native.io.json"',
                   'self.previous / "batch_r1_unfilter_h1/native.io.json"')
    text = replace(text, '"--search-sweep-ini", str(search), "--value-type", "Float", "--topk", "10",',
                   '("--search-ini" if "_fixture_desc" in config or "_scalar_" in config\n'
                   '                    else "--search-sweep-ini"), str(search), "--value-type", "Float", "--topk", "10",')
    text = replace(text, '''            validation = audit.audit_queries(capture, count, probe, case,
                                             self.truth[scenario], self.masks[scenario])''',
    '''            reference = self.references[scenario, case, probe]
            if count == 1000:
                digest = hashlib.sha256()
                with audit.query_stream(capture) as stream:
                    for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                        digest.update(block)
                require(digest.hexdigest() == reference["validation"]["raw_capture_sha256"],
                        "New native capture is not byte-identical to the fully audited parent capture")
                validation = copy.deepcopy(reference["validation"])
            else:
                validation = audit.audit_queries(capture, count, probe, case,
                                                 self.truth[scenario], self.masks[scenario])''')
    text = replace(text, '"validation": validation, "scalar_reference": reference["directory"]}',
                   '"validation": validation, "scalar_reference": reference["directory"],\n'
                   '                     "validation_method": ("byte_exact_audited_parent_capture" if count == 1000\n'
                   '                                           else "full_native_fixture_audit")}')
    text = replace(text, '"timing_protocol": "native_load_once_nprobe_array"}',
                   '"timing_protocol": "native_load_once_nprobe_array",\n'
                   '                           "sweep_execution": "single_load_nprobe_array",\n'
                   '                           "nprobe_ini_api": "SearchSweep.NProbe"}')
    text = replace(text, '"timing_protocol": "Fresh load-once native arrays; no previous per-process timings spliced.",',
                   '"timing_protocol": "Fresh SearchSweep.NProbe arrays; no previous timings spliced.",\n'
                   '            "sweep_execution": "single_load_nprobe_array",\n'
                   '            "nprobe_ini_api": "SearchSweep.NProbe",\n'
                   '            "full_capture_validation": "Every byte matched the fully audited parent native capture; "\n'
                   '                                       "fresh fixtures additionally received full semantic audits",')
    text = replace(text, '"previous_scalar_protocol": "Completed and preserved; superseded for current batch timing, not relabeled incomplete.",',
                   '"previous_scalar_protocol": "Parent is an independently verified native array dataset; "\n'
                   '                                            "its old INI spelling is superseded, timings are not reused.",')
    (HERE / "run.py").write_text(text)
    text = (OLD / "verify.py").read_text()
    text = replace(text, '"Empty nprobe array" in result.stderr',
                   '"Invalid [SearchSweep] NProbe" in result.stderr')
    text = replace(text, 'for row in rows:\n        key =', 'for row in rows:\n'
                   '        assert row["sweep_execution"] == "single_load_nprobe_array"\n'
                   '        assert row["nprobe_ini_api"] == "SearchSweep.NProbe"\n        key =')
    text = replace(text, '"verification_source": batch.fingerprint(root / "verification_source.py"),',
                   '"verification_source": batch.fingerprint(root / "verification_source.py"),\n'
                   '        "sweep_execution": "single_load_nprobe_array",\n'
                   '        "nprobe_ini_api": "SearchSweep.NProbe",')
    (HERE / "verify.py").write_text(text)
    print("Integrated MAIN parser/API into isolated native full-query harness; no MAIN-owned file edited.")


if __name__ == "__main__":
    main()
