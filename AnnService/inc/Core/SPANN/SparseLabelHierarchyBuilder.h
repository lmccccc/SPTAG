// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/SPANN/SparseLabelHierarchy.h"
#include "inc/Core/SPANN/CanonicalHierarchyVectors.h"
#include "inc/Core/SPANN/Options.h"
#include "inc/Core/SPANN/HierarchyPostingBuilder.h"
#include "inc/Core/Common/BKTree.h"
#include <numeric>
#include <queue>
#include <unordered_map>

namespace SPTAG { namespace SPANN {

inline std::vector<SizeType> SparseTerminalCounts(
    const std::vector<std::pair<int, SizeType>>& labels, double ratio,
    const std::array<std::uint32_t, 4>& caps, const std::array<std::uint32_t, 4>& navigation)
{
    const auto require = SparseLabelHierarchy::Require;
    require(std::isfinite(ratio) && ratio > 0 && ratio < 1, "Invalid sparse terminal ratio");
    std::vector<SizeType> counts(labels.size());
    for (std::size_t i = 0; i < labels.size(); ++i) {
        require(labels[i].first >= 2 && labels[i].first <= 5 && labels[i].second > 0,
            "Invalid sparse label population");
        counts[i] = std::max(1, static_cast<int>(std::ceil(labels[i].second * ratio)));
    }
    for (int tier = 2; tier <= 5; ++tier) {
        require(caps[tier - 2] >= navigation[tier - 2], "Sparse navigation exceeds source tier capacity");
        const std::uint64_t available = caps[tier - 2] - navigation[tier - 2];
        std::uint64_t total = 0, minimum = 0;
        for (std::size_t i = 0; i < labels.size(); ++i)
            if (labels[i].first == tier) { total += counts[i]; ++minimum; }
        require(minimum <= available, "Source tier cannot cover all admitted labels");
        if (total <= available) continue;
        std::uint64_t remaining = available - minimum, excess = total - minimum;
        // Apportion the remaining slots in label order, retaining one row per label.
        for (std::size_t i = 0; i < labels.size(); ++i) {
            if (labels[i].first != tier) continue;
            const std::uint64_t requested = counts[i] - 1;
            const auto assigned = excess ? remaining * requested / excess : 0;
            counts[i] = static_cast<SizeType>(1 + assigned);
            remaining -= assigned;
            excess -= requested;
        }
        require(remaining == 0, "Sparse tier apportionment lost capacity");
    }
    return counts;
}

template<class T>
std::vector<SizeType> SelectSparseRepresentatives(
    VectorIndex& heads, const std::vector<SizeType>& eligible, SizeType count, const Options& options)
{
    const auto require = SparseLabelHierarchy::Require;
    require(count > 0 && static_cast<std::size_t>(count) <= eligible.size(), "Invalid sparse representative count");
    if (eligible.size() == 1) return eligible;
    COMMON::Dataset<T> data(static_cast<SizeType>(eligible.size()), heads.GetFeatureDim(), 1024, eligible.size());
    for (std::size_t local = 0; local < eligible.size(); ++local)
        std::memcpy(data[local], heads.GetSample(eligible[local]), sizeof(T) * heads.GetFeatureDim());
    COMMON::BKTree tree;
    tree.m_iBKTKmeansK = options.m_iBKTKmeansK;
    tree.m_iBKTLeafSize = options.m_iBKTLeafSize;
    tree.m_iSamples = options.m_iSamples;
    tree.m_fBalanceFactor = options.m_fBalanceFactor;
    if (options.m_parallelBKTBuild)
        tree.BuildTreesParallel<T>(data, options.m_distCalcMethod, options.m_iSelectHeadNumberOfThreads,
            nullptr, nullptr, true);
    else
        tree.BuildTrees<T>(data, options.m_distCalcMethod, options.m_iSelectHeadNumberOfThreads,
            nullptr, nullptr, true);
    const auto children = [&](int id) {
        const auto& node = tree[id];
        if (node.childStart == -1 && node.childEnd == -1) return std::pair<int, int>{0, 0};
        const int begin = std::abs(node.childStart), end = node.childEnd;
        require(begin > id && end >= begin && end <= tree.size(), "Invalid native sparse selection tree");
        return std::pair<int, int>{begin, end};
    };
    std::vector<std::uint64_t> populations(tree.size(), 0);
    for (int id = static_cast<int>(tree.size()) - 1; id >= 0; --id) {
        const auto center = tree[id].centerid;
        populations[id] = center >= 0 && static_cast<std::size_t>(center) < eligible.size();
        const auto range = children(id);
        for (int child = range.first; child < range.second; ++child) populations[id] += populations[child];
    }
    require(populations[0] == eligible.size(), "Native sparse selection tree omitted or duplicated candidates");
    std::priority_queue<std::pair<std::uint64_t, int>> frontier;
    frontier.emplace(populations[0], 0);
    std::vector<SizeType> selected;
    selected.reserve(count);
    // Take native centers from the largest still-unrepresented spatial subtrees.
    while (selected.size() < static_cast<std::size_t>(count)) {
        require(!frontier.empty(), "Sparse representative selection exhausted its spatial tree");
        const int id = -frontier.top().second;
        frontier.pop();
        const auto center = tree[id].centerid;
        if (center >= 0 && static_cast<std::size_t>(center) < eligible.size()) selected.push_back(eligible[center]);
        const auto range = children(id);
        for (int child = range.first; child < range.second; ++child)
            if (populations[child]) frontier.emplace(populations[child], -child);
    }
    std::sort(selected.begin(), selected.end());
    require(std::adjacent_find(selected.begin(), selected.end()) == selected.end(), "Duplicate sparse representative");
    return selected;
}

template<class T>
SparseLabelHierarchy BuildSparseLabelHierarchy(
    VectorIndex& heads, const LimitedTagSupport& support,
    const std::vector<std::shared_ptr<VectorSet>>& catalogs, const PostingOwners& owners,
    const Options& options, std::uint64_t headIDs,
    const std::unordered_map<std::string, std::string>& headParameters = {})
{
    using Hierarchy = SparseLabelHierarchy;
    const auto require = Hierarchy::Require;
    const auto thresholds = Hierarchy::ParseThresholds(options.m_hierarchyLabelSelectivity);
    require(catalogs.size() == 4 && owners.Levels() == 4 && owners.HeadCount() == heads.GetNumSamples() &&
        support.HeadCount() == heads.GetNumSamples() && options.m_iSelectHeadNumberOfThreads > 0 &&
        options.m_ratio > 0 && options.m_ratio < 1 && options.m_iBKTKmeansK >= 2 &&
        options.m_iBKTLeafSize > 0 && options.m_iSamples > 0 &&
        options.m_indexAlgoType == IndexAlgoType::BKT && options.m_secondLevelReplicaCount > 0,
        "Sparse reconstruction requires the authenticated five-level source and valid native build settings");
    const auto* top = dynamic_cast<const CanonicalHierarchyVectorView*>(catalogs.back().get());
    require(top && top->Count() > 0, "Sparse reconstruction requires canonical source upper vectors");
    const int threads = options.m_iSelectHeadNumberOfThreads;
    const auto& physical = top->PhysicalIDs();
    const int cells = top->Count();

    // Freeze a query-independent spatial address for each original H2 owner.
    // The old CSR and all old upper vectors are discarded after reconstruction.
    std::vector<std::uint32_t> projected(cells);
    std::iota(projected.begin(), projected.end(), 0);
    for (int level = 3; level >= 1; --level) {
        std::vector<std::uint32_t> lower(catalogs[level - 1]->Count());
#pragma omp parallel for num_threads(threads) schedule(static)
        for (SizeType id = 0; id < static_cast<SizeType>(lower.size()); ++id) {
            float best = MaxDist;
            int chosen = -1;
            for (int parent : owners.Parents(level, id)) {
                const float distance = heads.ComputeDistance(
                    catalogs[level - 1]->GetVector(id), catalogs[level]->GetVector(parent));
                if (chosen < 0 || distance < best || (distance == best && parent < chosen)) {
                    chosen = parent;
                    best = distance;
                }
            }
            lower[id] = projected[chosen];
        }
        projected.swap(lower);
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Sparse spatial projection H%d: %zu addresses.\n",
            level + 1, projected.size());
    }

