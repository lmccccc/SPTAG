"""Patch a new authenticated runtime with a synchronous native H1 neighbor supplier."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
marker = root.parent / "supplier_integration.json"
if marker.exists():
    raise ValueError("Preserve the existing supplier runtime; use a new reconstruction")
if not (root.parent / "scenario_integration.json").exists():
    subprocess.run([sys.executable, str(here.parent / "full_scenarios/integrate.py"), str(root)], check=True)
changes = {}
pending = {}
FIELDS = ("supplyGraph supplyParent supplyChild supplyCache supplyRepeats supplyStarts supplyPops "
          "supplyExpansions supplySentinels supplyTrees supplyCalls supplyReturns supplyNeighbors "
          "supplyQueued supplyH2Rows supplyH3Rows supplyAscents supplyMembers supplyMaxMembers "
          "supplyMaxNeighbors supplyCandidates supplyEligible supplyTrace").split()
FIELDS += ("supplyRawBefore supplyEffectiveBefore supplyEffectiveAfter supplyTriggers supplyRequested "
           "supplyQualified supplyMaxQualified supplyFills supplyH2Fills supplyH2Exhausted "
           "supplyNativeStops supplyScopeStops supplyRouting supplySignatureChecks supplySignatureRejects "
           "supplySignatureCache supplyPredicateChecks supplyPredicateRejects supplyPredicateCache "
           "supplyPostingSkips supplyH2Completed supplyH3Completed supplyNativeMaxCheck supplyNativeOvershoot "
           "supplyContinuationPops supplyRejectedLeaves supplyUpperMembers supplyLowerMembers "
           "supplyDiscovered supplyDiscoveryReuse supplyEntryCandidates supplyEntryCalls supplyEntryBefore "
           "supplyStartupChecks supplyStartupCalls supplyStartupBlocked supplyStartupBefore "
           "supplyStartupAfter supplyStartupQualified").split()


def sub(t, old, new):
    if t.count(old) != 1:
        raise ValueError((old[:100], t.count(old)))
    return t.replace(old, new)


def patch(name, transform):
    p = root / name
    old = p.read_bytes()
    pending[p] = transform(old.decode()).encode()
    changes[name] = {"before": hashlib.sha256(old).hexdigest(),
                     "after": hashlib.sha256(pending[p]).hexdigest()}


def hooks(t):
    t = sub(t, 'inline bool profile = false, capture = false;',
            'inline bool profile = false, capture = false;\ninline int effectiveDegree = 16;')
    t = sub(t, "struct Observation {", "struct Observation {\n    std::uint64_t " +
            ", ".join(f"{f} = 0" for f in FIELDS) + ";\n"
            "    std::vector<int> ownIDs, parentEvaluated;\n"
            "    std::vector<std::pair<int, float>> evaluated;\n"
            "    std::vector<int> qualifications;\n"
            "    std::vector<std::array<std::uint64_t, 9>> degreeAudits;\n"
            "    std::vector<std::array<std::uint64_t, 4>> rowAudits;\n"
            "    std::vector<std::array<int, 2>> rejectedRows;\n"
            "    std::vector<int> predicateRejected, entryIDs;\n"
            "    std::vector<std::pair<int, float>> entryScored;\n"
            "    std::vector<std::array<int, 2>> discoveries;\n"
            "    std::vector<std::array<std::int64_t, 8>> startupAudits;")
    t = '#include <array>\n' + t
    t = sub(t, 'mode != "hierarchy")', 'mode != "hierarchy" && mode != "supplier" && mode != "control")')
    t = sub(t, 'p.first != "shortcutoutput" && p.first != "shortcutedges")',
            'p.first != "shortcutoutput" && p.first != "shortcutedges" &&\n'
            '            p.first != "shortcuteffectivedegree")')
    t = sub(t, '    cap = Unsigned(get("shortcutcap"));', """
    if (mode == "supplier" || mode == "control") {
        if (parameters.count("shortcutcap") || parameters.count("shortcutmemberlimit"))
            throw std::runtime_error("Removed supplier budgets must not be supplied");
    } else cap = Unsigned(get("shortcutcap"));""")
    return sub(t, '    LoadEdges(get("shortcutedges"));', """
    if (mode == "supplier" || mode == "control") {
        effectiveDegree = Unsigned(get("shortcuteffectivedegree"));
        if (effectiveDegree != 16)
            throw std::runtime_error("Require preregistered effective degree16 trigger");
    } else LoadEdges(get("shortcutedges"));""")


def bkt(t):
    t = '#include "Supplier.h"\n' + t
    begin = t.index("    m_pTrees.InitSearchTrees(m_pSamples, m_fComputeDistance, p_query, p_space, p_traversalFilter);")
    end = t.index("int Index<T>::SearchIterative", begin)
    prefix, suffix = t[:begin], t[end:]
    t = t[begin:end]
    t = sub(t, "    m_pTrees.InitSearchTrees(m_pSamples, m_fComputeDistance, p_query, p_space, p_traversalFilter);", """
    if (auto* context = H1Supplier::Active()) {
        if (context->index != this || EnableCrossEdges || !p_resultFilter || !p_traversalFilter)
            throw std::runtime_error("Supplier requires native predicate-first H1 traversal");
        if (++context->engine->stats.starts != 1) throw std::runtime_error("Native H1 restart");
        context->engine->stats.nativeMaxCheck = p_space.m_iMaxCheck;
    }
    m_pTrees.InitSearchTrees(m_pSamples, m_fComputeDistance, p_query, p_space, p_traversalFilter);""")
    t = sub(t, "        NodeDistPair gnode = p_space.m_NGQueue.pop();", """
        NodeDistPair gnode = p_space.m_NGQueue.pop();
        if (auto* c = H1Supplier::Active()) {
            ++c->engine->stats.pops; c->engine->Event(50, gnode.node);
            if (c->engine->stats.calls) ++c->engine->stats.continuationPops;
        }""")
    t = sub(t, "        SizeType checkNode = node[checkPos];", """
        SizeType checkNode = node[checkPos];
        if (auto* c = H1Supplier::Active())
            if (checkNode < -1) ++c->engine->stats.sentinels;""")
    t = sub(t, "                bool admitted = false;\n                bool traversalAllowed = true;", """
                if constexpr (!EnableCrossEdges) {
                    if (H1Supplier::Active() && collapsedLocal != p_representative &&
                        H1Supplier::Active()->engine->Qualify(collapsedLocal))
                        collapsedDistance = m_fComputeDistance(
                            p_query.GetQuantizedTarget(), p_index->m_pSamples[collapsedLocal], GetFeatureDim());
                }
                bool admitted = false;
                bool traversalAllowed = true;""")
    t = sub(t, "        const auto expandEdges =\n", """
        int supplierFresh = 0, supplierEffective = 0;
        if (auto* c = H1Supplier::Active()) ++c->engine->stats.expansions;
        const auto expandEdges =
