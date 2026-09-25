// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/Cache/PostingSignature.h"
#include "inc/Core/TagSchema.h"
#include <array>
#include <stdexcept>

namespace SPTAG {

// One descriptor per index, never one per head. Offsets address aligned records.
struct HeadMetadataLayout {
    TagSchema schema;
    Cache::HierWidthTable widths;
    std::array<std::size_t, Cache::HIER_LEVELS> categorical{};
    std::size_t stride = 0, pure = 0, tail = 0, own = 0;
    std::size_t vid = 0, bundle = 0, flags = 0, numeric = 0, tailNumeric = 0;
    std::size_t legacyPosting = 0;
    int ownColumns = Cache::HIER_LEVELS;
    int numericColumns = 0;
    std::int32_t count = 0;
    bool compact = false, hasTail = false;
    static constexpr std::size_t Absent = (std::numeric_limits<std::size_t>::max)();

    HeadMetadataLayout() { categorical.fill(Absent); }

    static HeadMetadataLayout Compact(const TagSchema& schema,
                                     const Cache::HierWidthTable& widths, bool tails) {
        if (schema.Width() <= 0 || schema.Width() > Cache::HIER_LEVELS)
            throw std::invalid_argument("Compact head metadata requires 1..5 explicit original columns");
        HeadMetadataLayout out;
        out.schema = schema;
        out.widths = widths;
        out.compact = true;
        out.hasTail = tails;
        out.ownColumns = schema.Width();
        out.numericColumns = static_cast<int>(schema.numeric.size());
        std::size_t offset = 0;
        out.pure = out.tail = Absent;
        if (!schema.categorical.empty()) {
            out.pure = offset;
            offset += sizeof(Cache::PostingBitmask);
            if (tails) { out.tail = offset; offset += sizeof(Cache::PostingBitmask); }
        }
        out.own = offset;
        offset = (offset + out.ownColumns * sizeof(std::uint32_t) + 7) & ~std::size_t(7);
        for (int column : schema.categorical) {
            const int bits = widths.bits[column];
            if (bits < 64 || bits > 256 || bits % 64 != 0)
                throw std::invalid_argument("Invalid categorical signature width");
            out.categorical[column] = offset;
            offset += static_cast<std::size_t>(bits / 8);
        }
        out.vid = offset;
        out.bundle = offset + 4;
        out.flags = offset + 6;
        offset += 8;
        if (out.numericColumns) {
            out.numeric = offset;
            offset += out.numericColumns * Cache::NUM_QUANT_WORDS * sizeof(std::uint64_t);
            if (tails) {
                out.tailNumeric = offset;
                offset += out.numericColumns * Cache::NUM_QUANT_WORDS * sizeof(std::uint64_t);
            }
        }
        out.stride = offset;
        return out;
    }
};

class HeadOwnTagsView {
public:
    HeadOwnTagsView(const std::uint32_t* values = nullptr, int columns = 0)
        : tag(values), count(columns) {}
    const std::uint32_t* const tag;
    const int count;
    explicit operator bool() const { return tag != nullptr; }
    bool operator==(std::nullptr_t) const { return tag == nullptr; }
    bool operator!=(std::nullptr_t) const { return tag != nullptr; }
    const HeadOwnTagsView* operator->() const { return this; }
    bool MayIntersect(const Cache::HierarchicalPostingMask& query,
                      const Cache::HierWidthTable& widths) const {
        for (int column = 0; column < count; ++column)
            if (tag[column] != Cache::OWN_TAG_EMPTY &&
                query.MayContain(column, tag[column], widths)) return true;
        return false;
    }
};

class HeadPostingMaskView {
    const std::uint8_t* m_record;
    const HeadMetadataLayout* m_layout;
public:
    HeadPostingMaskView(const std::uint8_t* record = nullptr,
                        const HeadMetadataLayout* layout = nullptr)
        : m_record(record), m_layout(layout) {}
    explicit operator bool() const { return m_record != nullptr; }
    bool operator==(std::nullptr_t) const { return m_record == nullptr; }
    bool operator!=(std::nullptr_t) const { return m_record != nullptr; }
    const HeadPostingMaskView* operator->() const { return this; }
    const HeadPostingMaskView& operator*() const { return *this; }
    std::uint64_t Word(int column, int word) const {
        const auto offset = m_layout->categorical[column];
        if (offset == HeadMetadataLayout::Absent) return 0;
        std::uint64_t value;
        std::memcpy(&value, m_record + offset + word * sizeof(value), sizeof(value));
        return value;
    }
    bool MayContain(int column, std::uint32_t value,
                    const Cache::HierWidthTable& widths) const {
        if (column < 0 || column >= Cache::HIER_LEVELS) return true;
        const auto bit = value % static_cast<std::uint32_t>(widths.bits[column]);
        return (Word(column, static_cast<int>(bit >> 6)) & (1ULL << (bit & 63))) != 0;
    }
    bool MayIntersect(const Cache::HierarchicalPostingMask& query,
                      const Cache::HierWidthTable& widths) const {
        for (int column = 0; column < Cache::HIER_LEVELS; ++column) {
            const auto offset = m_layout->categorical[column];
            if (offset == HeadMetadataLayout::Absent) continue;
            for (int word = 0; word < widths.bits[column] / 64; ++word) {
                std::uint64_t value;
                std::memcpy(&value, m_record + offset + word * sizeof(value), sizeof(value));
                if (value & query.mask[widths.wordOff[column] + word]) return true;
            }
        }
        return false;
    }
    void CopyTo(Cache::HierarchicalPostingMask& output,
                const Cache::HierWidthTable& widths) const {
        output.Clear();
        for (int column = 0; column < Cache::HIER_LEVELS; ++column) {
            const auto offset = m_layout->categorical[column];
            if (offset != HeadMetadataLayout::Absent)
                std::memcpy(output.mask + widths.wordOff[column], m_record + offset,
                    widths.bits[column] / 8);
        }
    }
};
}
