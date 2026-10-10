// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <functional>
#include <stdexcept>
#include <utility>
#include <vector>

namespace SPTAG { namespace COMMON {
class PostingAnchorCandidates
{
    using Candidate = std::pair<float, int>;
    std::size_t m_limit;
    std::vector<Candidate> m_candidates;
    Candidate m_bound;

    void Compact() {
        if (m_candidates.size() > m_limit) {
            std::nth_element(m_candidates.begin(), m_candidates.begin() + m_limit, m_candidates.end());
            m_candidates.resize(m_limit);
            m_bound = *std::max_element(m_candidates.begin(), m_candidates.end());
        }
    }
public:
    PostingAnchorCandidates(int limit, int reserve) : m_limit(limit) {
        if (limit < 0 || reserve < 0 || m_limit > m_candidates.max_size() / 2)
            throw std::invalid_argument("Invalid posting anchor capacity");
        m_candidates.reserve((std::min)(2 * m_limit, static_cast<std::size_t>(reserve)));
    }
    void Add(int id, float distance) {
        if (!m_limit || !std::isfinite(distance)) return;
        const Candidate candidate(distance, id);
        if (m_candidates.size() >= m_limit && !(candidate < m_bound)) return;
        m_candidates.push_back(candidate);
        if (m_candidates.size() == m_limit)
            m_bound = *std::max_element(m_candidates.begin(), m_candidates.end());
        else if (m_candidates.size() == 2 * m_limit)
            Compact();
    }
    std::size_t Size() const { return (std::min)(m_limit, m_candidates.size()); }
    bool Empty() const { return m_candidates.empty(); }
    const std::vector<Candidate>& Sorted() {
        Compact();
        std::sort(m_candidates.begin(), m_candidates.end());
        return m_candidates;
    }
};

class PostingNavigation
{
public:
    struct RowResult {
        unsigned degree = 0;
        unsigned eligible = 0;
        // The consumer's work budget, independent of whether its result heap is full.
        bool canContinue = true;
        // Fresh matching H1 neighbors, independent of result-heap acceptance.
        unsigned newCandidates = 0;
        // Result status only; a full heap can still improve while canContinue is true.
        bool targetFilled = false;

        bool Sparse() const {
            return degree > 0 && static_cast<std::uint64_t>(eligible) * 100 < degree;
        }
    };
    using Consumer = std::function<RowResult(const std::uint32_t*, int)>;
    virtual void SetSearchCapacity(int capacity) {
        if (capacity <= 0) throw std::invalid_argument("Posting search capacity must be positive");
    }
    virtual bool Converged() const { return false; }
    virtual bool PreferResultAnchors() const { return false; }
    virtual void Expand(const std::vector<int>& heads, const Consumer& consume) = 0;
    void Expand(int head, const Consumer& consume) { Expand(std::vector<int>{head}, consume); }
    virtual ~PostingNavigation() = default;
};
}}
