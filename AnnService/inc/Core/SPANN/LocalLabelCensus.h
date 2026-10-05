// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/SPANN/LocalLabelAdmission.h"
#include "inc/Core/SPANN/PostingNavigation.h"
#include "inc/Core/SPANN/Options.h"
#include "inc/Core/Common/BKTree.h"
#include <chrono>
#include <numeric>

namespace SPTAG { namespace SPANN {

template<class T>
LocalLabelAdmission BuildLocalLabelCensus(const LimitedTagSupport& support,
    const PostingOwners& owners, const VectorSet& coarse, const Options& options)
{
    const auto begin = std::chrono::steady_clock::now();
    const auto require = LocalLabelAdmission::Require;
    require(owners.Levels() == 4 && owners.HeadCount() == support.HeadCount() &&
        coarse.Count() == owners.Count(3) && options.m_hierarchyLocalTarget > 0 &&
        options.m_hierarchyLocalWindow > 0 && options.m_ratio > 0 && options.m_ratio < 1,
        "Local census requires a complete adjacent spatial source and positive native policy settings");
    struct Pair { std::uint32_t label, count; };
    struct Summary {
        std::vector<std::uint32_t> mass;
        std::vector<std::uint64_t> offsets;
        std::vector<Pair> pairs;
    } previous;
    LocalLabelAdmission result;
    std::uint64_t totalSupports = 0, transfers = 0, additions = 0, peakSummaryBytes = 0;
    for (const auto& entry : support.TagHeads()) {
        result.labels.push_back(entry.first);
        totalSupports += entry.second.size();
    }
    std::sort(result.labels.begin(), result.labels.end());
    require(!result.labels.empty(), "Local census has no supported labels");
    const std::size_t width = result.labels.size();
    std::vector<std::uint64_t> expected(width);
    for (std::size_t label = 0; label < width; ++label)
        expected[label] = support.TagHeads().at(result.labels[label]).size();
    std::vector<std::vector<std::uint32_t>> projection;
    for (std::size_t level = 0; level < owners.Levels(); ++level) {
        const int lower = level ? owners.Count(level - 1) : owners.HeadCount();
        const int upper = owners.Count(level);
        std::vector<std::uint32_t> primary(lower);
        std::vector<std::uint64_t> offsets(std::size_t(upper) + 1, 0);
        for (int child = 0; child < lower; ++child) {
            const auto parents = owners.Parents(level, child);
            require(parents.begin() != parents.end(), "Missing spatial statistics owner");
            // Canonical owner order is deterministic; this is not a nearest-owner claim.
            primary[child] = *parents.begin();
            require(primary[child] < static_cast<unsigned>(upper), "Invalid statistics owner");
            ++offsets[primary[child] + 1];
        }
        for (int parent = 0; parent < upper; ++parent) offsets[parent + 1] += offsets[parent];
        auto cursors = offsets;
        std::vector<std::uint32_t> children(lower);
        for (int child = 0; child < lower; ++child) children[cursors[primary[child]]++] = child;
        std::vector<std::uint64_t>().swap(cursors);
        Summary next;
        next.mass.resize(upper);
        next.offsets.reserve(std::size_t(upper) + 1);
        next.offsets.push_back(0);
        next.pairs.reserve(std::min<std::uint64_t>(totalSupports, std::uint64_t(upper) * width));
        std::vector<std::uint32_t> histogram(width), touched;
        std::vector<std::uint64_t> observed(width);
        std::uint64_t totalMass = 0;
        for (int parent = 0; parent < upper; ++parent) {
            std::uint64_t mass = 0;
            const auto add = [&](std::uint32_t label, std::uint32_t count) {
                require(count && std::uint64_t(histogram[label]) + count <= unsigned(support.HeadCount()),
                    "Local census count overflow");
                if (!histogram[label]) touched.push_back(label);
                histogram[label] += count;
                ++additions;
            };
            for (auto at = offsets[parent]; at < offsets[parent + 1]; ++at) {
                const auto child = children[at];
                ++transfers;
                if (!level) {
                    ++mass;
                    for (auto tag : support.HeadTags(child)) {
                        const auto found = std::lower_bound(result.labels.begin(), result.labels.end(), tag);
                        require(found != result.labels.end() && *found == tag, "Unknown H1 support in census");
                        add(found - result.labels.begin(), 1);
                    }
                } else {
                    mass += previous.mass[child];
                    for (auto i = previous.offsets[child]; i < previous.offsets[child + 1]; ++i)
                        add(previous.pairs[i].label, previous.pairs[i].count);
                }
            }
            require(mass <= unsigned(support.HeadCount()), "Local census mass overflow");
            next.mass[parent] = mass;
            totalMass += mass;
            std::sort(touched.begin(), touched.end());
            for (auto label : touched) {
                next.pairs.push_back({label, histogram[label]});
                observed[label] += histogram[label];
                histogram[label] = 0;
            }
            touched.clear();
            next.offsets.push_back(next.pairs.size());
        }
        require(totalMass == unsigned(support.HeadCount()) && observed == expected,
            "Adjacent local census lost or duplicated H1 mass or label support");
        const auto bytes = [](const Summary& value) {
            return value.pairs.capacity() * sizeof(Pair) + value.offsets.capacity() * 8 +
                value.mass.capacity() * 4;
        };
        peakSummaryBytes = std::max<std::uint64_t>(peakSummaryBytes, bytes(previous) + bytes(next));
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info,
            "Local census H%zu: children=%d cells=%d entries=%zu mass=%llu (adjacent metadata only).\n",
            level + 2, lower, upper, next.pairs.size(), static_cast<unsigned long long>(totalMass));
        previous = std::move(next);
        if (!level) result.home = std::move(primary);
        else projection.push_back(std::move(primary));
    }
    for (int level = static_cast<int>(projection.size()) - 2; level >= 0; --level)
        for (auto& parent : projection[level]) parent = projection[level + 1][parent];
    for (auto& parent : result.home) parent = projection.front()[parent];
    std::vector<std::vector<std::uint32_t>>().swap(projection);

