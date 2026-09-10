// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_SPANN_SECONDLEVELHIERARCHY_H_
#define _SPTAG_SPANN_SECONDLEVELHIERARCHY_H_

#include "inc/Core/Common/QueryResultSet.h"
#include "inc/Core/Common/VisitedBitmap.h"
#include "inc/Core/SPANN/LimitedTagSupport.h"
#include "inc/Core/SPANN/SecondLevelHeadPostings.h"
#include "inc/Core/VectorIndex.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <functional>
#include <limits>
#include <new>
#include <string>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#if defined(_MSC_VER)
#include <xmmintrin.h>
#endif

namespace SPTAG
{
namespace SPANN
{

inline Cache::PostingBitmask BuildHierarchyQuerySignature(
    const std::vector<std::uint32_t>& p_anchors,
    const LimitedTagSupport& p_support,
    const std::vector<SecondLevelHeadPostings>& p_postings)
{
    Cache::PostingBitmask signature;
    signature.Clear();
    for (std::uint32_t tag : p_anchors)
    {
        for (const auto& layer : p_postings)
        {
            // Legacy CSR domains are authenticated metadata, not routing
            // thresholds. One unrepresented OR anchor disables all signature
            // pruning, while exact H1/posting admission and navigation remain.
            if (!p_support.TagSelectivityInRange(
                    tag, layer.SignatureMinSelectivity(), layer.SignatureMaxSelectivity()))
            {
                signature.Clear();
                return signature;
            }
        }
        signature.Insert(tag);
    }
    return signature;
}

struct SecondLevelHierarchyLayerTimes
{
    double m_graphMs = 0.0;
    double m_mergeMs = 0.0;
    double m_tagMs = 0.0;
    double m_vectorMs = 0.0;
    double m_sortMs = 0.0;
};

struct SecondLevelHierarchySearchStats
{
    double m_graphMs = 0.0;
    double m_mergeMs = 0.0;
    double m_tagMs = 0.0;
    double m_vectorMs = 0.0;
    double m_sortMs = 0.0;
    std::uint64_t m_graphScanned = 0;
    std::uint64_t m_graphSignatureChecks = 0;
    std::uint64_t m_graphSignatureRejects = 0;
    std::uint64_t m_uniqueScanned = 0;
    std::uint64_t m_assignments = 0;
    int m_topProbe = 0;
    int m_maxCheck = 0;
    int m_iterations = 0;
    std::vector<std::uint64_t> m_layerBudgets;
    std::vector<std::uint64_t> m_layerCandidates;
    std::vector<std::uint64_t> m_layerAssignments;
    std::vector<std::uint64_t> m_layerEligible;
    std::vector<std::uint64_t> m_layerRetained;
    std::vector<std::uint64_t> m_layerRetainedEligible;
    std::vector<std::uint64_t> m_layerDistances;
    std::vector<SecondLevelHierarchyLayerTimes> m_layerTimes;
};

namespace SecondLevelHierarchyDetail
{
inline void PrefetchL1(const void* p_address)
{
    if (p_address == nullptr) return;
#if defined(_MSC_VER)
    _mm_prefetch(reinterpret_cast<const char*>(p_address), _MM_HINT_T0);
#elif defined(__GNUC__) || defined(__clang__)
    __builtin_prefetch(p_address, 0, 3);
#endif
}

struct HierarchyLayerWorkspace
{
    using Candidate = std::pair<float, SizeType>;
    COMMON::VisitedBitmap m_seen;
    std::vector<Candidate> m_frontier;
    std::vector<SizeType> m_selected;
    size_t m_nextParent = 0;

    void Reset(SizeType count)
    {
        m_seen.ResetSeen(static_cast<size_t>(count));
        m_frontier.clear();
        m_selected.clear();
        m_nextParent = 0;
    }

