// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_SPANN_HEADNODEMETADATA_H_
#define _SPTAG_SPANN_HEADNODEMETADATA_H_

#include "inc/Core/VectorIndex.h"
#include "inc/Core/SPANN/LimitedTagSupport.h"
#include "inc/Helper/AtomicFile.h"

#include <array>
#include <cstdint>
#include <fstream>
#include <functional>
#include <limits>
#include <string>
#include <utility>
#include <vector>

namespace SPTAG
{
namespace SPANN
{

// Shared with the wrapper's V8 head_node_meta.bin. Routing layers never use
// this format: only H1 owns real attributes and canonical result identities.
struct HeadNodeMetadataHeader
{
    std::int32_t version = 8;
    std::int32_t numSamples = 0;
    std::int32_t numQuantCols = 0;
    std::int32_t stride = 0;
};
static_assert(sizeof(HeadNodeMetadataHeader) == 16, "H1 V8 metadata header changed");

constexpr std::uint32_t HeadMetadataOwnTags = 1U;
constexpr std::uint32_t HeadMetadataPostingMasks = 2U;
constexpr std::uint32_t HeadMetadataTailSignatures = 4U;

inline void AppendHeadMetadataHash(std::uint64_t& hash, const void* data, std::size_t bytes)
{
    const auto* input = static_cast<const std::uint8_t*>(data);
    for (std::size_t i = 0; i < bytes; ++i) {
        hash ^= input[i];
        hash *= 1099511628211ULL;
    }
}

inline std::uint64_t HeadMetadataHashSeed(
    const HeadNodeMetadataHeader& header, const std::int32_t* widths,
    std::uint32_t flags, std::uint64_t domain, std::uint64_t generation,
    const std::string& schema = {})
{
    std::uint64_t hash = 1469598103934665603ULL;
    AppendHeadMetadataHash(hash, &header, sizeof(header));
    AppendHeadMetadataHash(hash, widths, sizeof(std::int32_t) * Cache::HIER_LEVELS);
    AppendHeadMetadataHash(hash, &flags, sizeof(flags));
    AppendHeadMetadataHash(hash, &domain, sizeof(domain));
    AppendHeadMetadataHash(hash, &generation, sizeof(generation));
    if (header.version == 9) {
        const std::uint32_t size = static_cast<std::uint32_t>(schema.size());
        AppendHeadMetadataHash(hash, &size, sizeof(size));
        AppendHeadMetadataHash(hash, schema.data(), schema.size());
    }
    return hash;
}

inline std::uint64_t HeadMetadataFingerprint(
    const HeadNodeMetadataHeader& header, const std::int32_t* widths,
    std::uint32_t flags, std::uint64_t numericDomain, std::uint64_t generation,
    const std::vector<std::uint8_t>& blob)
{
    std::uint64_t hash = 1469598103934665603ULL;
    const auto append = [&](const void* data, std::size_t bytes) {
        const auto* input = static_cast<const std::uint8_t*>(data);
        for (std::size_t i = 0; i < bytes; ++i)
        {
            hash ^= input[i];
            hash *= 1099511628211ULL;
        }
    };
    append(&header, sizeof(header));
    append(widths, sizeof(std::int32_t) * Cache::HIER_LEVELS);
    append(&flags, sizeof(flags));
    append(&numericDomain, sizeof(numericDomain));
    append(&generation, sizeof(generation));
    append(blob.data(), blob.size());
    return hash;
}

inline bool SaveHeadNodeMetadataV8(
    const std::string& path, const std::shared_ptr<VectorIndex>& index,
    std::uint64_t generation)
{
    if (index == nullptr || index->GetHeadMetadataLayout().compact || !index->HasHeadNodeMeta() ||
        (index->HasHeadNodeTailPS() && generation == 0) ||
        index->GetHeadNodeMetaStride() >
            static_cast<std::size_t>((std::numeric_limits<std::int32_t>::max)()))
        return false;
    HeadNodeMetadataHeader header;
    header.numSamples = index->GetHeadNodeMetaSampleCount();
    header.numQuantCols = index->GetHeadNodeNumQuantCols();
    header.stride = static_cast<std::int32_t>(index->GetHeadNodeMetaStride());
    if (header.numSamples <= 0 || header.stride <= 0) return false;
    if (generation != 0)
        for (SizeType head = 0; head < header.numSamples; ++head)
        {
            const SizeType vid = index->GetHeadNodeGlobalVID(head);
            if (vid < 0 || vid == MaxSize) return false;
        }
    const auto widths = index->GetHeadNodeHierWidths();
    std::array<std::int32_t, Cache::HIER_LEVELS> bits;
    for (int level = 0; level < Cache::HIER_LEVELS; ++level)
        bits[static_cast<std::size_t>(level)] = widths.bits[level];
    const std::uint32_t flags =
        (index->HasHeadNodeOwnTags() ? HeadMetadataOwnTags : 0U) |
        (index->HasHeadNodePostingHierMasks() ? HeadMetadataPostingMasks : 0U) |
        (index->HasHeadNodeTailPS() ? HeadMetadataTailSignatures : 0U);
    const std::uint64_t numericDomain = index->GetHeadNodeNumericDomainFingerprint();
    const auto& blob = static_cast<const VectorIndex&>(*index).GetHeadNodeMetaBlob();
    if (blob.size() != static_cast<std::uint64_t>(header.numSamples) * header.stride ||
        (header.numQuantCols > 0 && numericDomain == 0))
        return false;
    const std::uint64_t content = HeadMetadataFingerprint(
        header, bits.data(), flags, numericDomain, generation, blob);
    const std::string staged = path + ".tmp";
    std::ofstream output(staged, std::ios::binary | std::ios::trunc);
    output.write(reinterpret_cast<const char*>(&header), sizeof(header));
    output.write(reinterpret_cast<const char*>(bits.data()), sizeof(bits));
    output.write(reinterpret_cast<const char*>(&flags), sizeof(flags));
    output.write(reinterpret_cast<const char*>(&numericDomain), sizeof(numericDomain));
    output.write(reinterpret_cast<const char*>(&generation), sizeof(generation));
    output.write(reinterpret_cast<const char*>(&content), sizeof(content));
    output.write(reinterpret_cast<const char*>(blob.data()),
        static_cast<std::streamsize>(blob.size()));
    output.close();
    if (!output || !Helper::AtomicReplaceFile(staged, path))
    {
        std::remove(staged.c_str());
        return false;
    }
    return true;
}

inline bool LoadHeadNodeMetadataV8(
    const std::string& path, const std::shared_ptr<VectorIndex>& index,
    SizeType expectedCount, std::uint64_t expectedGeneration,
    const std::function<SizeType(SizeType)>& canonicalVID)
{
    if (index == nullptr || expectedCount <= 0 || !canonicalVID) return false;
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input) return false;
    const std::streamoff fileBytes = input.tellg();
    input.seekg(0);
    HeadNodeMetadataHeader header;
    std::array<std::int32_t, Cache::HIER_LEVELS> bits;
    std::uint32_t flags = 0;
    std::uint64_t numericDomain = 0, generation = 0, content = 0;
    input.read(reinterpret_cast<char*>(&header), sizeof(header));
    input.read(reinterpret_cast<char*>(bits.data()), sizeof(bits));
    input.read(reinterpret_cast<char*>(&flags), sizeof(flags));
    input.read(reinterpret_cast<char*>(&numericDomain), sizeof(numericDomain));
    input.read(reinterpret_cast<char*>(&generation), sizeof(generation));
    input.read(reinterpret_cast<char*>(&content), sizeof(content));
    if (!input || header.version != 8 || header.numSamples != expectedCount ||
        header.numQuantCols < 0 || header.stride <= 0 || (flags & ~7U) != 0 ||
        generation != expectedGeneration || content == 0 ||
        (header.numQuantCols > 0 && numericDomain == 0))
        return false;
    Cache::HierWidthTable widths;
    std::array<int, Cache::HIER_LEVELS> nativeWidths;
    for (int level = 0; level < Cache::HIER_LEVELS; ++level)
        nativeWidths[static_cast<std::size_t>(level)] = bits[static_cast<std::size_t>(level)];
    widths.Set(nativeWidths.data(), Cache::HIER_LEVELS);
    for (int level = 0; level < Cache::HIER_LEVELS; ++level)
        if (widths.bits[level] != bits[static_cast<std::size_t>(level)]) return false;
    const bool tails = (flags & HeadMetadataTailSignatures) != 0;
    std::size_t stride = 0;
    if (!VectorIndex::TryComputeHeadNodeMetaStride(header.numQuantCols, widths, tails, stride) ||
        stride != static_cast<std::size_t>(header.stride) ||
        static_cast<std::size_t>(expectedCount) >
            (std::numeric_limits<std::size_t>::max)() / stride)
        return false;
    const std::size_t bytes = static_cast<std::size_t>(expectedCount) * stride;
    constexpr std::size_t prefixBytes =
        sizeof(HeadNodeMetadataHeader) + sizeof(bits) + sizeof(flags) + 3 * sizeof(std::uint64_t);
    if (fileBytes < 0 || bytes > static_cast<std::size_t>((std::numeric_limits<std::streamsize>::max)()) ||
        static_cast<std::uint64_t>(fileBytes) != prefixBytes + static_cast<std::uint64_t>(bytes))
        return false;
    std::vector<std::uint8_t> blob(bytes);
    input.read(reinterpret_cast<char*>(blob.data()), static_cast<std::streamsize>(blob.size()));
    if (!input || HeadMetadataFingerprint(
            header, bits.data(), flags, numericDomain, generation, blob) != content)
        return false;
    if (!index->AdoptHeadNodeMeta(
            expectedCount, header.numQuantCols, widths, tails, std::move(blob)) ||
        index->GetHeadNodeMetaStride() != stride)
        return false;
    index->SetHeadNodeNumericDomainFingerprint(numericDomain);
    index->SetHeadNodeOwnTagsAvailable((flags & HeadMetadataOwnTags) != 0);
    index->SetHeadNodePostingHierMasksAvailable((flags & HeadMetadataPostingMasks) != 0);
    for (SizeType head = 0; head < expectedCount; ++head)
    {
        const SizeType expected = canonicalVID(head);
        if (expected < 0 || expected == MaxSize || index->GetHeadNodeGlobalVID(head) != expected)
        {
            index->ClearHeadNodeMeta();
            return false;
        }
    }
    return true;
}

inline bool SaveHeadNodeMetadata(
    const std::string& path, const std::shared_ptr<VectorIndex>& index,
    std::uint64_t generation)
{
    if (!index) return false;
    if (!index->GetHeadMetadataLayout().compact)
        return SaveHeadNodeMetadataV8(path, index, generation);
    const auto& layout = index->GetHeadMetadataLayout();
    HeadNodeMetadataHeader header;
    header.version = 9;
    header.numSamples = index->GetHeadNodeMetaSampleCount();
    header.numQuantCols = layout.numericColumns;
    header.stride = static_cast<std::int32_t>(layout.stride);
    if (header.numSamples <= 0 || (layout.hasTail && generation == 0)) return false;
    const auto& blob = static_cast<const VectorIndex&>(*index).GetHeadNodeMetaBlob();
    const auto domain = index->GetHeadNodeNumericDomainFingerprint();
    if (blob.size() != static_cast<std::size_t>(header.numSamples) * layout.stride ||
        (layout.numericColumns && domain == 0)) return false;
    for (SizeType head = 0; generation && head < header.numSamples; ++head)
        if (index->GetHeadNodeGlobalVID(head) < 0 ||
            index->GetHeadNodeGlobalVID(head) == MaxSize) return false;
    std::array<std::int32_t, Cache::HIER_LEVELS> bits;
    std::copy(std::begin(layout.widths.bits), std::end(layout.widths.bits), bits.begin());
    const std::uint32_t flags =
        (index->HasHeadNodeOwnTags() ? HeadMetadataOwnTags : 0U) |
        (index->HasHeadNodePostingHierMasks() ? HeadMetadataPostingMasks : 0U) |
        (layout.hasTail ? HeadMetadataTailSignatures : 0U);
    const auto& schema = layout.schema.text;
    const std::uint32_t length = static_cast<std::uint32_t>(schema.size());
    auto content = HeadMetadataHashSeed(header, bits.data(), flags, domain, generation, schema);
    AppendHeadMetadataHash(content, blob.data(), blob.size());
    const auto staged = path + ".tmp";
    std::ofstream output(staged, std::ios::binary | std::ios::trunc);
    output.write(reinterpret_cast<const char*>(&header), sizeof(header));
    output.write(reinterpret_cast<const char*>(bits.data()), sizeof(bits));
    output.write(reinterpret_cast<const char*>(&flags), sizeof(flags));
    output.write(reinterpret_cast<const char*>(&domain), sizeof(domain));
    output.write(reinterpret_cast<const char*>(&generation), sizeof(generation));
    output.write(reinterpret_cast<const char*>(&content), sizeof(content));
    output.write(reinterpret_cast<const char*>(&length), sizeof(length));
    output.write(schema.data(), schema.size());
    output.write(reinterpret_cast<const char*>(blob.data()), blob.size());
    output.close();
    if (!output || !Helper::AtomicReplaceFile(staged, path)) {
        std::remove(staged.c_str());
        return false;
    }
    return true;
}

// V8 conversion keeps only a bounded input chunk alongside the compact output.
// An explicit original-column schema is mandatory for dropping reserved lanes.
inline bool LoadHeadNodeMetadata(
    const std::string& path, const std::shared_ptr<VectorIndex>& index,
    SizeType expectedCount, std::uint64_t expectedGeneration,
    const std::function<SizeType(SizeType)>& canonicalVID,
    const TagSchema* expectedSchema = nullptr,
    const std::function<bool()>& validateIdentities = {})
{
    if (!index || expectedCount <= 0 || (!canonicalVID && !validateIdentities)) return false;
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input) return false;
    const auto fileBytes = input.tellg();
    input.seekg(0);
    HeadNodeMetadataHeader header;
    std::array<std::int32_t, Cache::HIER_LEVELS> bits{};
    std::uint32_t flags = 0;
    std::uint64_t domain = 0, generation = 0, content = 0;
    input.read(reinterpret_cast<char*>(&header), sizeof(header));
    input.read(reinterpret_cast<char*>(bits.data()), sizeof(bits));
    input.read(reinterpret_cast<char*>(&flags), sizeof(flags));
    input.read(reinterpret_cast<char*>(&domain), sizeof(domain));
    input.read(reinterpret_cast<char*>(&generation), sizeof(generation));
    input.read(reinterpret_cast<char*>(&content), sizeof(content));
    if (!input || (header.version != 8 && header.version != 9) ||
        header.numSamples != expectedCount || header.numQuantCols < 0 ||
        header.stride <= 0 || (flags & ~7U) || generation != expectedGeneration ||
        !content || (header.numQuantCols && !domain)) return false;
    if (header.version == 8 && !expectedSchema)
        return LoadHeadNodeMetadataV8(path, index, expectedCount, expectedGeneration, canonicalVID);
    try {
        Cache::HierWidthTable widths;
        std::array<int, Cache::HIER_LEVELS> nativeBits;
        std::copy(bits.begin(), bits.end(), nativeBits.begin());
        widths.Set(nativeBits.data(), Cache::HIER_LEVELS);
        for (int column = 0; column < Cache::HIER_LEVELS; ++column)
            if (widths.bits[column] != bits[column]) return false;
        std::string schemaText;
        if (header.version == 9) {
            std::uint32_t length = 0;
            input.read(reinterpret_cast<char*>(&length), sizeof(length));
            if (!input || length == 0 || length > 128) return false;
            schemaText.resize(length);
            input.read(&schemaText[0], length);
            if (!input) return false;
        } else schemaText = expectedSchema->text;
        const auto schema = TagSchema::Parse(schemaText);
        if (schema.text != schemaText || (expectedSchema && schema.text != expectedSchema->text) ||
            schema.numeric.size() != static_cast<std::size_t>(header.numQuantCols)) return false;
        const bool tails = (flags & HeadMetadataTailSignatures) != 0;
        const auto layout = HeadMetadataLayout::Compact(schema, widths, tails);
        std::size_t sourceStride = layout.stride;
        if (header.version == 8 &&
            !VectorIndex::TryComputeHeadNodeMetaStride(header.numQuantCols, widths, tails, sourceStride))
            return false;
        if (sourceStride != static_cast<std::size_t>(header.stride) ||
            fileBytes < 0 || static_cast<std::uint64_t>(fileBytes) !=
            static_cast<std::uint64_t>(input.tellg()) +
                static_cast<std::uint64_t>(expectedCount) * sourceStride) return false;
        index->InitializeCompactHeadNodeMeta(expectedCount, schema, widths, tails);
        auto hash = HeadMetadataHashSeed(header, bits.data(), flags, domain, generation, schemaText);
        if (header.version == 9) {
            auto& blob = index->GetHeadNodeMetaBlob();
            input.read(reinterpret_cast<char*>(blob.data()), blob.size());
            AppendHeadMetadataHash(hash, blob.data(), blob.size());
        } else {
            auto rowIndex = VectorIndex::CreateInstance(IndexAlgoType::BKT, VectorValueType::Float);
            rowIndex->InitializeHeadNodeMeta(1, header.numQuantCols, widths, tails);
            rowIndex->SetHeadNodeOwnTagsAvailable(true);
            rowIndex->SetHeadNodePostingHierMasksAvailable(true);
            auto& row = rowIndex->GetHeadNodeMetaBlob();
            const std::size_t chunkRows = 4096;
            std::vector<std::uint8_t> chunk(chunkRows * sourceStride);
            for (SizeType first = 0; first < expectedCount;) {
                const auto count = (std::min)(chunkRows, static_cast<std::size_t>(expectedCount - first));
                const auto bytes = count * sourceStride;
                input.read(reinterpret_cast<char*>(chunk.data()), bytes);
                if (!input) { index->ClearHeadNodeMeta(); return false; }
                AppendHeadMetadataHash(hash, chunk.data(), bytes);
                for (std::size_t offset = 0; offset < count; ++offset) {
                    std::memcpy(row.data(), chunk.data() + offset * sourceStride, sourceStride);
                    const SizeType head = first + static_cast<SizeType>(offset);
                    index->SetHeadNodeGlobalVID(head, rowIndex->GetHeadNodeGlobalVID(0));
                    index->SetHeadNodeBundleNodeId(head, rowIndex->GetHeadNodeBundleNodeId(0));
                    index->SetHeadNodeHeadOnly(head, rowIndex->IsHeadNodeHeadOnly(0));
                    index->SetHeadNodePS(head, *rowIndex->GetHeadNodePS(0));
                    if (tails) index->SetHeadNodeTailPS(head, *rowIndex->GetHeadNodeTailPS(0));
                    Cache::HierarchicalOwnTags own;
                    const auto sourceOwn = rowIndex->GetHeadNodeHierMask(0);
                    std::memcpy(own.tag, sourceOwn->tag, sizeof(own.tag));
                    for (int column = schema.Width(); column < Cache::HIER_LEVELS; ++column)
                        if ((flags & HeadMetadataOwnTags) && own.tag[column] != Cache::OWN_TAG_EMPTY)
                            throw std::invalid_argument("V8 has own values outside the declared schema");
                    index->SetHeadNodeHierMask(head, own);
                    Cache::HierarchicalPostingMask mask;
                    rowIndex->GetHeadNodePostingHierMask(0).CopyTo(mask, widths);
                    index->SetHeadNodePostingHierMask(head, mask);
                    const auto bytes = schema.numeric.size() * Cache::NUM_QUANT_WORDS * sizeof(std::uint64_t);
                    if (bytes) {
                        std::memcpy(index->GetHeadNodeNumQuantMutable(head), rowIndex->GetHeadNodeNumQuant(0), bytes);
                        if (tails) std::memcpy(index->GetHeadNodeTailNumQuantMutable(head),
                            rowIndex->GetHeadNodeTailNumQuant(0), bytes);
                    }
                }
                first += static_cast<SizeType>(count);
            }
        }
        if (!input || hash != content) { index->ClearHeadNodeMeta(); return false; }
        index->SetHeadNodeNumericDomainFingerprint(domain);
        index->SetHeadNodeOwnTagsAvailable((flags & HeadMetadataOwnTags) != 0);
        index->SetHeadNodePostingHierMasksAvailable((flags & HeadMetadataPostingMasks) != 0);
        for (SizeType head = 0; canonicalVID && head < expectedCount; ++head) {
            const auto expected = canonicalVID(head);
            if (expected < 0 || expected == MaxSize || index->GetHeadNodeGlobalVID(head) != expected) {
                index->ClearHeadNodeMeta();
                return false;
            }
        }
        if (validateIdentities && !validateIdentities()) {
            index->ClearHeadNodeMeta();
            return false;
        }
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info,
            "Compact H1 metadata: source=V%d schema=%s stride=%zu payload=%zu capacity=%zu bytes.\n",
            header.version, schema.text.c_str(), layout.stride, index->GetHeadNodeMetaBlob().size(),
            index->GetHeadNodeMetaBlob().capacity());
        return true;
    } catch (const std::invalid_argument& error) {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Compact head metadata: %s\n", error.what());
        index->ClearHeadNodeMeta();
        return false;
    }
}

