#pragma once
#include "NativeNeighborHooks.h"
#include <set>

namespace ShortcutFull {
inline std::string mode="graph",output;
inline bool profile=false,capture=false;
enum class DiagnosticAdmission { Original, Shortcut, PredicateBeforeShortcut };
inline DiagnosticAdmission diagnosticAdmission=DiagnosticAdmission::Original;
inline thread_local int immutableQueries=0;
struct SnapshotScope {
    bool enabled;
    explicit SnapshotScope(bool value):enabled(value) { if(enabled) ++immutableQueries; }
    ~SnapshotScope() { if(enabled) --immutableQueries; }
};
inline void RequireMutable() {
    if (mode!="graph" || immutableQueries)
        throw std::runtime_error("VisitedMatchMode requires immutable index; mutation is unsupported");
}
inline double activationRatio=.01;
struct Observation {
    bool invoked=false;
    SPTAG::COMMON::NativeNeighborHooks native;
    std::vector<int> heads,ownIDs;
    std::vector<float> distances;
    std::uint64_t selectedHeads=0,matchingHeads=0;
    std::vector<int> nativeSelectedHeadRecords;
};
inline Observation& Last() { static thread_local Observation value; return value; }
template<class Parameters> void Configure(const Parameters& params) {
    for (const auto& p:params)
        if (p.first.rfind("navix",0)==0 || p.first.find("twohop")!=std::string::npos ||
            p.first.rfind("arbitration",0)==0 || p.first.rfind("shortcut",0)==0 ||
            p.first.find("degree")!=std::string::npos || p.first.find("retainedratio")!=std::string::npos ||
            p.first=="observe" || p.first=="postfilterpostingmode" || p.first=="postingactivationratio" ||
            p.first=="nativepostfiltermode" || p.first=="postingfilterhitratio" ||
            p.first.rfind("cost",0)==0)
            throw std::runtime_error("Retired native control: "+p.first);
    const auto get=[&](const std::string& key)->const std::string& {
        const auto p=params.find(key);
        if (p==params.end()) throw std::runtime_error("Missing native parameter "+key);
        return p->second;
    };
    mode=get("visitedmatchmode");
    if (mode!="graph" && mode!="match" && mode!="posting")
        throw std::runtime_error("Invalid VisitedMatchMode");
    const auto& admission=get("diagnosticadmission");
    if (admission=="original") diagnosticAdmission=DiagnosticAdmission::Original;
    else if (admission=="shortcut") diagnosticAdmission=DiagnosticAdmission::Shortcut;
    else if (admission=="predicate-before-shortcut")
        diagnosticAdmission=DiagnosticAdmission::PredicateBeforeShortcut;
    else throw std::runtime_error("Invalid diagnostic-only admission control");
    if (diagnosticAdmission!=DiagnosticAdmission::Original && mode!="graph")
        throw std::runtime_error("Diagnostic admission ablation is graph-only");
    const auto& text=get("postingneighbormatchratio");
    std::size_t used=0;
    activationRatio=std::stod(text,&used);
    if (used!=text.size() || activationRatio!=.01) throw std::runtime_error("PostingNeighborMatchRatio must be fixed 0.01");
    Postfilter::ValidateRatio(activationRatio);
    capture=profile=false;
    if (get("headnavigationmode")!="H1Only" || get("numberofthreads")!="1" ||
        get("enablehybriddistance")!="false" || get("enableadaptivefilterednprobe")!="false")
        throw std::runtime_error("Postfilter posting requires fixed native H1 navigation");
}
}
