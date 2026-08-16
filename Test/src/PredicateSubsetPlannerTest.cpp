// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/CoreInterface.h"
#include "inc/PredicateSubsetPlanner.h"
#include "inc/Core/SPANN/PrimaryHeadCSR.h"
#include "inc/Test.h"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace {

struct ScopedPlannerTempDir {
    ScopedPlannerTempDir()
    {
        const auto nonce =
            std::chrono::steady_clock::now().time_since_epoch().count();
        path = std::filesystem::temp_directory_path() /
               ("sptag_predicate_subset_planner_" + std::to_string(nonce));
        std::filesystem::create_directories(path);
    }

    ~ScopedPlannerTempDir()
    {
        std::error_code error;
        std::filesystem::remove_all(path, error);
    }

    std::filesystem::path path;
};

SPTAG::PredicateSubsetPlanner::Workload MakeWorkload(
    const std::vector<std::pair<double, std::string>>& rows,
    int categoricalColumnCount)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    Workload workload;
    workload.categoricalColumnCount = categoricalColumnCount;
    std::unordered_map<std::string, int> atomIds;
    double totalWeight = 0.0;
    for (const auto& row : rows) {
        Query query;
        query.weight = row.first;
        std::string error;
        BOOST_REQUIRE_MESSAGE(
            ParseQuery(row.second,
                       categoricalColumnCount,
                       &workload.atoms,
                       &atomIds,
                       &query,
                       &error),
            error);
        workload.queries.push_back(std::move(query));
        totalWeight += row.first;
    }
    for (Query& query : workload.queries) query.weight /= totalWeight;
    return workload;
}

std::vector<std::uint32_t> MakeFourGroupTags()
{
    std::vector<std::uint32_t> tags;
    tags.reserve(64 * 2);
    for (std::uint32_t row = 0; row < 64; ++row) {
        tags.push_back(row / 16);
        tags.push_back(row);
    }
    return tags;
}

} // namespace

BOOST_AUTO_TEST_SUITE(PredicateSubsetPlannerTest)

BOOST_AUTO_TEST_CASE(RejectsTruncatedRuntimeDNF)
{
    constexpr std::uint32_t kDNF3Magic = 0x444E4633U;
    std::vector<std::uint32_t> malformed = {
        kDNF3Magic,
        1,
        2,
        0,
        0,
        SPTAG::Cache::DNF_EQ,
        1
    };
    float queryValue = 0.0f;
    TenantIndexManager manager(1, "SPANN", "Float");
    const SPTAG::ByteArray query(
        reinterpret_cast<std::uint8_t*>(&queryValue),
        sizeof(queryValue),
        false);
    const SPTAG::ByteArray predicate(
        reinterpret_cast<std::uint8_t*>(malformed.data()),
        malformed.size() * sizeof(std::uint32_t),
        false);

    BOOST_CHECK(manager.SearchWithACL(query, 0, 1, predicate, -1) == nullptr);
}

BOOST_AUTO_TEST_CASE(CategoricalRangeSignaturesFailOpen)
{
    using namespace SPTAG::Cache;
    DNFPredicate predicate;
    DNFClause clause;
    clause.lits.push_back({1, 10, DNF_GT, 0});
    predicate.clauses.push_back(std::move(clause));

    PageBitmask page;
    page.Clear();
    HierarchicalPostingMask hierarchy;
    hierarchy.Clear();

    BOOST_CHECK(predicate.MayMatchPage(page));
    BOOST_CHECK(predicate.MayMatchHier(hierarchy));

    const std::uint32_t matchingTags[] = {0, 11};
    const std::uint32_t rejectedTags[] = {0, 10};
    BOOST_CHECK(predicate.Matches(matchingTags, 2));
    BOOST_CHECK(!predicate.Matches(rejectedTags, 2));
}

