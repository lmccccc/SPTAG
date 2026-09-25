"""Patch only an authenticated isolated frozen source tree; no production writes."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
manifest = json.loads((root.parent / "authentication.json").read_text())["verified"]
changed = {}


def patch(name, transform):
    path = root / name
    saved = root.parent / "frozen" / name
    current = path.read_bytes()
    before = saved.read_bytes() if saved.exists() else current
    assert hashlib.sha256(before).hexdigest() == manifest[name], name
    if not saved.exists():
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(before)
    if current != before:
        previous = json.loads((root.parent / "integration.json").read_text())
        assert hashlib.sha256(current).hexdigest() == previous[name]["after"], name
    after = transform(before.decode("utf-8-sig").replace("\r\n", "\n"))
    after = after.replace("ShortcutFull::last", "ShortcutFull::Last()").encode()
    if after != current:
        path.write_bytes(after)
    changed[name] = {"before": manifest[name], "after": hashlib.sha256(path.read_bytes()).hexdigest()}


def replace(text, old, new):
    assert text.count(old) == 1, (old[:100], text.count(old))
    return text.replace(old, new)


def header(text):
    shim = """        public:
            auto BenchmarkDistance(bool capped) {
                auto original = m_fComputeDistance;
                m_fComputeDistance = [original, capped](const T* a, const T* b, DimensionType d) {
                    auto* state = ShortcutBench::active;
                    if (capped) {
                        if (!state->remaining) throw ShortcutBench::BudgetExhausted{};
                        --state->remaining;
                    }
                    if (state->profile) {
                        ++state->used;
                        if (state->inShortcut) ++state->shortcutDistances;
                    }
                    return original(a, b, d);
                };
                return original;
            }
            void BenchmarkRestore(std::function<float(const T*, const T*, DimensionType)> original) {
                m_fComputeDistance = std::move(original);
            }
            Index()
