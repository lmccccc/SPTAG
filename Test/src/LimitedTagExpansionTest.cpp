// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Core/SPANN/LimitedTagSupportExpansion.h"
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
    BOOST_REQUIRE(support.Initialize(4, 2, 2, floor, 0, 1, 12345));
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

BOOST_AUTO_TEST_CASE(SeededHeadGeometryIgnoresAmbientRandomState)
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
    const std::filesystem::path directory = "limited_tag_seeded_head_geometry";
    struct Cleanup
    {
        std::filesystem::path path;
        ~Cleanup() { std::error_code error; std::filesystem::remove_all(path, error); }
    } cleanup{directory};
    std::array<std::vector<char>, 2> expected;
    for (unsigned ambient : {123U, 987U})
    {
        auto metadataRoot = VectorIndex::CreateInstance(IndexAlgoType::KDT, VectorValueType::Float);
        BOOST_REQUIRE(metadataRoot != nullptr);
        BOOST_REQUIRE(metadataRoot->SetParameter("DistCalcMethod", "L2") == ErrorCode::Success);
        BOOST_REQUIRE(metadataRoot->SetParameter("NumberOfThreads", "1") == ErrorCode::Success);
        BOOST_REQUIRE(metadataRoot->SetParameter("KDTNumber", "1") == ErrorCode::Success);
        auto rootVector = std::make_shared<BasicVectorSet>(
            bytes, VectorValueType::Float, dimension, 1);
        BOOST_REQUIRE(metadataRoot->BuildIndex(rootVector, nullptr, true, false, false) ==
            ErrorCode::Success);
        std::srand(ambient);
        auto index = VectorIndex::CreateInstance(IndexAlgoType::BKT, VectorValueType::Float);
        BOOST_REQUIRE_EQUAL(index->GetParameter("BKTSeed"), "-1");
        BOOST_REQUIRE_EQUAL(index->GetParameter("TPTSeed"), "-1");
        for (const auto& option : std::vector<std::pair<const char*, const char*>>{
                 {"DistCalcMethod", "L2"}, {"NumberOfThreads", "1"},
                 {"BKTSeed", "0"}, {"TPTSeed", "7"}, {"BKTNumber", "1"},
                 {"BKTKmeansK", "8"}, {"BKTLeafSize", "8"}, {"Samples", "128"},
                 {"BKTLambdaFactor", "1"}, {"TPTNumber", "2"}, {"TPTLeafSize", "64"},
                 {"NumTopDimensionTpTreeSplit", "4"}, {"NeighborhoodSize", "16"},
                 {"RefineIterations", "1"}, {"MaxCheckForRefineGraph", "128"}})
            BOOST_REQUIRE(index->SetParameter(option.first, option.second) == ErrorCode::Success);
        BOOST_REQUIRE(index->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::Success);
        std::filesystem::create_directories(directory);
        BOOST_REQUIRE(index->SaveIndex(directory.string()) == ErrorCode::Success);
        size_t artifact = 0;
        for (const char* name : {"tree.bin", "graph.bin"})
        {
            std::ifstream input(directory / name, std::ios::binary);
            BOOST_REQUIRE(input.good());
            std::vector<char> content((std::istreambuf_iterator<char>(input)),
                                     std::istreambuf_iterator<char>());
            BOOST_REQUIRE(!content.empty());
            if (ambient == 123U) expected[artifact] = std::move(content);
            else BOOST_CHECK_MESSAGE(content == expected[artifact], name << " depends on ambient RNG");
            ++artifact;
        }
        std::shared_ptr<VectorIndex> loaded;
        BOOST_REQUIRE(VectorIndex::LoadIndex(directory.string(), loaded) == ErrorCode::Success);
        BOOST_CHECK_EQUAL(loaded->GetParameter("BKTSeed"), "0");
        BOOST_CHECK_EQUAL(loaded->GetParameter("TPTSeed"), "7");
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
        BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, 3, 3, &error));
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
    BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, 3, 3, &error));
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
    BOOST_REQUIRE(support.Initialize(2, 2, 2, 16, 0, 1, 12345));
    for (SizeType head = 0; head < 2; ++head)
    {
        const std::uint32_t own = 7 + head;
        BOOST_REQUIRE(support.SetHeadTags(head, {own}));
        BOOST_REQUIRE(support.SetHeadAttributes(head, &own, 1));
    }
    BOOST_REQUIRE(support.SetTagVectorCounts(2, {{7, 1}, {8, 1}}));
    SPANN::LimitedTagSupportExpansion expansion;
    std::string error;
    BOOST_REQUIRE(expansion.Initialize(support, {7, 8}, 16, 8, &error));
    BOOST_REQUIRE(expansion.Apply(support, &error));
    BOOST_REQUIRE(support.Finalize(&error));
    BOOST_CHECK(support.HasExpansion());
    BOOST_CHECK_EQUAL(support.HeadCount(), 2);
    BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 0U);
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(7), 1U);
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(8), 1U);
    BOOST_CHECK_EQUAL(expansion.CappedTags(), 2U);
}