BOOST_AUTO_TEST_CASE(EmptyPureAndTailSignaturesFailOpen)
{
    using namespace SPTAG::Cache;
    PostingBitmask pure;
    PostingBitmask tail;
    PostingBitmask query;
    query.Insert(42);

    BOOST_CHECK(PostingUnionMayIntersect(&pure, &tail, query));
    pure.Insert(7);
    BOOST_CHECK(!PostingUnionMayIntersect(&pure, &tail, query));
    tail.Insert(42);
    BOOST_CHECK(PostingUnionMayIntersect(&pure, &tail, query));
}

BOOST_AUTO_TEST_CASE(SupportsDynamicCategoricalSignatureColumns)
{
    using namespace SPTAG::Cache;
    HierWidthTable layout;
    layout.Configure({0, 2, 5, 7, 9, 12}, {64, 128, 64, 256, 64, 128});

    HierarchicalPostingMask posting(layout);
    posting.Insert(7, 7007);
    posting.Insert(12, 12012);

    DNFPredicate matching;
    matching.clauses.push_back(
        DNFClause{{DNFLiteral{7, 7007, DNF_EQ, 0}}});
    DNFPredicate rejected;
    rejected.clauses.push_back(
        DNFClause{{DNFLiteral{7, 7008, DNF_EQ, 0}}});

    BOOST_CHECK(matching.MayMatchHier(posting));
    BOOST_CHECK(!rejected.MayMatchHier(posting));

    auto index = SPTAG::VectorIndex::CreateInstance(
        SPTAG::IndexAlgoType::BKT,
        SPTAG::VectorValueType::Float);
    BOOST_REQUIRE(index != nullptr);
    index->InitializeHeadNodeMeta(
        1, 0, true, layout.columns, layout.bits);
    index->SetHeadNodePostingHierMask(0, posting);
    const auto persisted = index->GetHeadNodePostingHierMask(0);
    BOOST_REQUIRE(persisted.mask != nullptr);
    BOOST_CHECK(matching.MayMatchHier(persisted));
    BOOST_CHECK(!rejected.MayMatchHier(persisted));

    HierarchicalOwnTags own(layout);
    own.Insert(7, 7007);
    index->SetHeadNodeHierMask(0, own);
    const auto persistedOwn = index->GetHeadNodeHierMask(0);
    BOOST_REQUIRE(persistedOwn.tag != nullptr);
    BOOST_CHECK(persistedOwn.Matches(matching));
    BOOST_CHECK(!persistedOwn.Matches(rejected));
}

BOOST_AUTO_TEST_CASE(ParsesCanonicalArbitraryDNF)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    ScopedPlannerTempDir temp;
    const std::filesystem::path workloadPath = temp.path / "workload.tsv";
    {
        std::ofstream output(workloadPath);
        output << "# sptag_predicate_train_set_v5\n"
               << "# categorical_column_count=2\n"
               << "0.5\t0.1\t10\t0=1&2>10|1=3\n"
               << "0.25\t0.1\t10\t1=3|2>10&0=1\n"
               << "0.25\t0.1\t10\t0=1|0=1&2>10\n";
    }

    Workload workload;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        LoadWorkload(workloadPath.string(), 2, &workload, &error), error);
    BOOST_REQUIRE_EQUAL(workload.queries.size(), 2);
    BOOST_CHECK_CLOSE(workload.queries[0].weight, 0.75, 1e-9);
    BOOST_CHECK_CLOSE(workload.queries[1].weight, 0.25, 1e-9);
    BOOST_CHECK_EQUAL(workload.queries[1].canonical, "0=1");
    BOOST_REQUIRE_EQUAL(workload.atoms.size(), 3);
    bool foundNumericGreater = false;
    for (const Atom& atom : workload.atoms) {
        foundNumericGreater =
            foundNumericGreater ||
            (atom.column == 2 && atom.kind == 1 &&
             atom.op == Operator::Greater && atom.value == 10);
    }
    BOOST_CHECK(foundNumericGreater);
}

