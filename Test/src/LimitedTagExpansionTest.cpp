// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Core/SPANN/LimitedTagSupportExpansion.h"
#include "inc/Core/Common/BKTree.h"
#include "inc/Core/Common/QueryResultSet.h"
#include "inc/Core/Common/RelativeNeighborhoodGraph.h"
#include "inc/Core/VectorIndex.h"
#include "inc/Test.h"

#include <array>
#include <filesystem>
#include <fstream>
#include <cstdlib>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

using namespace SPTAG;

namespace
{
SPANN::LimitedTagSupport ExpansionBase(int floor = 3)
{
    SPANN::LimitedTagSupport support;
    BOOST_REQUIRE(support.Initialize(4, 2, floor, 0, 1, 12345));
    for (SizeType head = 0; head < 4; ++head)
    {
        const std::uint32_t own = head < 2 ? 7 : 8;
        BOOST_REQUIRE(support.SetHeadTags(head, {own, own == 7 ? 8U : 7U}));
        BOOST_REQUIRE(support.SetHeadAttributes(head, &own, 1));
    }
    BOOST_REQUIRE(support.SetTagVectorCounts(22, {{7, 10}, {8, 10}, {9, 2}}));
    return support;
}
}

BOOST_AUTO_TEST_SUITE(LimitedTagExpansionTest)

BOOST_AUTO_TEST_CASE(BaseSupportUsesOnlyRetainedOAndClosestDistinctTags)
{
    struct Edge { SizeType node; SizeType tonode; float distance; };
    const std::vector<std::uint32_t> tags{10, 20, 30, 40, 50, 60, 30};
    // Tag 20's pre-RNG result is absent from O. Tag 60 was cut, despite
    // its smaller distance. Tag 40 represents a retained rank > 2 candidate.
    const std::vector<Edge> original{
        {0, 2, 9}, {0, 3, 3}, {0, 4, 4}, {0, 6, 1}, {0, 0, 0},
        {0, 5, 0.1f}, {1, 3, 7}, {1, 3, 2}, {MaxSize, 0, 0}};
    const std::vector<int> retained{5, 2, 0};
    const std::vector<std::uint32_t> own{10, 20, 50};
    const auto tagAt = [&](SizeType vid) { return tags[static_cast<size_t>(vid)]; };
    for (int slots : {1, 2, 3, 8})
    {
        SPANN::LimitedTagSupport support;
        BOOST_REQUIRE(support.Initialize(3, slots, 1, 0, 1, 12345));
        std::string error;
        BOOST_REQUIRE_MESSAGE(SPANN::SelectRetainedOriginalBaseTags(
            support, own, original, retained, static_cast<SizeType>(tags.size()), tagAt, &error), error);
        BOOST_CHECK_EQUAL(support.OwnTag(0), 10U);
        BOOST_CHECK_EQUAL(support.OwnTag(1), 20U);
        BOOST_CHECK_EQUAL(support.OwnTag(2), 50U);
        BOOST_CHECK(!support.Supports(0, 20));
        BOOST_CHECK(!support.Supports(0, 60));
        BOOST_CHECK_EQUAL(support.Supports(0, 30), slots >= 2);
        BOOST_CHECK_EQUAL(support.Supports(0, 40), slots >= 3);
        BOOST_CHECK_EQUAL(support.Supports(0, 50), slots >= 4);
        BOOST_CHECK_EQUAL(support.Supports(1, 40), slots >= 2);
        for (int slot = 1; slot < slots; ++slot)
            BOOST_CHECK_EQUAL(support.TagAt(2, slot), SPANN::LimitedTagSupport::EmptyTag);
        auto reversed = original;
        std::reverse(reversed.begin(), reversed.begin() + retained[0]);
        SPANN::LimitedTagSupport reordered;
        BOOST_REQUIRE(reordered.Initialize(3, slots, 1, 0, 1, 12345));
        BOOST_REQUIRE(SPANN::SelectRetainedOriginalBaseTags(
            reordered, own, reversed, retained, static_cast<SizeType>(tags.size()), tagAt));
        for (SizeType head = 0; head < 3; ++head)
            BOOST_CHECK(reordered.HeadTags(head) == support.HeadTags(head));
    }
    BOOST_CHECK_EQUAL(original[5].tonode, 5);
    BOOST_CHECK_EQUAL(retained[0], 5);
}

