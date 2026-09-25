#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace Postfilter {
inline void ValidateRatio(double ratio) {
    if (!std::isfinite(ratio) || (ratio!=0 && ratio!=.01))
        throw std::runtime_error("PostingNeighborMatchRatio must be 0 or fixed 0.01");
}
struct Work {
    std::uint64_t members=0, fresh=0, predicateChecks=0, predicatePasses=0, useful=0;
    Work& operator+=(const Work& w) {
        members+=w.members; fresh+=w.fresh; predicateChecks+=w.predicateChecks;
        predicatePasses+=w.predicatePasses; useful+=w.useful; return *this;
    }
};
struct Decision {
    int head=-1,checks=0,passes=0,complete=0,activated=0,checkedBefore=0,checkedAfter=0;
    int degree=0,eligible=0,eligibleVisited=0,oracleDegree=0,oracleEligible=0;
    std::uint64_t signatures=0,representatives=0,members=0;
};
}
