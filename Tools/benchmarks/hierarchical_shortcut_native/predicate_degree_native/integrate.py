"""Apply only predicate-valid connectivity accounting to a copied latest native runtime."""
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = Path(sys.argv[1]).resolve()


def once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f"Expected one native anchor: {old[:100]!r}")
    return text.replace(old, new)


path = ROOT / "AnnService/src/Core/BKT/BKTIndex.cpp"
t = path.read_text()
t = once(t, """                        targetIndex->m_pSamples.R() ||
                    p_space.CheckAndSet(targetKey))
                {
                    continue;
                }""", """                        targetIndex->m_pSamples.R())
                {
                    continue;
                }
                if (p_space.CheckAndSet(targetKey)) {
                    if (auto* c = H1Supplier::Active())
                        if (!c->engine->childCall) ++c->engine->stats.ordinaryVisited;
                    continue;
                }""")
t = once(t, """        const SizeType* node = currentIndex->m_pGraph[currentLocal];""", """
        const SizeType* node = currentIndex->m_pGraph[currentLocal];
        std::unique_ptr<H1Supplier::Connectivity> connectivity;
        if (auto* c = H1Supplier::Active())
            connectivity = std::make_unique<H1Supplier::Connectivity>(
                *c->engine, 0, currentLocal, node, localEdgeCount,
                &p_space.m_iNumberOfCheckedLeaves);""")
t = once(t, """c->engine->Supply(currentLocal, supplierFresh, supplierEffective, [&](int id) {
                    const int before = supplierFresh;""",
         """c->engine->Supply(currentLocal, supplierFresh, connectivity->audit.before, [&](int id) {
                    const bool connection = connectivity->AddSupplied(id);
                    const int before = supplierFresh;""")
t = once(t, """(supplierEffective != effectiveBefore ? 2 : 0);""",
         """(supplierEffective != effectiveBefore ? 2 : 0) | (connection ? 4 : 0);""")
start = t.index("    if (auto* c = H1Supplier::Active()) {\n        auto& e = *c->engine;\n        e.collectingEntry = false;")
end = t.index("\n    while (true)", start)
t = t[:start] + """
    if (auto* c = H1Supplier::Active()) {
        auto& e = *c->engine;
        e.collectingEntry = false;
        e.stats.entryCalls = e.stats.Used();
        e.stats.entryBefore = p_space.m_NGQueue.size();
        if (e.enabled) {
            ++e.stats.startupChecks;
            if (e.stats.entryBefore < static_cast<unsigned>(e.degree)) {
                const int anchor = e.EntryAnchor();
                const auto checked = p_space.m_iNumberOfCheckedLeaves;
                const auto calls = e.stats.Used(), qualified = e.stats.qualified;
                const auto rows = e.stats.h2Rows;
                e.stats.startupBefore = e.stats.entryBefore;
                if (anchor < 0) {
                    ++e.stats.startupBlocked;
                } else {
                    H1Supplier::Connectivity connectivity(e, 1, anchor,
                        m_pGraph[anchor], m_pGraph.m_iNeighborhoodSize,
                        &p_space.m_iNumberOfCheckedLeaves);
                    int fresh = 0, effective = 0;
                    if (checked < p_space.m_iMaxCheck) {
                        const auto& adjacent = connectivity.audit.ordinary;
                        expandEdges(anchor, 0, this, fresh, effective, adjacent.data(),
                            0, static_cast<DimensionType>(adjacent.size()), false);
                    }
                    if (connectivity.audit.before >= e.degree) {
                        ++e.stats.startupConnected;
                    } else if (p_space.m_iNumberOfCheckedLeaves >= p_space.m_iMaxCheck) {
                        ++e.stats.startupBlocked;
                    } else {
                        ++e.stats.startupCalls;
                        e.Supply(anchor, fresh, connectivity.audit.before, [&](int id) {
                            const bool connection = connectivity.AddSupplied(id);
                            const int rawBefore = fresh, qualifiedBefore = effective;
                            expandEdges(anchor, 0, this, fresh, effective, &id, 0, 1, false);
                            return (fresh != rawBefore ? 1 : 0) |
                                (effective != qualifiedBefore ? 2 : 0) | (connection ? 4 : 0);
                        }, [&] { return p_space.m_iNumberOfCheckedLeaves < p_space.m_iMaxCheck; });
                    }
                }
                e.stats.startupAfter = p_space.m_NGQueue.size();
                e.stats.startupQualified = e.stats.qualified - qualified;
                if (e.capture) e.startupAudits.push_back({
                    anchor, static_cast<std::int64_t>(e.stats.entryBefore),
                    static_cast<std::int64_t>(e.stats.startupAfter), checked,
                    p_space.m_iNumberOfCheckedLeaves,
                    static_cast<std::int64_t>(e.stats.Used() - calls),
                    static_cast<std::int64_t>(e.stats.startupQualified),
                    static_cast<std::int64_t>(e.stats.h2Rows - rows)});
            }
        }
    }
""" + t[end:]
path.write_text(t)

