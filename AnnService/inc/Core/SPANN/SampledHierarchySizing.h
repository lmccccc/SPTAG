// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/SPANN/Options.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <map>
#include <numeric>
#include <stdexcept>
#include <unordered_set>

namespace SPTAG { namespace SPANN {

struct SampledHierarchySizing
{
    struct Trial {
        std::uint64_t heads = 0, pairs = 0, parents = 0, rows = 0, references = 0, sampleHash = 0;
        double estimatedReferences = 0;
    };
    struct Tier {
        std::uint64_t children = 0, pairs = 0, parents = 0, rows = 0, references = 0, trials = 0;
        std::array<Trial, 2> pilot{};
    };
    std::uint64_t version = 2, target = 0, sampleHeads = 0;
    std::array<Tier, 4> tiers{};

    static void Require(bool condition, const char* message)
    {
        if (!condition) throw std::invalid_argument(message);
    }
    static void ValidateOptions(const Options& options)
    {
        Require(options.m_hierarchyTargetPostingSize >= 0 && options.m_hierarchySizingSampleHeads >= 0 &&
            bool(options.m_hierarchyTargetPostingSize) == bool(options.m_hierarchySizingSampleHeads),
            "HierarchyTargetPostingSize and HierarchySizingSampleHeads must both be positive or both zero");
        Require(!options.m_hierarchyTargetPostingSize ||
            (options.m_hierarchyLocalTarget > 0 && options.m_hierarchyLocalWindow > 0 &&
             options.m_hierarchyLabelSelectivity.empty()),
            "Sampled upper sizing requires local admission; source Ratio remains the census ratio");
    }
    static SizeType Plan(std::uint64_t children, std::uint64_t cap,
                         std::uint64_t target, const Trial& trial)
    {
        Require(children > 0 && children <= MaxSize && cap > 0 && target > 0 &&
            trial.parents > 0 && trial.rows >= trial.parents &&
            std::isfinite(trial.estimatedReferences) && trial.estimatedReferences > 0,
            "Invalid sampled upper sizing estimate");
        const long double requested = std::ceil(
            static_cast<long double>(trial.estimatedReferences) * trial.parents /
            (static_cast<long double>(target) * trial.rows));
        return static_cast<SizeType>(std::max<long double>(1,
            std::min<long double>(std::min(children, cap), requested)));
    }
    void Validate(const std::array<std::uint32_t, 4>& caps, std::uint32_t replicas) const
    {
        Require((version == 1 || version == 2) && target > 0 && target <= MaxSize && sampleHeads > 0 &&
            sampleHeads <= MaxSize, "Invalid sampled sizing metadata");
        bool stopped = false;
        for (std::size_t level = 0; level < tiers.size(); ++level) {
            const auto& tier = tiers[level];
            if (!tier.children) {
                stopped = true;
                Require(!tier.pairs && !tier.parents && !tier.rows && !tier.references && !tier.trials,
                    "Nonempty sizing record for an empty tier");
            } else {
                Require(!stopped && tier.children <= MaxSize && tier.pairs >= tier.children &&
                    tier.pairs <= MaxSize && tier.parents > 0 &&
                    tier.parents <= std::min<std::uint64_t>(tier.children, caps[level]) &&
                    tier.rows >= tier.parents && tier.references >= tier.pairs &&
                    tier.references <= tier.pairs * replicas && tier.trials >= 1 && tier.trials <= 2,
                    "Invalid sampled sizing tier counts");
            }
            for (std::size_t pass = 0; pass < tier.pilot.size(); ++pass) {
                const auto& trial = tier.pilot[pass];
                if (pass >= tier.trials) {
                    Require(!trial.heads && !trial.pairs && !trial.parents && !trial.rows &&
                        !trial.references && !trial.sampleHash && trial.estimatedReferences == 0,
                        "Nonempty unused sizing trial");
                    continue;
                }
                Require(trial.heads == std::min(tier.children, sampleHeads) &&
                    trial.pairs >= trial.heads && trial.pairs <= tier.pairs &&
                    trial.parents > 0 && trial.parents <= trial.heads && trial.rows >= trial.parents &&
                    trial.references >= trial.pairs && trial.references <= trial.pairs * replicas &&
                    trial.rows <= trial.references &&
                    trial.sampleHash && std::isfinite(trial.estimatedReferences) &&
                    trial.estimatedReferences >= double(tier.pairs) * (1 - 1e-12) &&
                    trial.estimatedReferences <= double(tier.pairs * replicas) * (1 + 1e-12),
                    "Invalid sampled O/H trial");
            }
            if (tier.children)
                Require(tier.parents == static_cast<std::uint64_t>(
                    Plan(tier.children, caps[level], target, tier.pilot[tier.trials - 1])),
                    "Persisted center count does not match its sampled estimate");
        }
    }

