// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#pragma once

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <queue>
#include <sstream>
#include <string>
#include <system_error>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace SPTAG {
namespace PredicateWorkload {

enum class KeyKind {
    Label,
    Hierarchy,
    Range,
};

struct SyntheticKeyOptions {
    std::string keyAttribute;
    std::string sourceId;
    std::vector<int> keyColumns;
    KeyKind keyKind = KeyKind::Label;
    std::size_t samplesPerColumn = 16;
    std::size_t rangeSampleRows = 65536;
    std::uint64_t seed = 20260813;
};

struct TrainSetSummary {
    bool reusedExisting = false;
    std::uint64_t vectorCount = 0;
    std::size_t sampledPredicates = 0;
    std::vector<std::size_t> sampledDomainValuesPerColumn;
};

inline bool SetError(std::string* error, const std::string& message)
{
    if (error != nullptr) {
        *error = message;
    }
    return false;
}

inline const char* KeyKindName(KeyKind kind)
{
    switch (kind) {
    case KeyKind::Label:
        return "label";
    case KeyKind::Hierarchy:
        return "hierarchy";
    case KeyKind::Range:
        return "range";
    }
    return "unknown";
}

inline std::uint64_t Mix64(std::uint64_t value)
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

inline std::string KeyColumnsText(const std::vector<int>& columns)
{
    std::ostringstream output;
    for (std::size_t i = 0; i < columns.size(); ++i) {
        if (i != 0) output << ',';
        output << columns[i];
    }
    return output.str();
}

inline bool ReuseExistingTrainSet(const std::string& path,
                                  std::uint64_t vectorCount,
                                  const SyntheticKeyOptions& options,
                                  TrainSetSummary* summary,
                                  std::string* error)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return SetError(error, "cannot read existing predicate train set: " + path);
    }

    std::string firstLine;
    if (!std::getline(input, firstLine)) {
        return SetError(error, "existing predicate train set is empty: " + path);
    }

    // Opaque external workloads are intentionally accepted unchanged. Files
    // produced by this generator carry enough metadata to prevent accidentally
    // reusing a smoke workload or a workload generated for a different key.
    if (firstLine != "# sptag_predicate_train_set_v1") {
        if (firstLine.rfind("# sptag_predicate_train_set_v", 0) == 0) {
            return SetError(
                error,
                "existing generated predicate train set uses an incompatible format "
                "(remove it, choose another TrainSetFile, or provide an external workload): " +
                    path);
        }
        if (summary != nullptr) {
            summary->reusedExisting = true;
        }
        return true;
    }

    std::unordered_map<std::string, std::string> metadata;
    std::string line;
    while (std::getline(input, line)) {
        if (line.rfind("# ", 0) != 0) break;
        const std::size_t separator = line.find('=', 2);
        if (separator == std::string::npos) continue;
        metadata.emplace(line.substr(2, separator - 2), line.substr(separator + 1));
    }

    const std::vector<std::pair<std::string, std::string>> expected = {
        {"source", "synthetic_uniform_key"},
        {"source_id", options.sourceId},
        {"key_kind", KeyKindName(options.keyKind)},
        {"key_columns", KeyColumnsText(options.keyColumns)},
        {"vector_count", std::to_string(vectorCount)},
        {"samples_per_key_column", std::to_string(options.samplesPerColumn)},
        {"seed", std::to_string(options.seed)},
    };
    for (const auto& item : expected) {
        const auto found = metadata.find(item.first);
        if (found == metadata.end() || found->second != item.second) {
            return SetError(
                error,
                "existing generated predicate train set has incompatible " + item.first +
                    " (remove it, choose another TrainSetFile, or use a matching config): " +
                    path);
        }
    }
    if (options.keyKind == KeyKind::Range) {
        const auto found = metadata.find("range_sample_rows");
        if (found == metadata.end() ||
            found->second != std::to_string(options.rangeSampleRows)) {
            return SetError(
                error,
                "existing generated predicate train set has incompatible range_sample_rows "
                "(remove it, choose another TrainSetFile, or use a matching config): " + path);
        }
    }

    if (summary != nullptr) {
        summary->reusedExisting = true;
        summary->vectorCount = vectorCount;
    }
    return true;
}

