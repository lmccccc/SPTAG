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
inline std::vector<std::vector<int>> edges;
struct Observation {
    std::uint64_t calls = 0, adjacency = 0, shortcutCalls = 0;
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
    if (mode != "ordinary" && mode != "plain" && mode != "add8" && mode != "rewire8")
        throw std::runtime_error("Invalid shortcut policy");
    cap = Unsigned(get("shortcutcap"));
    if ((mode == "ordinary" && cap) || cap > 100000)
        throw std::runtime_error("Invalid shortcut cap");
    profile = Boolean(get("shortcutprofile"));
    capture = Boolean(get("shortcutcapture"));
    output = get("shortcutoutput");
    if (get("headnavigationmode") != "H1Only" || get("numberofthreads") != "1" ||
        get("internalresultnum") != "24" || get("dumpheads") != "0" ||
        get("logpathstats") != "false")
        throw std::runtime_error("Shortcut integration requires isolated unfiltered flat24 controls");
    for (const auto& p : parameters)
        if (p.first.rfind("shortcut", 0) == 0 &&
            p.first != "shortcutmode" && p.first != "shortcutcap" &&
            p.first != "shortcutprofile" && p.first != "shortcutcapture" &&
            p.first != "shortcutoutput" && p.first != "shortcutedges")
            throw std::runtime_error("Unknown shortcut control");
    LoadEdges(get("shortcutedges"));
}
}