""")
    t = sub(t, "                const float routeDistance =\n", """
                if (auto* c = H1Supplier::Active()) {
                    auto& e = *c->engine;
                    const bool eligible = e.qualifications[targetLocal] != 0;
                    supplierEffective += eligible;
                    e.stats.effectiveAfter += eligible;
                    if (!e.childCall) {
                        e.stats.effectiveBefore += eligible;
                    }
                }
                const float routeDistance =
""")
    t = sub(t, "                if (p_traversalFilter && !p_traversalFilter(targetLocal)) continue;", """
                ++supplierFresh;
                if (auto* c = H1Supplier::Active())
                    if (!c->engine->childCall) ++c->engine->stats.rawBefore;
                if (p_traversalFilter && !p_traversalFilter(targetLocal)) continue;""")
    t = sub(t, """                if (p_space.m_iNumberOfCheckedLeaves >=
                    p_space.m_iMaxCheck)""", """                if (p_space.m_iNumberOfCheckedLeaves >=
                    p_space.m_iMaxCheck &&
                    !(H1Supplier::Active() && H1Supplier::Active()->engine->inHelper))""")
    t = sub(t, "                ++p_space.m_iNumberOfCheckedLeaves;", """
                ++p_space.m_iNumberOfCheckedLeaves;
                if (auto* c = H1Supplier::Active())
                    c->engine->stats.nativeOvershoot = (std::max)(
                        c->engine->stats.nativeOvershoot,
                        static_cast<std::uint64_t>((std::max)(0,
                            p_space.m_iNumberOfCheckedLeaves - p_space.m_iMaxCheck)));""")
    t = sub(t, """                    p_space.m_NGQueue.insert(
                        NodeDistPair(
                            targetKey,
                            routeDistance));""", """
                    p_space.m_NGQueue.insert(
                        NodeDistPair(
                            targetKey,
                            routeDistance));
                    if (auto* c = H1Supplier::Active())
                        if (c->engine->childCall) ++c->engine->stats.queued;""")
    anchor = """        expandEdges(
            node, crossEdgeBegin, edgeCount, true);"""
    t = sub(t, anchor, """
        if (auto* c = H1Supplier::Active()) {
            {
                c->engine->Supply(currentLocal, supplierFresh, supplierEffective, [&](int id) {
                    const int before = supplierFresh;
                    const int effectiveBefore = supplierEffective;
                    expandEdges(&id, 0, 1, false);
                    return (supplierFresh != before ? 1 : 0) |
                           (supplierEffective != effectiveBefore ? 2 : 0);
                }, [&]() { return p_space.m_iNumberOfCheckedLeaves < p_space.m_iMaxCheck; });
            }
        }