BOOST_AUTO_TEST_CASE(BaseSupportMatchesUnboundedAggregationWithoutDistanceOrdering)
{
    struct Edge { SizeType node; SizeType tonode; float distance; };
    std::vector<Edge> original;
    std::unordered_map<std::uint32_t, float> closest;
    for (SizeType vid = 0; vid < 1000; ++vid)
    {
        const auto tag = static_cast<std::uint32_t>(vid % 37);
        const float distance = static_cast<float>((vid * 71) % 29);
        original.push_back({0, vid, distance});
        auto result = closest.emplace(tag, distance);
        result.first->second = (std::min)(result.first->second, distance);
    }
    std::vector<std::pair<float, std::uint32_t>> ranked;
    for (const auto& entry : closest)
        if (entry.first != 0) ranked.emplace_back(entry.second, entry.first);
    std::sort(ranked.begin(), ranked.end());
    for (int slots : {1, 2, 4, 40})
    {
        SPANN::LimitedTagSupport support;
        BOOST_REQUIRE(support.Initialize(1, slots, 1, 0, 1, 12345));
        BOOST_REQUIRE(SPANN::SelectRetainedOriginalBaseTags(support, {0}, original,
            {1000}, 1000, [](SizeType vid) { return static_cast<std::uint32_t>(vid % 37); }));
        for (int slot = 1; slot < slots; ++slot)
            BOOST_CHECK_EQUAL(support.TagAt(0, slot), static_cast<size_t>(slot) <= ranked.size()
                ? ranked[slot - 1].second : SPANN::LimitedTagSupport::EmptyTag);
    }
}

BOOST_AUTO_TEST_CASE(BKTCentersUseUpstreamGlobalRandomStream)
{
    constexpr SizeType count = 32;
    constexpr int centers = 4;
    COMMON::Dataset<float> data(count, 1, count, count);
    std::vector<SizeType> indices(count);
    for (SizeType row = 0; row < count; ++row) {
        data[row][0] = static_cast<float>(row);
        indices[row] = row;
    }
    const std::shared_ptr<COMMON::IQuantizer> quantizer;
    for (unsigned ambient : {123U, 987U}) {
        std::srand(ambient);
        std::array<float, centers> expected;
        for (auto& center : expected)
            center = data[COMMON::Utils::rand(count)][0];
        const int next = std::rand();
        // Isolated single-threaded InitCenters has no clock reseeding.
        std::srand(ambient);
        COMMON::KmeansArgs<float> args(centers, 1, count, 1, DistCalcMethod::L2, quantizer);
        COMMON::InitCenters<float, float>(data, indices, 0, count, args, count, 1);
        BOOST_CHECK_EQUAL_COLLECTIONS(args.centers, args.centers + centers,
            expected.begin(), expected.end());
        BOOST_CHECK_EQUAL(std::rand(), next);
    }
}

