// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Core/SPANN/HierarchyVectorCatalog.h"
#include "inc/Core/SPANN/HeadNodeMetadata.h"
#include "inc/Core/SPANN/LimitedTagSupport.h"
#include "inc/Test.h"

#include <boost/filesystem/operations.hpp>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <memory>
#include <new>
#include <set>
#include <stdexcept>
#include <string>
#include <system_error>
#include <unordered_map>
#include <utility>
#include <vector>

using namespace SPTAG;
using namespace SPTAG::SPANN;

namespace
{
using Catalogs = std::vector<std::shared_ptr<VectorSet>>;
using SamplingMaps = std::vector<std::vector<std::uint64_t>>;

template <typename T>
std::shared_ptr<VectorSet> NativeCatalog(
    DimensionType dimension, const std::vector<T>& values)
{
    BOOST_REQUIRE(dimension > 0);
    BOOST_REQUIRE_EQUAL(values.size() % static_cast<std::size_t>(dimension), 0U);
    ByteArray bytes = ByteArray::Alloc(values.size() * sizeof(T));
    if (bytes.Length() != 0) std::memcpy(bytes.Data(), values.data(), bytes.Length());
    return std::make_shared<BasicVectorSet>(
        bytes, GetEnumValueType<T>(), dimension,
        static_cast<SizeType>(values.size() / static_cast<std::size_t>(dimension)));
}

std::shared_ptr<VectorSet> NativeSelection(
    const std::shared_ptr<VectorSet>& source, const std::vector<std::uint64_t>& ids)
{
    const std::size_t rowSize = static_cast<std::size_t>(source->PerVectorDataSize());
    ByteArray bytes = ByteArray::Alloc(ids.size() * rowSize);
    for (std::size_t row = 0; row < ids.size(); ++row)
    {
        BOOST_REQUIRE(ids[row] < static_cast<std::uint64_t>(source->Count()));
        const void* sample = source->GetVector(static_cast<SizeType>(ids[row]));
        BOOST_REQUIRE(sample != nullptr);
        std::memcpy(bytes.Data() + row * rowSize, sample, rowSize);
    }
    return std::make_shared<BasicVectorSet>(
        bytes, source->GetValueType(), source->Dimension(), static_cast<SizeType>(ids.size()));
}

std::shared_ptr<VectorIndex> NativeTopIndex(
    const std::shared_ptr<VectorSet>& source, IndexAlgoType algorithm = IndexAlgoType::BKT)
{
    auto index = VectorIndex::CreateInstance(algorithm, source->GetValueType());
    BOOST_REQUIRE(index != nullptr);
    const std::pair<const char*, const char*> parameters[] = {
        {"DistCalcMethod", "L2"}, {"NumberOfThreads", "1"},
        {"DataBlockSize", "16"}, {"DataCapacity", "32"},
        {"NeighborhoodSize", "2"}, {"GraphNeighborhoodScale", "1"},
        {"TPTNumber", "1"}, {"RefineIterations", "1"}, {"CEF", "8"},
        {"MaxCheckForRefineGraph", "16"}, {"MaxCheck", "16"}
    };
    for (const auto& parameter : parameters)
        BOOST_REQUIRE(index->SetParameter(parameter.first, parameter.second) == ErrorCode::Success);
    if (algorithm == IndexAlgoType::BKT)
    {
        BOOST_REQUIRE(index->SetParameter("BKTNumber", "1") == ErrorCode::Success);
        BOOST_REQUIRE(index->SetParameter("BKTKmeansK", "2") == ErrorCode::Success);
        BOOST_REQUIRE(index->SetParameter("BKTLeafSize", "16") == ErrorCode::Success);
        BOOST_REQUIRE(index->SetParameter("BKTLambdaFactor", "1") == ErrorCode::Success);
    }
    else
    {
        BOOST_REQUIRE(index->SetParameter("KDTNumber", "1") == ErrorCode::Success);
        BOOST_REQUIRE(index->SetParameter("NumTopDimensionKDTSplit", "1") == ErrorCode::Success);
    }
    BOOST_REQUIRE(index->BuildIndex(
        source->GetData(), source->Count(), source->Dimension(), true, false) == ErrorCode::Success);
    BOOST_REQUIRE(index->IsReady());
    BOOST_REQUIRE_EQUAL(index->GetNumSamples(), source->Count());
    return index;
}

void CheckRows(const std::shared_ptr<VectorSet>& actual, const std::shared_ptr<VectorSet>& expected)
{
    BOOST_REQUIRE(actual != nullptr);
    BOOST_REQUIRE(expected != nullptr);
    BOOST_REQUIRE_EQUAL(actual->Count(), expected->Count());
    BOOST_REQUIRE_EQUAL(actual->Dimension(), expected->Dimension());
    BOOST_REQUIRE(actual->GetValueType() == expected->GetValueType());
    BOOST_REQUIRE_EQUAL(actual->PerVectorDataSize(), expected->PerVectorDataSize());
    for (SizeType row = 0; row < actual->Count(); ++row)
    {
        BOOST_REQUIRE(actual->GetVector(row) != nullptr);
        BOOST_REQUIRE(expected->GetVector(row) != nullptr);
        BOOST_CHECK_EQUAL(std::memcmp(actual->GetVector(row), expected->GetVector(row),
                                      static_cast<std::size_t>(actual->PerVectorDataSize())), 0);
    }
}

struct TinyHierarchy
{
    SamplingMaps maps{{6, 1, 7, 3}, {2, 0}};
    std::shared_ptr<VectorSet> h1;
    std::shared_ptr<VectorSet> h2;
    Catalogs owned;
    std::shared_ptr<VectorIndex> top;

    explicit TinyHierarchy(IndexAlgoType algorithm = IndexAlgoType::BKT) : owned(2)
    {
        std::vector<float> values;
        for (int row = 0; row < 8; ++row)
        {
            values.push_back(static_cast<float>(row) + 0.125f);
            values.push_back(-static_cast<float>(row) - 0.25f);
            values.push_back(-3.0f * static_cast<float>(row));
        }
        h1 = NativeCatalog<float>(3, values);
        h2 = NativeSelection(h1, maps[0]);
        top = NativeTopIndex(NativeSelection(h2, maps[1]), algorithm);
        BOOST_REQUIRE(PackOwnedHierarchyVectors(h1, maps[0], owned[0]) == ErrorCode::Success);
        BOOST_REQUIRE(PackOwnedHierarchyVectors(h2, maps[1], owned[1]) == ErrorCode::Success);
    }

    Catalogs Build() const
    {
        Catalogs views;
        std::string error = "old diagnostic";
        BOOST_REQUIRE_MESSAGE(BuildDisjointHierarchyCatalogs(
            h1->Count(), maps, owned, top, views, &error) == ErrorCode::Success, error);
        BOOST_CHECK(error.empty());
        return views;
    }
};

struct CatalogFile
{
    const std::string path =
        boost::filesystem::unique_path("hierarchy-catalog-test-%%%%-%%%%-%%%%.bin").string();

    CatalogFile() { BOOST_REQUIRE(!std::filesystem::exists(path)); }
    ~CatalogFile()
    {
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
    }
};

void CheckNativeFile(
    const std::string& path, const std::shared_ptr<VectorSet>& rows, int repetitions = 1)
{
    const std::size_t dataSize =
        static_cast<std::size_t>(rows->Count()) * rows->PerVectorDataSize();
    BOOST_REQUIRE_EQUAL(std::filesystem::file_size(path),
                        sizeof(SizeType) + sizeof(DimensionType) + dataSize * repetitions);
    std::ifstream input(path, std::ios::binary);
    BOOST_REQUIRE(input.good());
    SizeType count = -1;
    DimensionType dimension = -1;
    input.read(reinterpret_cast<char*>(&count), sizeof(count));
    input.read(reinterpret_cast<char*>(&dimension), sizeof(dimension));
    BOOST_REQUIRE(input.good());
    BOOST_CHECK_EQUAL(count, rows->Count() * repetitions);
    BOOST_CHECK_EQUAL(dimension, rows->Dimension());
    std::vector<std::uint8_t> data(dataSize);
    for (int repetition = 0; repetition < repetitions && dataSize != 0; ++repetition)
    {
        input.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(data.size()));
        BOOST_REQUIRE(input.good());
        BOOST_CHECK_EQUAL(std::memcmp(data.data(), rows->GetData(), dataSize), 0);
    }
    BOOST_CHECK_EQUAL(input.peek(), std::char_traits<char>::eof());
}

// Fault injection still uses a native BasicVectorSet; no fake index/metadata files.
class FaultingCatalog final : public BasicVectorSet
{
public:
    FaultingCatalog(DimensionType dimension, SizeType count, bool allocationFailure)
        : BasicVectorSet(ByteArray::Alloc(sizeof(float) * dimension * count),
                         VectorValueType::Float, dimension, count),
          m_allocationFailure(allocationFailure)
    {
        std::memset(GetData(), 0, sizeof(float) * dimension * count);
    }

    void* GetVector(SizeType id) const override
    {
        if (id == Count() - 1)
        {
            if (m_allocationFailure) throw std::bad_alloc();
            return nullptr;
        }
        return BasicVectorSet::GetVector(id);
    }

private:
    const bool m_allocationFailure;
};

template <typename T>
void CheckNativePacking()
{
    std::vector<T> values;
    for (int row = 0; row < 7; ++row)
        for (int column = 0; column < 3; ++column)
            values.push_back(static_cast<T>(11 * row + column - 30));
    const auto source = NativeCatalog<T>(3, values);
    std::shared_ptr<VectorSet> packed;
    std::string error = "old diagnostic";
    BOOST_REQUIRE(PackOwnedHierarchyVectors(source, {5, 1, 3}, packed, &error) == ErrorCode::Success);
    BOOST_CHECK(error.empty());
    BOOST_CHECK(std::dynamic_pointer_cast<BasicVectorSet>(packed) != nullptr);
    CheckRows(packed, NativeSelection(source, {0, 2, 4, 6}));
    BOOST_CHECK(packed->GetVector(0) != source->GetVector(0));

    BOOST_REQUIRE(PackOwnedHierarchyVectors(source, {}, packed) == ErrorCode::Success);
    CheckRows(packed, source);
    BOOST_CHECK(packed->GetData() != source->GetData());

    BOOST_REQUIRE(PackOwnedHierarchyVectors(
        source, {6, 0, 4, 2, 5, 1, 3}, packed) == ErrorCode::Success);
    const auto empty = NativeCatalog<T>(3, {});
    CheckRows(packed, empty);
    BOOST_CHECK(!packed->Available());
    BOOST_CHECK(packed->GetVector(0) == nullptr);
    BOOST_CHECK(packed->GetVector(-1) == nullptr);
    BOOST_REQUIRE(PackOwnedHierarchyVectors(empty, {}, packed) == ErrorCode::Success);
    CheckRows(packed, empty);
}

using SupportRequirements = std::unordered_map<std::uint32_t, std::uint32_t>;

SupportRequirements NativeSupportRequirements()
{
    return {{10, 3}, {20, 3}, {30, 2}, {40, 1}, {50, 1}};
}

LimitedTagSupport NativeSupportExpansionBase()
{
    LimitedTagSupport support;
    BOOST_REQUIRE(support.Initialize(5, 2, 3, 1, 3, 9123));
    const std::vector<std::vector<std::uint32_t>> tags{
        {10, 20}, {10, 20}, {20, 30}, {40}};
    for (SizeType head = 0; head < 4; ++head)
    {
        const std::uint32_t attributes[] = {
            static_cast<std::uint32_t>(100 + head), tags[head].front(),
            static_cast<std::uint32_t>(1000 + head)};
        BOOST_REQUIRE(support.SetHeadAttributes(head, attributes, 3));
        BOOST_REQUIRE(support.SetHeadTags(head, tags[head]));
    }
    BOOST_REQUIRE(support.SetTagVectorCounts(
        11, {{10, 4}, {20, 3}, {30, 2}, {40, 1}, {50, 1}}));
    return support;
}

