// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/VectorIndex.h"
#include "inc/Core/SPANN/LimitedTagSupport.h"
#include "inc/Core/SPANN/NumericMetadata.h"
#include "inc/Core/SPANN/SecondLevelHeadPostings.h"

namespace SPTAG { namespace SPANN {

class RoutingPredicate
{
    struct Range { int lane, low, high; };
    struct Clause {
        std::vector<std::pair<int, std::uint32_t>> categories;
        std::vector<Range> ranges;
        bool impossible = false;
    };
    std::vector<Clause> m_clauses;

public:
    RoutingPredicate(const Cache::DNFPredicate* dnf, const std::uint32_t* tags,
                     int count, const std::vector<int>& numericColumns,
                     const std::vector<Cache::NumQuantParam>& params)
    {
        if (dnf && !dnf->Empty()) {
            for (const auto& source : dnf->clauses) {
                if (source.lits.empty()) continue;
                Clause clause;
                for (const auto& literal : source.lits) {
                    if (literal.kind == 0) {
                        clause.categories.emplace_back(literal.col, literal.val);
                        continue;
                    }
                    const auto found = std::find(numericColumns.begin(), numericColumns.end(),
                                                 static_cast<int>(literal.col));
                    const int lane = static_cast<int>(found - numericColumns.begin());
                    if (found == numericColumns.end() || lane >= static_cast<int>(params.size()))
                        continue;
                    if (std::any_of(clause.ranges.begin(), clause.ranges.end(),
                                    [lane](const Range& r) { return r.lane == lane; })) continue;
                    int low = 0, high = 0;
                    if (!Cache::DNFPredicate::NumericRangeBuckets(
                            source, literal.col, params[lane], low, high)) {
                        clause.impossible = true;
                        break;
                    }
                    clause.ranges.push_back({lane, low, high});
                }
                m_clauses.push_back(std::move(clause));
            }
        } else {
            for (int i = 0; i < count; ++i) {
                Clause clause;
                clause.categories.emplace_back(-1, tags[i]);
                m_clauses.push_back(std::move(clause));
            }
        }
    }

    bool MayMatch(const Cache::PostingBitmask* coarse,
                  HeadPostingMaskView hierarchical,
                  const Cache::HierWidthTable& widths, const std::uint64_t* numeric) const
    {
        for (const auto& clause : m_clauses) {
            if (clause.impossible) continue;
            bool match = true;
            for (const auto& category : clause.categories) {
                const bool knownColumn = category.first >= 0 && category.first < Cache::HIER_LEVELS;
                if (hierarchical && knownColumn) {
                    if (!hierarchical.MayContain(category.first, category.second, widths)) {
                        match = false; break;
                    }
                } else if (coarse && !coarse->MayContain(category.second)) {
                    match = false; break;
                }
            }
            if (!match) continue;
            if (numeric) for (const auto& range : clause.ranges) {
                if (!Cache::NumQuantAnyInRange(numeric, range.lane, range.low, range.high)) {
                    match = false; break;
                }
            }
            if (match) return true;
        }
        return false;
    }

    bool MayMatch(const Cache::PostingBitmask* coarse,
                  const HeadPostingMaskView* hierarchical,
                  const Cache::HierWidthTable& widths, const std::uint64_t* numeric) const
    {
        return MayMatch(coarse, hierarchical ? *hierarchical : HeadPostingMaskView{}, widths, numeric);
    }
};

// H1 reuses its existing packed metadata. Only upper catalogs allocate summaries.
class RoutingSignatures
{
    struct Layer {
        std::vector<Cache::PostingBitmask> pure, tail;
        std::vector<std::uint64_t> pureNumeric, tailNumeric;
        const Cache::PostingBitmask* sharedPure = nullptr;
        const Cache::PostingBitmask* Pure(SizeType head) const {
            return sharedPure ? sharedPure + head : pure.data() + head;
        }
    };
    std::vector<Layer> m_layers;
    VectorIndex* m_heads = nullptr;
    std::uint64_t m_revision = 0;
    Cache::HierWidthTable m_widths;
    bool m_ready = false;

public:
    std::vector<Cache::NumQuantParam> params;
    std::size_t SharedBytes() const
    {
        std::size_t bytes = 0;
        for (const auto& layer : m_layers)
            bytes += (layer.pure.size() + layer.tail.size()) * sizeof(Cache::PostingBitmask) +
                (layer.pureNumeric.size() + layer.tailNumeric.size()) * sizeof(std::uint64_t);
        return bytes;
    }
    std::size_t SharedCapacityBytes() const
    {
        std::size_t bytes = 0;
        for (const auto& layer : m_layers)
            bytes += (layer.pure.capacity() + layer.tail.capacity()) * sizeof(Cache::PostingBitmask) +
                (layer.pureNumeric.capacity() + layer.tailNumeric.capacity()) * sizeof(std::uint64_t);
        return bytes;
    }
    bool Current(const VectorIndex* heads) const
    {
        return m_ready && heads == m_heads && heads->GetHeadNodeMetaRevision() == m_revision;
    }

