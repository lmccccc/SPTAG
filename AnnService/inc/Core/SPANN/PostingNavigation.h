// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#pragma once
#include "inc/Core/Common/PostingNavigation.h"
#include "inc/Core/Common/WorkSpace.h"
#include "inc/Core/SPANN/LimitedTagSupport.h"
#include "inc/Core/SPANN/SecondLevelHeadPostings.h"
#include <algorithm>
#include <cmath>
#include <memory_resource>
#include <stdexcept>
#include <tuple>
#include <type_traits>
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

struct NoPostingPrefetch {
    void operator()(std::size_t, std::uint32_t) const {}
};

struct NoPostingChildFilter {
    bool operator()(std::size_t, int) const { return true; }
};

struct NoPostingRowSelection {};

struct NoPostingParentFilter {
    bool operator()(std::size_t, int) const { return true; }
};

struct AdjacentPostingLayout {
    template<class Owners>
    static std::size_t NavigationLevels(const Owners& owners) { return owners.Levels(); }
    template<class Postings>
    static std::size_t NavigationLevel(const Postings&, std::size_t level, int) { return level; }
    template<class Postings>
    static bool PreferResultAnchors(const Postings&) { return false; }
    template<class Postings>
    static bool IsLeaf(const Postings&, std::size_t level, int) { return level == 0; }
    template<class Postings>
    static bool IsH2(const Postings&, std::size_t level, int) { return level == 0; }
    static std::size_t ChildLevel(std::size_t level) { return level - 1; }
    template<class Owners, class Visit>
    static void VisitParents(const Owners& owners, std::size_t level, int id, Visit visit)
    {
        if (level + 1 != owners.Levels())
            for (int parent : owners.Parents(level + 1, id)) visit(level + 1, parent);
    }
};

template<class Postings, class Signature, class Distance, class Owners = PostingOwners,
         class Prefetch = NoPostingPrefetch, class Layout = AdjacentPostingLayout,
         class ChildFilter = NoPostingChildFilter, class RowSelection = NoPostingRowSelection,
         class ParentFilter = NoPostingParentFilter>