""" + anchor)
    t = sub(t, """            m_pTrees.SearchTrees(m_pSamples, m_fComputeDistance, p_query, p_space,
                                 (std::min)(""", """
            if (auto* c = H1Supplier::Active()) ++c->engine->stats.trees;
            m_pTrees.SearchTrees(m_pSamples, m_fComputeDistance, p_query, p_space,
                                 (std::min)(""")
    start = t.index("        const auto expandEdges =\n")
    end = t.index("\n        if constexpr (!EnableCrossEdges) {\n"
                  "            if (ShortcutBench::active && ShortcutBench::active->edges)", start)
    shared = t[start:end]
    shared = sub(shared, "[&](const SizeType* p_edges,", """[&](SizeType currentLocal, int currentNode,
                const Index<T>* currentIndex, int& supplierFresh, int& supplierEffective,
                const SizeType* p_edges,""")
    t = t[:start] + t[end:]
    t = t.replace("expandEdges(", "expandEdges(currentLocal, currentNode, currentIndex, "
                  "supplierFresh, supplierEffective, ")
    startup = """
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
                if (anchor < 0 || checked >= p_space.m_iMaxCheck) {
                    ++e.stats.startupBlocked;
                } else {
                    ++e.stats.startupCalls;
                    int fresh = static_cast<int>(e.stats.entryBefore), effective = fresh;
                    e.Supply(anchor, fresh, effective, [&](int id) {
                        const int rawBefore = fresh, qualifiedBefore = effective;
                        expandEdges(anchor, 0, this, fresh, effective, &id, 0, 1, false);
                        return (fresh != rawBefore ? 1 : 0) | (effective != qualifiedBefore ? 2 : 0);
                    }, [&] { return p_space.m_iNumberOfCheckedLeaves < p_space.m_iMaxCheck; });
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
"""
    t = sub(t, "    while (true)\n    {", shared + startup + "\n    while (true)\n    {")
    t = prefix + t + suffix
    start = t.index("ErrorCode Index<T>::SearchIndexWithTraversalFilter(")
    end = t.index("    m_workSpaceFactory->ReturnWorkSpace", start)
    t = t[:end] + """    if (auto* c = H1Supplier::Active())
        c->nativeChecked = workSpace->m_iNumberOfCheckedLeaves;
""" + t[end:]
    return t


def tree(t):
    t = '#include "Supplier.h"\n' + t
    begin = t.index("            void InitSearchTrees(")
    end = t.index("            std::string GetPriorityID(", begin)
    a, b = t[:begin], t[end:]
    t = t[begin:end]
    old = """                const auto distanceToSample = [&](SizeType id) {
                    return fComputeDistance(p_query.GetQuantizedTarget(), data[id], data.C());
                };"""
    new = """                const auto skipLeaf = [&](const BKTNode& node) {
                    auto* c = H1Supplier::Active();
                    if (!c) return false;
                    if (p_space.m_iNumberOfCheckedLeaves >= p_space.m_iMaxCheck) return true;
                    c->engine->ObserveEntry(node.centerid);
                    if (node.childEnd < 0 && !c->engine->Qualify(node.centerid)) {
                        if (!p_space.CheckAndSet(node.centerid)) {
                            ++p_space.m_iNumberOfCheckedLeaves;
                            ++c->engine->stats.rejectedLeaves;
                            c->engine->Event(70, node.centerid);
                        }
                        return true;
                    }
                    return false;
                };
                const auto distanceToNode = [&](const BKTNode& node) {
                    auto* c = H1Supplier::Active();
                    if (c && node.childEnd < 0 && !c->engine->Qualify(node.centerid))
                        throw std::runtime_error("Rejected terminal leaf reached native distance");
                    const bool routing = c && node.childEnd >= 0;
                    if (c) c->engine->routingCall = routing;
                    const float d = fComputeDistance(
                        p_query.GetQuantizedTarget(), data[node.centerid], data.C());
                    if (c) c->engine->routingCall = false;
                    return d;
                };"""
    if t.count(old) != 2:
        raise ValueError("Unexpected native BKT tree callbacks")
    t = t.replace(old, new).replace("distanceToSample(node.centerid)", "distanceToNode(node)")
    t = t.replace("distanceToSample(index)", "distanceToNode(m_pTreeRoots[begin])")
    t = sub(t, """                    if (node.childStart < 0) {
                        p_space.m_SPTQueue.insert""", """                    if (node.childStart < 0) {
                        if (skipLeaf(node)) continue;
                        p_space.m_SPTQueue.insert""")
    t = t.replace("SizeType index = m_pTreeRoots[begin].centerid;",
                  "if (skipLeaf(m_pTreeRoots[begin])) continue;")
    t = sub(t, """                    NodeDistPair bcell = p_space.m_SPTQueue.pop();""", """
                    if (H1Supplier::Active() && p_space.m_iNumberOfCheckedLeaves >= p_limits) break;
                    NodeDistPair bcell = p_space.m_SPTQueue.pop();""")
    t = sub(t, """                    if (tnode.childStart < 0) {
                        if (p_space.m_iNumberOfCheckedLeaves""", """
                    if (tnode.childStart < 0) {
                        if (p_space.m_iNumberOfCheckedLeaves >= p_limits) break;
                        if (auto* c = H1Supplier::Active()) {
                            if (tnode.childEnd >= 0 && !c->engine->Qualify(tnode.centerid)) {
                                if (!p_space.CheckAndSet(tnode.centerid))
                                    ++p_space.m_iNumberOfCheckedLeaves;
                                // A collapsed group center is structural, not its siblings' predicate.
                                for (SizeType i = -tnode.childStart; tnode.childEnd >= 0 &&
                                     i < tnode.childEnd; ++i)
                                    if (!skipLeaf(m_pTreeRoots[i]))
                                        p_space.m_SPTQueue.insert(NodeDistPair(
                                            i, distanceToNode(m_pTreeRoots[i])));
                                continue;
                            }
                        }
                        if (p_space.m_iNumberOfCheckedLeaves""")
    return a + t + b


def spann(t):
    t = '#include "NativeAdapter.h"\n' + t
    t = sub(t, "    if (useHierarchyNavigation && m_extraSearcher != nullptr)",
            "    if ((useHierarchyNavigation || H1Supplier::Enabled()) && m_extraSearcher != nullptr)")
    t = sub(t, "    if (RefreshHierarchySignatures(levelToLowerIDs, m_hierarchyCatalogVersion == 3)",
            "    m_supplierMaps = levelToLowerIDs;\n"
            "    if (RefreshHierarchySignatures(levelToLowerIDs, m_hierarchyCatalogVersion == 3)")
    t = sub(t, "    if (useLimitedTagPure && useHierarchyNavigation)",
            "    if (useLimitedTagPure && (useHierarchyNavigation || H1Supplier::Enabled()))")
    anchor = "            auto* bkt = dynamic_cast<BKT::Index<T>*>(m_index.get());"
    t = sub(t, anchor, """
            if (H1Supplier::Enabled()) {
                auto* nativeBKT = dynamic_cast<BKT::Index<T>*>(m_index.get());
                if (!nativeBKT || !headPointResults || !admitHeadPoint)
                    throw std::runtime_error("Supplier requires native own-point workspace");
                if (m_options.m_enableHybridDistance || m_options.m_storage != Storage::STATIC)
                    throw std::runtime_error("Degree16 requires the authenticated static non-hybrid path");
                const auto& candidatePostingFilter =
                    (!useLimitedTagPure && limitedTagRegionsReady && hasExactFilter && tailPostingFilter)
                        ? tailPostingFilter : postingFilter;
                const auto qualification = [&](int head, bool postingAdmission) {
                    if (head < 0 || head >= m_vectorTranslateMap.R())
                        throw std::runtime_error("Invalid degree16 canonical head");
                    const auto mapped = *m_vectorTranslateMap[head];
                    if (mapped >= static_cast<std::uint64_t>(m_versionMap.Count()))
                        throw std::runtime_error("Invalid degree16 canonical VID");
                    const bool ownEligible = !m_versionMap.Deleted(static_cast<SizeType>(mapped)) &&
                        (!hasExactFilter || limitedHeadMatchesExactPredicate(head));
                    const bool postingEligible = postingAdmission &&
                        m_extraSearcher->CheckValidPosting(head, nullptr) &&
                        (useLimitedTagPure || !candidatePostingFilter || candidatePostingFilter(head));
                    return (postingEligible ? 1 : 0) | (ownEligible ? 2 : 0);
                };
                H1Supplier::NativeSearch(nativeBKT, p_queryResults, m_secondLevelCatalogs,
                    m_secondLevelPostings, m_supplierMaps, secondLevelHeadAdmission, admitHeadPoint,
                    qualification, [&](int level, int id) {
                        return H1Supplier::PostingMayMatch(
                            hierarchyQuerySignature, m_secondLevelPostings.at(level - 1), id);
                    });
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
    return sub(t, """    if (ShortcutFull::capture) {
        for (int i = 0; i < p_queryResults->GetResultNum(); ++i) {""", """
    if (ShortcutFull::capture) {
        if (headPointResults)
            for (auto& p : *headPointResults)
                if (p.second >= 0) ShortcutFull::Last().ownIDs.push_back(p.second);
        for (int i = 0; i < p_queryResults->GetResultNum(); ++i) {""")


def bench(t):
    t = sub(t, "                    o.heads = v.heads;", """
                    if (ShortcutFull::profile && v.supplyTrace != o.supplyTrace)
                        throw std::runtime_error("Capture changed supplier spatial trace");
                    o.supplyTrace = v.supplyTrace;
                    o.ownIDs = v.ownIDs; o.evaluated = v.evaluated; o.parentEvaluated = v.parentEvaluated;
                    o.qualifications = v.qualifications; o.degreeAudits = v.degreeAudits;
                    o.rowAudits = v.rowAudits; o.rejectedRows = v.rejectedRows;
                    o.predicateRejected = v.predicateRejected;
                    o.entryIDs = v.entryIDs; o.entryScored = v.entryScored;
                    o.startupAudits = v.startupAudits; o.discoveries = v.discoveries;
                    o.heads = v.heads;""")
    old = '<< ",\\"graph_checked\\":" << o.graphChecked << ",\\"heads\\":[";'
    new = '<< ",\\"graph_checked\\":" << o.graphChecked\n'
    for field in FIELDS:
        new += f'                     << ",\\"{field}\\":" << o.{field}\n'
    new += '                     << ",\\"own_ids\\":[";\n'
    new += """                for (size_t j = 0; j < o.ownIDs.size(); ++j) {
                    if (j) dump << ",";
                    dump << o.ownIDs[j];
                }
                dump << "],\\"parent_h1\\":[";
                for (size_t j = 0; j < o.parentEvaluated.size(); ++j) {
                    if (j) dump << ",";
                    dump << o.parentEvaluated[j];
                }
                dump << "],\\"evaluated_h1\\":[";
                for (size_t j = 0; j < o.evaluated.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[" << o.evaluated[j].first << "," << std::setprecision(9)
                         << o.evaluated[j].second << "]";
                }
                dump << "],\\"qualifications\\":[";
                for (size_t j = 0; j < o.qualifications.size(); ++j) {
                    if (j) dump << ",";
                    dump << o.qualifications[j];
                }
                dump << "],\\"degree_audits\\":[";
                for (size_t j = 0; j < o.degreeAudits.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[";
                    for (size_t k = 0; k < o.degreeAudits[j].size(); ++k) {
                        if (k) dump << ",";
                        dump << o.degreeAudits[j][k];
                    }
                    dump << "]";
                }
                dump << "],\\"row_audits\\":[";
                for (size_t j = 0; j < o.rowAudits.size(); ++j) {
                    if (j) dump << ",";
                    const auto& r = o.rowAudits[j];
                    dump << "[" << r[0] << "," << r[1] << "," << r[2] << "," << r[3] << "]";
                }
                dump << "],\\"signature_rejected\\":[";
                for (size_t j = 0; j < o.rejectedRows.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[" << o.rejectedRows[j][0] << "," << o.rejectedRows[j][1] << "]";
                }
                dump << "],\\"predicate_rejected\\":[";
                for (size_t j = 0; j < o.predicateRejected.size(); ++j) {
                    if (j) dump << ",";
                    dump << o.predicateRejected[j];
                }
                dump << "],\\"entry_ids\\":[";
                for (size_t j = 0; j < o.entryIDs.size(); ++j) {
                    if (j) dump << ",";
                    dump << o.entryIDs[j];
                }
                dump << "],\\"entry_scored\\":[";
                for (size_t j = 0; j < o.entryScored.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[" << o.entryScored[j].first << "," << std::setprecision(9)
                         << o.entryScored[j].second << "]";
                }
                dump << "],\\"discoveries\\":[";
                for (size_t j = 0; j < o.discoveries.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[" << o.discoveries[j][0] << "," << o.discoveries[j][1] << "]";
                }
                dump << "],\\"startup_audits\\":[";
                for (size_t j = 0; j < o.startupAudits.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[";
                    for (size_t k = 0; k < o.startupAudits[j].size(); ++k) {
                        if (k) dump << ",";
                        dump << o.startupAudits[j][k];
                    }
                    dump << "]";
                }
                dump << "],\\"heads\\":[";"""
    return sub(t, old, new)


patch("AnnService/FullHooks.h", hooks)
patch("AnnService/inc/Core/BKT/Index.h", lambda t: sub(
    t, "            auto BenchmarkDistance(bool capped) {",
    "            auto BenchmarkGetDistance() const { return m_fComputeDistance; }\n"
    "            auto BenchmarkDistance(bool capped) {"))
patch("AnnService/inc/Core/SPANN/Index.h", lambda t: sub(
    t, "            std::vector<SecondLevelHeadPostings> m_secondLevelPostings;",
    "            std::vector<SecondLevelHeadPostings> m_secondLevelPostings;\n"
    "            std::vector<std::vector<std::uint64_t>> m_supplierMaps;"))
patch("AnnService/src/Core/BKT/BKTIndex.cpp", bkt)
patch("AnnService/inc/Core/Common/BKTree.h", tree)
patch("AnnService/src/Core/SPANN/SPANNIndex.cpp", spann)
patch("Tools/benchmarks/SpannAclBench.cpp", bench)
for path, content in pending.items():
    path.write_bytes(content)
for name in ("Supplier.h", "NativeAdapter.h", "Signature.h"):
    shutil.copy2(here / name, root / "AnnService" / name)
marker.write_text(json.dumps(changes, indent=2) + "\n")