BOOST_AUTO_TEST_CASE(ParsesInterleavedAttributeSchema)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    ScopedPlannerTempDir temp;
    const auto workloadPath = temp.path / "interleaved.tsv";
    {
        std::ofstream output(workloadPath);
        output << "1\t0=7&1<=100&2=9\n";
    }

    Workload workload;
    std::string error;
    const std::vector<std::uint8_t> kinds = {0, 1, 0};
    BOOST_REQUIRE_MESSAGE(
        LoadWorkload(workloadPath.string(), kinds, &workload, &error),
        error);
    BOOST_CHECK_EQUAL(workload.categoricalColumnCount, 2);
    BOOST_CHECK(workload.attributeKinds == kinds);
    BOOST_REQUIRE_EQUAL(workload.atoms.size(), 3);
    BOOST_CHECK_EQUAL(workload.atoms[0].kind, 0);
    BOOST_CHECK_EQUAL(workload.atoms[1].kind, 1);
    BOOST_CHECK_EQUAL(workload.atoms[2].kind, 0);
}

BOOST_AUTO_TEST_CASE(LoadsGenericPrimaryHeadCSR)
{
    using namespace SPTAG::SPANN;
    ScopedPlannerTempDir temp;
    const auto path = temp.path / "primary_head_csr.bin";
    PrimaryHeadCSRHeader header;
    header.headCount = 2;
    header.entryCount = 3;
    header.attributeCount = 3;
    const std::uint32_t offsets[] = {0, 2, 3};
    const std::uint32_t vids[] = {4, 7, 9};
    const std::uint32_t attributes[] = {
        10, 100, 20,
        11, 101, 21,
        12, 102, 22,
    };
    {
        std::ofstream output(path, std::ios::binary);
        output.write(reinterpret_cast<const char*>(&header), sizeof(header));
        output.write(reinterpret_cast<const char*>(offsets), sizeof(offsets));
        output.write(reinterpret_cast<const char*>(vids), sizeof(vids));
        output.write(
            reinterpret_cast<const char*>(attributes), sizeof(attributes));
    }

    PrimaryHeadCSR csr;
    BOOST_REQUIRE(csr.Load(path.string(), 2));
    BOOST_CHECK_EQUAL(csr.AttributeCount(), 3);
    BOOST_CHECK_EQUAL(csr.Begin(0), 0);
    BOOST_CHECK_EQUAL(csr.End(0), 2);
    BOOST_CHECK_EQUAL(csr.Vid(2), 9);
    BOOST_CHECK_EQUAL(csr.Attributes(1)[1], 101);
    BOOST_CHECK(csr.MatchesAnyValue(2, 22));

    const auto legacyPath = temp.path / "primary_head_csr_v1.bin";
    LegacyPrimaryHeadCSRHeader legacyHeader;
    legacyHeader.headCount = 1;
    legacyHeader.entryCount = 1;
    legacyHeader.tagBases[0] = 100;
    legacyHeader.tagBases[1] = 200;
    legacyHeader.tagBases[2] = 300;
    legacyHeader.tagBases[3] = 400;
    const std::uint32_t legacyOffsets[] = {0, 1};
    LegacyPrimaryHeadCSREntry legacyEntry;
    legacyEntry.vid = 5;
    legacyEntry.attributes =
        1ULL | (2ULL << 8) | (3ULL << 16) | (4ULL << 24) |
        (999ULL << 32);
    {
        std::ofstream output(legacyPath, std::ios::binary);
        output.write(
            reinterpret_cast<const char*>(&legacyHeader),
            sizeof(legacyHeader));
        output.write(
            reinterpret_cast<const char*>(legacyOffsets),
            sizeof(legacyOffsets));
        output.write(
            reinterpret_cast<const char*>(&legacyEntry),
            sizeof(legacyEntry));
    }
    PrimaryHeadCSR legacy;
    BOOST_REQUIRE(legacy.Load(legacyPath.string(), 1));
    BOOST_CHECK_EQUAL(legacy.AttributeCount(), 5);
    BOOST_CHECK_EQUAL(legacy.Attributes(0)[0], 101);
    BOOST_CHECK_EQUAL(legacy.Attributes(0)[3], 404);
    BOOST_CHECK_EQUAL(legacy.Attributes(0)[4], 999);
}

