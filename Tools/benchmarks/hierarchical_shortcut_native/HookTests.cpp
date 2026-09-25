#include "ShortcutHooks.h"
#include <stdexcept>

int main() {
    int original[]{1,2,-1,-99}, out[64];
    std::vector<std::vector<int>> edges{{3,4,5}};
    auto require = [](bool ok) {if(!ok) throw std::runtime_error("adjacency fixture");};
    require(ShortcutBench::Neighbors(original,4,0,out) == 2 && out[0] == 1 && out[1] == 2);
    ShortcutBench::State state; state.edges = &edges;
    ShortcutBench::active = &state;
    require(ShortcutBench::Neighbors(original,4,0,out) == 5);
    state.rewire = true;
    require(ShortcutBench::Neighbors(original,4,0,out) == 2 && out[0] == 3 && out[1] == 4);
    edges[0].clear();
    require(ShortcutBench::Neighbors(original,4,0,out) == 2 && out[0] == 1 && out[1] == 2);
    original[0] = -1; edges[0] = {3,4};
    require(ShortcutBench::Neighbors(original,4,0,out) == 0);
    ShortcutBench::active = nullptr;
}
