#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace Navix {
enum Route { NoNeighbors=0, OneHop=1, Directed=2, FullTwoHop=3, Posting=4 };
inline void ValidateThresholds(double twoHopThreshold,double postingThreshold,bool postingEnabled) {
    if (!std::isfinite(twoHopThreshold) || twoHopThreshold<=0 || twoHopThreshold>1)
        throw std::runtime_error("Invalid native two-hop threshold: require finite 0 < threshold <= 1");
    if (!std::isfinite(postingThreshold) || postingThreshold<0 || postingThreshold>.5)
        throw std::runtime_error("Invalid native posting threshold: require finite 0 <= threshold <= 0.5");
    if (postingEnabled && postingThreshold>=twoHopThreshold)
        throw std::runtime_error("Invalid native posting precedence: active posting threshold must be strictly less than two-hop threshold");
}
inline Route GraphRouteValidated(int d,int e,double twoHopThreshold) {
    if (!d) return NoNeighbors;
    if (double(e)/d>=twoHopThreshold) return OneHop;
    return .4*(double(d)*e+e)>2.0*d-e ? Directed : FullTwoHop;
}
inline Route GraphRoute(int d,int e,double twoHopThreshold=.5) {
    ValidateThresholds(twoHopThreshold,0,false);
    return GraphRouteValidated(d,e,twoHopThreshold);
}
inline Route RouteValidated(int d,int e,double twoHopThreshold,double postingThreshold,bool postingEnabled) {
    const auto graph=GraphRouteValidated(d,e,twoHopThreshold);
    if (graph==NoNeighbors || graph==OneHop) return graph;
    return postingEnabled && double(e)/d<postingThreshold ? Posting : graph;
}
inline Route SelectRoute(int d,int e,double twoHopThreshold,double postingThreshold,bool postingEnabled=true) {
    ValidateThresholds(twoHopThreshold,postingThreshold,postingEnabled);
    return RouteValidated(d,e,twoHopThreshold,postingThreshold,postingEnabled);
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