BOOST_AUTO_TEST_CASE(ParsesFourClauseStressQuery)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    Workload workload = MakeWorkload(
        {{1.0, "0=0&1<=4|0=1&1<=20|0=2&1>31|0=3&1>47"}}, 1);
    BOOST_REQUIRE_EQUAL(workload.queries.size(), 1);
    BOOST_CHECK_EQUAL(workload.queries[0].clauses.size(), 4);
    BOOST_CHECK_EQUAL(workload.atoms.size(), 8);
}

BOOST_AUTO_TEST_CASE(RemovesDNFClausesSubsumedUnderCanonicalAtomOrdering)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    ScopedPlannerTempDir temp;
    const std::filesystem::path workloadPath = temp.path / "subsumed.tsv";
    {
        std::ofstream output(workloadPath);
        output << "1\t1\t1\t2=1&1=1|2=1\n";
    }
    Workload workload;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        LoadWorkload(workloadPath.string(), 3, &workload, &error), error);
    BOOST_REQUIRE_EQUAL(workload.queries.size(), 1);
    BOOST_CHECK_EQUAL(workload.queries[0].canonical, "2=1");
    BOOST_REQUIRE_EQUAL(workload.queries[0].clauses.size(), 1);
    BOOST_CHECK_EQUAL(workload.queries[0].clauses[0].atoms.size(), 1);
    BOOST_CHECK_EQUAL(workload.atoms.size(), 1);
}

BOOST_AUTO_TEST_CASE(DenseQueryStaysOnGlobalFallback)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    const std::vector<std::uint32_t> tags = MakeFourGroupTags();
    Workload workload =
        MakeWorkload({{1.0, "0=0|0=1|0=2|0=3"}}, 1);

    Plan plan;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        BuildPlan(
            workload,
            64,
            2,
            [&](std::size_t row, int column) {
                return tags[row * 2 + static_cast<std::size_t>(column)];
            },
            &plan,
            &error),
        error);
    BOOST_CHECK_EQUAL(plan.selectedNodes.size(), 1);
    BOOST_REQUIRE_EQUAL(plan.snapshots.size(), 1);
}

BOOST_AUTO_TEST_CASE(SelectsWorkloadAwareCutWithUniqueOwners)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    const std::vector<std::uint32_t> tags = MakeFourGroupTags();
    Workload workload = MakeWorkload(
        {
            {0.24, "0=0"},
            {0.24, "0=1"},
            {0.24, "0=2"},
            {0.24, "0=3"},
            {0.04, "0=0|0=1|0=2|0=3"},
        },
        1);
    Plan plan;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        BuildPlan(
            workload,
            64,
            2,
            [&](std::size_t row, int column) {
                return tags[row * 2 + static_cast<std::size_t>(column)];
            },
            &plan,
            &error),
        error);
    BOOST_CHECK_GT(plan.selectedNodes.size(), 1);
    BOOST_CHECK_LT(plan.selectedNodes.size(), 64);
    BOOST_CHECK_EQUAL(plan.snapshots.front().leafCount, 1);
    for (std::size_t i = 1; i < plan.snapshots.size(); ++i) {
        BOOST_CHECK_EQUAL(plan.snapshots[i - 1].leafCount + 1,
                          plan.snapshots[i].leafCount);
    }

    std::vector<int> ownerCounts(plan.selectedNodes.size(), 0);
    for (std::size_t row = 0; row < 64; ++row) {
        const int owner = AssignRow(
            plan,
            [&](int column) {
                return tags[row * 2 + static_cast<std::size_t>(column)];
            });
        BOOST_REQUIRE_GE(owner, 0);
        BOOST_REQUIRE_LT(owner, static_cast<int>(ownerCounts.size()));
        ++ownerCounts[static_cast<std::size_t>(owner)];
    }
    int assigned = 0;
    for (int count : ownerCounts) {
        BOOST_CHECK_GT(count, 0);
        assigned += count;
    }
    BOOST_CHECK_EQUAL(assigned, 64);
}

