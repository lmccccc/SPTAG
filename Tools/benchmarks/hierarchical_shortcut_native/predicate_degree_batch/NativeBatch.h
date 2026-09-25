#pragma once
#include <charconv>
#include <cstdint>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace NativeBatch {
inline std::uint64_t indexLoadCalls = 0;
inline std::uint64_t workspaceResets = 0;

inline std::string Trim(const std::string& text) {
    const auto first = text.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    return text.substr(first, text.find_last_not_of(" \t\r\n") - first + 1);
}
inline int Positive(const std::string& input) {
    const auto text = Trim(input);
    int result = 0;
    if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("Expected a positive decimal integer: " + input);
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), result);
    if (parsed.ec != std::errc() || parsed.ptr != text.data() + text.size() || result <= 0)
        throw std::runtime_error("Invalid or overflowing positive integer: " + input);
    return result;
}
inline std::vector<int> Probes(const std::string& input, int resultNum) {
    if (resultNum <= 0) throw std::runtime_error("ResultNum must be positive");
    auto text = Trim(input);
    if (!text.empty() && text.front() == '[') {
        if (text.back() != ']') throw std::runtime_error("Unclosed nprobe array");
        text = Trim(text.substr(1, text.size() - 2));
    }
    if (text.empty()) throw std::runtime_error("Empty nprobe array");
    std::vector<int> probes;
    std::set<int> seen;
    std::size_t start = 0;
    for (;;) {
        const auto end = text.find(',', start);
        const int probe = Positive(text.substr(start, end == std::string::npos ? end : end - start));
        if (probe < resultNum) throw std::runtime_error("nprobe capacity is below ResultNum");
        if (!seen.insert(probe).second) throw std::runtime_error("Duplicate nprobe in array");
        probes.push_back(probe);
        if (end == std::string::npos) break;
        start = end + 1;
    }
    return probes;
}
}
