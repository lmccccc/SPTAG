// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#include "inc/Core/SPANN/HeadNodeMetadata.h"
#include "inc/Core/SPANN/CanonicalHierarchyVectors.h"
#include "inc/Core/SPANN/RoutingSignatures.h"
#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>

using namespace SPTAG;
namespace fs = std::filesystem;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Compact metadata line " + std::to_string(__LINE__)); } while (0)

void CheckColumnProjection(const TagSchema& schema, const Cache::HierWidthTable& widths, bool legacy)
{
    auto index = VectorIndex::CreateInstance(IndexAlgoType::BKT, VectorValueType::Float);
    if (legacy) index->InitializeHeadNodeMeta(3, static_cast<int>(schema.numeric.size()), widths, true);
    else index->InitializeCompactHeadNodeMeta(3, schema, widths, true);
    Cache::HierarchicalPostingMask expected;
    for (int column = 0; column < Cache::HIER_LEVELS; ++column) {
        if (legacy || std::find(schema.categorical.begin(), schema.categorical.end(), column) !=
                          schema.categorical.end()) {
            expected.Insert(column, 67 + column, widths);
            expected.Insert(column, widths.bits[column] - 1, widths);
        }
    }
    index->SetHeadNodePostingHierMask(2, expected);
    const auto view = index->GetHeadNodePostingHierMask(2);
    Cache::HierarchicalPostingMask projected;
    std::memset(projected.mask, 0xff, sizeof(projected.mask));
    view.CopyTo(projected, widths);
    CHECK(std::memcmp(&projected, &expected, sizeof(expected)) == 0);
    for (int column = -1; column <= Cache::HIER_LEVELS; ++column) {
        for (std::uint32_t value : {0U, 63U, 64U, 67U, 127U, 128U, 191U, 192U, 255U, 256U, 511U, UINT32_MAX}) {
            CHECK(view.MayContain(column, value, widths) == expected.MayContain(column, value, widths));
            Cache::DNFPredicate dnf;
            dnf.clauses.push_back({{{static_cast<std::uint32_t>(column), value, Cache::DNF_EQ, 0}}});
            const SPANN::RoutingPredicate predicate(&dnf, nullptr, 0, {}, {});
            CHECK(predicate.MayMatch(nullptr, view, widths, nullptr) ==
                  predicate.MayMatch(nullptr, &view, widths, nullptr));
            if (column >= 0 && column < Cache::HIER_LEVELS) {
                CHECK(predicate.MayMatch(nullptr, view, widths, nullptr) ==
                      expected.MayContain(column, value, widths));
                Cache::HierarchicalPostingMask query;
                query.Insert(column, value, widths);
                CHECK(view.MayIntersect(query, widths) == expected.MayIntersect(query, widths));
            }
            CHECK(predicate.MayMatch(nullptr, HeadPostingMaskView{}, widths, nullptr));
        }
    }
    CHECK(!index->GetHeadNodePostingHierMask(-1));
    CHECK(!index->GetHeadNodePostingHierMask(3));
    CHECK(!index->GetHeadNodePostingHierMask((std::numeric_limits<SizeType>::max)()));
    CHECK(index->GetHeadNodeNumQuant(-1) == nullptr);
    CHECK(index->GetHeadNodeNumQuant(3) == nullptr);
    const auto saved = index->GetHeadNodeMetaBlob();
    index->GetHeadNodeMetaBlob().resize(saved.size() - 1);
    CHECK(!index->GetHeadNodePostingHierMask(2));
    if (!schema.numeric.empty()) CHECK(index->GetHeadNodeNumQuant(2) != nullptr);
    CHECK(index->GetHeadNodeTailNumQuant(2) == nullptr);
    index->GetHeadNodeMetaBlob() = saved;
    index->GetHeadNodePostingHierMask(2).CopyTo(projected, widths);
    CHECK(std::memcmp(&projected, &expected, sizeof(expected)) == 0);
}

