#pragma once
#include "NativeNeighborHooks.h"
#include <set>

namespace ShortcutFull {
inline std::string mode="ordinary", output;
inline bool profile=false, capture=false;
inline CostArbitration::Config costs;
struct Observation {
    bool invoked=false;
    SPTAG::COMMON::NativeNeighborHooks native;
    std::vector<int> heads,ownIDs;
    std::vector<float> distances;
};
inline Observation& Last() { static thread_local Observation value; return value; }
template<class Parameters> void Configure(const Parameters& params) {
    for (const auto& p : params)
        if (p.first.rfind("shortcut",0)==0 || p.first.find("retainedratio")!=std::string::npos ||
            p.first.find("minbasedegree")!=std::string::npos)
            throw std::runtime_error("Degree/deficit controls are invalid for cost arbitration");
    std::set<std::string> consumed;
    const auto get=[&](const std::string& key) -> const std::string& {
        const auto p=params.find(key);
        if (p==params.end()) throw std::runtime_error("Missing native parameter "+key);
        consumed.insert(key);
        return p->second;
    };
    mode=get("arbitrationmode");
    if (mode!="auto" && mode!="graph" && mode!="estimate_only")
        throw std::runtime_error("Invalid arbitration mode");
    const auto flag=get("arbitrationcapture");
    if (flag!="true" && flag!="false") throw std::runtime_error("Invalid capture Boolean");
    capture=flag=="true"; profile=false;
    const auto number=[&](const char* name) {
        const auto& text=get(name);
        std::size_t n=0;
        const double value=std::stod(text,&n);
        if (n!=text.size() || !std::isfinite(value)) throw std::runtime_error("Invalid native cost");
        return value;
    };
    costs.graphSetup=number("arbitrationgraphsetupcost");
    costs.postingSetup=number("arbitrationpostingsetupcost");
    costs.member=number("arbitrationmembercost");
    costs.distance=number("arbitrationdistancecost");
    costs.predicate=number("arbitrationpredicatecost");
    costs.signature=number("arbitrationsignaturecost");
    costs.weight=number("arbitrationpriorweight");
    costs.graphNovelty=number("arbitrationgraphnoveltyprior");
    costs.postingNovelty=number("arbitrationpostingnoveltyprior");
    costs.discoveryNovelty=number("arbitrationdiscoverynoveltyprior");
    costs.predicatePrior=number("arbitrationpredicateprior");
    costs.competitivePrior=number("arbitrationcompetitiveprior");
    costs.Validate();
    for (const auto& p : params)
        if (p.first.rfind("arbitration",0)==0 && !consumed.count(p.first))
            throw std::runtime_error("Unknown arbitration parameter "+p.first);
    if (get("headnavigationmode")!="H1Only" || get("numberofthreads")!="1" ||
        get("enablehybriddistance")!="false" || get("enableadaptivefilterednprobe")!="false")
        throw std::runtime_error("Arbitration requires fixed native post-filter navigation");
}
}