LimitedTagSupport NativeExpandedSupport()
{
    auto support = NativeSupportExpansionBase();
    std::string error;
    BOOST_REQUIRE_MESSAGE(support.ConfigureExpansion(
        {0, 2, 2, 3, 3, 3}, {30, 50, 10}, NativeSupportRequirements(), &error), error);
    BOOST_REQUIRE_MESSAGE(support.Finalize(&error), error);
    return support;
}

std::vector<std::uint8_t> NativeSupportBytes(const std::string& path)
{
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    BOOST_REQUIRE(input.good());
    const auto size = input.tellg();
    BOOST_REQUIRE(size >= 0);
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(size));
    input.seekg(0);
    if (!bytes.empty())
        input.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    BOOST_REQUIRE(input.good());
    return bytes;
}

void WriteNativeSupportBytes(const std::string& path, const std::vector<std::uint8_t>& bytes)
{
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    BOOST_REQUIRE(output.good());
    if (!bytes.empty())
        output.write(reinterpret_cast<const char*>(bytes.data()),
                     static_cast<std::streamsize>(bytes.size()));
    output.close();
    BOOST_REQUIRE(output.good());
}

template <typename T>
T NativeSupportValue(const std::vector<std::uint8_t>& bytes, std::size_t offset)
{
    BOOST_REQUIRE(offset <= bytes.size());
    BOOST_REQUIRE(sizeof(T) <= bytes.size() - offset);
    T value;
    std::memcpy(&value, bytes.data() + offset, sizeof(value));
    return value;
}

template <typename T>
void SetNativeSupportValue(std::vector<std::uint8_t>& bytes, std::size_t offset, const T& value)
{
    BOOST_REQUIRE(offset <= bytes.size());
    BOOST_REQUIRE(sizeof(T) <= bytes.size() - offset);
    std::memcpy(bytes.data() + offset, &value, sizeof(value));
}

