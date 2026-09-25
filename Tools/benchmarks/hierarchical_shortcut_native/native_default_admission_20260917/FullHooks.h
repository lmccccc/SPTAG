#pragma once
#include "NativeNeighborHooks.h"
#include <cmath>
#include <stdexcept>
#include <string>

namespace ShortcutFull {
inline std::string mode = "ordinary", output;
inline bool profile = false, capture = false;
inline double retainedRatio = 0.5;
inline int minBaseDegree = 16;
struct Observation {
    bool invoked = false;
    SPTAG::COMMON::NativeNeighborHooks native;
    std::vector<int> heads, ownIDs;
    std::vector<float> distances;
};
inline Observation& Last() { static thread_local Observation value; return value; }
inline bool Boolean(const std::string& text) {
    if (text == "true") return true;
    if (text == "false") return false;
    throw std::runtime_error("Invalid native injection Boolean");
}
template<class Parameters>
void Configure(const Parameters& parameters) {
    const auto get = [&](const char* key) -> const std::string& {
        const auto found = parameters.find(key);
        if (found == parameters.end()) throw std::runtime_error(std::string("Missing ") + key);
        return found->second;
    };
    mode = get("shortcutmode");
    if (mode != "ordinary" && mode != "supplier" && mode != "control")
        throw std::runtime_error("Native reuse has no legacy shortcut/scaffold mode");
    profile = Boolean(get("shortcutprofile")); capture = Boolean(get("shortcutcapture"));
    output = get("shortcutoutput");
    std::size_t used = 0;
    const auto& ratio = get("shortcutretainedratio");
    retainedRatio = std::stod(ratio, &used);
    if (used != ratio.size() || !std::isfinite(retainedRatio) || retainedRatio <= 0 || retainedRatio > 1)
        throw std::runtime_error("Invalid native retained ratio");
    const auto& minimum = get("shortcutminbasedegree");
    if (minimum.empty() || minimum.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("Invalid native minimum degree");
    minBaseDegree = std::stoi(minimum);
    if (minBaseDegree <= 0 || get("headnavigationmode") != "H1Only" ||
        get("numberofthreads") != "1" || get("dumpheads") != "0" || get("logpathstats") != "false")
        throw std::runtime_error("Invalid isolated native-reuse controls");
    for (const auto& p : parameters) {
        if (p.first.rfind("shortcut", 0) == 0 &&
            p.first != "shortcutmode" && p.first != "shortcutprofile" &&
            p.first != "shortcutcapture" && p.first != "shortcutoutput" &&
            p.first != "shortcutretainedratio" && p.first != "shortcutminbasedegree")
            throw std::runtime_error("Removed/unknown native-reuse control");
    }
}
}
