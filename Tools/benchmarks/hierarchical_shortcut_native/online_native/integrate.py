"""Generate the online owner policy in a new authenticated full native runtime."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
subprocess.run([sys.executable, str(here.parent / "full_scenarios/integrate.py"), str(root)], check=True)
changes = {}


def sub(text, before, after):
    assert text.count(before) == 1, (before[:100], text.count(before))
    return text.replace(before, after)


def patch(name, transform):
    p = root / name
    old = p.read_bytes()
    p.write_text(transform(old.decode()))
    changes[name] = {"before": hashlib.sha256(old).hexdigest(),
                     "after": hashlib.sha256(p.read_bytes()).hexdigest()}


FIELDS = ("onlineGraph onlineParent onlineChild onlineCache onlineBatches onlineUpH1 onlineUpH2 "
          "onlineRowsH2 onlineRowsH3 onlineCandidates onlineEligible onlineTrace").split()


def hooks(t):
    t = sub(t, 'inline bool profile = false, capture = false;',
            'inline bool profile = false, capture = false;\ninline int onlineSeed = 128, onlineBatch = 16;')
    t = sub(t, "struct Observation {", "struct Observation {\n    std::uint64_t " +
            ", ".join(f"{k} = 0" for k in FIELDS) + ";\n    std::vector<int> ownIDs;")
    t = sub(t, 'mode != "hierarchy")', 'mode != "hierarchy" && mode != "online" && mode != "admit")')
    t = sub(t, 'p.first != "shortcutoutput" && p.first != "shortcutedges")',
            'p.first != "shortcutoutput" && p.first != "shortcutedges" &&\n'
            '            p.first != "shortcutseed" && p.first != "shortcutbatch")')
    t = sub(t, '    LoadEdges(get("shortcutedges"));', """
    if (mode == "online" || mode == "admit") {
        onlineSeed = Unsigned(get("shortcutseed"));
        onlineBatch = Unsigned(get("shortcutbatch"));
        if (onlineSeed != 128 || onlineBatch != 16 || !cap)
            throw std::runtime_error("Require fixed seed128/batch16 and a positive unified budget");
    } else LoadEdges(get("shortcutedges"));""")
    return t


def header(t):
    return sub(t, "            auto BenchmarkDistance(bool capped) {", """
            auto BenchmarkGetDistance() const { return m_fComputeDistance; }
            const SizeType* BenchmarkGraphRow(int id) const { return m_pGraph[id]; }
            int BenchmarkGraphWidth() const { return m_pGraph.m_iNeighborhoodSize; }
            auto BenchmarkDistance(bool capped) {""")


def spann(t):
    t = '#include "NativeAdapter.h"\n' + t
    t = sub(t, "    if (useHierarchyNavigation && m_extraSearcher != nullptr)",
            "    if ((useHierarchyNavigation || OnlineOwners::Enabled()) && m_extraSearcher != nullptr)")
    anchor = "            auto* bkt = dynamic_cast<BKT::Index<T>*>(m_index.get());"
    t = sub(t, anchor, """
            if (OnlineOwners::Enabled()) {
                auto* onlineBKT = dynamic_cast<BKT::Index<T>*>(m_index.get());
                if (!onlineBKT || !headPointResults || !admitHeadPoint)
                    throw std::runtime_error("Online search requires native own-head admission workspace");
                OnlineOwners::NativeSearch(onlineBKT, p_queryResults, m_secondLevelCatalogs,
                    m_secondLevelPostings, secondLevelHeadAdmission, admitHeadPoint);
                ret = ErrorCode::Success;
            } else {
""" + anchor)
    t = sub(t, """            if (ret != ErrorCode::Success) return ret;
        }
    }


    if (ShortcutFull::mode == "hierarchy")""", """            if (ret != ErrorCode::Success) return ret;
            }
        }
    }


    if (ShortcutFull::mode == "hierarchy")""")
    t = sub(t, """    if (ShortcutFull::capture) {
        for (int i = 0; i < p_queryResults->GetResultNum(); ++i) {""", """
    if (ShortcutFull::capture) {
        if (headPointResults)
            for (auto& point : *headPointResults)
                if (point.second >= 0) ShortcutFull::Last().ownIDs.push_back(point.second);
        for (int i = 0; i < p_queryResults->GetResultNum(); ++i) {""")
    return t


def bench(t):
    t = sub(t, "                    o.heads = v.heads;", """
                    if (ShortcutFull::profile && v.onlineTrace != o.onlineTrace)
                        throw std::runtime_error("Capture changed online spatial trace");
                    o.onlineTrace = v.onlineTrace;
                    o.ownIDs = v.ownIDs;
                    o.heads = v.heads;""")
    anchor = '<< ",\\"graph_checked\\":" << o.graphChecked << ",\\"heads\\":[";'
    replacement = '<< ",\\"graph_checked\\":" << o.graphChecked\n'
    for field in FIELDS:
        replacement += f'                     << ",\\"{field}\\":" << o.{field}\n'
    replacement += '                     << ",\\"own_ids\\":[";\n'
    replacement += """                for (std::size_t j = 0; j < o.ownIDs.size(); ++j) {
                    if (j) dump << ",";
                    dump << o.ownIDs[j];
                }
                dump << "],\\"heads\\":[";"""
    return sub(t, anchor, replacement)


patch("AnnService/FullHooks.h", hooks)
patch("AnnService/inc/Core/BKT/Index.h", header)
patch("AnnService/src/Core/SPANN/SPANNIndex.cpp", spann)
patch("Tools/benchmarks/SpannAclBench.cpp", bench)
for name in ("OnlineNavigation.h", "NativeAdapter.h"):
    shutil.copy2(here / name, root / "AnnService" / name)
(root.parent / "online_integration.json").write_text(json.dumps(changes, indent=2) + "\n")