class HierarchyPostingQuery final : public COMMON::PostingNavigation
{
    const Owners& m_owners;
    const Postings& m_postings;
    Signature m_signature;
    Distance m_distance;
    Prefetch m_prefetch;
    ChildFilter m_childMayMatch;
    RowSelection m_selectRow;
    ParentFilter m_parentMayMatch;
    struct State {
        unsigned char signature = 0;
        bool discovered = false, expanded = false, ownersDiscovered = false;
    };
    class StateTable {
        struct Slot {
            std::uint32_t key = 0;
            State state;
        };
        static_assert(sizeof(Slot) == 8, "Packed posting state");
        std::pmr::vector<Slot> m_slots;
        std::size_t m_size = 0;
        static std::size_t Find(const std::pmr::vector<Slot>& slots, std::uint32_t key)
        {
            const auto mask = slots.size() - 1;
            auto at = static_cast<std::size_t>((std::uint64_t(key) * 11400714819323198485ULL) >> 32) & mask;
            while (slots[at].key && slots[at].key != key) at = (at + 1) & mask;
            return at;
        }
        void Grow()
        {
            if (m_slots.size() > m_slots.max_size() / 2)
                throw std::length_error("Posting state capacity overflow");
            std::pmr::vector<Slot> next(m_slots.get_allocator());
            next.resize(m_slots.empty() ? 16 : m_slots.size() * 2);
            for (const auto& slot : m_slots)
                if (slot.key) next[Find(next, slot.key)] = slot;
            m_slots.swap(next);
        }
    public:
        explicit StateTable(std::pmr::memory_resource* resource) : m_slots(resource) {}
        std::pair<State*, bool> Insert(int id)
        {
            const auto key = static_cast<std::uint32_t>(id) + 1;
            if (m_slots.empty()) Grow();
            auto at = Find(m_slots, key);
            if (m_slots[at].key) return {&m_slots[at].state, false};
            if (m_size >= m_slots.size() / 2) {
                Grow();
                at = Find(m_slots, key);
            }
            m_slots[at].key = key;
            ++m_size;
            return {&m_slots[at].state, true};
        }
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
    std::pmr::monotonic_buffer_resource m_stateMemory;
    std::vector<StateTable> m_state;
    std::vector<Candidate> m_discovered;
    COMMON::DistPriorityQueue m_distancePool;
    std::vector<COMMON::DistPriorityQueue> m_navigationPools;
    int m_navigationWidth = 0;
    int m_searchCapacity = 0;
    bool m_converged = false;
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    COMMON::GraphAccessStats::PostingReferences& References(std::size_t level, int id) const
    {
        return Layout::IsH2(m_postings, level, id) ? COMMON::g_graphAccessStats->m_h2Postings :
            COMMON::g_graphAccessStats->m_upperPostings;
    }
#endif
    State& GetState(std::size_t level, int id)
    {
        if (id < 0 || id >= m_owners.Count(level))
            throw std::out_of_range("Posting state ID");
        auto inserted = m_state[level].Insert(id);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (inserted.second && COMMON::g_graphAccessStats) {
            ++COMMON::g_graphAccessStats->m_postingStates;
            COMMON::g_graphAccessStats->m_stateInitializedBytes += sizeof(State);
            ++References(level, id).uniqueStates;
        }
#endif
        return *inserted.first;
    }
    void PrefetchUpper(std::size_t level, std::uint32_t id)
    {
        if constexpr (!std::is_same<Prefetch, NoPostingPrefetch>::value) {
            if (id < static_cast<std::uint32_t>(m_owners.Count(level)))
                m_prefetch(level + 1, id);
        }
    }
    auto Parents(std::size_t level, int id)
    {
        const auto parents = m_owners.Parents(level, id);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (COMMON::g_graphAccessStats) {
            COMMON::g_graphAccessStats->m_ownerReferences += parents.end() - parents.begin();
            for (int parent : parents) ++References(level, parent).ownerReferences;
        }
#endif
        return parents;
    }
    void DiscoverOwners(std::size_t level, int id, State& state)
    {
        if (state.ownersDiscovered) return;
        state.ownersDiscovered = true;
        if (!m_parentMayMatch(level, id)) return;
        Layout::VisitParents(m_owners, level, id, [&](std::size_t parentLevel, int parent) {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            if (COMMON::g_graphAccessStats) {
                ++COMMON::g_graphAccessStats->m_ownerReferences;
                ++References(parentLevel, parent).ownerReferences;
            }
#endif
            Discover(parentLevel, parent, DiscoveryPath::EntryOrOwner);
        });
    }
    bool Discover(std::size_t level, int id, DiscoveryPath path)
    {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (COMMON::g_graphAccessStats)
            ++References(level, id).candidateConsiderations;
#endif
        if constexpr (!std::is_same<ChildFilter, NoPostingChildFilter>::value) {
            if (path == DiscoveryPath::Child) {
                if (id < 0 || id >= m_owners.Count(level))
                    throw std::out_of_range("Posting state ID");
                // Exact child exclusion needs no state; entry/owner rejection must still ascend.
                if (!m_childMayMatch(level, id)) return false;
            }
        }
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
            ++References(level, id).representativeDistances;
        }
#endif
        const float distance = m_distance(level, id);
        if (!std::isfinite(distance))
            throw std::runtime_error("Invalid posting representative distance");
        if (Layout::IsLeaf(m_postings, level, id)) m_distancePool.insert(distance);
        else if (m_navigationWidth)
            m_navigationPools.at(Layout::NavigationLevel(m_postings, level, id)).insert(distance);
        m_discovered.push_back({distance, id, level});
        std::push_heap(m_discovered.begin(), m_discovered.end(), std::greater<Candidate>());
        return true;
    }
    void ExpandRow(std::size_t level, int selected, const Consumer& consume, Collection& collection)
    {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (COMMON::g_graphAccessStats)
            ++References(level, selected).expandAttempts;
#endif
        const auto state = GetState(level, selected);
        if (state.expanded) {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            if (COMMON::g_graphAccessStats)
                ++References(level, selected).expandedSkips;
#endif
            return;
        }
        if (state.signature != 1)
            throw std::logic_error("Cannot expand a signature-rejected posting");
        const auto* begin = m_postings[level].Begin(selected);
        const auto* end = m_postings[level].End(selected);
        if constexpr (!std::is_same<RowSelection, NoPostingRowSelection>::value)
            std::tie(begin, end) = m_selectRow(level, selected);
        const auto degree = begin == end ? 0 : end - begin;
        RowResult row;
        if (Layout::IsLeaf(m_postings, level, selected)) {
            for (auto member = begin; member != end; ++member) m_prefetch(0, *member);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            if (COMMON::g_graphAccessStats) {
                ++COMMON::g_graphAccessStats->m_selectedPostingRows;
                COMMON::g_graphAccessStats->m_auxiliaryMembers += degree;
            }
#endif
            row = consume(begin, static_cast<int>(degree));
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            if (COMMON::g_graphAccessStats && Layout::IsH2(m_postings, level, selected)) {
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
            const auto childLevel = Layout::ChildLevel(level);
            constexpr std::ptrdiff_t lookahead = 16;
            for (std::ptrdiff_t i = 0; i < (std::min)(degree, lookahead); ++i)
                PrefetchUpper(childLevel, begin[i]);
            for (auto member = begin; member != end; ++member) {
                if (end - member > lookahead)
                    PrefetchUpper(childLevel, member[lookahead]);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
                if (COMMON::g_graphAccessStats) {
                    ++COMMON::g_graphAccessStats->m_upperMembers;
                    ++References(childLevel, *member).memberReferences;
                }
#endif
                ++row.degree;
                row.eligible += Discover(childLevel, *member, DiscoveryPath::Child);
            }
        }
        if (row.degree != static_cast<std::size_t>(degree) || row.eligible > row.degree)
            throw std::logic_error("Posting consumer must report the complete selected row");
        // Child discovery may grow a state table; do not retain slot references across it.
        GetState(level, selected).expanded = true;
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        if (COMMON::g_graphAccessStats)
            ++References(level, selected).completedRows;
#endif
    }
public:
    HierarchyPostingQuery(const Owners& owners, const Postings& postings,
                          Signature signature, Distance distance, Prefetch prefetch = {},
                          int navigationWidth = 0, ChildFilter childMayMatch = {}, RowSelection selectRow = {},
                          ParentFilter parentMayMatch = {})
        : m_owners(owners), m_postings(postings), m_signature(signature), m_distance(distance),
          m_prefetch(prefetch), m_childMayMatch(childMayMatch), m_selectRow(selectRow),
          m_parentMayMatch(parentMayMatch),
          m_navigationWidth(navigationWidth)
    {
        if (navigationWidth < 0 || navigationWidth == MaxSize)
            throw std::invalid_argument("Posting navigation width must be nonnegative and below the native ID limit");
    }
    void SetSearchCapacity(int capacity) override
    {
        COMMON::PostingNavigation::SetSearchCapacity(capacity);
        if (!m_state.empty() && capacity != m_searchCapacity)
            throw std::logic_error("Cannot change an active posting search capacity");
        m_searchCapacity = capacity;
    }
    bool Converged() const override { return m_converged; }
    bool PreferResultAnchors() const override { return Layout::PreferResultAnchors(m_postings); }
    using COMMON::PostingNavigation::Expand;
    void Expand(const std::vector<int>& heads, const Consumer& consume) override
    {
        if (m_searchCapacity <= 0)
            throw std::logic_error("Posting search capacity must be configured before expansion");
        if (m_state.empty()) {
            m_state.reserve(m_owners.Levels());
            for (std::size_t level = 0; level < m_owners.Levels(); ++level)
                m_state.emplace_back(&m_stateMemory);
            m_distancePool.Resize(m_searchCapacity);
            if (m_navigationWidth) {
                m_navigationPools = std::vector<COMMON::DistPriorityQueue>(Layout::NavigationLevels(m_owners));
                for (auto& pool : m_navigationPools) pool.Resize(m_navigationWidth);
            }
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
        constexpr std::size_t lookahead = 16;
        for (std::size_t i = 0; i < (std::min)(owners.size(), lookahead); ++i)
            PrefetchUpper(0, owners[i]);
        for (std::size_t i = 0; i < owners.size(); ++i) {
            if (owners.size() - i > lookahead) PrefetchUpper(0, owners[i + lookahead]);
            Discover(0, owners[i], DiscoveryPath::EntryOrOwner);
        }
        bool navigationPruned = false;
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
            if (m_navigationWidth && !Layout::IsLeaf(m_postings, selected.level, selected.id) &&
                selected.distance > m_navigationPools.at(
                    Layout::NavigationLevel(m_postings, selected.level, selected.id)).worst()) {
                navigationPruned = true;
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
                if (COMMON::g_graphAccessStats) ++COMMON::g_graphAccessStats->m_navigationDistancePrunes;
#endif
                // Other tiers have independent bounds; do not stop their shared frontier.
                continue;
            }
            auto& state = GetState(selected.level, selected.id);
            DiscoverOwners(selected.level, selected.id, state);
            ExpandRow(selected.level, selected.id, consume, collection);
        }
        if (navigationPruned && collection.canContinue && m_discovered.empty()) m_converged = true;
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