BOOST_AUTO_TEST_CASE(NumericPredicatesCanDefineLeaves)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    const std::vector<std::uint32_t> tags = MakeFourGroupTags();
    Workload workload = MakeWorkload(
        {
            {0.25, "1<=15"},
            {0.25, "1>15&1<=31"},
            {0.25, "1>31&1<=47"},
            {0.25, "1>47"},
        },
        1);

    Plan plan;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        BuildPlan(
            workload,
            64,
            2,
            [&](std::size_t row, int column) {
                return tags[row * 2 + static_cast<std::size_t>(column)];
            },
            &plan,
            &error),
        error);
    BOOST_CHECK_GT(plan.selectedNodes.size(), 1);
    bool hasNumericSplit = false;
    for (const DecisionNode& node : plan.nodes) {
        if (node.splitAtom >= 0 &&
            plan.workload.atoms[static_cast<std::size_t>(node.splitAtom)].kind == 1) {
            hasNumericSplit = true;
            break;
        }
    }
    BOOST_CHECK(hasNumericSplit);
}

BOOST_AUTO_TEST_CASE(BuildsDeterministicSampledVectorAffinityGraph)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    const std::vector<float> vectors = {
        0.0f, 0.1f, 10.0f, 10.1f, 20.0f, 20.1f,
    };
    const std::vector<AffinityEdge> edges = BuildAffinityEdges(
        vectors.size(),
        [&](std::size_t left, std::size_t right) {
            const double difference =
                static_cast<double>(vectors[left]) -
                static_cast<double>(vectors[right]);
            return difference * difference;
        });
    BOOST_REQUIRE(!edges.empty());
    for (const AffinityEdge& edge : edges) {
        BOOST_CHECK_LT(edge.first, edge.second);
        BOOST_CHECK_GT(edge.weight, 0.0);
    }
    BOOST_CHECK_EQUAL(
        edges.size(),
        BuildAffinityEdges(
            vectors.size(),
            [&](std::size_t left, std::size_t right) {
                const double difference =
                    static_cast<double>(vectors[left]) -
                    static_cast<double>(vectors[right]);
                return difference * difference;
            })
            .size());
}

BOOST_AUTO_TEST_CASE(ChargesVectorAffinityEdgesCrossingTheSelectedCut)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    const std::vector<std::uint32_t> tags = MakeFourGroupTags();
    Workload workload = MakeWorkload(
        {
            {0.25, "0=0"},
            {0.25, "0=1"},
            {0.25, "0=2"},
            {0.25, "0=3"},
        },
        1);
    std::vector<AffinityEdge> edges;
    for (std::size_t row = 0; row + 16 < 64; ++row) {
        AffinityEdge edge;
        edge.first = row;
        edge.second = row + 16;
        edge.weight = 1.0;
        edges.push_back(edge);
    }

    Plan plan;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        BuildPlan(
            workload,
            64,
            2,
            [&](std::size_t row, int column) {
                return tags[row * 2 + static_cast<std::size_t>(column)];
            },
            edges,
            &plan,
            &error),
        error);
    BOOST_CHECK_GE(plan.selectedBoundaryFraction, 0.0);
    BOOST_CHECK_LE(plan.selectedBoundaryFraction, 1.0);
    if (plan.selectedNodes.size() > 1) {
        BOOST_CHECK_GT(plan.selectedBoundaryFraction, 0.0);
    }
}

