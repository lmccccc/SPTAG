// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_SPANN_LIMITEDTAGSUPPORTEXPANSION_H_
#define _SPTAG_SPANN_LIMITEDTAGSUPPORTEXPANSION_H_

#include "LimitedTagSupport.h"

#include <algorithm>
#include <cstdint>
#include <limits>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace SPTAG
{
namespace SPANN
{

class LimitedTagSupportExpansion
{
public:
    bool Initialize(
        const LimitedTagSupport& p_base,
        const std::vector<std::uint32_t>& p_observedTags,
        int p_floor,
        std::uint64_t p_maxExtraSupports,
        std::string* p_error = nullptr)
    {
        m_base = nullptr;
        m_plans.clear();
        m_required.clear();
        m_selectedCount = 0;
        m_expandedTags = 0;
        m_cappedTags = 0;
        m_originalAssignments = 0;
        m_lastHead = -1;
        if (p_base.HeadCount() <= 0 || p_base.HasExpansion() ||
            p_floor <= 0 || p_maxExtraSupports == 0 || p_observedTags.empty())
            return Fail(p_error, "invalid O-based support expansion configuration");
        std::uint32_t previous = 0;
        for (std::size_t i = 0; i < p_observedTags.size(); ++i)
        {
            const auto tag = p_observedTags[i];
            if (tag == LimitedTagSupport::EmptyTag ||
                (i > 0 && tag <= previous) || p_base.TagVectorCount(tag) == 0)
                return Fail(p_error, "O-based expansion requires sorted observed tag counts");
            m_required.emplace(tag, 0);
            previous = tag;
        }
        for (SizeType head = 0; head < p_base.HeadCount(); ++head)
        {
            for (int slot = 0; slot < p_base.SlotsPerHead(); ++slot)
            {
                const auto tag = p_base.TagAt(head, slot);
                if (tag == LimitedTagSupport::EmptyTag) continue;
                const auto found = m_required.find(tag);
                if (found == m_required.end())
                    return Fail(p_error, "base support contains an unobserved tag");
                ++found->second;
            }
        }
        m_floor = static_cast<std::uint32_t>(p_floor);
        for (auto& entry : m_required)
        {
            if (entry.second < m_floor)
            {
                Plan plan;
                plan.baseCoverage = entry.second;
                plan.needed = m_floor - entry.second;
                m_plans.emplace(entry.first, std::move(plan));
            }
            else
            {
                entry.second = m_floor;
            }
        }
        m_maxExtraSupports = p_maxExtraSupports;
        m_base = &p_base;
        return true;
    }

    bool ObserveOriginalAssignment(
        SizeType p_head, std::uint32_t p_tag, std::string* p_error = nullptr)
    {
        if (m_base == nullptr || p_head < 0 || p_head >= m_base->HeadCount() ||
            p_head < m_lastHead || p_tag == LimitedTagSupport::EmptyTag ||
            m_required.find(p_tag) == m_required.end())
            return Fail(p_error, "invalid or unsorted retained O assignment");
        m_lastHead = p_head;
        ++m_originalAssignments;
        const auto found = m_plans.find(p_tag);
        if (found == m_plans.end()) return true;
        auto& plan = found->second;
        if (plan.lastOriginalHead == p_head) return true;
        plan.lastOriginalHead = p_head;
        if (m_base->Supports(p_head, p_tag)) return true;

        const RankedHead candidate{Rank(p_tag, p_head), p_head};
        if (plan.candidates.size() < plan.needed)
        {
            if (m_selectedCount >= m_maxExtraSupports)
                return Fail(p_error, "O-based support expansion exceeds LimitedTagMaxExtraSupports");
            plan.candidates.push_back(candidate);
            std::push_heap(plan.candidates.begin(), plan.candidates.end());
            ++m_selectedCount;
        }
        else if (candidate < plan.candidates.front())
        {
            std::pop_heap(plan.candidates.begin(), plan.candidates.end());
            plan.candidates.back() = candidate;
            std::push_heap(plan.candidates.begin(), plan.candidates.end());
        }
        return true;
    }

    bool Apply(LimitedTagSupport& p_support, std::string* p_error = nullptr)
    {
        if (m_base != &p_support)
            return Fail(p_error, "O-based expansion must apply to its original base support");
        std::vector<std::pair<SizeType, std::uint32_t>> additions;
        if (m_selectedCount > additions.max_size())
            return Fail(p_error, "O-based support expansion size overflow");
        additions.reserve(static_cast<std::size_t>(m_selectedCount));
        for (const auto& entry : m_plans)
        {
            const auto& plan = entry.second;
            const auto target = plan.baseCoverage +
                static_cast<std::uint32_t>(plan.candidates.size());
            if (target == 0)
                return Fail(p_error, "an observed tag has neither an own/support head nor a retained O posting");
            m_required[entry.first] = target;
            m_expandedTags += !plan.candidates.empty();
            m_cappedTags += target < m_floor;
            for (const auto& candidate : plan.candidates)
                additions.emplace_back(candidate.second, entry.first);
        }
        std::sort(additions.begin(), additions.end());
        if (std::adjacent_find(additions.begin(), additions.end()) != additions.end())
            return Fail(p_error, "duplicate O-based support expansion pair");
        m_plans.clear();
        m_plans.rehash(0);

        std::vector<std::uint64_t> offsets(
            static_cast<std::size_t>(p_support.HeadCount()) + 1, 0);
        std::vector<std::uint32_t> tags;
        tags.reserve(additions.size());
        for (const auto& addition : additions)
        {
            ++offsets[static_cast<std::size_t>(addition.first) + 1];
            tags.push_back(addition.second);
        }
        for (std::size_t head = 1; head < offsets.size(); ++head)
            offsets[head] += offsets[head - 1];
        if (!p_support.ConfigureExpansion(
                std::move(offsets), std::move(tags), m_required,
                m_maxExtraSupports, p_error))
            return false;
        m_required.clear();
        m_required.rehash(0);
        m_base = nullptr;
        return true;
    }

    template <typename TEdge, typename TTagAt>
    bool ObserveRetainedOriginalPostings(
        const std::vector<TEdge>& p_edges,
        const std::vector<int>& p_retainedCounts,
        SizeType p_vectorCount,
        const TTagAt& p_tagAt,
        std::string* p_error = nullptr)
    {
        if (m_base == nullptr || m_originalAssignments != 0 || p_vectorCount <= 0 ||
            p_retainedCounts.size() != static_cast<std::size_t>(m_base->HeadCount()))
            return Fail(p_error, "invalid retained O posting dimensions");
        std::size_t read = 0;
        for (SizeType head = 0; head < m_base->HeadCount(); ++head)
        {
            const std::size_t begin = read;
            while (read < p_edges.size() && p_edges[read].node == head) ++read;
            const int retained = p_retainedCounts[static_cast<std::size_t>(head)];
            if (retained < 0 || static_cast<std::size_t>(retained) > read - begin)
                return Fail(p_error, "retained O prefix exceeds its sorted head group");
            // Posting cuts change counts, not the discarded suffix in the scratch edge array.
            for (int row = 0; row < retained; ++row)
            {
                const auto vid = p_edges[begin + static_cast<std::size_t>(row)].tonode;
                if (vid < 0 || vid >= p_vectorCount)
                    return Fail(p_error, "invalid retained O vector ID");
                if (!ObserveOriginalAssignment(head, p_tagAt(vid), p_error)) return false;
            }
        }
        for (; read < p_edges.size(); ++read)
            if (p_edges[read].node != MaxSize)
                return Fail(p_error, "retained O scratch edges are not sorted by head");
        return true;
    }

    std::uint64_t AddedSupports() const { return m_selectedCount; }
    std::uint64_t ExpandedTags() const { return m_expandedTags; }
    std::uint64_t CappedTags() const { return m_cappedTags; }
    std::uint64_t OriginalAssignments() const { return m_originalAssignments; }

private:
    using RankedHead = std::pair<std::uint64_t, SizeType>;
    struct Plan
    {
        std::uint32_t baseCoverage = 0;
        std::uint32_t needed = 0;
        SizeType lastOriginalHead = -1;
        std::vector<RankedHead> candidates;
    };

    static bool Fail(std::string* p_error, const char* p_message)
    {
        if (p_error != nullptr) *p_error = p_message;
        return false;
    }

    static std::uint64_t Rank(std::uint32_t p_tag, SizeType p_head)
    {
        // Stable pseudorandom ranks do not depend on O member order or worker scheduling.
        std::uint64_t value =
            (static_cast<std::uint64_t>(p_tag) << 32) |
            static_cast<std::uint32_t>(p_head);
        value += 0x9e3779b97f4a7c15ULL;
        value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
        value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
        return value ^ (value >> 31);
    }

    const LimitedTagSupport* m_base = nullptr;
    std::unordered_map<std::uint32_t, Plan> m_plans;
    std::unordered_map<std::uint32_t, std::uint32_t> m_required;
    std::uint32_t m_floor = 0;
    std::uint64_t m_maxExtraSupports = 0;
    std::uint64_t m_selectedCount = 0;
    std::uint64_t m_expandedTags = 0;
    std::uint64_t m_cappedTags = 0;
    std::uint64_t m_originalAssignments = 0;
    SizeType m_lastHead = -1;
};

} // namespace SPANN
} // namespace SPTAG

#endif // _SPTAG_SPANN_LIMITEDTAGSUPPORTEXPANSION_H_
