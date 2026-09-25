"""Extend a NEW isolated frozen runtime to joint native scenario validation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

source = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
marker = source.parent / "scenario_integration.json"
if marker.exists():
    raise ValueError("Scenario integration already exists; preserve its measured source")
subprocess.run([sys.executable, str(here.parent / "full/integrate.py"), str(source)], check=True)
changes = {}


def replace(text, old, new):
    if text.count(old) != 1:
        raise ValueError((old[:100], text.count(old)))
    return text.replace(old, new)


def patch(name, transform):
    path = source / name
    old = path.read_bytes()
    new = transform(old.decode())
    path.write_text(new)
    changes[name] = {"before": hashlib.sha256(old).hexdigest(),
                     "after": hashlib.sha256(path.read_bytes()).hexdigest()}


def hooks(text):
    text = replace(text, "bool exhausted = false, invoked = false;",
                   "std::uint64_t csrDistances = 0, csrAssignments = 0, graphChecked = 0;\n"
                   "    bool exhausted = false, invoked = false;")
    text = replace(text, 'mode != "rewire8")', 'mode != "rewire8" && mode != "hierarchy")')
    text = replace(text, '(mode == "ordinary" && cap)', '((mode == "ordinary" || mode == "hierarchy") && cap)')
    return replace(text, 'get("headnavigationmode") != "H1Only"',
                   'get("headnavigationmode") != (mode == "hierarchy" ? "H2Only" : "H1Only")')


def spann(text):
    text = replace(text, 'p_queryResults->GetResultNum() != 24 || queryTags || queryDNF)',
                   'p_queryResults->GetResultNum() != 24)')
    start = text.index("            if (ShortcutFull::capture) {")
    end = text.index("            if (ret != ErrorCode::Success) return ret;", start)
    text = text[:start] + text[end:]
    anchor = "    // Diagnostic: dump the selected head set (m_index-local hid + dist) for the\n"
    text = replace(text, anchor, """
    if (ShortcutFull::mode == "hierarchy") {
        if (!usedSecondLevelSearch) throw std::runtime_error("Expected original H3 hierarchy path");
        ShortcutFull::Last().invoked = true;
    }
    if (ShortcutFull::capture) {
        for (int i = 0; i < p_queryResults->GetResultNum(); ++i) {
            ShortcutFull::Last().heads.push_back(p_queryResults->GetResult(i)->VID);
            ShortcutFull::Last().distances.push_back(p_queryResults->GetResult(i)->Dist);
        }
    }
""" + anchor)
    text = replace(text, "    const ErrorCode status = SearchSecondLevelHierarchy(", """
    ShortcutBench::State countState;
    countState.profile = ShortcutFull::profile;
    auto* lowerBKT = dynamic_cast<BKT::Index<T>*>(m_index.get());
    auto* topBKT = dynamic_cast<BKT::Index<T>*>(m_secondLevelIndexes.back().get());
    if (!lowerBKT || !topBKT || lowerBKT == topBKT || ShortcutFull::cap != 0)
        throw std::runtime_error("Unexpected original H3 distance endpoints");
    std::function<float(const T*, const T*, DimensionType)> lowerDistance, topDistance;
    if (countState.profile) {
        ShortcutBench::active = &countState;
        lowerDistance = lowerBKT->BenchmarkDistance(false);
        topDistance = topBKT->BenchmarkDistance(false);
    }
    ErrorCode status;
    try {
        status = SearchSecondLevelHierarchy(""")
    text = replace(text, """        m_options.m_secondLevelGraphSignaturePruning);
    if (status != ErrorCode::Success) return status;""", """
        m_options.m_secondLevelGraphSignaturePruning);
    } catch (...) {
        if (countState.profile) {
            lowerBKT->BenchmarkRestore(std::move(lowerDistance));
            topBKT->BenchmarkRestore(std::move(topDistance));
            ShortcutBench::active = nullptr;
        }
        throw;
    }
    if (countState.profile) {
        lowerBKT->BenchmarkRestore(std::move(lowerDistance));
        topBKT->BenchmarkRestore(std::move(topDistance));
        ShortcutBench::active = nullptr;
        if (countState.used < hierarchyStats.m_uniqueScanned)
            throw std::runtime_error("Hierarchy callback accounting mismatch");
    }
    ShortcutFull::Last().calls = countState.used;
    ShortcutFull::Last().adjacency = countState.adjacency;
    ShortcutFull::Last().csrDistances = hierarchyStats.m_uniqueScanned;
    ShortcutFull::Last().csrAssignments = hierarchyStats.m_assignments;
    ShortcutFull::Last().graphChecked = hierarchyStats.m_graphScanned;
    if (status != ErrorCode::Success) return status;""")
    return text


def bench(text):
    text = replace(text, 'v.shortcutCalls != o.shortcutCalls || v.exhausted != o.exhausted)',
                   'v.shortcutCalls != o.shortcutCalls || v.exhausted != o.exhausted ||\n'
                   '                        v.csrDistances != o.csrDistances || v.csrAssignments != o.csrAssignments ||\n'
                   '                        v.graphChecked != o.graphChecked)')
    return replace(text, '<< ",\\"exhausted\\":" << o.exhausted << ",\\"heads\\":[";',
                   '<< ",\\"exhausted\\":" << o.exhausted\n'
                   '                     << ",\\"csr_distances\\":" << o.csrDistances\n'
                   '                     << ",\\"csr_assignments\\":" << o.csrAssignments\n'
                   '                     << ",\\"graph_checked\\":" << o.graphChecked << ",\\"heads\\":[";')


patch("AnnService/FullHooks.h", hooks)
patch("AnnService/src/Core/SPANN/SPANNIndex.cpp", spann)
patch("Tools/benchmarks/SpannAclBench.cpp", bench)
marker.write_text(json.dumps(changes, indent=2) + "\n")