BOOST_AUTO_TEST_CASE(UpstreamRandomHeadGeometryRoundTrips)
{
    constexpr SizeType count = 2048;
    constexpr DimensionType dimension = 8;
    ByteArray bytes = ByteArray::Alloc(static_cast<size_t>(count) * dimension * sizeof(float));
    auto* values = reinterpret_cast<float*>(bytes.Data());
    for (SizeType row = 0; row < count; ++row)
        for (DimensionType column = 0; column < dimension; ++column)
            values[static_cast<size_t>(row) * dimension + column] =
                static_cast<float>((row * 37 + column * 101) % 2053) / 2053.0f;
    auto vectors = std::make_shared<BasicVectorSet>(
        bytes, VectorValueType::Float, dimension, count);
    const std::filesystem::path directory = "limited_tag_upstream_head_geometry";
    struct Cleanup
    {
        std::filesystem::path path;
        ~Cleanup() { std::error_code error; std::filesystem::remove_all(path, error); }
    } cleanup{directory};
    for (auto algorithm : {IndexAlgoType::BKT, IndexAlgoType::KDT})
    for (const char* threads : {"1", "2"})
    {
        auto index = VectorIndex::CreateInstance(algorithm, VectorValueType::Float);
        BOOST_CHECK(index->GetParameter("BKTSeed").empty());
        BOOST_CHECK(index->GetParameter("TPTSeed").empty());
        BOOST_CHECK(index->SetParameter("BKTSeed", "0") == ErrorCode::FailedParseValue);
        BOOST_CHECK(index->SetParameter("TPTSeed", "7") == ErrorCode::FailedParseValue);
        for (const auto& option : std::vector<std::pair<const char*, const char*>>{
                 {"DistCalcMethod", "L2"}, {"NumberOfThreads", threads},
                 {"Samples", "128"}, {"TPTNumber", "2"}, {"TPTLeafSize", "64"},
                 {"NumTopDimensionTpTreeSplit", "4"}, {"NeighborhoodSize", "16"},
                 {"RefineIterations", "1"}, {"MaxCheckForRefineGraph", "128"}})
            BOOST_REQUIRE(index->SetParameter(option.first, option.second) == ErrorCode::Success);
        if (algorithm == IndexAlgoType::BKT) {
            for (const auto& option : std::vector<std::pair<const char*, const char*>>{
                     {"BKTNumber", "1"}, {"BKTKmeansK", "8"},
                     {"BKTLeafSize", "8"}, {"BKTLambdaFactor", "1"}})
                BOOST_REQUIRE(index->SetParameter(option.first, option.second) == ErrorCode::Success);
        } else BOOST_REQUIRE(index->SetParameter("KDTNumber", "1") == ErrorCode::Success);
        BOOST_REQUIRE(index->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::Success);
        std::filesystem::create_directories(directory);
        BOOST_REQUIRE(index->SaveIndex(directory.string()) == ErrorCode::Success);
        std::array<std::vector<char>, 2> expected;
        size_t artifact = 0;
        for (const char* name : {"tree.bin", "graph.bin"})
        {
            std::ifstream input(directory / name, std::ios::binary);
            BOOST_REQUIRE(input.good());
            std::vector<char> content((std::istreambuf_iterator<char>(input)),
                                     std::istreambuf_iterator<char>());
            BOOST_REQUIRE(!content.empty());
            expected[artifact] = std::move(content);
            ++artifact;
        }
        std::shared_ptr<VectorIndex> loaded;
        BOOST_REQUIRE(VectorIndex::LoadIndex(directory.string(), loaded) == ErrorCode::Success);
        BOOST_CHECK(loaded->GetParameter("BKTSeed").empty());
        BOOST_CHECK(loaded->GetParameter("TPTSeed").empty());
        BOOST_CHECK_EQUAL(loaded->GetNumSamples(), count);
#if !defined(GPU)
        if (algorithm == IndexAlgoType::BKT && std::string(threads) == "1") {
            COMMON::RelativeNeighborhoodGraph graph;
            graph.m_iTPTLeafSize = 64;
            graph.m_numTopDimensionTPTSplit = 4;
            for (unsigned ambient : {123U, 987U}) {
                std::vector<SizeType> indices(count);
                std::iota(indices.begin(), indices.end(), 0);
                std::vector<std::pair<SizeType, SizeType>> leaves;
                // Test the projection directly, outside the clock-reseeded worker.
                std::srand(ambient);
                graph.PartitionByTptree<float>(loaded.get(), indices, 0, count - 1, leaves);
                const int next = std::rand();
                BOOST_REQUIRE_GT(leaves.size(), 1U);
                SizeType begin = 0;
                for (const auto& leaf : leaves) {
                    BOOST_CHECK_EQUAL(leaf.first, begin);
                    BOOST_REQUIRE_GE(leaf.second, leaf.first);
                    BOOST_CHECK_LE(leaf.second - leaf.first, graph.m_iTPTLeafSize);
                    begin = leaf.second + 1;
                }
                BOOST_CHECK_EQUAL(begin, count);
                std::sort(indices.begin(), indices.end());
                for (SizeType row = 0; row < count; ++row) BOOST_CHECK_EQUAL(indices[row], row);
                // A binary tree has leaves-1 splits; upstream draws 100 sets
                // of projection weights from the global C stream per split.
                std::srand(ambient);
                for (size_t draw = 0; draw < (leaves.size() - 1) * 100 *
                         graph.m_numTopDimensionTPTSplit; ++draw) std::rand();
                BOOST_CHECK_EQUAL(next, std::rand());
            }
        }
#endif
        for (SizeType query : {0, 127, 1023}) {
            COMMON::QueryResultSet<float> before(values + query * dimension, 10);
            COMMON::QueryResultSet<float> after(values + query * dimension, 10);
            BOOST_REQUIRE(index->SearchIndex(before) == ErrorCode::Success);
            BOOST_REQUIRE(loaded->SearchIndex(after) == ErrorCode::Success);
            BOOST_CHECK_EQUAL(before.GetResult(0)->VID, query);
            for (int rank = 0; rank < 10; ++rank) {
                BOOST_REQUIRE_GE(before.GetResult(rank)->VID, 0);
                BOOST_REQUIRE_LT(before.GetResult(rank)->VID, count);
                BOOST_CHECK_EQUAL(before.GetResult(rank)->VID, after.GetResult(rank)->VID);
                BOOST_CHECK_EQUAL(before.GetResult(rank)->Dist, after.GetResult(rank)->Dist);
            }
        }
        const auto resaved = directory / "resaved";
        BOOST_REQUIRE(loaded->SaveIndex(resaved.string()) == ErrorCode::Success);
        artifact = 0;
        for (const char* name : {"tree.bin", "graph.bin"}) {
            std::ifstream input(resaved / name, std::ios::binary);
            BOOST_REQUIRE(input.good());
            const std::vector<char> content((std::istreambuf_iterator<char>(input)), {});
            BOOST_CHECK_MESSAGE(content == expected[artifact++], name << " changed on reload/save");
        }
    }
}