BOOST_AUTO_TEST_CASE(PersistsExactDecisionTreeAndSelectedCut)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    ScopedPlannerTempDir temp;
    const std::vector<std::uint32_t> tags = MakeFourGroupTags();
    Workload workload = MakeWorkload(
        {
            {0.25, "0=0"},
            {0.25, "0=1"},
            {0.25, "0=2"},
            {0.25, "0=3"},
        },
        1);
    workload.attributeKinds = {0, 1};
    Plan original;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        BuildPlan(
            workload,
            64,
            2,
            [&](std::size_t row, int column) {
                return tags[row * 2 + static_cast<std::size_t>(column)];
            },
            &original,
            &error),
        error);

    const std::filesystem::path planPath = temp.path / "plan.bin";
    BOOST_REQUIRE_MESSAGE(SavePlan(planPath.string(), original, &error), error);
    Plan loaded;
    BOOST_REQUIRE_MESSAGE(LoadPlan(planPath.string(), &loaded, &error), error);
    BOOST_CHECK(loaded.loadedFromFile);
    BOOST_CHECK_EQUAL(loaded.sourceRows, original.sourceRows);
    BOOST_CHECK_EQUAL(loaded.selectedNodes.size(), original.selectedNodes.size());
    BOOST_CHECK_EQUAL(loaded.workload.atoms.size(), original.workload.atoms.size());
    BOOST_CHECK(loaded.workload.attributeKinds == workload.attributeKinds);
    BOOST_CHECK(loaded.snapshots.empty());
    for (std::size_t row = 0; row < 64; ++row) {
        const auto accessor = [&](int column) {
            return tags[row * 2 + static_cast<std::size_t>(column)];
        };
        BOOST_CHECK_EQUAL(
            AssignRow(original, accessor),
            AssignRow(loaded, accessor));
    }
}

BOOST_AUTO_TEST_CASE(DerivesMultiAttributeConstraintsForEachSubset)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    ScopedPlannerTempDir temp;
    Plan plan;
    Atom attributeA;
    attributeA.column = 0;
    attributeA.value = 7;
    Atom attributeB;
    attributeB.column = 4;
    attributeB.value = 100;
    attributeB.kind = 1;
    attributeB.op = Operator::Greater;
    plan.workload.atoms = {attributeA, attributeB};
    plan.nodes.resize(5);
    plan.nodes[0].splitAtom = 0;
    plan.nodes[0].left = 1;
    plan.nodes[0].right = 2;
    plan.nodes[2].splitAtom = 1;
    plan.nodes[2].left = 3;
    plan.nodes[2].right = 4;
    plan.selectedNodes = {1, 3, 4};
    plan.nodeToLeaf = {-1, 0, -1, 1, 2};

    std::vector<LeafAttributes> attributes;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        BuildLeafAttributes(plan, &attributes, &error), error);
    BOOST_REQUIRE_EQUAL(attributes.size(), 3);
    BOOST_REQUIRE_EQUAL(attributes[0].constraints.size(), 1);
    BOOST_CHECK(!attributes[0].constraints[0].matches);
    BOOST_REQUIRE_EQUAL(attributes[1].constraints.size(), 2);
    BOOST_CHECK(attributes[1].constraints[0].matches);
    BOOST_CHECK(!attributes[1].constraints[1].matches);
    BOOST_REQUIRE_EQUAL(attributes[2].constraints.size(), 2);
    BOOST_CHECK(attributes[2].constraints[0].matches);
    BOOST_CHECK(attributes[2].constraints[1].matches);
    BOOST_CHECK_EQUAL(attributes[2].constraints[1].atom.column, 4);

    const auto path = temp.path / "predicate_subset_attributes.bin";
    BOOST_REQUIRE_MESSAGE(
        SaveLeafAttributes(path.string(), plan, &error), error);
    BOOST_CHECK(std::filesystem::exists(path));
    BOOST_CHECK_GT(std::filesystem::file_size(path), 16);
}