BOOST_AUTO_TEST_CASE(BudgetAndMissingSourceFailExplicitly)
{
    auto support = ExpansionBase();
    SPANN::LimitedTagSupportExpansion expansion;
    std::string error;
    BOOST_CHECK(!expansion.Initialize(support, {7, 8, 9}, 3, 0, &error));
    BOOST_CHECK(!error.empty());
    BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, 3, 1, &error));
    BOOST_REQUIRE(expansion.ObserveOriginalAssignment(0, 9, &error));
    BOOST_CHECK(!expansion.ObserveOriginalAssignment(1, 9, &error));
    BOOST_CHECK(error.find("LimitedTagMaxExtraSupports") != std::string::npos);
    BOOST_CHECK(!support.HasExpansion());
    BOOST_CHECK(!support.Supports(0, 9));
    BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, 3, 3, &error));
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
        BOOST_REQUIRE(support.Initialize(heads, 1, 1, 4, 0, 1, 12345));
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
            std::move(offsets), std::move(extraTags), required, 4, &error));
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
        BOOST_REQUIRE(loaded.Load(path, heads, 1, 1, 4, 0, 1, 12345, &error));
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
    constexpr std::uint64_t budget = std::uint64_t{1} << 40;
    BOOST_REQUIRE(support.Initialize(4, 1, 1, 1, 0, 1, 12345));
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
        budget, &error));
    const auto check = [&](const SPANN::LimitedTagSupport& table) {
        BOOST_REQUIRE(table.Validate(&error));
        BOOST_CHECK(table.HasInlineExtraTags());
        BOOST_CHECK_EQUAL(table.MaxExtraSupports(), budget);
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
    BOOST_REQUIRE(loaded.Load(path.string(), 4, 1, 1, 1, 0, 1, 12345, &error));
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
    BOOST_CHECK(!expansion.Initialize(support, {8, 7, 9}, 3, 3, &error));
    BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, 3, 3, &error));
    BOOST_REQUIRE(expansion.ObserveOriginalAssignment(2, 9, &error));
    BOOST_CHECK(!expansion.ObserveOriginalAssignment(1, 9, &error));
    BOOST_CHECK(!expansion.ObserveOriginalAssignment(4, 9, &error));
    BOOST_CHECK(!expansion.ObserveOriginalAssignment(3, 10, &error));
    const auto tagAt = [](SizeType) { return 9U; };
    for (const auto& edges : std::vector<std::vector<Edge>>{
             {{0, 22}}, {{1, 0}, {0, 1}}, {}})
    {
        BOOST_REQUIRE(expansion.Initialize(support, {7, 8, 9}, 3, 3, &error));
        BOOST_CHECK(!expansion.ObserveRetainedOriginalPostings(
            edges, {1, 0, 0, 0}, 22, tagAt, &error));
        BOOST_CHECK(!error.empty());
        BOOST_CHECK(!support.HasExpansion());
    }
}

BOOST_AUTO_TEST_SUITE_END()