template<class T> void CheckCanonical(const fs::path& folder, const char* filename)
{
    constexpr DimensionType dimension = 128;
    const auto type = GetEnumValueType<T>();
    auto heads = VectorIndex::CreateInstance(IndexAlgoType::BKT, type);
    heads->SetParameter("DistCalcMethod", "L2");
    heads->SetParameter("NumberOfThreads", "1");
    heads->SetParameter("BKTKmeansK", "4");
    heads->SetParameter("NeighborhoodSize", "8");
    std::vector<T> data(64 * dimension);
    for (std::size_t i = 0; i < data.size(); ++i)
        data[i] = static_cast<T>((i * 17 + i / dimension) % 251);
    CHECK(heads->BuildIndex(data.data(), 64, dimension, true, false) == ErrorCode::Success);
    const std::vector<std::uint32_t> ids{1, 7, 9};
    auto bytes = ByteArray::Alloc(ids.size() * dimension * sizeof(T));
    for (std::size_t i = 0; i < ids.size(); ++i)
        std::memcpy(bytes.Data() + i * dimension * sizeof(T),
            heads->GetSample(ids[i]), dimension * sizeof(T));
    BasicVectorSet source(bytes, type, dimension, 3);
    const auto file = (folder / filename).string();
    CHECK(SPANN::SaveCanonicalHierarchyVectors(file, heads, ids, source));
    CHECK(fs::file_size(file) == 32 + ids.size() * 4);
    std::shared_ptr<VectorSet> restored;
    CHECK(SPANN::LoadCanonicalOrOwnedHierarchyVectors(file, type, dimension, 3, heads, restored) ==
        ErrorCode::Success);
    CHECK(restored->GetVector(1) == heads->GetSample(7));
    CHECK(restored->GetVector(-1) == nullptr);
    CHECK(restored->GetVector(3) == nullptr);
    CHECK(source.Save(file + ".legacy") == ErrorCode::Success);
    CHECK(SPANN::LoadCanonicalOrOwnedHierarchyVectors(file + ".legacy", type, dimension, 3,
        heads, restored, nullptr, &ids) == ErrorCode::Success);
    CHECK(restored->GetVector(2) == heads->GetSample(9));
    const std::vector<std::uint32_t> wrongIDs{1, 7, 10};
    CHECK(SPANN::LoadCanonicalOrOwnedHierarchyVectors(file, type, dimension, 3,
        heads, restored, nullptr, &wrongIDs) != ErrorCode::Success);
    CHECK(SPANN::LoadCanonicalOrOwnedHierarchyVectors(file + ".legacy", type, dimension, 3,
        heads, restored, nullptr, &wrongIDs) != ErrorCode::Success);
    std::vector<std::uint32_t> flat;
    CHECK(SPANN::FlattenHierarchyIDs({2, 0}, 64, ids, flat));
    CHECK((flat == std::vector<std::uint32_t>{9, 1}));
    CHECK(!SPANN::FlattenHierarchyIDs({3}, 64, ids, flat));
    CHECK(!SPANN::SaveCanonicalHierarchyVectors(file + ".bad", heads, {1, 7, 64}, source));
    bytes.Data()[0] ^= 1;
    CHECK(!SPANN::SaveCanonicalHierarchyVectors(file + ".changed", heads, ids, source));
    {
        std::fstream out(file, std::ios::binary | std::ios::in | std::ios::out);
        const std::uint32_t bad = 64;
        out.seekp(32);
        out.write(reinterpret_cast<const char*>(&bad), sizeof(bad));
    }
    CHECK(SPANN::LoadCanonicalOrOwnedHierarchyVectors(file, type, dimension, 3, heads, restored) !=
        ErrorCode::Success);
}