    static std::vector<SizeType> Sample(SizeType population, SizeType budget, int tier)
    {
        Require(population > 0 && budget > 0 && tier >= 2 && tier <= 5, "Invalid sizing sample request");
        const SizeType count = std::min(population, budget);
        std::vector<SizeType> ids;
        ids.reserve(count);
        if (count == population) {
            ids.resize(count);
            std::iota(ids.begin(), ids.end(), 0);
            return ids;
        }
        // Floyd sampling uses bounded memory and does not alter native BKT/TPT RNG state.
        std::uint64_t state = 0x73697a696e673031ULL + tier;
        const auto random = [&]() {
            auto value = (state += 0x9e3779b97f4a7c15ULL);
            value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
            value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
            return value ^ (value >> 31);
        };
        std::unordered_set<SizeType> selected;
        selected.reserve(count);
        for (SizeType end = population - count; end < population; ++end) {
            const auto bound = std::uint64_t(end) + 1;
            const auto threshold = (std::uint64_t(0) - bound) % bound;
            auto draw = random();
            while (draw < threshold) draw = random();
            const auto candidate = static_cast<SizeType>(draw % bound);
            selected.insert(selected.count(candidate) ? end : candidate);
        }
        ids.assign(selected.begin(), selected.end());
        std::sort(ids.begin(), ids.end());
        Require(ids.size() == static_cast<std::size_t>(count), "Sizing sampler lost unique physical heads");
        return ids;
    }

    template<class LabelsAt>
    static std::vector<SizeType> SampleCovered(SizeType population, SizeType budget, int tier,
        const std::map<std::uint32_t, std::uint64_t>& populations, const LabelsAt& labelsAt)
    {
        auto ids = Sample(population, budget, tier);
        std::map<std::uint32_t, std::uint64_t> counts, required;
        for (const auto& entry : populations) {
            Require(entry.second > 0 && entry.second <= static_cast<std::uint64_t>(population),
                "Invalid per-label sizing population");
            required[entry.first] = std::min<std::uint64_t>(16, entry.second);
        }
        for (auto id : ids) for (auto tag : labelsAt(id)) ++counts[tag];
        using Candidate = std::pair<std::uint64_t, SizeType>;
        struct Deficit {
            std::size_t count;
            std::vector<Candidate> candidates;
        };
        std::map<std::uint32_t, Deficit> deficits;
        for (const auto& entry : required) {
            if (counts[entry.first] < entry.second)
                deficits.emplace(entry.first, Deficit{static_cast<std::size_t>(
                    entry.second - counts[entry.first]), {}});
        }
        if (deficits.empty()) return ids;
        std::unordered_set<SizeType> selected(ids.begin(), ids.end());
        const auto rank = [&](SizeType id, std::uint32_t tag) {
            std::uint64_t value = ((std::uint64_t(tag) << 32) | static_cast<std::uint32_t>(id)) +
                0x9e3779b97f4a7c15ULL + tier;
            value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
            value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
            return value ^ (value >> 31);
        };
        // Visit label metadata only; no lower-vector copy, distance or ANN query.
        for (SizeType id = 0; id < population; ++id) for (auto tag : labelsAt(id)) {
            const auto found = deficits.find(tag);
            if (found == deficits.end() || selected.count(id)) continue;
            auto& deficit = found->second;
            const Candidate candidate{rank(id, tag), id};
            auto& candidates = deficit.candidates;
            if (candidates.size() < deficit.count) {
                candidates.push_back(candidate);
                std::push_heap(candidates.begin(), candidates.end());
            } else if (candidate < candidates.front()) {
                std::pop_heap(candidates.begin(), candidates.end());
                candidates.back() = candidate;
                std::push_heap(candidates.begin(), candidates.end());
            }
        }
        std::size_t added = 0;
        for (const auto& entry : deficits) {
            Require(entry.second.candidates.size() == entry.second.count,
                "Sizing label census disagrees with available children");
            for (const auto& candidate : entry.second.candidates) {
                if (!selected.insert(candidate.second).second) continue;
                ++added;
                for (auto tag : labelsAt(candidate.second)) ++counts[tag];
            }
        }
        std::vector<Candidate> donors;
        donors.reserve(ids.size());
        for (auto id : ids) donors.emplace_back(rank(id, UINT32_MAX), id);
        std::sort(donors.begin(), donors.end());
        for (const auto& donor : donors) {
            if (selected.size() == ids.size()) break;
            const auto& tags = labelsAt(donor.second);
            if (!std::all_of(tags.begin(), tags.end(),
                [&](std::uint32_t tag) { return counts.at(tag) > required.at(tag); })) continue;
            selected.erase(donor.second);
            for (auto tag : tags) --counts[tag];
        }
        Require(selected.size() == ids.size(),
            "HierarchySizingSampleHeads cannot retain minimum per-label coverage; increase the sample budget");
        for (const auto& entry : required)
            Require(counts.at(entry.first) >= entry.second, "Sizing sample lost required label coverage");
        ids.assign(selected.begin(), selected.end());
        std::sort(ids.begin(), ids.end());
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info,
            "Sizing H%d stratified sample: heads=%zu replaced=%zu deficientLabels=%zu "
            "minPerLabel=16 metadataOnlyRepair=1.\n", tier, ids.size(), added, deficits.size());
        return ids;
    }
};
static_assert(sizeof(SampledHierarchySizing::Trial) == 56, "Sizing trial layout");
static_assert(sizeof(SampledHierarchySizing::Tier) == 160, "Sizing tier layout");
static_assert(sizeof(SampledHierarchySizing) == 664, "Sizing metadata layout");

}}