BOOST_AUTO_TEST_CASE(ChoosesGlobalTailBySubsetFanout)
{
    using namespace SPTAG::PredicateSubsetPlanner;

    const auto selectiveMultiSubset =
        EvaluateQueryExecutionCost(0.20, 2);
    BOOST_CHECK(selectiveMultiSubset.useGlobalTail);
    BOOST_CHECK_LT(
        selectiveMultiSubset.globalTailCostMs,
        selectiveMultiSubset.subsetCostMs);

    const auto broadTwoSubset =
        EvaluateQueryExecutionCost(0.80, 2);
    BOOST_CHECK(!broadTwoSubset.useGlobalTail);
    BOOST_CHECK_LT(
        broadTwoSubset.subsetCostMs,
        broadTwoSubset.globalTailCostMs);

    const auto largeFanout =
        EvaluateQueryExecutionCost(0.50, 8);
    BOOST_CHECK(largeFanout.useGlobalTail);
    BOOST_CHECK_LT(largeFanout.globalTailCostMs, largeFanout.subsetCostMs);

    const auto unique =
        EvaluateQueryExecutionCost(0.75, 1);
    BOOST_CHECK(!unique.useGlobalTail);
}

BOOST_AUTO_TEST_CASE(RoutesKnownDNFAtomsAndFallsBackForUnknownRanges)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    const std::unordered_map<std::uint32_t, std::vector<int>> tagToNodes = {
        {10, {0, 1}},
        {20, {1, 2}},
    };
    Atom numeric;
    numeric.column = 1;
    numeric.value = 31;
    numeric.op = Operator::LessEqual;
    numeric.kind = 1;
    const std::unordered_map<std::string, std::vector<int>> predicateToNodes = {
        {AtomKey(numeric), {1, 2}},
    };
    Atom tag10;
    tag10.column = 0;
    tag10.value = 10;
    Atom tag20;
    tag20.column = 0;
    tag20.value = 20;

    std::vector<int> routed;
    BOOST_REQUIRE(CollectRoutingNodes(
        tagToNodes,
        &predicateToNodes,
        {{tag10, numeric}, {tag20}},
        &routed));
    BOOST_REQUIRE_EQUAL(routed.size(), 2);
    BOOST_CHECK_EQUAL(routed[0], 1);
    BOOST_CHECK_EQUAL(routed[1], 2);

    Atom unknownRange = numeric;
    unknownRange.value = 47;
    BOOST_CHECK(!CollectRoutingNodes(
        tagToNodes,
        &predicateToNodes,
        {{unknownRange}},
        &routed));
    BOOST_CHECK(routed.empty());

    Atom sameValueDifferentColumn = tag10;
    sameValueDifferentColumn.column = 3;
    BOOST_CHECK(!CollectRoutingNodes(
        tagToNodes,
        &predicateToNodes,
        {{sameValueDifferentColumn}},
        &routed,
        false));
    BOOST_CHECK(routed.empty());
}

BOOST_AUTO_TEST_CASE(QueryHyperedgesUseExactConjunctionMatches)
{
    using namespace SPTAG::PredicateSubsetPlanner;
    Bitmap matchesA(4);
    matchesA.Set(0);
    matchesA.Set(1);
    Bitmap matchesB(4);
    matchesB.Set(0);
    matchesB.Set(2);
    const std::vector<Bitmap> atomMatches = {matchesA, matchesB};

    Query conjunction;
    Clause clause;
    clause.atoms = {0, 1};
    conjunction.clauses.push_back(clause);
    Bitmap falsePositiveLeaf(4);
    falsePositiveLeaf.Set(1);
    falsePositiveLeaf.Set(2);
    const std::vector<Bitmap> queryMatches =
        BuildQueryMatches({conjunction}, atomMatches, 4);
    BOOST_REQUIRE_EQUAL(queryMatches.size(), 1);
    BOOST_CHECK_EQUAL(
        Bitmap::IntersectionCount(queryMatches[0], falsePositiveLeaf), 0);
}

BOOST_AUTO_TEST_SUITE_END()
