// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#include "inc/Core/SPANN/SparseLabelHierarchyBuilder.h"
#include "inc/Core/SPANN/Index.h"
#include "inc/Core/BKT/Index.h"
#include <filesystem>
#include <iostream>
#include <set>
#include <unistd.h>

using namespace SPTAG;
using Hierarchy = SPANN::SparseLabelHierarchy;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Sparse hierarchy test line " + std::to_string(__LINE__)); } while (false)
template<class Action> void Reject(Action action)
{
    bool rejected = false;
    try { action(); } catch (const std::exception&) { rejected = true; }
    CHECK(rejected);
}
static SPANN::LimitedTagSupport Support(int count)
{
    SPANN::LimitedTagSupport support;
    CHECK(support.Initialize(count, 2, 1, 0, 2, 123));
    CHECK(support.SetTagVectorCounts(1000000, {{0,988890},{1,10000},{2,1000},{3,100},{4,10}}));
    for (int head = 0; head < count; ++head) {
        std::uint32_t own;
        std::vector<std::uint32_t> tags;
        if (count == 8) {
            const std::uint32_t values[] = {0,1,1,2,3,4,0,1};
            own = values[head];
            tags = {own};
            if (head == 6) tags.push_back(1);
            if (head == 7) tags.push_back(2);
        } else {
            own = head < 128 ? 1 : head < 192 ? 2 : head < 200 ? 3 : head == 200 ? 4 : 0;
            tags = {own};
            if (head == 127) tags.push_back(2);
        }
        const std::uint32_t attributes[] = {own, static_cast<std::uint32_t>(head)};
        CHECK(support.SetHeadAttributes(head, attributes, 2) && support.SetHeadTags(head, tags));
    }
    std::string error;
    CHECK(support.Finalize(&error));
    return support;
}
static Hierarchy Fixture(const SPANN::LimitedTagSupport& support, bool omit = false, bool wrongTag = false,
                         int version = 2)
{
    Hierarchy::Header header;
    header.version = version; header.assignment = version - 1;
    header.heads = 8; header.dimension = 1; header.valueType = static_cast<std::uint32_t>(VectorValueType::Float);
    header.headIDs = 42; header.support = support.ContentFingerprint(); header.replicas = 2;
    header.thresholds = Hierarchy::ParseThresholds("0.01,0.001,0.0001,0.00001");
    header.caps = {100,100,100,100};
    Hierarchy hierarchy;
    hierarchy.Initialize(header);
    hierarchy.AddRow(1, 1, 2, omit ? std::vector<std::uint32_t>{1,2,7} : std::vector<std::uint32_t>{1,2,6,7});
    hierarchy.AddRow(3, 2, 3, wrongTag ? std::vector<std::uint32_t>{0,3,7} : std::vector<std::uint32_t>{3,7});
    hierarchy.AddRow(4, 3, 4, {4});
    hierarchy.AddRow(5, 4, 5, {5});
    hierarchy.AddRow(1, Hierarchy::None, 3, {0});
    hierarchy.AddRow(1, Hierarchy::None, 4, {4,1});
    hierarchy.AddRow(1, Hierarchy::None, 5, {5,2});
    hierarchy.AddRow(1, Hierarchy::None, 5, {6,3});
    for (int head = 0; head < 8; ++head) hierarchy.SetEntry(head, 4);
    hierarchy.BuildOwners();
    return hierarchy;
}
static void CountTests()
{
    const std::vector<std::pair<int, SizeType>> populations{{2,1000},{3,1000},{4,1000},{5,1000}};
    CHECK(SPANN::SparseTerminalCounts(populations, .12, {1000,1000,1000,1000}, {0,20,10,5}) ==
        (std::vector<SizeType>{120,120,120,120}));
    CHECK(SPANN::SparseTerminalCounts({{3,1000},{3,500},{3,1}}, .12, {0,30,0,0}, {0,10,0,0}) ==
        (std::vector<SizeType>{12,7,1}));
    Reject([&] { SPANN::SparseTerminalCounts({{3,1},{3,1}}, .12, {0,2,0,0}, {0,1,0,0}); });
    Reject([&] { SPANN::SparseTerminalCounts({}, .12, {0,0,0,0}, {0,1,0,0}); });
}
static void CheckOwners(const Hierarchy& hierarchy)
{
    for (int head = 0; head < hierarchy.HeadCount(); ++head) {
        std::set<int> expected, actual;
        for (int id = 0; id < hierarchy.Count(); ++id)
            if (hierarchy.IsLeaf(id) &&
                std::binary_search(hierarchy.Begin(id), hierarchy.End(id), static_cast<unsigned>(head)))
                expected.insert(id);
        int spatial = 0;
        for (int id : hierarchy.Parents(0, head)) {
            if (!hierarchy.IsLeaf(id)) ++spatial;
            else CHECK(actual.insert(id).second);
        }
        CHECK(expected == actual && spatial == (hierarchy.Count() ? 1 : 0));
    }
}
static void ThresholdTests()
{
    auto support = Support(8);
    const auto thresholds = Hierarchy::ParseThresholds("0.01,0.001,0.0001,0.00001");
    for (int tag = 1; tag <= 4; ++tag) CHECK(Hierarchy::Tier(support, tag, thresholds) == tag + 1);
    CHECK(Hierarchy::Tier(support, 0, thresholds) == 0 && Hierarchy::Tier(support, 999, thresholds) == 0);
    for (const char* invalid : {"", "0.01", "0.01,0.001,0.0001,0", "0.01,0.001,0.001,0.00001",
        "1.1,0.1,0.01,0.001", "nan,0.1,0.01,0.001", "0.1,0.01,0.001,0.0001,",
        "0.1,0.01,0.001,0.0001,0.00001", "0.1, 0.01,0.001,0.0001"})
        Reject([&] { Hierarchy::ParseThresholds(invalid); });
    SPANN::Index<float> index;
    CHECK(index.SetParameter("HierarchyLabelSelectivity", "0.01,0.001,0.0001,0.00001", "SelectHead") == ErrorCode::Success);
    CHECK(index.SetParameter("HierarchyLabelSelectivity", "0.1,0.2,0.01,0.001", "SelectHead") != ErrorCode::Success);
    CHECK(index.SetParameter("HierarchyLabelSelectivity", "0.01,0.001,0.0001,0.00001", "SearchSSDIndex") != ErrorCode::Success);
    for (int tag = 1; tag <= 4; ++tag) {
        std::unordered_map<std::uint32_t, std::uint64_t> counts = {{1,10000},{2,1000},{3,100},{4,10}};
        ++counts[tag];
        counts[0] = 1000000 - counts[1] - counts[2] - counts[3] - counts[4];
        auto above = Support(8);
        CHECK(above.SetTagVectorCounts(1000000, counts));
        CHECK(Hierarchy::Tier(above, tag, thresholds) == (tag == 1 ? 0 : tag));
    }
}
static void PersistenceTests(const std::filesystem::path& directory)
{
    auto support = Support(8);
    auto hierarchy = Fixture(support);
    const auto validate = [&](const Hierarchy& value) {
        value.Validate(support, 42, hierarchy.Metadata().thresholds, 1, VectorValueType::Float);
    };
    validate(hierarchy);
    Reject([&] { validate(Fixture(support, true)); });
    Reject([&] { validate(Fixture(support, false, true)); });
    const auto path = (directory / "valid.bin").string();
    hierarchy.Save(path);
    Reject([&] { hierarchy.Save(path); });
    Hierarchy loaded;
    loaded.Load(path, support, 42, hierarchy.Metadata().thresholds, 1, VectorValueType::Float);
    CHECK(loaded.Fingerprint() == hierarchy.Fingerprint());
    CHECK(loaded.At(3).tier == 5 && *loaded.Begin(3) == 5 && loaded.At(3).parent == 7);
    const auto legacy = Fixture(support, false, false, 1);
    const auto legacyPath = (directory / "legacy.bin").string();
    legacy.Save(legacyPath);
    loaded.Load(legacyPath, support, 42, hierarchy.Metadata().thresholds, 1, VectorValueType::Float);
    CHECK(loaded.Metadata().version == 1 && loaded.Fingerprint() == legacy.Fingerprint());
    auto multiple = Fixture(support, false, false, 3);
    CheckOwners(multiple);
    const auto multiplePath = (directory / "multiple.bin").string();
    multiple.Save(multiplePath);
    loaded.Load(multiplePath, support, 42, hierarchy.Metadata().thresholds, 1, VectorValueType::Float);
    CHECK(loaded.Metadata().version == 3 && loaded.Fingerprint() == multiple.Fingerprint());
    CheckOwners(loaded);
    Reject([&] { loaded.SetEntry(0, 4); });
    Reject([&] { loaded.AddRow(1, 1, 2, {1}); });
    for (bool omitReplica : {false, true}) {
        Hierarchy replicated;
        replicated.Initialize(hierarchy.Metadata());
        replicated.AddRow(1, 1, 2, {1,2,6,7});
        replicated.AddRow(2, 1, 2, omitReplica ? std::vector<std::uint32_t>{1,2,7} :
            std::vector<std::uint32_t>{1,2,6,7});
        replicated.AddRow(3, 2, 3, {3,7});
        replicated.AddRow(4, 3, 4, {4});
        replicated.AddRow(5, 4, 5, {5});
        replicated.AddRow(1, Hierarchy::None, 5, {0,1,2,3,4});
        for (int head = 0; head < 8; ++head) replicated.SetEntry(head, 5);
        if (omitReplica) Reject([&] { validate(replicated); });
        else validate(replicated);
    }
    Reject([&] { loaded.Load(path, support, 41, hierarchy.Metadata().thresholds, 1, VectorValueType::Float); });
    Reject([&] { loaded.Load(path, support, 42, Hierarchy::ParseThresholds("0.02,0.001,0.0001,0.00001"), 1, VectorValueType::Float); });
    for (const auto name : {"truncated.bin", "corrupt.bin", "trailing.bin"}) {
        const auto bad = directory / name;
        std::filesystem::copy_file(path, bad);
        if (std::string(name) == "truncated.bin") std::filesystem::resize_file(bad, 128);
        else if (std::string(name) == "trailing.bin") {
            std::ofstream output(bad, std::ios::binary | std::ios::app); output.put(0);
        } else {
            std::fstream output(bad, std::ios::binary | std::ios::in | std::ios::out);
            output.seekp(128 + 8 * sizeof(int)); output.put(0xff);
        }
        Reject([&] { loaded.Load(bad.string(), support, 42, hierarchy.Metadata().thresholds, 1, VectorValueType::Float); });
    }
}
static void QueryTests()
{
    auto support = Support(8);
    auto hierarchy = Fixture(support);
    BKT::Index<float> heads;
    heads.InitializeHeadNodeMeta(8, 1, heads.GetHeadNodeHierWidths(), true);
    const std::vector<Cache::NumQuantParam> params = {{0,255}};
    for (int head = 0; head < 8; ++head) {
        auto* mask = heads.GetHeadNodeNumQuantMutable(head);
        std::fill_n(mask, Cache::NUM_QUANT_WORDS, 0);
        Cache::NumQuantInsert(mask, 0, Cache::NumQuantBucket(params[0], head));
    }
    hierarchy.Refresh(heads, params);
    const auto run = [&](const std::vector<std::uint32_t>& labels, int limit, int& distances) {
        SPANN::RoutingPredicate predicate(nullptr, labels.data(), labels.size(), {1}, params);
        const auto signature = [&](std::size_t, int id) { return hierarchy.MayMatch(id, labels, predicate); };
        const auto distance = [&](std::size_t, int) { ++distances; return 1.0f; };
        SPANN::HierarchyPostingQuery<Hierarchy, decltype(signature), decltype(distance),
            Hierarchy, SPANN::NoPostingPrefetch, SPANN::SparsePostingLayout>
            query(hierarchy, hierarchy, signature, distance);
        query.SetSearchCapacity(64);
        std::set<std::uint32_t> visited;
        query.Expand({0,0,6}, [&](const std::uint32_t* ids, int count) {
            COMMON::PostingNavigation::RowResult row;
            for (int i = 0; i < count; ++i) {
                ++row.degree; ++row.eligible;
                row.newCandidates += visited.insert(ids[i]).second;
            }
            row.canContinue = static_cast<int>(visited.size()) < limit;
            row.targetFilled = !row.canContinue;
            return row;
        });
        return visited;
    };
    int distances = 0;
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    COMMON::GraphAccessStats stats;
    {
        COMMON::ScopedGraphAccessStats capture(&stats);
        CHECK(run({4}, 100, distances) == std::set<std::uint32_t>{5});
    }
    CHECK(stats.m_h2Postings.representativeDistances == 0 && stats.m_upperPostings.representativeDistances == 2);
    CHECK(stats.m_selectedPostingRows == 1 && stats.m_auxiliaryMembers == 1);
    CHECK(stats.m_selectedH2ZeroEligibleRows == 0 && stats.m_selectedH2ZeroFreshRows == 0);
#else
    CHECK(run({4}, 100, distances) == std::set<std::uint32_t>{5});
#endif
    CHECK(distances == 2);
    distances = 0;
    CHECK(run({1,2,4,1}, 100, distances) == (std::set<std::uint32_t>{1,2,3,5,6,7}));
    distances = 0;
    CHECK(run({1}, 1, distances) == (std::set<std::uint32_t>{1,2,6,7}));
    distances = 0;
    CHECK(run({999}, 100, distances).empty());
    Cache::DNFPredicate otherColumn;
    otherColumn.clauses.push_back({{{0,4,Cache::DNF_EQ,0},{2,99,Cache::DNF_EQ,0}}});
    CHECK(hierarchy.MayMatch(3, {4}, SPANN::RoutingPredicate(&otherColumn, nullptr, 0, {1}, params)));
    Cache::DNFPredicate numeric;
    numeric.clauses.push_back({{{0,4,Cache::DNF_EQ,0},{1,0,Cache::DNF_LE,1}}});
    CHECK(!hierarchy.MayMatch(3, {4}, SPANN::RoutingPredicate(&numeric, nullptr, 0, {1}, params)));

    auto multiple = Fixture(support, false, false, 3);
    multiple.Refresh(heads, params);
    const auto direct = [&](const std::vector<int>& anchors, const std::vector<std::uint32_t>& labels) {
        SPANN::RoutingPredicate predicate(nullptr, labels.data(), labels.size(), {1}, params);
        const auto signature = [&](std::size_t, int id) { return multiple.MayMatch(id, labels, predicate); };
        const auto distance = [&](std::size_t, int id) { return multiple.IsLeaf(id) ? 1.0f : 10.0f; };
        SPANN::HierarchyPostingQuery<Hierarchy, decltype(signature), decltype(distance),
            Hierarchy, SPANN::NoPostingPrefetch, SPANN::SparsePostingLayout>
            query(multiple, multiple, signature, distance);
        CHECK(query.PreferResultAnchors());
        query.SetSearchCapacity(64);
        std::set<std::uint32_t> visited;
        query.Expand(anchors, [&](const std::uint32_t* ids, int count) {
            COMMON::PostingNavigation::RowResult row;
            for (int i = 0; i < count; ++i) {
                ++row.degree; ++row.eligible;
                row.newCandidates += visited.insert(ids[i]).second;
            }
            return row;
        });
        return visited;
    };
    CHECK(direct({7,7}, {1,2,1}) == (std::set<std::uint32_t>{1,2,3,6,7}));
    CHECK(direct({0}, {4}) == (std::set<std::uint32_t>{5}));
    CHECK(direct({0}, {999}).empty());
}
static void ChildLabelFastpathTests()
{
    const auto support = Support(8);
    BKT::Index<float> heads;
    heads.InitializeHeadNodeMeta(8, 1, heads.GetHeadNodeHierWidths(), true);
    const std::vector<Cache::NumQuantParam> params = {{0,255}};
    for (int head = 0; head < 8; ++head) {
        auto* mask = heads.GetHeadNodeNumQuantMutable(head);
        std::fill_n(mask, Cache::NUM_QUANT_WORDS, 0);
        Cache::NumQuantInsert(mask, 0, Cache::NumQuantBucket(params[0], head));
    }
    int savedSignatures = 0;
    for (int version : {1,2,3}) {
        auto hierarchy = Fixture(support, false, false, version);
        hierarchy.Refresh(heads, params);
        for (const auto& labels : std::vector<std::vector<std::uint32_t>>{{1},{4},{1,2,4,1},{999},{}}) {
            CHECK(!hierarchy.TerminalMayMatchLabels(0, {4}));
            CHECK(hierarchy.TerminalMayMatchLabels(0, {1,2}));
            CHECK(hierarchy.TerminalMayMatchLabels(4, {}));
            for (bool numeric : {false,true}) for (int width : {0,2,8}) for (int capacity : {1,64}) {
                Cache::DNFPredicate dnf;
                dnf.clauses.push_back({{{1,1,Cache::DNF_LE,1}}});
                SPANN::RoutingPredicate predicate(numeric ? &dnf : nullptr,
                    labels.data(), labels.size(), {1}, params);
                const auto run = [&](bool early) {
                    int signatures = 0, distances = 0;
                    const auto signature = [&](std::size_t, int id) {
                        ++signatures;
                        return hierarchy.MayMatch(id, labels, predicate);
                    };
                    const auto distance = [&](std::size_t, int id) { ++distances; return float(id + 1); };
                    const auto childMayMatch = [&](std::size_t, int id) {
                        return !early || hierarchy.TerminalMayMatchLabels(id, labels);
                    };
                    SPANN::HierarchyPostingQuery<Hierarchy, decltype(signature), decltype(distance),
                        Hierarchy, SPANN::NoPostingPrefetch, SPANN::SparsePostingLayout, decltype(childMayMatch)>
                        query(hierarchy, hierarchy, signature, distance, {}, width, childMayMatch);
                    query.SetSearchCapacity(capacity);
                    std::vector<std::vector<std::uint32_t>> rows;
                    query.Expand({0,0,6}, [&](const std::uint32_t* ids, int count) {
                        rows.emplace_back(ids, ids + count);
                        COMMON::PostingNavigation::RowResult row;
                        row.degree = row.eligible = row.newCandidates = count;
                        return row;
                    });
                    return std::make_tuple(rows, distances, signatures, query.Converged());
                };
                const auto before = run(false), after = run(true);
                CHECK(std::get<0>(before) == std::get<0>(after));
                CHECK(std::get<1>(before) == std::get<1>(after));
                CHECK(std::get<3>(before) == std::get<3>(after));
                CHECK(std::get<2>(after) <= std::get<2>(before));
                savedSignatures += std::get<2>(before) - std::get<2>(after);
            }
        }
    }
    CHECK(savedSignatures > 0);
    std::cout << "PASS sparse V1/V2/V3 exact child-label pruning, OR/numeric/empty predicates, frontier order and convergence parity\n";
}
template<class T> static void ConstructionTests(const std::filesystem::path& directory)
{
    constexpr int count = 1024, dimension = sizeof(T) == 1 ? 128 : 4;
    auto heads = std::make_shared<BKT::Index<T>>();
    for (auto parameter : std::vector<std::pair<const char*,const char*>>{
        {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"BKTKmeansK","4"},{"TPTNumber","1"},
        {"NeighborhoodSize","16"},{"RefineIterations","1"},{"MaxCheckForRefineGraph","128"}})
        CHECK(heads->SetParameter(parameter.first, parameter.second) == ErrorCode::Success);
    std::vector<T> data(count * dimension);
    for (int id = 0; id < count; ++id)
        for (int d = 0; d < dimension; ++d) data[id * dimension + d] = static_cast<T>((id * (d + 1)) % 101);
    CHECK(heads->BuildIndex(data.data(), count, dimension) == ErrorCode::Success);
    const auto support = Support(count);
    std::vector<SPANN::SecondLevelHeadPostings> csr(4);
    std::vector<std::shared_ptr<VectorSet>> catalogs;
    int lower = count;
    for (int level = 0; level < 4; ++level) {
        const int upper = lower / 2;
        std::vector<std::uint32_t> physical(upper);
        std::iota(physical.begin(), physical.end(), 0);
        catalogs.push_back(std::make_shared<SPANN::CanonicalHierarchyVectorView>(heads, physical));
        std::vector<std::vector<std::uint32_t>> rows(upper);
        for (int id = 0; id < lower; ++id) {
            rows[id % upper].push_back(id); rows[(id + 1) % upper].push_back(id);
        }
        std::vector<std::uint64_t> offsets{0}, ids(upper);
        std::iota(ids.begin(), ids.end(), 0);
        std::vector<std::uint32_t> members;
        for (const auto& row : rows) {
            members.insert(members.end(), row.begin(), row.end()); offsets.push_back(members.size());
        }
        CHECK(csr[level].Initialize(lower, upper, 2, 42, support.ContentFingerprint(), 0, 1, ids,
            offsets, members, std::vector<Cache::PostingBitmask>(upper)));
        lower = upper;
    }
    SPANN::PostingOwners owners(csr);
    SPANN::Options options;
    options.m_hierarchyLabelSelectivity = "0.01,0.001,0.0001,0.00001";
    options.m_iSelectHeadNumberOfThreads = 2; options.m_iBKTKmeansK = 4; options.m_iBKTLeafSize = 4;
    options.m_fBalanceFactor = 1; options.m_ratio = 0.12; options.m_secondLevelReplicaCount = 2;
    options.m_distCalcMethod = DistCalcMethod::L2;
    options.m_indexAlgoType = IndexAlgoType::BKT;
    auto hierarchy = SPANN::BuildSparseLabelHierarchy<T>(*heads, support, catalogs, owners, options, 42,
        {{"TPTNumber","1"},{"NeighborhoodSize","16"},{"RefineIterations","1"},{"MaxCheck","128"}});
    const auto path = (directory / ("constructed-" + std::to_string(sizeof(T)) + ".bin")).string();
    hierarchy.Save(path);
    Hierarchy loaded;
    loaded.Load(path, support, 42, hierarchy.Metadata().thresholds, dimension, heads->GetVectorValueType());
    CHECK(loaded.Fingerprint() == hierarchy.Fingerprint());
    CHECK(loaded.Metadata().version == 3 && loaded.Metadata().assignment == 2);
    CheckOwners(loaded);
    std::array<int,4> leaves{};
    for (int id = 0; id < loaded.Count(); ++id)
        if (loaded.IsLeaf(id)) ++leaves[loaded.At(id).tier - 2];
    CHECK(leaves == (std::array<int,4>{16,8,1,1}));
    for (int tag = 1; tag <= 4; ++tag) {
        std::vector<int> rows;
        for (int id = 0; id < loaded.Count(); ++id) if (loaded.At(id).tag == static_cast<unsigned>(tag)) rows.push_back(id);
        for (int head : support.TagHeads().at(tag)) {
            float nearest = MaxDist, assigned = MaxDist;
            int copies = 0;
            for (int id : rows) {
                const float distance = heads->ComputeDistance(heads->GetSample(head),
                    heads->GetSample(loaded.At(id).representative));
                nearest = std::min(nearest, distance);
                if (std::binary_search(loaded.Begin(id), loaded.End(id), static_cast<unsigned>(head))) {
                    assigned = std::min(assigned, distance);
                    ++copies;
                }
            }
            CHECK(copies == std::min<int>(options.m_secondLevelReplicaCount, rows.size()));
            CHECK(assigned == nearest);
        }
    }
    std::vector<SizeType> eligible(128);
    std::iota(eligible.begin(), eligible.end(), 0);
    for (bool parallel : {false, true}) {
        options.m_parallelBKTBuild = parallel;
        const auto selected = SPANN::SelectSparseRepresentatives<T>(*heads, eligible, 16, options);
        CHECK(selected.size() == 16 && std::adjacent_find(selected.begin(), selected.end()) == selected.end());
    }
    options.m_hierarchyLabelSelectivity = "1e-9,1e-10,1e-11,1e-12";
    auto empty = SPANN::BuildSparseLabelHierarchy<T>(*heads, support, catalogs, owners, options, 42);
    CHECK(empty.Count() == 0);
    CheckOwners(empty);
    auto identical = std::make_shared<BKT::Index<T>>();
    for (auto parameter : std::vector<std::pair<const char*,const char*>>{
        {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"BKTKmeansK","4"},{"TPTNumber","1"},
        {"NeighborhoodSize","16"},{"RefineIterations","1"},{"MaxCheckForRefineGraph","128"}})
        CHECK(identical->SetParameter(parameter.first, parameter.second) == ErrorCode::Success);
    std::vector<T> identicalData(eligible.size() * dimension, static_cast<T>(7));
    CHECK(identical->BuildIndex(identicalData.data(), eligible.size(), dimension) == ErrorCode::Success);
    for (bool parallel : {false, true}) {
        options.m_parallelBKTBuild = parallel;
        for (int quota : {1,16,128}) {
            const auto selected = SPANN::SelectSparseRepresentatives<T>(*identical, eligible, quota, options);
            CHECK(selected.size() == static_cast<std::size_t>(quota) &&
                std::adjacent_find(selected.begin(), selected.end()) == selected.end());
            CHECK(selected.front() >= 0 && selected.back() < identical->GetNumSamples());
        }
    }
}
static void SparseNavigationCutoffTests()
{
    const auto support = Support(8);
    for (int version : {1,2,3}) for (int width : {0,1,2,8}) {
        Hierarchy hierarchy;
        auto header = Fixture(support).Metadata();
        header.version = version; header.assignment = version - 1;
        hierarchy.Initialize(header);
        hierarchy.AddRow(1, 1, 2, {1,2,6,7});
        hierarchy.AddRow(3, 2, 3, {3,7});
        hierarchy.AddRow(4, 3, 4, {4});
        hierarchy.AddRow(5, 4, 5, {5});
        hierarchy.AddRow(1, Hierarchy::None, 4, {0});
        hierarchy.AddRow(3, Hierarchy::None, 4, {1});
        hierarchy.AddRow(4, Hierarchy::None, 4, {2});
        hierarchy.AddRow(1, Hierarchy::None, 5, {4,5,6,3});
        for (int head=0;head<8;++head) hierarchy.SetEntry(head,head==6?7:4);
        hierarchy.Validate(support,42,header.thresholds,1,VectorValueType::Float);
        hierarchy.BuildOwners();
        BKT::Index<float> heads;
        hierarchy.Refresh(heads,{});
        const std::vector<std::uint32_t> labels{1,2,3,4,1};
        SPANN::RoutingPredicate predicate(nullptr,labels.data(),labels.size(),{1},{});
        std::set<int> checked;
        const auto signature=[&](std::size_t,int id) {
            CHECK(checked.insert(id).second);
            return hierarchy.MayMatch(id,labels,predicate);
        };
        const std::array<float,8> distances{3,4,5,6,2,1,50,100};
        const auto distance=[&](std::size_t,int id) { return distances.at(id); };
        SPANN::HierarchyPostingQuery<Hierarchy,decltype(signature),decltype(distance),
            Hierarchy,SPANN::NoPostingPrefetch,SPANN::SparsePostingLayout>
            query(hierarchy,hierarchy,signature,distance,{},width);
        query.SetSearchCapacity(192);
        std::set<unsigned> visited;
        query.Expand({0,6,0},[&](const std::uint32_t* ids,int count) {
            COMMON::PostingNavigation::RowResult row;
            for (int i=0;i<count;++i) {
                ++row.degree; ++row.eligible;
                row.newCandidates+=visited.insert(ids[i]).second;
            }
            return row;
        });
        CHECK(visited==(width==0 || width>=3 ? std::set<unsigned>{1,2,3,4,5,6,7} :
                                              std::set<unsigned>{1,2,3,5,6,7}));
        CHECK(checked.count(2)==(width==0 || width>=3 ? 1U:0U));
        CHECK(checked.count(1)==1 && checked.count(7)==1);
    }
}
int main()
{
    const auto directory = std::filesystem::current_path() / ("sparse-label-test-" + std::to_string(getpid()));
    try {
        CHECK(std::filesystem::create_directory(directory));
        ThresholdTests(); CountTests(); PersistenceTests(directory); QueryTests(); ChildLabelFastpathTests(); SparseNavigationCutoffTests();
        ConstructionTests<float>(directory); ConstructionTests<std::uint8_t>(directory);
        for (const auto& entry : std::filesystem::directory_iterator(directory)) std::filesystem::remove(entry.path());
        std::filesystem::remove(directory);
        std::cout << "PASS sparse label tiers, complete support coverage, higher direct rows, OR, budget, persistence and native build\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\nFixture retained at " << directory << '\n';
        return 1;
    }
}
