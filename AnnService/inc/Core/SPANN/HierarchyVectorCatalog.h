// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_SPANN_HIERARCHYVECTORCATALOG_H_
#define _SPTAG_SPANN_HIERARCHYVECTORCATALOG_H_

#include "inc/Core/VectorIndex.h"
#include "inc/Core/VectorSet.h"

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace SPTAG
{
namespace SPANN
{
namespace HierarchyVectorCatalogDetail
{

inline ErrorCode Fail(ErrorCode code, const char* message, std::string* error)
{
    try
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "%s\n", message);
    }
    catch (const std::bad_alloc&)
    {
    }
    if (error != nullptr)
    {
        try
        {
            *error = message;
        }
        catch (const std::bad_alloc&)
        {
            error->clear();
        }
    }
    return code;
}

inline ErrorCode CheckedRowSize(
    VectorValueType type, DimensionType dimension,
    SizeType& rowSize, std::string* error)
{
    const std::size_t valueSize = GetValueTypeSize(type);
    if (valueSize == 0)
        return Fail(ErrorCode::FailedParseValue,
                    "Hierarchy catalog has an invalid vector value type.", error);
    if (dimension <= 0)
        return Fail(ErrorCode::DimensionSizeMismatch,
                    "Hierarchy catalog requires a positive vector dimension.", error);
    if (static_cast<std::size_t>(dimension) >
        static_cast<std::size_t>((std::numeric_limits<SizeType>::max)()) / valueSize)
        return Fail(ErrorCode::MemoryOverFlow,
                    "Hierarchy vector row size exceeds native SizeType.", error);
    rowSize = static_cast<SizeType>(static_cast<std::size_t>(dimension) * valueSize);
    return ErrorCode::Success;
}

// VectorSet's void* interface cannot express const rows. Callers must treat
// returned pointers and all backing catalogs/indexes as read-only while views live.
class ImmutableVectorView : public VectorSet
{
public:
    VectorValueType GetValueType() const override { return m_type; }
    DimensionType Dimension() const override { return m_dimension; }
    SizeType Count() const override { return m_count; }
    SizeType PerVectorDataSize() const override { return m_rowSize; }
    bool Available() const override { return m_count > 0; }

    // Logical hierarchy rows are not contiguous, including the top index view.
    void* GetData() const override
    {
        throw std::logic_error(
            "Hierarchy vector views do not provide contiguous data; use GetVector.");
    }

    void Normalize(int) override
    {
        throw std::logic_error("Hierarchy vector views are immutable; Normalize is unsupported.");
    }

    ErrorCode Save(const std::string&) const override
    {
        return Fail(ErrorCode::Undefined,
                    "Save is unsupported for logical hierarchy vector views; "
                    "save the owned BasicVectorSets and the top vector catalog instead.", nullptr);
    }

    ErrorCode AppendSave(const std::string&) const override
    {
        return Fail(ErrorCode::Undefined,
                    "AppendSave is unsupported for logical hierarchy vector views; "
                    "save the owned BasicVectorSets and the top vector catalog instead.", nullptr);
    }

protected:
    ImmutableVectorView(VectorValueType type, DimensionType dimension,
                        SizeType count, SizeType rowSize)
        : m_type(type), m_dimension(dimension), m_count(count), m_rowSize(rowSize)
    {
    }

private:
    const VectorValueType m_type;
    const DimensionType m_dimension;
    const SizeType m_count;
    const SizeType m_rowSize;
};

class ReadOnlyVectorView final : public ImmutableVectorView
{
public:
    ReadOnlyVectorView(const std::shared_ptr<VectorSet>& index, SizeType rowSize)
        : ImmutableVectorView(index->GetValueType(), index->Dimension(),
                              index->Count(), rowSize),
          m_index(index)
    {
    }

    void* GetVector(SizeType id) const override
    {
        if (id < 0 || id >= Count()) return nullptr;
        return const_cast<void*>(m_index->GetVector(id));
    }

private:
    const std::shared_ptr<const VectorSet> m_index;
};

class LowerVectorView final : public ImmutableVectorView
{
public:
    LowerVectorView(std::shared_ptr<const BasicVectorSet> owned,
                    std::shared_ptr<const VectorSet> upper,
                    std::vector<SizeType>&& rows)
        : ImmutableVectorView(upper->GetValueType(), upper->Dimension(),
                              static_cast<SizeType>(rows.size()), upper->PerVectorDataSize()),
          m_owned(std::move(owned)), m_upper(std::move(upper)), m_rows(std::move(rows))
    {
    }

    void* GetVector(SizeType id) const override
    {
        if (id < 0 || id >= Count()) return nullptr;
        const SizeType row = m_rows[static_cast<std::size_t>(id)];
        return row < 0 ? m_upper->GetVector(-row - 1) : m_owned->GetVector(row);
    }

private:
    const std::shared_ptr<const BasicVectorSet> m_owned;
    const std::shared_ptr<const VectorSet> m_upper;
    // Nonnegative = packed owned row; -(upper ID + 1) = promoted row.
    const std::vector<SizeType> m_rows;
};

} // namespace HierarchyVectorCatalogDetail

