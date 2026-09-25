// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/SPANN/HierarchyVectorCatalog.h"
#include "inc/Helper/AtomicFile.h"

namespace SPTAG { namespace SPANN {

class CanonicalHierarchyVectorView final :
    public HierarchyVectorCatalogDetail::ImmutableVectorView
{
    std::shared_ptr<const VectorIndex> m_heads;
    std::vector<std::uint32_t> m_ids;
public:
    CanonicalHierarchyVectorView(std::shared_ptr<const VectorIndex> heads,
                                std::vector<std::uint32_t> ids)
        : ImmutableVectorView(heads->GetVectorValueType(), heads->GetFeatureDim(),
              static_cast<SizeType>(ids.size()),
              static_cast<SizeType>(heads->GetFeatureDim() * GetValueTypeSize(heads->GetVectorValueType()))),
          m_heads(std::move(heads)), m_ids(std::move(ids)) {}
    void* GetVector(SizeType id) const override {
        if (id < 0 || id >= Count()) return nullptr;
        return const_cast<void*>(m_heads->GetSample(static_cast<SizeType>(m_ids[id])));
    }
    const std::vector<std::uint32_t>& PhysicalIDs() const { return m_ids; }
    std::size_t ResidentBytes() const { return m_ids.capacity() * sizeof(std::uint32_t); }
};

inline void CanonicalVectorHashAppend(std::uint64_t& hash, const void* value, std::size_t bytes)
{
    const auto* p = static_cast<const std::uint8_t*>(value);
    for (std::size_t i = 0; i < bytes; ++i) { hash ^= p[i]; hash *= 1099511628211ULL; }
}

inline bool SaveCanonicalHierarchyVectors(
    const std::string& path, const std::shared_ptr<VectorIndex>& heads,
    const std::vector<std::uint32_t>& ids, const VectorSet& source)
{
    if (!heads || heads->GetNumSamples() <= 0 || heads->m_pQuantizer ||
        source.Count() <= 0 || ids.size() != static_cast<std::size_t>(source.Count()) ||
        source.Dimension() != heads->GetFeatureDim() ||
        source.GetValueType() != heads->GetVectorValueType()) return false;
    const std::uint32_t header[] = {
        0x39435648U, 1U, static_cast<std::uint32_t>(heads->GetNumSamples()),
        static_cast<std::uint32_t>(heads->GetFeatureDim()),
        static_cast<std::uint32_t>(heads->GetVectorValueType()),
        static_cast<std::uint32_t>(ids.size())};
    const auto bytes = static_cast<std::size_t>(source.Dimension()) * GetValueTypeSize(source.GetValueType());
    std::uint64_t hash = 1469598103934665603ULL;
    CanonicalVectorHashAppend(hash, header, sizeof(header));
    CanonicalVectorHashAppend(hash, ids.data(), ids.size() * sizeof(ids[0]));
    for (std::size_t row = 0; row < ids.size(); ++row) {
        if (ids[row] >= header[2]) return false;
        const auto* canonical = heads->GetSample(static_cast<SizeType>(ids[row]));
        const auto* original = source.GetVector(static_cast<SizeType>(row));
        if (!canonical || !original || std::memcmp(canonical, original, bytes) != 0) return false;
        CanonicalVectorHashAppend(hash, canonical, bytes);
    }
    const auto staged = path + ".tmp";
    std::ofstream out(staged, std::ios::binary | std::ios::trunc);
    out.write(reinterpret_cast<const char*>(header), sizeof(header));
    out.write(reinterpret_cast<const char*>(&hash), sizeof(hash));
    out.write(reinterpret_cast<const char*>(ids.data()), ids.size() * sizeof(ids[0]));
    out.close();
    if (!out || !Helper::AtomicReplaceFile(staged, path)) {
        std::remove(staged.c_str());
        return false;
    }
    return true;
}

inline ErrorCode LoadCanonicalOrOwnedHierarchyVectors(
    const std::string& path, VectorValueType type, DimensionType dimension,
    SizeType count, const std::shared_ptr<VectorIndex>& heads,
    std::shared_ptr<VectorSet>& output, std::string* error = nullptr,
    const std::vector<std::uint32_t>* expectedIDs = nullptr)
{
    using namespace HierarchyVectorCatalogDetail;
    std::ifstream in(path, std::ios::binary | std::ios::ate);
    if (!in) return Fail(ErrorCode::FailedOpenFile, "Cannot open hierarchy vector catalog", error);
    const auto fileBytes = in.tellg();
    in.seekg(0);
    std::uint32_t header[6]{};
    in.read(reinterpret_cast<char*>(header), sizeof(header[0]));
    if (!in) return Fail(ErrorCode::FailedParseValue, "Truncated hierarchy vector catalog", error);
    if (header[0] != 0x39435648U && !expectedIDs)
        return LoadHierarchyVectorCatalog(path, type, dimension, count, output, error);
    if (header[0] != 0x39435648U) {
        SizeType rowBytes = 0;
        if (CheckedRowSize(type, dimension, rowBytes, error) != ErrorCode::Success ||
            !heads || heads->m_pQuantizer || heads->GetVectorValueType() != type ||
            heads->GetFeatureDim() != dimension || !header[0] ||
            header[0] != expectedIDs->size() ||
            (count >= 0 && header[0] != static_cast<std::uint32_t>(count)))
            return Fail(ErrorCode::FailedParseValue, "Invalid legacy canonical catalog shape", error);
        std::int32_t savedDimension = 0;
        in.read(reinterpret_cast<char*>(&savedDimension), sizeof(savedDimension));
        if (!in || savedDimension != dimension || fileBytes < 0 ||
            static_cast<std::uint64_t>(fileBytes) != 8 + static_cast<std::uint64_t>(header[0]) * rowBytes)
            return Fail(ErrorCode::FailedParseValue, "Invalid legacy vector catalog length", error);
        const auto rowsPerChunk = (std::max)(std::size_t(1), (std::size_t(1) << 20) / rowBytes);
        std::vector<std::uint8_t> chunk((std::min)(rowsPerChunk, expectedIDs->size()) * rowBytes);
        for (std::size_t first = 0; first < expectedIDs->size();) {
            const auto rows = (std::min)(rowsPerChunk, expectedIDs->size() - first);
            in.read(reinterpret_cast<char*>(chunk.data()), rows * rowBytes);
            if (!in) return Fail(ErrorCode::DiskIOFail, "Truncated legacy vector payload", error);
            for (std::size_t offset = 0; offset < rows; ++offset) {
                const auto id = (*expectedIDs)[first + offset];
                const auto* sample = id < static_cast<std::uint32_t>(heads->GetNumSamples())
                    ? heads->GetSample(static_cast<SizeType>(id)) : nullptr;
                if (!sample || std::memcmp(sample, chunk.data() + offset * rowBytes, rowBytes) != 0)
                    return Fail(ErrorCode::FailedParseValue, "Legacy representative differs from canonical H1", error);
            }
            first += rows;
        }
        output = std::make_shared<CanonicalHierarchyVectorView>(heads, *expectedIDs);
        return ErrorCode::Success;
    }
    in.read(reinterpret_cast<char*>(header + 1), sizeof(header) - sizeof(header[0]));
    std::uint64_t expected = 0;
    in.read(reinterpret_cast<char*>(&expected), sizeof(expected));
    if (!in || !heads || heads->m_pQuantizer || header[1] != 1 ||
        header[2] != static_cast<std::uint32_t>(heads->GetNumSamples()) ||
        header[3] != static_cast<std::uint32_t>(dimension) ||
        header[4] != static_cast<std::uint32_t>(type) ||
        heads->GetFeatureDim() != dimension || heads->GetVectorValueType() != type ||
        !header[5] || header[5] > header[2] ||
        (count >= 0 && header[5] != static_cast<std::uint32_t>(count)) ||
        fileBytes < 0 || static_cast<std::uint64_t>(fileBytes) !=
            sizeof(header) + sizeof(expected) + static_cast<std::uint64_t>(header[5]) * 4)
        return Fail(ErrorCode::FailedParseValue, "Invalid canonical hierarchy vector header", error);
    std::vector<std::uint32_t> ids(header[5]);
    in.read(reinterpret_cast<char*>(ids.data()), ids.size() * sizeof(ids[0]));
    if (!in) return Fail(ErrorCode::DiskIOFail, "Cannot read canonical hierarchy IDs", error);
    if (expectedIDs && ids != *expectedIDs)
        return Fail(ErrorCode::FailedParseValue, "Canonical IDs differ from persisted hierarchy geometry", error);
    auto hash = std::uint64_t(1469598103934665603ULL);
    CanonicalVectorHashAppend(hash, header, sizeof(header));
    CanonicalVectorHashAppend(hash, ids.data(), ids.size() * sizeof(ids[0]));
    const auto bytes = static_cast<std::size_t>(dimension) * GetValueTypeSize(type);
    for (auto id : ids) {
        if (id >= header[2] || !heads->GetSample(static_cast<SizeType>(id)))
            return Fail(ErrorCode::FailedParseValue, "Canonical H1 ID out of bounds", error);
        CanonicalVectorHashAppend(hash, heads->GetSample(static_cast<SizeType>(id)), bytes);
    }
    if (hash != expected)
        return Fail(ErrorCode::FailedParseValue, "Canonical hierarchy vector authentication failed", error);
    output = std::make_shared<CanonicalHierarchyVectorView>(heads, std::move(ids));
    return ErrorCode::Success;
}

inline bool FlattenHierarchyIDs(
    const std::vector<std::uint64_t>& lowerIDs, SizeType headCount,
    const std::vector<std::uint32_t>& previous, std::vector<std::uint32_t>& output)
{
    std::vector<std::uint32_t> result;
    result.reserve(lowerIDs.size());
    for (auto id : lowerIDs) {
        if (previous.empty()) {
            if (id >= static_cast<std::uint64_t>(headCount)) return false;
            result.push_back(static_cast<std::uint32_t>(id));
        } else {
            if (id >= previous.size()) return false;
            result.push_back(previous[id]);
        }
    }
    output = std::move(result);
    return true;
}
}}
