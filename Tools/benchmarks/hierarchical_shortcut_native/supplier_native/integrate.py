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
    t = sub(t, "struct Observation {", "struct Observation {\n    std::uint64_t " +
            ", ".join(f"{f} = 0" for f in FIELDS) + ";\n"
            "    std::vector<int> ownIDs, parentEvaluated;\n"
            "    std::vector<std::pair<int, float>> evaluated;")
    t = sub(t, 'mode != "hierarchy")', 'mode != "hierarchy" && mode != "supplier" && mode != "control")')
    return sub(t, '    LoadEdges(get("shortcutedges"));', """
    if (mode == "supplier" || mode == "control") {
        if (cap != 2000 && cap != 3200) throw std::runtime_error("Supplier caps must be2000/3200");
    } else LoadEdges(get("shortcutedges"));""")


def bkt(t):
    t = '#include "Supplier.h"\n' + t
    begin = t.index("    m_pTrees.InitSearchTrees(m_pSamples, m_fComputeDistance, p_query, p_space, p_traversalFilter);")
    end = t.index("int Index<T>::SearchIterative", begin)
    prefix, suffix = t[:begin], t[end:]
    t = t[begin:end]
    t = sub(t, "    m_pTrees.InitSearchTrees(m_pSamples, m_fComputeDistance, p_query, p_space, p_traversalFilter);", """
    if (auto* context = H1Supplier::Active()) {
        if (context->index != this || EnableCrossEdges || p_resultFilter || p_traversalFilter)
            throw std::runtime_error("Supplier requires one unfiltered native H1 traversal");
        if (++context->engine->stats.starts != 1) throw std::runtime_error("Native H1 restart");
    }
    m_pTrees.InitSearchTrees(m_pSamples, m_fComputeDistance, p_query, p_space, p_traversalFilter);""")
    t = sub(t, "        NodeDistPair gnode = p_space.m_NGQueue.pop();", """
        NodeDistPair gnode = p_space.m_NGQueue.pop();
        if (auto* c = H1Supplier::Active()) {
            ++c->engine->stats.pops; c->engine->Event(50, gnode.node);
        }""")
    t = sub(t, "        SizeType checkNode = node[checkPos];", """
        SizeType checkNode = node[checkPos];
        if (auto* c = H1Supplier::Active())
            if (checkNode < -1) ++c->engine->stats.sentinels;""")
    t = sub(t, "                bool admitted = false;\n                bool traversalAllowed = true;", """
                if constexpr (!EnableCrossEdges) {
                    if (H1Supplier::Active() && collapsedLocal != p_representative)
                        collapsedDistance = m_fComputeDistance(
                            p_query.GetQuantizedTarget(), p_index->m_pSamples[collapsedLocal], GetFeatureDim());
                }
                bool admitted = false;
                bool traversalAllowed = true;""")
    t = sub(t, "        const auto expandEdges =\n", """
        int supplierFresh = 0;
        if (auto* c = H1Supplier::Active()) ++c->engine->stats.expansions;
        const auto expandEdges =
""")
    t = sub(t, "                const float routeDistance =\n", """
                ++supplierFresh;
                const float routeDistance =
""")
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
            if (supplierFresh == 0 && p_space.m_iNumberOfCheckedLeaves < p_space.m_iMaxCheck) {
                c->engine->Supply(currentLocal, [&](int id) {
                    const int before = supplierFresh;
                    expandEdges(&id, 0, 1, false);
                    return supplierFresh != before;
                });
            }
        }
""" + anchor)
    t = sub(t, """            m_pTrees.SearchTrees(m_pSamples, m_fComputeDistance, p_query, p_space,
                                 (std::min)(""", """
            if (auto* c = H1Supplier::Active()) ++c->engine->stats.trees;
            m_pTrees.SearchTrees(m_pSamples, m_fComputeDistance, p_query, p_space,
                                 (std::min)(""")
    # Capture native checked work even when the actual-call exception interrupted finishSearch.
    t = prefix + t + suffix
    start = t.index("    } catch (const ShortcutBench::BudgetExhausted&)")
    end = t.index("    m_workSpaceFactory->ReturnWorkSpace", start)
    t = t[:end] + """    if (auto* c = H1Supplier::Active())
        c->nativeChecked = workSpace->m_iNumberOfCheckedLeaves;
""" + t[end:]
    return t


def spann(t):
    t = '#include "NativeAdapter.h"\n' + t
    t = sub(t, "    if (useHierarchyNavigation && m_extraSearcher != nullptr)",
            "    if ((useHierarchyNavigation || H1Supplier::Enabled()) && m_extraSearcher != nullptr)")
    t = sub(t, "    if (RefreshHierarchySignatures(levelToLowerIDs, m_hierarchyCatalogVersion == 3)",
            "    m_supplierMaps = levelToLowerIDs;\n"
            "    if (RefreshHierarchySignatures(levelToLowerIDs, m_hierarchyCatalogVersion == 3)")
    anchor = "            auto* bkt = dynamic_cast<BKT::Index<T>*>(m_index.get());"
    t = sub(t, anchor, """
            if (H1Supplier::Enabled()) {
                auto* nativeBKT = dynamic_cast<BKT::Index<T>*>(m_index.get());
                if (!nativeBKT || !headPointResults || !admitHeadPoint)
                    throw std::runtime_error("Supplier requires native own-point workspace");
                H1Supplier::NativeSearch(nativeBKT, p_queryResults, m_secondLevelCatalogs,
                    m_secondLevelPostings, m_supplierMaps, secondLevelHeadAdmission, admitHeadPoint);
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
patch("AnnService/src/Core/SPANN/SPANNIndex.cpp", spann)
patch("Tools/benchmarks/SpannAclBench.cpp", bench)
for path, content in pending.items():
    path.write_bytes(content)
for name in ("Supplier.h", "NativeAdapter.h"):
    shutil.copy2(here / name, root / "AnnService" / name)
marker.write_text(json.dumps(changes, indent=2) + "\n")