std::uint64_t NativeSupportHash(
    const void* data, std::size_t size, std::uint64_t hash = 1469598103934665603ULL)
{
    const auto* bytes = static_cast<const std::uint8_t*>(data);
    for (std::size_t i = 0; i < size; ++i)
    {
        hash ^= bytes[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::uint64_t NativeSupportFingerprint(const std::vector<std::uint8_t>& bytes)
{
    const auto header = NativeSupportValue<LimitedTagSupport::Header>(bytes, 0);
    const std::size_t rowsBytes = static_cast<std::size_t>(header.m_headCount) *
        (header.m_slotsPerHead + header.m_attributeCount) * sizeof(std::uint32_t);
    const std::size_t countsOffset = header.m_headerBytes + rowsBytes;
    const std::size_t countsBytes = header.m_tagCount * sizeof(LimitedTagSupport::TagCountRecord);
    BOOST_REQUIRE(countsOffset <= bytes.size());
    BOOST_REQUIRE(countsBytes <= bytes.size() - countsOffset);
    std::uint64_t hash = 1469598103934665603ULL;
    if (header.m_version == 5)
    {
        const std::uint64_t semantics = 0x5245515549524544ULL;
        hash = NativeSupportHash(&semantics, sizeof(semantics), hash);
    }
    if (header.m_legacyVoteHeadCount == 0)
    {
        const std::uint64_t source = 0x52455441494e4544ULL;
        hash = NativeSupportHash(&source, sizeof(source), hash);
    }
    hash = NativeSupportHash(bytes.data() + header.m_headerBytes, rowsBytes, hash);
    hash = NativeSupportHash(&header.m_vectorCount, sizeof(header.m_vectorCount), hash);
    hash = NativeSupportHash(bytes.data() + countsOffset, countsBytes, hash);
    if (header.m_version == 4 || header.m_version == 5)
    {
        const auto expanded = NativeSupportValue<LimitedTagSupport::HeaderV4>(bytes, 0);
        hash = NativeSupportHash(&expanded.m_extraTagCount, sizeof(expanded.m_extraTagCount), hash);
        hash = NativeSupportHash(&expanded.m_maxExtraSupports, sizeof(expanded.m_maxExtraSupports), hash);
        hash = NativeSupportHash(bytes.data() + countsOffset + countsBytes,
                                 bytes.size() - countsOffset - countsBytes, hash);
    }
    return hash;
}

void UpdateNativeSupportFingerprint(std::vector<std::uint8_t>& bytes)
{
    const auto hash = NativeSupportFingerprint(bytes);
    SetNativeSupportValue(bytes, offsetof(LimitedTagSupport::Header, m_bodyFingerprint), hash);
}
} // namespace

BOOST_AUTO_TEST_SUITE(HierarchyCatalogTest)

BOOST_AUTO_TEST_CASE(PreservesLogicalRowsAndUniquePhysicalOwnership)
{
    for (const auto algorithm : {IndexAlgoType::BKT, IndexAlgoType::KDT})
    {
        BOOST_TEST_CONTEXT("native algorithm " << static_cast<int>(algorithm))
        {
            TinyHierarchy hierarchy(algorithm);
            const auto originalMaps = hierarchy.maps;
            auto views = hierarchy.Build();
            BOOST_REQUIRE_EQUAL(views.size(), 3U);
            CheckRows(views[0], hierarchy.h1);
            CheckRows(views[1], hierarchy.h2);
            CheckRows(views[2], NativeSelection(hierarchy.h2, hierarchy.maps[1]));
            BOOST_CHECK(hierarchy.maps == originalMaps);

            std::set<const void*> physicalRows;
            SizeType ownedCount = hierarchy.top->GetNumSamples();
            for (const auto& catalog : hierarchy.owned)
            {
                ownedCount += catalog->Count();
                for (SizeType row = 0; row < catalog->Count(); ++row)
                    BOOST_CHECK(physicalRows.insert(catalog->GetVector(row)).second);
            }
            for (SizeType row = 0; row < hierarchy.top->GetNumSamples(); ++row)
            {
                const void* sample = hierarchy.top->GetSample(row);
                BOOST_CHECK(physicalRows.insert(sample).second);
                BOOST_CHECK(views.back()->GetVector(row) == sample);
            }
            BOOST_CHECK_EQUAL(ownedCount, hierarchy.h1->Count());
            BOOST_CHECK_EQUAL(physicalRows.size(), static_cast<std::size_t>(hierarchy.h1->Count()));

            std::set<const void*> logicalRows;
            for (const auto& view : views)
            {
                BOOST_CHECK(view->Available());
                BOOST_CHECK(view->GetVector(-1) == nullptr);
                BOOST_CHECK(view->GetVector(view->Count()) == nullptr);
                BOOST_CHECK(view->GetVector((std::numeric_limits<SizeType>::max)()) == nullptr);
                for (SizeType row = 0; row < view->Count(); ++row)
                {
                    const void* sample = view->GetVector(row);
                    BOOST_CHECK_EQUAL(physicalRows.count(sample), 1U);
                    logicalRows.insert(sample);
                }
            }
            BOOST_CHECK(logicalRows == physicalRows);
            for (std::size_t level = 0; level < hierarchy.maps.size(); ++level)
            {
                for (std::size_t upper = 0; upper < hierarchy.maps[level].size(); ++upper)
                    BOOST_CHECK(views[level]->GetVector(
                        static_cast<SizeType>(hierarchy.maps[level][upper])) ==
                        views[level + 1]->GetVector(static_cast<SizeType>(upper)));
            }
            BOOST_CHECK(views[0]->GetVector(7) == hierarchy.top->GetSample(0));
            BOOST_CHECK(views[0]->GetVector(6) == hierarchy.top->GetSample(1));
            const SizeType h1OwnedIDs[] = {0, 2, 4, 5};
            for (SizeType packed = 0; packed < 4; ++packed)
                BOOST_CHECK(views[0]->GetVector(h1OwnedIDs[packed]) ==
                            hierarchy.owned[0]->GetVector(packed));
            BOOST_CHECK(views[1]->GetVector(1) == hierarchy.owned[1]->GetVector(0));
            BOOST_CHECK(views[1]->GetVector(3) == hierarchy.owned[1]->GetVector(1));

            QueryResult query(hierarchy.top->GetSample(0), 1, false);
            BOOST_REQUIRE(hierarchy.top->SearchIndex(query) == ErrorCode::Success);
            BOOST_CHECK_EQUAL(query.GetResult(0)->VID, 0);
        }
    }
}

BOOST_AUTO_TEST_CASE(RetainsOwnersAndLocatorsUntilTheLastLowerViewDies)
{
    TinyHierarchy hierarchy;
    auto views = hierarchy.Build();
    const SizeType count = views[0]->Count();
    const std::size_t rowSize = static_cast<std::size_t>(views[0]->PerVectorDataSize());
    std::vector<std::uint8_t> expected(static_cast<std::size_t>(count) * rowSize);
    std::memcpy(expected.data(), hierarchy.h1->GetData(), expected.size());
    std::vector<const void*> pointers;
    for (SizeType row = 0; row < count; ++row) pointers.push_back(views[0]->GetVector(row));
    std::weak_ptr<VectorIndex> topOwner = hierarchy.top;
    std::weak_ptr<VectorSet> h1Owner = hierarchy.owned[0];
    std::weak_ptr<VectorSet> h2Owner = hierarchy.owned[1];
    std::weak_ptr<VectorSet> middleView = views[1];
    std::weak_ptr<VectorSet> topView = views[2];
    auto lowest = views.front();
    views.clear();
    hierarchy.top.reset();
    hierarchy.owned.clear();
    hierarchy.h1.reset();
    hierarchy.h2.reset();
    hierarchy.maps.clear();

    BOOST_CHECK(!topOwner.expired());
    BOOST_CHECK(!h1Owner.expired());
    BOOST_CHECK(!h2Owner.expired());
    BOOST_CHECK(!middleView.expired());
    BOOST_CHECK(!topView.expired());
    for (SizeType row = 0; row < count; ++row)
    {
        BOOST_CHECK(lowest->GetVector(row) == pointers[static_cast<std::size_t>(row)]);
        BOOST_CHECK_EQUAL(std::memcmp(lowest->GetVector(row),
            expected.data() + static_cast<std::size_t>(row) * rowSize, rowSize), 0);
    }
    lowest.reset();
    BOOST_CHECK(topOwner.expired());
    BOOST_CHECK(h1Owner.expired());
    BOOST_CHECK(h2Owner.expired());
    BOOST_CHECK(middleView.expired());
    BOOST_CHECK(topView.expired());
}

BOOST_AUTO_TEST_CASE(SupportsZeroOwnedLowerAndIntermediateLayersAndTopOnly)
{
    for (const SizeType count : {1, 2})
    {
        const auto source = count == 1
            ? NativeCatalog<std::int16_t>(2, {17, -31})
            : NativeCatalog<std::int16_t>(2, {17, -31, 29, -43});
        const std::vector<std::uint64_t> map = count == 1
            ? std::vector<std::uint64_t>{0} : std::vector<std::uint64_t>{1, 0};
        const auto top = NativeTopIndex(source);
        std::shared_ptr<VectorSet> empty;
        BOOST_REQUIRE(PackOwnedHierarchyVectors(source, map, empty) == ErrorCode::Success);
        BOOST_REQUIRE_EQUAL(empty->Count(), 0);
        Catalogs views;
        BOOST_REQUIRE(BuildDisjointHierarchyCatalogs(
            count, {map, map}, {empty, empty}, top, views) == ErrorCode::Success);
        BOOST_REQUIRE_EQUAL(views.size(), 3U);
        CheckRows(views[0], source);
        CheckRows(views[1], NativeSelection(source, map));
        CheckRows(views[2], source);
        for (const auto& view : views)
        {
            BOOST_CHECK(view->Available());
            BOOST_CHECK_EQUAL(view->Count(), count);
        }
        for (SizeType row = 0; row < count; ++row)
        {
            BOOST_CHECK(views[0]->GetVector(row) == top->GetSample(row));
            BOOST_CHECK(views[1]->GetVector(row) ==
                        top->GetSample(static_cast<SizeType>(map[static_cast<std::size_t>(row)])));
        }
        BOOST_REQUIRE(BuildDisjointHierarchyCatalogs(
            count, {}, {}, top, views) == ErrorCode::Success);
        BOOST_REQUIRE_EQUAL(views.size(), 1U);
        CheckRows(views.front(), source);
        BOOST_CHECK(views.front()->GetVector(0) == top->GetSample(0));
    }
}

BOOST_AUTO_TEST_CASE(PacksNativeTypesInLogicalOrderWithoutChangingBytes)
{
    CheckNativePacking<std::int8_t>();
    CheckNativePacking<std::uint8_t>();
    CheckNativePacking<std::int16_t>();
    CheckNativePacking<float>();

    const std::uint32_t bits[] = {
        0x80000000U, 0x7fc12345U, 0x3f800000U, 0x00000000U,
        0x7f800000U, 0xff800000U, 0x3f000000U, 0xbf000000U,
        0x00000001U, 0x80000001U, 0x40000000U, 0xc0000000U
    };
    ByteArray bytes = ByteArray::Alloc(sizeof(bits));
    std::memcpy(bytes.Data(), bits, sizeof(bits));
    auto source = std::make_shared<BasicVectorSet>(bytes, VectorValueType::Float, 2, 6);
    std::shared_ptr<VectorSet> packed;
    BOOST_REQUIRE(PackOwnedHierarchyVectors(source, {5, 1, 3}, packed) == ErrorCode::Success);
    CheckRows(packed, NativeSelection(source, {0, 2, 4}));
}

BOOST_AUTO_TEST_CASE(OwnedCatalogsSaveAndAppendInNativeFormatIncludingEmptyRows)
{
    const auto source = NativeCatalog<std::int16_t>(2, {1, 2, 3, 4, 5, 6, 7, 8, 9, 10});
    std::shared_ptr<VectorSet> packed;
    BOOST_REQUIRE(PackOwnedHierarchyVectors(source, {3, 1}, packed) == ErrorCode::Success);
    CatalogFile file;
    BOOST_REQUIRE(packed->Save(file.path) == ErrorCode::Success);
    CheckNativeFile(file.path, packed);
    BOOST_REQUIRE(packed->AppendSave(file.path) == ErrorCode::Success);
    CheckNativeFile(file.path, packed, 2);

    std::shared_ptr<VectorSet> empty;
    BOOST_REQUIRE(PackOwnedHierarchyVectors(
        source, {4, 2, 0, 3, 1}, empty) == ErrorCode::Success);
    BOOST_REQUIRE(empty->Save(file.path) == ErrorCode::Success);
    CheckNativeFile(file.path, empty);
    BOOST_REQUIRE(empty->AppendSave(file.path) == ErrorCode::Success);
    CheckNativeFile(file.path, empty);
}

BOOST_AUTO_TEST_CASE(ViewsRejectContiguousAccessMutationAndSerialization)
{
    TinyHierarchy hierarchy;
    const auto views = hierarchy.Build();
    CatalogFile file;
    for (const auto& view : views)
    {
        const void* first = view->GetVector(0);
        BOOST_CHECK_THROW(view->GetData(), std::logic_error);
        BOOST_CHECK_THROW(view->Normalize(1), std::logic_error);
        BOOST_CHECK(view->Save(file.path) == ErrorCode::Undefined);
        BOOST_CHECK(view->AppendSave(file.path) == ErrorCode::Undefined);
        BOOST_CHECK(!std::filesystem::exists(file.path));
        BOOST_CHECK(view->GetVector(0) == first);
    }
    CheckRows(views[0], hierarchy.h1);
    CheckRows(views[1], hierarchy.h2);
    CheckRows(views[2], NativeSelection(hierarchy.h2, hierarchy.maps[1]));
}

BOOST_AUTO_TEST_CASE(PacksLogicalViewsAndSupportsAliasedOutputs)
{
    TinyHierarchy hierarchy;
    const auto views = hierarchy.Build();
    for (std::size_t level = 0; level < hierarchy.owned.size(); ++level)
    {
        auto inputAndOutput = views[level];
        BOOST_REQUIRE(PackOwnedHierarchyVectors(
            inputAndOutput, hierarchy.maps[level], inputAndOutput) == ErrorCode::Success);
        CheckRows(inputAndOutput, hierarchy.owned[level]);
        BOOST_CHECK(std::dynamic_pointer_cast<BasicVectorSet>(inputAndOutput) != nullptr);
        BOOST_CHECK(inputAndOutput->GetVector(0) != hierarchy.owned[level]->GetVector(0));
    }
    auto inputAndOutput = hierarchy.owned;
    BOOST_REQUIRE(BuildDisjointHierarchyCatalogs(
        hierarchy.h1->Count(), hierarchy.maps, inputAndOutput, hierarchy.top,
        inputAndOutput) == ErrorCode::Success);
    BOOST_REQUIRE_EQUAL(inputAndOutput.size(), 3U);
    CheckRows(inputAndOutput[0], hierarchy.h1);
    CheckRows(inputAndOutput[1], hierarchy.h2);
    BOOST_CHECK(inputAndOutput[2]->GetVector(0) == hierarchy.top->GetSample(0));
}

BOOST_AUTO_TEST_CASE(RejectsInvalidSamplingMapsWithoutReplacingViews)
{
    TinyHierarchy hierarchy;
    auto views = hierarchy.Build();
    const auto original = views;
    const auto reject = [&](const SamplingMaps& maps)
    {
        std::string error;
        BOOST_CHECK(BuildDisjointHierarchyCatalogs(
            8, maps, hierarchy.owned, hierarchy.top, views, &error) == ErrorCode::FailedParseValue);
        BOOST_CHECK(!error.empty());
        BOOST_CHECK(views == original);
    };
    for (std::size_t level = 0; level < hierarchy.maps.size(); ++level)
    {
        auto maps = hierarchy.maps;
        maps[level][1] = maps[level][0];
        reject(maps);
        maps = hierarchy.maps;
        maps[level][0] = level == 0 ? 8 : 4;
        reject(maps);
        maps[level][0] = (std::numeric_limits<std::uint64_t>::max)();
        reject(maps);
    }
    auto maps = hierarchy.maps;
    maps[0] = {0, 1, 2, 3, 4, 5, 6, 7, 8};
    reject(maps);
    maps = hierarchy.maps;
    maps[0].clear();
    reject(maps);
    CheckRows(views[0], hierarchy.h1);
}

BOOST_AUTO_TEST_CASE(RejectsInconsistentNativeCatalogsAndTopGraph)
{
    TinyHierarchy hierarchy;
    auto views = hierarchy.Build();
    const auto original = views;
    const auto reject = [&](SizeType count, const SamplingMaps& maps, const Catalogs& owned,
                            const std::shared_ptr<VectorIndex>& top, ErrorCode expected)
    {
        std::string error;
        BOOST_CHECK(BuildDisjointHierarchyCatalogs(
            count, maps, owned, top, views, &error) == expected);
        BOOST_CHECK(!error.empty());
        BOOST_CHECK(views == original);
    };
    reject(0, hierarchy.maps, hierarchy.owned, hierarchy.top, ErrorCode::FailedParseValue);
    reject(-1, hierarchy.maps, hierarchy.owned, hierarchy.top, ErrorCode::FailedParseValue);
    reject(8, hierarchy.maps, {}, hierarchy.top, ErrorCode::FailedParseValue);
    reject(8, {}, hierarchy.owned, hierarchy.top, ErrorCode::FailedParseValue);
    reject(8, hierarchy.maps, hierarchy.owned, nullptr, ErrorCode::LackOfInputs);
    reject(3, {}, {}, hierarchy.top, ErrorCode::FailedParseValue);
    reject(8, hierarchy.maps, hierarchy.owned,
           VectorIndex::CreateInstance(IndexAlgoType::BKT, VectorValueType::Float), ErrorCode::EmptyIndex);

    auto owned = hierarchy.owned;
    owned[0].reset();
    reject(8, hierarchy.maps, owned, hierarchy.top, ErrorCode::LackOfInputs);
    owned[0] = NativeCatalog<float>(2, std::vector<float>(8, 1.0f));
    reject(8, hierarchy.maps, owned, hierarchy.top, ErrorCode::DimensionSizeMismatch);
    owned[0] = NativeCatalog<std::uint8_t>(3, std::vector<std::uint8_t>(12, 1));
    reject(8, hierarchy.maps, owned, hierarchy.top, ErrorCode::FailedParseValue);
    owned[0] = NativeCatalog<float>(3, std::vector<float>(9, 1.0f));
    reject(8, hierarchy.maps, owned, hierarchy.top, ErrorCode::FailedParseValue);
    owned[0] = views[1];
    reject(8, hierarchy.maps, owned, hierarchy.top, ErrorCode::FailedParseValue);
    owned[0] = std::make_shared<BasicVectorSet>(ByteArray::c_empty, VectorValueType::Float, 3, 4);
    reject(8, hierarchy.maps, owned, hierarchy.top, ErrorCode::VectorNotFound);
    owned[0] = std::make_shared<FaultingCatalog>(3, 4, false);
    reject(8, hierarchy.maps, owned, hierarchy.top, ErrorCode::VectorNotFound);
    owned[0] = std::make_shared<FaultingCatalog>(3, 4, true);
    reject(8, hierarchy.maps, owned, hierarchy.top, ErrorCode::MemoryOverFlow);
}

BOOST_AUTO_TEST_CASE(PackingRejectsInvalidIDsShapesMissingRowsAndAllocationFailures)
{
    auto source = NativeCatalog<float>(2, {1, 2, 3, 4, 5, 6});
    auto output = NativeCatalog<float>(2, {17, 19});
    const auto original = output;
    const auto reject = [&](const std::shared_ptr<VectorSet>& input,
                            const std::vector<std::uint64_t>& ids, ErrorCode expected)
    {
        std::string error;
        BOOST_CHECK(PackOwnedHierarchyVectors(input, ids, output, &error) == expected);
        BOOST_CHECK(!error.empty());
        BOOST_CHECK(output == original);
    };
    reject(nullptr, {}, ErrorCode::LackOfInputs);
    reject(source, {1, 1}, ErrorCode::FailedParseValue);
    reject(source, {3}, ErrorCode::FailedParseValue);
    reject(source, {(std::numeric_limits<std::uint64_t>::max)()}, ErrorCode::FailedParseValue);
    reject(source, {0, 1, 2, 0}, ErrorCode::FailedParseValue);
    reject(std::make_shared<BasicVectorSet>(
        ByteArray::c_empty, VectorValueType::Float, 2, -1), {}, ErrorCode::FailedParseValue);
    reject(std::make_shared<BasicVectorSet>(
        ByteArray::c_empty, VectorValueType::Undefined, 2, 0), {}, ErrorCode::FailedParseValue);
    reject(std::make_shared<BasicVectorSet>(
        ByteArray::c_empty, VectorValueType::Float, 0, 0), {}, ErrorCode::DimensionSizeMismatch);
    reject(std::make_shared<BasicVectorSet>(
        ByteArray::c_empty, VectorValueType::Float,
        (std::numeric_limits<DimensionType>::max)(), 0), {}, ErrorCode::MemoryOverFlow);
    reject(std::make_shared<BasicVectorSet>(
        ByteArray::c_empty, VectorValueType::Float, 2, 3), {}, ErrorCode::VectorNotFound);
    reject(std::make_shared<FaultingCatalog>(2, 3, false), {2}, ErrorCode::VectorNotFound);
    reject(std::make_shared<FaultingCatalog>(2, 3, true), {2}, ErrorCode::MemoryOverFlow);
}

BOOST_AUTO_TEST_CASE(MaterializationRestoresEveryIndependentLocalRow)
{
    TinyHierarchy hierarchy;
    const auto legacy = hierarchy.Build();
    Catalogs full(2);
    for (std::size_t level = 0; level < full.size(); ++level)
    {
        BOOST_REQUIRE(MaterializeHierarchyCatalog(legacy[level], full[level]) == ErrorCode::Success);
        CheckRows(full[level], legacy[level]);
        BOOST_REQUIRE(std::dynamic_pointer_cast<BasicVectorSet>(full[level]) != nullptr);
        const auto* data = static_cast<const std::uint8_t*>(full[level]->GetData());
        for (SizeType row = 0; row < full[level]->Count(); ++row)
        {
            BOOST_CHECK(full[level]->GetVector(row) ==
                data + static_cast<std::size_t>(row) * full[level]->PerVectorDataSize());
            BOOST_CHECK(full[level]->GetVector(row) != legacy[level]->GetVector(row));
        }
    }
    Catalogs independent;
    BOOST_REQUIRE(BuildIndependentHierarchyCatalogs(
        hierarchy.h1->Count(), hierarchy.maps, full, hierarchy.top, independent) == ErrorCode::Success);
    BOOST_REQUIRE_EQUAL(independent.size(), 3U);
    BOOST_CHECK_EQUAL(independent[0]->Count(), 8);
    BOOST_CHECK_EQUAL(independent[1]->Count(), 4);
    BOOST_CHECK_EQUAL(independent[2]->Count(), 2);
    for (SizeType row = 0; row < hierarchy.top->GetNumSamples(); ++row)
        BOOST_CHECK(independent.back()->GetVector(row) == hierarchy.top->GetSample(row));
    for (std::size_t level = 0; level < hierarchy.maps.size(); ++level)
        for (std::size_t row = 0; row < hierarchy.maps[level].size(); ++row)
            BOOST_CHECK(independent[level]->GetVector(static_cast<SizeType>(hierarchy.maps[level][row])) !=
                independent[level + 1]->GetVector(static_cast<SizeType>(row)));
    const auto retained = independent;
    auto badMaps = hierarchy.maps;
    badMaps[0][1] = badMaps[0][0];
    BOOST_CHECK(BuildIndependentHierarchyCatalogs(
        hierarchy.h1->Count(), badMaps, full, hierarchy.top, independent) != ErrorCode::Success);
    BOOST_CHECK(independent == retained);
    BOOST_CHECK(BuildIndependentHierarchyCatalogs(
        hierarchy.h1->Count(), hierarchy.maps, hierarchy.owned, hierarchy.top, independent) !=
        ErrorCode::Success);
    BOOST_CHECK(independent == retained);
    auto wrongShape = full;
    wrongShape[1] = NativeCatalog<float>(1, std::vector<float>(4, 0.0f));
    BOOST_CHECK(BuildIndependentHierarchyCatalogs(
        hierarchy.h1->Count(), hierarchy.maps, wrongShape, hierarchy.top, independent) !=
        ErrorCode::Success);
    BOOST_CHECK(independent == retained);
    wrongShape[1] = nullptr;
    BOOST_CHECK(BuildIndependentHierarchyCatalogs(
        hierarchy.h1->Count(), hierarchy.maps, wrongShape, hierarchy.top, independent) !=
        ErrorCode::Success);
    BOOST_CHECK(independent == retained);
    auto* upperByte = static_cast<std::uint8_t*>(full[1]->GetVector(1));
    *upperByte ^= 1U;
    BOOST_CHECK(BuildIndependentHierarchyCatalogs(
        hierarchy.h1->Count(), hierarchy.maps, full, hierarchy.top, independent) != ErrorCode::Success);
    BOOST_CHECK(independent == retained);
    *upperByte ^= 1U;
    auto* topByte = const_cast<std::uint8_t*>(
        static_cast<const std::uint8_t*>(hierarchy.top->GetSample(0)));
    *topByte ^= 1U;
    BOOST_CHECK(BuildIndependentHierarchyCatalogs(
        hierarchy.h1->Count(), hierarchy.maps, full, hierarchy.top, independent) != ErrorCode::Success);
    BOOST_CHECK(independent == retained);
    *topByte ^= 1U;
    BOOST_REQUIRE(BuildIndependentHierarchyCatalogs(
        hierarchy.h1->Count(), hierarchy.maps, full, hierarchy.top, independent) == ErrorCode::Success);
}

BOOST_AUTO_TEST_CASE(FullNativeCatalogRejectsTruncationAndTrailingBytes)
{
    TinyHierarchy hierarchy;
    const auto legacy = hierarchy.Build();
    std::shared_ptr<VectorSet> full;
    BOOST_REQUIRE(MaterializeHierarchyCatalog(legacy.front(), full) == ErrorCode::Success);
    CatalogFile file;
    BOOST_REQUIRE(full->Save(file.path) == ErrorCode::Success);
    std::shared_ptr<VectorSet> loaded;
    BOOST_REQUIRE(LoadHierarchyVectorCatalog(
        file.path, VectorValueType::Float, full->Dimension(), full->Count(), loaded) ==
        ErrorCode::Success);
    CheckRows(loaded, full);
    const auto preserved = loaded;
    BOOST_CHECK(LoadHierarchyVectorCatalog(
        file.path, VectorValueType::Float, full->Dimension(), full->Count() - 1, loaded) !=
        ErrorCode::Success);
    BOOST_CHECK(loaded == preserved);
    const auto bytes = std::filesystem::file_size(file.path);
    std::filesystem::resize_file(file.path, bytes - 1);
    BOOST_CHECK(LoadHierarchyVectorCatalog(
        file.path, VectorValueType::Float, full->Dimension(), full->Count(), loaded) !=
        ErrorCode::Success);
    BOOST_REQUIRE(full->Save(file.path) == ErrorCode::Success);
    {
        std::ofstream output(file.path, std::ios::binary | std::ios::app);
        output.put('\0');
    }
    BOOST_CHECK(LoadHierarchyVectorCatalog(
        file.path, VectorValueType::Float, full->Dimension(), full->Count(), loaded) !=
        ErrorCode::Success);
    BOOST_CHECK(loaded == preserved);
}

BOOST_AUTO_TEST_CASE(HeadMetadataAdoptsPayloadWithoutCopy)
{
    const auto data = NativeCatalog<float>(2, {1.0f, 0.0f, 0.0f, 2.0f});
    const auto source = NativeTopIndex(data);
    const auto target = NativeTopIndex(data, IndexAlgoType::KDT);
    Cache::HierWidthTable widths;
    const int bits[Cache::HIER_LEVELS] = {64, 128, 256, 128, 64};
    widths.Set(bits, Cache::HIER_LEVELS);
    for (bool tails : {false, true})
    {
        for (int quantCols : {0, 2})
        {
            source->InitializeHeadNodeMeta(2, quantCols, widths, tails);
            BOOST_CHECK_EQUAL(source->GetHeadNodeGlobalVID(0), MaxSize);
            BOOST_CHECK_EQUAL(source->GetHeadNodeBundleNodeId(1), -1);
            source->SetHeadNodeGlobalVID(0, 101);
            source->SetHeadNodeGlobalVID(1, 202);
            source->SetHeadNodeBundleNodeId(1, 7);
            source->SetHeadNodeHeadOnly(0, true);
            Cache::PostingBitmask pure, tail;
            pure.Insert(17);
            tail.Insert(93);
            source->SetHeadNodePS(0, pure);
            if (tails) source->SetHeadNodeTailPS(1, tail);
            if (quantCols > 0)
            {
                auto* numeric = source->GetHeadNodeNumQuantMutable(0);
                BOOST_REQUIRE(numeric != nullptr);
                numeric[0] = 0x123456789ULL;
            }
            const auto expected = source->GetHeadNodeMetaBlob();
            auto payload = expected;
            payload.reserve(payload.size() + 64);
            const auto* storage = payload.data();
            const auto capacity = payload.capacity();
            target->InitializeHeadNodeMeta(2, 3, Cache::HierWidthTable(), !tails);
            target->SetHeadNodeOwnTagsAvailable(true);
            target->SetHeadNodePostingHierMasksAvailable(true);
            target->SetHeadNodeNumericDomainFingerprint(123);
            BOOST_REQUIRE(target->AdoptHeadNodeMeta(
                2, quantCols, widths, tails, std::move(payload)));
            BOOST_CHECK(target->GetHeadNodeMetaBlob().data() == storage);
            BOOST_CHECK_EQUAL(target->GetHeadNodeMetaBlob().capacity(), capacity);
            BOOST_CHECK(target->GetHeadNodeMetaBlob() == expected);
            BOOST_CHECK(payload.empty());
            BOOST_CHECK_EQUAL(target->GetHeadNodeMetaSampleCount(), 2);
            BOOST_CHECK_EQUAL(target->GetHeadNodeMetaStride(), source->GetHeadNodeMetaStride());
            BOOST_CHECK_EQUAL(target->GetHeadNodeNumQuantCols(), quantCols);
            BOOST_CHECK_EQUAL(target->HasHeadNodeTailPS(), tails);
            BOOST_CHECK_EQUAL(target->GetHeadNodeGlobalVID(0), 101);
            BOOST_CHECK_EQUAL(target->GetHeadNodeGlobalVID(1), 202);
            BOOST_CHECK_EQUAL(target->GetHeadNodeBundleNodeId(1), 7);
            BOOST_CHECK(target->IsHeadNodeHeadOnly(0));
            BOOST_CHECK(!target->HasHeadNodeOwnTags());
            BOOST_CHECK(!target->HasHeadNodePostingHierMasks());
            BOOST_CHECK_EQUAL(target->GetHeadNodeNumericDomainFingerprint(), 0U);
            const auto adoptedWidths = target->GetHeadNodeHierWidths();
            for (int level = 0; level < Cache::HIER_LEVELS; ++level)
                BOOST_CHECK_EQUAL(adoptedWidths.bits[level], widths.bits[level]);

            std::vector<std::uint8_t> truncated(expected.begin(), expected.end() - 1);
            const auto* rejectedStorage = truncated.data();
            BOOST_CHECK(!target->AdoptHeadNodeMeta(
                2, quantCols, widths, tails, std::move(truncated)));
            BOOST_CHECK(!target->AdoptHeadNodeMeta(
                0, quantCols, widths, tails, std::move(truncated)));
            BOOST_CHECK(!target->AdoptHeadNodeMeta(
                -1, quantCols, widths, tails, std::move(truncated)));
            BOOST_CHECK(!target->AdoptHeadNodeMeta(
                MaxSize, quantCols, widths, tails, std::move(truncated)));
            BOOST_CHECK(truncated.data() == rejectedStorage);
            BOOST_CHECK(!target->AdoptHeadNodeMeta(
                2, quantCols, widths, tails, std::move(target->GetHeadNodeMetaBlob())));
            BOOST_CHECK(target->GetHeadNodeMetaBlob().data() == storage);
            BOOST_CHECK(target->GetHeadNodeMetaBlob() == expected);
            BOOST_CHECK_EQUAL(target->GetHeadNodeGlobalVID(1), 202);
        }
    }
}

BOOST_AUTO_TEST_CASE(H1SignaturesCoverPureAndOwnKeyWithoutTail)
{
    const auto data = NativeCatalog<float>(2, {1.0f, 0.0f, 0.0f, 2.0f});
    const auto head = NativeTopIndex(data);
    head->InitializeHeadNodeMeta(2, 0, Cache::HierWidthTable(), true);
    head->SetHeadNodeOwnTagsAvailable(true);
    Cache::PostingBitmask pure, tail;
    pure.Insert(27);
    pure.Insert(111);
    tail.Insert(93);
    head->SetHeadNodePS(0, pure);
    head->SetHeadNodeTailPS(0, tail);
    std::vector<Cache::PostingBitmask> signatures;
    LimitedTagSupport support;
    for (int keyColumn : {0, Cache::HIER_LEVELS})
    {
        BOOST_REQUIRE(support.Initialize(2, 2, 1, keyColumn, keyColumn + 1, 9123));
        for (SizeType row = 0; row < 2; ++row)
        {
            head->SetHeadNodeGlobalVID(row, row + 20);
            Cache::HierarchicalOwnTags own;
            own.Insert(keyColumn, 17 + row);
            head->SetHeadNodeHierMask(row, own);
            std::vector<std::uint32_t> attributes(static_cast<size_t>(keyColumn) + 1, 111);
            attributes[static_cast<size_t>(keyColumn)] = 17 + row;
            BOOST_REQUIRE(support.SetHeadAttributes(row, attributes.data(), keyColumn + 1));
            BOOST_REQUIRE(support.SetHeadTags(row, {static_cast<std::uint32_t>(17 + row), 27}));
        }
        BOOST_REQUIRE(CollectHierarchyHeadSignatures(*head, support, signatures));
        BOOST_REQUIRE_EQUAL(signatures.size(), 2U);
        BOOST_CHECK(signatures[0].MayContain(17));
        BOOST_CHECK(signatures[0].MayContain(27));
        BOOST_CHECK(!signatures[0].MayContain(93));
        BOOST_CHECK(!signatures[0].MayContain(111));
        BOOST_CHECK(signatures[1].MayContain(18));
        BOOST_CHECK_EQUAL(signatures[1].Popcount(), 1);
        Cache::PostingBitmask parent;
        for (const auto& signature : signatures) parent.MergeOR(signature);
        for (std::uint32_t tag : {17U, 18U, 27U}) BOOST_CHECK(parent.MayContain(tag));
        BOOST_CHECK(!parent.MayContain(93));
    }

    CatalogFile file;
    BOOST_REQUIRE(SaveHeadNodeMetadataV8(file.path, head, 9123));
    const auto blob = head->GetHeadNodeMetaBlob();
    head->ClearHeadNodeMeta();
    BOOST_REQUIRE(LoadHeadNodeMetadataV8(file.path, head, 2, 9123,
        [](SizeType row) { return row + 20; }));
    BOOST_CHECK(head->GetHeadNodeMetaBlob() == blob);
    BOOST_REQUIRE(CollectHierarchyHeadSignatures(*head, support, signatures));
    BOOST_CHECK(signatures[1].MayContain(18));
    BOOST_CHECK(!LoadHeadNodeMetadataV8(file.path, head, 2, 9124,
        [](SizeType row) { return row + 20; }));
    BOOST_CHECK(head->GetHeadNodeMetaBlob() == blob);
    BOOST_CHECK(!LoadHeadNodeMetadataV8(file.path, head, 2, 9123,
        [](SizeType row) { return row + 21; }));
    BOOST_CHECK(!head->HasHeadNodeMeta());
    BOOST_REQUIRE(LoadHeadNodeMetadataV8(file.path, head, 2, 9123,
        [](SizeType row) { return row + 20; }));
    {
        std::fstream bytes(file.path, std::ios::binary | std::ios::in | std::ios::out);
        bytes.seekg(-1, std::ios::end);
        char value = 0;
        bytes.read(&value, 1);
        value ^= 1;
        bytes.seekp(-1, std::ios::end);
        bytes.write(&value, 1);
        BOOST_REQUIRE(bytes.good());
    }
    BOOST_CHECK(!LoadHeadNodeMetadataV8(file.path, head, 2, 9123,
        [](SizeType row) { return row + 20; }));
    BOOST_CHECK(head->GetHeadNodeMetaBlob() == blob);
}

BOOST_AUTO_TEST_CASE(LimitedSupportRetainedSourceAndLegacyProvenanceRoundTrip)
{
    using Header = LimitedTagSupport::Header;
    using Record = LimitedTagSupport::TagCountRecord;
    BOOST_CHECK_EQUAL(sizeof(Header), 64U);
    BOOST_CHECK_EQUAL(sizeof(LimitedTagSupport::HeaderV4), 80U);
    BOOST_CHECK_EQUAL(Header().m_version, 3U);
    BOOST_CHECK_EQUAL(LimitedTagSupport::HeaderV4().m_base.m_version, 4U);

    LimitedTagSupport support;
    BOOST_REQUIRE(support.Initialize(3, 2, 3, 0, 1, 9123));
    const std::uint32_t tags[] = {10, 20, 20, 10, 10, 20};
    const std::uint32_t attributes[] = {10, 20, 10};
    for (SizeType head = 0; head < 3; ++head)
    {
        BOOST_REQUIRE(support.SetHeadTags(head, {tags[2 * head], tags[2 * head + 1]}));
        BOOST_REQUIRE(support.SetHeadAttributes(head, attributes + head, 1));
    }
    BOOST_REQUIRE(support.SetTagVectorCounts(3, {{10, 2}, {20, 1}}));
    CatalogFile file;
    BOOST_REQUIRE(support.Save(file.path));
    BOOST_CHECK(!support.HasExpansion());
    BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 0U);
    BOOST_CHECK_EQUAL(support.LegacyExtraSupportCap(), 0U);
    BOOST_CHECK_EQUAL(support.ExtraTagCount(0), 0U);
    BOOST_CHECK(support.ExtraTagData(0) == nullptr);
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(20), 3U);

    Header header;
    header.m_headCount = 3;
    header.m_slotsPerHead = 2;
    header.m_legacyVoteHeadCount = 0;
    header.m_minHeadCount = 3;
    header.m_tagCount = 2;
    header.m_attributeCount = 1;
    header.m_vectorCount = 3;
    header.m_generationFingerprint = 9123;
    std::vector<std::uint8_t> expected(sizeof(Header) + sizeof(tags) + sizeof(attributes) + 2 * sizeof(Record));
    SetNativeSupportValue(expected, 0, header);
    std::memcpy(expected.data() + sizeof(Header), tags, sizeof(tags));
    std::memcpy(expected.data() + sizeof(Header) + sizeof(tags), attributes, sizeof(attributes));
    const std::size_t countsOffset = sizeof(Header) + sizeof(tags) + sizeof(attributes);
    SetNativeSupportValue(expected, countsOffset, Record{10, 0, 2});
    SetNativeSupportValue(expected, countsOffset + sizeof(Record), Record{20, 0, 1});
    UpdateNativeSupportFingerprint(expected);
    BOOST_CHECK(NativeSupportBytes(file.path) == expected);
    BOOST_CHECK_EQUAL(support.ContentFingerprint(), NativeSupportFingerprint(expected));

    LimitedTagSupport loaded;
    BOOST_REQUIRE(loaded.Load(file.path, 3, 2, 3, 0, 1, 9123));
    BOOST_CHECK(loaded.UsesRetainedOriginalCandidates());
    BOOST_CHECK(!loaded.HasExpansion());
    BOOST_CHECK_EQUAL(loaded.LegacyExtraSupportCap(), 0U);
    BOOST_CHECK(loaded.HasTagVectorCounts());
    BOOST_CHECK_EQUAL(loaded.TagVectorCount(20), 1U);
    BOOST_REQUIRE(loaded.SetHeadTags(0, {10}));
    BOOST_CHECK(!loaded.Finalize());
    BOOST_REQUIRE(loaded.SetHeadTags(0, {10, 20}));
    BOOST_REQUIRE(loaded.Finalize());

    for (int version : {1, 2})
    {
        const std::size_t headerBytes = version == 1
            ? sizeof(LimitedTagSupport::HeaderV1) : sizeof(LimitedTagSupport::HeaderV2);
        const std::size_t bodyBytes = sizeof(tags) + (version == 1 ? 0 : sizeof(attributes));
        std::vector<std::uint8_t> legacy(headerBytes + bodyBytes);
        std::memcpy(legacy.data() + headerBytes, expected.data() + sizeof(Header), bodyBytes);
        const auto hash = NativeSupportHash(legacy.data() + headerBytes, bodyBytes);
        if (version == 1)
        {
            LimitedTagSupport::HeaderV1 old{0x3153544cU, 1, 48, 3, 2, 2, 3, 2, 9123, hash};
            SetNativeSupportValue(legacy, 0, old);
        }
        else
        {
            LimitedTagSupport::HeaderV2 old{0x3153544cU, 2, 56, 3, 2, 2, 3, 2, 0, 1, 9123, hash};
            SetNativeSupportValue(legacy, 0, old);
        }
        WriteNativeSupportBytes(file.path, legacy);
        BOOST_REQUIRE(loaded.Load(file.path, 3, 2, 3, 0, 1, 9123));
        BOOST_CHECK(!loaded.UsesRetainedOriginalCandidates());
        BOOST_REQUIRE(loaded.Validate());
        BOOST_CHECK(!loaded.HasExpansion());
        BOOST_CHECK_EQUAL(loaded.LegacyExtraSupportCap(), 0U);
        BOOST_CHECK(!loaded.HasTagVectorCounts());
        BOOST_CHECK_EQUAL(loaded.ContentFingerprint(), hash);
        BOOST_CHECK_EQUAL(loaded.RequiredHeadCount(10), 3U);
        BOOST_CHECK_EQUAL(loaded.CoverageCount(20), 3);
        BOOST_CHECK_EQUAL(loaded.HeadAttributes(1)[0], 20U);
        auto invalidLegacySource = legacy;
        SetNativeSupportValue(invalidLegacySource, std::size_t{20}, std::uint32_t{0});
        WriteNativeSupportBytes(file.path, invalidLegacySource);
        BOOST_CHECK(!loaded.Load(file.path, 3, 2, 3, 0, 1, 9123));
        WriteNativeSupportBytes(file.path, legacy);
        BOOST_REQUIRE(loaded.Load(file.path, 3, 2, 3, 0, 1, 9123));
        BOOST_CHECK(!loaded.Save(file.path));
        BOOST_REQUIRE(loaded.SetTagVectorCounts(3, {{10, 2}, {20, 1}}));
        BOOST_REQUIRE(loaded.Save(file.path));
        auto legacyExpected = expected;
        SetNativeSupportValue(legacyExpected, offsetof(Header, m_legacyVoteHeadCount), std::uint32_t{2});
        UpdateNativeSupportFingerprint(legacyExpected);
        BOOST_CHECK(NativeSupportBytes(file.path) == legacyExpected);
        BOOST_REQUIRE(loaded.Load(file.path, 3, 2, 3, 0, 1, 9123));
        BOOST_CHECK(!loaded.UsesRetainedOriginalCandidates());
    }

    auto invalidReserved = expected;
    SetNativeSupportValue(invalidReserved, countsOffset + offsetof(Record, m_reserved), std::uint32_t{1});
    UpdateNativeSupportFingerprint(invalidReserved);
    WriteNativeSupportBytes(file.path, invalidReserved);
    BOOST_CHECK(!loaded.Load(file.path, 3, 2, 3, 0, 1, 9123));
}

