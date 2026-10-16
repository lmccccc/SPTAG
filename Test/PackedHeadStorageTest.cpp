// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#include "inc/Core/BKT/Index.h"
#include "inc/Core/SPANN/HeadNodeMetadata.h"
#include "inc/Core/SPANN/CanonicalHierarchyVectors.h"
#include "inc/Core/SPANN/RoutingSignatures.h"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>

using namespace SPTAG;
namespace fs = std::filesystem;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Packed storage line " + std::to_string(__LINE__)); } while (0)

template <typename Action> void Reject(Action action)
{
    bool rejected = false;
    try { action(); } catch (const std::logic_error&) { rejected = true; }
    CHECK(rejected);
}

std::vector<char> Read(const fs::path& path)
{
    std::ifstream input(path, std::ios::binary);
    CHECK(input);
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

void CheckSharedRows()
{
    Reject([] { COMMON::SharedAlignedBuffer buffer(0); });
    Reject([] { COMMON::SharedAlignedBuffer buffer((std::numeric_limits<std::size_t>::max)()); });
    std::weak_ptr<COMMON::SharedAlignedBuffer> lifetime;
    {
        COMMON::Dataset<float> vectors;
        {
            auto buffer = std::make_shared<COMMON::SharedAlignedBuffer>(256);
            lifetime = buffer;
            CHECK(reinterpret_cast<std::uintptr_t>(buffer->Data()) % 64 == 0);
            COMMON::Dataset<SizeType> edges, invalid;
            CHECK(invalid.AttachSharedRows(buffer, 2, 4, 127, 64) != ErrorCode::Success);
            CHECK(invalid.AttachSharedRows(buffer, 2, 4, 128, 65) != ErrorCode::Success);
            CHECK(invalid.AttachSharedRows(buffer, 3, 4, 128, 64) != ErrorCode::Success);
            CHECK(invalid.AttachSharedRows(buffer, 2, 32, 128, 64) != ErrorCode::Success);
            CHECK(vectors.AttachSharedRows(buffer, 2, 4, 128, 64) == ErrorCode::Success);
            CHECK(edges.AttachSharedRows(buffer, 2, 8, 128, 96) == ErrorCode::Success);
            CHECK(vectors.SharedStorage() == edges.SharedStorage());
            CHECK(reinterpret_cast<const char*>(edges[1]) - reinterpret_cast<const char*>(vectors[1]) == 32);
            vectors[1][0] = 17.0f;
            CHECK(vectors.AddBatch(1) != ErrorCode::Success);
            CHECK(vectors.RepackColumns(8) != ErrorCode::Success);
            CHECK(vectors.Load(static_cast<char*>(nullptr), 16, 16) != ErrorCode::Success);
            Reject([&] { vectors.SetR(1); });
            Reject([&] { vectors.Initialize(2, 4, 16, 16); });
            buffer.reset();
        }
        CHECK(!lifetime.expired() && vectors[1][0] == 17.0f);
        CHECK(vectors.C() == 4 && vectors.RowStride() == 128 && vectors.RowOffset() == 64);
    }
    CHECK(lifetime.expired());
    const auto layout = PackedHeadLayout::Create(
        HeadMetadataLayout::Compact(TagSchema::Parse("categorical"), {}, true), 0, 2, 13, 32);
    PackedHeadStorage packed(layout, 2);
    COMMON::Dataset<std::uint8_t> bytes;
    CHECK(bytes.AttachSharedRows(packed.Buffer(), 2, 13, layout.stride, layout.vector) == ErrorCode::Success);
    CHECK(bytes.C() == 13 && bytes[1] == packed.Row(1) + layout.vector);
    for (int column = 0; column < bytes.C(); ++column) bytes[1][column] = column + 1;
    CHECK(bytes[1][12] == 13 && packed.Row(1)[layout.vector + 13] == 0);
}

struct AllRows : COMMON::PostingNavigation {
    std::vector<std::uint32_t> members;
    int calls = 0;
    explicit AllRows(SizeType count) {
        for (SizeType id = 0; id < count; ++id) members.push_back(id);
        members.push_back(0);
    }
    void Expand(const std::vector<int>&, const Consumer& consume) override {
        ++calls;
        consume(members.data(), static_cast<int>(members.size()));
    }
};

struct Trace {
    std::vector<std::pair<SizeType, float>> results;
    std::vector<SizeType> predicates;
    std::array<std::uint64_t, 4> work{};
    int expansions = 0;
    bool operator==(const Trace& other) const {
        return results == other.results && predicates == other.predicates &&
            work == other.work && expansions == other.expansions;
    }
};

template <typename T>
Trace Search(BKT::Index<T>& index, const T* query, const SPANN::LimitedTagSupport& support,
             const SPANN::RoutingSignatures& routing, const Cache::DNFPredicate& dnf,
             const TagSchema& schema, int mode)
{
    Trace trace;
    SPANN::RoutingPredicate predicate(&dnf, nullptr, 0, schema.numeric, routing.params);
    const auto prefetch = predicate.HeadPrefetch(index.GetPackedHeadStorage(), true, true);
    const std::function<bool(SizeType)> match = [&](SizeType id) {
        trace.predicates.push_back(id);
        return support.Supports(id, 1) && routing.HeadMayMatch(predicate, id, true);
    };
    COMMON::QueryResultSet<T> result(query, 8);
    COMMON::GraphAccessStats work;
    AllRows posting(index.GetNumSamples());
    {
        COMMON::ScopedGraphAccessStats scope(&work);
        if (mode == 0) CHECK(index.SearchIndexWithMaxCheck(result, 16) == ErrorCode::Success);
        else if (mode == 1) CHECK(index.SearchIndexWithResultFilter(result, match, 16) == ErrorCode::Success);
        else CHECK(index.SearchIndexWithPostingNavigation(result, match, &posting, 16, false,
            8, 32, 100, 4, mode == 2, &prefetch) == ErrorCode::Success);
    }
    for (int i = 0; i < result.GetResultNum(); ++i)
        trace.results.emplace_back(result.GetResult(i)->VID, result.GetResult(i)->Dist);
    trace.work = {work.m_distanceCalls, work.m_graphRows, work.m_visitedChecks, work.m_treeNodeVisits};
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    CHECK(trace.work[0] > 0);
#endif
    trace.expansions = posting.calls;
    return trace;
}

template <typename T>
void CheckIndex(const fs::path& folder, const TagSchema& schema, DimensionType dimension, bool expanded)
{
    constexpr SizeType count = 128;
    const int key = schema.categorical.front();
    auto index = std::make_shared<BKT::Index<T>>();
    for (auto setting : std::vector<std::pair<const char*, const char*>>{
            {"DistCalcMethod","L2"}, {"NumberOfThreads","1"}, {"BKTKmeansK","4"},
            {"BKTLeafSize","4"}, {"NeighborhoodSize","32"}, {"CEF","64"}, {"TPTNumber","1"},
            {"NumberOfInitialDynamicPivots","8"}, {"NumberOfOtherDynamicPivots","1"}})
        CHECK(index->SetParameter(setting.first, setting.second) == ErrorCode::Success);
    std::vector<T> data(count * dimension);
    for (std::size_t i = 0; i < data.size(); ++i)
        data[i] = static_cast<T>((i * 17 + i / dimension * 13) % 251);
    CHECK(index->BuildIndex(data.data(), count, dimension, true, false) == ErrorCode::Success);
    Cache::HierWidthTable widths;
    index->InitializeCompactHeadNodeMeta(count, schema, widths, true);
    SPANN::LimitedTagSupport support;
    CHECK(support.Initialize(count, 2, 1, key, schema.Width(), 77));
    std::unordered_map<std::uint32_t, std::uint64_t> counts;
    std::unordered_map<std::uint32_t, std::uint32_t> required;
    for (std::uint32_t tag = 0; tag < 7; ++tag) { counts[tag] = 100; required[tag] = 1; }
    CHECK(support.SetTagVectorCounts(700, counts));
    for (SizeType head = 0; head < count; ++head) {
        Cache::HierarchicalOwnTags own;
        for (int column = 0; column < schema.Width(); ++column)
            own.tag[column] = head == count - 1 ? 0 :
                schema.IsCategorical(column) ? (head + column) % 7 : (head * 3 + column) % 256;
        CHECK(support.SetHeadAttributes(head, own.tag, schema.Width()));
        if (head != count - 1)
            CHECK(support.SetHeadTags(head, {own.tag[key], (own.tag[key] + 1) % 7}));
        index->SetHeadNodeHierMask(head, own);
        index->SetHeadNodeGlobalVID(head, head + 1000);
        index->SetHeadNodeBundleNodeId(head, head % 3 - 1);
        index->SetHeadNodeHeadOnly(head, head % 2 != 0);
        Cache::PostingBitmask h, o;
        h.Insert((head + 3) % 7); o.Insert(999);
        index->SetHeadNodePS(head, h);
        index->SetHeadNodeTailPS(head, o);
        Cache::HierarchicalPostingMask categories;
        for (int column : schema.categorical) categories.Insert(column, (head + column + 1) % 7, widths);
        index->SetHeadNodePostingHierMask(head, categories);
        for (std::size_t lane = 0; lane < schema.numeric.size(); ++lane) {
            Cache::NumQuantInsert(index->GetHeadNodeNumQuantMutable(head), lane, (head + lane) % 256);
            Cache::NumQuantInsert(index->GetHeadNodeTailNumQuantMutable(head), lane, (head + lane + 71) % 256);
        }
    }
    if (expanded) CHECK(support.ConfigureExpansion(std::vector<std::uint64_t>(count + 1), {}, required));
    CHECK(support.Finalize());
    index->SetHeadNodeNumericDomainFingerprint(schema.numeric.empty() ? 0 : 1234);
    SPANN::RoutingSignatures routing;
    CHECK(routing.Build(*index, support, schema.categorical, schema.numeric, {},
                        std::vector<Cache::NumQuantParam>(schema.numeric.size(), {0, 255})));
    CHECK(routing.Current(index.get()));
    const auto originalSupport = support;
    const auto originalMetadata = static_cast<const VectorIndex&>(*index).GetHeadNodeMetaBlob();
    const auto revision = index->GetHeadNodeMetaRevision();
    const auto generation = support.GenerationFingerprint(), fingerprint = support.ContentFingerprint();
    std::vector<SizeType> graph;
    for (SizeType head = 0; head < count; ++head)
        graph.insert(graph.end(), index->GetGraph()[head], index->GetGraph()[head] + index->GetNeighborhoodSize());
    SPANN::CanonicalHierarchyVectorView upper(index, {3, 17, 65});
    Cache::DNFPredicate categorical;
    categorical.clauses.push_back({{{static_cast<std::uint32_t>(key), 1, Cache::DNF_EQ, 0}}});
    auto mixed = categorical;
    if (!schema.numeric.empty()) {
        mixed.clauses[0].lits.push_back({static_cast<std::uint32_t>(schema.numeric.front()), 80, Cache::DNF_GE, 1});
        mixed.clauses.push_back({{{static_cast<std::uint32_t>(key), 6, Cache::DNF_EQ, 0}}});
    }
    std::vector<Trace> baseline;
    for (const auto& predicate : {categorical, mixed})
        for (int mode = 0; mode < 4; ++mode)
            baseline.push_back(Search(*index, data.data() + 17 * dimension, support, routing, predicate, schema, mode));
    CHECK(SPANN::SaveHeadNodeMetadata((folder / "before.meta").string(), index, 77));
    CHECK(support.Save((folder / "before.support").string()));
    CHECK(index->SaveIndex((folder / "before").string()) == ErrorCode::Success);

    const auto layout = PackedHeadLayout::Create(index->GetHeadMetadataLayout(), key, 2,
                                                 dimension * sizeof(T), index->GetNeighborhoodSize());
    CHECK(layout.vector % 64 == 0 && layout.edges % 64 == 0 && layout.stride % 64 == 0);
    auto invalid = layout.serialized;
    invalid.own += 8;
    Reject([&] { PackedHeadLayout::Create(invalid, key, 2, dimension * sizeof(T), 32); });
    Reject([&] { PackedHeadLayout::Create(layout.serialized, key, 0, dimension * sizeof(T), 32); });
    bool overflow = false;
    try { PackedHeadLayout::Create(layout.serialized, key, 2, (std::numeric_limits<std::size_t>::max)(), 32); }
    catch (const std::overflow_error&) { overflow = true; }
    CHECK(overflow);
    if (!expanded) {
        auto mismatch = support;
        auto own = index->GetHeadNodeHierMask(0);
        std::vector<std::uint32_t> changed(own.tag, own.tag + own.count);
        changed[key] = (changed[key] + 2) % 7;
        CHECK(mismatch.SetHeadAttributes(0, changed.data(), changed.size()));
        CHECK(mismatch.SetHeadTags(0, {changed[key]}));
        CHECK(mismatch.Finalize());
        auto rejected = std::make_shared<PackedHeadStorage>(layout, count);
        const void* prior = index->GetSample(0);
        Reject([&] { index->PackHeadRows(rejected, [&] { mismatch.AttachPackedStorage(rejected); }); });
        CHECK(index->GetSample(0) == prior && !index->GetPackedHeadStorage() && !mismatch.PackedStorage());
        if (schema.Width() > 1) {
            mismatch = support;
            changed.assign(own.tag, own.tag + own.count);
            ++changed[(key + 1) % schema.Width()];
            CHECK(mismatch.SetHeadAttributes(0, changed.data(), changed.size()) && mismatch.Finalize());
            Reject([&] { index->PackHeadRows(rejected, [&] { mismatch.AttachPackedStorage(rejected); }); });
            CHECK(index->GetSample(0) == prior && !index->GetPackedHeadStorage() && !mismatch.PackedStorage());
        }
    }
    auto storage = std::make_shared<PackedHeadStorage>(layout, count);
    CHECK(index->PackHeadRows(storage, [&] { support.AttachPackedStorage(storage); }) == ErrorCode::Success);
    CHECK(index->m_headNodeMeta.capacity() == 0 && support.OwnedHeadPayloadBytes() == 0);
    CHECK(routing.Current(index.get()) && index->GetHeadNodeMetaRevision() == revision);
    CHECK(support.Validate() && support.GenerationFingerprint() == generation && support.ContentFingerprint() == fingerprint);
    CHECK(!support.IsActiveHead(count - 1) && support.TagAt(count - 1, 0) == SPANN::LimitedTagSupport::EmptyTag);
    std::vector<std::uint8_t> metadata(layout.serialized.stride);
    for (SizeType head = 0; head < count; ++head) {
        const auto* row = storage->Row(head);
        CHECK(reinterpret_cast<std::uintptr_t>(row) % 64 == 0);
        CHECK(index->GetSample(head) == row + layout.vector);
        CHECK(reinterpret_cast<const std::uint8_t*>(index->GetGraph()[head]) == row + layout.edges);
        CHECK(support.HeadAttributes(head) == index->GetHeadNodeHierMask(head).tag);
        CHECK(support.HeadTags(head) == originalSupport.HeadTags(head));
        for (std::uint32_t tag : {0U, 1U, 6U, 999U, UINT32_MAX})
            CHECK(support.Supports(head, tag) == originalSupport.Supports(head, tag));
        CHECK(std::memcmp(index->GetSample(head), data.data() + head * dimension, dimension * sizeof(T)) == 0);
        CHECK(std::memcmp(index->GetGraph()[head], graph.data() + head * layout.degree, layout.degree * sizeof(SizeType)) == 0);
        index->CopyHeadNodeMetadataRow(head, metadata.data(), metadata.size());
        CHECK(std::memcmp(metadata.data(), originalMetadata.data() + head * metadata.size(), metadata.size()) == 0);
    }
    CHECK(upper.GetVector(1) == index->GetSample(17));
    std::size_t trace = 0;
    for (const auto& predicate : {categorical, mixed})
        for (int mode = 0; mode < 4; ++mode)
            CHECK(baseline[trace++] == Search(*index, data.data() + 17 * dimension, support, routing, predicate, schema, mode));
    CHECK(SPANN::SaveHeadNodeMetadata((folder / "after.meta").string(), index, 77));
    CHECK(support.Save((folder / "after.support").string()));
    CHECK(Read(folder / "before.meta") == Read(folder / "after.meta"));
    CHECK(Read(folder / "before.support") == Read(folder / "after.support"));
    CHECK(index->SaveIndex((folder / "after").string()) == ErrorCode::Success);
    const auto indexFiles = index->GetIndexFiles();
    for (const auto& file : *indexFiles) CHECK(Read(folder / "before" / file) == Read(folder / "after" / file));
    auto restored = VectorIndex::CreateInstance(IndexAlgoType::BKT, GetEnumValueType<T>());
    CHECK(SPANN::LoadHeadNodeMetadata((folder / "after.meta").string(), restored, count, 77,
                                    [](SizeType head) { return head + 1000; }, &schema));
    CHECK(restored->GetHeadNodeMetaBlob() == originalMetadata);
    SPANN::LimitedTagSupport restoredSupport;
    CHECK(restoredSupport.Load((folder / "after.support").string(), count, 2, 1, key, schema.Width(), 77));
    CHECK(restoredSupport.ContentFingerprint() == fingerprint);
    Reject([&] { index->ClearHeadNodeMeta(); });
    Reject([&] { index->SetHeadNodeGlobalVID(0, 5); });
    Reject([&] { index->GetHeadNodeNumQuantMutable(0); });
    Reject([&] { index->GetHeadNodeMetaBlob(); });
    Reject([&] { index->GetMutableGraph(); });
    Reject([&] { support.Reset(); });
    Reject([&] { support.TombstoneRuntimeHead(0); });
    Reject([&] { support.PrepareRuntimeTombstoneHead(); });
    Reject([&] { support.Finalize(); });
    Reject([&] { support.HeadTagData(0); });
    CHECK(index->GetHeadNodeMetaRevision() == revision);
    CHECK(index->BuildIndex(data.data(), count, dimension) != ErrorCode::Success);
    CHECK(index->AddIndex(data.data(), 1, dimension, nullptr) != ErrorCode::Success);
    CHECK(index->AddIndexIdx(0, 1) != ErrorCode::Success);
    CHECK(index->DeleteIndex(SizeType{0}) != ErrorCode::Success);
    CHECK(index->SetParameter("NeighborhoodSize", "64") != ErrorCode::Success);
    CHECK(index->SetParameter("MaxCheck", "64") == ErrorCode::Success);
}

int main()
{
    const auto folder = fs::current_path() / ("packed_head_fixture_" + std::to_string(
        std::chrono::steady_clock::now().time_since_epoch().count()));
    try {
        CHECK(fs::create_directory(folder));
        CheckSharedRows();
        const auto yfcc = TagSchema::Parse("numeric,numeric,categorical,categorical");
        const auto layout = PackedHeadLayout::Create(HeadMetadataLayout::Compact(yfcc, {}, true), 2, 2, 192, 32);
        CHECK(layout.vector == 320 && layout.edges == 512 && layout.stride == 640);
        CHECK(layout.metadata.own == 32 && layout.additionalSupport == 48 && layout.ownSupportPresent == 60);
        PackedHeadStorage node(layout, 1);
        Cache::DNFPredicate dnf;
        dnf.clauses.push_back({{{2, 1, Cache::DNF_EQ, 0}}});
        SPANN::RoutingPredicate predicate(&dnf, nullptr, 0, {}, {});
        CHECK(predicate.HeadPrefetch(&node, true, true).Lines() == std::vector<std::size_t>{0});
        CHECK(predicate.HeadPrefetch(&node, false, false).Lines() == std::vector<std::size_t>{64});
        CheckIndex<std::uint8_t>(folder, yfcc, 192, true);
        CheckIndex<float>(folder, TagSchema::Parse("categorical,numeric,categorical"), 7, false);
        CheckIndex<std::uint8_t>(folder, TagSchema::Parse("categorical"), 16, false);
        fs::remove_all(folder);
        std::cout << "PASS packed ownership, layout, shared fields, authentication, query traces and immutable exports\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\nFixture retained: " << folder << '\n';
        return 1;
    }
}
