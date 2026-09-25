"""Materialize native reuse from authenticated native code, not the old search adapter."""
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
ORIGINAL = DATA / "toolchains/shortcut_full_20260915"
PARENT = DATA / "toolchains/h1_ratio_phase1_20260917/source"


def once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f"Expected one native integration site: {old[:110]}")
    return text.replace(old, new)


def main():
    if (DATA / "comparisons/h1_native_reuse_20260917_v2").exists():
        raise RuntimeError("Preserve the measured native-reuse runtime")
    t = (ORIGINAL / "source/AnnService/inc/Core/Common/WorkSpace.h").read_text()
    t = '#include "NativeNeighborHooks.h"\n' + t
    t = once(t, "            OptHashPosVector nodeCheckStatus;",
             "            NativeNeighborHooks* m_nativeHooks = nullptr;\n            OptHashPosVector nodeCheckStatus;")
    t = once(t, "                nodeCheckStatus.clear();",
             "                m_nativeHooks = nullptr;\n                nodeCheckStatus.clear();")
    (HERE / "WorkSpace.h").write_text(t)
    t = (ORIGINAL / "frozen/AnnService/inc/Core/BKT/Index.h").read_text()
    anchor = "            ErrorCode SearchIndexWithTraversalFilter("
    t = once(t, anchor, """            ErrorCode SearchIndexWithNativeHooks(
                QueryResult& query, COMMON::NativeNeighborHooks& hooks,
                const std::function<bool(SizeType)>& resultFilter,
                const std::function<bool(SizeType)>& traversalFilter,
                int maxCheck = 0, bool searchDeleted = false) const;
""" + anchor)
    (HERE / "BKTIndex.h").write_text(t)
    t = (ORIGINAL / "source/AnnService/inc/Core/SPANN/Index.h").read_text()
    t = '#include "PostingSupplier.h"\n' + t
    t = once(t, "            COMMON::VersionLabel m_versionMap;",
             "            std::unique_ptr<NativeReuse::PostingModel> m_nativePostingModel;\n"
             "            COMMON::VersionLabel m_versionMap;")
    (HERE / "SPANNIndex.h").write_text(t)
    t = (PARENT / "AnnService/inc/Core/Common/BKTree.h").read_text()
    t = t.replace('#include "Supplier.h"\n', "")
    start = t.index("                const auto skipLeaf =")
    end = t.index("                for (char i =", start)
    original = t[start:end]
    replacement = """                const auto skipLeaf = [&](const BKTNode& node) {
                    if (!p_space.m_nativeHooks || !p_graphFilter) return false;
                    if (p_space.m_iNumberOfCheckedLeaves >= p_space.m_iMaxCheck) return true;
                    if (node.childEnd < 0 && !p_graphFilter(node.centerid)) {
                        if (!p_space.CheckAndSet(node.centerid)) ++p_space.m_iNumberOfCheckedLeaves;
                        return true;
                    }
                    return false;
                };
                const auto distanceToNode = [&](const BKTNode& node) {
                    const float d = fComputeDistance(
                        p_query.GetQuantizedTarget(), data[node.centerid], data.C());
                    auto* hook = p_space.m_nativeHooks;
                    if (hook && (hook->diagnostics || (hook->entry && hook->expand)))
                        hook->Distance(node.centerid, d, node.childEnd >= 0);
                    return d;
                };
"""
    assert t.count(original) == 2
    t = t.replace(original, replacement)
    t = t.replace("H1Supplier::Active() && p_space.m_iNumberOfCheckedLeaves >= p_limits",
                  "p_space.m_nativeHooks && p_graphFilter && p_space.m_iNumberOfCheckedLeaves >= p_limits")
    t = once(t, "if (auto* c = H1Supplier::Active()) {",
             "if (p_space.m_nativeHooks && p_graphFilter) {")
    t = once(t, "!c->engine->Qualify(tnode.centerid)", "!p_graphFilter(tnode.centerid)")
    for expression in ("tnode.centerid, tmp.distance", "tnode.centerid, bcell.distance"):
        anchor = f"p_space.m_NGQueue.insert(NodeDistPair({expression}));"
        assert t.count(anchor) == (1 if "tmp" in expression else 2)
        t = t.replace(anchor, f"""{{ if (p_space.m_nativeHooks && p_space.m_nativeHooks->ownPoint)
                                    p_space.m_nativeHooks->ownPoint({expression});
                                """ + anchor + " }")
    assert "H1Supplier" not in t
    (HERE / "BKTree.h").write_text(t)
    t = (ORIGINAL / "frozen/AnnService/src/Core/BKT/BKTIndex.cpp").read_text()
    t = once(t, """traversalAllowed = !p_space.CheckResultAndSet(collapsedLocal) &&
                            p_traversalFilter(collapsedLocal);""",
             "traversalAllowed = p_traversalFilter(collapsedLocal);")
    anchor = "            return p_resultFilter(p_result) &&"
    t = once(t, anchor, """            if (p_space.m_nativeHooks && p_space.m_nativeHooks->ownPoint &&
                notDeleted(p_index->m_deletedID, p_local) &&
                checkFilter(p_index->m_pMetadata, p_local, filterFunc))
                p_space.m_nativeHooks->ownPoint(p_result, p_distance);
""" + anchor)
    begin = t.index("        const auto expandEdges =")
    end = t.index("        expandEdges(node, 0, localEdgeCount, false);", begin)
    edges = t[begin:end]
    edges = once(edges, "[&](const SizeType* p_edges,", """[&](SizeType currentLocal, int currentNode, const Index<T>* currentIndex,
                const SizeType* p_edges,""")
    edges = once(edges, "                    p_space.m_iMaxCheck)\n",
                 "                    p_space.m_iMaxCheck && !finishInjectedRow)\n")
    anchor = "                const float routeDistance ="
    edges = once(edges, anchor, """                if (p_space.m_nativeHooks && p_space.m_nativeHooks->diagnostics)
                    p_space.m_nativeHooks->Distance(targetLocal, distance, false);
""" + anchor)
    anchor = "                if (p_space.m_Results.insert(\n                        routeDistance))"
    edges = once(edges, anchor, """                auto* observation = p_space.m_nativeHooks;
                if (observation && observation->diagnostics) ++observation->queueOffers;
""" + anchor)
    anchor = """                {
                    p_space.m_NGQueue.insert(
                        NodeDistPair(
                            targetKey,
                            routeDistance));
                }"""
    edges = once(edges, anchor, """                {
                    if (observation && observation->diagnostics) ++observation->queueAccepted;
                    p_space.m_NGQueue.insert(
                        NodeDistPair(
                            targetKey,
                            routeDistance));
                } else if (observation && observation->diagnostics) ++observation->queueRejected;""")
    t = t[:begin] + t[end:]
    anchor = "    while (true)\n    {\n        if (p_space.m_NGQueue.empty())"
    hook = """    bool finishInjectedRow = false;
""" + edges + """
    const auto inject = [&](SizeType head, int nodeID, const Index<T>* index,
                            const SizeType* row, int width, bool startup) {
        auto* hook = p_space.m_nativeHooks;
        if (!hook || !hook->expand) return;
        COMMON::NativeEdgeConsumer native{
            [&](int id, bool fullRow) {
                finishInjectedRow = fullRow;
                hook->injected = fullRow;
                expandEdges(head, nodeID, index, &id, 0, 1, false);
                hook->injected = false;
                finishInjectedRow = false;
            },
            [&] { return p_space.m_iNumberOfCheckedLeaves < p_space.m_iMaxCheck; },
            [&] { return p_space.m_iNumberOfCheckedLeaves; }};
        hook->expand(head, row, width, startup, native);
    };
    if (p_space.m_nativeHooks) {
        auto& hook = *p_space.m_nativeHooks;
        hook.entry = false;
        if (hook.expand && hook.anchor >= 0 &&
            p_space.m_NGQueue.size() < std::ceil(hook.retainedRatio * m_pGraph.m_iNeighborhoodSize))
            inject(hook.anchor, 0, this, m_pGraph[hook.anchor], m_pGraph.m_iNeighborhoodSize, true);
    }

""" + anchor
    t = once(t, anchor, hook)
    t = once(t, "        expandEdges(node, 0, localEdgeCount, false);", """        expandEdges(currentLocal, currentNode, currentIndex, node, 0, localEdgeCount, false);
        if constexpr (!EnableCrossEdges)
            inject(currentLocal, currentNode, currentIndex, node, localEdgeCount, false);""")
    t = once(t, "        expandEdges(\n            node, crossEdgeBegin, edgeCount, true);",
             "        expandEdges(currentLocal, currentNode, currentIndex,\n            node, crossEdgeBegin, edgeCount, true);")
    t = once(t, "                expandEdges(\n                    currentIndex->m_pGraph[sibling],",
             "                expandEdges(currentLocal, currentNode, currentIndex,\n                    currentIndex->m_pGraph[sibling],")
    anchor = "template <typename T>\nErrorCode Index<T>::SearchIndexWithTraversalFilter("
    api = """template <typename T>
ErrorCode Index<T>::SearchIndexWithNativeHooks(
    QueryResult& query, COMMON::NativeNeighborHooks& hooks,
    const std::function<bool(SizeType)>& resultFilter,
    const std::function<bool(SizeType)>& traversalFilter,
    int maxCheck, bool searchDeleted) const
{
    if (!m_bReady) return ErrorCode::EmptyIndex;
    auto workspace = RentWorkSpace(query.GetResultNum(), nullptr, maxCheck > 0 ? maxCheck : m_iMaxCheck);
    if (resultFilter) workspace->PrepareResultCheckStatus();
    using NativeDistance = float (*)(const T*, const T*, DimensionType);
    hooks.originalDistanceFunction = m_fComputeDistance.template target<NativeDistance>() != nullptr;
    workspace->m_nativeHooks = &hooks;
    SearchIndex(*static_cast<COMMON::QueryResultSet<T>*>(&query), *workspace,
                searchDeleted, true, nullptr, resultFilter, traversalFilter);
    hooks.checked = workspace->m_iNumberOfCheckedLeaves;
    workspace->m_nativeHooks = nullptr;
    m_workSpaceFactory->ReturnWorkSpace(std::move(workspace));
    if (query.WithMeta() && m_pMetadata != nullptr) {
        for (int rank = 0; rank < query.GetResultNum(); ++rank) {
            const auto id = query.GetResult(rank)->VID;
            query.SetMetadata(rank, id < 0 ? ByteArray::c_empty : m_pMetadata->GetMetadataCopy(id));
        }
    }
    return ErrorCode::Success;
}

""" + anchor
    t = once(t, anchor, api)
    assert "H1Supplier" not in t and "BenchmarkRestore" not in t
    (HERE / "BKTIndex.cpp").write_text(t)
    t = (ORIGINAL / "frozen/AnnService/src/Core/SPANN/SPANNIndex.cpp").read_text()
    t = '#include "NativeSupplier.h"\n' + t
    anchor = '    SPTAGLIB_LOG(\n        Helper::LogLevel::LL_Info,\n        "Loaded %d hierarchy layers'
    t = once(t, anchor, """    m_nativePostingModel.reset();
    if (m_secondLevelPostings.size() == 2) {
        auto model = std::make_unique<NativeReuse::PostingModel>();
        model->counts = {static_cast<int>(m_secondLevelPostings[0].FirstLevelHeadCount()),
            static_cast<int>(m_secondLevelPostings[0].SecondLevelHeadCount()),
            static_cast<int>(m_secondLevelPostings[1].SecondLevelHeadCount())};
        model->children = [this](int level, int id) -> NativeReuse::PostingModel::Row {
            return {m_secondLevelPostings[level - 1].Begin(id), m_secondLevelPostings[level - 1].End(id)};
        };
        model->BuildOwners();
        m_nativePostingModel = std::move(model);
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "NATIVE_SUPPLIER_OWNER_LOAD count=1\\n");
    }
""" + anchor)
    t = once(t, "if (useLimitedTagPure && useHierarchyNavigation)",
             "if (useLimitedTagPure && (useHierarchyNavigation || NativeReuse::Enabled()))")
    t = once(t, "if (useHierarchyNavigation && m_extraSearcher != nullptr)",
             "if ((useHierarchyNavigation || NativeReuse::Enabled()) && m_extraSearcher != nullptr)")
    anchor = "            ret = m_index->SearchIndex(\n                *p_queryResults);"
    t = once(t, anchor, """            auto* nativeBKT = dynamic_cast<BKT::Index<T>*>(m_index.get());
            if (!nativeBKT || m_options.m_enableHybridDistance || m_options.m_storage != Storage::STATIC)
                throw std::runtime_error("Native reuse requires the original static H1 BKT path");
            if (NativeReuse::Enabled() && (!admitHeadPoint || !headPointResults))
                throw std::runtime_error("Native own-point workspace is missing");
            const auto& candidatePostingFilter =
                (!useLimitedTagPure && limitedTagRegionsReady && hasExactFilter && tailPostingFilter)
                    ? tailPostingFilter : postingFilter;
            const auto posting = [&](int head) {
                return secondLevelHeadAdmission(head) &&
                    m_extraSearcher->CheckValidPosting(head, nullptr) &&
                    (useLimitedTagPure || !candidatePostingFilter || candidatePostingFilter(head));
            };
            const auto traversal = [&](int head) {
                if (head < 0 || head >= m_vectorTranslateMap.R())
                    throw std::runtime_error("Invalid native canonical H1 head");
                if (posting(head)) return true;
                const auto mapped = *m_vectorTranslateMap[head];
                if (mapped >= static_cast<std::uint64_t>(m_versionMap.Count()))
                    throw std::runtime_error("Invalid native canonical H1 point");
                return !m_versionMap.Deleted(static_cast<SizeType>(mapped)) &&
                    (!hasExactFilter || limitedHeadMatchesExactPredicate(head));
            };
            if (NativeReuse::Enabled() && !m_nativePostingModel)
                throw std::runtime_error("Native supplier metadata not loaded");
            ret = NativeReuse::Search(nativeBKT, p_queryResults, m_nativePostingModel.get(), m_secondLevelCatalogs,
                m_secondLevelPostings, posting, admitHeadPoint, traversal, [&](int level, int id) {
                    return NativeReuse::PostingMayMatch(hierarchyQuerySignature,
                                                       m_secondLevelPostings.at(level - 1), id);
                });""")
    anchor = "    auto _phT1 ="
    t = once(t, anchor, """    if (invalidHeadPoint) throw std::runtime_error("Native own-point admission failed");
    if (ShortcutFull::capture) {
        if (headPointResults)
            for (const auto& p : *headPointResults)
                if (p.second >= 0) ShortcutFull::Last().ownIDs.push_back(p.second);
        for (int i = 0; i < p_queryResults->GetResultNum(); ++i) {
            ShortcutFull::Last().heads.push_back(p_queryResults->GetResult(i)->VID);
            ShortcutFull::Last().distances.push_back(p_queryResults->GetResult(i)->Dist);
        }
    }
""" + anchor)
    assert "BenchmarkRestore" not in t and "NativeAdapter" not in t
    (HERE / "SPANNIndex.cpp").write_text(t)
    signature = (HERE.parent / "ratio_degree_phase1/Signature.h").read_text().replace(
        "namespace H1Supplier", "namespace NativeReuse")
    (HERE / "Signature.h").write_text(signature)
    t = (PARENT / "Tools/benchmarks/SpannAclBench.cpp").read_text()
    begin = t.index("                    if (v.calls != o.calls")
    end = t.index("                    for (int j = 0; j < options.topk; ++j)", begin)
    t = t[:begin] + """                    const auto& a = v.native;
                    const auto& b = o.native;
                    if (a.calls != b.calls || a.parentDistances != b.parentDistances ||
                        a.members != b.members || a.checked != b.checked ||
                        a.h2Rows != b.h2Rows || a.h3Rows != b.h3Rows ||
                        !a.originalDistanceFunction || !b.originalDistanceFunction)
                        throw std::runtime_error("Capture changed native injection work or distance primitive");
                    if (ShortcutFull::profile && (a.headDistances != b.headDistances ||
                        a.routingDistances != b.routingDistances || a.childDistances != b.childDistances))
                        throw std::runtime_error("Profile changed native distance work");
                    o = v;
""" + t[end:]
    begin = t.index("                calls += o.calls;")
    end = t.index('                dump << "],\\"heads\\":[";', begin)
    t = t[:begin] + (HERE / "Capture.inc").read_text() + "\n" + t[end:]
    (HERE / "SpannAclBench.cpp").write_text(t)
    print("Materialized native traversal/primitive/heap reuse plus local adjacency injection and admission hooks.")


if __name__ == "__main__":
    main()