// Copy only nonpromoted rows, in increasing logical-ID order, to a native
// BasicVectorSet suitable for VectorSet::Save/AppendSave. Empty physical catalogs
// retain their type/dimension and serialize as the normal count/dimension header.
// The output is replaced only on success (input/output aliasing is supported).
inline ErrorCode PackOwnedHierarchyVectors(
    const std::shared_ptr<VectorSet>& logicalCatalog,
    const std::vector<std::uint64_t>& promotedLowerIDs,
    std::shared_ptr<VectorSet>& ownedCatalog,
    std::string* error = nullptr)
{
    using namespace HierarchyVectorCatalogDetail;
    if (error != nullptr) error->clear();
    try
    {
        if (logicalCatalog == nullptr)
            return Fail(ErrorCode::LackOfInputs,
                        "Cannot pack a missing logical hierarchy catalog.", error);
        const SizeType count = logicalCatalog->Count();
        if (count < 0)
            return Fail(ErrorCode::FailedParseValue,
                        "Cannot pack a hierarchy catalog with a negative row count.", error);

        SizeType rowSize = 0;
        const ErrorCode shape = CheckedRowSize(
            logicalCatalog->GetValueType(), logicalCatalog->Dimension(), rowSize, error);
        if (shape != ErrorCode::Success) return shape;
        if (logicalCatalog->PerVectorDataSize() != rowSize)
            return Fail(ErrorCode::DimensionSizeMismatch,
                        "Hierarchy catalog row byte size does not match its type/dimension.", error);
        if (promotedLowerIDs.size() > static_cast<std::size_t>(count))
            return Fail(ErrorCode::FailedParseValue,
                        "Hierarchy promotion count exceeds the logical lower row count.", error);
        if (count > 0 && !logicalCatalog->Available())
            return Fail(ErrorCode::VectorNotFound,
                        "Logical hierarchy catalog has no vector storage.", error);

        const SizeType ownedCount = count - static_cast<SizeType>(promotedLowerIDs.size());
        const std::size_t bytesPerRow = static_cast<std::size_t>(rowSize);
        if (static_cast<std::size_t>(ownedCount) >
            (std::numeric_limits<std::size_t>::max)() / bytesPerRow)
            return Fail(ErrorCode::MemoryOverFlow,
                        "Packed hierarchy vector buffer size overflows size_t.", error);

        std::vector<bool> promoted;
        if (static_cast<std::size_t>(count) > promoted.max_size())
            return Fail(ErrorCode::MemoryOverFlow,
                        "Hierarchy promotion bitmap exceeds the native allocation limit.", error);
        promoted.resize(static_cast<std::size_t>(count), false);
        for (const std::uint64_t id : promotedLowerIDs)
        {
            if (id >= static_cast<std::uint64_t>(count))
                return Fail(ErrorCode::FailedParseValue,
                            "Hierarchy sampling ID is outside the logical lower catalog.", error);
            if (promoted[static_cast<std::size_t>(id)])
                return Fail(ErrorCode::FailedParseValue,
                            "Hierarchy sampling IDs contain a duplicate lower row.", error);
            promoted[static_cast<std::size_t>(id)] = true;
        }

        ByteArray bytes = ByteArray::Alloc(static_cast<std::size_t>(ownedCount) * bytesPerRow);
        std::size_t offset = 0;
        for (SizeType id = 0; id < count; ++id)
        {
            const void* sample = logicalCatalog->GetVector(id);
            if (sample == nullptr)
                return Fail(ErrorCode::VectorNotFound,
                            "Logical hierarchy catalog contains a missing vector row.", error);
            if (!promoted[static_cast<std::size_t>(id)])
            {
                std::memcpy(bytes.Data() + offset, sample, bytesPerRow);
                offset += bytesPerRow;
            }
        }

        auto packed = std::make_shared<BasicVectorSet>(
            bytes, logicalCatalog->GetValueType(), logicalCatalog->Dimension(), ownedCount);
        ownedCatalog = std::move(packed);
        return ErrorCode::Success;
    }
    catch (const std::bad_alloc&)
    {
        return Fail(ErrorCode::MemoryOverFlow,
                    "Insufficient memory to pack owned hierarchy vectors.", error);
    }
    catch (const std::length_error&)
    {
        return Fail(ErrorCode::MemoryOverFlow,
                    "Owned hierarchy vector allocation exceeds the native size limit.", error);
    }
}

