// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#pragma once

#include "inc/PredicateWorkload.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <functional>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <queue>
#include <random>
#include <sstream>
#include <string>
#include <system_error>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace SPTAG {
namespace PredicateWorkload {

struct WeightedCount {
    std::size_t value = 0;
    double weight = 0.0;
};

struct SyntheticDNFOptions {
    std::string sourceId;
    std::vector<int> keyColumns;
    KeyKind keyKind = KeyKind::Label;
    std::vector<int> predicateColumns;
    int categoricalColumnCount = 0;
    std::size_t samplesPerAttribute = 16;
    std::size_t statisticsSampleRows = 65536;
    std::size_t queryCount = 1024;
    std::vector<WeightedCount> clauseCountWeights;
    std::vector<WeightedCount> clauseAttributeCountWeights;
    std::string clauseCountWeightsText =
        "auto_equal_clause_budget_feasible_1,2";
    std::string clauseAttributeCountWeightsText =
        "auto_uniform_nonempty_attribute_masks";
    std::uint64_t seed = 20260813;
};

struct DNFTrainSetSummary {
    bool reusedExisting = false;
    std::uint64_t vectorCount = 0;
    std::size_t statisticsSampleRows = 0;
    std::size_t generatedQueries = 0;
    std::size_t generatedDNFQueries = 0;
    std::size_t uniqueQueries = 0;
};

namespace Detail {

enum class AtomOp {
    Equal,
    Greater,
    LessEqual,
};

struct Atom {
    int rawColumn = -1;
    std::size_t sampleColumn = 0;
    AtomOp op = AtomOp::Equal;
    std::uint32_t value = 0;
};

struct Clause {
    std::vector<Atom> atoms;
};

struct Query {
    std::vector<Clause> clauses;
    std::string text;
    std::size_t frequency = 0;
    std::size_t sampleMatchCount = 0;
};

struct ColumnCatalog {
    int rawColumn = -1;
    std::size_t sampleColumn = 0;
    bool numeric = false;
    std::vector<std::uint32_t> equalityValues;
    std::vector<std::uint32_t> rangeThresholds;
};

inline std::string ColumnListText(const std::vector<int>& columns)
{
    std::ostringstream output;
    for (std::size_t i = 0; i < columns.size(); ++i) {
        if (i != 0) output << ',';
        output << columns[i];
    }
    return output.str();
}

inline std::string HistogramText(
    const std::map<std::size_t, std::size_t>& histogram)
{
    std::ostringstream output;
    bool first = true;
    for (const auto& item : histogram) {
        if (!first) output << ',';
        output << item.first << ':' << item.second;
        first = false;
    }
    return output.str();
}

inline void UpdatePredicateContentHash(std::uint64_t* hash,
                                       const std::string& predicate,
                                       std::size_t frequency,
                                       std::uint64_t matchCount,
                                       double selectivity)
{
    for (unsigned char ch : predicate) {
        *hash = Mix64(*hash ^ ch);
    }
    std::ostringstream selectivityText;
    selectivityText << std::setprecision(17) << selectivity;
    for (unsigned char ch : selectivityText.str()) {
        *hash = Mix64(*hash ^ ch);
    }
    *hash = Mix64(*hash ^ static_cast<std::uint64_t>(frequency));
    *hash = Mix64(*hash ^ matchCount);
}

inline std::string PredicateContentHashText(std::uint64_t hash)
{
    std::ostringstream output;
    output << std::hex << std::setfill('0') << std::setw(16) << hash;
    return output.str();
}

struct ParsedPredicateShape {
    std::vector<std::size_t> clauseAttributeCounts;
};

inline bool ParseGeneratedPredicateShape(
    const std::string& predicate,
    const std::unordered_set<int>& predicateColumns,
    const std::unordered_set<int>& keyColumns,
    KeyKind keyKind,
    int categoricalColumnCount,
    ParsedPredicateShape* shape)
{
    shape->clauseAttributeCounts.clear();
    if (predicate.empty() ||
        predicate.back() == '|' ||
        predicate.back() == '&') {
        return false;
    }
    std::unordered_set<std::string> seenClauses;
    std::size_t clauseBegin = 0;
    while (clauseBegin < predicate.size()) {
        const std::size_t clauseEnd = predicate.find('|', clauseBegin);
        const std::size_t clauseLength =
            (clauseEnd == std::string::npos ? predicate.size() : clauseEnd) -
            clauseBegin;
        if (clauseLength == 0) return false;
        const std::string clauseText =
            predicate.substr(clauseBegin, clauseLength);
        if (clauseText.back() == '&') return false;
        if (!seenClauses.insert(clauseText).second) return false;

        bool hasKey = false;
        std::size_t keyAtomCount = 0;
        bool hasKeyLowerBound = false;
        bool hasKeyUpperBound = false;
        int keyRawColumn = -1;
        std::unordered_set<int> nonKeyColumns;
        std::size_t atomBegin = clauseBegin;
        const std::size_t absoluteClauseEnd =
            clauseEnd == std::string::npos ? predicate.size() : clauseEnd;
        while (atomBegin < absoluteClauseEnd) {
            const std::size_t atomEnd =
                std::min(predicate.find('&', atomBegin), absoluteClauseEnd);
            if (atomEnd == atomBegin) return false;

            std::size_t cursor = atomBegin;
            if (cursor >= atomEnd ||
                !std::isdigit(static_cast<unsigned char>(predicate[cursor]))) {
                return false;
            }
            std::uint64_t rawColumn = 0;
            while (cursor < atomEnd &&
                   std::isdigit(static_cast<unsigned char>(predicate[cursor]))) {
                const std::uint64_t digit =
                    static_cast<unsigned char>(predicate[cursor] - '0');
                const std::uint64_t maximum =
                    static_cast<std::uint64_t>(
                        std::numeric_limits<int>::max());
                if (rawColumn > (maximum - digit) / 10) {
                    return false;
                }
                rawColumn = rawColumn * 10 + digit;
                ++cursor;
            }

            AtomOp op;
            if (cursor + 1 < atomEnd &&
                predicate[cursor] == '<' &&
                predicate[cursor + 1] == '=') {
                op = AtomOp::LessEqual;
                cursor += 2;
            } else if (cursor < atomEnd && predicate[cursor] == '=') {
                op = AtomOp::Equal;
                ++cursor;
            } else if (cursor < atomEnd && predicate[cursor] == '>') {
                op = AtomOp::Greater;
                ++cursor;
            } else {
                return false;
            }

            if (cursor >= atomEnd ||
                !std::isdigit(static_cast<unsigned char>(predicate[cursor]))) {
                return false;
            }
            std::uint64_t value = 0;
            while (cursor < atomEnd &&
                   std::isdigit(static_cast<unsigned char>(predicate[cursor]))) {
                const std::uint64_t digit =
                    static_cast<unsigned char>(predicate[cursor] - '0');
                const std::uint64_t maximum =
                    std::numeric_limits<std::uint32_t>::max();
                if (value > (maximum - digit) / 10) {
                    return false;
                }
                value = value * 10 + digit;
                ++cursor;
            }
            if (cursor != atomEnd) return false;

            const int column = static_cast<int>(rawColumn);
            if (predicateColumns.find(column) == predicateColumns.end()) {
                return false;
            }
            if (keyColumns.find(column) != keyColumns.end()) {
                hasKey = true;
                ++keyAtomCount;
                if (keyRawColumn != -1 && keyRawColumn != column) return false;
                keyRawColumn = column;
                if (keyKind == KeyKind::Range) {
                    if (op == AtomOp::Greater) {
                        hasKeyLowerBound = true;
                    } else if (op == AtomOp::LessEqual) {
                        hasKeyUpperBound = true;
                    } else {
                        return false;
                    }
                } else if (op != AtomOp::Equal) {
                    return false;
                }
            } else {
                const bool numeric = column >= categoricalColumnCount;
                if ((numeric && op != AtomOp::LessEqual) ||
                    (!numeric && op != AtomOp::Equal) ||
                    !nonKeyColumns.insert(column).second) {
                    return false;
                }
            }

            atomBegin = atomEnd + 1;
        }
        if (keyKind == KeyKind::Range && hasKey) {
            if (keyAtomCount > 2 || !hasKeyUpperBound ||
                (keyAtomCount == 2 && !hasKeyLowerBound)) {
                return false;
            }
        } else if (keyAtomCount > 1) {
            return false;
        }

        const std::size_t attributeCount =
            (hasKey ? 1 : 0) + nonKeyColumns.size();
        if (attributeCount == 0) return false;
        shape->clauseAttributeCounts.push_back(attributeCount);
        if (clauseEnd == std::string::npos) break;
        clauseBegin = clauseEnd + 1;
    }
    return !shape->clauseAttributeCounts.empty();
}

inline bool ReadGeneratedMetadata(const std::string& path,
                                  std::string* firstLine,
                                  std::unordered_map<std::string, std::string>* metadata,
                                  std::string* error)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return SetError(error, "cannot read existing predicate train set: " + path);
    }
    if (!std::getline(input, *firstLine)) {
        return SetError(error, "existing predicate train set is empty: " + path);
    }