BOOST_AUTO_TEST_CASE(DeterministicBoundedDistinctOCandidates)
{
    std::vector<std::vector<std::uint32_t>> firstRows;
    for (int duplicates : {1, 3})
    {
        auto support = ExpansionBase();
        SPANN::LimitedTagSupportExpansion expansion;
        std::string error;
        BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, 3, &error));
        for (SizeType head = 0; head < 4; ++head)
        {
            for (int repeat = 0; repeat < duplicates; ++repeat)
            {
                BOOST_REQUIRE(expansion.ObserveOriginalAssignment(head, 9, &error));
                BOOST_REQUIRE(expansion.ObserveOriginalAssignment(head, 7, &error));
            }
        }
        BOOST_REQUIRE(expansion.Apply(support, &error));
        BOOST_REQUIRE(support.Finalize(&error));
        BOOST_CHECK(support.HasExpansion());
        BOOST_CHECK_EQUAL(support.HeadCount(), 4);
        BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 3U);
        BOOST_CHECK_EQUAL(support.CoverageCount(9), 3U);
        BOOST_CHECK_EQUAL(support.RequiredHeadCount(9), 3U);
        BOOST_CHECK_EQUAL(expansion.ExpandedTags(), 1U);
        BOOST_CHECK_EQUAL(expansion.CappedTags(), 0U);
        std::vector<std::vector<std::uint32_t>> rows;
        for (SizeType head = 0; head < 4; ++head) rows.push_back(support.HeadTags(head));
        if (duplicates == 1) firstRows = rows;
        else BOOST_CHECK(rows == firstRows);
    }
}

BOOST_AUTO_TEST_CASE(RetainedOPrefixExcludesPostingCutSuffix)
{
    struct Edge { SizeType node; SizeType tonode; };
    const std::vector<Edge> original{{0, 0}, {0, 20}, {1, 21}};
    const std::vector<int> retained{1, 1, 0, 0};
    const auto tagAt = [](SizeType vid) { return vid >= 20 ? 9U : vid >= 10 ? 8U : 7U; };
    auto support = ExpansionBase();
    SPANN::LimitedTagSupportExpansion expansion;
    std::string error;
    BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, 3, &error));
    BOOST_REQUIRE(expansion.ObserveRetainedOriginalPostings(
        original, retained, 22, tagAt, &error));
    BOOST_REQUIRE(expansion.Apply(support, &error));
    BOOST_REQUIRE(support.Finalize(&error));
    BOOST_CHECK(!support.Supports(0, 9));
    BOOST_CHECK(support.Supports(1, 9));
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(9), 1U);
    BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 1U);
    BOOST_CHECK_EQUAL(expansion.OriginalAssignments(), 2U);
    BOOST_CHECK_EQUAL(expansion.CappedTags(), 1U);
    BOOST_CHECK_EQUAL(original.size(), 3U);
    BOOST_CHECK_EQUAL(original[1].tonode, 20);
    BOOST_CHECK_EQUAL(retained[0], 1);
}