    // Only the source H5 representatives organize larger statistical windows.
    // No H1 neighborhood search, per-label ANN, or recursive vector expansion occurs.
    COMMON::Dataset<T> data(coarse.Count(), coarse.Dimension(), 1024, coarse.Count());
    for (int cell = 0; cell < coarse.Count(); ++cell)
        std::memcpy(data[cell], coarse.GetVector(cell), sizeof(T) * coarse.Dimension());
    COMMON::BKTree tree;
    tree.m_iBKTKmeansK = 2;
    tree.m_iBKTLeafSize = options.m_iBKTLeafSize;
    tree.m_iSamples = options.m_iSamples;
    tree.m_fBalanceFactor = options.m_fBalanceFactor;
    tree.BuildTrees<T>(data, options.m_distCalcMethod, options.m_iSelectHeadNumberOfThreads,
        nullptr, nullptr, true);
    std::vector<int> parent(tree.size(), -1), cellNode(coarse.Count(), -1);
    std::vector<std::uint32_t> mass(tree.size(), 0);
    require(std::size_t(tree.size()) <= std::vector<std::uint32_t>().max_size() / width,
        "Coarse census matrix exceeds native capacity");
    std::vector<std::uint32_t> counts(std::size_t(tree.size()) * width, 0);
    for (int node = 0; node < tree.size(); ++node) {
        const auto& value = tree[node];
        if (value.centerid >= 0 && value.centerid < coarse.Count()) {
            const int cell = value.centerid;
            require(cellNode[cell] < 0, "Duplicate coarse statistical center");
            cellNode[cell] = node;
            mass[node] = previous.mass[cell];
            for (auto at = previous.offsets[cell]; at < previous.offsets[cell + 1]; ++at)
                counts[std::size_t(node) * width + previous.pairs[at].label] = previous.pairs[at].count;
        }
        if (value.childStart == -1 && value.childEnd == -1) continue;
        const int first = std::abs(value.childStart), last = value.childEnd;
        require(first > node && last >= first && last <= tree.size(), "Malformed coarse statistics tree");
        for (int child = first; child < last; ++child) {
            require(parent[child] == -1, "Duplicate coarse statistics parent");
            parent[child] = node;
        }
    }
    require(std::find(cellNode.begin(), cellNode.end(), -1) == cellNode.end(),
        "Coarse statistics tree omitted a source cell");
    require(tree.size() >= 2 && tree[tree.size() - 1].centerid == -1 &&
        tree[tree.size() - 1].childStart == -1 && tree[tree.size() - 1].childEnd == -1,
        "Missing native BKT end sentinel");
    for (int node = tree.size() - 2; node > 0; --node) {
        require(parent[node] >= 0, "Disconnected coarse statistics tree");
        const int owner = parent[node];
        require(std::uint64_t(mass[owner]) + mass[node] <= unsigned(support.HeadCount()),
            "Coarse census mass overflow");
        mass[owner] += mass[node];
        for (std::size_t label = 0; label < width; ++label) {
            auto& target = counts[std::size_t(owner) * width + label];
            const auto value = counts[std::size_t(node) * width + label];
            require(std::uint64_t(target) + value <= unsigned(support.HeadCount()), "Coarse label overflow");
            target += value;
        }
    }
    require(mass[0] == unsigned(support.HeadCount()), "Coarse census does not cover the source");
    result.windows.resize(coarse.Count());
    result.masks.resize(std::size_t(coarse.Count()) * width, 0);
    for (int cell = 0; cell < coarse.Count(); ++cell) {
        int node = cellNode[cell];
        long double desired = options.m_hierarchyLocalWindow;
        for (int tier = 0; tier < 4; ++tier) {
            const auto target = static_cast<std::uint64_t>(
                std::min<long double>(support.HeadCount(), std::ceil(desired)));
            while (mass[node] < target && parent[node] >= 0) node = parent[node];
            result.windows[cell][tier] = mass[node];
            for (std::size_t label = 0; label < width; ++label) {
                auto& mask = result.masks[std::size_t(cell) * width + label];
                // An oversized statistics cell estimates density, not extra search capacity.
                if ((!tier || (mask & (1U << (tier - 1)))) &&
                    std::uint64_t(counts[std::size_t(node) * width + label]) * target <
                        std::uint64_t(options.m_hierarchyLocalTarget) * mass[node])
                    mask |= 1U << tier;
            }
            desired = std::min<long double>(support.HeadCount(), desired / options.m_ratio);
        }
    }
    result.Validate(support.HeadCount());
    SPTAGLIB_LOG(Helper::LogLevel::LL_Info,
        "Local census complete: primaryTransfers=%llu histogramAdditions=%llu peakSummaryBytes=%llu "
        "coarseVectors=%d extraWholeH1VectorPasses=0 perLabelANN=0 target=%d baseWindow=%d seconds=%.3f.\n",
        static_cast<unsigned long long>(transfers), static_cast<unsigned long long>(additions),
        static_cast<unsigned long long>(peakSummaryBytes), coarse.Count(),
        options.m_hierarchyLocalTarget, options.m_hierarchyLocalWindow,
        std::chrono::duration<double>(std::chrono::steady_clock::now() - begin).count());
    return result;
}
}}
