#pragma once
#include "NativeNeighborHooks.h"
#include <set>

namespace ShortcutFull {
inline std::string mode="ordinary",output;
inline bool profile=false,capture=false;
inline double postingThreshold=.05;
inline double twoHopThreshold=.5;
struct Observation {
    bool invoked=false;
    SPTAG::COMMON::NativeNeighborHooks native;
    std::vector<int> heads,ownIDs;
    std::vector<float> distances;
};
inline Observation& Last() { static thread_local Observation value; return value; }
template<class Parameters> void Configure(const Parameters& params) {
    for (const auto& p:params)
        if (p.first.rfind("arbitration",0)==0 || p.first.rfind("shortcut",0)==0 ||
            p.first.find("retainedratio")!=std::string::npos || p.first.find("minbasedegree")!=std::string::npos)
            throw std::runtime_error("Retired cost/degree control: "+p.first);
    std::set<std::string> consumed;
    const auto get=[&](const std::string& key)->const std::string& {
        const auto p=params.find(key);
        if (p==params.end()) throw std::runtime_error("Missing native parameter "+key);
        consumed.insert(key); return p->second;
    };
    mode=get("navixmode");
    if (mode!="navix" && mode!="posting") throw std::runtime_error("Invalid NaviX mode");
    const auto& flag=get("navixcapture");
    if (flag!="false") throw std::runtime_error("Ordinary timing requires NavixCapture=false");
    capture=false; profile=false;
    const auto& text=get("navixpostingthreshold");
    std::size_t used=0;
    postingThreshold=std::stod(text,&used);
    if (used!=text.size() || !std::isfinite(postingThreshold) || postingThreshold<0 || postingThreshold>.5)
        throw std::runtime_error("Invalid native posting threshold");
    const auto& twoHopText=get("navixtwohopthreshold");
    twoHopThreshold=std::stod(twoHopText,&used);
    if (used!=twoHopText.size() || !std::isfinite(twoHopThreshold) ||
        twoHopThreshold<=0 || twoHopThreshold>1 ||
        (mode=="posting" && postingThreshold>twoHopThreshold))
        throw std::runtime_error("Invalid native two-hop threshold or posting precedence");
    for (const auto& p:params)
        if (p.first.rfind("navix",0)==0 && !consumed.count(p.first))
            throw std::runtime_error("Unknown NaviX control: "+p.first);
    if (get("headnavigationmode")!="H1Only" || get("numberofthreads")!="1" ||
        get("enablehybriddistance")!="false" || get("enableadaptivefilterednprobe")!="false")
        throw std::runtime_error("NaviX requires fixed native H1 navigation");
}
}
