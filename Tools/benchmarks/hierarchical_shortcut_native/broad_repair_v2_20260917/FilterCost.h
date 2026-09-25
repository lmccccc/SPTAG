#pragma once
#include "FullHooks.h"
#include <cstdint>
#include <unordered_set>

namespace FilterCost {
struct Counters {
    bool exactPredicate = false, numericPredicate = false, observer = false;
    std::uint64_t posting = 0, traversal = 0, own = 0, exactHead = 0;
    std::uint64_t degreeRows = 0, physicalMembers = 0;
    std::uint64_t qualifications = 0, uniqueQualifications = 0, repeatedQualifications = 0;
    std::unordered_set<int> qualified;
};
inline Counters& Last() { static thread_local Counters value; return value; }
inline void Count(std::uint64_t Counters::*field) {
    if (ShortcutFull::capture) ++(Last().*field);
}
inline void Qualification(int id) {
    if (!ShortcutFull::capture) return;
    auto& out = Last();
    ++out.qualifications;
    if (out.qualified.insert(id).second) ++out.uniqueQualifications;
    else ++out.repeatedQualifications;
}
}