BOOST_AUTO_TEST_CASE(OwnOnlyTagsNeedNoInventedPostingOrHead)
{
    SPANN::LimitedTagSupport support;
    BOOST_REQUIRE(support.Initialize(2, 2, 16, 0, 1, 12345));
    for (SizeType head = 0; head < 2; ++head)
    {
        const std::uint32_t own = 7 + head;
        BOOST_REQUIRE(support.SetHeadTags(head, {own}));
        BOOST_REQUIRE(support.SetHeadAttributes(head, &own, 1));
    }
    BOOST_REQUIRE(support.SetTagVectorCounts(2, {{7, 1}, {8, 1}}));
    SPANN::LimitedTagSupportExpansion expansion;
    std::string error;
    BOOST_REQUIRE(expansion.Initialize(support, {7, 8}, 16, &error));
    BOOST_REQUIRE(expansion.Apply(support, &error));
    BOOST_REQUIRE(support.Finalize(&error));
    BOOST_CHECK(support.HasExpansion());
    BOOST_CHECK_EQUAL(support.HeadCount(), 2);
    BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 0U);
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(7), 1U);
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(8), 1U);
    BOOST_CHECK_EQUAL(expansion.CappedTags(), 2U);
}

BOOST_AUTO_TEST_CASE(InvalidFloorAndMissingSourceFailExplicitly)
{
    auto support = ExpansionBase();
    SPANN::LimitedTagSupportExpansion expansion;
    std::string error;
    BOOST_CHECK(!expansion.Initialize(support, {7, 8, 9}, 0, &error));
    BOOST_CHECK(!error.empty());
    BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, 3, &error));
    BOOST_CHECK(!expansion.Apply(support, &error));
    BOOST_CHECK(error.find("neither") != std::string::npos);
    BOOST_CHECK(!support.HasExpansion());
}