    void Push(float distance, SizeType id)
    {
        m_frontier.emplace_back(distance, id);
    }
};

struct SearchWorkspace
{
    std::vector<SizeType> m_children;
    std::vector<std::pair<float, SizeType>> m_headNearest;
    std::vector<std::pair<float, SizeType>> m_headPointResults;
    std::vector<HierarchyLayerWorkspace> m_layers;
};

inline void RetainNearest(
    std::vector<std::pair<float, SizeType>>& p_heap,
    const std::pair<float, SizeType>& p_candidate)
{
    if (p_heap.empty() || !(p_candidate.first < MaxDist) ||
        !(p_candidate < p_heap.front())) return;
    size_t parent = 0;
    size_t child = 1;
    while (child < p_heap.size())
    {
        if (child + 1 < p_heap.size() && p_heap[child] < p_heap[child + 1])
            ++child;
        if (!(p_candidate < p_heap[child])) break;
        p_heap[parent] = p_heap[child];
        parent = child;
        child = parent * 2 + 1;
    }
    p_heap[parent] = p_candidate;
}
} // namespace SecondLevelHierarchyDetail

template <typename T>
ErrorCode SearchSecondLevelHierarchyForPlacement(
    const T* p_target,
    int p_resultCount,
    const std::function<bool(SizeType)>& p_filter,
    COMMON::QueryResultSet<T>& p_results,
    const std::shared_ptr<VectorIndex>& p_distanceIndex,
    const std::vector<std::shared_ptr<VectorIndex>>& p_indexes,
    const std::vector<std::shared_ptr<VectorSet>>& p_catalogs,
    const std::vector<std::vector<std::uint64_t>>& p_offsets,
    const std::vector<std::vector<SecondLevelHeadPostings::Member>>& p_members)
{
    const size_t levels = p_offsets.size();
    if (p_target == nullptr || p_resultCount <= 0 ||
        p_resultCount > p_results.GetResultNum() || p_distanceIndex == nullptr ||
        levels == 0 || p_members.size() != levels || p_indexes.size() != levels ||
        p_catalogs.size() != levels || p_indexes.back() == nullptr)
        return ErrorCode::Fail;
    const SizeType topCount = p_indexes.back()->GetNumSamples();
    if (topCount <= 0) return ErrorCode::Fail;

    const int placementBeam = (std::max)(p_resultCount, 64);
    COMMON::QueryResultSet<T> topResults(p_target, placementBeam);
    const ErrorCode status = p_indexes.back()->SearchIndex(topResults);
    if (status != ErrorCode::Success) return status;

    static thread_local SecondLevelHierarchyDetail::SearchWorkspace workspace;
    workspace.m_layers.resize(levels + 1);
    auto& topState = workspace.m_layers.back();
    topState.Reset(topCount);
    for (int rank = 0; rank < topResults.GetResultNum(); ++rank)
    {
        const BasicResult* top = topResults.GetResult(rank);
        if (top == nullptr || top->VID < 0) break;
        if (top->VID >= topCount) return ErrorCode::Fail;
        topState.m_selected.push_back(top->VID);
    }

    std::uint64_t expanded = 0;
    auto& children = workspace.m_children;
    for (size_t level = levels; level-- > 0;)
    {
        const auto& offsets = p_offsets[level];
        const auto& members = p_members[level];
        const VectorSet* catalog = level == 0 ? nullptr : p_catalogs[level - 1].get();
        if (level > 0 && catalog == nullptr) return ErrorCode::Fail;
        const SizeType lowerCount = level == 0 ? p_distanceIndex->GetNumSamples() : catalog->Count();
        if (lowerCount <= 0 || offsets.size() < 2 || offsets.front() != 0 ||
            offsets.back() != members.size())
            return ErrorCode::Fail;
        const size_t upperCount = offsets.size() - 1;
        auto& state = workspace.m_layers[level];
        state.Reset(lowerCount);
        children.clear();
        for (SizeType upper : workspace.m_layers[level + 1].m_selected)
        {
            if (upper < 0 || static_cast<size_t>(upper) >= upperCount)
                return ErrorCode::Fail;
            const std::uint64_t begin = offsets[static_cast<size_t>(upper)];
            const std::uint64_t end = offsets[static_cast<size_t>(upper) + 1];
            if (begin > end || end > members.size()) return ErrorCode::Fail;
            for (std::uint64_t position = begin; position < end; ++position)
            {
                ++expanded;
                const auto member = members[static_cast<size_t>(position)];
                if (member >= static_cast<std::uint64_t>(lowerCount)) return ErrorCode::Fail;
                const SizeType child = static_cast<SizeType>(member);
                if (!state.m_seen.CheckAndSet(static_cast<size_t>(child)))
                    children.push_back(child);
            }
        }

        if (level == 0)
        {
            for (SizeType head : children)
            {
                if (p_filter && !p_filter(head)) continue;
                const void* sample = p_distanceIndex->GetSample(head);
                if (sample == nullptr) return ErrorCode::Fail;
                p_results.AddPoint(head, p_distanceIndex->ComputeDistance(p_target, sample));
            }
            break;
        }

        // The beam bounds retained heads, not the ID-ordered CSR prefix.
        const int retainCount = static_cast<int>(
            (std::min)(children.size(), static_cast<size_t>(placementBeam)));
        COMMON::QueryResultSet<T> nearest(p_target, (std::max)(1, retainCount));
        for (SizeType child : children)
        {
            const void* sample = catalog->GetVector(child);
            if (sample == nullptr) return ErrorCode::Fail;
            nearest.AddPoint(child, p_distanceIndex->ComputeDistance(p_target, sample));
        }
        nearest.SortResult();
        for (int rank = 0; rank < retainCount; ++rank)
        {
            const BasicResult* child = nearest.GetResult(rank);
            if (child == nullptr || child->VID < 0) break;
            state.m_selected.push_back(child->VID);
        }
    }
    p_results.SortResult();
    const std::uint64_t scanned =
        static_cast<std::uint64_t>((std::max)(0, topResults.GetScanned())) + expanded;
    p_results.SetScanned(static_cast<int>((std::min)(
        scanned, static_cast<std::uint64_t>((std::numeric_limits<int>::max)()))));
    return ErrorCode::Success;
}

template <typename T>
ErrorCode BuildSecondLevelHierarchyAssignments(
    SizeType p_lowerCount,
    SizeType p_upperCount,
    const std::vector<SizeType>& p_lowerToUpper,
    const std::function<const void*(SizeType)>& p_lowerSample,
    const std::shared_ptr<VectorIndex>& p_upperIndex,
    int p_replicaCount,
    int p_candidateCount,
    int p_workerCount,
    float p_rngFactor,
    int& p_effectiveReplicas,
    std::uint64_t& p_assignmentCount,
    std::vector<std::uint64_t>& p_offsets,
    std::vector<SecondLevelHeadPostings::Member>& p_members)
{
    using Member = SecondLevelHeadPostings::Member;
    if (p_lowerCount <= 0 || p_upperCount <= 0 ||
        p_upperCount > (std::numeric_limits<int>::max)() ||
        p_replicaCount <= 0 || p_candidateCount <= 0 ||
        p_workerCount <= 0 || p_lowerSample == nullptr)
    {
        return ErrorCode::Fail;
    }
    p_effectiveReplicas = (std::min)(p_replicaCount, static_cast<int>(p_upperCount));
    p_assignmentCount =
        static_cast<std::uint64_t>(p_lowerCount) *
        static_cast<std::uint64_t>(p_effectiveReplicas);
    if (p_effectiveReplicas <= 0 ||
        p_upperIndex == nullptr ||
        p_lowerToUpper.size() != static_cast<size_t>(p_lowerCount) ||
        p_assignmentCount > static_cast<std::uint64_t>(p_members.max_size()))
    {
        return ErrorCode::MemoryOverFlow;
    }
    try
    {
        p_members.resize(static_cast<size_t>(p_assignmentCount));
    }
    catch (const std::bad_alloc&)
    {
        return ErrorCode::MemoryOverFlow;
    }

    std::atomic<std::int64_t> nextLower(0);
    std::atomic<bool> failed(false);
    std::atomic<bool> allocationFailed(false);
    const auto assignHeads = [&]() {
        std::vector<SizeType> selected;
        selected.reserve(static_cast<size_t>(p_effectiveReplicas));
        constexpr std::int64_t kAssignmentBatch = 256;
        while (!failed.load(std::memory_order_relaxed))
        {
            const std::int64_t begin =
                nextLower.fetch_add(kAssignmentBatch, std::memory_order_relaxed);
            if (begin >= p_lowerCount) return;
            const SizeType end = static_cast<SizeType>((std::min)(
                begin + kAssignmentBatch,
                static_cast<std::int64_t>(p_lowerCount)));
            for (SizeType lower = static_cast<SizeType>(begin);
                 lower < end; ++lower)
            {
                COMMON::QueryResultSet<T> query(
                    reinterpret_cast<const T*>(p_lowerSample(lower)),
                    p_candidateCount);
                if (query.GetTarget() == nullptr ||
                    p_upperIndex->SearchIndex(query) != ErrorCode::Success)
                {
                    failed.store(true, std::memory_order_relaxed);
                    return;
                }

                selected.clear();
                const SizeType self =
                    p_lowerToUpper[static_cast<size_t>(lower)];
                if (self >= 0) selected.push_back(self);
                for (int result = 0;
                     result < p_candidateCount &&
                     selected.size() < static_cast<size_t>(p_effectiveReplicas);
                     ++result)
                {
                    const BasicResult* candidate = query.GetResult(result);
                    if (candidate == nullptr ||
                        candidate->VID < 0 ||
                        candidate->VID >= p_upperCount ||
                        std::find(
                            selected.begin(), selected.end(),
                            candidate->VID) != selected.end())
                    {
                        continue;
                    }
                    bool accepted = true;
                    for (SizeType chosen : selected)
                    {
                        const float neighborDistance =
                            p_upperIndex->ComputeDistance(
                                p_upperIndex->GetSample(candidate->VID),
                                p_upperIndex->GetSample(chosen));
                        if (p_rngFactor * neighborDistance < candidate->Dist)
                        {
                            accepted = false;
                            break;
                        }
                    }
                    if (accepted) selected.push_back(candidate->VID);
                }
                for (int result = 0;
                     result < p_candidateCount &&
                     selected.size() < static_cast<size_t>(p_effectiveReplicas);
                     ++result)
                {
                    const BasicResult* candidate = query.GetResult(result);
                    if (candidate != nullptr &&
                        candidate->VID >= 0 &&
                        candidate->VID < p_upperCount &&
                        std::find(
                            selected.begin(), selected.end(),
                            candidate->VID) == selected.end())
                    {
                        selected.push_back(candidate->VID);
                    }
                }
                if (selected.size() != static_cast<size_t>(p_effectiveReplicas))
                {
                    failed.store(true, std::memory_order_relaxed);
                    return;
                }
                const size_t row =
                    static_cast<size_t>(lower) *
                    static_cast<size_t>(p_effectiveReplicas);
                for (int replica = 0; replica < p_effectiveReplicas; ++replica)
                {
                    p_members[row + static_cast<size_t>(replica)] =
                        static_cast<Member>(
                            selected[static_cast<size_t>(replica)]);
                }
            }
        }
    };

    std::vector<std::thread> workers;
    try
    {
        workers.reserve(static_cast<size_t>(p_workerCount));
        for (int worker = 0; worker < p_workerCount; ++worker)
        {
            workers.emplace_back([&]() {
                try
                {
                    assignHeads();
                }
                catch (const std::bad_alloc&)
                {
                    allocationFailed.store(true, std::memory_order_relaxed);
                    failed.store(true, std::memory_order_relaxed);
                }
            });
        }
    }
    catch (const std::bad_alloc&)
    {
        failed.store(true, std::memory_order_relaxed);
        for (auto& worker : workers)
            if (worker.joinable()) worker.join();
        return ErrorCode::MemoryOverFlow;
    }
    catch (const std::system_error&)
    {
        failed.store(true, std::memory_order_relaxed);
        for (auto& worker : workers)
            if (worker.joinable()) worker.join();
        return ErrorCode::MemoryOverFlow;
    }
    for (auto& worker : workers)
        if (worker.joinable()) worker.join();
    if (allocationFailed.load(std::memory_order_relaxed))
        return ErrorCode::MemoryOverFlow;
    if (failed.load(std::memory_order_relaxed))
        return ErrorCode::Fail;

    try
    {
        p_offsets.assign(
            static_cast<size_t>(p_upperCount) + 1, 0);
    }
    catch (const std::bad_alloc&)
    {
        return ErrorCode::MemoryOverFlow;
    }
    for (Member upper : p_members)
    {
        if (upper >= static_cast<Member>(p_upperCount))
            return ErrorCode::Fail;
        ++p_offsets[static_cast<size_t>(upper) + 1];
    }
    for (SizeType upper = 0; upper < p_upperCount; ++upper)
    {
        p_offsets[static_cast<size_t>(upper) + 1] +=
            p_offsets[static_cast<size_t>(upper)];
    }
    std::vector<std::uint64_t> cursors;
    try
    {
        cursors = p_offsets;
    }
    catch (const std::bad_alloc&)
    {
        return ErrorCode::MemoryOverFlow;
    }
    constexpr Member kFinalMember = static_cast<Member>(1U << 31);
    for (size_t source = 0; source < p_members.size(); ++source)
    {
        if ((p_members[source] & kFinalMember) != 0) continue;
        Member currentUpper = p_members[source];
        Member currentLower = static_cast<Member>(
            source / static_cast<size_t>(p_effectiveReplicas));
        size_t steps = 0;
        while (true)
        {
            if (currentUpper >= static_cast<Member>(p_upperCount) ||
                steps++ >= p_members.size())
                return ErrorCode::Fail;
            const size_t upper = static_cast<size_t>(currentUpper);
            if (cursors[upper] >= p_offsets[upper + 1])
                return ErrorCode::Fail;
            const size_t destination =
                static_cast<size_t>(cursors[upper]++);
            if (destination >= p_members.size() ||
                (p_members[destination] & kFinalMember) != 0)
                return ErrorCode::Fail;
            const Member displaced = p_members[destination];
            p_members[destination] = currentLower | kFinalMember;
            if (destination == source) break;
            currentUpper = displaced;
            currentLower = static_cast<Member>(
                destination / static_cast<size_t>(p_effectiveReplicas));
        }
    }
    for (Member& member : p_members) member &= ~kFinalMember;
    return ErrorCode::Success;
}

template <typename T>
ErrorCode SearchSecondLevelHierarchy(
    COMMON::QueryResultSet<T>& p_results,
    int p_resultBudget,
    int p_maxCheck,
    double p_initialProbeRatio,
    const Cache::PostingBitmask& p_querySignature,
    const std::function<bool(SizeType)>& p_headAdmission,
    const std::shared_ptr<VectorIndex>& p_distanceIndex,
    const std::vector<std::shared_ptr<VectorIndex>>& p_indexes,
    const std::vector<std::shared_ptr<VectorSet>>& p_catalogs,
    const std::vector<SecondLevelHeadPostings>& p_postings,
    SecondLevelHierarchySearchStats& p_stats,
    std::string* p_workLog = nullptr,
    bool p_profile = false,
    const LimitedTagSupport* p_headSupport = nullptr,
    // A null distance offers a nonrouting H1 point; true reports a new distance evaluation.
    const std::function<bool(SizeType, const float*)>& p_headPointCandidate = nullptr,
    SecondLevelHierarchyDetail::SearchWorkspace* p_workspace = nullptr,
    bool p_batchVectorPrefetch = false,
    bool p_graphSignaturePruning = false)
{
    const int levels = static_cast<int>(p_postings.size());
    if (p_resultBudget <= 0 || p_resultBudget > p_results.GetResultNum() || p_maxCheck <= 0 ||
        !std::isfinite(p_initialProbeRatio) ||
        p_initialProbeRatio <= 0.0 || p_initialProbeRatio > 1.0 ||
        !p_headAdmission || p_distanceIndex == nullptr ||
        levels <= 0 || p_indexes.size() != p_postings.size() ||
        p_catalogs.size() != p_postings.size() ||
        p_indexes.back() == nullptr)
    {
        return ErrorCode::Fail;
    }

    p_stats = SecondLevelHierarchySearchStats();
    p_stats.m_layerBudgets.assign(static_cast<size_t>(levels) + 1, 0);
    p_stats.m_layerCandidates.assign(static_cast<size_t>(levels) + 1, 0);
    p_stats.m_layerAssignments.assign(static_cast<size_t>(levels), 0);
    p_stats.m_layerEligible.assign(static_cast<size_t>(levels) + 1, 0);
    p_stats.m_layerRetained.assign(static_cast<size_t>(levels) + 1, 0);
    p_stats.m_layerRetainedEligible.assign(static_cast<size_t>(levels) + 1, 0);
    p_stats.m_layerDistances.assign(static_cast<size_t>(levels), 0);
    if (p_profile) p_stats.m_layerTimes.resize(static_cast<size_t>(levels) + 1);
    const auto now = [p_profile]() {
        return p_profile ? std::chrono::steady_clock::now()
                         : std::chrono::steady_clock::time_point{};
    };
    const auto elapsed = [&now](std::chrono::steady_clock::time_point p_start) {
        return std::chrono::duration<double, std::milli>(now() - p_start).count();
    };

    const auto& topPostings = p_postings.back();
    const int topCount = static_cast<int>(topPostings.SecondLevelHeadCount());
    if (topCount <= 0) return ErrorCode::Fail;
    const int fullTopProbe = (std::min)(topCount, (std::max)(1, p_resultBudget));
    int routingBudget = (std::max)(
        1, (std::min)(p_resultBudget, static_cast<int>(std::ceil(
            static_cast<long double>(p_resultBudget) * p_initialProbeRatio))));
    const bool hasSignature = p_querySignature.Popcount() > 0;
    const auto postingMatches =
        [&p_querySignature, hasSignature](
            const SecondLevelHeadPostings& p_layer,
            SizeType p_upper) {
            if (!hasSignature) return true;
            const auto* signature = p_layer.SignatureAt(p_upper);
            // Nonempty query signatures contain only represented anchors.
            return signature != nullptr && signature->MayIntersect(p_querySignature);
        };
    static thread_local SecondLevelHierarchyDetail::SearchWorkspace fallbackWorkspace;
    auto& workspace = p_workspace != nullptr ? *p_workspace : fallbackWorkspace;
    auto& children = workspace.m_children;
    auto& headNearest = workspace.m_headNearest;
    constexpr size_t kScanBatch = LimitedTagSupport::LookupBatchSize;
    const size_t vectorBytes =
        static_cast<size_t>(p_distanceIndex->GetFeatureDim()) * sizeof(T);
    const SizeType headCount = p_distanceIndex->GetNumSamples();
    if (headCount <= 0 || headCount != p_postings.front().FirstLevelHeadCount() ||
        p_indexes.back()->GetNumSamples() != topCount ||
        (p_headSupport != nullptr && p_headSupport->HeadCount() != headCount))
        return ErrorCode::Fail;
    const auto* headLookupData = p_headSupport == nullptr ? nullptr : p_headSupport->HeadLookupData(0);
    const size_t headLookupStride = p_headSupport == nullptr ? 0 : p_headSupport->HeadLookupStride();
    const bool filterHeadPoints = hasSignature && p_headPointCandidate && p_headSupport != nullptr;
    const auto* headAttributes = filterHeadPoints ? p_headSupport->HeadAttributes(0) : nullptr;
    const size_t headAttributeStride = filterHeadPoints ? p_headSupport->HeadAttributeStride() : 0;
    const int headKeyColumn = filterHeadPoints ? p_headSupport->KeyColumn() : 0;
    if (filterHeadPoints && (headAttributes == nullptr || headKeyColumn < 0 ||
        headKeyColumn >= p_headSupport->AttributeCount()))
        return ErrorCode::Fail;
    workspace.m_layers.resize(static_cast<size_t>(levels) + 1);
    for (int level = 0; level < levels; ++level)
    {
        const auto& postings = p_postings[static_cast<size_t>(level)];
        if (postings.FirstLevelHeadCount() <= 0 ||
            p_catalogs[static_cast<size_t>(level)] == nullptr ||
            p_catalogs[static_cast<size_t>(level)]->Count() != postings.SecondLevelHeadCount() ||
            (level > 0 && postings.FirstLevelHeadCount() !=
                p_postings[static_cast<size_t>(level - 1)].SecondLevelHeadCount()))
            return ErrorCode::Fail;
        workspace.m_layers[static_cast<size_t>(level)].Reset(postings.FirstLevelHeadCount());
    }
    auto& topState = workspace.m_layers[static_cast<size_t>(levels)];
    topState.Reset(topCount);
    headNearest.assign(static_cast<size_t>(p_resultBudget), {MaxDist, -1});
    std::uint64_t scoredHeads = 0;
    p_results.Reset();
    bool invalidSample = false;
    const auto observeTop = [&](SizeType id, float distance) {
        if (topState.m_seen.CheckAndSet(static_cast<size_t>(id))) return;
        ++p_stats.m_layerCandidates[static_cast<size_t>(levels)];
        if (distance < MaxDist && postingMatches(topPostings, id))
            topState.Push(distance, id);
    };

    // Matching top results, not an unfiltered top-k, consume the routing ceiling.
    // Native result admission continues the same graph/tree frontier when
    // underfilled; later CSR widening never restarts that search.
    COMMON::QueryResultSet<T> topResults(p_results.GetTarget(), fullTopProbe);
    p_stats.m_topProbe = fullTopProbe;
    p_stats.m_maxCheck = p_maxCheck;
    const auto graphStart = now();
    ErrorCode graphStatus;
    if (hasSignature)
    {
        const std::function<bool(SizeType)> graphAdmission = [&](SizeType id) {
            ++p_stats.m_graphSignatureChecks;
            if (id < 0 || id >= topCount)
            {
                invalidSample = true;
                return false;
            }
            const bool admitted = postingMatches(topPostings, id);
            if (!admitted) ++p_stats.m_graphSignatureRejects;
            return admitted;
        };
        graphStatus = p_graphSignaturePruning
            ? p_indexes.back()->SearchIndexWithTraversalFilter(
                  topResults, graphAdmission, p_maxCheck)
            : p_indexes.back()->SearchIndexWithResultFilter(
                  topResults, graphAdmission, p_maxCheck);
    }
    else
    {
        graphStatus = p_indexes.back()->SearchIndexWithMaxCheck(topResults, p_maxCheck);
    }
    if (graphStatus != ErrorCode::Success) return graphStatus;
    if (invalidSample) return ErrorCode::Fail;
    p_stats.m_graphMs = elapsed(graphStart);
    if (p_profile)
        p_stats.m_layerTimes[static_cast<size_t>(levels)].m_graphMs = p_stats.m_graphMs;
    p_stats.m_graphScanned =
        static_cast<std::uint64_t>((std::max)(0, topResults.GetScanned()));
    const auto topAdmissionStart = now();
    for (int rank = 0; rank < fullTopProbe; ++rank)
    {
        const auto* point = topResults.GetResult(rank);
        if (point == nullptr || point->VID < 0) continue;
        if (point->VID >= topCount) return ErrorCode::Fail;
        observeTop(point->VID, point->Dist);
    }
    p_stats.m_layerEligible[static_cast<size_t>(levels)] = topState.m_frontier.size();
    const double topTagMs = elapsed(topAdmissionStart);
    p_stats.m_tagMs += topTagMs;
    if (p_profile)
        p_stats.m_layerTimes[static_cast<size_t>(levels)].m_tagMs += topTagMs;

    const auto selectParents = [&](int level, int budget) {
        auto& state = workspace.m_layers[static_cast<size_t>(level)];
        auto& frontier = state.m_frontier;
        const size_t needed = static_cast<size_t>(budget) > state.m_selected.size()
            ? (std::min)(static_cast<size_t>(budget) - state.m_selected.size(), frontier.size()) : 0;
        if (needed > 0)
        {
            const auto selectedEnd = frontier.begin() + needed;
            std::partial_sort(frontier.begin(), selectedEnd, frontier.end());
            for (auto point = frontier.begin(); point != selectedEnd; ++point)
                state.m_selected.push_back(point->second);
            frontier.erase(frontier.begin(), selectedEnd);
        }
        p_stats.m_layerBudgets[static_cast<size_t>(level)] = budget;
        p_stats.m_layerRetained[static_cast<size_t>(level)] = state.m_selected.size();
        p_stats.m_layerRetainedEligible[static_cast<size_t>(level)] = state.m_selected.size();
    };

    while (true)
    {
        ++p_stats.m_iterations;
        selectParents(levels, (std::min)(topCount, routingBudget));
        for (int level = levels - 1; level >= 0; --level)
        {
            const auto& postings = p_postings[static_cast<size_t>(level)];
            auto& state = workspace.m_layers[static_cast<size_t>(level)];
            auto& parents = workspace.m_layers[static_cast<size_t>(level + 1)];
            const auto& selected = parents.m_selected;
            const SizeType lowerCount = postings.FirstLevelHeadCount();
            const VectorSet* catalog =
                level == 0 ? nullptr : p_catalogs[static_cast<size_t>(level - 1)].get();
            if (level > 0 && catalog == nullptr) return ErrorCode::Fail;
            const auto mergeStart = now();
            children.clear();
            std::uint64_t assignments = 0;
            constexpr size_t kRowLookahead = 4;
            for (size_t row = parents.m_nextParent; row < selected.size(); ++row)
            {
                if (row + kRowLookahead < selected.size())
                {
                    const SizeType ahead = selected[row + kRowLookahead];
                    SecondLevelHierarchyDetail::PrefetchL1(postings.Begin(ahead));
                    SecondLevelHierarchyDetail::PrefetchL1(postings.SignatureAt(ahead));
                }
                const SizeType upper = selected[row];
                if (upper < 0 || upper >= postings.SecondLevelHeadCount())
                    return ErrorCode::Fail;
                const auto* begin = postings.Begin(upper);
                const auto* end = postings.End(upper);
                if (begin == nullptr || end == nullptr || begin > end)
                    return ErrorCode::Fail;
                assignments += static_cast<std::uint64_t>(end - begin);
                for (const auto* child = begin; child != end; ++child)
                {
                    const SizeType candidate = static_cast<SizeType>(*child);
                    if (candidate < 0 || candidate >= lowerCount) return ErrorCode::Fail;
                    if (!state.m_seen.CheckAndSet(static_cast<size_t>(candidate)))
                        children.push_back(candidate);
                }
            }
            parents.m_nextParent = selected.size();
            p_stats.m_assignments += assignments;
            p_stats.m_layerAssignments[static_cast<size_t>(level)] += assignments;
            p_stats.m_layerCandidates[static_cast<size_t>(level)] += children.size();
            const double mergeMs = elapsed(mergeStart);
            p_stats.m_mergeMs += mergeMs;
            if (p_profile)
                p_stats.m_layerTimes[static_cast<size_t>(level)].m_mergeMs += mergeMs;

            const auto tagStart = now();
            if (level == 0 || hasSignature)
            {
                size_t admitted = 0;
                const bool prefetchExtraPayload =
                    p_headSupport != nullptr && p_headSupport->NeedsExtraLookupPrefetch();
                for (size_t batch = 0; batch < children.size(); batch += kScanBatch)
                {
                    const size_t end = (std::min)(children.size(), batch + kScanBatch);
                    for (size_t pos = batch; pos < end; ++pos)
                    {
                        if (level > 0)
                            SecondLevelHierarchyDetail::PrefetchL1(
                                p_postings[static_cast<size_t>(level - 1)].SignatureAt(children[pos]));
                        else if (headLookupData != nullptr)
                        {
                            const size_t head = static_cast<size_t>(children[pos]);
                            SecondLevelHierarchyDetail::PrefetchL1(headLookupData + head * headLookupStride);
                            if (headAttributes != nullptr)
                                SecondLevelHierarchyDetail::PrefetchL1(
                                    headAttributes + head * headAttributeStride);
                        }
                    }
                    if (level == 0 && prefetchExtraPayload)
                    {
                        std::array<SizeType, kScanBatch> extraTagHeads;
                        size_t extraTagHeadCount = 0;
                        for (size_t pos = batch; pos < end; ++pos)
                        {
                            if (const auto* offsets = p_headSupport->ExtraHeadLookupData(children[pos]))
                            {
                                SecondLevelHierarchyDetail::PrefetchL1(offsets);
                                SecondLevelHierarchyDetail::PrefetchL1(offsets + 1);
                                extraTagHeads[extraTagHeadCount++] = children[pos];
                            }
                        }
                        // Resolve the prefetched offsets before admitting this batch.
                        for (size_t row = 0; row < extraTagHeadCount; ++row)
                        {
                            const auto tags = p_headSupport->ExtraLookupTagRange(extraTagHeads[row]);
                            if (tags.first != nullptr)
                            {
                                SecondLevelHierarchyDetail::PrefetchL1(tags.first);
                                SecondLevelHierarchyDetail::PrefetchL1(tags.second - 1);
                            }
                        }
                    }
                    for (size_t pos = batch; pos < end; ++pos)
                    {
                        const SizeType child = children[pos];
                        if (level == 0 ? p_headAdmission(child)
                            : postingMatches(p_postings[static_cast<size_t>(level - 1)], child))
                            children[admitted++] = child;
                        else if (level == 0 && p_headPointCandidate)
                        {
                            if (headAttributes != nullptr)
                            {
                                const auto* attributes =
                                    headAttributes + static_cast<size_t>(child) * headAttributeStride;
                                // Local visited is already set. A coarse own-key
                                // rejection never evaluates the exact point predicate.
                                if (!p_querySignature.MayContain(attributes[headKeyColumn])) continue;
                            }
                            if (p_headPointCandidate(child, nullptr))
                            {
                                ++p_stats.m_layerDistances.front();
                                ++p_stats.m_uniqueScanned;
                            }
                        }
                    }
                }
                children.resize(admitted);
            }
            p_stats.m_layerEligible[static_cast<size_t>(level)] += children.size();
            const double tagMs = elapsed(tagStart);
            p_stats.m_tagMs += tagMs;
            if (p_profile)
                p_stats.m_layerTimes[static_cast<size_t>(level)].m_tagMs += tagMs;

            const auto vectorStart = now();
            const size_t prefetchAhead = p_batchVectorPrefetch ? 64 : 16;
            std::array<const void*, 64> samples{};
            const auto prefetchSample = [&](size_t pos) -> const void* {
                const SizeType child = children[pos];
                const void* sample = level == 0 ? p_distanceIndex->GetSample(child)
                                              : catalog->GetVector(child);
                if (sample == nullptr) { invalidSample = true; return nullptr; }
                const char* bytes = static_cast<const char*>(sample);
                const size_t prefetchBytes = p_batchVectorPrefetch
                    ? (std::min)(vectorBytes, size_t{128}) : vectorBytes;
                for (size_t offset = 0; offset < prefetchBytes; offset += 64)
                    SecondLevelHierarchyDetail::PrefetchL1(bytes + offset);
                if (!p_batchVectorPrefetch && vectorBytes > 0)
                    SecondLevelHierarchyDetail::PrefetchL1(bytes + vectorBytes - 1);
                return sample;
            };
            for (size_t pos = 0; pos < (std::min)(children.size(), prefetchAhead); ++pos)
                samples[pos] = prefetchSample(pos);
            for (size_t pos = 0; pos < children.size(); ++pos)
            {
                const size_t slot = pos & (prefetchAhead - 1);
                const void* sample = samples[slot];
                if (p_batchVectorPrefetch && slot == 0 && pos > 0)
                {
                    for (size_t batch = pos;
                         batch < (std::min)(children.size(), pos + prefetchAhead); ++batch)
                        samples[batch - pos] = prefetchSample(batch);
                    sample = samples[0];
                }
                else if (!p_batchVectorPrefetch && pos + prefetchAhead < children.size())
                    samples[slot] = prefetchSample(pos + prefetchAhead);
                if (invalidSample) return ErrorCode::Fail;
                const float distance = p_distanceIndex->ComputeDistance(
                    p_results.GetQuantizedTarget(), sample);
                ++p_stats.m_layerDistances[static_cast<size_t>(level)];
                ++p_stats.m_uniqueScanned;
                if (!(distance < MaxDist)) continue;
                if (level == 0)
                {
                    if (p_headPointCandidate) p_headPointCandidate(children[pos], &distance);
                    SecondLevelHierarchyDetail::RetainNearest(
                        headNearest, {distance, children[pos]});
                    ++scoredHeads;
                }
                else state.Push(distance, children[pos]);
            }
            const double vectorMs = elapsed(vectorStart);
            p_stats.m_vectorMs += vectorMs;
            if (p_profile)
                p_stats.m_layerTimes[static_cast<size_t>(level)].m_vectorMs += vectorMs;
            const auto sortStart = now();
            if (level == 0)
            {
                p_stats.m_layerBudgets.front() = p_resultBudget;
                p_stats.m_layerRetained.front() = (std::min)(
                    scoredHeads, static_cast<std::uint64_t>(p_resultBudget));
                p_stats.m_layerRetainedEligible.front() = p_stats.m_layerRetained.front();
            }
            else selectParents(level, (std::min)(lowerCount, routingBudget));
            const double sortMs = elapsed(sortStart);
            p_stats.m_sortMs += sortMs;
            if (p_profile)
                p_stats.m_layerTimes[static_cast<size_t>(level)].m_sortMs += sortMs;
        }
        if (p_stats.m_layerRetained.front() >= static_cast<std::uint64_t>(p_resultBudget) ||
            routingBudget >= p_resultBudget)
            break;
        routingBudget = p_resultBudget;
    }

    const auto sortStart = now();
    std::sort(headNearest.begin(), headNearest.end());
    for (int rank = 0; rank < p_resultBudget; ++rank)
    {
        const auto& head = headNearest[static_cast<size_t>(rank)];
        p_results.SetResult(rank, head.second, head.first);
    }
    const double finalSortMs = elapsed(sortStart);
    p_stats.m_sortMs += finalSortMs;
    if (p_profile) p_stats.m_layerTimes.front().m_sortMs += finalSortMs;
    if (p_workLog != nullptr)
    {
        *p_workLog = "HierarchyWork:";
        for (int level = levels; level >= 0; --level)
        {
            *p_workLog += " level=H" + std::to_string(level + 1) +
                ",budget=" +
                std::to_string(p_stats.m_layerBudgets[
                    static_cast<size_t>(level)]) +
                ",candidates=" +
                std::to_string(p_stats.m_layerCandidates[
                    static_cast<size_t>(level)]) +
                ",eligible=" +
                std::to_string(p_stats.m_layerEligible[static_cast<size_t>(level)]) +
                ",retained=" +
                std::to_string(p_stats.m_layerRetained[static_cast<size_t>(level)]) +
                ",retained_eligible=" +
                std::to_string(p_stats.m_layerRetainedEligible[static_cast<size_t>(level)]);
            if (level < levels)
            {
                *p_workLog += ",assignments=" +
                    std::to_string(p_stats.m_layerAssignments[
                        static_cast<size_t>(level)]) +
                    ",distances=" +
                    std::to_string(p_stats.m_layerDistances[static_cast<size_t>(level)]);
            }
            if (level == levels)
                *p_workLog += ",graph_checked=" +
                    std::to_string(p_stats.m_graphScanned) +
                    ",graph_sig_checks=" + std::to_string(p_stats.m_graphSignatureChecks) +
                    ",graph_sig_rejects=" + std::to_string(p_stats.m_graphSignatureRejects) +
                    ",graph_result_filter=" + std::to_string(hasSignature) +
                    ",graph_traversal_filter=" +
                    std::to_string(hasSignature && p_graphSignaturePruning);
            if (p_profile)
            {
                const auto& times = p_stats.m_layerTimes[static_cast<size_t>(level)];
                *p_workLog += ",graph_ms=" + std::to_string(times.m_graphMs) +
                    ",merge_ms=" + std::to_string(times.m_mergeMs) +
                    ",tag_ms=" + std::to_string(times.m_tagMs) +
                    ",vec_ms=" + std::to_string(times.m_vectorMs) +
                    ",sort_ms=" + std::to_string(times.m_sortMs);
            }
        }
    }
    return ErrorCode::Success;
}

} // namespace SPANN
} // namespace SPTAG

#endif // _SPTAG_SPANN_SECONDLEVELHIERARCHY_H_
