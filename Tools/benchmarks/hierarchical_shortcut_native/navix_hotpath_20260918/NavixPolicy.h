#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace Navix {
enum Route { NoNeighbors=0, OneHop=1, Directed=2, FullTwoHop=3, Posting=4 };
inline Route GraphRoute(int d,int e) {
    if (!d) return NoNeighbors;
    if (double(e)/d>=.5) return OneHop;
    return .4*(double(d)*e+e)>2.0*d-e ? Directed : FullTwoHop;
}
struct Work {
    std::uint64_t members=0, fresh=0, predicateChecks=0, predicatePasses=0, useful=0;
    Work& operator+=(const Work& w) {
        members+=w.members; fresh+=w.fresh; predicateChecks+=w.predicateChecks;
        predicatePasses+=w.predicatePasses; useful+=w.useful; return *this;
    }
};
struct Decision {
    int head=-1,d=0,e=0,route=0,fallback=0,checkedBefore=0,checkedAfter=0;
    std::uint64_t signatures=0,representatives=0,members=0;
};
}
