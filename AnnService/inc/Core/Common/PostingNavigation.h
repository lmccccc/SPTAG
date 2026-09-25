// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#pragma once
#include <cstdint>
#include <functional>
#include <stdexcept>
#include <vector>

namespace SPTAG { namespace COMMON {
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
    virtual void Expand(const std::vector<int>& heads, const Consumer& consume) = 0;
    void Expand(int head, const Consumer& consume) { Expand(std::vector<int>{head}, consume); }
    virtual ~PostingNavigation() = default;
};
}}
