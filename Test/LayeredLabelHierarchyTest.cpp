// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#include "inc/Core/SPANN/LayeredLabelHierarchyBuilder.h"
#include "inc/Core/SPANN/Index.h"
#include "inc/Core/BKT/Index.h"
#include <filesystem>
#include <iostream>
#include <set>
#include <unistd.h>
#include <sys/wait.h>

using namespace SPTAG;
using Hierarchy = SPANN::SparseLabelHierarchy;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Layered hierarchy test line " + std::to_string(__LINE__)); } while (false)
template<class Action> void Reject(const Action& action)
{
    bool failed = false;
    try { action(); } catch (const std::exception&) { failed = true; }
    CHECK(failed);
}
static SPANN::LimitedTagSupport Support(int count = 8)
{
    SPANN::LimitedTagSupport support;
    CHECK(support.Initialize(count, 2, 1, 0, 1, 123));
    CHECK(support.SetTagVectorCounts(1000000000, count == 8
        ? std::unordered_map<std::uint32_t, std::uint64_t>{{0,999967000},{1,1000},{2,2000},{3,30000}}
        : std::unordered_map<std::uint32_t, std::uint64_t>{{0,999997000},{1,1000},{2,2000}}));
    for (int head = 0; head < count; ++head) {
        std::vector<std::uint32_t> tags;
        if (count != 8) tags = head == count - 1 ? std::vector<std::uint32_t>{0} : std::vector<std::uint32_t>{1,2};
        else if (head == 0) tags = {1,2};
        else if (head == 1 || head == 5) tags = {1};
        else if (head == 2 || head == 6) tags = {2};
        else if (head == 3) tags = {3};
        else tags = {0};
        CHECK(support.SetHeadTags(head, tags) && support.SetHeadAttributes(head, tags.data(), 1));
    }
    std::string error;
    CHECK(support.Finalize(&error));
    return support;
}
static Hierarchy Fixture(const SPANN::LimitedTagSupport& support, int invalid = 0)
{
    Hierarchy::Header header;
    header.version = 4; header.assignment = 3; header.heads = 8;
    header.dimension = 1; header.valueType = static_cast<unsigned>(VectorValueType::Float);
    header.headIDs = 42; header.support = support.ContentFingerprint(); header.replicas = 2;
    header.thresholds = Hierarchy::ParseThresholds("0.01,0.001,0.0001,0.00001");
    header.caps = {10,10,10,10};
    Hierarchy result;
    result.Initialize(header);
    auto b = invalid == 1 ? std::vector<std::uint32_t>{2,6} : std::vector<std::uint32_t>{0,2,6};
    if (invalid == 2) b.push_back(6);
    result.AddLayeredRow(0, 1, 2, {{1,{0,1,5}}, {2,b}});
    result.AddLayeredRow(3, 3, 2, {{3,{3}}});
    result.AddLayeredRow(0, 1, 3, {{1,{0}}, {2,{0}}});
    result.AddLayeredRow(3, 3, 3, {{3,{1}}});
    result.AddLayeredRow(0, 1, 4, {{1,{invalid == 3 ? 0U : 2U}}, {2,{2}}});
    result.AddLayeredRow(3, 3, 4, {{3,{3}}});
    result.AddLayeredRow(0, 1, 5, {{1,{4}}, {2,{4}}});
    for (int head = 0; head < 8; ++head) result.SetEntry(head, head == 3 ? 1 : 0);
    return result;
}
static void SelectionTests()
{
    const std::vector<std::uint32_t> tags{9,9,9,2,3};
    const auto tagAt = [&](int id) { return tags.at(id); };
    CHECK(SPANN::SelectNearestLimitedLabels(9, 0, {{0,0},{1,1},{2,2},{3,3}}, 2, tagAt) ==
        std::vector<std::uint32_t>{9});
    CHECK(SPANN::SelectNearestLimitedLabels(9, 0, {{4,4},{0,0},{1,3},{2,1}}, 2, tagAt) ==
        (std::vector<std::uint32_t>{9,2}));
    CHECK(SPANN::SelectNearestLimitedLabels(9, 0, {{1,3},{1,2}}, 2, tagAt) ==
        std::vector<std::uint32_t>{9});
    Reject([&] { SPANN::SelectNearestLimitedLabels(9, 0, {}, 0, tagAt); });
    const float sample = 0;
    for (float factor : {0.5f,1.0f,2.0f}) for (int replica : {1,2,4}) {
        COMMON::QueryResultSet<float> query(&sample, 4);
        for (int id = 0; id < 4; ++id) query.AddPoint(id, float(id + 1));
        query.SortResult();
        const auto distance = [](int left, int right) { return float(std::abs(left - right)) / 4; };
        std::vector<SizeType> expected;
        for (int rank = 0; rank < 4 && expected.size() < static_cast<unsigned>(replica); ++rank) {
            const auto* candidate = query.GetResult(rank);
            bool accepted = true;
            for (auto prior : expected)
                if (factor * distance(candidate->VID, prior) <= candidate->Dist) { accepted = false; break; }
            if (accepted) expected.push_back(candidate->VID);
        }
        std::vector<SizeType> selected, emitted;
        CHECK(SPANN::SelectLimitedTagPostingCandidates(query, 4, replica, factor,
            [](int) { return true; }, distance, selected,
            [&](const BasicResult& result) { emitted.push_back(result.VID); }));
        CHECK(selected == expected && emitted == expected && selected.size() == 1);
        CHECK(!SPANN::SelectLimitedTagPostingCandidates(query, 4, replica, factor,
            [](int) { return false; }, distance, selected, [](const BasicResult&) {}));
    }
    auto support = Support();
    const auto thresholds = Fixture(support).Metadata().thresholds;
    CHECK(Hierarchy::Admitted(support, 1, thresholds, 5));
    CHECK(!Hierarchy::Admitted(support, 3, thresholds, 5));
    auto equal = thresholds;
    equal[3] = 0.000001;
    CHECK(!Hierarchy::Admitted(support, 1, equal, 5));
    std::cout << "PASS repeated candidate labels, anchor-first support, strict original selectivity and unchanged H RNG/no-fill\n";
}
static void CensusTests()
{
    const auto support = Support();
    for (bool replicated : {false, true}) {
        std::vector<SPANN::SecondLevelHeadPostings> csr(4);
        int lower = 8;
        const int coarseCount = replicated ? 1 : 2;
        for (int level = 0; level < 4; ++level) {
            const int upper = replicated && level ? 1 : 2;
            const int replicas = replicated && !level ? 2 : 1;
            std::vector<std::uint64_t> ids(upper), offsets{0};
            for (int id = 0; id < upper; ++id) ids[id] = replicated ? id : id * (lower / upper);
            std::vector<std::uint32_t> members;
            for (int parent = 0; parent < upper; ++parent) {
                for (int child = 0; child < lower; ++child)
                    if (replicated || child / (lower / upper) == parent) members.push_back(child);
                offsets.push_back(members.size());
            }
            CHECK(csr[level].Initialize(lower, upper, replicas, 42, support.ContentFingerprint(), 0, 1,
                ids, offsets, members, std::vector<Cache::PostingBitmask>(upper)));
            lower = upper;
        }
        SPANN::PostingOwners owners(csr);
        auto bytes = ByteArray::Alloc(coarseCount * sizeof(float));
        auto* values = reinterpret_cast<float*>(bytes.Data());
        for (int cell = 0; cell < coarseCount; ++cell) values[cell] = cell * 100;
        BasicVectorSet coarse(bytes, VectorValueType::Float, 1, coarseCount);
        SPANN::Options options;
        options.m_hierarchyLocalTarget = replicated ? 4 : 2;
        options.m_hierarchyLocalWindow = replicated ? 8 : 4; options.m_ratio = .5;
        options.m_iBKTLeafSize = 4; options.m_iSamples = 32;
        options.m_iSelectHeadNumberOfThreads = 1; options.m_fBalanceFactor = 1;
        options.m_distCalcMethod = DistCalcMethod::L2;
        const auto policy = SPANN::BuildLocalLabelCensus<float>(support, owners, coarse, options);
        if (replicated) {
            CHECK(policy.windows.size() == 1 && policy.windows[0][0] == 8);
            CHECK(policy.Admits(0, 1, 5));
        } else {
            CHECK(!policy.Admits(0, 1, 2) && policy.Admits(5, 1, 2));
            CHECK(!policy.Admits(5, 1, 5));
            CHECK(policy.windows[0][0] == 4 && policy.windows[0][3] == 8);
            options.m_ratio = .99;
            const auto stopped = SPANN::BuildLocalLabelCensus<float>(support, owners, coarse, options);
            CHECK(!stopped.Admits(0, 1, 3) && !stopped.Admits(0, 1, 5));
            CHECK(stopped.Admits(5, 1, 5));
            options.m_ratio = .5;
        }
        CHECK(!policy.Admits(0, 999, 2));
        options.m_hierarchyLocalWindow = 1;
        options.m_hierarchyLocalTarget = 1;
        const auto capped = SPANN::BuildLocalLabelCensus<float>(support, owners, coarse, options);
        CHECK(capped.Admits(0, 1, 2));
        auto invalid = policy;
        invalid.masks[0] = 2;
        Reject([&] { invalid.Validate(8); });
        invalid = policy; invalid.home[0] = coarseCount;
        Reject([&] { invalid.Validate(8); });
    }
    std::cout << "PASS adjacent mass conservation, replica dedup, singleton coarse tree and region-local admission\n";
}
static void PersistenceTests(const std::filesystem::path& directory)
{
    const auto support = Support();
    auto hierarchy = Fixture(support);
    const auto validate = [&](const Hierarchy& value) {
        value.Validate(support, 42, hierarchy.Metadata().thresholds, 1, VectorValueType::Float);
    };
    validate(hierarchy);
    for (int invalid : {1,2,3}) Reject([&] { validate(Fixture(support, invalid)); });
    hierarchy.BuildOwners();
    for (int head = 0; head < hierarchy.HeadCount(); ++head) {
        std::set<int> unique;
        for (int parent : hierarchy.Parents(0, head)) CHECK(unique.insert(parent).second);
    }
    std::vector<int> owners;
    hierarchy.VisitUpperParents(0, [&](int id) { owners.push_back(id); });
    CHECK(owners == std::vector<int>{2});
    CHECK(hierarchy.FindLabel(0, 2) && !hierarchy.FindLabel(0, 3));
    std::vector<std::uint32_t> scratch;
    auto row = hierarchy.SelectMembers(0, {2}, scratch);
    CHECK((std::vector<std::uint32_t>(row.first, row.second) == std::vector<std::uint32_t>{0,2,6}));
    row = hierarchy.SelectMembers(0, {1,2,2}, scratch);
    CHECK((std::vector<std::uint32_t>(row.first, row.second) == std::vector<std::uint32_t>{0,1,2,5,6}));
    row = hierarchy.SelectMembers(2, {1,2}, scratch);
    CHECK(row.second - row.first == 1 && *row.first == 0);
    row = hierarchy.SelectMembers(0, {999}, scratch);
    CHECK(row.first == row.second);
    const auto path = directory / "layered.bin";
    hierarchy.Save(path.string());
    Reject([&] { hierarchy.Save(path.string()); });
    Hierarchy loaded;
    loaded.Load(path.string(), support, 42, hierarchy.Metadata().thresholds, 1, VectorValueType::Float);
    CHECK(loaded.Layered() && loaded.Fingerprint() == hierarchy.Fingerprint());
    owners.clear();
    loaded.VisitUpperParents(2, [&](int id) { owners.push_back(id); });
    CHECK(owners == std::vector<int>{4});
    Reject([&] { loaded.SetEntry(0, 0); });
    Reject([&] { loaded.AddLayeredRow(0, 1, 2, {{1,{0}}}); });
    Reject([&] { loaded.Load(path.string(), support, 43, hierarchy.Metadata().thresholds, 1, VectorValueType::Float); });
    for (const auto name : {"truncated.bin", "trailing.bin", "corrupt-label.bin", "oversized-labels.bin"}) {
        const auto bad = directory / name;
        std::filesystem::copy_file(path, bad);
        if (std::string(name) == "truncated.bin") std::filesystem::resize_file(bad, 128);
        else if (std::string(name) == "trailing.bin") {
            std::ofstream out(bad, std::ios::binary | std::ios::app); out.put(0);
        } else {
            std::fstream out(bad, std::ios::binary | std::ios::in | std::ios::out);
            if (std::string(name) == "oversized-labels.bin") out.seekp(120);
            else out.seekp(-16, std::ios::end);
            out.put(0xff);
        }
        Reject([&] { loaded.Load(bad.string(), support, 42, hierarchy.Metadata().thresholds, 1, VectorValueType::Float); });
    }
    std::cout << "PASS layered persistence, source authentication, malformed lanes, adjacent coverage and owner dedup\n";
}
static void QueryTests()
{
    const auto support = Support();
    auto hierarchy = Fixture(support);
    hierarchy.Validate(support, 42, hierarchy.Metadata().thresholds, 1, VectorValueType::Float);
    hierarchy.BuildOwners();
    BKT::Index<float> heads;
    heads.InitializeHeadNodeMeta(8, 1, heads.GetHeadNodeHierWidths(), true);
    const std::vector<Cache::NumQuantParam> params{{0,255}};
    for (int head = 0; head < 8; ++head) {
        auto* mask = heads.GetHeadNodeNumQuantMutable(head);
        std::fill_n(mask, Cache::NUM_QUANT_WORDS, 0);
        Cache::NumQuantInsert(mask, 0, Cache::NumQuantBucket(params[0], head));
    }
    hierarchy.Refresh(heads, params);
    for (const auto& labels : std::vector<std::vector<std::uint32_t>>{{1},{2},{1,2,2},{3},{999},{}}) {
        for (bool numeric : {false,true}) for (int width : {0,1,8}) for (int budget : {1,100}) {
            Cache::DNFPredicate dnf;
            dnf.clauses.push_back({{{1,200,Cache::DNF_GE,1}}});
            SPANN::RoutingPredicate predicate(numeric ? &dnf : nullptr, labels.data(), labels.size(), {1}, params);
            const auto signature = [&](std::size_t, int id) { return hierarchy.MayMatch(id, labels, predicate); };
            std::set<int> scored;
            const auto distance = [&](std::size_t, int id) {
                CHECK(scored.insert(id).second);
                return float(hierarchy.IsLeaf(id) ? 10 + id : id);
            };
            const auto child = [&](std::size_t, int id) { return hierarchy.TerminalMayMatchLabels(id, labels); };
            std::vector<std::uint32_t> scratch;
            const auto select = [&](std::size_t, int id) { return hierarchy.SelectMembers(id, labels, scratch); };
            SPANN::HierarchyPostingQuery<Hierarchy, decltype(signature), decltype(distance), Hierarchy,
                SPANN::NoPostingPrefetch, SPANN::SparsePostingLayout, decltype(child), decltype(select)>
                query(hierarchy, hierarchy, signature, distance, {}, width, child, select);
            query.SetSearchCapacity(32);
            std::set<std::uint32_t> visited;
            query.Expand({7,3,7}, [&](const std::uint32_t* ids, int count) {
                COMMON::PostingNavigation::RowResult row;
                for (int i = 0; i < count; ++i) {
                    bool match = false;
                    for (auto tag : labels) match |= support.Supports(ids[i], tag);
                    CHECK(match);
                    ++row.degree; ++row.eligible;
                    row.newCandidates += visited.insert(ids[i]).second;
                }
                row.canContinue = visited.size() < static_cast<unsigned>(budget);
                return row;
            });
            std::set<std::uint32_t> expected;
            if (!numeric) for (auto tag : labels) {
                const auto found = support.TagHeads().find(tag);
                if (found != support.TagHeads().end()) expected.insert(found->second.begin(), found->second.end());
            }
            CHECK(visited == expected);
        }
    }
    std::cout << "PASS label-indexed child descent, OR dedup, zero-match anchors, numeric pruning and complete-row budgets\n";
}
template<class T> static void ConstructionTests(const std::filesystem::path& directory)
{
    constexpr int count = 257, dimension = sizeof(T) == 1 ? 128 : 4;
    auto heads = std::make_shared<BKT::Index<T>>();
    for (const auto& parameter : std::vector<std::pair<const char*,const char*>>{
        {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"BKTKmeansK","4"},
        {"TPTNumber","1"},{"NeighborhoodSize","16"},{"RefineIterations","1"},{"MaxCheck","512"}})
        CHECK(heads->SetParameter(parameter.first, parameter.second) == ErrorCode::Success);
    std::vector<T> data(count * dimension);
    for (int head = 0; head < count; ++head)
        for (int d = 0; d < dimension; ++d) data[head * dimension + d] = static_cast<T>((head + d) % 256);
    CHECK(heads->BuildIndex(data.data(), count, dimension) == ErrorCode::Success);
    auto support = Support(count);
    CHECK(support.ConfigureExpansion(std::vector<std::uint64_t>(count + 1, 0), {}, {{0,1},{1,1},{2,1}}));
    CHECK(support.Finalize());
    std::vector<SPANN::SecondLevelHeadPostings> csr(4);
    std::vector<std::shared_ptr<VectorSet>> catalogs;
    int lower = count;
    for (int layer = 0; layer < 4; ++layer) {
        int upper = lower / 2;
        std::vector<std::uint32_t> physical(upper);
        std::iota(physical.begin(), physical.end(), 0);
        catalogs.push_back(std::make_shared<SPANN::CanonicalHierarchyVectorView>(heads, physical));
        std::vector<std::uint64_t> ids(upper), offsets{0};
        std::iota(ids.begin(), ids.end(), 0);
        std::vector<std::uint32_t> members;
        for (int head = 0; head < upper; ++head) {
            for (int child = head; child < lower; child += upper) members.push_back(child);
            offsets.push_back(members.size());
        }
        CHECK(csr[layer].Initialize(lower, upper, 1, 42, support.ContentFingerprint(), 0, 1,
            ids, offsets, members, std::vector<Cache::PostingBitmask>(upper)));
        lower = upper;
    }
    SPANN::PostingOwners owners(csr);
    SPANN::Options options;
    options.m_hierarchyLabelSelectivity = "0.01,0.001,0.0001,0.00001";
    options.m_ratio = .5; options.m_iSelectHeadNumberOfThreads = 2;
    options.m_iBKTKmeansK = 4; options.m_iBKTLeafSize = 4; options.m_fBalanceFactor = 1;
    options.m_secondLevelReplicaCount = 2; options.m_internalResultNum = 32;
    options.m_limitedTagSlotsPerHead = 2; options.m_limitedTagMinHeadCount = 1;
    options.m_enableLimitedTagSupportExpansion = true;
    options.m_indexAlgoType = IndexAlgoType::BKT; options.m_distCalcMethod = DistCalcMethod::L2;
    const auto build = [&]() {
        return SPANN::BuildLayeredLabelHierarchy<T>(*heads, support, catalogs, owners, options, 42,
            {{"TPTNumber","1"},{"NeighborhoodSize","16"},{"RefineIterations","1"},{"MaxCheck","512"}});
    };
    auto hierarchy = build();
    CHECK(hierarchy.Layered());
    std::array<int,4> counts{};
    for (int id = 0; id < hierarchy.Count(); ++id) {
        ++counts[hierarchy.At(id).tier - 2];
        CHECK(hierarchy.At(id).reserved <= 2);
        CHECK(hierarchy.FindLabel(id, hierarchy.At(id).tag));
    }
    CHECK((counts == std::array<int,4>{128,64,32,16}));
    const auto path = directory / ("constructed-" + std::to_string(sizeof(T)) + ".bin");
    hierarchy.Save(path.string());
    Hierarchy loaded;
    loaded.Load(path.string(), support, 42, hierarchy.Metadata().thresholds, dimension, heads->GetVectorValueType());
    CHECK(loaded.Fingerprint() == hierarchy.Fingerprint());
    options.m_hierarchyLabelSelectivity.clear();
    options.m_hierarchyLocalTarget = 64;
    options.m_hierarchyLocalWindow = 16;
    auto local = build();
    CHECK(local.Local() && local.HasQueryLabel(0));
    const auto localPath = directory / ("local-" + std::to_string(sizeof(T)) + ".bin");
    local.Save(localPath.string());
    Hierarchy localLoaded;
    localLoaded.Load(localPath.string(), support, 42, Hierarchy::AdmissionParameters(options),
        dimension, heads->GetVectorValueType());
    CHECK(localLoaded.Local() && localLoaded.Fingerprint() == local.Fingerprint());
    CHECK(localLoaded.HasQueryLabel(0));
    const auto hash = localLoaded.Fingerprint();
    localLoaded.ReleaseLocalStatistics();
    CHECK(localLoaded.Fingerprint() == hash && localLoaded.HasQueryLabel(0));
    Reject([&] { localLoaded.Save((directory / "released-local.bin").string()); });
    auto changed = Hierarchy::AdmissionParameters(options);
    ++changed[0];
    Reject([&] { localLoaded.Load(localPath.string(), support, 42, changed, dimension, heads->GetVectorValueType()); });
    const auto corrupt = directory / ("bad-local-" + std::to_string(sizeof(T)) + ".bin");
    std::filesystem::copy_file(localPath, corrupt);
    {
        std::fstream out(corrupt, std::ios::binary | std::ios::in | std::ios::out);
        out.seekp(-1, std::ios::end); out.put(2);
    }
    Reject([&] { localLoaded.Load(corrupt.string(), support, 42, Hierarchy::AdmissionParameters(options),
        dimension, heads->GetVectorValueType()); });
    options.m_hierarchyLocalTarget = 0;
    Reject(build);
    options.m_hierarchyLocalWindow = 0;
    options.m_hierarchyLabelSelectivity = "1e-9,1e-10,1e-11,1e-12";
    CHECK(build().Count() == 0);
    options.m_limitedTagSlotsPerHead = 3;
    Reject(build);
    std::cout << "PASS native layered build, two-label logical replication, fixed physical quotas and empty admission\n";
}
static void NativeRebuildTest(const std::filesystem::path& directory, const char* tool, bool local = false)
{
    namespace fs = std::filesystem;
    const auto source = directory / (local ? "native-local-source" : "native-source");
    const auto output = directory / (local ? "native-local-layered" : "native-layered");
    CHECK(fs::create_directory(source));
    constexpr int count = 4096, dimension = 8;
    auto bytes = ByteArray::Alloc(sizeof(float) * count * dimension);
    auto* data = reinterpret_cast<float*>(bytes.Data());
    std::vector<std::uint32_t> tags(count);
    for (int row = 0; row < count; ++row) {
        tags[row] = row % 10 == 0 ? 1 : row % 10 == 1 ? 2 : 0;
        for (int col = 0; col < dimension; ++col)
            data[row * dimension + col] = float((row * 7 + col * 31) % 4093);
    }
    auto vectors = std::make_shared<BasicVectorSet>(bytes, VectorValueType::Float, dimension, count);
    auto index = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
    const auto set = [&](const char* section, const char* key, const std::string& value) {
        CHECK(index->SetParameter(key, value, section) == ErrorCode::Success);
    };
    set("Base", "DistCalcMethod", "L2"); set("Base", "IndexAlgoType", "BKT");
    set("Base", "IndexDirectory", source.string());
    for (const auto* section : {"SelectHead", "BuildHead", "BuildSSDIndex"}) {
        set(section, "isExecute", "true"); set(section, "NumberOfThreads", "1");
    }
    set("SelectHead", "SelectHeadType", "Random"); set("SelectHead", "Ratio", "0.25");
    set("SelectHead", "HierarchyEnabled", "true"); set("SelectHead", "HierarchyLevels", "5");
    set("SelectHead", "HierarchyReplicaCount", "2"); set("SelectHead", "BKTLambdaFactor", "1");
    set("BuildHead", "NeighborhoodSize", "16"); set("BuildHead", "RefineIterations", "1");
    set("BuildSSDIndex", "BuildSsdIndex", "true"); set("BuildSSDIndex", "Storage", "STATIC");
    set("BuildSSDIndex", "EnableLimitedTagPosting", "true");
    set("BuildSSDIndex", "LimitedTagSlotsPerHead", "2"); set("BuildSSDIndex", "LimitedTagMinHeadCount", "1");
    set("BuildSSDIndex", "EnableLimitedTagSupportExpansion", "true");
    set("BuildSSDIndex", "NumTagsPerVec", "1"); set("BuildSSDIndex", "StaticACLTagCols", "1");
    set("BuildSSDIndex", "ExcludeHead", "true"); set("BuildSSDIndex", "CrossEdges", "0");
    set("BuildSSDIndex", "ReplicaCount", "2"); set("BuildSSDIndex", "PostingPageLimit", "8");
    set("BuildSSDIndex", "InternalResultNum", "32");
    set("SearchSSDIndex", "InternalResultNum", "16"); set("SearchSSDIndex", "MaxCheck", "1");
    set("SearchSSDIndex", "EnablePostingNavigation", "true");
    set("SearchSSDIndex", "PostingAnchorCount", "0"); set("SearchSSDIndex", "PostingAdditionalMaxCheck", "128");
    auto* spann = dynamic_cast<SPANN::Index<float>*>(index.get());
    CHECK(spann);
    spann->SetVectorTags(tags.data(), count, 1);
    CHECK(index->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::Success);
    CHECK(index->SaveIndex(source.string()) == ErrorCode::Success);
    index.reset();
    const auto snapshot = [&]() {
        std::map<std::string, std::uint64_t> files;
        for (const auto& entry : fs::recursive_directory_iterator(source)) {
            if (!entry.is_regular_file()) continue;
            std::ifstream in(entry.path(), std::ios::binary);
            std::uint64_t hash = SPANN::SecondLevelHeadPostings::BeginIDFingerprint();
            std::array<char, 4096> block;
            while (in.read(block.data(), block.size()) || in.gcount())
                hash = SPANN::SecondLevelHeadPostings::AddContentFingerprint(hash, block.data(), in.gcount());
            CHECK(in.eof());
            files.emplace(fs::relative(entry.path(), source).string(), hash);
        }
        return files;
    };
    const auto before = snapshot();
    const auto config = directory / "rebuild.ini";
    {
        std::ofstream out(config);
        out << "[RebuildHierarchy]\nSourceIndex=" << source.string() << "\nOutputIndex=" << output.string()
            << "\n[SelectHead]\n"
            << (local ? "HierarchyLocalTarget=2048\nHierarchyLocalWindow=16\n" :
                "HierarchyLabelSelectivity=0.5,0.4,0.3,0.2\n")
            << "NumberOfThreads=1\n";
        CHECK(bool(out));
    }
    const auto invoke = [&]() {
        const pid_t pid = fork();
        CHECK(pid >= 0);
        if (pid == 0) {
            execl(tool, tool, "-c", config.c_str(), static_cast<char*>(nullptr));
            _exit(127);
        }
        int status = 0;
        CHECK(waitpid(pid, &status, 0) == pid && WIFEXITED(status));
        return WEXITSTATUS(status);
    };
    CHECK(invoke() == 0 && snapshot() == before);
    CHECK(fs::is_symlink(output / "HeadIndex/graph.bin"));
    CHECK(!fs::exists(output / "SPTAGSecondLevelHeadVectors.bin"));
    CHECK(invoke() != 0 && snapshot() == before);
    CHECK(VectorIndex::LoadIndex(output.string(), index) == ErrorCode::Success);
    if (local) {
        CHECK(index->SetParameter("HierarchyLocalTarget", "2049", "SelectHead") != ErrorCode::Success);
        CHECK(index->SetParameter("HierarchyLocalWindow", "32", "SearchSSDIndex") != ErrorCode::Success);
    }
    std::uint64_t activations = 0;
    for (int graphBudget : {1, count}) {
          set("SearchSSDIndex", "MaxCheck", std::to_string(graphBudget));
          for (const auto& queryTags : std::vector<std::vector<std::uint32_t>>{{1},{2},{1,2,1},{0},{}}) {
              for (int row : {0,1,20,21}) {
                  VectorIndex::ThreadLocalSearchContext context;
                  context.m_queryTags = queryTags;
                  context.m_limitedTagMembershipEligible = !queryTags.empty();
                  context.m_limitedTagQueryValues = queryTags;
                  VectorIndex::ThreadLocalSearchContextGuard guard(std::move(context));
                  COMMON::QueryResultSet<float> results(data + row * dimension, 10);
                  COMMON::GraphAccessStats stats;
                  {
                      COMMON::ScopedGraphAccessStats capture(&stats);
                      CHECK(index->SearchIndex(results) == ErrorCode::Success);
                  }
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
                  CHECK(stats.m_postingActivations <= 1);
                  if (!queryTags.empty()) CHECK(stats.m_graphLeaves <= graphBudget);
                  if (queryTags.empty() || (!local && queryTags.front() == 0)) CHECK(stats.m_postingActivations == 0);
                  CHECK(stats.m_preservedHeads == stats.m_headBefore);
                  if (graphBudget == 1) activations += stats.m_postingActivations;
#endif
                  // A one-leaf filtered search may exhaust its reachable posting frontier with no match.
                  if (graphBudget == count || queryTags.empty()) CHECK(results.GetResult(0)->VID >= 0);
                  std::set<int> found;
                  bool missing = false;
                  for (int rank = 0; rank < results.GetResultNum(); ++rank) {
                      const auto* result = results.GetResult(rank);
                      if (result->VID < 0) {
                          CHECK(result->VID == -1 && result->Dist == MaxDist);
                          missing = true;
                          continue;
                      }
                      CHECK(!missing);
                      CHECK(result->VID < count && found.insert(result->VID).second);
                      CHECK(queryTags.empty() || std::find(queryTags.begin(), queryTags.end(), tags[result->VID]) != queryTags.end());
                      double exact = 0;
                      for (int col = 0; col < dimension; ++col) {
                          const double delta = data[row * dimension + col] - data[result->VID * dimension + col];
                          exact += delta * delta;
                      }
                      CHECK(std::abs(exact - result->Dist) <= std::max(1.0, exact * 1e-6));
                  }
              }
          }
    }
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    CHECK(local ? activations > 0 : activations == 12);
#else
    (void)activations;
#endif
    CHECK(index->SaveIndex((directory / "forbidden-export").string()) != ErrorCode::Success);
    index.reset();
    CHECK(snapshot() == before);
    std::cout << "PASS native CLI rebuild/reload, unchanged H1/SSD, single/OR search, protected H1 and read-only snapshots\n";
}
int main(int argc, char** argv)
{
    const auto directory = std::filesystem::current_path() / ("layered-label-test-" + std::to_string(getpid()));
    try {
        CHECK(std::filesystem::create_directory(directory));
        CHECK(argc == 2);
        SelectionTests(); CensusTests(); PersistenceTests(directory); QueryTests();
        ConstructionTests<float>(directory); ConstructionTests<std::uint8_t>(directory);
        NativeRebuildTest(directory, argv[1]);
        NativeRebuildTest(directory, argv[1], true);
        std::filesystem::remove_all(directory);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\nFixture retained at " << directory << '\n';
        return 1;
    }
}
