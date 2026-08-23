// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_SPANN_LIMITEDTAGSUPPORT_H_
#define _SPTAG_SPANN_LIMITEDTAGSUPPORT_H_

#include "inc/Core/Common.h"
#include "inc/Helper/AtomicFile.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <limits>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace SPTAG
{
namespace SPANN
{

class LimitedTagSupport
{
public:
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
        std::uint32_t m_reserved = 0;
        std::uint64_t m_count = 0;
    };

    struct Header
    {
        std::uint32_t m_magic = 0x3153544cU; // LTS1
        std::uint32_t m_version = 3;
        std::uint32_t m_headerBytes = 64;
        std::uint32_t m_headCount = 0;
        std::uint32_t m_slotsPerHead = 0;
        std::uint32_t m_voteHeadCount = 0;
        std::uint32_t m_minHeadCount = 0;
        std::uint32_t m_tagCount = 0;
        std::uint32_t m_keyColumn = 0;
        std::uint32_t m_attributeCount = 0;
        std::uint64_t m_vectorCount = 0;
        std::uint64_t m_generationFingerprint = 0;
        std::uint64_t m_bodyFingerprint = 0;
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

    void Reset()
    {
        m_header = Header();
        m_tags.clear();
        m_headAttributes.clear();
        m_tagVectorCounts.clear();
        m_headsByTag.clear();
        m_legacyWithoutTagVectorCounts = false;
    }

    bool Initialize(
        SizeType p_headCount,
        int p_slotsPerHead,
        int p_voteHeadCount,
        int p_minHeadCount,
        int p_keyColumn,
        int p_attributeCount,
        std::uint64_t p_generationFingerprint)
    {
        Reset();
        if (p_headCount <= 0 ||
            !IsSupportedSlotCount(p_slotsPerHead) ||
            p_voteHeadCount <= 0 || p_minHeadCount <= 0 ||
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
        m_header.m_voteHeadCount =
            static_cast<std::uint32_t>(p_voteHeadCount);
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

    bool SetHeadAttributes(
        SizeType p_head,
        const std::uint32_t* p_attributes,
        int p_attributeCount)
    {
        if (p_head < 0 || p_attributes == nullptr ||
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
        if (p_head < 0 ||
            static_cast<std::uint32_t>(p_head) >=
                m_header.m_headCount ||
            p_tags.size() > m_header.m_slotsPerHead)
        {
            return false;
        }
        const size_t offset =
            static_cast<size_t>(p_head) *
            m_header.m_slotsPerHead;
        std::fill_n(
            m_tags.begin() + offset,
            m_header.m_slotsPerHead, EmptyTag);
        std::unordered_set<std::uint32_t> unique;
        for (size_t slot = 0; slot < p_tags.size(); ++slot)
        {
            if (p_tags[slot] == EmptyTag ||
                !unique.insert(p_tags[slot]).second)
            {
                return false;
            }
            m_tags[offset + slot] = p_tags[slot];
        }
        return true;
    }

    bool Supports(SizeType p_head, std::uint32_t p_tag) const
    {
        if (p_head < 0 ||
            static_cast<std::uint32_t>(p_head) >=
                m_header.m_headCount)
        {
            return false;
        }
        const size_t offset =
            static_cast<size_t>(p_head) *
            m_header.m_slotsPerHead;
        for (std::uint32_t slot = 0;
             slot < m_header.m_slotsPerHead; ++slot)
        {
            if (m_tags[offset + slot] == p_tag) return true;
        }
        return false;
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
        return m_tags[
            static_cast<size_t>(p_head) *
                m_header.m_slotsPerHead +
            static_cast<size_t>(p_slot)];
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
        return m_tags.data() +
            static_cast<size_t>(p_head) *
                m_header.m_slotsPerHead;
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
        return m_headAttributes.data() +
            static_cast<size_t>(p_head) *
                m_header.m_attributeCount;
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

        const size_t offset =
            static_cast<size_t>(p_head) *
            m_header.m_slotsPerHead;
        for (std::uint32_t slot = 0;
             slot < m_header.m_slotsPerHead; ++slot)
        {
            const std::uint32_t tag = m_tags[offset + slot];
            if (tag != EmptyTag) tags.push_back(tag);
        }
        return tags;
    }

    bool Validate(std::string* p_error = nullptr) const
    {
        auto fail = [&](const std::string& p_message) {
            if (p_error != nullptr) *p_error = p_message;
            return false;
        };
        if (m_header.m_magic != 0x3153544cU ||
            m_header.m_version != 3 ||
            m_header.m_headerBytes != sizeof(Header) ||
            m_header.m_headCount == 0 ||
            m_header.m_slotsPerHead == 0 ||
            m_header.m_slotsPerHead >
                static_cast<std::uint32_t>(
                    (std::numeric_limits<int>::max)()) ||
            m_header.m_voteHeadCount == 0 ||
            m_header.m_minHeadCount == 0 ||
            m_header.m_tagCount == 0 ||
            m_header.m_attributeCount == 0 ||
            m_header.m_keyColumn >=
                m_header.m_attributeCount ||
            m_header.m_generationFingerprint == 0)
        {
            return fail("invalid limited-tag support header");
        }
        if (!m_legacyWithoutTagVectorCounts &&
            (m_header.m_vectorCount == 0 ||
             m_tagVectorCounts.size() !=
                 m_header.m_tagCount))
        {
            return fail(
                "limited-tag vector-count metadata is incomplete");
        }
        if (m_tags.size() !=
            static_cast<size_t>(m_header.m_headCount) *
                m_header.m_slotsPerHead)
        {
            return fail("limited-tag support body size mismatch");
        }
        if (m_headAttributes.size() !=
            static_cast<size_t>(m_header.m_headCount) *
                m_header.m_attributeCount)
        {
            return fail(
                "limited-tag head attribute size mismatch");
        }
        if (static_cast<std::uint64_t>(
                m_header.m_tagCount) >
            static_cast<std::uint64_t>(
                m_header.m_headCount) *
                m_header.m_slotsPerHead)
        {
            return fail(
                "limited-tag support tag count exceeds body capacity");
        }

        std::unordered_map<std::uint32_t, std::uint32_t> coverage;
        for (std::uint32_t head = 0;
             head < m_header.m_headCount; ++head)
        {
            std::unordered_set<std::uint32_t> unique;
            const size_t offset =
                static_cast<size_t>(head) *
                m_header.m_slotsPerHead;
            for (std::uint32_t slot = 0;
                 slot < m_header.m_slotsPerHead; ++slot)
            {
                const std::uint32_t tag = m_tags[offset + slot];
                if (tag == EmptyTag) continue;
                if (!unique.insert(tag).second)
                {
                    return fail(
                        "duplicate tag in limited-tag head support");
                }
                ++coverage[tag];
            }
            const std::uint32_t ownTag =
                m_tags[offset];
            const std::uint32_t* attributes =
                HeadAttributes(
                    static_cast<SizeType>(head));
            if (ownTag == EmptyTag ||
                attributes == nullptr ||
                attributes[m_header.m_keyColumn] !=
                    ownTag)
            {
                return fail(
                    "limited-tag own tag does not match the key-column attribute");
            }
        }
        if (coverage.size() != m_header.m_tagCount)
        {
            return fail("limited-tag support tag count mismatch");
        }
        for (const auto& entry : coverage)
        {
            if (entry.second < m_header.m_minHeadCount)
            {
                return fail(
                    "limited-tag support coverage below minimum");
            }
        }
        if (!m_legacyWithoutTagVectorCounts)
        {
            std::uint64_t total = 0;
            std::uint32_t previous = 0;
            for (size_t i = 0;
                 i < m_tagVectorCounts.size(); ++i)
            {
                const TagCountRecord& entry =
                    m_tagVectorCounts[i];
                if (entry.m_tag == EmptyTag ||
                    entry.m_reserved != 0 ||
                    entry.m_count == 0 ||
                    entry.m_count >
                        m_header.m_vectorCount ||
                    (i > 0 && entry.m_tag <= previous) ||
                    coverage.find(entry.m_tag) ==
                        coverage.end() ||
                    total >
                        (std::numeric_limits<
                            std::uint64_t>::max)() -
                            entry.m_count)
                {
                    return fail(
                        "invalid limited-tag vector-count metadata");
                }
                previous = entry.m_tag;
                total += entry.m_count;
            }
            if (total != m_header.m_vectorCount)
            {
                return fail(
                    "limited-tag vector counts do not cover the dataset");
            }
        }
        return true;
    }

    bool Finalize(std::string* p_error = nullptr)
    {
        std::unordered_set<std::uint32_t> tags;
        for (std::uint32_t tag : m_tags)
        {
            if (tag != EmptyTag) tags.insert(tag);
        }
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
            BodyFingerprint(
                m_tags, m_headAttributes,
                m_header.m_vectorCount,
                m_tagVectorCounts);
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
        output.write(
            reinterpret_cast<const char*>(&m_header),
            sizeof(m_header));
        output.write(
            reinterpret_cast<const char*>(m_tags.data()),
            static_cast<std::streamsize>(
                m_tags.size() * sizeof(std::uint32_t)));
        output.write(
            reinterpret_cast<const char*>(
                m_headAttributes.data()),
            static_cast<std::streamsize>(
                m_headAttributes.size() *
                sizeof(std::uint32_t)));
        output.write(
            reinterpret_cast<const char*>(
                m_tagVectorCounts.data()),
            static_cast<std::streamsize>(
                m_tagVectorCounts.size() *
                sizeof(TagCountRecord)));
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
        int p_expectedVoteHeadCount,
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
        if (input && prefix[0] == 0x3153544cU &&
            prefix[1] == 1 &&
            prefix[2] == sizeof(HeaderV1))
        {
            HeaderV1 legacy;
            input.read(
                reinterpret_cast<char*>(&legacy),
                sizeof(legacy));
            if (!input)
            {
                if (p_error != nullptr)
                    *p_error =
                        "cannot read limited-tag v1 header";
                Reset();
                return false;
            }
            m_header.m_headCount = legacy.m_headCount;
            m_header.m_slotsPerHead =
                legacy.m_slotsPerHead;
            m_header.m_voteHeadCount =
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
            if (!input)
            {
                if (p_error != nullptr)
                    *p_error =
                        "cannot read limited-tag v2 header";
                Reset();
                return false;
            }
            m_header.m_headCount = legacy.m_headCount;
            m_header.m_slotsPerHead =
                legacy.m_slotsPerHead;
            m_header.m_voteHeadCount =
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
            m_header.m_version != 3 ||
            m_header.m_headerBytes != sizeof(Header) ||
            m_header.m_headCount !=
                static_cast<std::uint32_t>(p_expectedHeadCount) ||
            !IsSupportedSlotCount(
                p_expectedSlotsPerHead) ||
            m_header.m_slotsPerHead !=
                static_cast<std::uint32_t>(p_expectedSlotsPerHead) ||
            m_header.m_voteHeadCount !=
                static_cast<std::uint32_t>(p_expectedVoteHeadCount) ||
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
        const size_t tagCount =
            static_cast<size_t>(m_header.m_headCount) *
            m_header.m_slotsPerHead;
        const size_t attributeCount =
            static_cast<size_t>(m_header.m_headCount) *
            m_header.m_attributeCount;
        const std::streamoff expectedBytes =
            bodyOffset +
            static_cast<std::streamoff>(
                (tagCount +
                 (legacyV1 ? 0 : attributeCount)) *
                sizeof(std::uint32_t)) +
            static_cast<std::streamoff>(
                (legacyV1 || legacyV2
                     ? 0
                     : m_header.m_tagCount) *
                sizeof(TagCountRecord));
        if (fileBytes != expectedBytes)
        {
            if (p_error != nullptr)
                *p_error = "limited-tag support file size mismatch";
            Reset();
            return false;
        }
        m_tags.resize(tagCount);
        input.read(
            reinterpret_cast<char*>(m_tags.data()),
            static_cast<std::streamsize>(
                tagCount * sizeof(std::uint32_t)));
        if (legacyV1)
        {
            m_headAttributes.resize(
                static_cast<size_t>(
                    m_header.m_headCount));
            for (std::uint32_t head = 0;
                 head < m_header.m_headCount; ++head)
            {
                m_headAttributes[
                    static_cast<size_t>(head)] =
                    m_tags[
                        static_cast<size_t>(head) *
                        m_header.m_slotsPerHead];
            }
        }
        else
        {
            m_headAttributes.resize(attributeCount);
            input.read(
                reinterpret_cast<char*>(
                    m_headAttributes.data()),
                static_cast<std::streamsize>(
                    attributeCount *
                    sizeof(std::uint32_t)));
        }
        if (!legacyV1 && !legacyV2)
        {
            m_tagVectorCounts.resize(
                m_header.m_tagCount);
            input.read(
                reinterpret_cast<char*>(
                    m_tagVectorCounts.data()),
                static_cast<std::streamsize>(
                    m_tagVectorCounts.size() *
                    sizeof(TagCountRecord)));
        }
        const std::uint64_t bodyFingerprint =
            legacyV1
                ? BodyFingerprintV1(m_tags)
                : legacyV2
                     ? BodyFingerprintV2(
                           m_tags,
                           m_headAttributes)
                     : BodyFingerprint(
                           m_tags,
                           m_headAttributes,
                           m_header.m_vectorCount,
                           m_tagVectorCounts);
        if (!input ||
            bodyFingerprint !=
                m_header.m_bodyFingerprint)
        {
            if (p_error != nullptr && p_error->empty())
                *p_error =
                    "limited-tag support body fingerprint mismatch";
            Reset();
            return false;
        }
        if (!Validate(p_error))
        {
            Reset();
            return false;
        }
        RebuildHeadIndex();
        return true;
    }

    SizeType HeadCount() const
    {
        return static_cast<SizeType>(m_header.m_headCount);
    }

    int SlotsPerHead() const
    {
        return static_cast<int>(m_header.m_slotsPerHead);
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
            const size_t offset =
                static_cast<size_t>(head) *
                m_header.m_slotsPerHead;
            for (std::uint32_t slot = 0;
                 slot < m_header.m_slotsPerHead;
                 ++slot)
            {
                const std::uint32_t tag =
                    m_tags[offset + slot];
                if (tag != EmptyTag)
                {
                    m_headsByTag[tag].push_back(
                        static_cast<SizeType>(
                            head));
                }
            }
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

    Header m_header;
    std::vector<std::uint32_t> m_tags;
    std::vector<std::uint32_t> m_headAttributes;
    std::vector<TagCountRecord> m_tagVectorCounts;
    std::unordered_map<
        std::uint32_t,
        std::vector<SizeType>> m_headsByTag;
    bool m_legacyWithoutTagVectorCounts = false;
};

} // namespace SPANN
} // namespace SPTAG

#endif // _SPTAG_SPANN_LIMITEDTAGSUPPORT_H_