inline bool CollectHierarchyHeadSignatures(
    const VectorIndex& index, const LimitedTagSupport& support,
    std::vector<Cache::PostingBitmask>& signatures)
{
    if (!index.HasHeadNodeMeta() || !index.HasHeadNodeOwnTags() ||
        !index.HasHeadNodeTailPS() ||
        index.GetHeadNodeMetaSampleCount() != index.GetNumSamples() ||
        support.HeadCount() != index.GetNumSamples() ||
        support.KeyColumn() < 0 || support.KeyColumn() >= support.AttributeCount())
        return false;
    signatures.resize(static_cast<std::size_t>(index.GetNumSamples()));
    for (SizeType head = 0; head < index.GetNumSamples(); ++head)
    {
        const auto* pure = index.GetHeadNodePS(head);
        const auto* attributes = support.HeadAttributes(head);
        const auto* tags = support.HeadTagData(head);
        if (pure == nullptr || attributes == nullptr || tags == nullptr) return false;
        Cache::PostingBitmask supported;
        for (int slot = 0; slot < support.SlotsPerHead(); ++slot)
            if (tags[slot] != LimitedTagSupport::EmptyTag) supported.Insert(tags[slot]);
        const auto* extraTags = support.ExtraTagData(head);
        const auto extraCount = support.ExtraTagCount(head);
        for (std::size_t slot = 0; slot < extraCount; ++slot)
            supported.Insert(extraTags[slot]);
        auto& signature = signatures[static_cast<std::size_t>(head)];
        signature = *pure;
        // Only anchored H-prefix queries use this signature. Support restricts
        // the shared PS mask to possible routing-key bits; O scans bypass it.
        for (std::size_t word = 0; word < sizeof(signature.bits) / sizeof(signature.bits[0]); ++word)
            signature.bits[word] &= supported.bits[word];
        signature.Insert(attributes[support.KeyColumn()]);
    }
    return true;
}

} // namespace SPANN
} // namespace SPTAG

#endif // _SPTAG_SPANN_HEADNODEMETADATA_H_
