#pragma once

#include <charconv>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

namespace NativeNProbeSweep {

inline std::string Trim(const std::string& text)
{
    const auto first = text.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return {};
    return text.substr(first, text.find_last_not_of(" \t\r\n") - first + 1);
}

inline std::vector<int> Parse(const std::string& input, int topk)
{
    std::string text = Trim(input);
    if (!text.empty() && text.front() == '[' && text.back() == ']') {
        text = text.substr(1, text.size() - 2);
    }
    std::vector<int> probes;
    std::unordered_set<int> seen;
    std::size_t begin = 0;
    while (true) {
        const auto end = text.find(',', begin);
        const auto token = Trim(text.substr(begin, end == std::string::npos ? end : end - begin));
        int probe = 0;
        const auto parsed = std::from_chars(token.data(), token.data() + token.size(), probe);
        if (parsed.ec != std::errc() || parsed.ptr != token.data() + token.size() ||
            probe <= 0 || probe < topk || !seen.insert(probe).second) {
            throw std::invalid_argument(
                "Invalid [SearchSweep] NProbe: require distinct positive integers >= topk");
        }
        probes.push_back(probe);
        if (end == std::string::npos) break;
        begin = end + 1;
    }
    return probes;
}

} // namespace NativeNProbeSweep
