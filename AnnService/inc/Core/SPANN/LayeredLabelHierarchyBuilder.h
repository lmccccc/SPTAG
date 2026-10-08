// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/SPANN/SparseLabelHierarchyBuilder.h"
#include "inc/Core/SPANN/LimitedTagSupportExpansion.h"
#include "inc/Core/SPANN/LocalLabelCensus.h"
#include <exception>
#include <map>
#include <mutex>

namespace SPTAG { namespace SPANN {

template<class Action>
void BuildLayeredParallel(SizeType count, int threads, Helper::BuildProgress& progress, const Action& action)
{
    std::atomic<bool> failed(false);
    std::exception_ptr error;
    std::mutex mutex;
#pragma omp parallel for num_threads(threads) schedule(dynamic, 1)
    for (std::int64_t begin = 0; begin < count; begin += 64) {
        Helper::BuildProgress::Work work;
        std::uint64_t completed = 0;
        for (auto id = begin; id < (std::min)(begin + 64, std::int64_t(count)); ++id) {
            if (failed.load(std::memory_order_relaxed)) break;
            try { work.Add(action(static_cast<SizeType>(id))); ++completed; }
            catch (const std::exception&) {
                std::lock_guard<std::mutex> lock(mutex);
                if (!error) error = std::current_exception();
                failed.store(true, std::memory_order_relaxed);
            }
        }
        progress.Advance(completed, work);
    }
    if (error) std::rethrow_exception(error);
    progress.Finish();
}

template<class T>
SparseLabelHierarchy BuildLayeredLabelHierarchy(
    VectorIndex& heads, const LimitedTagSupport& support,
    const std::vector<std::shared_ptr<VectorSet>>& catalogs, const PostingOwners& owners,
    const Options& options, std::uint64_t headIDs,
    const std::unordered_map<std::string, std::string>& headParameters = {})
{
    using Hierarchy = SparseLabelHierarchy;
    const auto require = Hierarchy::Require;
    const auto thresholds = Hierarchy::AdmissionParameters(options);
    require(catalogs.size() == 4 && owners.Levels() == 4 && owners.HeadCount() == heads.GetNumSamples() &&
        support.HeadCount() == heads.GetNumSamples() && support.HasTagVectorCounts() &&
        options.m_iSelectHeadNumberOfThreads > 0 && options.m_ratio > 0 && options.m_ratio < 1 &&
        options.m_iBKTKmeansK >= 2 && options.m_iBKTLeafSize > 0 && options.m_iSamples > 0 &&
        options.m_indexAlgoType == IndexAlgoType::BKT && options.m_secondLevelReplicaCount > 0 &&
        options.m_internalResultNum > 0 && std::isfinite(options.m_rngFactor) && options.m_rngFactor > 0 &&
        options.m_limitedTagSlotsPerHead == support.SlotsPerHead() &&
        options.m_limitedTagMinHeadCount == support.MinHeadCount() &&
        options.m_enableLimitedTagSupportExpansion == support.HasExpansion(),
        "Layered reconstruction requires canonical H1 and unchanged native limited-tag settings");
    Hierarchy::Header header;
    header.version = options.m_hierarchyLocalTarget ? 5 : 4;
    header.assignment = header.version - 1;
    header.heads = heads.GetNumSamples(); header.headIDs = headIDs;
    header.support = support.ContentFingerprint(); header.thresholds = thresholds;
    header.replicas = options.m_secondLevelReplicaCount;
    header.dimension = heads.GetFeatureDim();
    header.valueType = static_cast<std::uint32_t>(heads.GetVectorValueType());
    for (int i = 0; i < 4; ++i) {
        require(catalogs[i] && catalogs[i]->Count() > 0, "Missing source layer count cap");
        header.caps[i] = catalogs[i]->Count();
    }
    Hierarchy result;
    result.Initialize(header);
    if (result.Local()) {
        Helper::BuildProgress progress("local-label-census");
        result.SetLocalAdmission(BuildLocalLabelCensus<T>(support, owners, *catalogs.back(), options));
        progress.Finish();
    }
    struct Lower {
        std::uint32_t id, physical, anchor;
        std::vector<std::uint32_t> labels;
    };
    struct Item { SizeType lower; std::uint32_t tag; };
    struct Edge { SizeType node, tonode; float distance; };
    Helper::BuildProgress admissionProgress("H1-label-admission", support.TagHeads().size());
    std::vector<std::pair<SizeType, std::uint32_t>> pairs;
    for (const auto& entry : support.TagHeads()) {
        for (auto head : entry.second)
            if (result.AdmitChild(support, head, entry.first, 2)) pairs.emplace_back(head, entry.first);
        admissionProgress.Advance();
    }
    admissionProgress.Finish();
    Helper::BuildProgress inputProgress("H1-sort-and-group");
    std::sort(pairs.begin(), pairs.end());
    require(std::adjacent_find(pairs.begin(), pairs.end()) == pairs.end(),
        "Duplicate H1 sparse head-label input");
    std::vector<Lower> lower;
    for (const auto& pair : pairs) {
        if (lower.empty() || lower.back().id != static_cast<unsigned>(pair.first)) {
            const auto own = support.HeadAttributes(pair.first)[support.KeyColumn()];
            lower.push_back({static_cast<unsigned>(pair.first), static_cast<unsigned>(pair.first), own, {}});
        }
        lower.back().labels.push_back(pair.second);
    }
    std::vector<std::pair<SizeType, std::uint32_t>>().swap(pairs);
    inputProgress.Finish();
    const int threads = options.m_iSelectHeadNumberOfThreads;
    const int replicas = options.m_secondLevelReplicaCount;
    for (int tier = 2; tier <= 5; ++tier) {
        const auto stage = "H" + std::to_string(tier) + "-";
        Helper::BuildProgress selectionProgress(stage + "select-and-plan");
        for (auto& child : lower) {
            auto& tags = child.labels;
            tags.erase(std::remove_if(tags.begin(), tags.end(), [&](std::uint32_t tag) {
                return !result.AdmitChild(support, child.physical, tag, tier);
            }), tags.end());
            if (!tags.empty() && !std::binary_search(tags.begin(), tags.end(), child.anchor))
                child.anchor = tags.front();
        }
        lower.erase(std::remove_if(lower.begin(), lower.end(),
            [](const Lower& child) { return child.labels.empty(); }), lower.end());
        if (lower.empty()) { selectionProgress.Finish(); break; }
        require(lower.size() <= static_cast<std::size_t>(MaxSize), "Too many layered physical children");
        std::vector<SizeType> physical;
        for (const auto& child : lower) physical.push_back(child.physical);
        require(std::is_sorted(physical.begin(), physical.end()) &&
            std::adjacent_find(physical.begin(), physical.end()) == physical.end(),
            "Layered physical inputs must be unique, not previous replica edges");
        const auto upperCount = static_cast<SizeType>(std::min<double>(header.caps[tier - 2],
            std::max(1.0, std::ceil(lower.size() * options.m_ratio))));
        const auto selected = SelectSparseRepresentatives<T>(heads, physical, upperCount, options);
        std::vector<SizeType> selectedLower;
        for (auto head : selected)
            selectedLower.push_back(static_cast<SizeType>(
                std::lower_bound(physical.begin(), physical.end(), head) - physical.begin()));
        std::vector<Item> items;
        std::vector<std::uint32_t> observed;
        std::unordered_map<std::uint32_t, std::uint64_t> populations;
        std::vector<SizeType> itemOffsets{0};
        for (SizeType child = 0; child < static_cast<SizeType>(lower.size()); ++child) {
            require(lower[child].labels.size() <= static_cast<std::size_t>(MaxSize) - items.size(),
                "Layered logical head-label input exceeds native IDs");
            for (auto tag : lower[child].labels) {
                items.push_back({child, tag});
                ++populations[tag];
            }
            itemOffsets.push_back(static_cast<SizeType>(items.size()));
        }
        for (const auto& entry : populations) observed.push_back(entry.first);
        std::sort(observed.begin(), observed.end());
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info,
            "Layered H%d plan: physicalChildren=%zu logicalChildLabels=%zu parents=%d labels=%zu policy=%s.\n",
            tier, lower.size(), items.size(), upperCount, observed.size(), result.Local() ? "local" : "global");
        std::vector<SizeType> ownItems(upperCount), itemOwner(items.size(), -1);
        std::vector<std::uint32_t> ownTags;
        for (SizeType upper = 0; upper < upperCount; ++upper) {
            const auto child = selectedLower[upper];
            ownTags.push_back(lower[child].anchor);
            const auto at = std::lower_bound(lower[child].labels.begin(), lower[child].labels.end(), ownTags.back());
            ownItems[upper] = itemOffsets[child] + static_cast<SizeType>(at - lower[child].labels.begin());
            itemOwner[ownItems[upper]] = upper;
        }
        selectionProgress.Finish();
        Helper::BuildProgress graphProgress(stage + "temporary-BKT");
        auto index = VectorIndex::CreateInstance(options.m_indexAlgoType, heads.GetVectorValueType());
        require(index != nullptr, "Cannot create layered native assignment index");
        for (const auto& parameter : headParameters)
            require(index->SetParameter(parameter.first.c_str(), parameter.second.c_str()) == ErrorCode::Success,
                "Invalid source BuildHead setting for layered assignment");
        COMMON::Dataset<T> data(upperCount, heads.GetFeatureDim(), 1024, upperCount);
        for (SizeType id = 0; id < upperCount; ++id)
            std::memcpy(data[id], heads.GetSample(selected[id]), sizeof(T) * heads.GetFeatureDim());
        require(index->SetParameter("DistCalcMethod",
                    Helper::Convert::ConvertToString(options.m_distCalcMethod)) == ErrorCode::Success &&
                index->SetParameter("NumberOfThreads", std::to_string(threads)) == ErrorCode::Success &&
                index->BuildIndex(data[0], upperCount, heads.GetFeatureDim(), true, false) == ErrorCode::Success,
                "Cannot build layered temporary native assignment graph");
        graphProgress.Finish();
        const auto distance = [&](SizeType left, SizeType right) {
            return index->ComputeDistance(index->GetSample(left), index->GetSample(right));
        };
        const int candidates = std::min(upperCount, options.m_internalResultNum);
        std::vector<std::vector<std::pair<float, SizeType>>> original(lower.size());
        Helper::BuildProgress originalProgress(stage + "O-assignment", lower.size());
        BuildLayeredParallel(static_cast<SizeType>(lower.size()), threads, originalProgress, [&](SizeType child) {
            COMMON::QueryResultSet<T> query(static_cast<const T*>(heads.GetSample(lower[child].physical)), candidates);
            require(index->SearchIndex(query) == ErrorCode::Success, "Layered original posting search failed");
            std::vector<SizeType> chosen;
            require(SelectLimitedTagPostingCandidates(query, upperCount, replicas, options.m_rngFactor,
                [](SizeType) { return true; }, distance, chosen, [&](const BasicResult& candidate) {
                    original[child].emplace_back(candidate.Dist, candidate.VID);
                }) && !chosen.empty(), "Layered original posting has no native RNG assignment");
            Helper::BuildProgress::Work work;
            work.checked = work.maxChecked = query.GetScanned();
            return work;
        });
        Helper::BuildProgress supportProgress(stage + "retained-O-support");
        std::vector<Edge> originalEdges;
        std::vector<int> retained(upperCount, 0);
        for (SizeType item = 0; item < static_cast<SizeType>(items.size()); ++item) {
            if (itemOwner[item] >= 0) continue;
            for (const auto& assignment : original[items[item].lower]) {
                require(retained[assignment.second] < MaxSize, "Layered original posting too large");
                originalEdges.push_back({assignment.second, item, assignment.first});
                ++retained[assignment.second];
            }
        }
        original.clear();
        original.shrink_to_fit();
        std::sort(originalEdges.begin(), originalEdges.end(), [](const Edge& left, const Edge& right) {
            return std::tie(left.node, left.distance, left.tonode) < std::tie(right.node, right.distance, right.tonode);
        });
        LimitedTagSupport upperSupport;
        require(upperSupport.Initialize(upperCount, support.SlotsPerHead(), support.MinHeadCount(), 0, 1, headIDs) &&
            upperSupport.SetTagVectorCounts(items.size(), populations), "Cannot initialize layered limited support");
        std::size_t read = 0;
        for (SizeType upper = 0; upper < upperCount; ++upper) {
            std::vector<std::pair<float, SizeType>> row;
            for (int i = 0; i < retained[upper]; ++i) {
                const auto& edge = originalEdges[read++];
                row.emplace_back(edge.distance, edge.tonode);
            }
            const auto tags = SelectNearestLimitedLabels(ownTags[upper], ownItems[upper], row,
                support.SlotsPerHead(), [&](SizeType item) { return items[item].tag; });
            require(upperSupport.SetHeadTags(upper, tags) &&
                upperSupport.SetHeadAttributes(upper, &ownTags[upper], 1), "Cannot set layered limited labels");
        }
        std::string error;
        if (options.m_enableLimitedTagSupportExpansion) {
            LimitedTagSupportExpansion expansion;
            if (!expansion.Initialize(upperSupport, observed, support.MinHeadCount(), &error) ||
                !expansion.ObserveRetainedOriginalPostings(originalEdges, retained,
                    static_cast<SizeType>(items.size()), [&](SizeType item) { return items[item].tag; }, &error) ||
                !expansion.Apply(upperSupport, &error))
                throw std::runtime_error("Layered native retained-O support expansion: " + error);
        }
        if (!upperSupport.Finalize(&error))
            throw std::runtime_error("Layered native limited support invalid: " + error);
        std::vector<Edge>().swap(originalEdges);
        supportProgress.Finish();
        std::vector<std::vector<SizeType>> assigned(items.size());
        std::atomic<std::uint64_t> fallbacks(0);
        Helper::BuildProgress constrainedProgress(stage + "H-assignment", items.size());
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info,
            "Layered H%d construction search: MaxCheck=%s candidates<=%d support-aware direct/exact work reported separately.\n",
            tier, index->GetParameter("MaxCheck").c_str(), candidates);
        BuildLayeredParallel(static_cast<SizeType>(items.size()), threads, constrainedProgress, [&](SizeType item) {
            Helper::BuildProgress::Work work;
            if (itemOwner[item] >= 0) {
                assigned[item].push_back(itemOwner[item]);
                return work;
            }
            const auto& value = items[item];
            const auto* sample = static_cast<const T*>(heads.GetSample(lower[value.lower].physical));
            const auto allowed = [&](SizeType parent) { return upperSupport.Supports(parent, value.tag); };
            const auto supported = upperSupport.TagHeads().find(value.tag);
            require(supported != upperSupport.TagHeads().end() && !supported->second.empty(),
                "Layered child-label has no supported parent");
            COMMON::QueryResultSet<T> query(sample,
                static_cast<int>(std::min<std::size_t>(candidates, supported->second.size())));
            require(SearchLimitedTagPostingCandidates(*index, supported->second,
                options.m_enableLimitedTagSupportExpansion, query,
                [&](COMMON::QueryResultSet<T>& resultSet) {
                    return index->SearchIndexForConstruction(resultSet, allowed);
                }, work) == ErrorCode::Success,
                "Layered constrained native posting search failed");
            std::vector<SizeType> chosen;
            const auto select = [&](const COMMON::QueryResultSet<T>& resultSet) {
                return SelectLimitedTagPostingCandidates(resultSet, upperCount, replicas, options.m_rngFactor,
                    allowed, distance, chosen, [&](const BasicResult& candidate) {
                        assigned[item].push_back(candidate.VID);
                    });
            };
            require(select(query), "Invalid layered constrained candidate");
            fallbacks.fetch_add(work.fallbacks, std::memory_order_relaxed);
            require(!assigned[item].empty(), "Layered child-label has no posting, matching native H placement failure");
            return work;
        });
        Helper::BuildProgress rowsProgress(stage + "materialize-rows");
        std::vector<std::map<std::uint32_t, std::vector<std::uint32_t>>> rows(upperCount);
        std::uint64_t references = 0;
        for (SizeType item = 0; item < static_cast<SizeType>(items.size()); ++item)
            for (auto parent : assigned[item]) {
                rows[parent][items[item].tag].push_back(lower[items[item].lower].id);
                ++references;
            }
        std::vector<Lower> next;
        const int first = result.Count();
        for (SizeType upper = 0; upper < upperCount; ++upper) {
            std::vector<std::pair<std::uint32_t, std::vector<std::uint32_t>>> labelRows;
            std::vector<std::uint32_t> tags;
            for (auto& row : rows[upper]) {
                std::sort(row.second.begin(), row.second.end());
                require(std::adjacent_find(row.second.begin(), row.second.end()) == row.second.end(),
                    "Duplicate layered child-label assignment");
                tags.push_back(row.first);
                labelRows.emplace_back(row.first, std::move(row.second));
            }
            const auto id = result.AddLayeredRow(selected[upper], ownTags[upper], tier, labelRows);
            next.push_back({id, static_cast<std::uint32_t>(selected[upper]), ownTags[upper], std::move(tags)});
        }
        rowsProgress.Finish();
        if (tier == 2) {
            Helper::BuildProgress entryProgress("H1-spatial-entries", heads.GetNumSamples());
            BuildLayeredParallel(heads.GetNumSamples(), threads, entryProgress, [&](SizeType head) {
                COMMON::QueryResultSet<T> query(static_cast<const T*>(heads.GetSample(head)), 1);
                require(index->SearchIndex(query) == ErrorCode::Success && query.GetResult(0)->VID >= 0 &&
                    query.GetResult(0)->VID < upperCount, "Cannot assign layered H1 spatial entry");
                result.SetEntry(head, first + query.GetResult(0)->VID);
                Helper::BuildProgress::Work work;
                work.checked = work.maxChecked = query.GetScanned();
                return work;
            });
        }
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info,
            "Layered H%d: physicalChildren=%zu logicalChildLabels=%zu heads=%d cap=%u references=%llu "
            "replicaLimit=%d baseSlots=%d expansion=%d exactFallbacks=%llu (no replica fill).\n",
            tier, lower.size(), items.size(), upperCount, header.caps[tier - 2],
            static_cast<unsigned long long>(references), replicas, support.SlotsPerHead(),
            int(options.m_enableLimitedTagSupportExpansion), static_cast<unsigned long long>(fallbacks.load()));
        lower = std::move(next);
    }
    Helper::BuildProgress validationProgress("hierarchy-validate-and-owners");
    result.Validate(support, headIDs, thresholds, heads.GetFeatureDim(), heads.GetVectorValueType());
    result.BuildOwners();
    validationProgress.Finish();
    return result;
}
}}
