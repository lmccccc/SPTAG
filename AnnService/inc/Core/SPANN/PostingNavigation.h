// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#pragma once
#include "inc/Core/Common/PostingNavigation.h"
#include "inc/Core/Common/WorkSpace.h"
#include "inc/Core/SPANN/LimitedTagSupport.h"
#include "inc/Core/SPANN/SecondLevelHeadPostings.h"
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <tuple>
#include <unordered_map>
#include <vector>
#include "inc/Core/Common/GraphAccessStats.h"

namespace SPTAG { namespace SPANN {
inline Cache::PostingBitmask BuildHierarchyQuerySignature(
    const std::vector<std::uint32_t>& anchors, const LimitedTagSupport& support,
    const std::vector<SecondLevelHeadPostings>& postings)
{
    Cache::PostingBitmask signature;
    signature.Clear();
    for (const auto tag : anchors) {
        for (const auto& layer : postings)
            if (!support.TagSelectivityInRange(tag, layer.SignatureMinSelectivity(),
                                               layer.SignatureMaxSelectivity())) {
                signature.Clear();
                return signature;
            }
        signature.Insert(tag);
    }
    return signature;
}

class PostingOwners
{
    struct Layer {
        int lower, upper, replicas;
        std::vector<int> parents;
    };
    std::vector<Layer> m_layers;
public:
    struct Range {
        const int* first;
        const int* last;
        const int* begin() const { return first; }
        const int* end() const { return last; }
    };
    template<class Postings> explicit PostingOwners(const Postings& postings)
    {
        if (postings.empty()) throw std::invalid_argument("Posting navigation requires hierarchy CSR");
        for (const auto& posting : postings) {
            const int lower = posting.FirstLevelHeadCount(), upper = posting.SecondLevelHeadCount();
            const int replicas = (std::min)(posting.ReplicaCount(), upper);
            if (lower <= 0 || replicas <= 0 ||
                (!m_layers.empty() && lower != m_layers.back().upper))
                throw std::invalid_argument("Invalid posting hierarchy shape");
            Layer layer{lower, upper, replicas, std::vector<int>(std::size_t(lower) * replicas)};
            std::vector<int> counts(lower, 0);
            for (int parent = 0; parent < upper; ++parent)
                for (auto p = posting.Begin(parent); p != posting.End(parent); ++p) {
                    if (*p >= static_cast<unsigned>(lower))
                        throw std::invalid_argument("Invalid posting child");
                    auto* row = layer.parents.data() + std::size_t(*p) * replicas;
                    auto& size = counts[*p];
                    if (size == replicas || std::find(row, row + size, parent) != row + size)
                        throw std::invalid_argument("Duplicate/excess posting owner");
                    row[size++] = parent;
                }
            for (int count : counts) if (count != replicas)
                throw std::invalid_argument("Posting owner count disagrees with persisted replicas");
            m_layers.push_back(std::move(layer));
        }
    }
    std::size_t Levels() const { return m_layers.size(); }
    int HeadCount() const { return m_layers.front().lower; }
    int Count(std::size_t level) const { return m_layers.at(level).upper; }
    Range Parents(std::size_t level, int id) const
    {
        const auto& layer = m_layers.at(level);
        if (id < 0 || id >= layer.lower) throw std::out_of_range("Posting owner ID");
        const auto* begin = layer.parents.data() + std::size_t(id) * layer.replicas;
        return {begin, begin + layer.replicas};
    }
};

template<class Postings, class Signature, class Distance, class Owners = PostingOwners>
class HierarchyPostingQuery final : public COMMON::PostingNavigation
{
    const Owners& m_owners;
    const Postings& m_postings;
    Signature m_signature;
    Distance m_distance;
    struct State {
        unsigned char signature = 0;
        bool discovered = false, expanded = false, ownersDiscovered = false;
    };
    struct Collection {
        bool canContinue = true;
        bool targetFilled = false;
    };
    enum class DiscoveryPath { EntryOrOwner, Child };
    struct Candidate {
        float distance;
        int id;
        std::size_t level;
        bool operator>(const Candidate& other) const
        {
            return std::tie(distance, level, id) >
                std::tie(other.distance, other.level, other.id);
        }
    };
    std::vector<std::unordered_map<int, State>> m_state;
    std::vector<Candidate> m_discovered;
    COMMON::DistPriorityQueue m_distancePool;
    int m_searchCapacity = 0;
    bool m_converged = false;
    State& GetState(std::size_t level, int id)
    {
        if (id < 0 || id >= m_owners.Count(level))
            throw std::out_of_range("Posting state ID");
        auto inserted = m_state[level].try_emplace(id);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (inserted.second && COMMON::g_graphAccessStats) {
            ++COMMON::g_graphAccessStats->m_postingStates;
            COMMON::g_graphAccessStats->m_stateInitializedBytes += sizeof(State);
            ++(level == 0 ? COMMON::g_graphAccessStats->m_h2Postings :
                COMMON::g_graphAccessStats->m_upperPostings).uniqueStates;
        }
#endif
        return inserted.first->second;
    }
    auto Parents(std::size_t level, int id)
    {
        const auto parents = m_owners.Parents(level, id);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (COMMON::g_graphAccessStats) {
            COMMON::g_graphAccessStats->m_ownerReferences += parents.end() - parents.begin();
            (level == 0 ? COMMON::g_graphAccessStats->m_h2Postings :
                COMMON::g_graphAccessStats->m_upperPostings).ownerReferences += parents.end() - parents.begin();
        }
#endif
        return parents;
    }
    void DiscoverOwners(std::size_t level, int id, State& state)
    {
        if (state.ownersDiscovered) return;
        state.ownersDiscovered = true;
        if (level + 1 == m_owners.Levels()) return;
        for (int parent : Parents(level + 1, id))
            Discover(level + 1, parent, DiscoveryPath::EntryOrOwner);
    }
    bool Discover(std::size_t level, int id, DiscoveryPath path)
    {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (COMMON::g_graphAccessStats)
            ++(level == 0 ? COMMON::g_graphAccessStats->m_h2Postings :
                COMMON::g_graphAccessStats->m_upperPostings).candidateConsiderations;
#endif
        auto& state = GetState(level, id);
        if (!state.signature) {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            if (COMMON::g_graphAccessStats) ++COMMON::g_graphAccessStats->m_signatureChecks;
#endif
            state.signature = m_signature(level, id) ? 1 : 2;
        }
        const bool allowed = state.signature == 1;
        if (!allowed) {
            state.discovered = true;
            // Rejected descent must not fan out through the child's other owners.
            if (path == DiscoveryPath::EntryOrOwner) DiscoverOwners(level, id, state);
            return false;
        }
        if (state.discovered) return allowed;
        state.discovered = true;
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (COMMON::g_graphAccessStats) {
            ++COMMON::g_graphAccessStats->m_upperDistances;
            ++(level == 0 ? COMMON::g_graphAccessStats->m_h2Postings :
                COMMON::g_graphAccessStats->m_upperPostings).representativeDistances;
        }
#endif
        const float distance = m_distance(level, id);
        if (!std::isfinite(distance))
            throw std::runtime_error("Invalid posting representative distance");
        if (level == 0) m_distancePool.insert(distance);
        m_discovered.push_back({distance, id, level});
        std::push_heap(m_discovered.begin(), m_discovered.end(), std::greater<Candidate>());
        return true;
    }
    void ExpandRow(std::size_t level, int selected, const Consumer& consume, Collection& collection)
    {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (COMMON::g_graphAccessStats)
            ++(level == 0 ? COMMON::g_graphAccessStats->m_h2Postings :
                COMMON::g_graphAccessStats->m_upperPostings).expandAttempts;
#endif
        auto& state = GetState(level, selected);
        if (state.expanded) {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            if (COMMON::g_graphAccessStats)
                ++(level == 0 ? COMMON::g_graphAccessStats->m_h2Postings :
                    COMMON::g_graphAccessStats->m_upperPostings).expandedSkips;
#endif
            return;
        }
        if (state.signature != 1)
            throw std::logic_error("Cannot expand a signature-rejected posting");
        const auto* begin = m_postings[level].Begin(selected);
        const auto* end = m_postings[level].End(selected);
        const auto degree = begin == end ? 0 : end - begin;
        RowResult row;
        if (level == 0) {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            if (COMMON::g_graphAccessStats) {
                ++COMMON::g_graphAccessStats->m_selectedPostingRows;
                COMMON::g_graphAccessStats->m_auxiliaryMembers += degree;
            }
#endif
            row = consume(begin, static_cast<int>(degree));
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            if (COMMON::g_graphAccessStats) {
                if (row.eligible == 0) ++COMMON::g_graphAccessStats->m_selectedH2ZeroEligibleRows;
                if (row.newCandidates == 0) ++COMMON::g_graphAccessStats->m_selectedH2ZeroFreshRows;
            }
#endif
            if (row.newCandidates > row.eligible)
                throw std::logic_error("Fresh candidates must be matching physical H1 neighbors");
            collection.canContinue = row.canContinue;
            collection.targetFilled = row.targetFilled;
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            if (COMMON::g_graphAccessStats)
                COMMON::g_graphAccessStats->m_postingNewCandidates += row.newCandidates;
#endif
        } else {
            for (auto member = begin; member != end; ++member) {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
                if (COMMON::g_graphAccessStats) {
                    ++COMMON::g_graphAccessStats->m_upperMembers;
                    auto& references = level == 1 ? COMMON::g_graphAccessStats->m_h2Postings :
                        COMMON::g_graphAccessStats->m_upperPostings;
                    ++references.memberReferences;
                }
#endif
                ++row.degree;
                row.eligible += Discover(level - 1, *member, DiscoveryPath::Child);
            }
        }
        if (row.degree != static_cast<std::size_t>(degree) || row.eligible > row.degree)
            throw std::logic_error("Posting consumer must report the complete physical row");
        state.expanded = true;
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (COMMON::g_graphAccessStats)
            ++(level == 0 ? COMMON::g_graphAccessStats->m_h2Postings :
                COMMON::g_graphAccessStats->m_upperPostings).completedRows;
#endif
    }
public:
    HierarchyPostingQuery(const Owners& owners, const Postings& postings,
                          Signature signature, Distance distance)
        : m_owners(owners), m_postings(postings), m_signature(signature), m_distance(distance)
    {
    }
    void SetSearchCapacity(int capacity) override
    {
        COMMON::PostingNavigation::SetSearchCapacity(capacity);
        if (!m_state.empty() && capacity != m_searchCapacity)
            throw std::logic_error("Cannot change an active posting search capacity");
        m_searchCapacity = capacity;
    }
    bool Converged() const override { return m_converged; }
    using COMMON::PostingNavigation::Expand;
    void Expand(const std::vector<int>& heads, const Consumer& consume) override
    {
        if (m_searchCapacity <= 0)
            throw std::logic_error("Posting search capacity must be configured before expansion");
        if (m_state.empty()) {
            m_state.resize(m_owners.Levels());
            m_distancePool.Resize(m_searchCapacity);
        }
        m_converged = false;
        Collection collection;
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (COMMON::g_graphAccessStats) ++COMMON::g_graphAccessStats->m_postingActivations;
#endif
        std::vector<int> owners;
        for (int head : heads) {
            const auto parents = Parents(0, head);
            owners.insert(owners.end(), parents.begin(), parents.end());
        }
        std::sort(owners.begin(), owners.end());
        owners.erase(std::unique(owners.begin(), owners.end()), owners.end());
        for (int owner : owners) Discover(0, owner, DiscoveryPath::EntryOrOwner);
        while (collection.canContinue && !m_discovered.empty()) {
            // Native ANN frontier convergence, not a lower bound on unvisited row members.
            if (m_discovered.front().distance > m_distancePool.worst()) {
                m_converged = true;
                break;
            }
            std::pop_heap(m_discovered.begin(), m_discovered.end(), std::greater<Candidate>());
            const Candidate selected = m_discovered.back();
            m_discovered.pop_back();
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            if (COMMON::g_graphAccessStats) ++COMMON::g_graphAccessStats->m_discoveredPops;
#endif
            auto& state = GetState(selected.level, selected.id);
            DiscoverOwners(selected.level, selected.id, state);
            ExpandRow(selected.level, selected.id, consume, collection);
        }
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (auto* stats = COMMON::g_graphAccessStats) {
            if (collection.targetFilled) ++stats->m_postingTargetMet;
            else {
                ++stats->m_postingUnderfilled;
                if (!collection.canContinue) ++stats->m_postingBudgetUnderfilled;
            }
        }
#endif
    }
};
}}