    COMMON::Dataset<T> topData(cells, heads.GetFeatureDim(), 1024, cells);
    for (int cell = 0; cell < cells; ++cell)
        std::memcpy(topData[cell], top->GetVector(cell), top->PerVectorDataSize());
    COMMON::BKTree tree;
    tree.m_iBKTKmeansK = options.m_iBKTKmeansK;
    tree.m_iBKTLeafSize = options.m_iBKTLeafSize;
    tree.m_iSamples = options.m_iSamples;
    tree.m_fBalanceFactor = options.m_fBalanceFactor;
    tree.BuildTrees<T>(topData, options.m_distCalcMethod, threads, nullptr, nullptr, true);
    std::vector<int> cellGroup(cells, -1), groupParent, groupCenter, coarseCenter;
    std::function<void(int, int, int, int)> partition = [&](int node, int depth, int coarse, int group) {
        const auto& item = tree[node];
        const bool validCenter = item.centerid >= 0 && item.centerid < cells;
        if (depth == 1 || (depth == 0 && validCenter)) {
            coarse = static_cast<int>(coarseCenter.size());
            coarseCenter.push_back(validCenter ? item.centerid : 0);
            group = -1;
        }
        if (depth == 2 || (validCenter && group < 0)) {
            require(coarse >= 0, "Invalid native spatial partition");
            group = static_cast<int>(groupParent.size());
            groupParent.push_back(coarse);
            groupCenter.push_back(validCenter ? item.centerid : 0);
        }
        if (validCenter) {
            require(group >= 0 && cellGroup[item.centerid] < 0, "Duplicate native spatial cell");
            cellGroup[item.centerid] = group;
        }
        if (item.childStart != -1 || item.childEnd != -1) {
            const int start = std::abs(item.childStart);
            require(start >= 0 && item.childEnd >= start && item.childEnd <= tree.size(),
                "Malformed native spatial tree");
            for (int child = start; child < item.childEnd; ++child)
                partition(child, depth + 1, coarse, group);
        }
    };
    partition(0, 0, -1, -1);
    require(std::all_of(cellGroup.begin(), cellGroup.end(), [](int group) { return group >= 0; }),
        "Native spatial partition omitted a source cell");

