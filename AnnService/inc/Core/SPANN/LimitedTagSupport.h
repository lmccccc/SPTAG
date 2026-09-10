// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_SPANN_LIMITEDTAGSUPPORT_H_
#define _SPTAG_SPANN_LIMITEDTAGSUPPORT_H_

#include "inc/Core/Common.h"
#include "inc/Helper/AtomicFile.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <limits>
#include <new>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace SPTAG
{
namespace SPANN
{

class LimitedTagSupport
{
public:
    static constexpr size_t LookupBatchSize = 64;
    static constexpr std::uint32_t EmptyTag =
        (std::numeric_limits<std::uint32_t>::max)();

    static constexpr bool IsSupportedSlotCount(int p_slots)
    {
        return p_slots > 0;
    }

#pragma pack(push, 1)
    struct HeaderV1
    {
        std::uint32_t m_magic = 0x3153544cU; // LTS1
        std::uint32_t m_version = 1;
        std::uint32_t m_headerBytes = 48;
        std::uint32_t m_headCount = 0;
        std::uint32_t m_slotsPerHead = 0;
        std::uint32_t m_voteHeadCount = 0;
        std::uint32_t m_minHeadCount = 0;
        std::uint32_t m_tagCount = 0;
        std::uint64_t m_generationFingerprint = 0;
        std::uint64_t m_bodyFingerprint = 0;
    };

    struct HeaderV2
    {
        std::uint32_t m_magic = 0x3153544cU; // LTS1
        std::uint32_t m_version = 2;
        std::uint32_t m_headerBytes = 56;
        std::uint32_t m_headCount = 0;
        std::uint32_t m_slotsPerHead = 0;
        std::uint32_t m_voteHeadCount = 0;
        std::uint32_t m_minHeadCount = 0;
        std::uint32_t m_tagCount = 0;
        std::uint32_t m_keyColumn = 0;
        std::uint32_t m_attributeCount = 0;
        std::uint64_t m_generationFingerprint = 0;
        std::uint64_t m_bodyFingerprint = 0;
    };

    struct TagCountRecord
    {
        std::uint32_t m_tag = EmptyTag;
        std::uint32_t m_reserved = 0; // v4/v5: required support heads; v3: zero.
        std::uint64_t m_count = 0;
    };

    struct Header
    {
        std::uint32_t m_magic = 0x3153544cU; // LTS1
        std::uint32_t m_version = 3;
        std::uint32_t m_headerBytes = 64;
        std::uint32_t m_headCount = 0;
        std::uint32_t m_slotsPerHead = 0;
        // Zero identifies retained-O candidates; positive values preserve the
        // legacy pre-RNG vote provenance on read/export. Not a build setting.
        std::uint32_t m_legacyVoteHeadCount = 0;
        std::uint32_t m_minHeadCount = 0;
        std::uint32_t m_tagCount = 0;
        std::uint32_t m_keyColumn = 0;
        std::uint32_t m_attributeCount = 0;
        std::uint64_t m_vectorCount = 0;
        std::uint64_t m_generationFingerprint = 0;
        std::uint64_t m_bodyFingerprint = 0;
    };

    struct HeaderV4
    {
        Header m_base;
        std::uint64_t m_extraTagCount = 0;
        // V4: immutable legacy configured cap. V5 (same 80-byte layout):
        // exact sum of source-capped per-tag deficits, never a tuning limit.
        std::uint64_t m_maxExtraSupports = 0;

        HeaderV4()
        {
            m_base.m_version = 4;
            m_base.m_headerBytes = 80;
        }
    };
#pragma pack(pop)
    static_assert(sizeof(HeaderV1) == 48,
                  "LimitedTagSupport v1 header layout changed");
    static_assert(sizeof(HeaderV2) == 56,
                  "LimitedTagSupport v2 header layout changed");
    static_assert(sizeof(TagCountRecord) == 16,
                  "LimitedTagSupport tag-count layout changed");
    static_assert(sizeof(Header) == 64,
                  "LimitedTagSupport header layout changed");
    static_assert(sizeof(HeaderV4) == 80,
                  "LimitedTagSupport v4 header layout changed");

    void Reset()
    {
        m_header = Header();
        m_tags.clear();
        m_headAttributes.clear();
        m_tagVectorCounts.clear();
        m_extraTagOffsets.clear();
        m_extraTags.clear();
        m_headsWithExtraTags.clear();
        m_inlineExtraTag = false;
        m_maxExtraSupports = 0;
        m_headsByTag.clear();
        m_legacyWithoutTagVectorCounts = false;
    }

    bool Initialize(
        SizeType p_headCount,
        int p_slotsPerHead,
        int p_minHeadCount,
        int p_keyColumn,
        int p_attributeCount,
        std::uint64_t p_generationFingerprint)
    {
        Reset();
        if (p_headCount <= 0 ||
            !IsSupportedSlotCount(p_slotsPerHead) ||
            p_minHeadCount <= 0 ||
            p_keyColumn < 0 || p_attributeCount <= 0 ||
            p_keyColumn >= p_attributeCount ||
            p_generationFingerprint == 0)
        {
            return false;
        }
        const size_t headCount = static_cast<size_t>(p_headCount);
        const size_t slots = static_cast<size_t>(p_slotsPerHead);
        if (headCount >
            (std::numeric_limits<size_t>::max)() / slots)
        {
            return false;
        }
        m_header.m_headCount =
            static_cast<std::uint32_t>(p_headCount);
        m_header.m_slotsPerHead =
            static_cast<std::uint32_t>(p_slotsPerHead);
        m_header.m_minHeadCount =
            static_cast<std::uint32_t>(p_minHeadCount);
        m_header.m_keyColumn =
            static_cast<std::uint32_t>(p_keyColumn);
        m_header.m_attributeCount =
            static_cast<std::uint32_t>(p_attributeCount);
        m_header.m_generationFingerprint =
            p_generationFingerprint;
        m_tags.assign(headCount * slots, EmptyTag);
        if (headCount >
            (std::numeric_limits<size_t>::max)() /
                static_cast<size_t>(p_attributeCount))
        {
            Reset();
            return false;
        }
        m_headAttributes.assign(
            headCount *
                static_cast<size_t>(p_attributeCount),
            0);
        return true;
    }

    bool SetTagVectorCounts(
        std::uint64_t p_vectorCount,
        const std::unordered_map<
            std::uint32_t, std::uint64_t>& p_counts)
    {
        if (HasExpansion()) return false;
        m_header.m_vectorCount = 0;
        m_tagVectorCounts.clear();
        if (p_vectorCount == 0 || p_counts.empty() ||
            p_counts.size() >
                (std::numeric_limits<std::uint32_t>::max)())
        {
            return false;
        }

        std::uint64_t total = 0;
        m_tagVectorCounts.reserve(p_counts.size());
        for (const auto& entry : p_counts)
        {
            if (entry.first == EmptyTag || entry.second == 0 ||
                entry.second > p_vectorCount ||
                total >
                    (std::numeric_limits<std::uint64_t>::max)() -
                        entry.second)
            {
                m_tagVectorCounts.clear();
                return false;
            }
            total += entry.second;
            m_tagVectorCounts.push_back(
                {entry.first, 0, entry.second});
        }
        if (total != p_vectorCount)
        {
            m_tagVectorCounts.clear();
            return false;
        }
        std::sort(
            m_tagVectorCounts.begin(),
            m_tagVectorCounts.end(),
            [](const TagCountRecord& p_left,
               const TagCountRecord& p_right) {
                return p_left.m_tag < p_right.m_tag;
            });
        m_header.m_vectorCount = p_vectorCount;
        return true;
    }

    // Expanded support is immutable: fixed-slot mutations cannot update
    // overflow rows and their effective coverage requirements atomically.
    bool ConfigureExpansion(
        std::vector<std::uint64_t> p_offsets,
        std::vector<std::uint32_t> p_extraTags,
        const std::unordered_map<
            std::uint32_t, std::uint32_t>& p_requiredCounts,
        std::string* p_error = nullptr)
    {
        if (p_error != nullptr) p_error->clear();
        auto fail = [&](const char* p_message) {
            if (p_error != nullptr) *p_error = p_message;
            return false;
        };
        if (HasExpansion())
            return fail("expanded limited-tag support is immutable");
        if (static_cast<std::uint64_t>(p_offsets.size()) !=
                static_cast<std::uint64_t>(m_header.m_headCount) + 1)
        {
            return fail("invalid limited-tag expansion shape");
        }
        if (m_tagVectorCounts.empty() ||
            p_requiredCounts.size() != m_tagVectorCounts.size())
        {
            return fail("limited-tag expansion requirements must cover every observed tag");
        }
        try
        {
            auto counts = m_tagVectorCounts;
            for (auto& entry : counts)
            {
                const auto required = p_requiredCounts.find(entry.m_tag);
                if (required == p_requiredCounts.end())
                    return fail("limited-tag expansion requirements contain an unknown or missing tag");
                entry.m_reserved = required->second;
            }
            Header expanded = m_header;
            expanded.m_version = 5;
            expanded.m_headerBytes = sizeof(HeaderV4);
            expanded.m_tagCount = static_cast<std::uint32_t>(counts.size());
            expanded.m_bodyFingerprint = 0;
            std::uint64_t requirementBound = 0;
            if (!ExpansionRequirementBound(expanded, counts, false, requirementBound, p_error) ||
                !ValidateStorage(
                    expanded, counts, p_offsets, p_extraTags,
                    requirementBound, p_error))
            {
                return false;
            }
            if (CanInlineExtraTags(p_extraTags.size()))
                m_tags.reserve(InlineTagElementCount());
            auto headsWithExtras = CanInlineExtraTags(p_extraTags.size())
                ? std::vector<std::uint64_t>() : ExtraHeadPresence(p_offsets);
            m_header = expanded;
            m_tagVectorCounts = std::move(counts);
            m_extraTagOffsets = std::move(p_offsets);
            m_extraTags = std::move(p_extraTags);
            m_headsWithExtraTags = std::move(headsWithExtras);
            InlineFirstExtraTags();
            m_maxExtraSupports = requirementBound;
            m_legacyWithoutTagVectorCounts = false;
            m_headsByTag.clear();
            return true;
        }
        catch (const std::bad_alloc&)
        {
            return fail("cannot allocate limited-tag expansion metadata");
        }
        catch (const std::length_error&)
        {
            return fail("limited-tag expansion metadata is too large");
        }
    }

    bool SetHeadAttributes(
        SizeType p_head,
        const std::uint32_t* p_attributes,
        int p_attributeCount)
    {
        if (HasExpansion() ||
            p_head < 0 || p_attributes == nullptr ||
            p_attributeCount !=
                static_cast<int>(
                    m_header.m_attributeCount) ||
            static_cast<std::uint32_t>(p_head) >=
                m_header.m_headCount)
        {
            return false;
        }
        std::copy_n(
            p_attributes,
            m_header.m_attributeCount,
            m_headAttributes.begin() +
                static_cast<size_t>(p_head) *
                    m_header.m_attributeCount);
        return true;
    }

    bool SetHeadTags(
        SizeType p_head,
        const std::vector<std::uint32_t>& p_tags)
    {
        if (HasExpansion() || p_head < 0 ||
            static_cast<std::uint32_t>(p_head) >=
                m_header.m_headCount ||
            p_tags.size() > m_header.m_slotsPerHead)
        {
            return false;
        }
        for (size_t index = 0;
             index < p_tags.size(); ++index)
        {
            if (p_tags[index] == EmptyTag)
            {
                return false;
            }
            for (size_t prior = 0;
                 prior < index; ++prior)
            {
                if (p_tags[prior] ==
                    p_tags[index])
                {
                    return false;
                }
            }
        }
        const size_t offset =
            static_cast<size_t>(p_head) *
            m_header.m_slotsPerHead;
        std::fill_n(
            m_tags.begin() + offset,
            m_header.m_slotsPerHead, EmptyTag);
        for (size_t slot = 0; slot < p_tags.size(); ++slot)
        {
            m_tags[offset + slot] = p_tags[slot];
        }
        return true;
    }

    bool AppendHead(
        const std::vector<std::uint32_t>& p_tags,
        const std::uint32_t* p_attributes,
        int p_attributeCount)
    {
        if (HasExpansion() || p_tags.empty() ||
            p_tags.size() > m_header.m_slotsPerHead ||
            p_attributes == nullptr ||
            p_attributeCount !=
                static_cast<int>(
                    m_header.m_attributeCount) ||
            p_tags.front() !=
                p_attributes[m_header.m_keyColumn] ||
            m_header.m_headCount ==
                (std::numeric_limits<std::uint32_t>::max)())
        {
            return false;
        }
        std::unordered_set<std::uint32_t> unique;
        for (std::uint32_t tag : p_tags)
        {
            if (tag == EmptyTag ||
                !unique.insert(tag).second)
            {
                return false;
            }
        }
        if (m_tags.size() >
                (std::numeric_limits<size_t>::max)() -
                    m_header.m_slotsPerHead ||
            m_headAttributes.size() >
                (std::numeric_limits<size_t>::max)() -
                    m_header.m_attributeCount)
        {
            return false;
        }

        m_tags.insert(
            m_tags.end(), m_header.m_slotsPerHead,
            EmptyTag);
        std::copy(
            p_tags.begin(), p_tags.end(),
            m_tags.end() - m_header.m_slotsPerHead);
        m_headAttributes.insert(
            m_headAttributes.end(), p_attributes,
            p_attributes + p_attributeCount);
        ++m_header.m_headCount;
        return true;
    }

    bool AppendTombstoneHead()
    {
        if (HasExpansion() ||
            m_header.m_headCount ==
                (std::numeric_limits<std::uint32_t>::max)() ||
            m_tags.size() >
                (std::numeric_limits<size_t>::max)() -
                    m_header.m_slotsPerHead ||
            m_headAttributes.size() >
                (std::numeric_limits<size_t>::max)() -
                    m_header.m_attributeCount)
        {
            return false;
        }
        m_tags.insert(
            m_tags.end(), m_header.m_slotsPerHead,
            EmptyTag);
        m_headAttributes.insert(
            m_headAttributes.end(),
            m_header.m_attributeCount, 0);
        ++m_header.m_headCount;
        return true;
    }

    bool TombstoneHead(SizeType p_head)
    {
        if (HasExpansion() || p_head < 0 ||
            static_cast<std::uint32_t>(p_head) >=
                m_header.m_headCount)
        {
            return false;
        }
        std::fill_n(
            m_tags.begin() +
                static_cast<size_t>(p_head) *
                    m_header.m_slotsPerHead,
            m_header.m_slotsPerHead, EmptyTag);
        std::fill_n(
            m_headAttributes.begin() +
                static_cast<size_t>(p_head) *
                    m_header.m_attributeCount,
            m_header.m_attributeCount, 0);
        return true;
    }

    bool ReplaceHead(
        SizeType p_head,
        const std::vector<std::uint32_t>& p_tags,
        const std::uint32_t* p_attributes,
        int p_attributeCount)
    {
        if (HasExpansion() ||
            p_tags.empty() || p_attributes == nullptr ||
            p_attributeCount !=
                static_cast<int>(
                    m_header.m_attributeCount) ||
            p_tags.front() !=
                p_attributes[m_header.m_keyColumn])
        {
            return false;
        }
        return SetHeadAttributes(
                   p_head, p_attributes,
                   p_attributeCount) &&
            SetHeadTags(p_head, p_tags);
    }

    bool PrepareRuntimeAppendHead(
        const std::vector<std::uint32_t>& p_tags,
        const std::uint32_t* p_attributes,
        int p_attributeCount)
    {
        if (HasExpansion() ||
            !ValidActiveRow(
                p_tags, p_attributes,
                p_attributeCount) ||
            m_header.m_headCount ==
                (std::numeric_limits<std::uint32_t>::max)())
        {
            return false;
        }
        try
        {
            m_tags.reserve(
                m_tags.size() +
                m_header.m_slotsPerHead);
            m_headAttributes.reserve(
                m_headAttributes.size() +
                m_header.m_attributeCount);
            for (std::uint32_t tag : p_tags)
            {
                auto found = m_headsByTag.find(tag);
                if (found == m_headsByTag.end())
                    return false;
                found->second.reserve(
                    found->second.size() + 1);
            }
        }
        catch (const std::bad_alloc&)
        {
            return false;
        }
        catch (const std::length_error&)
        {
            return false;
        }
        return true;
    }

    bool AppendRuntimeHead(
        const std::vector<std::uint32_t>& p_tags,
        const std::uint32_t* p_attributes,
        int p_attributeCount)
    {
        if (!PrepareRuntimeAppendHead(
                p_tags, p_attributes,
                p_attributeCount))
        {
            return false;
        }
        return AppendPreparedRuntimeHead(
            p_tags, p_attributes,
            p_attributeCount);
    }

    bool AppendPreparedRuntimeHead(
        const std::vector<std::uint32_t>& p_tags,
        const std::uint32_t* p_attributes,
        int p_attributeCount)
    {
        if (HasExpansion() ||
            !ValidActiveRow(
                p_tags, p_attributes,
                p_attributeCount) ||
            m_header.m_headCount ==
                (std::numeric_limits<std::uint32_t>::max)() ||
            m_tags.capacity() <
                m_tags.size() +
                    m_header.m_slotsPerHead ||
            m_headAttributes.capacity() <
                m_headAttributes.size() +
                    m_header.m_attributeCount)
        {
            return false;
        }
        for (std::uint32_t tag : p_tags)
        {
            const auto found =
                m_headsByTag.find(tag);
            if (found == m_headsByTag.end() ||
                found->second.capacity() <
                    found->second.size() + 1)
            {
                return false;
            }
        }
        const SizeType head =
            static_cast<SizeType>(
                m_header.m_headCount);
        m_tags.insert(
            m_tags.end(),
            m_header.m_slotsPerHead,
            EmptyTag);
        std::copy(
            p_tags.begin(), p_tags.end(),
            m_tags.end() -
                m_header.m_slotsPerHead);
        m_headAttributes.insert(
            m_headAttributes.end(),
            p_attributes,
            p_attributes + p_attributeCount);
        for (std::uint32_t tag : p_tags)
        {
            m_headsByTag.find(tag)
                ->second.push_back(head);
        }
        ++m_header.m_headCount;
        m_header.m_bodyFingerprint = 0;
        return true;
    }

    bool PrepareRuntimeTombstoneHead()
    {
        if (HasExpansion() ||
            m_header.m_headCount ==
            (std::numeric_limits<std::uint32_t>::max)())
        {
            return false;
        }
        try
        {
            m_tags.reserve(
                m_tags.size() +
                m_header.m_slotsPerHead);
            m_headAttributes.reserve(
                m_headAttributes.size() +
                m_header.m_attributeCount);
        }
        catch (const std::bad_alloc&)
        {
            return false;
        }
        catch (const std::length_error&)
        {
            return false;
        }
        return true;
    }

    bool AppendRuntimeTombstoneHead()
    {
        if (!PrepareRuntimeTombstoneHead())
            return false;
        return AppendPreparedRuntimeTombstoneHead();
    }

    bool AppendPreparedRuntimeTombstoneHead()
    {
        if (HasExpansion() ||
            m_header.m_headCount ==
                (std::numeric_limits<std::uint32_t>::max)() ||
            m_tags.capacity() <
                m_tags.size() +
                    m_header.m_slotsPerHead ||
            m_headAttributes.capacity() <
                m_headAttributes.size() +
                    m_header.m_attributeCount)
        {
            return false;
        }
        m_tags.insert(
            m_tags.end(),
            m_header.m_slotsPerHead,
            EmptyTag);
        m_headAttributes.insert(
            m_headAttributes.end(),
            m_header.m_attributeCount, 0);
        ++m_header.m_headCount;
        m_header.m_bodyFingerprint = 0;
        return true;
    }

    bool TombstoneRuntimeHead(SizeType p_head)
    {
        if (HasExpansion() || !IsActiveHead(p_head))
            return false;
        const size_t offset =
            static_cast<size_t>(p_head) *
            m_header.m_slotsPerHead;
        for (std::uint32_t slot = 0;
             slot < m_header.m_slotsPerHead;
             ++slot)
        {
            const std::uint32_t tag =
                m_tags[offset + slot];
            if (tag == EmptyTag) continue;
            auto found = m_headsByTag.find(tag);
            if (found == m_headsByTag.end())
                return false;
        }
        for (std::uint32_t slot = 0;
             slot < m_header.m_slotsPerHead;
             ++slot)
        {
            const std::uint32_t tag =
                m_tags[offset + slot];
            if (tag == EmptyTag) continue;
            auto& heads =
                m_headsByTag.find(tag)->second;
            heads.erase(
                std::remove(
                    heads.begin(), heads.end(),
                    p_head),
                heads.end());
        }
        if (!TombstoneHead(p_head))
            return false;
        m_header.m_bodyFingerprint = 0;
        return true;
    }

    bool PrepareRuntimeReplaceAndTombstone(
        SizeType p_survivor,
        const std::vector<std::uint32_t>& p_tags,
        const std::uint32_t* p_attributes,
        int p_attributeCount,
        SizeType p_loser)
    {
        if (HasExpansion() || p_survivor == p_loser ||
            !IsActiveHead(p_survivor) ||
            !IsActiveHead(p_loser) ||
            !ValidActiveRow(
                p_tags, p_attributes,
                p_attributeCount))
        {
            return false;
        }
        std::unordered_set<std::uint32_t> affected;
        try
        {
            const auto survivorTags =
                HeadTags(p_survivor);
            const auto loserTags =
                HeadTags(p_loser);
            affected.reserve(
                survivorTags.size() +
                loserTags.size() +
                p_tags.size());
            affected.insert(
                survivorTags.begin(),
                survivorTags.end());
            affected.insert(
                loserTags.begin(),
                loserTags.end());
            affected.insert(
                p_tags.begin(), p_tags.end());
            for (std::uint32_t tag : affected)
            {
                auto found = m_headsByTag.find(tag);
                if (found == m_headsByTag.end())
                    return false;
                int coverage =
                    static_cast<int>(
                        found->second.size());
                coverage -=
                    std::find(
                        found->second.begin(),
                        found->second.end(),
                        p_survivor) !=
                    found->second.end()
                        ? 1
                        : 0;
                coverage -=
                    std::find(
                        found->second.begin(),
                        found->second.end(),
                        p_loser) !=
                    found->second.end()
                        ? 1
                        : 0;
                if (std::find(
                        p_tags.begin(), p_tags.end(),
                        tag) != p_tags.end())
                {
                    ++coverage;
                }
                if (coverage <
                    static_cast<int>(
                        m_header.m_minHeadCount))
                {
                    return false;
                }
            }
            for (std::uint32_t tag : p_tags)
            {
                auto& heads =
                    m_headsByTag.find(tag)->second;
                if (std::find(
                        heads.begin(), heads.end(),
                        p_survivor) ==
                    heads.end())
                {
                    heads.reserve(heads.size() + 1);
                }
            }
        }
        catch (const std::bad_alloc&)
        {
            return false;
        }
        catch (const std::length_error&)
        {
            return false;
        }
        return true;
    }

    bool ReplaceAndTombstoneRuntime(
        SizeType p_survivor,
        const std::vector<std::uint32_t>& p_tags,
        const std::uint32_t* p_attributes,
        int p_attributeCount,
        SizeType p_loser)
    {
        if (!PrepareRuntimeReplaceAndTombstone(
                p_survivor, p_tags,
                p_attributes, p_attributeCount,
                p_loser))
        {
            return false;
        }
        return ApplyPreparedRuntimeReplaceAndTombstone(
            p_survivor, p_tags,
            p_attributes, p_attributeCount,
            p_loser);
    }

    bool ApplyPreparedRuntimeReplaceAndTombstone(
        SizeType p_survivor,
        const std::vector<std::uint32_t>& p_tags,
        const std::uint32_t* p_attributes,
        int p_attributeCount,
        SizeType p_loser)
    {
        if (HasExpansion() || p_survivor == p_loser ||
            !IsActiveHead(p_survivor) ||
            !IsActiveHead(p_loser) ||
            !ValidActiveRow(
                p_tags, p_attributes,
                p_attributeCount))
        {
            return false;
        }
        for (std::uint32_t tag : p_tags)
        {
            const auto found =
                m_headsByTag.find(tag);
            if (found == m_headsByTag.end())
                return false;
            const bool alreadyPresent =
                std::find(
                    found->second.begin(),
                    found->second.end(),
                    p_survivor) !=
                found->second.end();
            if (!alreadyPresent &&
                found->second.capacity() <
                    found->second.size() + 1)
            {
                return false;
            }
        }
        const size_t survivorOffset =
            static_cast<size_t>(p_survivor) *
            m_header.m_slotsPerHead;
        const size_t loserOffset =
            static_cast<size_t>(p_loser) *
            m_header.m_slotsPerHead;
        for (std::uint32_t slot = 0;
             slot < m_header.m_slotsPerHead;
             ++slot)
        {
            const std::uint32_t tag =
                m_tags[survivorOffset + slot];
            if (tag == EmptyTag) continue;
            const auto found =
                m_headsByTag.find(tag);
            if (found == m_headsByTag.end())
                return false;
        }
        for (std::uint32_t slot = 0;
             slot < m_header.m_slotsPerHead;
             ++slot)
        {
            const std::uint32_t tag =
                m_tags[loserOffset + slot];
            if (tag == EmptyTag) continue;
            const auto found =
                m_headsByTag.find(tag);
            if (found == m_headsByTag.end())
                return false;
        }
        for (std::uint32_t slot = 0;
             slot < m_header.m_slotsPerHead;
             ++slot)
        {
            const std::uint32_t tag =
                m_tags[survivorOffset + slot];
            if (tag == EmptyTag) continue;
            auto& heads =
                m_headsByTag.find(tag)->second;
            heads.erase(
                std::remove(
                    heads.begin(), heads.end(),
                    p_survivor),
                heads.end());
        }
        for (std::uint32_t slot = 0;
             slot < m_header.m_slotsPerHead;
             ++slot)
        {
            const std::uint32_t tag =
                m_tags[loserOffset + slot];
            if (tag == EmptyTag) continue;
            auto& heads =
                m_headsByTag.find(tag)->second;
            heads.erase(
                std::remove(
                    heads.begin(), heads.end(),
                    p_loser),
                heads.end());
        }
        if (!ReplaceHead(
                p_survivor, p_tags,
                p_attributes,
                p_attributeCount) ||
            !TombstoneHead(p_loser))
        {
            return false;
        }
        for (std::uint32_t tag : p_tags)
        {
            m_headsByTag.find(tag)
                ->second.push_back(p_survivor);
        }
        m_header.m_bodyFingerprint = 0;
        return true;
    }

    bool Supports(SizeType p_head, std::uint32_t p_tag) const
    {
        if (m_inlineExtraTag) return SupportsInlineRow(p_head, p_tag);
        if (p_head < 0 ||
            static_cast<std::uint32_t>(p_head) >=
                m_header.m_headCount ||
            (HasExpansion() && p_tag == EmptyTag))
        {
            return false;
        }
        const size_t offset = static_cast<size_t>(p_head) * m_header.m_slotsPerHead;
        for (std::uint32_t slot = 0;
             slot < m_header.m_slotsPerHead; ++slot)
        {
            if (m_tags[offset + slot] == p_tag) return true;
        }
        if (m_extraTags.empty() || !HasExpansion()) return false;
        const size_t head = static_cast<size_t>(p_head);
        if ((m_headsWithExtraTags[head >> 6] & (std::uint64_t{1} << (head & 63))) == 0)
            return false;
        return std::binary_search(
            m_extraTags.data() + static_cast<size_t>(m_extraTagOffsets[head]),
            m_extraTags.data() + static_cast<size_t>(m_extraTagOffsets[head + 1]), p_tag);
    }

    std::uint32_t TagAt(
        SizeType p_head, int p_slot) const
    {
        if (p_head < 0 || p_slot < 0 ||
            static_cast<std::uint32_t>(p_head) >=
                m_header.m_headCount ||
            static_cast<std::uint32_t>(p_slot) >=
                m_header.m_slotsPerHead)
        {
            return EmptyTag;
        }
        return HeadTagData(p_head)[p_slot];
    }

    std::uint32_t OwnTag(SizeType p_head) const
    {
        return TagAt(p_head, 0);
    }

    const std::uint32_t* HeadTagData(
        SizeType p_head) const
    {
        if (p_head < 0 ||
            static_cast<std::uint32_t>(p_head) >=
                m_header.m_headCount)
        {
            return nullptr;
        }
        return m_tags.data() + (m_inlineExtraTag
            ? static_cast<size_t>(p_head) * InlineTagStride() + 2
            : static_cast<size_t>(p_head) * m_header.m_slotsPerHead);
    }

    const std::uint32_t* HeadLookupData(SizeType p_head) const
    {
        const auto* tags = HeadTagData(p_head);
        return tags == nullptr || !m_inlineExtraTag ? tags : tags - 2;
    }

    size_t HeadLookupStride() const
    {
        return m_inlineExtraTag ? InlineTagStride() : m_header.m_slotsPerHead;
    }

    size_t HeadAttributeStride() const
    {
        return m_inlineExtraTag ? InlineTagStride() : m_header.m_attributeCount;
    }

    bool HasExpansion() const
    {
        return m_header.m_version == 4 || m_header.m_version == 5;
    }

    std::uint64_t ExtraSupportCount() const
    {
        return static_cast<std::uint64_t>(m_extraTags.size());
    }

    // Read-only provenance; zero for new requirement-derived V5 supports.
    std::uint64_t LegacyExtraSupportCap() const
    {
        return m_header.m_version == 4 ? m_maxExtraSupports : 0;
    }

    bool HasInlineExtraTags() const
    {
        return m_inlineExtraTag;
    }

    bool NeedsExtraLookupPrefetch() const
    {
        return !m_inlineExtraTag && !m_extraTags.empty();
    }

    // Inline row metadata is already covered by HeadLookupData().
    const std::uint64_t* ExtraHeadLookupData(SizeType p_head) const
    {
        if (m_inlineExtraTag || m_extraTags.empty() || !HasExpansion() || p_head < 0 ||
            static_cast<std::uint32_t>(p_head) >= m_header.m_headCount)
        {
            return nullptr;
        }
        const size_t head = static_cast<size_t>(p_head);
        if ((m_headsWithExtraTags[head >> 6] & (std::uint64_t{1} << (head & 63))) == 0)
            return nullptr;
        return m_extraTagOffsets.data() + head;
    }

    size_t ExtraTagCount(SizeType p_head) const
    {
        if (m_inlineExtraTag)
        {
            if (p_head < 0 || static_cast<std::uint32_t>(p_head) >= m_header.m_headCount)
                return 0;
            return static_cast<size_t>(ExtraOffsetAt(static_cast<size_t>(p_head) + 1) -
                ExtraOffsetAt(static_cast<size_t>(p_head)));
        }
        const auto* offsets = ExtraHeadLookupData(p_head);
        return offsets == nullptr ? 0 : static_cast<size_t>(offsets[1] - offsets[0]);
    }

    const std::uint32_t* ExtraTagData(SizeType p_head) const
    {
        return ExtraTagCount(p_head) == 0 ? nullptr
            : m_extraTags.data() + static_cast<size_t>(ExtraOffsetAt(p_head));
    }

    std::pair<const std::uint32_t*, const std::uint32_t*>
    ExtraLookupTagRange(SizeType p_head) const
    {
        const size_t count = ExtraTagCount(p_head);
        const size_t skip = m_inlineExtraTag ? 1 : 0;
        if (count <= skip) return {};
        const size_t begin = static_cast<size_t>(ExtraOffsetAt(p_head));
        return {m_extraTags.data() + begin + skip, m_extraTags.data() + begin + count};
    }

    std::uint32_t RequiredHeadCount(std::uint32_t p_tag) const
    {
        // Non-expanded tables use the global floor; unknown expanded tags have no requirement.
        if (!HasExpansion()) return m_header.m_minHeadCount;
        const auto found = std::lower_bound(
            m_tagVectorCounts.begin(), m_tagVectorCounts.end(), p_tag,
            [](const TagCountRecord& p_entry, std::uint32_t p_value) {
                return p_entry.m_tag < p_value;
            });
        return found != m_tagVectorCounts.end() && found->m_tag == p_tag
            ? found->m_reserved : 0;
    }

    const std::uint32_t* HeadAttributes(
        SizeType p_head) const
    {
        if (p_head < 0 ||
            static_cast<std::uint32_t>(p_head) >=
                m_header.m_headCount ||
            m_header.m_attributeCount == 0)
        {
            return nullptr;
        }
        return m_inlineExtraTag
            ? HeadTagData(p_head) + m_header.m_slotsPerHead
            : m_headAttributes.data() +
                static_cast<size_t>(p_head) * m_header.m_attributeCount;
    }

    std::vector<std::uint32_t> HeadTags(SizeType p_head) const
    {
        std::vector<std::uint32_t> tags;
        if (p_head < 0 ||
            static_cast<std::uint32_t>(p_head) >=
                m_header.m_headCount)
        {
            return tags;
        }

        const auto* base = HeadTagData(p_head);
        for (std::uint32_t slot = 0;
             slot < m_header.m_slotsPerHead; ++slot)
        {
            const std::uint32_t tag = base[slot];
            if (tag != EmptyTag) tags.push_back(tag);
        }
        const std::uint32_t* extra = ExtraTagData(p_head);
        if (extra != nullptr)
            tags.insert(tags.end(), extra, extra + ExtraTagCount(p_head));
        return tags;
    }

    bool Validate(std::string* p_error = nullptr) const
    {
        return ValidateStorage(
            m_header, m_tagVectorCounts, m_extraTagOffsets,
            m_extraTags, m_maxExtraSupports, p_error, m_inlineExtraTag);
    }

    bool Finalize(std::string* p_error = nullptr)
    {
        std::unordered_set<std::uint32_t> tags;
        ForEachBaseTagChunk([&](const std::uint32_t* values, size_t count) {
            for (size_t i = 0; i < count; ++i)
                if (values[i] != EmptyTag) tags.insert(values[i]);
        });
        tags.insert(m_extraTags.begin(), m_extraTags.end());
        m_header.m_tagCount =
            static_cast<std::uint32_t>(tags.size());
        if (m_tagVectorCounts.size() != tags.size() ||
            m_header.m_vectorCount == 0)
        {
            if (p_error != nullptr)
                *p_error =
                    "limited-tag vector counts are required before finalization";
            return false;
        }
        m_header.m_bodyFingerprint =
            CurrentBodyFingerprint();
        if (!Validate(p_error)) return false;
        RebuildHeadIndex();
        return true;
    }

    bool Save(const std::string& p_path, std::string* p_error = nullptr)
    {
        if (!Finalize(p_error)) return false;

        const std::string temporary = p_path + ".tmp";
        std::ofstream output(
            temporary, std::ios::binary | std::ios::trunc);
        if (!output)
        {
            if (p_error != nullptr)
                *p_error = "cannot open limited-tag support output";
            return false;
        }
        if (HasExpansion())
        {
            HeaderV4 expanded;
            expanded.m_base = m_header;
            expanded.m_extraTagCount = ExtraSupportCount();
            expanded.m_maxExtraSupports = m_maxExtraSupports;
            output.write(
                reinterpret_cast<const char*>(&expanded), sizeof(expanded));
        }
        else
        {
            output.write(
                reinterpret_cast<const char*>(&m_header), sizeof(m_header));
        }
        ForEachBaseTagChunk([&](const std::uint32_t* values, size_t count) {
            output.write(reinterpret_cast<const char*>(values),
                static_cast<std::streamsize>(count * sizeof(std::uint32_t)));
        });
        ForEachHeadAttributeChunk([&](const std::uint32_t* values, size_t count) {
            output.write(reinterpret_cast<const char*>(values),
                static_cast<std::streamsize>(count * sizeof(std::uint32_t)));
        });
        output.write(
            reinterpret_cast<const char*>(
                m_tagVectorCounts.data()),
            static_cast<std::streamsize>(
                m_tagVectorCounts.size() *
                sizeof(TagCountRecord)));
        if (HasExpansion())
        {
            ForEachExtraOffsetChunk([&](const std::uint64_t* offsets, size_t count) {
                output.write(reinterpret_cast<const char*>(offsets),
                    static_cast<std::streamsize>(count * sizeof(std::uint64_t)));
            });
            if (!m_extraTags.empty())
            {
                output.write(
                    reinterpret_cast<const char*>(m_extraTags.data()),
                    static_cast<std::streamsize>(
                        m_extraTags.size() * sizeof(std::uint32_t)));
            }
        }
        output.close();
        if (!output)
        {
            std::remove(temporary.c_str());
            if (p_error != nullptr)
                *p_error = "cannot write limited-tag support output";
            return false;
        }
        if (!Helper::AtomicReplaceFile(
                temporary, p_path))
        {
            std::remove(temporary.c_str());
            if (p_error != nullptr)
                *p_error = "cannot publish limited-tag support output";
            return false;
        }
        return true;
    }

    bool Load(
        const std::string& p_path,
        SizeType p_expectedHeadCount,
        int p_expectedSlotsPerHead,
        int p_expectedMinHeadCount,
        int p_expectedKeyColumn,
        int p_expectedAttributeCount,
        std::uint64_t p_expectedGeneration,
        std::string* p_error = nullptr)
    {
        Reset();
        std::ifstream input(p_path, std::ios::binary | std::ios::ate);
        if (!input)
        {
            if (p_error != nullptr)
                *p_error = "cannot open limited-tag support input";
            return false;
        }
        const std::streamoff fileBytes = input.tellg();
        input.seekg(0);
        std::uint32_t prefix[3] = {};
        input.read(
            reinterpret_cast<char*>(prefix),
            sizeof(prefix));
        input.seekg(0);
        std::streamoff bodyOffset = 0;
        bool legacyV1 = false;
        bool legacyV2 = false;
        std::uint64_t extraTagCount = 0;
        if (input && prefix[0] == 0x3153544cU &&
            prefix[1] == 1 &&
            prefix[2] == sizeof(HeaderV1))
        {
            HeaderV1 legacy;
            input.read(
                reinterpret_cast<char*>(&legacy),
                sizeof(legacy));
            if (!input || legacy.m_voteHeadCount == 0)
            {
                if (p_error != nullptr)
                    *p_error =
                        "invalid limited-tag v1 header or legacy provenance";
                Reset();
                return false;
            }
            m_header.m_headCount = legacy.m_headCount;
            m_header.m_slotsPerHead =
                legacy.m_slotsPerHead;
            m_header.m_legacyVoteHeadCount =
                legacy.m_voteHeadCount;
            m_header.m_minHeadCount =
                legacy.m_minHeadCount;
            m_header.m_tagCount = legacy.m_tagCount;
            m_header.m_keyColumn = 0;
            m_header.m_attributeCount = 1;
            m_header.m_generationFingerprint =
                legacy.m_generationFingerprint;
            m_header.m_bodyFingerprint =
                legacy.m_bodyFingerprint;
            bodyOffset = sizeof(HeaderV1);
            legacyV1 = true;
        }
        else if (input && prefix[0] == 0x3153544cU &&
                 prefix[1] == 2 &&
                 prefix[2] == sizeof(HeaderV2))
        {
            HeaderV2 legacy;
            input.read(
                reinterpret_cast<char*>(&legacy),
                sizeof(legacy));
            if (!input || legacy.m_voteHeadCount == 0)
            {
                if (p_error != nullptr)
                    *p_error =
                        "invalid limited-tag v2 header or legacy provenance";
                Reset();
                return false;
            }
            m_header.m_headCount = legacy.m_headCount;
            m_header.m_slotsPerHead =
                legacy.m_slotsPerHead;
            m_header.m_legacyVoteHeadCount =
                legacy.m_voteHeadCount;
            m_header.m_minHeadCount =
                legacy.m_minHeadCount;
            m_header.m_tagCount = legacy.m_tagCount;
            m_header.m_keyColumn = legacy.m_keyColumn;
            m_header.m_attributeCount =
                legacy.m_attributeCount;
            m_header.m_generationFingerprint =
                legacy.m_generationFingerprint;
            m_header.m_bodyFingerprint =
                legacy.m_bodyFingerprint;
            bodyOffset = sizeof(HeaderV2);
            legacyV2 = true;
        }
        else if (input && prefix[0] == 0x3153544cU &&
                 (prefix[1] == 4 || prefix[1] == 5) &&
                 prefix[2] == sizeof(HeaderV4))
        {
            HeaderV4 expanded;
            input.read(reinterpret_cast<char*>(&expanded), sizeof(expanded));
            m_header = expanded.m_base;
            extraTagCount = expanded.m_extraTagCount;
            m_maxExtraSupports = expanded.m_maxExtraSupports;
            bodyOffset = sizeof(HeaderV4);
        }
        else
        {
            input.read(
                reinterpret_cast<char*>(&m_header),
                sizeof(m_header));
            bodyOffset = sizeof(Header);
        }
        m_legacyWithoutTagVectorCounts =
            legacyV1 || legacyV2;
        if (!input ||
            m_header.m_magic != 0x3153544cU ||
            (!HasExpansion() && m_header.m_version != 3) ||
            m_header.m_headerBytes != (HasExpansion() ? sizeof(HeaderV4) : sizeof(Header)) ||
            p_expectedHeadCount <= 0 ||
            p_expectedMinHeadCount <= 0 ||
            p_expectedKeyColumn < 0 ||
            p_expectedAttributeCount <= p_expectedKeyColumn ||
            p_expectedGeneration == 0 ||
            m_header.m_headCount !=
                static_cast<std::uint32_t>(p_expectedHeadCount) ||
            !IsSupportedSlotCount(
                p_expectedSlotsPerHead) ||
            m_header.m_slotsPerHead !=
                static_cast<std::uint32_t>(p_expectedSlotsPerHead) ||
            m_header.m_minHeadCount !=
                static_cast<std::uint32_t>(p_expectedMinHeadCount) ||
            m_header.m_keyColumn !=
                static_cast<std::uint32_t>(
                    p_expectedKeyColumn) ||
            m_header.m_attributeCount !=
                static_cast<std::uint32_t>(
                    p_expectedAttributeCount) ||
            m_header.m_generationFingerprint !=
                p_expectedGeneration)
        {
            if (p_error != nullptr)
                *p_error =
                    "limited-tag support configuration mismatch";
            Reset();
            return false;
        }
        const std::uint64_t tagCount =
            static_cast<std::uint64_t>(m_header.m_headCount) *
            m_header.m_slotsPerHead;
        const std::uint64_t attributeCount =
            static_cast<std::uint64_t>(m_header.m_headCount) *
            m_header.m_attributeCount;
        const std::uint64_t offsetCount = HasExpansion()
            ? static_cast<std::uint64_t>(m_header.m_headCount) + 1 : 0;
        std::uint64_t expectedBytes = static_cast<std::uint64_t>(bodyOffset);
        auto addBytes = [&](std::uint64_t p_count, size_t p_width) {
            const auto limit = static_cast<std::uint64_t>(
                (std::numeric_limits<std::streamoff>::max)());
            if (p_count > (std::numeric_limits<size_t>::max)() / p_width ||
                p_count > static_cast<std::uint64_t>(
                    (std::numeric_limits<std::streamsize>::max)()) / p_width ||
                expectedBytes > limit || p_count > (limit - expectedBytes) / p_width)
            {
                return false;
            }
            expectedBytes += p_count * p_width;
            return true;
        };
        if (fileBytes < 0 || m_header.m_tagCount == 0 ||
            tagCount > m_tags.max_size() ||
            attributeCount > m_headAttributes.max_size() ||
            m_header.m_tagCount > m_tagVectorCounts.max_size() ||
            offsetCount > m_extraTagOffsets.max_size() ||
            extraTagCount > m_extraTags.max_size() ||
            (m_header.m_tagCount > tagCount &&
             m_header.m_tagCount - tagCount > extraTagCount) ||
            (m_header.m_version == 4 &&
             (m_maxExtraSupports == 0 || extraTagCount > m_maxExtraSupports)) ||
            (m_header.m_version == 5 &&
             (extraTagCount != m_maxExtraSupports ||
              extraTagCount > static_cast<std::uint64_t>(m_header.m_tagCount) *
                  (std::min)(m_header.m_minHeadCount, m_header.m_headCount))) ||
            !addBytes(tagCount, sizeof(std::uint32_t)) ||
            !addBytes(legacyV1 ? 0 : attributeCount, sizeof(std::uint32_t)) ||
            !addBytes(legacyV1 || legacyV2 ? 0 : m_header.m_tagCount, sizeof(TagCountRecord)) ||
            !addBytes(offsetCount, sizeof(std::uint64_t)) ||
            !addBytes(extraTagCount, sizeof(std::uint32_t)) ||
            expectedBytes != static_cast<std::uint64_t>(fileBytes))
        {
            if (p_error != nullptr)
                *p_error = "limited-tag support file size mismatch";
            Reset();
            return false;
        }
        try
        {
            if (CanInlineExtraTags(extraTagCount))
                m_tags.reserve(InlineTagElementCount());
            m_tags.resize(static_cast<size_t>(tagCount));
            input.read(
                reinterpret_cast<char*>(m_tags.data()),
                static_cast<std::streamsize>(tagCount * sizeof(std::uint32_t)));
            if (legacyV1)
            {
                m_headAttributes.resize(static_cast<size_t>(m_header.m_headCount));
                for (std::uint32_t head = 0; head < m_header.m_headCount; ++head)
                {
                    m_headAttributes[static_cast<size_t>(head)] =
                        m_tags[static_cast<size_t>(head) * m_header.m_slotsPerHead];
                }
            }
            else
            {
                m_headAttributes.resize(static_cast<size_t>(attributeCount));
                input.read(
                    reinterpret_cast<char*>(m_headAttributes.data()),
                    static_cast<std::streamsize>(attributeCount * sizeof(std::uint32_t)));
            }
            if (!legacyV1 && !legacyV2)
            {
                m_tagVectorCounts.resize(m_header.m_tagCount);
                input.read(
                    reinterpret_cast<char*>(m_tagVectorCounts.data()),
                    static_cast<std::streamsize>(
                        m_tagVectorCounts.size() * sizeof(TagCountRecord)));
            }
            if (HasExpansion())
            {
                m_extraTagOffsets.resize(static_cast<size_t>(offsetCount));
                input.read(
                    reinterpret_cast<char*>(m_extraTagOffsets.data()),
                    static_cast<std::streamsize>(offsetCount * sizeof(std::uint64_t)));
                bool validOffsets = input && m_extraTagOffsets.front() == 0 &&
                    m_extraTagOffsets.back() == extraTagCount;
                for (size_t i = 1; validOffsets && i < m_extraTagOffsets.size(); ++i)
                    validOffsets = m_extraTagOffsets[i - 1] <= m_extraTagOffsets[i] &&
                        m_extraTagOffsets[i] <= extraTagCount;
                std::uint64_t requirementBound = 0;
                if (!validOffsets ||
                    !ExpansionRequirementBound(m_header, m_tagVectorCounts, false,
                                               requirementBound, p_error) ||
                    (m_header.m_version == 5 && requirementBound != extraTagCount))
                {
                    if (p_error != nullptr) *p_error = "invalid limited-tag expansion offsets or requirements";
                    Reset();
                    return false;
                }
                m_extraTags.resize(static_cast<size_t>(extraTagCount));
                if (extraTagCount != 0)
                {
                    input.read(
                        reinterpret_cast<char*>(m_extraTags.data()),
                        static_cast<std::streamsize>(extraTagCount * sizeof(std::uint32_t)));
                }
            }
            const std::uint64_t bodyFingerprint =
                legacyV1 ? BodyFingerprintV1(m_tags) :
                legacyV2 ? BodyFingerprintV2(m_tags, m_headAttributes) :
                CurrentBodyFingerprint();
            if (!input || bodyFingerprint != m_header.m_bodyFingerprint)
            {
                if (p_error != nullptr && p_error->empty())
                    *p_error = "limited-tag support body fingerprint mismatch";
                Reset();
                return false;
            }
            if (!Validate(p_error))
            {
                Reset();
                return false;
            }
            m_headsWithExtraTags = CanInlineExtraTags(m_extraTags.size())
                ? std::vector<std::uint64_t>() : ExtraHeadPresence(m_extraTagOffsets);
            InlineFirstExtraTags();
            RebuildHeadIndex();
            return true;
        }
        catch (const std::bad_alloc&)
        {
            if (p_error != nullptr)
                *p_error = "cannot allocate limited-tag support input";
        }
        catch (const std::length_error&)
        {
            if (p_error != nullptr)
                *p_error = "limited-tag support input is too large";
        }
        Reset();
        return false;
    }

    SizeType HeadCount() const
    {
        return static_cast<SizeType>(m_header.m_headCount);
    }

    int SlotsPerHead() const
    {
        return static_cast<int>(m_header.m_slotsPerHead);
    }

    bool UsesRetainedOriginalCandidates() const
    {
        return m_header.m_legacyVoteHeadCount == 0;
    }

    int MinHeadCount() const
    {
        return static_cast<int>(
            m_header.m_minHeadCount);
    }

    int KeyColumn() const
    {
        return static_cast<int>(m_header.m_keyColumn);
    }

    int AttributeCount() const
    {
        return static_cast<int>(
            m_header.m_attributeCount);
    }

    std::uint64_t GenerationFingerprint() const
    {
        return m_header.m_generationFingerprint;
    }

    std::uint64_t ContentFingerprint() const
    {
        return m_header.m_bodyFingerprint;
    }

    bool HasTagVectorCounts() const
    {
        return
            !m_legacyWithoutTagVectorCounts &&
            m_header.m_vectorCount > 0 &&
            m_tagVectorCounts.size() ==
                m_header.m_tagCount;
    }

    std::uint64_t VectorCount() const
    {
        return m_header.m_vectorCount;
    }

    std::uint64_t TagVectorCount(
        std::uint32_t p_tag) const
    {
        const auto found = std::lower_bound(
            m_tagVectorCounts.begin(),
            m_tagVectorCounts.end(), p_tag,
            [](const TagCountRecord& p_entry,
               std::uint32_t p_value) {
                return p_entry.m_tag < p_value;
            });
        return
            found != m_tagVectorCounts.end() &&
                    found->m_tag == p_tag
                ? found->m_count
                : 0;
    }

    int CoverageCount(std::uint32_t p_tag) const
    {
        const auto found = m_headsByTag.find(p_tag);
        return found == m_headsByTag.end()
            ? 0
            : static_cast<int>(found->second.size());
    }

    bool IsActiveHead(SizeType p_head) const
    {
        return OwnTag(p_head) != EmptyTag;
    }

    bool TagSelectivityInRange(
        std::uint32_t p_tag,
        double p_minExclusive,
        double p_maxInclusive) const
    {
        const std::uint64_t count =
            TagVectorCount(p_tag);
        if (count == 0 || m_header.m_vectorCount == 0)
            return false;
        const double selectivity =
            static_cast<double>(count) /
            static_cast<double>(
                m_header.m_vectorCount);
        return
            selectivity > p_minExclusive &&
            selectivity <= p_maxInclusive;
    }

    const std::unordered_map<
        std::uint32_t,
        std::vector<SizeType>>& TagHeads() const
    {
        return m_headsByTag;
    }

private:
    // Keep layout-specific register pressure out of the sparse lookup hot path.
#if defined(_MSC_VER)
    __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
    __attribute__((noinline))
#endif
    bool SupportsInlineRow(SizeType p_head, std::uint32_t p_tag) const
    {
        if (p_head < 0 || static_cast<std::uint32_t>(p_head) >= m_header.m_headCount ||
            p_tag == EmptyTag)
            return false;
        const auto* tags = m_tags.data() + static_cast<size_t>(p_head) * InlineTagStride() + 2;
        for (std::uint32_t slot = 0; slot < m_header.m_slotsPerHead; ++slot)
            if (tags[slot] == p_tag) return true;
        const std::uint32_t firstTag = tags[-1];
        if (p_tag <= firstTag) return p_tag == firstTag;
        return std::binary_search(
            m_extraTags.data() + static_cast<size_t>(tags[-2]) + 1,
            m_extraTags.data() + static_cast<size_t>(
                ExtraOffsetAt(static_cast<size_t>(p_head) + 1)), p_tag);
    }

    bool ValidateStorage(
        const Header& p_header,
        const std::vector<TagCountRecord>& p_counts,
        const std::vector<std::uint64_t>& p_offsets,
        const std::vector<std::uint32_t>& p_extraTags,
        std::uint64_t p_maxExtraSupports,
        std::string* p_error,
        bool p_inlineExtraTag = false) const
    {
        auto fail = [&](const char* p_message) {
            if (p_error != nullptr) *p_error = p_message;
            return false;
        };
        const bool expanded = p_header.m_version == 4 || p_header.m_version == 5;
        const size_t inlineStride = static_cast<size_t>(p_header.m_slotsPerHead) +
            p_header.m_attributeCount + 2;
        const auto offsetAt = [&](size_t index) {
            return p_inlineExtraTag
                ? static_cast<std::uint64_t>(m_tags[index * inlineStride]) : p_offsets[index];
        };
        if (p_header.m_magic != 0x3153544cU ||
            (!expanded && p_header.m_version != 3) ||
            p_header.m_headerBytes != (expanded ? sizeof(HeaderV4) : sizeof(Header)) ||
            p_header.m_headCount == 0 ||
            p_header.m_slotsPerHead == 0 ||
            p_header.m_slotsPerHead >
                static_cast<std::uint32_t>((std::numeric_limits<int>::max)()) ||
            p_header.m_minHeadCount == 0 ||
            p_header.m_tagCount == 0 ||
            p_header.m_attributeCount == 0 ||
            p_header.m_keyColumn >= p_header.m_attributeCount ||
            p_header.m_generationFingerprint == 0)
        {
            return fail("invalid limited-tag support header");
        }
        if ((expanded || !m_legacyWithoutTagVectorCounts) &&
            (p_header.m_vectorCount == 0 || p_counts.size() != p_header.m_tagCount))
        {
            return fail("limited-tag vector-count metadata is incomplete");
        }
        const std::uint64_t baseTagCount =
            static_cast<std::uint64_t>(p_header.m_headCount) * p_header.m_slotsPerHead;
        const std::uint64_t tagElements = p_inlineExtraTag
            ? static_cast<std::uint64_t>(p_header.m_headCount) * inlineStride + 2
            : baseTagCount;
        if (m_tags.size() != tagElements)
            return fail("limited-tag support body size mismatch");
        if (m_headAttributes.size() != (p_inlineExtraTag ? 0 :
            static_cast<std::uint64_t>(p_header.m_headCount) * p_header.m_attributeCount))
        {
            return fail("limited-tag head attribute size mismatch");
        }
        if (p_header.m_tagCount > baseTagCount &&
            p_header.m_tagCount - baseTagCount > p_extraTags.size())
        {
            return fail("limited-tag support tag count exceeds body capacity");
        }
        if (expanded)
        {
            if ((p_header.m_version == 4 &&
                 (p_maxExtraSupports == 0 || p_extraTags.size() > p_maxExtraSupports)) ||
                (p_header.m_version == 5 && p_extraTags.size() != p_maxExtraSupports) ||
                (p_inlineExtraTag ? !p_offsets.empty()
                    : p_offsets.size() != static_cast<std::uint64_t>(p_header.m_headCount) + 1) ||
                offsetAt(0) != 0 || offsetAt(p_header.m_headCount) != p_extraTags.size())
            {
                return fail("invalid limited-tag expansion shape or provenance");
            }
            for (size_t i = 1; i <= p_header.m_headCount; ++i)
            {
                if (offsetAt(i) < offsetAt(i - 1) || offsetAt(i) > p_extraTags.size())
                    return fail("invalid limited-tag expansion offsets");
            }
            if (p_inlineExtraTag &&
                (p_extraTags.size() > (std::numeric_limits<std::uint32_t>::max)() ||
                 m_tags.back() != EmptyTag))
                return fail("invalid inline limited-tag expansion metadata");
        }
        else if (!p_offsets.empty() || !p_extraTags.empty() || p_maxExtraSupports != 0)
        {
            return fail("legacy limited-tag support cannot contain expansion metadata");
        }

        std::uint64_t requirementBound = 0;
        if (p_header.m_version == 5 &&
            (!ExpansionRequirementBound(p_header, p_counts, p_inlineExtraTag,
                                        requirementBound, p_error) ||
             requirementBound != p_maxExtraSupports))
            return fail("limited-tag expansion requirement bound mismatch");
        std::unordered_map<std::uint32_t, std::uint32_t> coverage;
        std::unordered_map<std::uint32_t, std::uint32_t> extraCoverage;
        for (std::uint32_t head = 0; head < p_header.m_headCount; ++head)
        {
            std::unordered_set<std::uint32_t> unique;
            bool active = false;
            const size_t offset = p_inlineExtraTag ? static_cast<size_t>(head) * inlineStride + 2
                : static_cast<size_t>(head) * p_header.m_slotsPerHead;
            for (std::uint32_t slot = 0; slot < p_header.m_slotsPerHead; ++slot)
            {
                const std::uint32_t tag = m_tags[offset + slot];
                if (tag == EmptyTag) continue;
                active = true;
                if (!unique.insert(tag).second)
                    return fail("duplicate tag in limited-tag head support");
                ++coverage[tag];
            }
            const std::uint32_t ownTag = m_tags[offset];
            if (expanded)
            {
                const size_t begin = static_cast<size_t>(offsetAt(head));
                const size_t end = static_cast<size_t>(offsetAt(static_cast<size_t>(head) + 1));
                if (p_inlineExtraTag &&
                    m_tags[static_cast<size_t>(head) * inlineStride + 1] !=
                        (begin == end ? EmptyTag : p_extraTags[begin]))
                    return fail("invalid inline limited-tag expansion tag");
                if (ownTag == EmptyTag && begin != end)
                    return fail("inactive limited-tag heads cannot contain extra supports");
                for (size_t i = begin; i < end; ++i)
                {
                    const std::uint32_t tag = p_extraTags[i];
                    if (tag == EmptyTag || (i > begin && tag <= p_extraTags[i - 1]) ||
                        unique.find(tag) != unique.end())
                    {
                        return fail("invalid, duplicate or overlapping limited-tag extra support");
                    }
                    ++coverage[tag];
                    if (p_header.m_version == 5) ++extraCoverage[tag];
                }
            }
            if (!active) continue;
            const std::uint32_t* attributes = p_inlineExtraTag
                ? m_tags.data() + offset + p_header.m_slotsPerHead
                : m_headAttributes.data() + static_cast<size_t>(head) * p_header.m_attributeCount;
            if (ownTag == EmptyTag || attributes[p_header.m_keyColumn] != ownTag)
            {
                return fail("limited-tag own tag does not match the key-column attribute");
            }
        }
        if (coverage.size() != p_header.m_tagCount)
            return fail("limited-tag support tag count mismatch");
        if (!expanded)
        {
            for (const auto& entry : coverage)
            {
                if (entry.second < p_header.m_minHeadCount)
                    return fail("limited-tag support coverage below minimum");
            }
        }
        if (expanded || !m_legacyWithoutTagVectorCounts)
        {
            std::uint64_t total = 0;
            std::uint32_t previous = 0;
            for (size_t i = 0; i < p_counts.size(); ++i)
            {
                const TagCountRecord& entry = p_counts[i];
                const auto found = coverage.find(entry.m_tag);
                const bool invalidRequirement = expanded
                    ? entry.m_reserved == 0 || entry.m_reserved > p_header.m_minHeadCount ||
                        entry.m_reserved > p_header.m_headCount
                    : entry.m_reserved != 0;
                if (entry.m_tag == EmptyTag || invalidRequirement ||
                    entry.m_count == 0 || entry.m_count > p_header.m_vectorCount ||
                    (i > 0 && entry.m_tag <= previous) || found == coverage.end() ||
                    total > (std::numeric_limits<std::uint64_t>::max)() - entry.m_count)
                {
                    return fail("invalid limited-tag vector-count metadata");
                }
                if (expanded && found->second < entry.m_reserved)
                    return fail("limited-tag support coverage below effective minimum");
                if (p_header.m_version == 5 && extraCoverage[entry.m_tag] != 0 &&
                    found->second != entry.m_reserved)
                    return fail("limited-tag extra supports exceed per-tag requirement");
                previous = entry.m_tag;
                total += entry.m_count;
            }
            if (total != p_header.m_vectorCount)
                return fail("limited-tag vector counts do not cover the dataset");
        }
        return true;
    }

    bool ExpansionRequirementBound(
        const Header& p_header, const std::vector<TagCountRecord>& p_counts,
        bool p_inline, std::uint64_t& p_bound, std::string* p_error) const
    {
        auto fail = [&]() {
            if (p_error != nullptr) *p_error = "invalid limited-tag expansion requirements";
            return false;
        };
        p_bound = 0;
        if (p_counts.empty() || p_counts.size() != p_header.m_tagCount ||
            p_header.m_vectorCount == 0) return fail();
        std::unordered_map<std::uint32_t, std::uint32_t> baseCoverage;
        const size_t stride = p_inline
            ? static_cast<size_t>(p_header.m_slotsPerHead) + p_header.m_attributeCount + 2
            : p_header.m_slotsPerHead;
        for (size_t head = 0; head < p_header.m_headCount; ++head)
            for (size_t slot = 0; slot < p_header.m_slotsPerHead; ++slot)
            {
                const auto tag = m_tags[head * stride + (p_inline ? 2 : 0) + slot];
                if (tag != EmptyTag) ++baseCoverage[tag];
            }
        std::uint64_t total = 0;
        for (size_t i = 0; i < p_counts.size(); ++i)
        {
            const auto& entry = p_counts[i];
            if (entry.m_tag == EmptyTag || (i && entry.m_tag <= p_counts[i - 1].m_tag) ||
                entry.m_reserved == 0 || entry.m_reserved > p_header.m_minHeadCount ||
                entry.m_reserved > p_header.m_headCount || entry.m_count == 0 ||
                total > p_header.m_vectorCount || entry.m_count > p_header.m_vectorCount - total)
                return fail();
            total += entry.m_count;
            const auto found = baseCoverage.find(entry.m_tag);
            const auto base = found == baseCoverage.end() ? 0U : found->second;
            const std::uint64_t deficit = entry.m_reserved > base ? entry.m_reserved - base : 0;
            if (p_bound > (std::numeric_limits<std::uint64_t>::max)() - deficit) return fail();
            p_bound += deficit;
        }
        return total == p_header.m_vectorCount || fail();
    }

    bool ValidActiveRow(
        const std::vector<std::uint32_t>& p_tags,
        const std::uint32_t* p_attributes,
        int p_attributeCount) const
    {
        if (p_tags.empty() ||
            p_tags.size() >
                m_header.m_slotsPerHead ||
            p_attributes == nullptr ||
            p_attributeCount !=
                static_cast<int>(
                    m_header.m_attributeCount) ||
            p_tags.front() !=
                p_attributes[
                    m_header.m_keyColumn])
        {
            return false;
        }
        for (size_t index = 0;
             index < p_tags.size(); ++index)
        {
            const std::uint32_t tag =
                p_tags[index];
            if (tag == EmptyTag ||
                TagVectorCount(tag) == 0)
            {
                return false;
            }
            for (size_t prior = 0;
                 prior < index; ++prior)
            {
                if (p_tags[prior] == tag)
                {
                    return false;
                }
            }
        }
        return true;
    }

    void RebuildHeadIndex()
    {
        m_headsByTag.clear();
        m_headsByTag.reserve(
            static_cast<size_t>(
                m_header.m_tagCount) *
                2 + 1);
        for (std::uint32_t head = 0;
             head < m_header.m_headCount; ++head)
        {
            const auto* base = HeadTagData(static_cast<SizeType>(head));
            for (std::uint32_t slot = 0;
                 slot < m_header.m_slotsPerHead;
                 ++slot)
            {
                const std::uint32_t tag = base[slot];
                if (tag != EmptyTag)
                {
                    m_headsByTag[tag].push_back(
                        static_cast<SizeType>(
                            head));
                }
            }
            const SizeType headID = static_cast<SizeType>(head);
            const std::uint32_t* extra = ExtraTagData(headID);
            for (size_t i = 0; i < ExtraTagCount(headID); ++i)
                m_headsByTag[extra[i]].push_back(headID);
        }
    }

    static void FingerprintAppend(
        std::uint64_t& p_hash,
        const void* p_data,
        size_t p_byteCount)
    {
        const auto* bytes =
            reinterpret_cast<const std::uint8_t*>(
                p_data);
        for (size_t i = 0; i < p_byteCount; ++i)
        {
            p_hash ^= bytes[i];
            p_hash *= 1099511628211ULL;
        }
    }

    static void FingerprintAppend(
        std::uint64_t& p_hash,
        const std::vector<std::uint32_t>& p_values)
    {
        FingerprintAppend(
            p_hash, p_values.data(),
            p_values.size() * sizeof(std::uint32_t));
    }

    static std::uint64_t BodyFingerprintV1(
        const std::vector<std::uint32_t>& p_tags)
    {
        std::uint64_t hash = 1469598103934665603ULL;
        FingerprintAppend(hash, p_tags);
        return hash;
    }

    static std::uint64_t BodyFingerprintV2(
        const std::vector<std::uint32_t>& p_tags,
        const std::vector<std::uint32_t>& p_attributes)
    {
        std::uint64_t hash = 1469598103934665603ULL;
        FingerprintAppend(hash, p_tags);
        FingerprintAppend(hash, p_attributes);
        return hash;
    }

    static std::uint64_t BodyFingerprint(
        const std::vector<std::uint32_t>& p_tags,
        const std::vector<std::uint32_t>& p_attributes,
        std::uint64_t p_vectorCount,
        const std::vector<TagCountRecord>& p_counts)
    {
        std::uint64_t hash =
            BodyFingerprintV2(p_tags, p_attributes);
        FingerprintAppend(
            hash, &p_vectorCount,
            sizeof(p_vectorCount));
        FingerprintAppend(
            hash, p_counts.data(),
            p_counts.size() *
                sizeof(TagCountRecord));
        return hash;
    }

    std::uint64_t CurrentBodyFingerprint() const
    {
        std::uint64_t hash = 1469598103934665603ULL;
        if (m_header.m_version == 5)
        {
            const std::uint64_t semantics = 0x5245515549524544ULL; // REQUIRED
            FingerprintAppend(hash, &semantics, sizeof(semantics));
        }
        if (UsesRetainedOriginalCandidates())
        {
            const std::uint64_t source = 0x52455441494e4544ULL; // RETAINED
            FingerprintAppend(hash, &source, sizeof(source));
        }
        ForEachBaseTagChunk([&](const std::uint32_t* tags, size_t count) {
            FingerprintAppend(hash, tags, count * sizeof(std::uint32_t));
        });
        ForEachHeadAttributeChunk([&](const std::uint32_t* attributes, size_t count) {
            FingerprintAppend(hash, attributes, count * sizeof(std::uint32_t));
        });
        FingerprintAppend(hash, &m_header.m_vectorCount, sizeof(m_header.m_vectorCount));
        FingerprintAppend(hash, m_tagVectorCounts.data(),
            m_tagVectorCounts.size() * sizeof(TagCountRecord));
        if (HasExpansion())
        {
            const std::uint64_t extraTagCount = ExtraSupportCount();
            FingerprintAppend(hash, &extraTagCount, sizeof(extraTagCount));
            FingerprintAppend(hash, &m_maxExtraSupports, sizeof(m_maxExtraSupports));
            ForEachExtraOffsetChunk([&](const std::uint64_t* offsets, size_t count) {
                FingerprintAppend(hash, offsets, count * sizeof(std::uint64_t));
            });
            FingerprintAppend(hash, m_extraTags);
        }
        return hash;
    }

    size_t InlineTagStride() const
    {
        return static_cast<size_t>(m_header.m_slotsPerHead) + m_header.m_attributeCount + 2;
    }

    bool CanInlineExtraTags(std::uint64_t p_extraCount) const
    {
        const std::uint64_t stride = static_cast<std::uint64_t>(m_header.m_slotsPerHead) +
            m_header.m_attributeCount + 2;
        return p_extraCount != 0 &&
            p_extraCount <= (std::numeric_limits<std::uint32_t>::max)() &&
            m_tags.max_size() >= 2 &&
            m_header.m_headCount <= (static_cast<std::uint64_t>(m_tags.max_size()) - 2) / stride;
    }

    size_t InlineTagElementCount() const
    {
        return static_cast<size_t>(m_header.m_headCount) * InlineTagStride() + 2;
    }

    std::uint64_t ExtraOffsetAt(size_t p_head) const
    {
        return m_inlineExtraTag ? m_tags[p_head * InlineTagStride()] : m_extraTagOffsets[p_head];
    }

    void InlineFirstExtraTags()
    {
        if (m_inlineExtraTag || !HasExpansion() || !CanInlineExtraTags(m_extraTags.size()))
            return;
        // Co-locate all admission fields without another retained allocation:
        // [uint32 offset, first extra tag, base tags..., own attributes...].
        const size_t stride = InlineTagStride();
        const size_t slots = m_header.m_slotsPerHead;
        m_tags.resize(InlineTagElementCount());
        for (size_t head = m_header.m_headCount; head-- > 0;)
        {
            const std::uint64_t begin = m_extraTagOffsets[head];
            const std::uint64_t end = m_extraTagOffsets[head + 1];
            const std::uint32_t first = begin == end ? EmptyTag
                : m_extraTags[static_cast<size_t>(begin)];
            std::copy_backward(m_tags.begin() + head * slots,
                m_tags.begin() + (head + 1) * slots,
                m_tags.begin() + head * stride + 2 + slots);
            std::copy_n(m_headAttributes.data() + head * m_header.m_attributeCount,
                m_header.m_attributeCount, m_tags.data() + head * stride + 2 + slots);
            m_tags[head * stride] = static_cast<std::uint32_t>(begin);
            m_tags[head * stride + 1] = first;
        }
        m_tags[static_cast<size_t>(m_header.m_headCount) * stride] =
            static_cast<std::uint32_t>(m_extraTagOffsets.back());
        m_tags.back() = EmptyTag;
        m_inlineExtraTag = true;
        std::vector<std::uint64_t>().swap(m_extraTagOffsets);
        std::vector<std::uint32_t>().swap(m_headAttributes);
        std::vector<std::uint64_t>().swap(m_headsWithExtraTags);
    }

    template <typename Consumer>
    void ForEachBaseTagChunk(Consumer&& p_consumer) const
    {
        if (!m_inlineExtraTag)
        {
            p_consumer(m_tags.data(), m_tags.size());
            return;
        }
        ForEachInlineHeadField(2, m_header.m_slotsPerHead, p_consumer);
    }

    template <typename Consumer>
    void ForEachHeadAttributeChunk(Consumer&& p_consumer) const
    {
        if (!m_inlineExtraTag)
        {
            p_consumer(m_headAttributes.data(), m_headAttributes.size());
            return;
        }
        ForEachInlineHeadField(static_cast<size_t>(m_header.m_slotsPerHead) + 2,
            m_header.m_attributeCount, p_consumer);
    }

    template <typename Consumer>
    void ForEachInlineHeadField(size_t p_start, size_t p_width, Consumer&& p_consumer) const
    {
        std::array<std::uint32_t, 1024> tags;
        size_t filled = 0;
        for (std::uint32_t head = 0; head < m_header.m_headCount; ++head)
        {
            const auto* source = m_tags.data() + static_cast<size_t>(head) * InlineTagStride() + p_start;
            size_t remaining = p_width;
            while (remaining != 0)
            {
                const size_t count = (std::min)(tags.size() - filled, remaining);
                std::copy_n(source, count, tags.data() + filled);
                source += count;
                filled += count;
                remaining -= count;
                if (filled == tags.size())
                {
                    p_consumer(tags.data(), filled);
                    filled = 0;
                }
            }
        }
        if (filled != 0) p_consumer(tags.data(), filled);
    }

    template <typename Consumer>
    void ForEachExtraOffsetChunk(Consumer&& p_consumer) const
    {
        if (!m_inlineExtraTag)
        {
            p_consumer(m_extraTagOffsets.data(), m_extraTagOffsets.size());
            return;
        }
        std::array<std::uint64_t, 1024> offsets;
        const size_t offsetCount = static_cast<size_t>(m_header.m_headCount) + 1;
        for (size_t begin = 0; begin < offsetCount; begin += offsets.size())
        {
            const size_t count = (std::min)(offsets.size(), offsetCount - begin);
            for (size_t i = 0; i < count; ++i)
                offsets[i] = ExtraOffsetAt(begin + i);
            p_consumer(offsets.data(), count);
        }
    }

    static std::vector<std::uint64_t> ExtraHeadPresence(
        const std::vector<std::uint64_t>& p_offsets)
    {
        if (p_offsets.empty() || p_offsets.back() == 0) return {};
        const size_t heads = p_offsets.size() - 1;
        std::vector<std::uint64_t> presence((heads + 63) / 64, 0);
        for (size_t head = 0; head < heads; ++head)
            if (p_offsets[head] != p_offsets[head + 1])
                presence[head >> 6] |= std::uint64_t{1} << (head & 63);
        return presence;
    }

    Header m_header;
    std::vector<std::uint32_t> m_tags;
    std::vector<std::uint32_t> m_headAttributes;
    std::vector<TagCountRecord> m_tagVectorCounts;
    std::vector<std::uint64_t> m_extraTagOffsets;
    std::vector<std::uint32_t> m_extraTags;
    std::vector<std::uint64_t> m_headsWithExtraTags;
    std::uint64_t m_maxExtraSupports = 0;
    std::unordered_map<
        std::uint32_t,
        std::vector<SizeType>> m_headsByTag;
    bool m_legacyWithoutTagVectorCounts = false;
    bool m_inlineExtraTag = false;
};

} // namespace SPANN
} // namespace SPTAG

#endif // _SPTAG_SPANN_LIMITEDTAGSUPPORT_H_