"""
    return '#include "ShortcutHooks.h"\n' + replace(text, "            Index()\n", shim)


def bkt(text):
    text = replace(text, "        expandEdges(node, 0, localEdgeCount, false);", """
        if constexpr (!EnableCrossEdges) {
            if (ShortcutBench::active && ShortcutBench::active->edges) {
                if (localEdgeCount > 56) throw std::runtime_error("Shortcut graph width exceeds fixture");
                int neighbors[64];
                int n = ShortcutBench::Neighbors(node, localEdgeCount, currentLocal, neighbors);
                expandEdges(neighbors, 0, n, false);
            } else {
                expandEdges(node, 0, localEdgeCount, false);
            }
        } else {
            expandEdges(node, 0, localEdgeCount, false);
        }""")
    text = replace(text, "                SizeType targetKey = edgeValue;", """
                if constexpr (!EnableCrossEdges) {
                    if (ShortcutBench::active && ShortcutBench::active->profile)
                        ++ShortcutBench::active->adjacency;
                }
                SizeType targetKey = edgeValue;""")
    text = replace(text, """                const float distance =
                    m_fComputeDistance(
                        p_query.GetQuantizedTarget(),
                        targetIndex
                            ->m_pSamples[targetLocal],
                        GetFeatureDim());""", """
                if constexpr (!EnableCrossEdges) {
                    if (ShortcutBench::active && ShortcutBench::active->profile) {
                        auto& s = *ShortcutBench::active;
                        s.inShortcut = s.edges &&
                            std::find((*s.edges)[currentLocal].begin(), (*s.edges)[currentLocal].end(), targetLocal)
                            != (*s.edges)[currentLocal].end();
                    }
                }
                const float distance =
                    m_fComputeDistance(
                        p_query.GetQuantizedTarget(),
                        targetIndex
                            ->m_pSamples[targetLocal],
                        GetFeatureDim());
                if constexpr (!EnableCrossEdges) {
                    if (ShortcutBench::active) ShortcutBench::active->inShortcut = false;
                }""")
    return replace(text, """    SearchIndex(*((COMMON::QueryResultSet<T> *)&p_query), *workSpace, p_searchDeleted, true);""", """
    try {
        SearchIndex(*((COMMON::QueryResultSet<T> *)&p_query), *workSpace, p_searchDeleted, true);
    } catch (const ShortcutBench::BudgetExhausted&) {
        if (!ShortcutBench::active) throw;
        ShortcutBench::active->exhausted = true;
        while (!workSpace->m_NGQueue.empty()) {
            auto candidate = workSpace->m_NGQueue.pop();
            ((COMMON::QueryResultSet<T> *)&p_query)->AddPoint(candidate.node, candidate.distance);
        }
        ((COMMON::QueryResultSet<T> *)&p_query)->SortResult();
    }""")


def spann(text):
    text = '#include "FullHooks.h"\n' + text
    return replace(text, """            ret = m_index->SearchIndex(
                *p_queryResults);""", """
            auto* bkt = dynamic_cast<BKT::Index<T>*>(m_index.get());
            if (!bkt || m_index->GetNumSamples() != static_cast<int>(ShortcutFull::edges.size()) ||
                p_queryResults->GetResultNum() != 24 || queryTags || queryDNF)
                throw std::runtime_error("Shortcut full-query path invariant failed");
            ShortcutBench::State state;
            state.remaining = ShortcutFull::cap;
            state.profile = ShortcutFull::profile;
            state.rewire = ShortcutFull::mode == "rewire8";
            if (ShortcutFull::mode == "add8" || state.rewire) state.edges = &ShortcutFull::edges;
            const bool wrapped = ShortcutFull::cap || state.profile;
            std::function<float(const T*, const T*, DimensionType)> original;
            if (wrapped || state.edges) ShortcutBench::active = &state;
            if (wrapped) original = bkt->BenchmarkDistance(ShortcutFull::cap != 0);
            try {
                ret = m_index->SearchIndex(*p_queryResults);
            } catch (...) {
                if (wrapped) bkt->BenchmarkRestore(std::move(original));
                ShortcutBench::active = nullptr;
                throw;
            }
            if (wrapped) bkt->BenchmarkRestore(std::move(original));
            ShortcutBench::active = nullptr;
            ShortcutFull::last.invoked = true;
            ShortcutFull::last.calls = state.used;
            ShortcutFull::last.adjacency = state.adjacency;
            ShortcutFull::last.shortcutCalls = state.shortcutDistances;
            ShortcutFull::last.exhausted = state.exhausted;
            if (ShortcutFull::capture) {
                for (int i = 0; i < p_queryResults->GetResultNum(); ++i) {
                    ShortcutFull::last.heads.push_back(p_queryResults->GetResult(i)->VID);
                    ShortcutFull::last.distances.push_back(p_queryResults->GetResult(i)->Dist);
                }
            }""")


def bench(text):
    text = '#include "FullHooks.h"\n' + text
    text = replace(text, "    for (const auto& parameter : parameters) {\n        p_manager.SetSearchParam(",
                   """    ShortcutFull::Configure(parameters);
    for (const auto& parameter : parameters) {
        if (parameter.first.rfind("shortcut", 0) == 0) continue;
        p_manager.SetSearchParam(""")
    text = replace(text, "            auto search = [&](std::size_t p_queryIndex) {", """
            auto search = [&](std::size_t p_queryIndex) {
                ShortcutFull::last = {};
""")
    text = replace(text, "            for (std::size_t i = 0; i < warmup; ++i) {", """
            std::vector<ShortcutFull::Observation> validation;
            std::vector<std::pair<int, float>> validationResults;
            if (ShortcutFull::capture) {
                for (std::size_t i = 0; i < measuredQueries; ++i) {
                    auto r = search(options.measureOffset + i);
                    if (!r || !ShortcutFull::last.invoked)
                        throw std::runtime_error("Full-query validation search failed");
                    validation.push_back(std::move(ShortcutFull::last));
                    for (int j = 0; j < options.topk; ++j)
                        validationResults.emplace_back(r->GetResult(j)->VID, r->GetResult(j)->Dist);
                }
                ShortcutFull::capture = false;
            }
            for (std::size_t i = 0; i < warmup; ++i) {""")
    text = replace(text, "            std::size_t failed = 0;", """
            std::vector<float> resultDistances(resultIds.size(), MaxDist);
            std::vector<ShortcutFull::Observation> observations(measuredQueries);
            std::size_t failed = 0;""")
    text = replace(text, "                const auto postingStats = VectorIndex::GetThreadLocalPostingScanStats();", """
                if (!ShortcutFull::last.invoked) throw std::runtime_error("Full H1 hook not invoked");
                observations[i] = std::move(ShortcutFull::last);
                const auto postingStats = VectorIndex::GetThreadLocalPostingScanStats();""")
    text = replace(text, """                            static_cast<std::int32_t>(item->VID);""", """
                            static_cast<std::int32_t>(item->VID);
                        resultDistances[i * options.topk + j] = item->Dist;""")
    text = replace(text, "            const std::size_t hits = RecallHits(", """
            std::ofstream dump(ShortcutFull::output, std::ios::binary);
            if (!dump) throw std::runtime_error("Cannot write shortcut query results");
            std::uint64_t calls = 0, adjacency = 0, shortcutCalls = 0, exhausted = 0;
            for (std::size_t i = 0; i < measuredQueries; ++i) {
                auto& o = observations[i];
                if (!validation.empty()) {
                    const auto& v = validation[i];
                    if (v.calls != o.calls || v.adjacency != o.adjacency ||
                        v.shortcutCalls != o.shortcutCalls || v.exhausted != o.exhausted)
                        throw std::runtime_error("Capture changed native navigation work");
                    o.heads = v.heads;
                    o.distances = v.distances;
                    for (int j = 0; j < options.topk; ++j) {
                        auto position = i * options.topk + j;
                        if (validationResults[position].first != resultIds[position] ||
                            validationResults[position].second != resultDistances[position])
                            throw std::runtime_error("Capture changed final native results");
                    }
                }
                calls += o.calls; adjacency += o.adjacency; shortcutCalls += o.shortcutCalls;
                exhausted += o.exhausted;
                dump << "{\\\"query\\\":" << i << ",\\\"calls\\\":" << o.calls
                     << ",\\\"adjacency\\\":" << o.adjacency << ",\\\"shortcut_calls\\\":" << o.shortcutCalls
                     << ",\\\"exhausted\\\":" << o.exhausted << ",\\\"heads\\\":[";
                for (std::size_t j = 0; j < o.heads.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[" << o.heads[j] << "," << std::setprecision(9) << o.distances[j] << "]";
                }
                dump << "],\\\"results\\\":[";
                for (int j = 0; j < options.topk; ++j) {
                    if (j) dump << ",";
                    dump << "[" << resultIds[i * options.topk + j] << ","
                         << std::setprecision(9) << resultDistances[i * options.topk + j] << "]";
                }
                dump << "]}\\n";
            }
            if (!dump) throw std::runtime_error("Shortcut query result write failed");
            std::cout << "SHORTCUT_WORK calls=" << calls << " adjacency=" << adjacency
                      << " shortcut=" << shortcutCalls << " exhausted=" << exhausted << "\\n";
            const std::size_t hits = RecallHits(""")
    return text


patch("AnnService/inc/Core/BKT/Index.h", header)
patch("AnnService/src/Core/BKT/BKTIndex.cpp", bkt)
patch("AnnService/src/Core/SPANN/SPANNIndex.cpp", spann)
patch("Tools/benchmarks/SpannAclBench.cpp", bench)
for source, target in [(here / "FullHooks.h", root / "AnnService/FullHooks.h"),
                       (here.parent / "ShortcutHooks.h", root / "AnnService/ShortcutHooks.h")]:
    if not target.exists() or source.read_bytes() != target.read_bytes():
        shutil.copy2(source, target)
(root.parent / "integration.json").write_text(json.dumps(changed, indent=2) + "\n")
