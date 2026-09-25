#pragma once
#include "NativeNeighborHooks.h"
#include <set>

namespace ShortcutFull {
inline std::string mode="graph",output;
inline bool profile=false,capture=false;
inline double activationRatio=.01;
inline bool Gather() { return mode=="observe" || mode=="posting"; }
struct Observation {
    bool invoked=false;
    SPTAG::COMMON::NativeNeighborHooks native;
    std::vector<int> heads,ownIDs;
    std::vector<float> distances;
};
inline Observation& Last() { static thread_local Observation value; return value; }
template<class Parameters> void Configure(const Parameters& params) {
    for (const auto& p:params)
        if (p.first.rfind("navix",0)==0 || p.first.find("twohop")!=std::string::npos ||
            p.first.rfind("arbitration",0)==0 || p.first.rfind("shortcut",0)==0 ||
            p.first.find("degree")!=std::string::npos || p.first.find("retainedratio")!=std::string::npos)
            throw std::runtime_error("Retired native control: "+p.first);
    const auto get=[&](const std::string& key)->const std::string& {
        const auto p=params.find(key);
        if (p==params.end()) throw std::runtime_error("Missing native parameter "+key);
        return p->second;
    };
    mode=get("postfilterpostingmode");
    if (mode!="graph" && mode!="observe" && mode!="posting")
        throw std::runtime_error("Invalid PostFilterPostingMode");
    const auto& text=get("postingactivationratio");
    std::size_t used=0;
    activationRatio=std::stod(text,&used);
    if (used!=text.size()) throw std::runtime_error("Invalid PostingActivationRatio");
    Postfilter::ValidateRatio(activationRatio);
    capture=profile=false;
    if (get("headnavigationmode")!="H1Only" || get("numberofthreads")!="1" ||
        get("enablehybriddistance")!="false" || get("enableadaptivefilterednprobe")!="false")
        throw std::runtime_error("Postfilter posting requires fixed native H1 navigation");
}
}
