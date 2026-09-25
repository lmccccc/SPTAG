#pragma once
#include <algorithm>
#include <cstdint>
#include <vector>

// Isolated experiment state, never compiled into the production library.
namespace ShortcutBench {
struct BudgetExhausted {};
struct State {
    const std::vector<std::vector<int>>* edges = nullptr;
    bool rewire = false;
    std::uint64_t remaining = 0, used = 0, adjacency = 0, shortcutDistances = 0;
    bool profile = false, inShortcut = false, exhausted = false;
};
inline thread_local State* active = nullptr;
inline int Neighbors(const int* original, int width, int source, int* output) {
    int degree = 0;
    while (degree < width && original[degree] >= 0) ++degree;
    const auto* extras = active && active->edges ? &(*active->edges)[source] : nullptr;
    int extra = extras ? static_cast<int>(extras->size()) : 0;
    if (active && active->rewire) {
        extra = std::min(degree, extra);
        degree -= extra;
    }
    int count = 0, a = 0, b = 0;
    while (a < degree || b < extra) {
        for (int j = 0; j < 4 && a < degree; ++j) output[count++] = original[a++];
        if (b < extra) output[count++] = (*extras)[b++];
    }
    return count;
}
}
