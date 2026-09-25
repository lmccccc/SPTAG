"""Generate isolated ABI-preserving BKT access and bounded adjacency hooks."""
import hashlib
import json
from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
header = source / "AnnService/inc/Core/BKT/Index.h"
cpp = source / "AnnService/src/Core/BKT/BKTIndex.cpp"
text = header.read_text()
anchor = "        public:\n            Index()\n"
assert text.count(anchor) == 1
shim = """        public:
            const SizeType* BenchmarkGraphRow(int id) const { return m_pGraph[id]; }
            int BenchmarkGraphWidth() const { return m_pGraph.m_iNeighborhoodSize; }
            auto BenchmarkWrapDistance(std::uint64_t* counter)
            {
                auto original = m_fComputeDistance;
                m_fComputeDistance = [original, counter](const T* a, const T* b, DimensionType d) {
                    if (counter) ++*counter;
                    return original(a, b, d);
                };
                return original;
            }
            auto BenchmarkBudgetDistance()
            {
                auto original = m_fComputeDistance;
                m_fComputeDistance = [original](const T* a, const T* b, DimensionType d) {
                    auto* state = ShortcutBench::active;
                    if (state) {
                        if (!state->remaining) throw ShortcutBench::BudgetExhausted{};
                        --state->remaining;
                        if (state->profile) {
                            ++state->used;
                            if (state->inShortcut) ++state->shortcutDistances;
                        }
                    }
                    return original(a, b, d);
                };
                return original;
            }
            void BenchmarkRestoreDistance(std::function<float(const T*, const T*, DimensionType)> original)
            {
                m_fComputeDistance = std::move(original);
            }
            Index()
"""
patched = '#include "ShortcutHooks.h"\n' + text.replace(anchor, shim)
dest = output / "inc/Core/BKT/Index.h"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(patched)
text = cpp.read_text()
start = text.index("void Index<T>::Search(COMMON::QueryResultSet<T>")
end = text.index("int Index<T>::SearchIterative(", start)
part = text[start:end]
anchor = """        for (DimensionType i = 0; i <= checkPos; i++)
        {
            SizeType nn_index = node[i];"""
replacement = """        int neighbors[64];
        const int neighborCount = ShortcutBench::Neighbors(node, checkPos + 1, gnode.node, neighbors);
        for (DimensionType i = 0; i < neighborCount; i++)
        {
            SizeType nn_index = neighbors[i];
            if (ShortcutBench::active && ShortcutBench::active->profile)
                ++ShortcutBench::active->adjacency;"""
assert part.count(anchor) == 1
part = part.replace(anchor, replacement)
anchor = """            float distance2leaf =
                m_fComputeDistance(p_query.GetQuantizedTarget(), (m_pSamples)[nn_index], GetFeatureDim());"""
replacement = """            if (ShortcutBench::active && ShortcutBench::active->profile) {
                auto& state = *ShortcutBench::active;
                state.inShortcut = state.edges &&
                    std::find((*state.edges)[gnode.node].begin(), (*state.edges)[gnode.node].end(), nn_index)
                    != (*state.edges)[gnode.node].end();
            }
            float distance2leaf =
                m_fComputeDistance(p_query.GetQuantizedTarget(), (m_pSamples)[nn_index], GetFeatureDim());
            if (ShortcutBench::active) ShortcutBench::active->inShortcut = false;"""
assert part.count(anchor) == 1
part = part.replace(anchor, replacement)
text = text[:start] + part + text[end:]
start = text.index("template <typename T> ErrorCode Index<T>::SearchIndex(QueryResult &p_query, bool p_searchDeleted) const")
anchor = """    SearchIndex(*((COMMON::QueryResultSet<T> *)&p_query), *workSpace, p_searchDeleted, true);"""
position = text.index(anchor, start)
replacement = """    try {
        SearchIndex(*((COMMON::QueryResultSet<T> *)&p_query), *workSpace, p_searchDeleted, true);
    } catch (const ShortcutBench::BudgetExhausted&) {
        ShortcutBench::active->exhausted = true;
        // Return already-scored frontier candidates; never score or restart here.
        while (!workSpace->m_NGQueue.empty()) {
            auto candidate = workSpace->m_NGQueue.pop();
            ((COMMON::QueryResultSet<T> *)&p_query)->AddPoint(candidate.node, candidate.distance);
        }
        ((COMMON::QueryResultSet<T> *)&p_query)->SortResult();
    }"""
text = text[:position] + text[position:].replace(anchor, replacement, 1)
(output / "BKTIndex.cpp").write_text(text)
records = {}
for name, path in [("upstream_header", header), ("upstream_cpp", cpp),
                   ("isolated_header", dest), ("isolated_cpp", output / "BKTIndex.cpp")]:
    records[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
(output / "instrumentation.json").write_text(json.dumps(records, indent=2) + "\n")