    Hierarchy::Header header;
    header.heads = heads.GetNumSamples();
    header.headIDs = headIDs;
    header.support = support.ContentFingerprint();
    header.thresholds = thresholds;
    header.replicas = options.m_secondLevelReplicaCount;
    header.dimension = heads.GetFeatureDim();
    header.valueType = static_cast<std::uint32_t>(heads.GetVectorValueType());
    for (int i = 0; i < 4; ++i) header.caps[i] = catalogs[i]->Count();
    Hierarchy result;
    result.Initialize(header);
    std::vector<std::vector<std::uint32_t>> cellChildren(cells),
        groupChildren(groupParent.size()), coarseChildren(coarseCenter.size());
    std::vector<std::uint32_t> rootChildren;
    std::vector<std::uint32_t> labels;
    for (const auto& entry : support.TagHeads())
        if (Hierarchy::Tier(support, entry.first, thresholds)) labels.push_back(entry.first);
    std::sort(labels.begin(), labels.end());
    std::vector<std::pair<int, SizeType>> populations;
    for (auto tag : labels)
        populations.emplace_back(Hierarchy::Tier(support, tag, thresholds),
            static_cast<SizeType>(support.TagHeads().at(tag).size()));
    const auto quotas = SparseTerminalCounts(populations, options.m_ratio, header.caps,
        {0, static_cast<std::uint32_t>(cells), static_cast<std::uint32_t>(groupParent.size()),
            static_cast<std::uint32_t>(coarseCenter.size() + 1)});
    const auto bucketFor = [&](std::uint32_t cell, int depth) -> std::uint32_t {
        if (depth == 0) return cell;
        const int group = cellGroup[cell];
        if (depth == 1) return group;
        if (depth == 2) return groupParent[group];
        return 0;
    };
    const auto centerFor = [&](std::uint32_t bucket, int depth) -> const void* {
        if (depth == 0) return top->GetVector(bucket);
        if (depth == 1) return top->GetVector(groupCenter[bucket]);
        if (depth == 2) return top->GetVector(coarseCenter[bucket]);
        return top->GetVector(0);
    };
    const auto attach = [&](int depth, std::uint32_t bucket, std::uint32_t node) {
        if (depth == 0) cellChildren[bucket].push_back(node);
        else if (depth == 1) groupChildren[bucket].push_back(node);
        else if (depth == 2) coarseChildren[bucket].push_back(node);
        else rootChildren.push_back(node);
    };
    for (std::size_t label = 0; label < labels.size(); ++label) {
        const auto tag = labels[label];
        const int tier = Hierarchy::Tier(support, tag, thresholds);
        auto eligible = support.TagHeads().at(tag);
        std::sort(eligible.begin(), eligible.end());
        require(std::adjacent_find(eligible.begin(), eligible.end()) == eligible.end(),
            "Duplicate supported H1 head in a label");
        const auto cap = quotas[label];
        const int depth = tier - 2;
        const auto selected = SelectSparseRepresentatives<T>(heads, eligible, cap, options);
        struct Representative {
            std::uint32_t head, bucket;
        };
        std::vector<Representative> representatives;
        for (auto head : selected) {
            float nearest = MaxDist;
            std::uint32_t chosen = Hierarchy::None;
            for (int owner : owners.Parents(0, head)) {
                const auto bucket = bucketFor(projected[owner], depth);
                const float distance = heads.ComputeDistance(centerFor(bucket, depth), heads.GetSample(head));
                require(std::isfinite(distance), "Non-finite sparse representative distance");
                if (chosen == Hierarchy::None || distance < nearest || (distance == nearest && bucket < chosen)) {
                    chosen = bucket;
                    nearest = distance;
                }
            }
            require(chosen != Hierarchy::None, "Sparse representative has no source spatial owner");
            representatives.push_back({static_cast<std::uint32_t>(head), chosen});
        }
        const auto count = representatives.size();
        const auto upperCount = static_cast<SizeType>(count);
        std::vector<std::uint64_t> offsets;
        std::vector<std::uint32_t> assigned;
        std::uint64_t references = 0;
        if (upperCount == 1) {
            offsets = {0, eligible.size()};
            assigned.resize(eligible.size());
            std::iota(assigned.begin(), assigned.end(), 0);
            references = eligible.size();
        } else {
            COMMON::Dataset<T> data(upperCount, heads.GetFeatureDim(), 1024, upperCount);
            std::vector<SizeType> self(eligible.size(), -1);
            for (SizeType upper = 0; upper < upperCount; ++upper) {
                const auto physicalHead = representatives[upper].head;
                std::memcpy(data[upper], heads.GetSample(physicalHead), sizeof(T) * heads.GetFeatureDim());
                const auto local = std::lower_bound(eligible.begin(), eligible.end(), physicalHead);
                require(local != eligible.end() && *local == static_cast<SizeType>(physicalHead),
                    "Sparse representative is not label-supported");
                self[local - eligible.begin()] = upper;
            }
            auto assignmentIndex = VectorIndex::CreateInstance(options.m_indexAlgoType, heads.GetVectorValueType());
            require(assignmentIndex != nullptr, "Cannot create label-local native assignment index");
            for (const auto& parameter : headParameters)
                require(assignmentIndex->SetParameter(parameter.first.c_str(), parameter.second.c_str()) == ErrorCode::Success,
                    "Invalid native label-local graph parameter");
            require(assignmentIndex->SetParameter("DistCalcMethod",
                        Helper::Convert::ConvertToString(options.m_distCalcMethod)) == ErrorCode::Success &&
                    assignmentIndex->SetParameter("NumberOfThreads", std::to_string(threads)) == ErrorCode::Success &&
                    assignmentIndex->BuildIndex(data[0], upperCount, heads.GetFeatureDim(), true, false) == ErrorCode::Success,
                    "Cannot build the temporary label-local native assignment graph");
            const auto sample = [&](SizeType lower) { return heads.GetSample(eligible[lower]); };
            int effectiveReplicas = 0;
            const int candidateCount = std::min(upperCount, std::max(
                options.m_secondLevelReplicaCount, options.m_internalResultNum));
            require(BuildHierarchyPostingAssignments<T>(static_cast<SizeType>(eligible.size()), upperCount,
                self, sample, assignmentIndex, options.m_secondLevelReplicaCount, candidateCount,
                threads, options.m_rngFactor, effectiveReplicas, references, offsets, assigned) == ErrorCode::Success,
                "Label-local native ANN/RNG assignment failed");
        }
        for (SizeType upper = 0; upper < upperCount; ++upper) {
            std::vector<std::uint32_t> members;
            members.reserve(offsets[upper + 1] - offsets[upper]);
            for (auto at = offsets[upper]; at < offsets[upper + 1]; ++at)
                members.push_back(eligible[assigned[at]]);
            std::sort(members.begin(), members.end());
            const auto& representative = representatives[upper];
            const auto node = result.AddRow(representative.head, tag, tier, members);
            attach(depth, representative.bucket, node);
        }
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info,
            "Sparse label=%u selectivity=%.12g H%d supported_heads=%zu postings=%llu cap=%llu references=%llu grid=%d assignment=label-local-native-rng.\n",
            tag, double(support.TagVectorCount(tag)) / support.VectorCount(), tier, eligible.size(),
            static_cast<unsigned long long>(count), static_cast<unsigned long long>(cap),
            static_cast<unsigned long long>(references), depth);
    }
    const auto addNavigation = [&](const std::vector<std::uint32_t>& children, int tier,
                                    const void* center) -> std::uint32_t {
        if (children.empty()) return Hierarchy::None;
        float best = MaxDist;
        std::uint32_t representative = Hierarchy::None;
        for (auto child : children) {
            const auto head = result.At(child).representative;
            const float distance = heads.ComputeDistance(center, heads.GetSample(head));
            require(std::isfinite(distance), "Non-finite sparse navigation representative");
            if (representative == Hierarchy::None || distance < best) { representative = head; best = distance; }
        }
        return result.AddRow(representative, Hierarchy::None, tier, children);
    };
    std::vector<std::uint32_t> cellNodes(cells, Hierarchy::None),
        groupNodes(groupParent.size(), Hierarchy::None), coarseNodes(coarseCenter.size(), Hierarchy::None);
    for (int cell = 0; cell < cells; ++cell) {
        cellNodes[cell] = addNavigation(cellChildren[cell], 3, top->GetVector(cell));
        if (cellNodes[cell] != Hierarchy::None) groupChildren[cellGroup[cell]].push_back(cellNodes[cell]);
    }
    for (std::size_t group = 0; group < groupParent.size(); ++group) {
        groupNodes[group] = addNavigation(groupChildren[group], 4, top->GetVector(groupCenter[group]));
        if (groupNodes[group] != Hierarchy::None) coarseChildren[groupParent[group]].push_back(groupNodes[group]);
    }
    for (std::size_t coarse = 0; coarse < coarseCenter.size(); ++coarse) {
        coarseNodes[coarse] = addNavigation(coarseChildren[coarse], 5, top->GetVector(coarseCenter[coarse]));
        if (coarseNodes[coarse] != Hierarchy::None) rootChildren.push_back(coarseNodes[coarse]);
    }
    const auto root = addNavigation(rootChildren, 5, top->GetVector(0));
    if (root != Hierarchy::None) {
        std::vector<std::uint32_t> cellEntry(cells);
        for (int cell = 0; cell < cells; ++cell) {
            const int group = cellGroup[cell];
            auto entry = cellNodes[cell];
            if (entry == Hierarchy::None) entry = groupNodes[group];
            if (entry == Hierarchy::None) entry = coarseNodes[groupParent[group]];
            cellEntry[cell] = entry == Hierarchy::None ? root : entry;
        }
#pragma omp parallel for num_threads(threads) schedule(static)
        for (SizeType head = 0; head < heads.GetNumSamples(); ++head) {
            float nearest = MaxDist;
            std::uint32_t chosen = Hierarchy::None;
            for (int owner : owners.Parents(0, head)) {
                const auto cell = projected[owner];
                const float distance = heads.ComputeDistance(heads.GetSample(head), heads.GetSample(physical[cell]));
                if (chosen == Hierarchy::None || distance < nearest || (distance == nearest && cell < chosen)) {
                    nearest = distance;
                    chosen = cell;
                }
            }
            result.SetEntry(head, cellEntry[chosen]);
        }
    }
    result.Validate(support, headIDs, thresholds, heads.GetFeatureDim(), heads.GetVectorValueType());
    result.BuildOwners();
    SPTAGLIB_LOG(Helper::LogLevel::LL_Info,
        "Sparse hierarchy complete: %d postings, %zu eligible labels, %d H1 spatial entry references.\n",
        result.Count(), labels.size(), result.HeadCount());
    return result;
}
}}