BOOST_AUTO_TEST_CASE(LimitedSupportExpansionIndexesBaseAndOverflow)
{
    auto support = NativeSupportExpansionBase();
    std::vector<std::uint64_t> offsets{0, 2, 2, 3, 3, 3};
    std::vector<std::uint32_t> extras{30, 50, 10};
    extras.reserve(32);
    const auto* extraStorage = extras.data();
    std::string error = "old diagnostic";
    BOOST_REQUIRE_MESSAGE(support.ConfigureExpansion(
        std::move(offsets), std::move(extras), NativeSupportRequirements(), &error), error);
    BOOST_CHECK(error.empty());
    BOOST_CHECK(extras.empty());
    BOOST_CHECK(support.ExtraTagData(0) == extraStorage);
    BOOST_REQUIRE(support.Finalize());
    BOOST_REQUIRE(support.Validate());
    BOOST_CHECK(support.HasExpansion());
    BOOST_CHECK_EQUAL(support.HeadCount(), 5);
    BOOST_CHECK_EQUAL(support.SlotsPerHead(), 2);
    BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 3U);
    BOOST_CHECK_EQUAL(support.ExtraTagCount(0), 2U);
    BOOST_CHECK_EQUAL(support.ExtraTagCount(2), 1U);
    BOOST_CHECK_EQUAL(support.ExtraTagData(0)[0], 30U);
    BOOST_CHECK_EQUAL(support.ExtraTagData(0)[1], 50U);
    BOOST_CHECK_EQUAL(support.ExtraTagData(2)[0], 10U);
    for (SizeType head : {-1, 1, 3, 4, 5, MaxSize})
    {
        BOOST_CHECK_EQUAL(support.ExtraTagCount(head), 0U);
        BOOST_CHECK(support.ExtraTagData(head) == nullptr);
    }
    BOOST_CHECK_EQUAL(support.HeadTagData(0)[0], 10U);
    BOOST_CHECK_EQUAL(support.HeadTagData(0)[1], 20U);
    BOOST_CHECK_EQUAL(support.TagAt(0, 2), LimitedTagSupport::EmptyTag);
    BOOST_CHECK(support.HeadTags(0) == std::vector<std::uint32_t>({10, 20, 30, 50}));
    BOOST_CHECK(support.HeadTags(2) == std::vector<std::uint32_t>({20, 30, 10}));
    BOOST_CHECK(support.HeadTags(4).empty());
    BOOST_CHECK(support.HeadTags(-1).empty());
    BOOST_CHECK(support.HeadTags(5).empty());
    for (std::uint32_t tag : {10U, 20U, 30U, 50U}) BOOST_CHECK(support.Supports(0, tag));
    BOOST_CHECK(support.Supports(2, 10));
    BOOST_CHECK(!support.Supports(0, 40));
    BOOST_CHECK(!support.Supports(0, 99));
    BOOST_CHECK(!support.Supports(-1, 10));
    BOOST_CHECK(!support.Supports(5, 10));
    BOOST_CHECK(!support.Supports(4, LimitedTagSupport::EmptyTag));
    BOOST_CHECK(!support.IsActiveHead(4));
    const std::unordered_map<std::uint32_t, std::vector<SizeType>> expected{
        {10, {0, 1, 2}}, {20, {0, 1, 2}}, {30, {0, 2}}, {40, {3}}, {50, {0}}};
    BOOST_CHECK(support.TagHeads() == expected);
    for (const auto& entry : NativeSupportRequirements())
    {
        BOOST_CHECK_EQUAL(support.RequiredHeadCount(entry.first), entry.second);
        BOOST_CHECK_EQUAL(support.CoverageCount(entry.first), expected.at(entry.first).size());
    }
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(99), 0U);
    BOOST_CHECK_EQUAL(support.CoverageCount(99), 0);
    BOOST_CHECK_EQUAL(support.VectorCount(), 11U);
    BOOST_CHECK_EQUAL(support.TagVectorCount(40), 1U);
    BOOST_CHECK(support.TagSelectivityInRange(40, 0.0, 0.1));
}