BOOST_AUTO_TEST_CASE(ExtraRowsCrossBatchBoundariesAndReload)
{
    for (const auto configuration : {
        std::pair<SizeType, bool>{66, true}, {1024, true}, {66, false}, {1024, false}})
    {
        const SizeType heads = configuration.first;
        const bool inlineExpected = configuration.second;
        const std::array<SizeType, 4> expandedHeads{0, 63, 64, heads - 1};
        SPANN::LimitedTagSupport support;
        BOOST_REQUIRE(support.Initialize(heads, 1, 4, 0, 1, 12345));
        const std::uint32_t own = 17;
        std::vector<std::uint64_t> offsets(static_cast<size_t>(heads) + 1, 0);
        std::vector<std::uint32_t> extraTags;
        std::unordered_map<std::uint32_t, std::uint64_t> counts{
            {own, static_cast<std::uint64_t>(heads)}};
        std::unordered_map<std::uint32_t, std::uint32_t> required{{own, 4}};
        if (inlineExpected)
        {
            counts.emplace(29, 1);
            required.emplace(29, 4);
            extraTags.assign(4, 29);
        }
        for (SizeType head = 0; head < heads; ++head)
        {
            BOOST_REQUIRE(support.SetHeadTags(head, {own}));
            BOOST_REQUIRE(support.SetHeadAttributes(head, &own, 1));
            const bool expanded = inlineExpected &&
                std::find(expandedHeads.begin(), expandedHeads.end(), head) != expandedHeads.end();
            offsets[static_cast<size_t>(head) + 1] = offsets[static_cast<size_t>(head)] + expanded;
        }
        BOOST_REQUIRE(support.SetTagVectorCounts(
            static_cast<std::uint64_t>(heads) + inlineExpected, counts));
        std::string error;
        BOOST_REQUIRE(support.ConfigureExpansion(
            std::move(offsets), std::move(extraTags), required, &error));
        const auto check = [&](const SPANN::LimitedTagSupport& table) {
            BOOST_CHECK_EQUAL(table.HasInlineExtraTags(), inlineExpected);
            BOOST_CHECK_EQUAL(table.HeadLookupStride(), inlineExpected ? 4U : 1U);
            BOOST_CHECK_EQUAL(table.HeadAttributeStride(), inlineExpected ? 4U : 1U);
            BOOST_CHECK(!table.NeedsExtraLookupPrefetch());
            for (SizeType head = 0; head < heads; ++head)
            {
                const bool expanded = inlineExpected &&
                    std::find(expandedHeads.begin(), expandedHeads.end(), head) != expandedHeads.end();
                BOOST_CHECK_EQUAL(table.ExtraTagCount(head), expanded ? 1U : 0U);
                BOOST_CHECK(table.Supports(head, own));
                BOOST_CHECK_EQUAL(table.Supports(head, 29), expanded);
                BOOST_CHECK(!table.Supports(head, SPANN::LimitedTagSupport::EmptyTag));
                BOOST_CHECK(table.ExtraHeadLookupData(head) == nullptr);
                BOOST_REQUIRE(table.HeadLookupData(head) != nullptr);
                BOOST_CHECK(table.HeadLookupData(head) + (inlineExpected ? 2 : 0) == table.HeadTagData(head));
                BOOST_CHECK(table.HeadLookupData(0) +
                    static_cast<size_t>(head) * table.HeadLookupStride() == table.HeadLookupData(head));
                BOOST_REQUIRE(table.HeadAttributes(head) != nullptr);
                BOOST_CHECK(table.HeadAttributes(0) +
                    static_cast<size_t>(head) * table.HeadAttributeStride() == table.HeadAttributes(head));
                BOOST_CHECK_EQUAL(table.HeadAttributes(head)[0], own);
                const auto* tags = table.ExtraTagData(head);
                BOOST_CHECK_EQUAL(tags != nullptr, expanded);
                if (expanded)
                {
                    BOOST_REQUIRE(tags != nullptr);
                    BOOST_CHECK_EQUAL(tags[0], 29U);
                }
                const auto tail = table.ExtraLookupTagRange(head);
                BOOST_CHECK(tail.first == nullptr);
                BOOST_CHECK(tail.second == nullptr);
            }
            BOOST_CHECK(table.ExtraHeadLookupData(-1) == nullptr);
            BOOST_CHECK(table.ExtraHeadLookupData(heads) == nullptr);
            BOOST_CHECK(table.ExtraTagData(-1) == nullptr);
            BOOST_CHECK(table.ExtraTagData(heads) == nullptr);
        };
        check(support);
        auto copied = support;
        check(copied);
        const std::string path = "limited_expansion_presence.bin";
        BOOST_REQUIRE(support.Save(path, &error));
        SPANN::LimitedTagSupport loaded;
        BOOST_REQUIRE(loaded.Load(path, heads, 1, 4, 0, 1, 12345, &error));
        check(loaded);
        support.Reset();
        BOOST_CHECK_EQUAL(support.ExtraTagCount(64), 0U);
        BOOST_CHECK(support.ExtraHeadLookupData(64) == nullptr);
        BOOST_CHECK(support.ExtraTagData(64) == nullptr);
        BOOST_REQUIRE(std::filesystem::remove(path));
    }
}