inline bool EnsureSyntheticKeyTrainSet(const std::string& path,
                                       const std::uint32_t* tags,
                                       std::uint64_t vectorCount,
                                       int numTagsPerVector,
                                       const SyntheticKeyOptions& options,
                                       TrainSetSummary* summary,
                                       std::string* error)
{
    namespace fs = std::filesystem;

    if (summary != nullptr) {
        *summary = TrainSetSummary();
    }
    if (path.empty()) {
        return SetError(error, "predicate train-set path is empty");
    }

    std::error_code ec;
    const bool exists = fs::exists(path, ec);
    if (ec) {
        return SetError(error, "cannot inspect predicate train set '" + path + "': " + ec.message());
    }
    if (exists) {
        if (!fs::is_regular_file(path, ec) || ec) {
            return SetError(error, "predicate train-set path is not a regular file: " + path);
        }
        const std::uintmax_t size = fs::file_size(path, ec);
        if (ec || size == 0) {
            return SetError(error, "existing predicate train set is empty or unreadable: " + path);
        }
        return ReuseExistingTrainSet(path, vectorCount, options, summary, error);
    }

    if (tags == nullptr || vectorCount == 0 || numTagsPerVector <= 0) {
        return SetError(error, "cannot generate predicate train set without non-empty tag rows");
    }
    if (options.keyAttribute.empty()) {
        return SetError(error, "key attribute name is empty");
    }
    if (options.sourceId.empty()) {
        return SetError(error, "sourceId is required for generated predicate train sets");
    }
    if (options.keyColumns.empty()) {
        return SetError(error, "key attribute has no tag columns");
    }
    if (options.samplesPerColumn == 0 || options.samplesPerColumn > 1000000) {
        return SetError(error, "samplesPerColumn must be in [1,1000000]");
    }
    if (options.rangeSampleRows == 0 || options.rangeSampleRows > 10000000) {
        return SetError(error, "rangeSampleRows must be in [1,10000000]");
    }
    if (options.keyKind == KeyKind::Range && options.keyColumns.size() != 1) {
        return SetError(error, "range key attributes require exactly one numeric column");
    }
    if (options.keyKind == KeyKind::Label && options.keyColumns.size() != 1) {
        return SetError(error, "label key attributes require exactly one categorical column");
    }
    if (options.keyKind == KeyKind::Hierarchy && options.keyColumns.size() < 2) {
        return SetError(error, "hierarchy key attributes require at least two columns");
    }
    if (vectorCount > std::numeric_limits<std::size_t>::max() /
                          static_cast<std::size_t>(numTagsPerVector)) {
        return SetError(error, "tag matrix is too large for addressable memory");
    }

    std::unordered_set<int> seenColumns;
    for (int column : options.keyColumns) {
        if (column < 0 || column >= numTagsPerVector) {
            return SetError(error, "key attribute column is outside the tag row");
        }
        if (!seenColumns.insert(column).second) {
            return SetError(error, "key attribute contains a duplicate tag column");
        }
    }

    struct SampledPredicate {
        int column;
        std::uint32_t value;
        std::uint64_t matchCount;
        double weight;
        const char* op;
    };

    const std::size_t rowCount = static_cast<std::size_t>(vectorCount);
    const std::size_t stride = static_cast<std::size_t>(numTagsPerVector);
    std::vector<SampledPredicate> sampled;
    std::vector<std::size_t> sampledDomainValues;

    if (options.keyKind == KeyKind::Range) {
        using RangeSample = std::tuple<std::uint64_t, std::uint64_t, std::uint32_t>;
        std::priority_queue<RangeSample> reservoir;
        const std::size_t reservoirCapacity =
            std::min(options.rangeSampleRows, rowCount);
        const std::size_t column = static_cast<std::size_t>(options.keyColumns[0]);
        for (std::size_t row = 0; row < rowCount; ++row) {
            const std::uint64_t rowId = static_cast<std::uint64_t>(row);
            const RangeSample candidate(
                Mix64(options.seed ^ rowId), rowId, tags[row * stride + column]);
            if (reservoir.size() < reservoirCapacity) {
                reservoir.push(candidate);
            } else if (candidate < reservoir.top()) {
                reservoir.pop();
                reservoir.push(candidate);
            }
        }

        std::vector<std::uint32_t> sampledValues;
        sampledValues.reserve(reservoir.size());
        while (!reservoir.empty()) {
            sampledValues.push_back(std::get<2>(reservoir.top()));
            reservoir.pop();
        }
        std::sort(sampledValues.begin(), sampledValues.end());
        sampledDomainValues.push_back(sampledValues.size());

        const std::size_t targetCount =
            std::min(options.samplesPerColumn, sampledValues.size());
        std::vector<std::uint32_t> thresholds;
        thresholds.reserve(targetCount);
        for (std::size_t sample = 0; sample < targetCount; ++sample) {
            const long double fraction =
                static_cast<long double>(sample + 1) /
                static_cast<long double>(targetCount + 1);
            const std::size_t rank = std::max<std::size_t>(
                1, static_cast<std::size_t>(
                       std::ceil(fraction * static_cast<long double>(sampledValues.size()))));
            const std::uint32_t threshold = sampledValues[rank - 1];
            if (thresholds.empty() || thresholds.back() != threshold) {
                thresholds.push_back(threshold);
            }
        }

        std::vector<std::uint64_t> firstSatisfied(thresholds.size(), 0);
        for (std::size_t row = 0; row < rowCount; ++row) {
            const std::uint32_t value = tags[row * stride + column];
            const auto threshold = std::lower_bound(thresholds.begin(), thresholds.end(), value);
            if (threshold != thresholds.end()) {
                ++firstSatisfied[static_cast<std::size_t>(threshold - thresholds.begin())];
            }
        }
        std::uint64_t cumulative = 0;
        for (std::size_t i = 0; i < thresholds.size(); ++i) {
            cumulative += firstSatisfied[i];
            sampled.push_back(
                {options.keyColumns[0], thresholds[i], cumulative, 0.0, "<="});
        }
        const double weight = 1.0 / static_cast<double>(sampled.size());
        for (auto& predicate : sampled) {
            predicate.weight = weight;
        }
    } else {
        using HashValue = std::pair<std::uint64_t, std::uint32_t>;
        std::vector<std::priority_queue<HashValue>> bottomK(options.keyColumns.size());
        std::vector<std::unordered_set<std::uint32_t>> selected(options.keyColumns.size());

        // Deterministic bottom-k hashes sample distinct label values uniformly
        // without retaining every distinct value. A second pass obtains exact
        // selectivities for only the selected values.
        for (std::size_t row = 0; row < rowCount; ++row) {
            const std::size_t base = row * stride;
            for (std::size_t level = 0; level < options.keyColumns.size(); ++level) {
                const std::uint32_t value =
                    tags[base + static_cast<std::size_t>(options.keyColumns[level])];
                if (selected[level].find(value) != selected[level].end()) continue;
                const std::uint64_t hash = Mix64(
                    options.seed ^
                    (static_cast<std::uint64_t>(
                         static_cast<std::uint32_t>(options.keyColumns[level])) << 32) ^
                    value);
                const HashValue candidate(hash, value);
                if (bottomK[level].size() < options.samplesPerColumn) {
                    bottomK[level].push(candidate);
                    selected[level].insert(value);
                } else if (candidate < bottomK[level].top()) {
                    selected[level].erase(bottomK[level].top().second);
                    bottomK[level].pop();
                    bottomK[level].push(candidate);
                    selected[level].insert(value);
                }
            }
        }

        std::vector<std::vector<std::uint32_t>> selectedValues(options.keyColumns.size());
        std::vector<std::unordered_map<std::uint32_t, std::size_t>> selectedIndexes(
            options.keyColumns.size());
        std::vector<std::vector<std::uint64_t>> selectedCounts(options.keyColumns.size());
        for (std::size_t level = 0; level < bottomK.size(); ++level) {
            while (!bottomK[level].empty()) {
                selectedValues[level].push_back(bottomK[level].top().second);
                bottomK[level].pop();
            }
            std::sort(selectedValues[level].begin(), selectedValues[level].end());
            sampledDomainValues.push_back(selectedValues[level].size());
            selectedCounts[level].assign(selectedValues[level].size(), 0);
            selectedIndexes[level].reserve(selectedValues[level].size());
            for (std::size_t i = 0; i < selectedValues[level].size(); ++i) {
                selectedIndexes[level].emplace(selectedValues[level][i], i);
            }
        }

        for (std::size_t row = 0; row < rowCount; ++row) {
            const std::size_t base = row * stride;
            for (std::size_t level = 0; level < options.keyColumns.size(); ++level) {
                const std::uint32_t value =
                    tags[base + static_cast<std::size_t>(options.keyColumns[level])];
                const auto selectedValue = selectedIndexes[level].find(value);
                if (selectedValue != selectedIndexes[level].end()) {
                    ++selectedCounts[level][selectedValue->second];
                }
            }
        }

        for (std::size_t level = 0; level < selectedValues.size(); ++level) {
            const double weight =
                1.0 / (static_cast<double>(selectedValues.size()) *
                       static_cast<double>(selectedValues[level].size()));
            for (std::size_t i = 0; i < selectedValues[level].size(); ++i) {
                sampled.push_back(
                    {options.keyColumns[level], selectedValues[level][i],
                     selectedCounts[level][i], weight, "="});
            }
        }
    }

    if (sampled.empty()) {
        return SetError(error, "key attribute sampling produced no predicates");
    }
    std::sort(sampled.begin(), sampled.end(),
              [](const auto& left, const auto& right) {
                  if (left.column != right.column) return left.column < right.column;
                  return left.value < right.value;
              });

    const fs::path outputPath(path);
    const fs::path parent = outputPath.parent_path();
    if (!parent.empty()) {
        fs::create_directories(parent, ec);
        if (ec) {
            return SetError(error, "cannot create predicate train-set directory '" +
                                       parent.string() + "': " + ec.message());
        }
    }

    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    const fs::path tempPath = outputPath.string() + ".tmp." + std::to_string(nonce);
    std::ofstream output(tempPath, std::ios::binary | std::ios::trunc);
    if (!output) {
        return SetError(error, "cannot open temporary predicate train set: " +
                                   tempPath.string());
    }

    output << "# sptag_predicate_train_set_v1\n"
           << "# source=synthetic_uniform_key\n"
           << "# source_id=" << options.sourceId << "\n"
           << "# key_attribute=" << options.keyAttribute << "\n"
           << "# key_kind=" << KeyKindName(options.keyKind) << "\n"
           << "# key_columns=" << KeyColumnsText(options.keyColumns) << "\n"
           << "# vector_count=" << vectorCount << "\n"
           << "# samples_per_key_column=" << options.samplesPerColumn << "\n"
           << "# range_sample_rows=" << options.rangeSampleRows << "\n"
           << "# seed=" << options.seed << "\n";
    for (std::size_t i = 0; i < options.keyColumns.size(); ++i) {
        output << "# key_column_" << options.keyColumns[i]
               << "_sampled_domain_values=" << sampledDomainValues[i] << "\n";
    }
    output << "# columns=weight\tselectivity\tmatch_count\tpredicate\n";
    output << std::setprecision(17);
    for (const auto& predicate : sampled) {
        const double selectivity =
            static_cast<double>(predicate.matchCount) / static_cast<double>(vectorCount);
        output << predicate.weight << '\t'
               << selectivity << '\t'
               << predicate.matchCount << '\t'
               << predicate.column << predicate.op << predicate.value << '\n';
    }
    output.flush();
    if (!output) {
        output.close();
        fs::remove(tempPath, ec);
        return SetError(error, "failed while writing predicate train set: " + path);
    }
    output.close();
    if (output.fail()) {
        fs::remove(tempPath, ec);
        return SetError(error, "failed to close predicate train set: " + path);
    }

    // A hard-link publication is atomic and never replaces an existing file.
    // If another builder wins the race, discard our temporary file and validate
    // the winner with the same generated/external workload rules.
    fs::create_hard_link(tempPath, outputPath, ec);
    if (ec) {
        const bool raced = ec == std::make_error_code(std::errc::file_exists);
        std::error_code removeError;
        fs::remove(tempPath, removeError);
        if (raced) {
            return ReuseExistingTrainSet(path, vectorCount, options, summary, error);
        }
        return SetError(error, "cannot publish predicate train set '" + path + "': " +
                                   ec.message());
    }
    fs::remove(tempPath, ec);
    if (ec) {
        return SetError(error, "predicate train set was published but its temporary link "
                               "could not be removed: " + ec.message());
    }

    if (summary != nullptr) {
        summary->vectorCount = vectorCount;
        summary->sampledPredicates = sampled.size();
        summary->sampledDomainValuesPerColumn = std::move(sampledDomainValues);
    }
    return true;
}

} // namespace PredicateWorkload
} // namespace SPTAG
