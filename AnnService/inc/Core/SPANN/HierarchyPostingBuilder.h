// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once
#include "inc/Core/Common/QueryResultSet.h"
#include "inc/Core/SPANN/SecondLevelHeadPostings.h"
#include "inc/Core/VectorIndex.h"
#include <atomic>
#include <thread>

namespace SPTAG { namespace SPANN {
template <typename T>
ErrorCode BuildHierarchyPostingAssignments(
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


}}