BOOST_AUTO_TEST_CASE(InlineExtraTagPreservesFullRowsAndSerializedOffsets)
{
    SPANN::LimitedTagSupport support;
    constexpr std::uint32_t own = 1;
    constexpr std::uint32_t high = 4000000000U;
    constexpr std::uint32_t last = SPANN::LimitedTagSupport::EmptyTag - 1;
    BOOST_REQUIRE(support.Initialize(4, 1, 1, 0, 1, 12345));
    for (SizeType head = 0; head < 4; ++head)
    {
        BOOST_REQUIRE(support.SetHeadTags(head, {own}));
        BOOST_REQUIRE(support.SetHeadAttributes(head, &own, 1));
    }
    BOOST_REQUIRE(support.SetTagVectorCounts(
        10, {{own, 4}, {0, 1}, {17, 1}, {high, 1}, {last, 1}, {23, 1}, {31, 1}}));
    std::string error;
    BOOST_REQUIRE(support.ConfigureExpansion(
        {0, 3, 3, 4, 6}, {0, 17, high, last, 23, 31},
        {{own, 1}, {0, 1}, {17, 1}, {high, 1}, {last, 1}, {23, 1}, {31, 1}},
        &error));
    const auto check = [&](const SPANN::LimitedTagSupport& table) {
        BOOST_REQUIRE(table.Validate(&error));
        BOOST_CHECK(table.HasInlineExtraTags());
        BOOST_CHECK_EQUAL(table.LegacyExtraSupportCap(), 0U);
        for (std::uint32_t tag : {0U, own, 17U, high})
            BOOST_CHECK(table.Supports(0, tag));
        for (std::uint32_t tag : {8U, 18U, last, SPANN::LimitedTagSupport::EmptyTag})
            BOOST_CHECK(!table.Supports(0, tag));
        BOOST_CHECK(table.Supports(2, last));
        BOOST_CHECK(!table.Supports(2, 0));
        BOOST_CHECK(table.Supports(3, 23));
        BOOST_CHECK(table.Supports(3, 31));
        BOOST_CHECK(!table.Supports(3, 24));
        BOOST_CHECK_EQUAL(table.ExtraTagCount(0), 3U);
        BOOST_CHECK_EQUAL(table.ExtraTagCount(1), 0U);
        BOOST_CHECK_EQUAL(table.ExtraTagCount(2), 1U);
        BOOST_REQUIRE(table.ExtraTagData(0) != nullptr);
        BOOST_CHECK_EQUAL(table.ExtraTagData(0)[0], 0U);
        const auto tail = table.ExtraLookupTagRange(0);
        BOOST_REQUIRE(tail.first != nullptr);
        BOOST_CHECK_EQUAL(tail.second - tail.first, 2);
        BOOST_CHECK_EQUAL(tail.first[0], 17U);
        BOOST_CHECK_EQUAL(tail.first[1], high);
        BOOST_CHECK(table.ExtraLookupTagRange(2).first == nullptr);
    };
    check(support);
    const std::filesystem::path path = "limited_expansion_inline.bin";
    BOOST_REQUIRE(support.Save(path.string(), &error));
    std::ifstream input(path, std::ios::binary);
    input.seekg(sizeof(SPANN::LimitedTagSupport::HeaderV4) +
        4 * sizeof(std::uint32_t) + 4 * sizeof(std::uint32_t) +
        7 * sizeof(SPANN::LimitedTagSupport::TagCountRecord));
    std::array<std::uint64_t, 5> offsets{};
    input.read(reinterpret_cast<char*>(offsets.data()), sizeof(offsets));
    BOOST_REQUIRE(input.good());
    const std::array<std::uint64_t, 5> expected{0, 3, 3, 4, 6};
    BOOST_CHECK(offsets == expected);
    input.close();
    SPANN::LimitedTagSupport loaded;
    BOOST_REQUIRE(loaded.Load(path.string(), 4, 1, 1, 0, 1, 12345, &error));
    check(loaded);
    check(SPANN::LimitedTagSupport(loaded));
    BOOST_CHECK_EQUAL(loaded.ContentFingerprint(), support.ContentFingerprint());
    BOOST_REQUIRE(std::filesystem::remove(path));
}