    bool Build(VectorIndex& heads, const LimitedTagSupport& support,
               const std::vector<int>& categorical, const std::vector<int>& numeric,
               const std::vector<SecondLevelHeadPostings>& postings,
               std::vector<Cache::NumQuantParam> authenticatedParams)
    {
        m_ready = false;
        m_layers.clear();
        params = std::move(authenticatedParams);
        m_heads = &heads;
        if (!heads.HasHeadNodeTailPS() || !heads.HasHeadNodePostingHierMasks() ||
            support.HeadCount() != heads.GetNumSamples() || support.AttributeCount() <= 0)
            return false;
        m_widths = heads.GetHeadNodeHierWidths();
        const std::size_t words = params.size() * Cache::NUM_QUANT_WORDS;
        for (SizeType head = 0; head < heads.GetNumSamples(); ++head) {
            const auto* own = support.HeadAttributes(head);
            const auto source = heads.GetHeadNodePostingHierMask(head);
            const auto* sourceTail = heads.GetHeadNodeTailPS(head);
            if (!own || !source || !sourceTail) return false;
            Cache::HierarchicalPostingMask pure;
            pure.Clear();
            source.CopyTo(pure, m_widths);
            auto tail = *sourceTail;
            for (int column : categorical) {
                if (column < 0 || column >= support.AttributeCount()) return false;
                pure.Insert(column, own[column], m_widths);
                tail.Insert(own[column]);
            }
            heads.SetHeadNodePostingHierMask(head, pure);
            heads.SetHeadNodeTailPS(head, tail);
            if (words) {
                auto* pureQuant = heads.GetHeadNodeNumQuantMutable(head);
                auto* tailQuant = heads.GetHeadNodeTailNumQuantMutable(head);
                if (!pureQuant || !tailQuant || numeric.size() != params.size()) return false;
                for (std::size_t lane = 0; lane < params.size(); ++lane) {
                    if (numeric[lane] < 0 || numeric[lane] >= support.AttributeCount()) return false;
                    const int bucket = Cache::NumQuantBucket(params[lane], own[numeric[lane]]);
                    Cache::NumQuantInsert(pureQuant, static_cast<int>(lane), bucket);
                    Cache::NumQuantInsert(tailQuant, static_cast<int>(lane), bucket);
                }
            }
        }
        m_layers.reserve(postings.size());
        for (std::size_t level = 0; level < postings.size(); ++level) {
            const auto& csr = postings[level];
            Layer layer;
            const std::size_t count = csr.SecondLevelHeadCount();
            layer.pure.resize(count);
            layer.tail.resize(count);
            layer.pureNumeric.resize(count * words);
            layer.tailNumeric.resize(count * words);
            for (SizeType upper = 0; upper < csr.SecondLevelHeadCount(); ++upper) {
                for (auto member = csr.Begin(upper); member != csr.End(upper); ++member) {
                    const SizeType lower = static_cast<SizeType>(*member);
                    const Cache::PostingBitmask* pure;
                    const Cache::PostingBitmask* tail;
                    const std::uint64_t* pureQuant;
                    const std::uint64_t* tailQuant;
                    if (level == 0) {
                        pure = heads.GetHeadNodePS(lower);
                        tail = heads.GetHeadNodeTailPS(lower);
                        pureQuant = heads.GetHeadNodeNumQuant(lower);
                        tailQuant = heads.GetHeadNodeTailNumQuant(lower);
                        const auto* own = support.HeadAttributes(lower);
                        if (!pure || !tail || !own) return false;
                        for (int column : categorical) layer.pure[upper].Insert(own[column]);
                    } else {
                        const auto& previous = m_layers.back();
                        pure = previous.Pure(lower);
                        tail = &previous.tail[lower];
                        pureQuant = words ? previous.pureNumeric.data() + lower * words : nullptr;
                        tailQuant = words ? previous.tailNumeric.data() + lower * words : nullptr;
                    }
                    layer.pure[upper].MergeOR(*pure);
                    layer.tail[upper].MergeOR(*tail);
                    for (std::size_t word = 0; word < words; ++word) {
                        layer.pureNumeric[upper * words + word] |= pureQuant[word];
                        layer.tailNumeric[upper * words + word] |= tailQuant[word];
                    }
                }
            }
            bool samePure = count != 0;
            for (SizeType head = 0; samePure && head < csr.SecondLevelHeadCount(); ++head) {
                const auto* source = csr.SignatureAt(head);
                samePure = source && std::memcmp(source, &layer.pure[head], sizeof(*source)) == 0;
            }
            if (samePure) {
                layer.sharedPure = csr.SignatureAt(0);
                std::vector<Cache::PostingBitmask>().swap(layer.pure);
            }
            m_layers.push_back(std::move(layer));
        }
        m_revision = heads.GetHeadNodeMetaRevision();
        m_ready = true;
        return true;
    }

    bool HeadMayMatch(const RoutingPredicate& predicate, SizeType head, bool pure) const
    {
        const auto* numeric = params.empty() ? nullptr : pure ? m_heads->GetHeadNodeNumQuant(head)
                                                            : m_heads->GetHeadNodeTailNumQuant(head);
        const auto columns = pure ? m_heads->GetHeadNodePostingHierMask(head)
                                  : HeadPostingMaskView{};
        return predicate.MayMatch(
            pure ? nullptr : m_heads->GetHeadNodeTailPS(head),
            columns, m_widths, numeric);
    }
    bool UpperMayMatch(const RoutingPredicate& predicate, std::size_t level,
                       SizeType head, bool pure) const
    {
        const auto& layer = m_layers[level];
        const auto& quant = pure ? layer.pureNumeric : layer.tailNumeric;
        return predicate.MayMatch(pure ? layer.Pure(head) : &layer.tail[head], nullptr, m_widths,
            params.empty() ? nullptr : quant.data() + head * params.size() * Cache::NUM_QUANT_WORDS);
    }
};
}}