// Preserve all logical rows; the top vector catalog is not materialized a second time.
inline ErrorCode MaterializeHierarchyCatalog(
    const std::shared_ptr<VectorSet>& logicalCatalog,
    std::shared_ptr<VectorSet>& catalog,
    std::string* error = nullptr)
{
    return PackOwnedHierarchyVectors(logicalCatalog, {}, catalog, error);
}

inline ErrorCode LoadHierarchyVectorCatalog(
    const std::string& path, VectorValueType type, DimensionType dimension,
    SizeType expectedCount, std::shared_ptr<VectorSet>& catalog,
    std::string* error = nullptr)
{
    using namespace HierarchyVectorCatalogDetail;
    if (error != nullptr) error->clear();
    try
    {
        SizeType rowSize = 0;
        const ErrorCode shape = CheckedRowSize(type, dimension, rowSize, error);
        if (shape != ErrorCode::Success) return shape;
        std::ifstream input(path, std::ios::binary | std::ios::ate);
        if (!input)
            return Fail(ErrorCode::FailedOpenFile, "Cannot open hierarchy vector catalog.", error);
        const std::streamoff fileBytes = input.tellg();
        input.seekg(0);
        SizeType count = -1;
        DimensionType savedDimension = 0;
        input.read(reinterpret_cast<char*>(&count), sizeof(count));
        input.read(reinterpret_cast<char*>(&savedDimension), sizeof(savedDimension));
        if (!input || count < 0 || (expectedCount >= 0 && count != expectedCount) ||
            savedDimension != dimension)
            return Fail(ErrorCode::FailedParseValue, "Invalid hierarchy vector catalog header.", error);
        const std::uint64_t bytes = static_cast<std::uint64_t>(count) * rowSize;
        if (bytes > (std::numeric_limits<std::size_t>::max)() ||
            bytes > static_cast<std::uint64_t>((std::numeric_limits<std::streamsize>::max)()) ||
            fileBytes < 0 ||
            static_cast<std::uint64_t>(fileBytes) != sizeof(count) + sizeof(savedDimension) + bytes)
            return Fail(ErrorCode::FailedParseValue, "Hierarchy vector catalog size mismatch.", error);
        ByteArray data = ByteArray::Alloc(static_cast<std::size_t>(bytes));
        if (bytes != 0)
            input.read(reinterpret_cast<char*>(data.Data()), static_cast<std::streamsize>(bytes));
        if (!input)
            return Fail(ErrorCode::DiskIOFail, "Cannot read hierarchy vector catalog.", error);
        catalog = std::make_shared<BasicVectorSet>(data, type, dimension, count);
        return ErrorCode::Success;
    }
    catch (const std::bad_alloc&)
    {
        return Fail(ErrorCode::MemoryOverFlow, "Cannot allocate hierarchy vector catalog.", error);
    }
    catch (const std::length_error&)
    {
        return Fail(ErrorCode::MemoryOverFlow, "Hierarchy vector catalog exceeds allocation limits.", error);
    }
}

