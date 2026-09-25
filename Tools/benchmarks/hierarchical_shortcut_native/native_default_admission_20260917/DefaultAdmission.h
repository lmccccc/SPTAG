#pragma once
#include <cmath>

namespace NativeReuse {
template<class ValidOwnPoint>
bool AllHeadsOwnValid(int heads, ValidOwnPoint valid) {
    if (heads <= 0) return false;
    for (int head = 0; head < heads; ++head)
        if (!valid(head)) return false;
    return true;
}

inline bool DefaultAdmissionAllowed(bool exactPredicate, bool immutable,
                                    bool allOwnValid, int nprobe, int topk,
                                    double retainedRatio, int minimumDegree) {
    return !exactPredicate && immutable && allOwnValid && topk > 0 && nprobe >= topk &&
        minimumDegree > 0 && std::isfinite(retainedRatio) &&
        retainedRatio > 0 && retainedRatio <= 1;
}
}
