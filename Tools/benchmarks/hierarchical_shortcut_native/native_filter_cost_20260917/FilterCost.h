#pragma once
#include "FullHooks.h"
#include <cstdint>

namespace FilterCost {
struct Counters {
    bool exactPredicate = false, numericPredicate = false, observer = false;
    std::uint64_t posting = 0, traversal = 0, own = 0, exactHead = 0;
    std::uint64_t degreeRows = 0, physicalMembers = 0;
};
inline Counters& Last() { static thread_local Counters value; return value; }
inline void Count(std::uint64_t Counters::*field) {
    if (ShortcutFull::capture) ++(Last().*field);
}
}