BOOST_AUTO_TEST_CASE(LimitedSupportExpansionAllowsHeadOnlySingletonWithoutExtras)
{
    LimitedTagSupport support;
    BOOST_REQUIRE(support.Initialize(1, 2, 3, 0, 1, 9123));
    const std::uint32_t attribute = 40;
    BOOST_REQUIRE(support.SetHeadTags(0, {40}));
    BOOST_REQUIRE(support.SetHeadAttributes(0, &attribute, 1));
    BOOST_REQUIRE(support.SetTagVectorCounts(1, {{40, 1}}));
    BOOST_CHECK(!support.Finalize());
    BOOST_CHECK(!support.ConfigureExpansion({0, 0}, {}, {{40, 2}}));
    BOOST_CHECK(!support.HasExpansion());
    BOOST_REQUIRE(support.ConfigureExpansion({0, 0}, {}, {{40, 1}}));
    BOOST_REQUIRE(support.Finalize());
    BOOST_CHECK(support.HasExpansion());
    BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 0U);
    BOOST_CHECK_EQUAL(support.LegacyExtraSupportCap(), 0U);
    BOOST_CHECK(support.ExtraTagData(0) == nullptr);
    BOOST_CHECK_EQUAL(support.MinHeadCount(), 3);
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(40), 1U);
    BOOST_CHECK_EQUAL(support.CoverageCount(40), 1);
    BOOST_CHECK_EQUAL(support.TagVectorCount(40), 1U);
    BOOST_CHECK_EQUAL(support.VectorCount(), 1U);
    BOOST_CHECK(support.HeadTags(0) == std::vector<std::uint32_t>({40}));
    CatalogFile file;
    BOOST_REQUIRE(support.Save(file.path));
    const auto bytes = NativeSupportBytes(file.path);
    const auto header = NativeSupportValue<LimitedTagSupport::HeaderV4>(bytes, 0);
    BOOST_CHECK_EQUAL(header.m_extraTagCount, 0U);
    BOOST_CHECK_EQUAL(header.m_maxExtraSupports, 0U);
    BOOST_CHECK_EQUAL(bytes.size(), 80U + 2U * 4U + 4U + 16U + 2U * 8U);
    LimitedTagSupport loaded;
    BOOST_REQUIRE(loaded.Load(file.path, 1, 2, 3, 0, 1, 9123));
    BOOST_CHECK(loaded.HasExpansion());
    BOOST_CHECK_EQUAL(loaded.LegacyExtraSupportCap(), 0U);
    BOOST_CHECK_EQUAL(loaded.ContentFingerprint(), support.ContentFingerprint());
    BOOST_CHECK_EQUAL(loaded.RequiredHeadCount(40), 1U);
    BOOST_CHECK_EQUAL(loaded.TagVectorCount(40), 1U);
    BOOST_CHECK(!loaded.TombstoneHead(0));
    BOOST_CHECK(!loaded.AppendRuntimeTombstoneHead());
    auto legacy = bytes;
    SetNativeSupportValue(legacy, offsetof(LimitedTagSupport::Header, m_version), std::uint32_t{4});
    SetNativeSupportValue(legacy, offsetof(LimitedTagSupport::HeaderV4, m_maxExtraSupports),
                          std::uint64_t{262144});
    UpdateNativeSupportFingerprint(legacy);
    WriteNativeSupportBytes(file.path, legacy);
    BOOST_REQUIRE(loaded.Load(file.path, 1, 2, 3, 0, 1, 9123));
    BOOST_CHECK_EQUAL(loaded.LegacyExtraSupportCap(), 262144U);
    BOOST_CHECK_EQUAL(loaded.ExtraSupportCount(), 0U);
    BOOST_REQUIRE(loaded.Save(file.path));
    BOOST_CHECK(NativeSupportBytes(file.path) == legacy);
}

