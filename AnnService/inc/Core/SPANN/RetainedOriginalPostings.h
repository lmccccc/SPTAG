// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_SPANN_RETAINEDORIGINALPOSTINGS_H_
#define _SPTAG_SPANN_RETAINEDORIGINALPOSTINGS_H_

#include "LimitedTagSupport.h"
#include <cmath>

namespace SPTAG { namespace SPANN {

template<class Candidates, class Allowed, class Distance, class Emit>
bool SelectLimitedTagPostingCandidates(const Candidates& candidates, SizeType headCount,
    int replicas, float rngFactor, const Allowed& allowed, const Distance& distance,
    std::vector<SizeType>& selected, const Emit& emit)
{
    selected.clear();
    for (int rank = 0; rank < candidates.GetResultNum() &&
         selected.size() < static_cast<std::size_t>(replicas); ++rank) {
        const auto* candidate = candidates.GetResult(rank);
        if (candidate == nullptr || candidate->VID < 0) break;
        if (candidate->VID >= headCount || !allowed(candidate->VID)) return false;
        bool accepted = true;
        for (auto prior : selected)
            if (rngFactor * distance(candidate->VID, prior) <= candidate->Dist) {
                accepted = false;
                break;
            }
        if (!accepted) continue;
        selected.push_back(candidate->VID);
        emit(*candidate);
    }
    return true;
}

// Repeated labels occupy candidate positions, but the stored support remains a set.
template<class TagAt>
std::vector<std::uint32_t> SelectNearestLimitedLabels(std::uint32_t own, SizeType ownItem,
    const std::vector<std::pair<float, SizeType>>& candidates, int slots, const TagAt& tagAt)
{
    if (slots <= 0 || own == LimitedTagSupport::EmptyTag)
        throw std::invalid_argument("Invalid nearest limited-label slots or anchor");
    std::vector<std::pair<float, SizeType>> nearest;
    const auto capacity = static_cast<std::size_t>(slots - 1);
    for (const auto& candidate : candidates) {
        if (!std::isfinite(candidate.first) || candidate.second < 0)
            throw std::invalid_argument("Invalid nearest limited-label candidate");
        if (candidate.second == ownItem || capacity == 0) continue;
        if (nearest.size() < capacity) {
            nearest.push_back(candidate);
            std::push_heap(nearest.begin(), nearest.end());
        } else if (candidate < nearest.front()) {
            std::pop_heap(nearest.begin(), nearest.end());
            nearest.back() = candidate;
            std::push_heap(nearest.begin(), nearest.end());
        }
    }
    std::vector<std::uint32_t> tags{own};
    for (const auto& candidate : nearest) {
        const auto tag = tagAt(candidate.second);
        if (tag == LimitedTagSupport::EmptyTag)
            throw std::invalid_argument("Invalid nearest limited-label tag");
        tags.push_back(tag);
    }
    std::sort(tags.begin(), tags.end());
    tags.erase(std::unique(tags.begin(), tags.end()), tags.end());
    const auto anchor = std::find(tags.begin(), tags.end(), own);
    std::rotate(tags.begin(), anchor, anchor + 1);
    return tags;
}

// Cuts retain a prefix of each head group; discarded scratch suffixes are not O.
template <typename TEdge, typename TConsumer>
bool VisitRetainedOriginalPostings(
    const std::vector<TEdge>& edges, const std::vector<int>& retainedCounts,
    SizeType vectorCount, const TConsumer& consume, std::string* error = nullptr)
{
    auto fail = [&](const char* message) {
        if (error != nullptr) *error = message;
        return false;
    };
    if (vectorCount <= 0 || retainedCounts.empty() ||
        retainedCounts.size() > static_cast<size_t>(MaxSize))
        return fail("invalid retained O posting dimensions");
    size_t read = 0;
    for (size_t head = 0; head < retainedCounts.size(); ++head)
    {
        const size_t begin = read;
        while (read < edges.size() && edges[read].node == static_cast<SizeType>(head)) ++read;
        const int retained = retainedCounts[head];
        if (retained < 0 || static_cast<size_t>(retained) > read - begin)
            return fail("retained O prefix exceeds its sorted head group");
        const size_t end = begin + static_cast<size_t>(retained);
        for (size_t row = begin; row < end; ++row)
            if (edges[row].tonode < 0 || edges[row].tonode >= vectorCount)
                return fail("invalid retained O vector ID");
        if (!consume(static_cast<SizeType>(head), begin, end)) return false;
    }
    for (; read < edges.size(); ++read)
        if (edges[read].node != MaxSize)
            return fail("retained O scratch edges are not sorted by head");
    return true;
}

// Streaming top distinct tags by minimum member distance. Memory is O(slots),
// independent of vector count, posting length and the number of distinct tags.
template <typename TEdge, typename TTagAt>
bool SelectRetainedOriginalBaseTags(
    LimitedTagSupport& support, const std::vector<std::uint32_t>& ownTags,
    const std::vector<TEdge>& edges, const std::vector<int>& retainedCounts,
    SizeType vectorCount, const TTagAt& tagAt, std::string* error = nullptr)
{
    if (ownTags.size() != retainedCounts.size() ||
        ownTags.size() != static_cast<size_t>(support.HeadCount()) ||
        support.SlotsPerHead() <= 0)
    {
        if (error != nullptr) *error = "invalid retained O base support dimensions";
        return false;
    }
    using Choice = std::pair<float, std::uint32_t>;
    std::vector<Choice> choices;
    const size_t capacity = static_cast<size_t>(support.SlotsPerHead() - 1);
    choices.reserve(capacity);
    std::vector<std::uint32_t> tags;
    tags.reserve(capacity + 1);
    return VisitRetainedOriginalPostings(edges, retainedCounts, vectorCount,
        [&](SizeType head, size_t begin, size_t end) {
            choices.clear();
            const auto own = ownTags[static_cast<size_t>(head)];
            if (own == LimitedTagSupport::EmptyTag) return false;
            for (size_t row = begin; row < end; ++row)
            {
                const auto tag = tagAt(edges[row].tonode);
                const float distance = edges[row].distance;
                if (tag == LimitedTagSupport::EmptyTag || !std::isfinite(distance))
                {
                    if (error != nullptr) *error = "invalid retained O tag or distance";
                    return false;
                }
                if (tag == own || capacity == 0) continue;
                auto found = std::find_if(choices.begin(), choices.end(),
                    [tag](const Choice& choice) { return choice.second == tag; });
                if (found != choices.end())
                    found->first = (std::min)(found->first, distance);
                else if (choices.size() < capacity)
                    choices.emplace_back(distance, tag);
                else
                {
                    auto worst = std::max_element(choices.begin(), choices.end());
                    const Choice candidate(distance, tag);
                    if (candidate < *worst) *worst = candidate;
                }
            }
            std::sort(choices.begin(), choices.end());
            tags.clear();
            tags.push_back(own);
            for (const auto& choice : choices) tags.push_back(choice.second);
            return support.SetHeadTags(head, tags);
        }, error);
}

} }
#endif