fields = ("ConnChecks", "ConnLow", "ConnBefore", "ConnAfter", "ConnAdded",
          "OrdinaryVisited", "StartupConnected")
path = ROOT / "AnnService/FullHooks.h"
t = '#include "Supplier.h"\n' + path.read_text()
t = once(t, "struct Observation {", "struct Observation {\n    std::uint64_t " +
         ", ".join("supply" + f + " = 0" for f in fields) +
         ";\n    std::vector<H1Supplier::ConnectivityAudit> connectivityAudits;")
path.write_text(t)

path = ROOT / "Tools/benchmarks/SpannAclBench.cpp"
t = path.read_text()
t = once(t, "o.startupAudits = v.startupAudits; o.discoveries = v.discoveries;",
         "o.startupAudits = v.startupAudits; o.discoveries = v.discoveries;\n"
         "                    o.connectivityAudits = v.connectivityAudits;")
t = once(t, '                     << ",\\"supplyGraph\\":" << o.supplyGraph',
         "".join('                     << ",\\"supply' + f + '\\":" << o.supply' + f + "\n" for f in fields) +
         '                     << ",\\"supplyGraph\\":" << o.supplyGraph')
anchor = '                dump << "],\\"entry_ids\\":[";'
dump = """
                dump << "],\\"connectivity_audits\\":[";
                for (size_t j = 0; j < o.connectivityAudits.size(); ++j) {
                    if (j) dump << ",";
                    const auto& a = o.connectivityAudits[j];
                    dump << "{\\"phase\\":" << a.phase << ",\\"node\\":" << a.node
                         << ",\\"width\\":" << a.width << ",\\"stop_slot\\":" << a.stopSlot
                         << ",\\"terminator\\":" << a.terminator << ",\\"before\\":" << a.before
                         << ",\\"after\\":" << a.after << ",\\"calls\\":" << a.calls
                         << ",\\"fresh\\":" << a.fresh << ",\\"fresh_qualified\\":" << a.freshQualified
                         << ",\\"visited\\":" << a.visited << ",\\"checked_before\\":" << a.checkedBefore
                         << ",\\"checked_after\\":" << a.checkedAfter << ",\\"ordinary\\":[";
                    for (size_t k = 0; k < a.ordinary.size(); ++k) {
                        if (k) dump << ",";
                        dump << "[" << a.ordinary[k] << "," << a.flags[k] << "]";
                    }
                    dump << "],\\"supplied\\":[";
                    for (size_t k = 0; k < a.supplied.size(); ++k) {
                        if (k) dump << ",";
                        dump << a.supplied[k];
                    }
                    dump << "]}";
                }
"""
t = once(t, anchor, dump + anchor)
path.write_text(t)
for name in ("Supplier.h", "NativeAdapter.h", "Signature.h"):
    shutil.copy2(HERE / name, ROOT / "AnnService" / name)
print("Integrated predicate-valid connectivity, independent fresh work, and per-node native evidence.")
