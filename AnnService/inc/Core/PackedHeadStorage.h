// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/Common/SharedAlignedBuffer.h"
#include "inc/Core/HeadMetadataLayout.h"

#include <algorithm>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <utility>
#include <vector>
#include <xmmintrin.h>

namespace SPTAG {

struct PackedHeadLayout {
    struct Field {
        std::size_t serialized, packed, bytes;
    };

    HeadMetadataLayout serialized;
    HeadMetadataLayout metadata;
    std::vector<Field> fields;
    std::size_t additionalSupport = 0, ownSupportPresent = 0;
    std::size_t vector = 0, vectorBytes = 0, edges = 0, stride = 0;
    DimensionType degree = 0;
    int keyColumn = 0, supportSlots = 0;

    bool MatchesMetadata(const HeadMetadataLayout& other) const
    {
        return serialized.compact == other.compact && serialized.hasTail == other.hasTail &&
            serialized.schema.text == other.schema.text &&
            serialized.ownColumns == other.ownColumns && serialized.numericColumns == other.numericColumns &&
            serialized.stride == other.stride && serialized.own == other.own &&
            serialized.pure == other.pure && serialized.tail == other.tail &&
            serialized.vid == other.vid && serialized.bundle == other.bundle &&
            serialized.flags == other.flags && serialized.numeric == other.numeric &&
            serialized.tailNumeric == other.tailNumeric && serialized.categorical == other.categorical &&
            std::equal(std::begin(serialized.widths.bits), std::end(serialized.widths.bits),
                       std::begin(other.widths.bits));
    }

    static std::size_t Align(std::size_t value, std::size_t alignment)
    {
        if (value > (std::numeric_limits<std::size_t>::max)() - (alignment - 1))
            throw std::overflow_error("Packed head layout overflow");
        return (value + alignment - 1) / alignment * alignment;
    }

    static PackedHeadLayout Create(const HeadMetadataLayout& source, int key, int slots,
                                   std::size_t vectorSize, DimensionType neighbors)
    {
        if (!source.compact || !source.hasTail || source.stride == 0 ||
            key < 0 || key >= source.ownColumns ||
            std::find(source.schema.categorical.begin(), source.schema.categorical.end(), key) ==
                source.schema.categorical.end() ||
            slots <= 0 || vectorSize == 0 || neighbors <= 0)
            throw std::invalid_argument("Packed heads require compact H/O metadata and a categorical key");
        PackedHeadLayout out;
        out.serialized = source;
        out.metadata = source;
        if (!out.MatchesMetadata(HeadMetadataLayout::Compact(source.schema, source.widths, source.hasTail)))
            throw std::invalid_argument("Packed heads require a canonical compact metadata descriptor");
        out.keyColumn = key;
        out.supportSlots = slots;
        out.vectorBytes = vectorSize;
        out.degree = neighbors;
        std::size_t offset = 0;
        const auto allocate = [&](std::size_t bytes, std::size_t alignment) {
            offset = Align(offset, alignment);
            if (bytes > (std::numeric_limits<std::size_t>::max)() - offset)
                throw std::overflow_error("Packed head layout overflow");
            const auto start = offset;
            offset += bytes;
            return start;
        };
        const auto field = [&](std::size_t original, std::size_t bytes, std::size_t alignment) {
            if (original == HeadMetadataLayout::Absent || original > source.stride ||
                bytes > source.stride - original)
                throw std::invalid_argument("Invalid source metadata field");
            const auto target = allocate(bytes, alignment);
            out.fields.push_back({original, target, bytes});
            return target;
        };
        out.metadata.categorical.fill(HeadMetadataLayout::Absent);
        out.metadata.categorical[key] =
            field(source.categorical[key], source.widths.bits[key] / 8, 8);
        out.metadata.own =
            field(source.own, Align(source.ownColumns * sizeof(std::uint32_t), 8), 8);
        out.additionalSupport = allocate(static_cast<std::size_t>(slots - 1) * sizeof(std::uint32_t), 4);
        out.metadata.vid = field(source.vid, 8, 4);
        out.metadata.bundle = out.metadata.vid + 4;
        out.metadata.flags = out.metadata.vid + 6;
        out.ownSupportPresent = allocate(1, 1);
        offset = Align(offset, COMMON::SharedAlignedBuffer::Alignment);
        out.metadata.pure = field(source.pure, sizeof(Cache::PostingBitmask), 8);
        out.metadata.tail = field(source.tail, sizeof(Cache::PostingBitmask), 8);
        for (int column : source.schema.categorical)
            if (column != key)
                out.metadata.categorical[column] =
                    field(source.categorical[column], source.widths.bits[column] / 8, 8);
        if (source.numericColumns) {
            const auto bytes = source.numericColumns * Cache::NUM_QUANT_WORDS * sizeof(std::uint64_t);
            out.metadata.numeric = field(source.numeric, bytes, 8);
            out.metadata.tailNumeric = field(source.tailNumeric, bytes, 8);
        }
        out.vector = allocate(vectorSize, COMMON::SharedAlignedBuffer::Alignment);
        out.edges = allocate(static_cast<std::size_t>(neighbors) * sizeof(SizeType),
                             COMMON::SharedAlignedBuffer::Alignment);
        out.stride = Align(offset, COMMON::SharedAlignedBuffer::Alignment);
        if (out.stride > static_cast<std::size_t>((std::numeric_limits<DimensionType>::max)()))
            throw std::overflow_error("Packed head stride exceeds Dataset capacity");
        out.metadata.stride = out.stride;
        return out;
    }
};

class PackedHeadStorage {
public:
    PackedHeadStorage(PackedHeadLayout layout, SizeType count)
        : m_layout(std::move(layout)), m_count(count)
    {
        if (count <= 0 || m_layout.stride == 0 ||
            static_cast<std::size_t>(count) >
                (std::numeric_limits<std::size_t>::max)() / m_layout.stride)
            throw std::invalid_argument("Invalid packed head count");
        m_buffer = std::make_shared<COMMON::SharedAlignedBuffer>(
            static_cast<std::size_t>(count) * m_layout.stride);
        std::memset(m_buffer->Data(), 0, m_buffer->Size());
    }

