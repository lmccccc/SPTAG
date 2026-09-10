#pragma once

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace SPTAG {
// Columns and query literals always retain their original input indices.
class TagSchema {
public:
    std::vector<int> categorical;
    std::vector<int> numeric;
    std::vector<int> numericLanes;
    std::string text;

    static TagSchema Parse(const std::string& value, int width = -1,
                           int legacyCategorical = 0) {
        TagSchema result;
        if (value.empty()) {
            if (width < 0 || legacyCategorical < 0 || legacyCategorical > width)
                throw std::invalid_argument("Invalid tag schema width");
            const int count = legacyCategorical > 0 ? legacyCategorical : width;
            for (int i = 0; i < width; ++i) result.Append(i < count);
        } else {
            size_t start = 0;
            do {
                const size_t end = value.find(',', start);
                std::string type = value.substr(start, end - start);
                const auto first = type.find_first_not_of(" \t\r\n");
                const auto last = type.find_last_not_of(" \t\r\n");
                type = first == std::string::npos ? "" : type.substr(first, last - first + 1);
                std::transform(type.begin(), type.end(), type.begin(),
                    [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
                if (type == "categorical" || type == "cate") result.Append(true);
                else if (type == "numeric" || type == "num") result.Append(false);
                else throw std::invalid_argument("ColumnTypes requires categorical or numeric for every column");
                if (end == std::string::npos) break;
                start = end + 1;
            } while (true);
            if (width >= 0 && result.Width() != width)
                throw std::invalid_argument("ColumnTypes width disagrees with NumTagsPerVec");
        }
        return result;
    }

    int Width() const { return static_cast<int>(numericLanes.size()); }
    bool IsCategorical(int column) const {
        return column >= 0 && column < Width() && numericLanes[column] < 0;
    }
    int NumericLane(int column) const {
        return column >= 0 && column < Width() ? numericLanes[column] : -1;
    }
    std::string Fingerprint() const {
        std::uint64_t hash = 1469598103934665603ULL;
        // Version 1: canonical types in original column order.
        for (unsigned char c : std::string("TagSchema1:") + text) {
            hash ^= c;
            hash *= 1099511628211ULL;
        }
        return std::to_string(hash);
    }
private:
    void Append(bool category) {
        const int column = Width();
        if (!text.empty()) text += ',';
        text += category ? "categorical" : "numeric";
        numericLanes.push_back(category ? -1 : static_cast<int>(numeric.size()));
        (category ? categorical : numeric).push_back(column);
    }
};
}