// Sampling maps bind independently owned adjacent catalogs, never result identities.
inline ErrorCode BuildIndependentHierarchyCatalogs(
    SizeType headCount,
    const std::vector<std::vector<std::uint64_t>>& upperToLower,
    const std::vector<std::shared_ptr<VectorSet>>& fullLowerCatalogs,
    const std::shared_ptr<VectorSet>& topIndex,
    std::vector<std::shared_ptr<VectorSet>>& catalogs,
    std::string* error = nullptr)
{
    using namespace HierarchyVectorCatalogDetail;
    if (error != nullptr) error->clear();
    try
    {
        if (headCount <= 0 || upperToLower.empty() ||
            fullLowerCatalogs.size() != upperToLower.size() ||
            topIndex == nullptr || !topIndex->Available() || topIndex->Count() <= 0)
            return Fail(ErrorCode::FailedParseValue, "Incomplete routing-only hierarchy catalogs.", error);
        SizeType rowSize = 0;
        const ErrorCode shape = CheckedRowSize(
            topIndex->GetValueType(), topIndex->Dimension(), rowSize, error);
        if (shape != ErrorCode::Success) return shape;
        std::vector<std::shared_ptr<VectorSet>> result = fullLowerCatalogs;
        result.push_back(std::make_shared<ReadOnlyVectorView>(topIndex, rowSize));
        SizeType lowerCount = headCount;
        for (std::size_t level = 0; level < upperToLower.size(); ++level)
        {
            const auto& lower = result[level];
            const auto& upper = result[level + 1];
            const auto& map = upperToLower[level];
            if (std::dynamic_pointer_cast<BasicVectorSet>(lower) == nullptr ||
                lower->Count() != lowerCount || !lower->Available() ||
                lower->GetValueType() != topIndex->GetValueType() ||
                lower->Dimension() != topIndex->Dimension() ||
                lower->PerVectorDataSize() != rowSize ||
                upper == nullptr || upper->Count() <= 0 || !upper->Available() ||
                upper->GetValueType() != topIndex->GetValueType() ||
                upper->Dimension() != topIndex->Dimension() ||
                upper->PerVectorDataSize() != rowSize ||
                map.size() != static_cast<std::size_t>(upper->Count()) ||
                map.size() > static_cast<std::size_t>(lowerCount))
                return Fail(ErrorCode::FailedParseValue, "Routing-only catalog shape mismatch.", error);
            std::vector<bool> sampled(static_cast<std::size_t>(lowerCount), false);
            for (std::size_t id = 0; id < map.size(); ++id)
            {
                if (map[id] >= static_cast<std::uint64_t>(lowerCount) ||
                    sampled[static_cast<std::size_t>(map[id])])
                    return Fail(ErrorCode::FailedParseValue, "Invalid or duplicate hierarchy sample ID.", error);
                sampled[static_cast<std::size_t>(map[id])] = true;
                const void* lowerVector = lower->GetVector(static_cast<SizeType>(map[id]));
                const void* upperVector = upper->GetVector(static_cast<SizeType>(id));
                if (lowerVector == nullptr || upperVector == nullptr ||
                    std::memcmp(lowerVector, upperVector, static_cast<std::size_t>(rowSize)) != 0)
                    return Fail(ErrorCode::FailedParseValue, "Hierarchy sample coordinates do not match.", error);
                if (lowerVector == upperVector)
                    return Fail(ErrorCode::FailedParseValue, "Routing-only catalogs must own independent rows.", error);
            }
            lowerCount = upper->Count();
        }
        catalogs.swap(result);
        return ErrorCode::Success;
    }
    catch (const std::bad_alloc&)
    {
        return Fail(ErrorCode::MemoryOverFlow, "Cannot allocate routing-only hierarchy catalogs.", error);
    }
    catch (const std::length_error&)
    {
        return Fail(ErrorCode::MemoryOverFlow, "Routing-only catalog exceeds allocation limits.", error);
    }
}

