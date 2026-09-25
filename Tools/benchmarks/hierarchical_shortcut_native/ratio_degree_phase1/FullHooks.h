#include "Supplier.h"
#include <array>
#pragma once
#include "ShortcutHooks.h"
#include <fstream>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <cstring>

namespace ShortcutFull {
inline std::string mode = "ordinary", output;
inline std::uint64_t cap = 0;
inline bool profile = false, capture = false;
inline double retainedRatio = 0.5;
inline int minBaseDegree = 16;
inline bool optimized = true;
inline std::vector<std::vector<int>> edges;
struct Observation {
    std::uint64_t supplyResetNs = 0, supplyDegreeNs = 0, supplyQualifyNs = 0, supplyDistanceNs = 0, supplyAdmitNs = 0, supplyHelperNs = 0, supplyStateClears = 0, supplyShortRows = 0, supplyQueueOffers = 0, supplyQueueAccepted = 0, supplyQueueRejected = 0, supplyChildQueueOffers = 0, supplyChildQueueRejected = 0, supplyDirectCalls = 0;
    std::uint64_t supplyConnChecks = 0, supplyConnLow = 0, supplyConnBefore = 0, supplyConnAfter = 0, supplyConnAdded = 0, supplyOrdinaryVisited = 0, supplyStartupConnected = 0;
    std::vector<H1Supplier::ConnectivityAudit> connectivityAudits;
    std::uint64_t supplyGraph = 0, supplyParent = 0, supplyChild = 0, supplyCache = 0, supplyRepeats = 0, supplyStarts = 0, supplyPops = 0, supplyExpansions = 0, supplySentinels = 0, supplyTrees = 0, supplyCalls = 0, supplyReturns = 0, supplyNeighbors = 0, supplyQueued = 0, supplyH2Rows = 0, supplyH3Rows = 0, supplyAscents = 0, supplyMembers = 0, supplyMaxMembers = 0, supplyMaxNeighbors = 0, supplyCandidates = 0, supplyEligible = 0, supplyTrace = 0, supplyRawBefore = 0, supplyEffectiveBefore = 0, supplyEffectiveAfter = 0, supplyTriggers = 0, supplyRequested = 0, supplyQualified = 0, supplyMaxQualified = 0, supplyFills = 0, supplyH2Fills = 0, supplyH2Exhausted = 0, supplyNativeStops = 0, supplyScopeStops = 0, supplyRouting = 0, supplySignatureChecks = 0, supplySignatureRejects = 0, supplySignatureCache = 0, supplyPredicateChecks = 0, supplyPredicateRejects = 0, supplyPredicateCache = 0, supplyPostingSkips = 0, supplyH2Completed = 0, supplyH3Completed = 0, supplyNativeMaxCheck = 0, supplyNativeOvershoot = 0, supplyContinuationPops = 0, supplyRejectedLeaves = 0, supplyUpperMembers = 0, supplyLowerMembers = 0, supplyDiscovered = 0, supplyDiscoveryReuse = 0, supplyEntryCandidates = 0, supplyEntryCalls = 0, supplyEntryBefore = 0, supplyStartupChecks = 0, supplyStartupCalls = 0, supplyStartupBlocked = 0, supplyStartupBefore = 0, supplyStartupAfter = 0, supplyStartupQualified = 0;
    std::vector<int> ownIDs, parentEvaluated;
    std::vector<std::pair<int, float>> evaluated;
    std::vector<int> qualifications;
    std::vector<std::array<std::uint64_t, 9>> degreeAudits;
    std::vector<std::array<std::uint64_t, 4>> rowAudits;
    std::vector<std::array<int, 2>> rejectedRows;
    std::vector<int> predicateRejected, entryIDs;
    std::vector<std::pair<int, float>> entryScored;
    std::vector<std::array<int, 2>> discoveries;
    std::vector<std::array<std::int64_t, 8>> startupAudits;
    std::uint64_t calls = 0, adjacency = 0, shortcutCalls = 0;
    std::uint64_t csrDistances = 0, csrAssignments = 0, graphChecked = 0;
    bool exhausted = false, invoked = false;
    std::vector<int> heads;
    std::vector<float> distances;
};
inline Observation& Last() {
    static thread_local Observation value;
    return value;
}

inline std::uint64_t Unsigned(const std::string& value) {
    if (value.empty() || value.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("Invalid unsigned shortcut control");
    return std::stoull(value);
}
inline bool Boolean(const std::string& value) {
    if (value == "true") return true;
    if (value == "false") return false;
    throw std::runtime_error("Invalid boolean shortcut control");
}
inline void LoadEdges(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    auto read = [&](auto* p, std::size_t n) {
        if (!in.read(reinterpret_cast<char*>(p), n * sizeof(*p)))
            throw std::runtime_error("Truncated shortcut overlay");
    };
    char magic[8]; int n, bound;
    read(magic, 8); read(&n, 1); read(&bound, 1);
    if (std::memcmp(magic, "H13EDGE1", 8) || n != 160091 || bound != 8)
        throw std::runtime_error("Wrong SIFT1M shortcut overlay");
    std::vector<std::uint64_t> offsets(n + 1);
    read(offsets.data(), offsets.size());
    if (offsets[0] || offsets.back() > std::uint64_t(n) * 8)
        throw std::runtime_error("Invalid shortcut offsets");
    edges.assign(n, {});
    for (int i = 0; i < n; ++i) {
        if (offsets[i + 1] < offsets[i] || offsets[i + 1] - offsets[i] > 8)
            throw std::runtime_error("Invalid shortcut degree");
        auto& row = edges[i];
        row.resize(offsets[i + 1] - offsets[i]);
        read(row.data(), row.size());
        for (std::size_t j = 0; j < row.size(); ++j)
            if (row[j] < 0 || row[j] >= n || row[j] == i ||
                std::find(row.begin(), row.begin() + j, row[j]) != row.begin() + j)
                throw std::runtime_error("Invalid shortcut destination");
    }
    if (in.peek() != std::ifstream::traits_type::eof())
        throw std::runtime_error("Trailing shortcut data");
}
template<class Parameters>
void Configure(const Parameters& parameters) {
    const auto get = [&](const char* key) -> const std::string& {
        auto it = parameters.find(key);
        if (it == parameters.end()) throw std::runtime_error(std::string("Missing ") + key);
        return it->second;
    };
    mode = get("shortcutmode");
    if (mode != "ordinary" && mode != "plain" && mode != "add8" && mode != "rewire8" && mode != "hierarchy" && mode != "supplier" && mode != "control")
        throw std::runtime_error("Invalid shortcut policy");

    if (mode == "supplier" || mode == "control") {
        if (parameters.count("shortcutcap") || parameters.count("shortcutmemberlimit"))
            throw std::runtime_error("Removed supplier budgets must not be supplied");
    } else cap = Unsigned(get("shortcutcap"));
    if (((mode == "ordinary" || mode == "hierarchy") && cap) || cap > 100000)
        throw std::runtime_error("Invalid shortcut cap");
    profile = Boolean(get("shortcutprofile"));
    capture = Boolean(get("shortcutcapture"));
    output = get("shortcutoutput");
    if (get("headnavigationmode") != (mode == "hierarchy" ? "H2Only" : "H1Only") || get("numberofthreads") != "1" ||
        Unsigned(get("internalresultnum")) < Unsigned(get("resultnum")) || get("dumpheads") != "0" ||
        get("logpathstats") != "false")
        throw std::runtime_error("Shortcut integration requires isolated native controls and capacity >= ResultNum");
    for (const auto& p : parameters)
        if (p.first.rfind("shortcut", 0) == 0 &&
            p.first != "shortcutmode" && p.first != "shortcutcap" &&
            p.first != "shortcutprofile" && p.first != "shortcutcapture" &&
            p.first != "shortcutoutput" && p.first != "shortcutedges" &&
            p.first != "shortcutretainedratio" && p.first != "shortcutminbasedegree" &&
            p.first != "shortcuthotpath")
            throw std::runtime_error("Unknown shortcut control");

    if (mode == "supplier" || mode == "control") {
        std::size_t consumed = 0;
        const auto& ratio = get("shortcutretainedratio");
        retainedRatio = std::stod(ratio, &consumed);
        const auto minimum = Unsigned(get("shortcutminbasedegree"));
        if (consumed != ratio.size() || !std::isfinite(retainedRatio) ||
            retainedRatio <= 0 || retainedRatio > 1 || minimum == 0 ||
            minimum > static_cast<std::uint64_t>((std::numeric_limits<int>::max)()))
            throw std::runtime_error("Invalid retained ratio or minimum physical degree");
        minBaseDegree = static_cast<int>(minimum);
        const auto& hotpath = get("shortcuthotpath");
        if (hotpath != "optimized" && hotpath != "reference")
            throw std::runtime_error("Invalid phase1 hot-path implementation");
        optimized = hotpath == "optimized";
    } else LoadEdges(get("shortcutedges"));
}
}