BOOST_AUTO_TEST_CASE(LimitedSupportExpansionRoundTripsExactMetadataAndFingerprints)
{
    using Header = LimitedTagSupport::Header;
    using HeaderV4 = LimitedTagSupport::HeaderV4;
    using Record = LimitedTagSupport::TagCountRecord;
    auto support = NativeExpandedSupport();
    CatalogFile file;
    BOOST_REQUIRE(support.Save(file.path));
    const auto original = NativeSupportBytes(file.path);
    const auto header = NativeSupportValue<HeaderV4>(original, 0);
    BOOST_CHECK_EQUAL(original.size(), 320U);
    BOOST_CHECK_EQUAL(header.m_base.m_magic, 0x3153544cU);
    BOOST_CHECK_EQUAL(header.m_base.m_version, 5U);
    BOOST_CHECK_EQUAL(header.m_base.m_headerBytes, 80U);
    BOOST_CHECK_EQUAL(header.m_base.m_headCount, 5U);
    BOOST_CHECK_EQUAL(header.m_base.m_slotsPerHead, 2U);
    BOOST_CHECK_EQUAL(header.m_base.m_legacyVoteHeadCount, 0U);
    BOOST_CHECK_EQUAL(header.m_base.m_minHeadCount, 3U);
    BOOST_CHECK_EQUAL(header.m_base.m_keyColumn, 1U);
    BOOST_CHECK_EQUAL(header.m_base.m_attributeCount, 3U);
    BOOST_CHECK_EQUAL(header.m_base.m_tagCount, 5U);
    BOOST_CHECK_EQUAL(header.m_base.m_vectorCount, 11U);
    BOOST_CHECK_EQUAL(header.m_base.m_generationFingerprint, 9123U);
    BOOST_CHECK_EQUAL(header.m_extraTagCount, 3U);
    BOOST_CHECK_EQUAL(header.m_maxExtraSupports, 3U);
    BOOST_CHECK_EQUAL(support.LegacyExtraSupportCap(), 0U);
    BOOST_CHECK_EQUAL(header.m_base.m_bodyFingerprint, support.ContentFingerprint());
    BOOST_CHECK_EQUAL(header.m_base.m_bodyFingerprint, NativeSupportFingerprint(original));
    const std::size_t countsOffset = 80 + (10 + 15) * sizeof(std::uint32_t);
    const std::size_t offsetsOffset = countsOffset + 5 * sizeof(Record);
    const std::size_t extrasOffset = offsetsOffset + 6 * sizeof(std::uint64_t);
    const std::uint64_t offsets[] = {0, 2, 2, 3, 3, 3};
    for (std::size_t i = 0; i < 6; ++i)
        BOOST_CHECK_EQUAL(NativeSupportValue<std::uint64_t>(original, offsetsOffset + i * 8), offsets[i]);
    const Record records[] = {{10, 3, 4}, {20, 3, 3}, {30, 2, 2}, {40, 1, 1}, {50, 1, 1}};
    for (std::size_t i = 0; i < 5; ++i)
    {
        const auto actual = NativeSupportValue<Record>(original, countsOffset + i * sizeof(Record));
        BOOST_CHECK_EQUAL(actual.m_tag, records[i].m_tag);
        BOOST_CHECK_EQUAL(actual.m_reserved, records[i].m_reserved);
        BOOST_CHECK_EQUAL(actual.m_count, records[i].m_count);
    }
    const std::uint32_t extras[] = {30, 50, 10};
    for (std::size_t i = 0; i < 3; ++i)
        BOOST_CHECK_EQUAL(NativeSupportValue<std::uint32_t>(original, extrasOffset + i * 4), extras[i]);

    std::vector<std::vector<std::uint8_t>> variants(5, original);
    // These historical V4 files authenticate their opaque configured cap,
    // including requirements less than already materialized coverage.
    SetNativeSupportValue(variants[1], offsetof(Header, m_version), std::uint32_t{4});
    SetNativeSupportValue(variants[2], offsetof(Header, m_version), std::uint32_t{4});
    SetNativeSupportValue(variants[1], offsetof(HeaderV4, m_maxExtraSupports), std::uint64_t{4});
    SetNativeSupportValue(variants[2], countsOffset + 2 * sizeof(Record) + offsetof(Record, m_reserved),
                          std::uint32_t{1});
    SetNativeSupportValue(variants[3], offsetof(Header, m_vectorCount), std::uint64_t{12});
    SetNativeSupportValue(variants[3], countsOffset + offsetof(Record, m_count), std::uint64_t{5});
    SetNativeSupportValue(variants[4], offsetsOffset + sizeof(std::uint64_t), std::uint64_t{1});
    for (std::size_t variant = 0; variant < variants.size(); ++variant)
    {
        auto& bytes = variants[variant];
        UpdateNativeSupportFingerprint(bytes);
        const auto saved = NativeSupportValue<HeaderV4>(bytes, 0);
        if (variant != 0)
            BOOST_CHECK_NE(saved.m_base.m_bodyFingerprint, header.m_base.m_bodyFingerprint);
        WriteNativeSupportBytes(file.path, bytes);
        LimitedTagSupport loaded;
        BOOST_REQUIRE(loaded.Load(file.path, 5, 2, 3, 1, 3, 9123));
        BOOST_REQUIRE(loaded.Validate());
        BOOST_CHECK(loaded.HasExpansion());
        BOOST_CHECK(loaded.HasTagVectorCounts());
        BOOST_CHECK_EQUAL(loaded.VectorCount(), saved.m_base.m_vectorCount);
        BOOST_CHECK_EQUAL(loaded.LegacyExtraSupportCap(),
                          saved.m_base.m_version == 4 ? saved.m_maxExtraSupports : 0U);
        BOOST_CHECK_EQUAL(loaded.ContentFingerprint(), saved.m_base.m_bodyFingerprint);
        for (std::size_t i = 0; i < 5; ++i)
        {
            const auto record = NativeSupportValue<Record>(bytes, countsOffset + i * sizeof(Record));
            BOOST_CHECK_EQUAL(loaded.TagVectorCount(record.m_tag), record.m_count);
            BOOST_CHECK_EQUAL(loaded.RequiredHeadCount(record.m_tag), record.m_reserved);
        }
        for (SizeType head = 0; head < 5; ++head)
        {
            std::vector<std::uint32_t> allTags;
            for (int slot = 0; slot < 2; ++slot)
            {
                const auto tag = NativeSupportValue<std::uint32_t>(bytes, 80 + (head * 2 + slot) * 4);
                BOOST_CHECK_EQUAL(loaded.TagAt(head, slot), tag);
                if (tag != LimitedTagSupport::EmptyTag) allTags.push_back(tag);
            }
            for (int column = 0; column < 3; ++column)
            {
                BOOST_CHECK_EQUAL(loaded.HeadAttributes(head)[column],
                    NativeSupportValue<std::uint32_t>(bytes, 80 + 10 * 4 + (head * 3 + column) * 4));
            }
            const auto begin = NativeSupportValue<std::uint64_t>(bytes, offsetsOffset + head * 8);
            const auto end = NativeSupportValue<std::uint64_t>(bytes, offsetsOffset + (head + 1) * 8);
            BOOST_CHECK_EQUAL(loaded.ExtraTagCount(head), end - begin);
            for (auto i = begin; i < end; ++i)
                allTags.push_back(NativeSupportValue<std::uint32_t>(bytes, extrasOffset + i * 4));
            BOOST_CHECK(loaded.HeadTags(head) == allTags);
            for (auto tag : allTags) BOOST_CHECK(loaded.Supports(head, tag));
        }
        BOOST_REQUIRE(loaded.Save(file.path));
        BOOST_CHECK(NativeSupportBytes(file.path) == bytes);
    }
}