// upperToLower[i] is the existing H(i+2)->H(i+1) sampling map, not a global VID
// table. Each non-top physical BasicVectorSet contains the complement of that
// map in increasing logical-ID order. The ready top vector catalog owns all top
// rows; no vector buffer is copied here. Positive logical layer counts are required.
//
// All returned views retain their backing owners, reject GetData/Normalize with
// std::logic_error, and reject Save/AppendSave with ErrorCode::Undefined (the
// native unsupported-operation result). The caller must not mutate backing
// owners or returned row pointers. Logical IDs and routing/CSR data are unchanged.
// On failure logicalCatalogs is unchanged; on success it contains H1 through top.
inline ErrorCode BuildDisjointHierarchyCatalogs(
    SizeType headCount,
    const std::vector<std::vector<std::uint64_t>>& upperToLower,
    const std::vector<std::shared_ptr<VectorSet>>& ownedLowerCatalogs,
    const std::shared_ptr<VectorSet>& topIndex,
    std::vector<std::shared_ptr<VectorSet>>& logicalCatalogs,
    std::string* error = nullptr)
{
    using namespace HierarchyVectorCatalogDetail;
    if (error != nullptr) error->clear();
    try
    {
        if (headCount <= 0)
            return Fail(ErrorCode::FailedParseValue,
                        "Hierarchy H1 logical row count must be positive.", error);
        if (ownedLowerCatalogs.size() != upperToLower.size())
            return Fail(ErrorCode::FailedParseValue,
                        "Hierarchy requires one owned lower catalog per sampling map.", error);
        if (topIndex == nullptr)
            return Fail(ErrorCode::LackOfInputs,
                        "Hierarchy is missing its top vector catalog.", error);
        if (!topIndex->Available() || topIndex->Count() <= 0)
            return Fail(ErrorCode::EmptyIndex,
                        "Hierarchy requires a ready, nonempty top vector catalog.", error);

        SizeType lowerCount = headCount;
        for (const auto& map : upperToLower)
        {
            if (map.size() >
                static_cast<std::size_t>((std::numeric_limits<SizeType>::max)()))
                return Fail(ErrorCode::MemoryOverFlow,
                            "Hierarchy sampling map count exceeds native SizeType.", error);
            if (map.empty() || map.size() > static_cast<std::size_t>(lowerCount))
                return Fail(ErrorCode::FailedParseValue,
                            "Hierarchy upper count must be positive and no larger than its lower count.", error);
            lowerCount = static_cast<SizeType>(map.size());
        }
        if (topIndex->Count() != lowerCount)
            return Fail(ErrorCode::FailedParseValue,
                        "Top vector catalog count does not match the logical top catalog.", error);

        const VectorValueType type = topIndex->GetValueType();
        const DimensionType dimension = topIndex->Dimension();
        SizeType rowSize = 0;
        const ErrorCode shape = CheckedRowSize(type, dimension, rowSize, error);
        if (shape != ErrorCode::Success) return shape;

        std::vector<std::shared_ptr<VectorSet>> views;
        if (upperToLower.size() >= views.max_size())
            return Fail(ErrorCode::MemoryOverFlow,
                        "Hierarchy has too many catalog levels.", error);
        std::vector<std::shared_ptr<const BasicVectorSet>> owned(ownedLowerCatalogs.size());
        lowerCount = headCount;
        for (std::size_t level = 0; level < owned.size(); ++level)
        {
            if (ownedLowerCatalogs[level] == nullptr)
                return Fail(ErrorCode::LackOfInputs,
                            "Hierarchy is missing an owned lower catalog.", error);
            owned[level] = std::dynamic_pointer_cast<const BasicVectorSet>(ownedLowerCatalogs[level]);
            if (owned[level] == nullptr)
                return Fail(ErrorCode::FailedParseValue,
                            "Hierarchy owned lower catalogs must be native BasicVectorSets, not logical views.", error);
            if (owned[level]->GetValueType() != type)
                return Fail(ErrorCode::FailedParseValue,
                            "Hierarchy vector value types differ between catalog levels.", error);
            if (owned[level]->Dimension() != dimension || owned[level]->PerVectorDataSize() != rowSize)
                return Fail(ErrorCode::DimensionSizeMismatch,
                            "Hierarchy vector dimensions/row sizes differ between catalog levels.", error);
            const SizeType upperCount = static_cast<SizeType>(upperToLower[level].size());
            if (owned[level]->Count() != lowerCount - upperCount)
                return Fail(ErrorCode::FailedParseValue,
                            "Hierarchy owned lower count must equal logical lower count minus promoted upper count.", error);
            if (owned[level]->Count() > 0 && !owned[level]->Available())
                return Fail(ErrorCode::VectorNotFound,
                            "Owned hierarchy catalog has no vector storage.", error);
            for (SizeType id = 0; id < owned[level]->Count(); ++id)
            {
                if (owned[level]->GetVector(id) == nullptr)
                    return Fail(ErrorCode::VectorNotFound,
                                "Owned hierarchy catalog contains a missing vector row.", error);
            }
            lowerCount = upperCount;
        }
        for (SizeType id = 0; id < topIndex->Count(); ++id)
        {
            if (topIndex->GetVector(id) == nullptr)
                return Fail(ErrorCode::VectorNotFound,
                            "Top vector catalog contains a missing hierarchy vector row.", error);
        }

        views.resize(upperToLower.size() + 1);
        views.back() = std::make_shared<ReadOnlyVectorView>(topIndex, rowSize);
        for (std::size_t level = upperToLower.size(); level > 0; --level)
        {
            const std::size_t lowerLevel = level - 1;
            lowerCount = lowerLevel == 0
                ? headCount : static_cast<SizeType>(upperToLower[lowerLevel - 1].size());
            const auto& map = upperToLower[lowerLevel];
            const SizeType unassigned = (std::numeric_limits<SizeType>::max)();
            std::vector<SizeType> rows;
            if (static_cast<std::size_t>(lowerCount) > rows.max_size())
                return Fail(ErrorCode::MemoryOverFlow,
                            "Hierarchy row locator allocation exceeds the native size limit.", error);
            rows.assign(static_cast<std::size_t>(lowerCount), unassigned);
            for (std::size_t upperID = 0; upperID < map.size(); ++upperID)
            {
                const std::uint64_t lowerID = map[upperID];
                if (lowerID >= static_cast<std::uint64_t>(lowerCount))
                    return Fail(ErrorCode::FailedParseValue,
                                "Hierarchy sampling ID is outside the logical lower catalog.", error);
                SizeType& row = rows[static_cast<std::size_t>(lowerID)];
                if (row != unassigned)
                    return Fail(ErrorCode::FailedParseValue,
                                "Hierarchy sampling IDs contain a duplicate lower row.", error);
                row = -static_cast<SizeType>(upperID) - 1;
            }
            SizeType ownedID = 0;
            for (SizeType& row : rows)
            {
                if (row == unassigned) row = ownedID++;
            }
            views[lowerLevel] = std::make_shared<LowerVectorView>(
                owned[lowerLevel], views[level], std::move(rows));
        }

        logicalCatalogs.swap(views);
        return ErrorCode::Success;
    }
    catch (const std::bad_alloc&)
    {
        return Fail(ErrorCode::MemoryOverFlow,
                    "Insufficient memory to build logical hierarchy vector views.", error);
    }
    catch (const std::length_error&)
    {
        return Fail(ErrorCode::MemoryOverFlow,
                    "Hierarchy catalog allocation exceeds the native size limit.", error);
    }
}

} // namespace SPANN
} // namespace SPTAG

#endif // _SPTAG_SPANN_HIERARCHYVECTORCATALOG_H_
