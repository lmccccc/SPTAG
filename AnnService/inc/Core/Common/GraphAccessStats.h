// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_COMMON_GRAPHACCESSSTATS_H_
#define _SPTAG_COMMON_GRAPHACCESSSTATS_H_

#include <cstdint>
#include <vector>

namespace SPTAG
{
namespace COMMON
{
struct GraphAccessStats
{
    std::uint64_t m_distanceCalls = 0;
    std::uint64_t m_graphRows = 0;
    std::uint64_t m_visitedChecks = 0;
    std::uint64_t m_treeNodeVisits = 0;
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    std::uint64_t m_upperDistances = 0;
    std::uint64_t m_signatureChecks = 0;
    std::uint64_t m_ownerReferences = 0;
    std::uint64_t m_upperMembers = 0;
    std::uint64_t m_auxiliaryMembers = 0;
    std::uint64_t m_predicateCalls = 0;
    std::uint64_t m_postingStates = 0;
    std::uint64_t m_stateInitializedBytes = 0;
    std::uint64_t m_selectedPostingRows = 0;
    std::uint64_t m_discoveredPops = 0;
    std::uint64_t m_cppAllocations = 0;
    std::uint64_t m_cppAllocationBytes = 0;
    std::uint64_t m_nativeHashClearedBytes = 0;
    std::uint64_t m_postingActivations = 0;
    std::uint64_t m_postingNewCandidates = 0;
    std::uint64_t m_postingTargetMet = 0;
    std::uint64_t m_postingUnderfilled = 0;
    std::uint64_t m_postingBudgetUnderfilled = 0;
    std::uint64_t m_auxiliaryFirstVisits = 0;
    std::uint64_t m_auxiliaryNegativeFirstVisits = 0;
    std::uint64_t m_auxiliaryVisitedSkips = 0;
    // Fresh post-graph rejections cached as visited without a distance evaluation.
    std::uint64_t m_auxiliaryUnvisitedNegativeSkips = 0;
    std::uint64_t m_selectedH2ZeroEligibleRows = 0;
    std::uint64_t m_selectedH2ZeroFreshRows = 0;
    std::uint64_t m_auxiliaryPrefetchLines = 0;
    struct PostingReferences {
        std::uint64_t ownerReferences = 0;
        std::uint64_t memberReferences = 0;
        std::uint64_t candidateConsiderations = 0;
        std::uint64_t uniqueStates = 0;
        std::uint64_t expandAttempts = 0;
        std::uint64_t expandedSkips = 0;
        std::uint64_t completedRows = 0;
        std::uint64_t representativeDistances = 0;
    };
    PostingReferences m_h2Postings, m_upperPostings;
    std::uint64_t m_headBefore = 0, m_headAfter = 0, m_headTarget = 0;
    std::uint64_t m_graphUnique = 0, m_graphMatches = 0, m_anchorCount = 0;
    std::uint64_t m_supplementReason = 0, m_graphLeaves = 0, m_supplementLeaves = 0;
    std::uint64_t m_graphDistances = 0, m_supplementDistances = 0, m_preservedHeads = 0;
    std::vector<std::int32_t> m_graphHeadIds;
    std::vector<float> m_graphHeadDistances;
#endif
};

inline thread_local GraphAccessStats* g_graphAccessStats = nullptr;

class ScopedGraphAccessStats
{
public:
    explicit ScopedGraphAccessStats(GraphAccessStats* stats)
        : m_previous(g_graphAccessStats)
    {
        g_graphAccessStats = stats;
    }

    ~ScopedGraphAccessStats() { g_graphAccessStats = m_previous; }
    ScopedGraphAccessStats(const ScopedGraphAccessStats&) = delete;
    ScopedGraphAccessStats& operator=(const ScopedGraphAccessStats&) = delete;

private:
    GraphAccessStats* m_previous;
};
} // namespace COMMON
} // namespace SPTAG

#endif