BOOST_AUTO_TEST_CASE(LimitedSupportExpansionRejectsInvalidInputsAtomically)
{
    auto support = NativeSupportExpansionBase();
    const auto original = support;
    const std::vector<std::uint64_t> offsets{0, 2, 2, 3, 3, 3};
    const std::vector<std::uint32_t> extras{30, 50, 10};
    const auto reject = [&](std::vector<std::uint64_t> badOffsets,
                            std::vector<std::uint32_t> badExtras,
                            SupportRequirements requirements = NativeSupportRequirements())
    {
        std::string error;
        BOOST_CHECK(!support.ConfigureExpansion(
            std::move(badOffsets), std::move(badExtras), requirements, &error));
        BOOST_CHECK(!error.empty());
        BOOST_CHECK(!support.HasExpansion());
        BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 0U);
        BOOST_CHECK_EQUAL(support.LegacyExtraSupportCap(), 0U);
        BOOST_CHECK_EQUAL(support.HeadCount(), original.HeadCount());
        BOOST_CHECK_EQUAL(support.ContentFingerprint(), original.ContentFingerprint());
        BOOST_CHECK_EQUAL(support.VectorCount(), original.VectorCount());
        BOOST_CHECK(support.TagHeads() == original.TagHeads());
        for (SizeType head = 0; head < original.HeadCount(); ++head)
        {
            BOOST_CHECK(support.HeadTags(head) == original.HeadTags(head));
            BOOST_CHECK_EQUAL(std::memcmp(support.HeadAttributes(head), original.HeadAttributes(head),
                                          3 * sizeof(std::uint32_t)), 0);
        }
        for (const auto& entry : NativeSupportRequirements())
        {
            BOOST_CHECK_EQUAL(support.TagVectorCount(entry.first), original.TagVectorCount(entry.first));
            BOOST_CHECK_EQUAL(support.RequiredHeadCount(entry.first), 3U);
        }
    };
    reject({}, extras);
    reject({0, 2, 2, 3, 3}, extras);
    reject({0, 2, 2, 3, 3, 3, 3}, extras);
    reject({1, 2, 2, 3, 3, 3}, extras);
    reject({0, 2, 2, 3, 3, 2}, extras);
    reject({0, 2, 1, 3, 3, 3}, extras);
    reject({0, 4, 4, 3, 3, 3}, extras);
    reject({0, (std::numeric_limits<std::uint64_t>::max)(), 2, 3, 3, 3}, extras);
    reject(offsets, {30, 30, 10});
    reject(offsets, {50, 30, 10});
    reject(offsets, {20, 50, 10});
    reject(offsets, {30, LimitedTagSupport::EmptyTag, 10});
    reject(offsets, {30, 99, 10});
    reject(offsets, {30, 50});
    reject({0, 1, 1, 2, 2, 3}, {30, 10, 50});
    auto requirements = NativeSupportRequirements();
    requirements.erase(50);
    reject(offsets, extras, requirements);
    requirements.emplace(99, 1);
    reject(offsets, extras, requirements);
    requirements = NativeSupportRequirements();
    requirements.emplace(99, 1);
    reject(offsets, extras, requirements);
    requirements = NativeSupportRequirements();
    requirements[30] = 0;
    reject(offsets, extras, requirements);
    requirements[30] = 4;
    reject(offsets, extras, requirements);
    requirements[30] = 3;
    reject(offsets, extras, requirements);

    auto mismatchedOwnTag = original;
    const std::uint32_t wrongAttributes[] = {100, 99, 1000};
    BOOST_REQUIRE(mismatchedOwnTag.SetHeadAttributes(0, wrongAttributes, 3));
    BOOST_CHECK(!mismatchedOwnTag.ConfigureExpansion(
        offsets, extras, NativeSupportRequirements()));
    BOOST_CHECK(!mismatchedOwnTag.HasExpansion());
    LimitedTagSupport missingCounts;
    BOOST_REQUIRE(missingCounts.Initialize(1, 2, 1, 0, 1, 9123));
    const std::uint32_t ownTag = 40;
    BOOST_REQUIRE(missingCounts.SetHeadTags(0, {40}));
    BOOST_REQUIRE(missingCounts.SetHeadAttributes(0, &ownTag, 1));
    BOOST_CHECK(!missingCounts.ConfigureExpansion({0, 0}, {}, {{40, 1}}));
    BOOST_REQUIRE(missingCounts.SetTagVectorCounts(1, {{40, 1}}));
    CatalogFile file;
    BOOST_REQUIRE(missingCounts.Save(file.path));
    const auto stable = NativeSupportBytes(file.path);
    BOOST_CHECK(!missingCounts.ConfigureExpansion({0, 1}, {40}, {{40, 1}}));
    BOOST_REQUIRE(missingCounts.Save(file.path));
    BOOST_CHECK(NativeSupportBytes(file.path) == stable);

    BOOST_REQUIRE(support.ConfigureExpansion(offsets, extras, NativeSupportRequirements()));
    BOOST_REQUIRE(support.Finalize());
    BOOST_CHECK_EQUAL(support.CoverageCount(10), 3);
}

BOOST_AUTO_TEST_CASE(LimitedSupportLegacyExpandedReadExportKeepsCandidateProvenance)
{
    using Header = LimitedTagSupport::Header;
    auto support = NativeExpandedSupport();
    CatalogFile file;
    BOOST_REQUIRE(support.Save(file.path));
    const auto retained = NativeSupportBytes(file.path);
    auto legacy = retained;
    SetNativeSupportValue(legacy, offsetof(Header, m_version), std::uint32_t{4});
    SetNativeSupportValue(legacy, offsetof(LimitedTagSupport::HeaderV4, m_maxExtraSupports),
                          std::uint64_t{1} << 40);
    SetNativeSupportValue(legacy, offsetof(Header, m_legacyVoteHeadCount), std::uint32_t{7});
    UpdateNativeSupportFingerprint(legacy);
    WriteNativeSupportBytes(file.path, legacy);
    LimitedTagSupport loaded;
    BOOST_REQUIRE(loaded.Load(file.path, 5, 2, 3, 1, 3, 9123));
    BOOST_CHECK(loaded.HasExpansion());
    BOOST_CHECK(!loaded.UsesRetainedOriginalCandidates());
    BOOST_CHECK_EQUAL(loaded.LegacyExtraSupportCap(), std::uint64_t{1} << 40);
    BOOST_CHECK(loaded.TagHeads() == support.TagHeads());
    BOOST_REQUIRE(loaded.Save(file.path));
    BOOST_CHECK(NativeSupportBytes(file.path) == legacy);
    // Changing provenance without recomputing its source-bound hash is corruption.
    SetNativeSupportValue(legacy, offsetof(Header, m_legacyVoteHeadCount), std::uint32_t{0});
    WriteNativeSupportBytes(file.path, legacy);
    BOOST_CHECK(!loaded.Load(file.path, 5, 2, 3, 1, 3, 9123));
    WriteNativeSupportBytes(file.path, retained);
    BOOST_REQUIRE(loaded.Load(file.path, 5, 2, 3, 1, 3, 9123));
    BOOST_CHECK(loaded.UsesRetainedOriginalCandidates());
}