int main()
{
    const fs::path folder = fs::current_path() /
        ("compact_metadata_fixture_" + std::to_string(
            std::chrono::steady_clock::now().time_since_epoch().count()));
    try {
        fs::create_directory(folder);
        Cache::HierWidthTable variedWidths;
        const int bits[] = {64, 128, 192, 256, 64};
        variedWidths.Set(bits, Cache::HIER_LEVELS);
        for (const auto& shape : {"categorical,numeric", "numeric,categorical,numeric,categorical",
                                  "numeric", "categorical,categorical,categorical,categorical,categorical"})
            for (bool legacy : {false, true}) {
                CheckColumnProjection(TagSchema::Parse(shape), variedWidths, legacy);
                CheckColumnProjection(TagSchema::Parse(shape), Cache::HierWidthTable{}, legacy);
            }
        const auto schema = TagSchema::Parse("categorical,numeric");
        Cache::HierWidthTable widths;
        auto old = VectorIndex::CreateInstance(IndexAlgoType::BKT, VectorValueType::Float);
        old->InitializeHeadNodeMeta(2, 1, widths, true);
        CHECK(old->GetHeadNodeMetaStride() == 320);
        for (int head = 0; head < 2; ++head) {
            old->SetHeadNodeGlobalVID(head, head + 7);
            old->SetHeadNodeBundleNodeId(head, head - 1);
            old->SetHeadNodeHeadOnly(head, head != 0);
            Cache::HierarchicalOwnTags own;
            own.Insert(0, 100 + head);
            own.Insert(1, 300 + head);
            old->SetHeadNodeHierMask(head, own);
            Cache::PostingBitmask h, o;
            h.Insert(100 + head);
            o.Insert(999);
            old->SetHeadNodePS(head, h);
            old->SetHeadNodeTailPS(head, o);
            Cache::HierarchicalPostingMask column;
            column.Insert(0, 100 + head, widths);
            column.Insert(0, 42, widths); // Distinct from the coarse H mask.
            old->SetHeadNodePostingHierMask(head, column);
            Cache::NumQuantInsert(old->GetHeadNodeNumQuantMutable(head), 0, 129);
            Cache::NumQuantInsert(old->GetHeadNodeTailNumQuantMutable(head), 0, 17);
        }
        old->SetHeadNodeNumericDomainFingerprint(1234);
        const auto legacy = (folder / "v8.bin").string();
        const auto compact = (folder / "v9.bin").string();
        CHECK(SPANN::SaveHeadNodeMetadataV8(legacy, old, 77));
        auto index = VectorIndex::CreateInstance(IndexAlgoType::BKT, VectorValueType::Float);
        const auto vid = [](SizeType head) { return head + 7; };
        CHECK(SPANN::LoadHeadNodeMetadata(legacy, index, 2, 77, vid, &schema));
        CHECK(index->GetHeadNodeMetaStride() == 176);
        CHECK(index->GetHeadNodeMetaBlob().size() == 352);
        CHECK(index->GetHeadNodeHierMask(1)->count == 2);
        CHECK(index->GetHeadNodeHierMask(1)->tag[1] == 301);
        CHECK(index->GetHeadNodePostingHierMask(0)->MayContain(0, 42, widths));
        CHECK(!index->GetHeadNodePS(0)->MayContain(42));
        CHECK(!index->GetHeadNodePostingHierMask(0)->MayContain(1, 300, widths));
        CHECK(index->GetHeadNodeTailPS(0)->MayContain(999));
        CHECK(index->GetHeadNodeBundleNodeId(0) == -1);
        CHECK(!index->IsHeadNodeHeadOnly(0) && index->IsHeadNodeHeadOnly(1));
        CHECK(reinterpret_cast<std::uintptr_t>(index->GetHeadNodeNumQuant(0)) % 8 == 0);
        CHECK(index->GetHeadNodeTailNumQuant(-1) == nullptr);
        CHECK(index->GetHeadNodeNumQuant(2) == nullptr);
        CHECK(Cache::NumQuantAnyInRange(index->GetHeadNodeNumQuant(0), 0, 129, 129));
        CHECK(!Cache::NumQuantAnyInRange(index->GetHeadNodeTailNumQuant(0), 0, 129, 129));
        CHECK(SPANN::SaveHeadNodeMetadata(compact, index, 77));
        CHECK(fs::file_size(compact) == 68 + schema.text.size() + 352);
        CHECK(!SPANN::SaveHeadNodeMetadataV8((folder / "invalid.bin").string(), index, 77));
        CHECK(SPANN::LoadHeadNodeMetadata(compact, index, 2, 77, vid, &schema));
        int identityChecks = 0;
        CHECK(!SPANN::LoadHeadNodeMetadata(compact, old, 2, 77, {}, &schema,
            [&]() { ++identityChecks; return false; }));
        CHECK(identityChecks == 1 && !old->HasHeadNodeMeta());
        const auto interleaved = TagSchema::Parse("numeric,categorical,numeric,categorical");
        auto second = VectorIndex::CreateInstance(IndexAlgoType::BKT, VectorValueType::UInt8);
        second->InitializeCompactHeadNodeMeta(3, interleaved, widths, true);
        Cache::HierarchicalPostingMask mask;
        mask.Insert(1, 678, widths);
        mask.Insert(3, 901, widths);
        second->SetHeadNodePostingHierMask(2, mask);
        CHECK(second->GetHeadNodePostingHierMask(2)->MayContain(1, 678, widths));
        CHECK(second->GetHeadNodePostingHierMask(2)->MayContain(3, 901, widths));
        CHECK(index->GetHeadNodePostingHierMask(0)->MayContain(0, 42, widths));
        CHECK(!SPANN::LoadHeadNodeMetadata(compact, second, 2, 77, vid, &interleaved));
        CHECK(!SPANN::LoadHeadNodeMetadata(compact, second, 2, 78, vid, &schema));
        CHECK(!SPANN::LoadHeadNodeMetadata(compact, second, 2, 77,
            [](SizeType head) { return head; }, &schema));
        {
            std::fstream corrupt(compact, std::ios::in | std::ios::out | std::ios::binary);
            corrupt.seekp(-1, std::ios::end);
            const char changed = 1;
            corrupt.write(&changed, 1);
        }
        CHECK(!SPANN::LoadHeadNodeMetadata(compact, second, 2, 77, vid, &schema));
        CHECK(!second->HasHeadNodeMeta());
        CHECK(SPANN::LoadHeadNodeMetadataV8(legacy, second, 2, 77, vid));
        CHECK(second->GetHeadNodeMetaStride() == 320);
        const auto numericOnly = TagSchema::Parse("numeric");
        second->InitializeCompactHeadNodeMeta(2, numericOnly, widths, true);
        CHECK(second->GetHeadNodeMetaStride() == 80);
        CHECK(second->GetHeadNodePS(0)->Popcount() == 0);
        CheckCanonical<float>(folder, "float.map");
        CheckCanonical<std::uint8_t>(folder, "uint8.map");
        fs::remove_all(folder);
        std::cout << "PASS compact views, H/O, distinct categorical semantics, lanes, alignment, V8/V9 authentication and independent layouts\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\nFixture retained: " << folder << '\n';
        return 1;
    }
}