BOOST_AUTO_TEST_CASE(RejectsInvalidOrderingCountsAndVIDs)
{
    struct Edge { SizeType node; SizeType tonode; };
    auto support = ExpansionBase();
    SPANN::LimitedTagSupportExpansion expansion;
    std::string error;
    BOOST_CHECK(!expansion.Initialize(support, {8, 7, 9}, 3, &error));
    BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, 3, &error));
    BOOST_REQUIRE(expansion.ObserveOriginalAssignment(2, 9, &error));
    BOOST_CHECK(!expansion.ObserveOriginalAssignment(1, 9, &error));
    BOOST_CHECK(!expansion.ObserveOriginalAssignment(4, 9, &error));
    BOOST_CHECK(!expansion.ObserveOriginalAssignment(3, 10, &error));
    const auto tagAt = [](SizeType) { return 9U; };
    for (const auto& edges : std::vector<std::vector<Edge>>{
             {{0, 22}}, {{1, 0}, {0, 1}}, {}})
    {
        BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, 3, &error));
        BOOST_CHECK(!expansion.ObserveRetainedOriginalPostings(
            edges, {1, 0, 0, 0}, 22, tagAt, &error));
        BOOST_CHECK(!error.empty());
        BOOST_CHECK(!support.HasExpansion());
    }
}

BOOST_AUTO_TEST_CASE(PerTagDeficitsCanExceedFormerGlobalCap)
{
    constexpr SizeType heads = 17;
    constexpr std::uint32_t tags = 16385;
    constexpr int floor = 16;
    SPANN::LimitedTagSupport support;
    BOOST_REQUIRE(support.Initialize(heads, 1, floor, 0, 1, 12345));
    const std::uint32_t own = 0;
    for (SizeType head = 0; head < heads; ++head)
    {
        BOOST_REQUIRE(support.SetHeadTags(head, {own}));
        BOOST_REQUIRE(support.SetHeadAttributes(head, &own, 1));
    }
    std::unordered_map<std::uint32_t, std::uint64_t> counts;
    std::vector<std::uint32_t> observed;
    for (std::uint32_t tag = 0; tag <= tags; ++tag)
    {
        counts.emplace(tag, heads);
        observed.push_back(tag);
    }
    BOOST_REQUIRE(support.SetTagVectorCounts(
        static_cast<std::uint64_t>(tags + 1) * heads, counts));
    SPANN::LimitedTagSupportExpansion expansion;
    std::string error;
    BOOST_REQUIRE(expansion.Initialize(support, observed, floor, &error));
    for (SizeType head = 0; head < heads; ++head)
        for (std::uint32_t tag = 1; tag <= tags; ++tag)
        {
            BOOST_REQUIRE(expansion.ObserveOriginalAssignment(head, tag, &error));
            BOOST_REQUIRE(expansion.ObserveOriginalAssignment(head, tag, &error));
        }
    BOOST_REQUIRE_MESSAGE(expansion.Apply(support, &error), error);
    BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 262160U);
    BOOST_CHECK_EQUAL(expansion.CappedTags(), 0U);
    for (std::uint32_t tag = 1; tag <= tags; ++tag)
        BOOST_CHECK_EQUAL(support.RequiredHeadCount(tag), floor);
    const std::string path = "limited_expansion_above_old_cap.bin";
    BOOST_REQUIRE(support.Save(path, &error));
    SPANN::LimitedTagSupport loaded;
    BOOST_REQUIRE_MESSAGE(loaded.Load(path, heads, 1, floor, 0, 1, 12345, &error), error);
    BOOST_CHECK_EQUAL(loaded.ExtraSupportCount(), 262160U);
    BOOST_CHECK_EQUAL(loaded.ContentFingerprint(), support.ContentFingerprint());
    BOOST_CHECK(loaded.TagHeads() == support.TagHeads());
    BOOST_REQUIRE(std::filesystem::remove(path));
}

BOOST_AUTO_TEST_CASE(HugeFloorUsesOnlyActualDistinctSources)
{
    const auto floor = (std::numeric_limits<int>::max)();
    auto support = ExpansionBase(floor);
    SPANN::LimitedTagSupportExpansion expansion;
    std::string error;
    BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, floor, &error));
    BOOST_REQUIRE(expansion.ObserveOriginalAssignment(0, 9, &error));
    BOOST_REQUIRE(expansion.ObserveOriginalAssignment(0, 9, &error));
    BOOST_REQUIRE_MESSAGE(expansion.Apply(support, &error), error);
    BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 1U);
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(7), 4U);
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(8), 4U);
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(9), 1U);
}

BOOST_AUTO_TEST_SUITE_END()