BOOST_AUTO_TEST_CASE(LimitedSupportExpansionRejectsCorruptStorage)
{
    using Header = LimitedTagSupport::Header;
    using HeaderV4 = LimitedTagSupport::HeaderV4;
    using Record = LimitedTagSupport::TagCountRecord;
    auto support = NativeExpandedSupport();
    CatalogFile file;
    BOOST_REQUIRE(support.Save(file.path));
    const auto original = NativeSupportBytes(file.path);
    const std::size_t countsOffset = 180;
    const std::size_t offsetsOffset = 260;
    const std::size_t extrasOffset = 308;
    LimitedTagSupport loaded = support;
    const auto reject = [&](std::vector<std::uint8_t> bytes, bool rehash = false,
                            bool fingerprintError = false)
    {
        if (rehash) UpdateNativeSupportFingerprint(bytes);
        WriteNativeSupportBytes(file.path, bytes);
        std::string error;
        BOOST_CHECK(!loaded.Load(file.path, 5, 2, 3, 1, 3, 9123, &error));
        BOOST_CHECK(!error.empty());
        if (fingerprintError) BOOST_CHECK(error.find("fingerprint") != std::string::npos);
        BOOST_CHECK(!loaded.HasExpansion());
        BOOST_CHECK_EQUAL(loaded.HeadCount(), 0);
        BOOST_CHECK_EQUAL(loaded.ExtraSupportCount(), 0U);
        BOOST_CHECK_EQUAL(loaded.LegacyExtraSupportCap(), 0U);
        BOOST_CHECK(loaded.ExtraTagData(0) == nullptr);
        BOOST_CHECK(loaded.TagHeads().empty());
        BOOST_CHECK_EQUAL(loaded.ContentFingerprint(), 0U);
    };
    const auto reject32 = [&](std::size_t offset, std::uint32_t value, bool rehash = true,
                              bool fingerprintError = false)
    {
        auto bytes = original;
        SetNativeSupportValue(bytes, offset, value);
        reject(std::move(bytes), rehash, fingerprintError);
    };
    const auto reject64 = [&](std::size_t offset, std::uint64_t value, bool rehash = true,
                              bool fingerprintError = false)
    {
        auto bytes = original;
        SetNativeSupportValue(bytes, offset, value);
        reject(std::move(bytes), rehash, fingerprintError);
    };

    reject32(80 + 4, 19, false);
    reject32(80 + 10 * 4, 101, false, true);
    reject64(offsetof(Header, m_vectorCount), 12, false);
    reject64(countsOffset + offsetof(Record, m_count), 5, false);
    reject32(countsOffset + offsetof(Record, m_reserved), 2, false);
    reject64(offsetof(HeaderV4, m_maxExtraSupports), 4, false);
    reject64(offsetsOffset + 8, 1, false, true);
    reject32(extrasOffset + 4, 40, false, true);
    reject64(offsetof(Header, m_bodyFingerprint), support.ContentFingerprint() ^ 1U, false, true);

    for (const auto& field : std::vector<std::pair<std::size_t, std::uint32_t>>{
        {offsetof(Header, m_magic), 0},
        {offsetof(Header, m_version), 6},
        {offsetof(Header, m_version), 3},
        {offsetof(Header, m_headerBytes), 64},
        {offsetof(Header, m_headCount), 6},
        {offsetof(Header, m_slotsPerHead), 3},
        {offsetof(Header, m_legacyVoteHeadCount), 4},
        {offsetof(Header, m_minHeadCount), 4},
        {offsetof(Header, m_keyColumn), 2},
        {offsetof(Header, m_attributeCount), 4},
        {offsetof(Header, m_tagCount), 0},
        {offsetof(Header, m_tagCount), (std::numeric_limits<std::uint32_t>::max)()}})
    {
        reject32(field.first, field.second, false);
    }
    reject64(offsetof(Header, m_generationFingerprint), 9124, false);
    reject64(offsetof(HeaderV4, m_extraTagCount), 2, false);
    auto impossibleCount = original;
    SetNativeSupportValue(impossibleCount, offsetof(HeaderV4, m_extraTagCount),
                          (std::numeric_limits<std::uint64_t>::max)());
    SetNativeSupportValue(impossibleCount, offsetof(HeaderV4, m_maxExtraSupports),
                          (std::numeric_limits<std::uint64_t>::max)());
    reject(std::move(impossibleCount));
    auto impossibleRows = original;
    const auto maxInt = (std::numeric_limits<int>::max)();
    SetNativeSupportValue(impossibleRows, offsetof(Header, m_headCount), static_cast<std::uint32_t>(MaxSize));
    SetNativeSupportValue(impossibleRows, offsetof(Header, m_slotsPerHead), static_cast<std::uint32_t>(maxInt));
    SetNativeSupportValue(impossibleRows, offsetof(Header, m_attributeCount), static_cast<std::uint32_t>(maxInt));
    WriteNativeSupportBytes(file.path, impossibleRows);
    BOOST_CHECK(!loaded.Load(file.path, MaxSize, maxInt, 3, 1, maxInt, 9123));
    BOOST_CHECK_EQUAL(loaded.HeadCount(), 0);

    reject32(80 + 4, 10);
    reject32(80, LimitedTagSupport::EmptyTag);
    reject32(80 + 10 * 4 + 4, 99);
    reject32(extrasOffset + 4, 30);
    auto unsorted = original;
    SetNativeSupportValue(unsorted, extrasOffset, std::uint32_t{50});
    SetNativeSupportValue(unsorted, extrasOffset + 4, std::uint32_t{30});
    reject(std::move(unsorted), true);
    reject32(extrasOffset, 20);
    reject32(extrasOffset + 4, LimitedTagSupport::EmptyTag);
    reject32(extrasOffset + 4, 99);
    reject64(offsetsOffset, 1);
    reject64(offsetsOffset + 5 * 8, 2);
    reject64(offsetsOffset + 2 * 8, 1);
    reject64(offsetsOffset + 8, (std::numeric_limits<std::uint64_t>::max)());
    auto inactive = original;
    const std::uint64_t inactiveOffsets[] = {0, 1, 1, 2, 2, 3};
    std::memcpy(inactive.data() + offsetsOffset, inactiveOffsets, sizeof(inactiveOffsets));
    reject(std::move(inactive), true);
    reject64(offsetof(HeaderV4, m_maxExtraSupports), 0);
    reject64(offsetof(HeaderV4, m_maxExtraSupports), 2);
    reject32(countsOffset + offsetof(Record, m_reserved), 0);
    reject32(countsOffset + offsetof(Record, m_reserved), 4);
    reject32(countsOffset + 3 * sizeof(Record) + offsetof(Record, m_reserved), 2);
    auto shiftedRequirement = original;
    SetNativeSupportValue(shiftedRequirement, countsOffset + offsetof(Record, m_reserved),
                          std::uint32_t{2});
    SetNativeSupportValue(shiftedRequirement,
                          countsOffset + 2 * sizeof(Record) + offsetof(Record, m_reserved),
                          std::uint32_t{3});
    reject(std::move(shiftedRequirement), true);
    reject32(countsOffset + sizeof(Record) + offsetof(Record, m_tag), 10);
    reject32(countsOffset + 4 * sizeof(Record) + offsetof(Record, m_tag), 99);
    reject32(countsOffset + 4 * sizeof(Record) + offsetof(Record, m_tag), LimitedTagSupport::EmptyTag);
    auto unorderedCounts = original;
    const auto first = NativeSupportValue<Record>(original, countsOffset);
    const auto second = NativeSupportValue<Record>(original, countsOffset + sizeof(Record));
    SetNativeSupportValue(unorderedCounts, countsOffset, second);
    SetNativeSupportValue(unorderedCounts, countsOffset + sizeof(Record), first);
    reject(std::move(unorderedCounts), true);
    reject64(countsOffset + offsetof(Record, m_count), 0);
    reject64(countsOffset + offsetof(Record, m_count), 12);
    reject64(offsetof(Header, m_vectorCount), 12);
    auto overflowingCounts = original;
    SetNativeSupportValue(overflowingCounts, offsetof(Header, m_vectorCount),
                          (std::numeric_limits<std::uint64_t>::max)());
    SetNativeSupportValue(overflowingCounts, countsOffset + offsetof(Record, m_count),
                          (std::numeric_limits<std::uint64_t>::max)());
    reject(std::move(overflowingCounts), true);

    for (std::size_t length : {0U, 11U, 63U, 79U, 80U, 259U, 307U, 319U})
        reject(std::vector<std::uint8_t>(original.begin(), original.begin() + length));
    auto trailing = original;
    trailing.push_back(0);
    reject(std::move(trailing));
    WriteNativeSupportBytes(file.path, original);
    BOOST_REQUIRE(loaded.Load(file.path, 5, 2, 3, 1, 3, 9123));
    BOOST_CHECK(loaded.TagHeads() == support.TagHeads());
}

BOOST_AUTO_TEST_CASE(LimitedSupportExpansionFreezesAllMutationAPIs)
{
    auto support = NativeSupportExpansionBase();
    BOOST_REQUIRE(support.PrepareRuntimeTombstoneHead());
    BOOST_REQUIRE(support.ConfigureExpansion(
        {0, 2, 2, 3, 3, 3}, {30, 50, 10}, NativeSupportRequirements()));
    BOOST_REQUIRE(support.Finalize());
    CatalogFile file;
    BOOST_REQUIRE(support.Save(file.path));
    const auto originalBytes = NativeSupportBytes(file.path);
    for (bool reload : {false, true})
    {
        if (reload) BOOST_REQUIRE(support.Load(file.path, 5, 2, 3, 1, 3, 9123));
        const auto original = support;
        const auto* baseStorage = support.HeadTagData(0);
        const auto* attributeStorage = support.HeadAttributes(0);
        const auto* extraStorage = support.ExtraTagData(0);
        const auto reject = [&](bool result)
        {
            BOOST_CHECK(!result);
            BOOST_CHECK(support.HasExpansion());
            BOOST_CHECK_EQUAL(support.HeadCount(), original.HeadCount());
            BOOST_CHECK_EQUAL(support.ContentFingerprint(), original.ContentFingerprint());
            BOOST_CHECK_EQUAL(support.VectorCount(), original.VectorCount());
            BOOST_CHECK_EQUAL(support.ExtraSupportCount(), original.ExtraSupportCount());
            BOOST_CHECK_EQUAL(support.LegacyExtraSupportCap(), original.LegacyExtraSupportCap());
            BOOST_CHECK(support.TagHeads() == original.TagHeads());
            BOOST_CHECK(support.HeadTagData(0) == baseStorage);
            BOOST_CHECK(support.HeadAttributes(0) == attributeStorage);
            BOOST_CHECK(support.ExtraTagData(0) == extraStorage);
            for (SizeType head = 0; head < original.HeadCount(); ++head)
            {
                BOOST_CHECK(support.HeadTags(head) == original.HeadTags(head));
                BOOST_CHECK_EQUAL(std::memcmp(support.HeadAttributes(head), original.HeadAttributes(head),
                                              3 * sizeof(std::uint32_t)), 0);
            }
            for (const auto& entry : NativeSupportRequirements())
            {
                BOOST_CHECK_EQUAL(support.TagVectorCount(entry.first), original.TagVectorCount(entry.first));
                BOOST_CHECK_EQUAL(support.RequiredHeadCount(entry.first), entry.second);
            }
        };
        const std::uint32_t attributes[] = {999, 10, 999};
        const std::vector<std::uint32_t> tags{10, 20};
        reject(support.SetTagVectorCounts(0, {}));
        reject(support.SetTagVectorCounts(11, {{10, 4}, {20, 3}, {30, 2}, {40, 1}, {50, 1}}));
        reject(support.SetHeadAttributes(0, attributes, 3));
        reject(support.SetHeadTags(0, {10}));
        reject(support.AppendHead(tags, attributes, 3));
        reject(support.AppendTombstoneHead());
        reject(support.TombstoneHead(0));
        reject(support.TombstoneHead(4));
        reject(support.ReplaceHead(0, tags, attributes, 3));
        reject(support.PrepareRuntimeAppendHead(tags, attributes, 3));
        reject(support.AppendRuntimeHead(tags, attributes, 3));
        reject(support.AppendPreparedRuntimeHead(tags, attributes, 3));
        reject(support.PrepareRuntimeTombstoneHead());
        reject(support.AppendRuntimeTombstoneHead());
        reject(support.AppendPreparedRuntimeTombstoneHead());
        reject(support.TombstoneRuntimeHead(0));
        reject(support.PrepareRuntimeReplaceAndTombstone(0, tags, attributes, 3, 1));
        reject(support.ReplaceAndTombstoneRuntime(0, tags, attributes, 3, 1));
        reject(support.ApplyPreparedRuntimeReplaceAndTombstone(0, tags, attributes, 3, 1));
        reject(support.ConfigureExpansion(
            {0, 2, 2, 3, 3, 3}, {30, 50, 10}, NativeSupportRequirements()));
        BOOST_REQUIRE(support.Validate());
        BOOST_REQUIRE(support.Finalize());
        BOOST_REQUIRE(support.Save(file.path));
        BOOST_CHECK(NativeSupportBytes(file.path) == originalBytes);
    }

    support.Reset();
    BOOST_CHECK(!support.HasExpansion());
    BOOST_CHECK_EQUAL(support.HeadCount(), 0);
    BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 0U);
    BOOST_CHECK_EQUAL(support.LegacyExtraSupportCap(), 0U);
    BOOST_CHECK(support.ExtraTagData(0) == nullptr);
    BOOST_CHECK_EQUAL(support.RequiredHeadCount(10), 0U);
    BOOST_CHECK(support.TagHeads().empty());
    BOOST_REQUIRE(support.Initialize(2, 2, 1, 0, 1, 9123));
    const std::uint32_t attributes[] = {7, 0};
    for (SizeType head = 0; head < 2; ++head)
    {
        BOOST_REQUIRE(support.SetHeadTags(head, {attributes[head]}));
        BOOST_REQUIRE(support.SetHeadAttributes(head, attributes + head, 1));
    }
    BOOST_REQUIRE(support.SetTagVectorCounts(2, {{7, 1}, {0, 1}}));
    BOOST_REQUIRE(support.Save(file.path));
    BOOST_CHECK_EQUAL(NativeSupportValue<LimitedTagSupport::Header>(
        NativeSupportBytes(file.path), 0).m_headerBytes, 64U);
    BOOST_REQUIRE(support.ConfigureExpansion({0, 0, 0}, {}, {{7, 1}, {0, 1}}));
    BOOST_REQUIRE(support.Finalize());
    BOOST_CHECK(support.Supports(1, 0));
    BOOST_CHECK_EQUAL(support.CoverageCount(0), 1);
}

BOOST_AUTO_TEST_SUITE_END()
