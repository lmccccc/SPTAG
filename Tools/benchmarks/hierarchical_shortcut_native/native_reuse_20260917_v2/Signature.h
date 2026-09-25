#pragma once
#include "inc/Core/SPANN/SecondLevelHierarchy.h"

namespace NativeReuse {
inline bool PostingMayMatch(const SPTAG::Cache::PostingBitmask& query,
                            const SPTAG::SPANN::SecondLevelHeadPostings& layer, int id) {
    if (id < 0 || id >= layer.SecondLevelHeadCount())
        throw std::runtime_error("Invalid signature posting ID");
    if (!query.Popcount()) return true;
    const auto* signature = layer.SignatureAt(id);
    if (!signature) throw std::runtime_error("Authenticated posting signature missing");
    return signature->MayIntersect(query);
}
}