    const PackedHeadLayout& Layout() const { return m_layout; }
    SizeType Count() const { return m_count; }
    const std::shared_ptr<COMMON::SharedAlignedBuffer>& Buffer() const { return m_buffer; }

    std::uint8_t* Row(SizeType head)
    {
        if (head < 0 || head >= m_count) throw std::out_of_range("Invalid packed head ID");
        return m_buffer->Data() + static_cast<std::size_t>(head) * m_layout.stride;
    }
    const std::uint8_t* Row(SizeType head) const
    {
        if (head < 0 || head >= m_count) throw std::out_of_range("Invalid packed head ID");
        return m_buffer->Data() + static_cast<std::size_t>(head) * m_layout.stride;
    }
    const std::uint32_t* Attributes(SizeType head) const
    {
        return reinterpret_cast<const std::uint32_t*>(Row(head) + m_layout.metadata.own);
    }
    std::uint32_t Support(SizeType head, int slot) const
    {
        if (slot < 0 || slot >= m_layout.supportSlots)
            throw std::out_of_range("Invalid packed support slot");
        const auto* row = Row(head);
        if (slot == 0)
            return row[m_layout.ownSupportPresent] ? Attributes(head)[m_layout.keyColumn] : UINT32_MAX;
        return reinterpret_cast<const std::uint32_t*>(row + m_layout.additionalSupport)[slot - 1];
    }
    void ImportMetadata(SizeType head, const std::uint8_t* source)
    {
        auto* target = Row(head);
        for (const auto& field : m_layout.fields)
            std::memcpy(target + field.packed, source + field.serialized, field.bytes);
    }
    void ExportMetadata(SizeType head, std::uint8_t* target) const
    {
        std::memset(target, 0, m_layout.serialized.stride);
        const auto* source = Row(head);
        for (const auto& field : m_layout.fields)
            std::memcpy(target + field.serialized, source + field.packed, field.bytes);
    }

private:
    PackedHeadLayout m_layout;
    SizeType m_count;
    std::shared_ptr<COMMON::SharedAlignedBuffer> m_buffer;
};

class PackedHeadPrefetch {
public:
    static constexpr int Lookahead = 4;

    explicit PackedHeadPrefetch(const PackedHeadStorage* storage = nullptr) : m_storage(storage) {}
    explicit operator bool() const { return m_storage != nullptr && !m_lines.empty(); }

    void Add(std::size_t offset, std::size_t bytes)
    {
        if (!m_storage || !bytes) return;
        if (offset > m_storage->Layout().vector || bytes > m_storage->Layout().vector - offset)
            throw std::invalid_argument("Packed metadata prefetch exceeds the metadata region");
        for (auto line = offset / 64 * 64; line < offset + bytes; line += 64)
            if (std::find(m_lines.begin(), m_lines.end(), line) == m_lines.end())
                m_lines.push_back(line);
    }

    void Prefetch(SizeType head) const
    {
        if (!m_storage || head < 0 || head >= m_storage->Count()) return;
        const auto* row = reinterpret_cast<const char*>(m_storage->Row(head));
        for (auto line : m_lines) _mm_prefetch(row + line, _MM_HINT_T0);
    }

    const std::vector<std::size_t>& Lines() const { return m_lines; }

private:
    const PackedHeadStorage* m_storage;
    std::vector<std::size_t> m_lines;
};

}