    std::string line;
    while (std::getline(input, line)) {
        if (line.rfind("# ", 0) != 0) break;
        const std::size_t separator = line.find('=', 2);
        if (separator == std::string::npos) continue;
        metadata->emplace(line.substr(2, separator - 2), line.substr(separator + 1));
    }
    return true;
}

inline bool ReuseExistingDNFTrainSet(const std::string& path,
                                     std::uint64_t vectorCount,
                                     const SyntheticDNFOptions& options,
                                     DNFTrainSetSummary* summary,
                                     std::string* error)
{
    std::string firstLine;
    std::unordered_map<std::string, std::string> metadata;
    if (!ReadGeneratedMetadata(path, &firstLine, &metadata, error)) {
        return false;
    }

    const bool generatedV5 = firstLine == "# sptag_predicate_train_set_v5";
    const bool generatedFormat =
        firstLine.rfind("# sptag_predicate_train_set_v", 0) == 0;
    if (!generatedFormat) {
        if (summary != nullptr) summary->reusedExisting = true;
        return true;
    }
    if (!generatedV5) {
        return SetError(
            error,
            "existing generated predicate train set uses an incompatible format "
            "(remove it, choose another TrainSetFile, or provide an external workload): " +
                path);
    }

    const std::vector<std::pair<std::string, std::string>> expected = {
        {"source", "synthetic_predicate_dnf"},
        {"source_id", options.sourceId},
        {"key_kind", KeyKindName(options.keyKind)},
        {"key_columns", ColumnListText(options.keyColumns)},
        {"predicate_columns", ColumnListText(options.predicateColumns)},
        {"key_clause_policy", "optional"},
        {"clause_marginal_policy", "positive_on_statistics_sample"},
        {"categorical_column_count", std::to_string(options.categoricalColumnCount)},
        {"vector_count", std::to_string(vectorCount)},
        {"samples_per_attribute", std::to_string(options.samplesPerAttribute)},
        {"statistics_sample_rows", std::to_string(options.statisticsSampleRows)},
        {"query_count", std::to_string(options.queryCount)},
        {"clause_count_weights", options.clauseCountWeightsText},
        {"clause_attribute_count_weights", options.clauseAttributeCountWeightsText},
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

    auto readSize = [&](const char* key, std::size_t* parsed) -> bool {
        const auto found = metadata.find(key);
        if (found == metadata.end()) return false;
        char* end = nullptr;
        const unsigned long long value =
            std::strtoull(found->second.c_str(), &end, 10);
        if (end == found->second.c_str() || *end != '\0' ||
            value > std::numeric_limits<std::size_t>::max()) {
            return false;
        }
        *parsed = static_cast<std::size_t>(value);
        return true;
    };
    std::size_t discoveryRows = 0;
    std::size_t statisticsRows = 0;
    std::size_t generatedDNFQueries = 0;
    std::size_t uniqueQueries = 0;
    if (!readSize("discovery_sample_rows_used", &discoveryRows) ||
        !readSize("statistics_sample_rows_used", &statisticsRows) ||
        !readSize("generated_dnf_query_count", &generatedDNFQueries) ||
        !readSize("unique_query_count", &uniqueQueries) ||
        discoveryRows == 0 || statisticsRows == 0 ||
        generatedDNFQueries > options.queryCount ||
        uniqueQueries == 0 || uniqueQueries > options.queryCount ||
        metadata.find("realized_clause_counts") == metadata.end() ||
        metadata.find("realized_clause_attribute_counts") == metadata.end() ||
        metadata.find("selectivity_source") == metadata.end() ||
        metadata.find("match_count") == metadata.end() ||
        metadata.find("content_hash") == metadata.end() ||
        metadata.find("density_filter") == metadata.end() ||
        metadata.at("density_filter") != "none") {
        return SetError(
            error,
            "existing generated predicate train set is incomplete or malformed: " + path);
    }

    std::ifstream workload(path, std::ios::binary);
    if (!workload) {
        return SetError(error, "cannot validate predicate train set: " + path);
    }
    std::size_t dataRows = 0;
    std::size_t frequencySum = 0;
    std::size_t realizedDNFQueries = 0;
    long double weightSum = 0.0;
    std::uint64_t contentHash = 0x510e527fade682d1ULL;
    std::map<std::size_t, std::size_t> realizedClauseCounts;
    std::map<std::size_t, std::size_t> realizedAttributeCounts;
    std::unordered_set<std::string> seenPredicates;
    seenPredicates.reserve(uniqueQueries);
    const std::unordered_set<int> predicateColumnSet(
        options.predicateColumns.begin(), options.predicateColumns.end());
    const std::unordered_set<int> keyColumnSet(
        options.keyColumns.begin(), options.keyColumns.end());
    std::string line;
    while (std::getline(workload, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream row(line);
        double weight = 0.0;
        double selectivity = 0.0;
        std::uint64_t matchCount = 0;
        std::string predicate;
        std::string extra;
        if (!(row >> weight >> selectivity >> matchCount >> predicate) ||
            (row >> extra) ||
            !std::isfinite(weight) || weight <= 0.0 || weight > 1.0 ||
            !std::isfinite(selectivity) || selectivity <= 0.0 ||
            selectivity > 1.0 || matchCount == 0 ||
            matchCount > vectorCount || predicate.empty()) {
            return SetError(
                error,
                "existing generated predicate train set has an invalid data row: " + path);
        }
        const long double exactFrequency =
            static_cast<long double>(weight) *
            static_cast<long double>(options.queryCount);
        const std::size_t frequency = static_cast<std::size_t>(
            std::llround(exactFrequency));
        const double expectedWeight =
            static_cast<double>(frequency) /
            static_cast<double>(options.queryCount);
        const std::size_t sampleMatchCount = static_cast<std::size_t>(
            std::llround(
                selectivity * static_cast<double>(statisticsRows)));
        const double expectedSelectivity =
            static_cast<double>(sampleMatchCount) /
            static_cast<double>(statisticsRows);
        const std::uint64_t expectedMatchCount =
            static_cast<std::uint64_t>(
                std::llround(
                    expectedSelectivity *
                    static_cast<double>(vectorCount)));
        ParsedPredicateShape shape;
        if (frequency == 0 ||
            std::fabs(
                exactFrequency - static_cast<long double>(frequency)) >
                1e-8L ||
            weight != expectedWeight ||
            sampleMatchCount == 0 ||
            sampleMatchCount > statisticsRows ||
            selectivity != expectedSelectivity ||
            expectedMatchCount != matchCount ||
            !seenPredicates.insert(predicate).second ||
            !ParseGeneratedPredicateShape(
                predicate,
                predicateColumnSet,
                keyColumnSet,
                options.keyKind,
                options.categoricalColumnCount,
                &shape)) {
            return SetError(
                error,
                "existing generated predicate train set has invalid predicate "
                "content: " + path);
        }
        frequencySum += frequency;
        const std::size_t clauseCount =
            shape.clauseAttributeCounts.size();
        realizedClauseCounts[clauseCount] += frequency;
        if (clauseCount > 1) realizedDNFQueries += frequency;
        for (std::size_t attributeCount :
             shape.clauseAttributeCounts) {
            realizedAttributeCounts[attributeCount] += frequency;
        }
        UpdatePredicateContentHash(
            &contentHash,
            predicate,
            frequency,
            matchCount,
            selectivity);
        weightSum += weight;
        ++dataRows;
    }
    if (dataRows != uniqueQueries ||
        frequencySum != options.queryCount ||
        realizedDNFQueries != generatedDNFQueries ||
        HistogramText(realizedClauseCounts) !=
            metadata.at("realized_clause_counts") ||
        HistogramText(realizedAttributeCounts) !=
            metadata.at("realized_clause_attribute_counts") ||
        PredicateContentHashText(contentHash) !=
            metadata.at("content_hash") ||
        std::fabs(weightSum - 1.0L) > 1e-9L) {
        return SetError(
            error,
            "existing generated predicate train set has incomplete rows or "
            "inconsistent weights, shapes, or content hash: " + path);
    }

    if (summary != nullptr) {
        summary->reusedExisting = true;
        summary->vectorCount = vectorCount;
        summary->statisticsSampleRows = statisticsRows;
        summary->generatedQueries = options.queryCount;
        summary->generatedDNFQueries = generatedDNFQueries;
        summary->uniqueQueries = uniqueQueries;
    }
    return true;
}

inline std::size_t UniformIndex(std::mt19937_64* rng, std::size_t limit)
{
    if (limit <= 1) return 0;
    const std::uint64_t bound =
        std::numeric_limits<std::uint64_t>::max() -
        (std::numeric_limits<std::uint64_t>::max() % static_cast<std::uint64_t>(limit));
    std::uint64_t value = 0;
    do {
        value = (*rng)();
    } while (value >= bound);
    return static_cast<std::size_t>(value % static_cast<std::uint64_t>(limit));
}

inline std::size_t ChooseWeighted(const std::vector<WeightedCount>& choices,
                                  std::mt19937_64* rng)
{
    long double total = 0.0;
    for (const auto& choice : choices) total += choice.weight;
    const long double unit =
        static_cast<long double>((*rng)()) /
        static_cast<long double>(std::numeric_limits<std::uint64_t>::max());
    const long double target = unit * total;
    long double cumulative = 0.0;
    for (const auto& choice : choices) {
        cumulative += choice.weight;
        if (target < cumulative) return choice.value;
    }
    return choices.back().value;
}

inline std::vector<std::size_t> AllocateSchedule(
    std::size_t total,
    const std::vector<WeightedCount>& choices,
    std::mt19937_64* rng)
{
    struct Allocation {
        std::size_t value;
        std::size_t count;
        long double remainder;
    };

    long double totalWeight = 0.0;
    for (const auto& choice : choices) totalWeight += choice.weight;

    std::vector<Allocation> allocations;
    allocations.reserve(choices.size());
    std::size_t assigned = 0;
    for (const auto& choice : choices) {
        const long double exact =
            static_cast<long double>(total) * choice.weight / totalWeight;
        const std::size_t count = static_cast<std::size_t>(std::floor(exact));
        allocations.push_back({choice.value, count, exact - count});
        assigned += count;
    }
    std::sort(
        allocations.begin(), allocations.end(),
        [](const Allocation& left, const Allocation& right) {
            if (left.remainder != right.remainder) {
                return left.remainder > right.remainder;
            }
            return left.value < right.value;
        });
    for (std::size_t i = 0; assigned < total; ++i, ++assigned) {
        ++allocations[i % allocations.size()].count;
    }

    std::vector<std::size_t> schedule;
    schedule.reserve(total);
    for (const auto& allocation : allocations) {
        schedule.insert(schedule.end(), allocation.count, allocation.value);
    }
    std::shuffle(schedule.begin(), schedule.end(), *rng);
    return schedule;
}

inline std::string ScheduleHistogramText(const std::vector<std::size_t>& schedule)
{
    std::map<std::size_t, std::size_t> counts;
    for (std::size_t value : schedule) ++counts[value];
    std::ostringstream output;
    bool first = true;
    for (const auto& item : counts) {
        if (!first) output << ',';
        output << item.first << ':' << item.second;
        first = false;
    }
    return output.str();
}

inline std::vector<std::size_t> ChooseDistinct(std::size_t population,
                                               std::size_t count,
                                               std::mt19937_64* rng)
{
    std::unordered_set<std::size_t> selected;
    selected.reserve(count);
    while (selected.size() < count) {
        selected.insert(UniformIndex(rng, population));
    }
    std::vector<std::size_t> result(selected.begin(), selected.end());
    std::sort(result.begin(), result.end());
    return result;
}

inline int AtomOpRank(AtomOp op)
{
    switch (op) {
    case AtomOp::Greater:
        return 0;
    case AtomOp::Equal:
        return 1;
    case AtomOp::LessEqual:
        return 2;
    }
    return 3;
}

inline std::string AtomText(const Atom& atom)
{
    std::ostringstream output;
    output << atom.rawColumn;
    switch (atom.op) {
    case AtomOp::Equal:
        output << '=';
        break;
    case AtomOp::Greater:
        output << '>';
        break;
    case AtomOp::LessEqual:
        output << "<=";
        break;
    }
    output << atom.value;
    return output.str();
}

inline std::string CanonicalizeClause(Clause* clause)
{
    std::sort(
        clause->atoms.begin(), clause->atoms.end(),
        [](const Atom& left, const Atom& right) {
            if (left.rawColumn != right.rawColumn) {
                return left.rawColumn < right.rawColumn;
            }
            const int leftRank = AtomOpRank(left.op);
            const int rightRank = AtomOpRank(right.op);
            if (leftRank != rightRank) return leftRank < rightRank;
            return left.value < right.value;
        });
    std::ostringstream text;
    for (std::size_t i = 0; i < clause->atoms.size(); ++i) {
        if (i != 0) text << '&';
        text << AtomText(clause->atoms[i]);
    }
    return text.str();
}

inline std::string CanonicalizeQuery(Query* query)
{
    std::vector<std::pair<std::string, Clause>> clauses;
    clauses.reserve(query->clauses.size());
    for (auto& clause : query->clauses) {
        const std::string clauseText = CanonicalizeClause(&clause);
        clauses.emplace_back(clauseText, std::move(clause));
    }
    std::sort(
        clauses.begin(), clauses.end(),
        [](const auto& left, const auto& right) { return left.first < right.first; });

    query->clauses.clear();
    std::ostringstream result;
    std::string previous;
    bool first = true;
    for (auto& clause : clauses) {
        if (!first && clause.first == previous) continue;
        if (!first) result << '|';
        result << clause.first;
        previous = clause.first;
        query->clauses.push_back(std::move(clause.second));
        first = false;
    }
    query->text = result.str();
    return query->text;
}

inline bool AtomMatches(const Atom& atom, const std::uint32_t* sampleRow)
{
    const std::uint32_t value = sampleRow[atom.sampleColumn];
    switch (atom.op) {
    case AtomOp::Equal:
        return value == atom.value;
    case AtomOp::Greater:
        return value > atom.value;
    case AtomOp::LessEqual:
        return value <= atom.value;
    }
    return false;
}

inline bool QueryMatches(const Query& query, const std::uint32_t* sampleRow)
{
    for (const auto& clause : query.clauses) {
        bool matches = true;
        for (const auto& atom : clause.atoms) {
            if (!AtomMatches(atom, sampleRow)) {
                matches = false;
                break;
            }
        }
        if (matches) return true;
    }
    return false;
}

inline bool ClauseMatches(const Clause& clause, const std::uint32_t* sampleRow)
{
    for (const auto& atom : clause.atoms) {
        if (!AtomMatches(atom, sampleRow)) return false;
    }
    return true;
}

inline bool PublishFileWithoutReplace(const std::filesystem::path& tempPath,
                                      const std::filesystem::path& outputPath,
                                      std::string* error)
{
    namespace fs = std::filesystem;
    std::error_code ec;
    fs::create_hard_link(tempPath, outputPath, ec);
    if (ec) {
        const bool raced = ec == std::make_error_code(std::errc::file_exists);
        std::error_code removeError;
        fs::remove(tempPath, removeError);
        if (raced) return false;
        return SetError(error, "cannot publish predicate train set '" +
                                   outputPath.string() + "': " + ec.message());
    }
    fs::remove(tempPath, ec);
    if (ec) {
        return SetError(error, "predicate train set was published but its temporary link "
                               "could not be removed: " + ec.message());
    }
    return true;
}

} // namespace Detail

inline bool EnsureSyntheticDNFTrainSet(const std::string& path,
                                       const std::uint32_t* tags,
                                       std::uint64_t vectorCount,
                                       int numTagsPerVector,
                                       const SyntheticDNFOptions& options,
                                       DNFTrainSetSummary* summary,
                                       std::string* error)
{
    namespace fs = std::filesystem;
    using namespace Detail;

    if (summary != nullptr) *summary = DNFTrainSetSummary();
    if (path.empty()) return SetError(error, "predicate train-set path is empty");

    std::error_code ec;
    const bool exists = fs::exists(path, ec);
    if (ec) {
        return SetError(error, "cannot inspect predicate train set '" + path + "': " +
                                   ec.message());
    }
    if (exists) {
        if (!fs::is_regular_file(path, ec) || ec) {
            return SetError(error, "predicate train-set path is not a regular file: " + path);
        }
        const std::uintmax_t size = fs::file_size(path, ec);
        if (ec || size == 0) {
            return SetError(error, "existing predicate train set is empty or unreadable: " +
                                       path);
        }
        return ReuseExistingDNFTrainSet(path, vectorCount, options, summary, error);
    }

    if (tags == nullptr || vectorCount == 0 || numTagsPerVector <= 0) {
        return SetError(error, "cannot generate DNF workload without non-empty tag rows");
    }
    if (options.sourceId.empty()) return SetError(error, "sourceId is required");
    if (options.keyColumns.empty()) return SetError(error, "key attribute has no columns");
    if (options.predicateColumns.empty()) {
        return SetError(error, "predicate column set is empty");
    }
    if (options.samplesPerAttribute == 0 || options.samplesPerAttribute > 65536) {
        return SetError(error, "samplesPerAttribute must be in [1,65536]");
    }
    if (options.statisticsSampleRows == 0 || options.statisticsSampleRows > 1000000) {
        return SetError(error, "statisticsSampleRows must be in [1,1000000]");
    }
    if (options.queryCount == 0 || options.queryCount > 10000) {
        return SetError(error, "queryCount must be in [1,10000]");
    }
    if (options.clauseCountWeightsText.empty() ||
        options.clauseAttributeCountWeightsText.empty()) {
        return SetError(error, "DNF shape policy metadata must not be empty");
    }
    if (options.categoricalColumnCount < 0 ||
        options.categoricalColumnCount > numTagsPerVector) {
        return SetError(error, "categoricalColumnCount is outside the tag row");
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
    if (vectorCount > std::numeric_limits<std::size_t>::max()) {
        return SetError(error, "vectorCount exceeds addressable memory");
    }

    std::vector<int> relevantColumns = options.predicateColumns;
    std::sort(relevantColumns.begin(), relevantColumns.end());
    if (relevantColumns.size() > 64) {
        return SetError(error, "DNF workloads support at most 64 predicate attributes");
    }
    if (std::adjacent_find(relevantColumns.begin(), relevantColumns.end()) !=
        relevantColumns.end()) {
        return SetError(error, "predicate columns must be unique");
    }
    for (int column : relevantColumns) {
        if (column < 0 || column >= numTagsPerVector) {
            return SetError(error, "predicate attribute column is outside the tag row");
        }
    }
    std::unordered_set<int> keyColumnSet;
    keyColumnSet.reserve(options.keyColumns.size());
    for (int column : options.keyColumns) {
        if (column < 0 || column >= numTagsPerVector ||
            !keyColumnSet.insert(column).second) {
            return SetError(error, "key attribute columns must be valid and unique");
        }
        if (!std::binary_search(
                relevantColumns.begin(), relevantColumns.end(), column)) {
            return SetError(error, "predicate columns must include every key column");
        }
        const bool numeric = column >= options.categoricalColumnCount;
        if ((options.keyKind == KeyKind::Range) != numeric) {
            return SetError(error, "key kind does not match the key column type");
        }
    }
    const std::size_t logicalAttributeCount =
        1 + relevantColumns.size() - options.keyColumns.size();

    for (const auto& choice : options.clauseCountWeights) {
        if (choice.value == 0 || choice.value > 64 ||
            !std::isfinite(choice.weight) || choice.weight <= 0.0) {
            return SetError(error, "invalid clause-count distribution");
        }
    }
    for (const auto& choice : options.clauseAttributeCountWeights) {
        if (choice.value == 0 ||
            choice.value > logicalAttributeCount ||
            !std::isfinite(choice.weight) || choice.weight <= 0.0) {
            return SetError(error, "invalid clause-attribute-count distribution");
        }
    }

    const std::size_t rowCount = static_cast<std::size_t>(vectorCount);
    const bool fullPopulationStatistics =
        rowCount <= options.statisticsSampleRows;
    const std::size_t requestedRows = options.statisticsSampleRows;
    const std::size_t selectedRowCount = fullPopulationStatistics
        ? rowCount
        : std::min<std::size_t>(rowCount, requestedRows * 2);
    const std::size_t discoveryCount = fullPopulationStatistics
        ? rowCount
        : selectedRowCount / 2;
    const std::size_t evaluationCount = fullPopulationStatistics
        ? rowCount
        : selectedRowCount - discoveryCount;
    if (discoveryCount == 0 || evaluationCount == 0) {
        return SetError(error, "not enough rows for independent workload statistics");
    }

    if (std::max(discoveryCount, evaluationCount) >
        std::numeric_limits<std::size_t>::max() / relevantColumns.size()) {
        return SetError(error, "statistics sample matrix is too large");
    }

    std::unordered_map<int, std::size_t> sampleColumnByRaw;
    sampleColumnByRaw.reserve(relevantColumns.size());
    for (std::size_t i = 0; i < relevantColumns.size(); ++i) {
        sampleColumnByRaw.emplace(relevantColumns[i], i);
    }

    const std::size_t stride = static_cast<std::size_t>(numTagsPerVector);
    std::vector<std::uint32_t> discoverySample(
        discoveryCount * relevantColumns.size());
    std::vector<std::uint32_t> evaluationSample(
        evaluationCount * relevantColumns.size());
    auto copyRow = [&](std::size_t sourceRow,
                       std::size_t targetRow,
                       std::vector<std::uint32_t>* target) {
        const std::size_t sourceBase = sourceRow * stride;
        const std::size_t sampleBase = targetRow * relevantColumns.size();
        for (std::size_t column = 0; column < relevantColumns.size(); ++column) {
            (*target)[sampleBase + column] =
                tags[sourceBase + static_cast<std::size_t>(relevantColumns[column])];
        }
    };

    if (fullPopulationStatistics) {
        for (std::size_t row = 0; row < rowCount; ++row) {
            copyRow(row, row, &discoverySample);
            copyRow(row, row, &evaluationSample);
        }
    } else {
        using RowTicket = std::pair<std::uint64_t, std::uint64_t>;
        std::priority_queue<RowTicket> rows;
        for (std::uint64_t row = 0; row < vectorCount; ++row) {
            const RowTicket candidate(
                Mix64(options.seed ^ 0x8f3f73b5cf1c9ad1ULL ^ row), row);
            if (rows.size() < selectedRowCount) {
                rows.push(candidate);
            } else if (candidate < rows.top()) {
                rows.pop();
                rows.push(candidate);
            }
        }
        std::vector<std::size_t> sampledRows;
        sampledRows.reserve(rows.size());
        while (!rows.empty()) {
            sampledRows.push_back(static_cast<std::size_t>(rows.top().second));
            rows.pop();
        }
        std::sort(
            sampledRows.begin(), sampledRows.end(),
            [&](std::size_t left, std::size_t right) {
                const std::uint64_t leftHash =
                    Mix64(options.seed ^ 0x41c64e6da3bc0074ULL ^ left);
                const std::uint64_t rightHash =
                    Mix64(options.seed ^ 0x41c64e6da3bc0074ULL ^ right);
                if (leftHash != rightHash) return leftHash < rightHash;
                return left < right;
            });
        for (std::size_t row = 0; row < discoveryCount; ++row) {
            copyRow(sampledRows[row], row, &discoverySample);
        }
        for (std::size_t row = 0; row < evaluationCount; ++row) {
            copyRow(
                sampledRows[discoveryCount + row], row, &evaluationSample);
        }
    }

    std::unordered_map<int, ColumnCatalog> catalogByColumn;
    catalogByColumn.reserve(relevantColumns.size());
    for (int rawColumn : relevantColumns) {
        ColumnCatalog catalog;
        catalog.rawColumn = rawColumn;
        catalog.sampleColumn = sampleColumnByRaw.at(rawColumn);
        catalog.numeric = rawColumn >= options.categoricalColumnCount;

        if (catalog.numeric) {
            std::vector<std::uint32_t> values(discoveryCount);
            for (std::size_t row = 0; row < discoveryCount; ++row) {
                values[row] =
                    discoverySample[
                        row * relevantColumns.size() + catalog.sampleColumn];
            }
            std::sort(values.begin(), values.end());
            const std::size_t targetCount =
                std::min(options.samplesPerAttribute, values.size());
            for (std::size_t i = 0; i < targetCount; ++i) {
                const long double fraction =
                    static_cast<long double>(i + 1) /
                    static_cast<long double>(targetCount + 1);
                const std::size_t rank = std::max<std::size_t>(
                    1, static_cast<std::size_t>(
                           std::ceil(fraction * static_cast<long double>(values.size()))));
                const std::uint32_t threshold = values[rank - 1];
                if (catalog.rangeThresholds.empty() ||
                    catalog.rangeThresholds.back() != threshold) {
                    catalog.rangeThresholds.push_back(threshold);
                }
            }
            if (catalog.rangeThresholds.empty()) {
                catalog.rangeThresholds.push_back(values.back());
            }
        } else {
            std::unordered_set<std::uint32_t> distinct;
            distinct.reserve(std::min<std::size_t>(discoveryCount, 65536));
            for (std::size_t row = 0; row < discoveryCount; ++row) {
                distinct.insert(
                    discoverySample[
                        row * relevantColumns.size() + catalog.sampleColumn]);
            }
            std::vector<std::pair<std::uint64_t, std::uint32_t>> candidates;
            candidates.reserve(distinct.size());
            for (std::uint32_t value : distinct) {
                candidates.emplace_back(
                    Mix64(options.seed ^
                          (static_cast<std::uint64_t>(
                               static_cast<std::uint32_t>(rawColumn)) << 32) ^
                          value),
                    value);
            }
            std::sort(candidates.begin(), candidates.end());
            candidates.resize(std::min(options.samplesPerAttribute, candidates.size()));
            for (const auto& candidate : candidates) {
                catalog.equalityValues.push_back(candidate.second);
            }
            std::sort(catalog.equalityValues.begin(), catalog.equalityValues.end());
        }
        catalogByColumn.emplace(rawColumn, std::move(catalog));
    }

    std::vector<std::vector<Atom>> keyPredicates;
    if (options.keyKind == KeyKind::Range) {
        const ColumnCatalog& keyCatalog = catalogByColumn.at(options.keyColumns[0]);
        std::vector<std::uint32_t> values(discoveryCount);
        for (std::size_t row = 0; row < discoveryCount; ++row) {
            values[row] =
                discoverySample[
                    row * relevantColumns.size() + keyCatalog.sampleColumn];
        }
        std::sort(values.begin(), values.end());
        const std::size_t targetCount =
            std::min(options.samplesPerAttribute, values.size());
        std::vector<std::uint32_t> boundaries;
        for (std::size_t i = 0; i < targetCount; ++i) {
            const long double fraction =
                static_cast<long double>(i + 1) /
                static_cast<long double>(targetCount);
            const std::size_t rank = std::max<std::size_t>(
                1, static_cast<std::size_t>(
                       std::ceil(fraction * static_cast<long double>(values.size()))));
            const std::uint32_t boundary = values[rank - 1];
            if (boundaries.empty() || boundaries.back() != boundary) {
                boundaries.push_back(boundary);
            }
        }
        for (std::size_t i = 0; i < boundaries.size(); ++i) {
            std::vector<Atom> region;
            if (i != 0) {
                region.push_back(
                    {keyCatalog.rawColumn, keyCatalog.sampleColumn,
                     AtomOp::Greater, boundaries[i - 1]});
            }
            region.push_back(
                {keyCatalog.rawColumn, keyCatalog.sampleColumn,
                 AtomOp::LessEqual, boundaries[i]});
            keyPredicates.push_back(std::move(region));
        }
    } else {
        for (int keyColumn : options.keyColumns) {
            const ColumnCatalog& keyCatalog = catalogByColumn.at(keyColumn);
            for (std::uint32_t value : keyCatalog.equalityValues) {
                keyPredicates.push_back(
                    {{keyCatalog.rawColumn, keyCatalog.sampleColumn,
                      AtomOp::Equal, value}});
            }
        }
    }
    if (keyPredicates.empty()) {
        return SetError(error, "key attribute has no sampled predicates");
    }

    std::vector<std::vector<std::vector<Atom>>> logicalPredicates;
    logicalPredicates.reserve(logicalAttributeCount);
    logicalPredicates.push_back(std::move(keyPredicates));
    for (int rawColumn : relevantColumns) {
        if (keyColumnSet.find(rawColumn) != keyColumnSet.end()) continue;
        const ColumnCatalog& catalog = catalogByColumn.at(rawColumn);
        std::vector<std::vector<Atom>> predicates;
        if (catalog.numeric) {
            predicates.reserve(catalog.rangeThresholds.size());
            for (std::uint32_t threshold : catalog.rangeThresholds) {
                predicates.push_back(
                    {{catalog.rawColumn, catalog.sampleColumn,
                      AtomOp::LessEqual, threshold}});
            }
        } else {
            predicates.reserve(catalog.equalityValues.size());
            for (std::uint32_t value : catalog.equalityValues) {
                predicates.push_back(
                    {{catalog.rawColumn, catalog.sampleColumn,
                      AtomOp::Equal, value}});
            }
        }
        if (predicates.empty()) {
            return SetError(
                error,
                "predicate column " + std::to_string(rawColumn) +
                    " has no sampled predicates");
        }
        logicalPredicates.push_back(std::move(predicates));
    }

    constexpr std::size_t kMaximumClauseCount = 64;
    constexpr std::size_t kExactClausePoolLimit = 256;
    constexpr std::size_t kClauseCapacitySentinel =
        kExactClausePoolLimit + 1;
    std::vector<std::size_t> clauseCapacityByAttributeCount(
        logicalAttributeCount + 1, 0);
    clauseCapacityByAttributeCount[0] = 1;
    std::size_t processedAttributes = 0;
    for (const auto& predicates : logicalPredicates) {
        ++processedAttributes;
        for (std::size_t selected = processedAttributes;
             selected > 0;
             --selected) {
            const std::size_t prior =
                clauseCapacityByAttributeCount[selected - 1];
            const std::size_t added =
                 prior > kClauseCapacitySentinel / predicates.size()
                     ? kClauseCapacitySentinel
                    : prior * predicates.size();
            clauseCapacityByAttributeCount[selected] =
                std::min(
                     kClauseCapacitySentinel,
                    clauseCapacityByAttributeCount[selected] + added);
        }
    }

    std::vector<std::vector<Clause>> exactClausePools(
        logicalAttributeCount + 1);
    for (std::size_t selected = 1;
         selected <= logicalAttributeCount;
         ++selected) {
        if (clauseCapacityByAttributeCount[selected] >
             kExactClausePoolLimit) {
             continue;
        }
        Clause clause;
        std::function<void(std::size_t, std::size_t)> enumerate =
             [&](std::size_t attribute, std::size_t remaining) {
                 if (remaining == 0) {
                     exactClausePools[selected].push_back(clause);
                     return;
                 }
                 if (attribute == logicalPredicates.size() ||
                     logicalPredicates.size() - attribute < remaining) {
                     return;
                 }
                 enumerate(attribute + 1, remaining);
                 for (const auto& predicate :
                      logicalPredicates[attribute]) {
                     const std::size_t originalSize = clause.atoms.size();
                     clause.atoms.insert(
                         clause.atoms.end(),
                         predicate.begin(),
                         predicate.end());
                     enumerate(attribute + 1, remaining - 1);
                     clause.atoms.resize(originalSize);
                 }
             };
        enumerate(0, selected);
        if (exactClausePools[selected].size() !=
             clauseCapacityByAttributeCount[selected]) {
             return SetError(error, "failed to enumerate predicate clause catalog");
        }
    }

    std::size_t maximumDistinctClauses = 0;
    for (std::size_t selected = 1;
         selected < clauseCapacityByAttributeCount.size();
         ++selected) {
        maximumDistinctClauses =
            std::min(
                kMaximumClauseCount,
                maximumDistinctClauses +
                    clauseCapacityByAttributeCount[selected]);
    }
    auto supportsClauseCount = [&](std::size_t clauseCount) {
        return clauseCount <= maximumDistinctClauses;
    };

    std::vector<WeightedCount> effectiveClauseWeights =
        options.clauseCountWeights;
    if (effectiveClauseWeights.empty()) {
        for (std::size_t clauseCount : {1U, 2U}) {
            if (supportsClauseCount(clauseCount)) {
                effectiveClauseWeights.push_back(
                    {clauseCount, 1.0 / static_cast<double>(clauseCount)});
            }
        }
    }
    if (effectiveClauseWeights.empty()) {
        return SetError(error, "predicate catalog has no feasible clause-count stratum");
    }

    std::vector<WeightedCount> effectiveAttributeWeights =
        options.clauseAttributeCountWeights;
    if (effectiveAttributeWeights.empty()) {
        long double combinations = 1.0;
        for (std::size_t selected = 1;
             selected <= logicalAttributeCount;
             ++selected) {
            combinations =
                combinations *
                static_cast<long double>(logicalAttributeCount - selected + 1) /
                static_cast<long double>(selected);
            effectiveAttributeWeights.push_back(
                {selected, static_cast<double>(combinations)});
        }
    }

    std::mt19937_64 rng(options.seed ^ 0x7c6a180b368f923dULL);
    const std::vector<std::size_t> clauseSchedule =
        AllocateSchedule(options.queryCount, effectiveClauseWeights, &rng);
    const std::unordered_set<std::size_t> realizedClauseCounts(
        clauseSchedule.begin(), clauseSchedule.end());
    for (std::size_t clauseCount : realizedClauseCounts) {
        if (!supportsClauseCount(clauseCount)) {
            return SetError(
                error,
                "predicate catalog cannot support realized clause count " +
                    std::to_string(clauseCount));
        }
    }

    std::size_t totalClauseCount = 0;
    for (std::size_t clauseCount : clauseSchedule) {
        totalClauseCount += clauseCount;
    }
    const std::vector<std::size_t> attributeSchedule =
        AllocateSchedule(totalClauseCount, effectiveAttributeWeights, &rng);
    std::map<std::size_t, std::size_t> attributeQuotas;
    for (std::size_t attributeCount : attributeSchedule) {
        ++attributeQuotas[attributeCount];
    }
    std::vector<std::pair<std::size_t, std::size_t>> quotaOrder(
        attributeQuotas.begin(), attributeQuotas.end());
    std::sort(
        quotaOrder.begin(), quotaOrder.end(),
        [&](const auto& left, const auto& right) {
            const std::size_t leftCapacity =
                clauseCapacityByAttributeCount[left.first];
            const std::size_t rightCapacity =
                clauseCapacityByAttributeCount[right.first];
            if (leftCapacity != rightCapacity) {
                return leftCapacity < rightCapacity;
            }
            return left.first < right.first;
        });

    std::vector<std::size_t> remainingClauseSlots = clauseSchedule;
    std::vector<std::vector<std::size_t>> clauseAttributeCountsByQuery(
        clauseSchedule.size());
    for (const auto& quota : quotaOrder) {
        const std::size_t attributeCount = quota.first;
        std::size_t remaining = quota.second;
        const std::size_t perQueryCapacity =
            clauseCapacityByAttributeCount[attributeCount];
        std::vector<std::size_t> assignedForQuery(
            clauseSchedule.size(), 0);
        using AssignmentTicket =
            std::pair<std::pair<std::size_t, std::uint64_t>, std::size_t>;
        std::priority_queue<AssignmentTicket> candidates;
        for (std::size_t query = 0;
             query < remainingClauseSlots.size();
             ++query) {
            if (remainingClauseSlots[query] != 0) {
                candidates.push(
                    {{remainingClauseSlots[query], rng()}, query});
            }
        }
        while (remaining != 0) {
            if (candidates.empty()) {
                return SetError(
                    error,
                    "attribute-count quotas cannot be assigned without "
                    "collapsing DNF clauses");
            }
            const std::size_t query = candidates.top().second;
            candidates.pop();
            clauseAttributeCountsByQuery[query].push_back(attributeCount);
            --remainingClauseSlots[query];
            ++assignedForQuery[query];
            --remaining;
            if (remainingClauseSlots[query] != 0 &&
                assignedForQuery[query] < perQueryCapacity) {
                candidates.push(
                    {{remainingClauseSlots[query], rng()}, query});
            }
        }
    }
    if (std::find_if(
            remainingClauseSlots.begin(),
            remainingClauseSlots.end(),
            [](std::size_t remaining) { return remaining != 0; }) !=
        remainingClauseSlots.end()) {
        return SetError(error, "attribute-count quota assignment is incomplete");
    }
    const std::size_t generatedDNFQueries = static_cast<std::size_t>(
        std::count_if(
            clauseSchedule.begin(), clauseSchedule.end(),
            [](std::size_t count) { return count > 1; }));

    constexpr std::uint64_t kEvaluationBudget = 10000000000ULL;
    std::uint64_t scheduledAtoms = 0;
    for (std::size_t queryIndex = 0;
         queryIndex < clauseSchedule.size();
         ++queryIndex) {
        std::uint64_t queryAtoms = 0;
        for (std::size_t attributeCount :
             clauseAttributeCountsByQuery[queryIndex]) {
            queryAtoms += 2 * attributeCount;
        }
        if (queryAtoms > kEvaluationBudget ||
            scheduledAtoms > kEvaluationBudget - queryAtoms) {
            return SetError(
                error,
                "configured DNF generation exceeds the 1e10 predicate-evaluation budget");
        }
        scheduledAtoms += queryAtoms;
    }
    if (scheduledAtoms != 0 &&
        evaluationCount > kEvaluationBudget / scheduledAtoms) {
        return SetError(
            error,
            "configured DNF generation exceeds the 1e10 predicate-evaluation budget");
    }

    std::map<std::string, Query> queries;
    std::unordered_set<std::string> rejectedQueries;
    rejectedQueries.reserve(options.queryCount);
    std::uint64_t evaluatedWork = 0;
    constexpr std::size_t kAttemptsPerShape = 32;
    for (std::size_t queryIndex = 0;
         queryIndex < clauseSchedule.size();
         ++queryIndex) {
        const std::size_t clauseCount = clauseSchedule[queryIndex];
        const std::vector<std::size_t>& clauseAttributeCounts =
            clauseAttributeCountsByQuery[queryIndex];

        bool accepted = false;
        for (std::size_t attempt = 0;
             attempt < kAttemptsPerShape && !accepted;
             ++attempt) {
            std::vector<std::size_t> assignedAttributeCounts =
                clauseAttributeCounts;
            std::shuffle(
                assignedAttributeCounts.begin(), assignedAttributeCounts.end(), rng);

            Query query;
            query.clauses.resize(clauseCount);
            std::map<std::size_t, std::vector<std::size_t>> clausePositions;
            for (std::size_t clause = 0;
                 clause < assignedAttributeCounts.size();
                 ++clause) {
                clausePositions[assignedAttributeCounts[clause]].push_back(clause);
            }
            std::unordered_set<std::string> selectedClauses;
            bool builtDistinctClauses = true;
            for (const auto& positions : clausePositions) {
                const std::size_t attributeCount = positions.first;
                const auto& exactPool = exactClausePools[attributeCount];
                if (!exactPool.empty()) {
                    const std::vector<std::size_t> selected =
                        ChooseDistinct(
                            exactPool.size(),
                            positions.second.size(),
                            &rng);
                    for (std::size_t i = 0;
                         i < positions.second.size();
                         ++i) {
                        Clause clause = exactPool[selected[i]];
                        const std::string clauseText =
                            CanonicalizeClause(&clause);
                        if (!selectedClauses.insert(clauseText).second) {
                            builtDistinctClauses = false;
                            break;
                        }
                        query.clauses[positions.second[i]] =
                            std::move(clause);
                    }
                    if (!builtDistinctClauses) break;
                    continue;
                }

                for (std::size_t position : positions.second) {
                    bool selectedDistinctClause = false;
                    for (std::size_t clauseAttempt = 0;
                         clauseAttempt < 256 && !selectedDistinctClause;
                         ++clauseAttempt) {
                        Clause clause;
                        for (std::size_t selected :
                             ChooseDistinct(
                                 logicalPredicates.size(),
                                 attributeCount,
                                 &rng)) {
                            const auto& predicates =
                                logicalPredicates[selected];
                            const auto& predicate =
                                predicates[
                                    UniformIndex(&rng, predicates.size())];
                            clause.atoms.insert(
                                clause.atoms.end(),
                                predicate.begin(),
                                predicate.end());
                        }
                        const std::string clauseText =
                            CanonicalizeClause(&clause);
                        if (selectedClauses.insert(clauseText).second) {
                            query.clauses[position] = std::move(clause);
                            selectedDistinctClause = true;
                        }
                    }
                    if (!selectedDistinctClause) {
                        builtDistinctClauses = false;
                        break;
                    }
                }
                if (!builtDistinctClauses) break;
            }
            if (!builtDistinctClauses) continue;

            const std::string text = CanonicalizeQuery(&query);
            if (query.clauses.size() != clauseCount) {
                continue;
            }
            auto found = queries.find(text);
            if (found != queries.end()) {
                ++found->second.frequency;
                accepted = true;
                continue;
            }
            if (rejectedQueries.find(text) != rejectedQueries.end()) {
                continue;
            }

            std::size_t atomsPerRow = 0;
            for (const auto& clause : query.clauses) {
                atomsPerRow += clause.atoms.size();
            }
            if (atomsPerRow != 0 &&
                evaluationCount >
                    (kEvaluationBudget - evaluatedWork) / atomsPerRow) {
                return SetError(
                    error,
                    "DNF retries exhausted the 1e10 predicate-evaluation budget");
            }
            evaluatedWork +=
                static_cast<std::uint64_t>(evaluationCount * atomsPerRow);

            std::vector<bool> clauseHasMarginalMatch(
                query.clauses.size(), false);
            for (std::size_t row = 0; row < evaluationCount; ++row) {
                const std::uint32_t* sampleRow =
                    &evaluationSample[row * relevantColumns.size()];
                std::size_t firstMatchingClause = query.clauses.size();
                std::size_t matchingClauseCount = 0;
                for (std::size_t clause = 0;
                     clause < query.clauses.size();
                     ++clause) {
                    if (ClauseMatches(query.clauses[clause], sampleRow)) {
                        if (matchingClauseCount == 0) {
                            firstMatchingClause = clause;
                        }
                        ++matchingClauseCount;
                    }
                }
                if (matchingClauseCount != 0) {
                    ++query.sampleMatchCount;
                }
                if (matchingClauseCount == 1) {
                    clauseHasMarginalMatch[firstMatchingClause] = true;
                }
            }
            if (query.sampleMatchCount == 0 ||
                std::find(
                    clauseHasMarginalMatch.begin(),
                    clauseHasMarginalMatch.end(),
                    false) != clauseHasMarginalMatch.end()) {
                rejectedQueries.insert(text);
                continue;
            }

            query.frequency = 1;
            queries.emplace(text, std::move(query));
            accepted = true;
        }
        if (!accepted) {
            std::ostringstream shape;
            shape << "clauses=" << clauseCount << " attributes=";
            for (std::size_t i = 0; i < clauseAttributeCounts.size(); ++i) {
                if (i != 0) shape << ',';
                shape << clauseAttributeCounts[i];
            }
            return SetError(
                error,
                "cannot generate a sample-supported query for fixed shape " +
                    shape.str());
        }
    }

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

    auto querySelectivity = [&](const Query& query) {
        return static_cast<double>(query.sampleMatchCount) /
               static_cast<double>(evaluationCount);
    };
    auto estimatedMatchCount = [&](const Query& query) {
        return static_cast<std::uint64_t>(
            std::llround(
                querySelectivity(query) *
                static_cast<double>(vectorCount)));
    };
    std::uint64_t contentHash = 0x510e527fade682d1ULL;
    for (const auto& entry : queries) {
        const Query& query = entry.second;
        UpdatePredicateContentHash(
            &contentHash,
            query.text,
            query.frequency,
            estimatedMatchCount(query),
            querySelectivity(query));
    }

    output << "# sptag_predicate_train_set_v5\n"
           << "# source=synthetic_predicate_dnf\n"
           << "# source_id=" << options.sourceId << "\n"
           << "# key_kind=" << KeyKindName(options.keyKind) << "\n"
           << "# key_columns=" << ColumnListText(options.keyColumns) << "\n"
           << "# predicate_columns=" << ColumnListText(options.predicateColumns) << "\n"
           << "# key_clause_policy=optional\n"
           << "# clause_marginal_policy=positive_on_statistics_sample\n"
           << "# categorical_column_count=" << options.categoricalColumnCount << "\n"
           << "# vector_count=" << vectorCount << "\n"
           << "# samples_per_attribute=" << options.samplesPerAttribute << "\n"
           << "# statistics_sample_rows=" << options.statisticsSampleRows << "\n"
           << "# discovery_sample_rows_used=" << discoveryCount << "\n"
           << "# statistics_sample_rows_used=" << evaluationCount << "\n"
           << "# query_count=" << options.queryCount << "\n"
           << "# unique_query_count=" << queries.size() << "\n"
           << "# generated_dnf_query_count=" << generatedDNFQueries << "\n"
           << "# clause_count_weights=" << options.clauseCountWeightsText << "\n"
           << "# clause_attribute_count_weights="
           << options.clauseAttributeCountWeightsText << "\n"
           << "# realized_clause_counts="
           << ScheduleHistogramText(clauseSchedule) << "\n"
           << "# realized_clause_attribute_counts="
           << ScheduleHistogramText(attributeSchedule) << "\n"
           << "# content_hash="
           << PredicateContentHashText(contentHash) << "\n"
           << "# seed=" << options.seed << "\n"
           << "# selectivity_source="
           << (evaluationCount == static_cast<std::size_t>(vectorCount)
                   ? "full_tag_scan"
                   : "independent_deterministic_row_sample")
           << "\n"
           << "# match_count="
           << (evaluationCount == static_cast<std::size_t>(vectorCount)
                   ? "exact"
                   : "estimated_from_selectivity")
           << "\n"
           << "# density_filter=none\n"
           << "# grammar=DNF(|),conjunction(&),equal(=),greater(>),less_equal(<=)\n"
           << "# columns=weight\tselectivity\tmatch_count\tpredicate\n";
    output << std::setprecision(17);
    for (const auto& entry : queries) {
        const Query& query = entry.second;
        const double weight =
            static_cast<double>(query.frequency) /
            static_cast<double>(options.queryCount);
        const double selectivity = querySelectivity(query);
        output << weight << '\t'
               << selectivity << '\t'
               << estimatedMatchCount(query) << '\t'
               << query.text << '\n';
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

    if (!PublishFileWithoutReplace(tempPath, outputPath, error)) {
        if (fs::exists(outputPath)) {
            return ReuseExistingDNFTrainSet(path, vectorCount, options, summary, error);
        }
        return false;
    }

    if (summary != nullptr) {
        summary->vectorCount = vectorCount;
        summary->statisticsSampleRows = evaluationCount;
        summary->generatedQueries = options.queryCount;
        summary->generatedDNFQueries = generatedDNFQueries;
        summary->uniqueQueries = queries.size();
    }
    return true;
}

} // namespace PredicateWorkload
} // namespace SPTAG
