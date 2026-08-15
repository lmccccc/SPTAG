// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_PREDICATE_SUBSET_PLANNER_H_
#define _SPTAG_PREDICATE_SUBSET_PLANNER_H_

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <limits>
#include <map>
#include <numeric>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#ifdef _MSC_VER
#include <intrin.h>
#endif

namespace SPTAG {
namespace PredicateSubsetPlanner {

// SIFT1M UInt8 static-SPANN calibration, measured on the local single-threaded
// SearchWithACL path with 100 queries and three repetitions.
constexpr std::size_t kPlannerSampleRows = 4096;
constexpr std::size_t kPlannerMaxLeaves = 64;
constexpr std::size_t kPlannerConvergencePatience = 8;
constexpr double kBaseQueryCostMs = 0.40657040;
constexpr double kFullPopulationScanCostMs = 0.10746861;
constexpr double kAdditionalSubsetCostMs = 0.02330404;
// Cross-edge on/off differed by less than one microsecond at the median. Keep
// that measurement resolution as a conservative boundary regularizer.
constexpr double kBoundaryCutCostMs = 0.001;
constexpr double kSubsetMaintenanceCostMs = 0.0;
// Runtime route calibration for the full-precision SIFT1M index at nprobe 96.
// The common nprobe scale cancels in the pure-vs-tail comparison.
constexpr double kRuntimeHeadSearchCostMs = 0.607;
constexpr double kRuntimePostingScanCostMs = 2.141;
constexpr double kRuntimeSubsetCoordinationCostMs = 0.080;

enum class Operator : std::uint8_t {
    Equal = 0,
    Less = 1,
    LessEqual = 2,
    Greater = 3,
    GreaterEqual = 4,
};

struct Atom {
    int column = -1;
    std::uint32_t value = 0;
    Operator op = Operator::Equal;
    std::uint8_t kind = 0;
};

struct Clause {
    std::vector<int> atoms;
};

struct Query {
    double weight = 0.0;
    std::vector<Clause> clauses;
    std::string canonical;
};

struct Workload {
    int categoricalColumnCount = 0;
    std::vector<Atom> atoms;
    std::vector<Query> queries;
};

struct AffinityEdge {
    std::size_t first = 0;
    std::size_t second = 0;
    double weight = 0.0;
};

struct DecisionNode {
    int splitAtom = -1;
    int left = -1;
    int right = -1;
};

struct Snapshot {
    int leafCount = 0;
    double trainingCost = 0.0;
    double validationCost = 0.0;
    std::vector<int> activeNodes;
};

struct LeafConstraint {
    Atom atom;
    bool matches = false;
};

struct LeafAttributes {
    int leafId = -1;
    int decisionNode = -1;
    std::vector<LeafConstraint> constraints;
};

struct Plan {
    Workload workload;
    std::vector<DecisionNode> nodes;
    std::vector<int> selectedNodes;
    std::vector<int> nodeToLeaf;
    std::vector<Snapshot> snapshots;
    std::size_t sampleRows = 0;
    std::size_t sourceRows = 0;
    double startupCost = 0.0;
    double scanCost = kFullPopulationScanCostMs;
    double coordinationCost = 0.0;
    double boundaryPenalty = 0.0;
    double subsetOverhead = 0.0;
    double selectedBoundaryFraction = 0.0;
    double selectedTrainingCost = 0.0;
    double selectedValidationCost = 0.0;
    bool loadedFromFile = false;
};

struct QueryExecutionCost {
    double routedPopulationFraction = 1.0;
    std::size_t routedSubsetCount = 0;
    double subsetCostMs = 0.0;
    double globalTailCostMs = 0.0;
    bool useGlobalTail = false;
};

inline QueryExecutionCost EvaluateQueryExecutionCost(
    double routedPopulationFraction,
    std::size_t routedSubsetCount)
{
    QueryExecutionCost result;
    result.routedPopulationFraction =
        std::clamp(routedPopulationFraction, 0.0, 1.0);
    result.routedSubsetCount = routedSubsetCount;

    // A unique subset searches one local graph directly. Multi-subset pure
    // search must retrieve about 1/p global heads to retain one predicate-valid
    // posting, while tail search keeps the configured nprobe and scans the
    // complete posting. This preserves nprobe as the number of head postings
    // considered and compares the two routes at approximately equal recall.
    const double subsetCoordination =
        routedSubsetCount > 1
            ? kRuntimeSubsetCoordinationCostMs *
                  static_cast<double>(routedSubsetCount - 1)
            : 0.0;

    const double population =
        std::max(result.routedPopulationFraction, 1e-6);
    result.subsetCostMs = kRuntimeHeadSearchCostMs +
        kRuntimePostingScanCostMs * population;
    if (routedSubsetCount > 1) {
        result.subsetCostMs =
            kRuntimeHeadSearchCostMs / population +
            kRuntimePostingScanCostMs * population +
            subsetCoordination;
    }
    result.globalTailCostMs =
        kRuntimeHeadSearchCostMs +
        kRuntimePostingScanCostMs;
    result.useGlobalTail =
        routedSubsetCount > 1 &&
        result.globalTailCostMs < result.subsetCostMs;
    return result;
}

inline bool SetError(std::string* error, const std::string& message)
{
    if (error != nullptr) *error = message;
    return false;
}

inline std::string Trim(const std::string& text)
{
    std::size_t begin = 0;
    while (begin < text.size() &&
           (text[begin] == ' ' || text[begin] == '\t' ||
            text[begin] == '\r' || text[begin] == '\n')) {
        ++begin;
    }
    std::size_t end = text.size();
    while (end > begin &&
           (text[end - 1] == ' ' || text[end - 1] == '\t' ||
            text[end - 1] == '\r' || text[end - 1] == '\n')) {
        --end;
    }
    return text.substr(begin, end - begin);
}

inline const char* OperatorText(Operator op)
{
    switch (op) {
    case Operator::Equal: return "=";
    case Operator::Less: return "<";
    case Operator::LessEqual: return "<=";
    case Operator::Greater: return ">";
    case Operator::GreaterEqual: return ">=";
    }
    return "?";
}

inline bool EvaluateAtom(const Atom& atom, std::uint32_t value)
{
    switch (atom.op) {
    case Operator::Equal: return value == atom.value;
    case Operator::Less: return value < atom.value;
    case Operator::LessEqual: return value <= atom.value;
    case Operator::Greater: return value > atom.value;
    case Operator::GreaterEqual: return value >= atom.value;
    }
    return false;
}

inline std::string AtomKey(const Atom& atom)
{
    std::ostringstream output;
    output << static_cast<unsigned int>(atom.kind) << ':'
           << atom.column << ':'
           << static_cast<unsigned int>(atom.op) << ':'
           << atom.value;
    return output.str();
}

inline bool CollectRoutingNodes(
    const std::unordered_map<std::uint32_t, std::vector<int>>& tagToNodes,
    const std::unordered_map<std::string, std::vector<int>>* predicateToNodes,
    const std::vector<std::vector<Atom>>& clauses,
    std::vector<int>* output,
    bool allowColumnlessEqualityFallback = true)
{
    if (output == nullptr) return false;
    output->clear();
    if (clauses.empty()) return false;

    std::unordered_set<int> unionNodes;
    for (const std::vector<Atom>& clause : clauses) {
        if (clause.empty()) continue;
        std::unordered_set<int> clauseNodes;
        bool first = true;
        for (const Atom& atom : clause) {
            const std::vector<int>* literalNodes = nullptr;
            if (predicateToNodes != nullptr) {
                auto predicate = predicateToNodes->find(AtomKey(atom));
                if (predicate != predicateToNodes->end() &&
                    !predicate->second.empty()) {
                    literalNodes = &predicate->second;
                }
            }
            if (literalNodes == nullptr &&
                allowColumnlessEqualityFallback &&
                atom.kind == 0 &&
                atom.op == Operator::Equal) {
                auto tag = tagToNodes.find(atom.value);
                if (tag == tagToNodes.end() || tag->second.empty()) {
                    clauseNodes.clear();
                    break;
                }
                literalNodes = &tag->second;
            }
            if (literalNodes == nullptr) {
                output->clear();
                return false;
            }

            if (first) {
                clauseNodes.insert(
                    literalNodes->begin(), literalNodes->end());
                first = false;
            } else {
                std::unordered_set<int> intersection;
                for (int nodeId : *literalNodes) {
                    if (clauseNodes.count(nodeId) != 0) {
                        intersection.insert(nodeId);
                    }
                }
                clauseNodes.swap(intersection);
            }
            if (clauseNodes.empty()) break;
        }
        unionNodes.insert(clauseNodes.begin(), clauseNodes.end());
    }
    output->assign(unionNodes.begin(), unionNodes.end());
    std::sort(output->begin(), output->end());
    return !output->empty();
}

inline std::uint64_t StableHash(const std::string& text)
{
    std::uint64_t hash = 1469598103934665603ULL;
    for (unsigned char ch : text) {
        hash ^= static_cast<std::uint64_t>(ch);
        hash *= 1099511628211ULL;
    }
    return hash;
}

inline bool ParseUnsigned(const std::string& text, std::uint32_t* value)
{
    if (value == nullptr || text.empty()) return false;
    std::uint64_t parsed = 0;
    for (char ch : text) {
        if (ch < '0' || ch > '9') return false;
        parsed = parsed * 10 + static_cast<unsigned int>(ch - '0');
        if (parsed > std::numeric_limits<std::uint32_t>::max()) return false;
    }
    *value = static_cast<std::uint32_t>(parsed);
    return true;
}

inline bool ParseColumn(const std::string& text, int* column)
{
    std::uint32_t parsed = 0;
    if (!ParseUnsigned(text, &parsed) ||
        parsed > static_cast<std::uint32_t>(std::numeric_limits<int>::max())) {
        return false;
    }
    *column = static_cast<int>(parsed);
    return true;
}

inline bool ParseAtom(const std::string& text,
                      int categoricalColumnCount,
                      Atom* atom,
                      std::string* error)
{
    if (atom == nullptr) return SetError(error, "null atom output");
    const std::string input = Trim(text);
    struct Candidate {
        const char* token;
        Operator op;
    };
    const Candidate candidates[] = {
        {"<=", Operator::LessEqual},
        {">=", Operator::GreaterEqual},
        {"=", Operator::Equal},
        {"<", Operator::Less},
        {">", Operator::Greater},
    };

    std::size_t opPosition = std::string::npos;
    std::size_t opLength = 0;
    Operator op = Operator::Equal;
    for (const Candidate& candidate : candidates) {
        const std::size_t position = input.find(candidate.token);
        if (position != std::string::npos) {
            opPosition = position;
            opLength = std::char_traits<char>::length(candidate.token);
            op = candidate.op;
            break;
        }
    }
    if (opPosition == std::string::npos || opPosition == 0 ||
        opPosition + opLength >= input.size()) {
        return SetError(error, "invalid predicate atom: " + input);
    }

    int column = -1;
    std::uint32_t value = 0;
    if (!ParseColumn(input.substr(0, opPosition), &column) ||
        !ParseUnsigned(input.substr(opPosition + opLength), &value)) {
        return SetError(error, "invalid predicate atom: " + input);
    }
    if (categoricalColumnCount < 0) {
        return SetError(error, "categorical column count must be non-negative");
    }

    atom->column = column;
    atom->value = value;
    atom->op = op;
    atom->kind = static_cast<std::uint8_t>(
        column < categoricalColumnCount ? 0 : 1);
    return true;
}

inline std::vector<std::string> Split(const std::string& text, char delimiter)
{
    std::vector<std::string> parts;
    std::size_t begin = 0;
    while (begin <= text.size()) {
        const std::size_t end = text.find(delimiter, begin);
        parts.push_back(text.substr(
            begin, end == std::string::npos ? std::string::npos : end - begin));
        if (end == std::string::npos) break;
        begin = end + 1;
    }
    return parts;
}

inline std::string CanonicalizeQuery(std::vector<Clause>* clauses,
                                     const std::vector<Atom>& atoms)
{
    for (Clause& clause : *clauses) {
        std::sort(clause.atoms.begin(), clause.atoms.end(),
                  [&atoms](int left, int right) {
                      return AtomKey(atoms[left]) < AtomKey(atoms[right]);
                  });
        clause.atoms.erase(
            std::unique(clause.atoms.begin(), clause.atoms.end()),
            clause.atoms.end());
    }

    std::vector<Clause> uniqueClauses;
    for (const Clause& clause : *clauses) {
        bool duplicate = false;
        for (const Clause& existing : uniqueClauses) {
            if (existing.atoms == clause.atoms) {
                duplicate = true;
                break;
            }
        }
        if (!duplicate) uniqueClauses.push_back(clause);
    }
    clauses->swap(uniqueClauses);

    // In DNF, A subsumes A&B. Dropping the stronger duplicate keeps the
    // canonical workload and its implication relationships compact.
    std::vector<Clause> reduced;
    for (std::size_t candidateIndex = 0;
         candidateIndex < clauses->size();
         ++candidateIndex) {
        const Clause& candidate = (*clauses)[candidateIndex];
        bool subsumed = false;
        for (std::size_t otherIndex = 0;
             otherIndex < clauses->size();
             ++otherIndex) {
            if (candidateIndex == otherIndex) continue;
            const Clause& existing = (*clauses)[otherIndex];
            if (existing.atoms.size() > candidate.atoms.size()) continue;
            if (std::includes(candidate.atoms.begin(), candidate.atoms.end(),
                              existing.atoms.begin(), existing.atoms.end(),
                              [&atoms](int left, int right) {
                                  return AtomKey(atoms[left]) <
                                         AtomKey(atoms[right]);
                              })) {
                subsumed = true;
                break;
            }
        }
        if (!subsumed) reduced.push_back(candidate);
    }
    clauses->swap(reduced);
    std::sort(clauses->begin(), clauses->end(),
              [&atoms](const Clause& left, const Clause& right) {
                  const std::size_t common =
                      std::min(left.atoms.size(), right.atoms.size());
                  for (std::size_t i = 0; i < common; ++i) {
                      const std::string leftKey = AtomKey(atoms[left.atoms[i]]);
                      const std::string rightKey = AtomKey(atoms[right.atoms[i]]);
                      if (leftKey != rightKey) return leftKey < rightKey;
                  }
                  return left.atoms.size() < right.atoms.size();
              });
    clauses->erase(
        std::unique(clauses->begin(), clauses->end(),
                    [](const Clause& left, const Clause& right) {
                        return left.atoms == right.atoms;
                    }),
        clauses->end());

    std::ostringstream output;
    for (std::size_t clauseIndex = 0;
         clauseIndex < clauses->size();
         ++clauseIndex) {
        if (clauseIndex > 0) output << '|';
        const Clause& clause = (*clauses)[clauseIndex];
        for (std::size_t atomIndex = 0;
             atomIndex < clause.atoms.size();
             ++atomIndex) {
            if (atomIndex > 0) output << '&';
            const Atom& atom = atoms[clause.atoms[atomIndex]];
            output << atom.column << OperatorText(atom.op) << atom.value;
        }
    }
    return output.str();
}

inline bool ParseQuery(const std::string& predicate,
                       int categoricalColumnCount,
                       std::vector<Atom>* atoms,
                       std::unordered_map<std::string, int>* atomIds,
                       Query* query,
                       std::string* error)
{
    if (atoms == nullptr || atomIds == nullptr || query == nullptr) {
        return SetError(error, "null query parser output");
    }
    query->clauses.clear();
    for (const std::string& clauseText : Split(Trim(predicate), '|')) {
        if (Trim(clauseText).empty()) {
            return SetError(error, "predicate contains an empty DNF clause");
        }
        Clause clause;
        for (const std::string& atomText : Split(clauseText, '&')) {
            Atom atom;
            if (!ParseAtom(atomText, categoricalColumnCount, &atom, error)) {
                return false;
            }
            const std::string key = AtomKey(atom);
            auto id = atomIds->find(key);
            if (id == atomIds->end()) {
                const int next = static_cast<int>(atoms->size());
                atoms->push_back(atom);
                (*atomIds)[key] = next;
                clause.atoms.push_back(next);
            } else {
                clause.atoms.push_back(id->second);
            }
        }
        if (clause.atoms.empty()) {
            return SetError(error, "predicate contains an empty DNF clause");
        }
        query->clauses.push_back(std::move(clause));
    }
    if (query->clauses.empty()) {
        return SetError(error, "predicate contains no DNF clauses");
    }
    query->canonical = CanonicalizeQuery(&query->clauses, *atoms);
    return !query->canonical.empty();
}

inline bool ParseWeightAndPredicate(const std::string& line,
                                    double* weight,
                                    std::string* predicate)
{
    if (weight == nullptr || predicate == nullptr) return false;
    const std::vector<std::string> columns = Split(line, '\t');
    if (columns.size() >= 2) {
        char* end = nullptr;
        const double parsed = std::strtod(columns.front().c_str(), &end);
        if (end != columns.front().c_str() && *end == '\0' &&
            std::isfinite(parsed) && parsed > 0.0) {
            *weight = parsed;
            *predicate = Trim(columns.back());
            return !predicate->empty();
        }
    }

    std::istringstream input(line);
    double parsed = 0.0;
    if (input >> parsed && std::isfinite(parsed) && parsed > 0.0) {
        std::string rest;
        std::getline(input, rest);
        rest = Trim(rest);
        const std::size_t lastSpace = rest.find_last_of(" \t");
        *weight = parsed;
        *predicate = Trim(lastSpace == std::string::npos
                              ? rest
                              : rest.substr(lastSpace + 1));
        return !predicate->empty();
    }

    *weight = 1.0;
    *predicate = Trim(line);
    return !predicate->empty();
}

inline bool LoadWorkload(const std::string& path,
                         int categoricalColumnCount,
                         Workload* workload,
                         std::string* error)
{
    if (workload == nullptr) return SetError(error, "null workload output");
    std::ifstream input(path);
    if (!input) return SetError(error, "cannot open predicate workload: " + path);

    Workload parsed;
    parsed.categoricalColumnCount = categoricalColumnCount;
    std::unordered_map<std::string, int> atomIds;
    std::unordered_map<std::string, std::size_t> queryIds;
    std::string line;
    std::size_t lineNumber = 0;
    while (std::getline(input, line)) {
        ++lineNumber;
        line = Trim(line);
        if (line.empty() || line[0] == '#') continue;

        double weight = 0.0;
        std::string predicate;
        if (!ParseWeightAndPredicate(line, &weight, &predicate)) {
            return SetError(error, "invalid workload row at line " +
                                      std::to_string(lineNumber));
        }

        Query query;
        query.weight = weight;
        std::string parseError;
        if (!ParseQuery(predicate,
                        categoricalColumnCount,
                        &parsed.atoms,
                        &atomIds,
                        &query,
                        &parseError)) {
            return SetError(error, "invalid workload row at line " +
                                      std::to_string(lineNumber) + ": " +
                                      parseError);
        }
        auto existing = queryIds.find(query.canonical);
        if (existing == queryIds.end()) {
            queryIds[query.canonical] = parsed.queries.size();
            parsed.queries.push_back(std::move(query));
        } else {
            parsed.queries[existing->second].weight += weight;
        }
    }
    if (parsed.queries.empty()) {
        return SetError(error, "predicate workload contains no queries");
    }

    double totalWeight = 0.0;
    for (const Query& query : parsed.queries) totalWeight += query.weight;
    if (!(totalWeight > 0.0) || !std::isfinite(totalWeight)) {
        return SetError(error, "predicate workload has invalid total weight");
    }
    for (Query& query : parsed.queries) query.weight /= totalWeight;

    std::vector<std::uint8_t> usedAtoms(parsed.atoms.size(), 0);
    for (const Query& query : parsed.queries) {
        for (const Clause& clause : query.clauses) {
            for (int atomId : clause.atoms) {
                usedAtoms[static_cast<std::size_t>(atomId)] = 1;
            }
        }
    }
    std::vector<int> atomRemap(parsed.atoms.size(), -1);
    std::vector<Atom> compactAtoms;
    compactAtoms.reserve(parsed.atoms.size());
    for (std::size_t atomId = 0; atomId < parsed.atoms.size(); ++atomId) {
        if (!usedAtoms[atomId]) continue;
        atomRemap[atomId] = static_cast<int>(compactAtoms.size());
        compactAtoms.push_back(parsed.atoms[atomId]);
    }
    for (Query& query : parsed.queries) {
        for (Clause& clause : query.clauses) {
            for (int& atomId : clause.atoms) {
                atomId = atomRemap[static_cast<std::size_t>(atomId)];
            }
        }
    }
    parsed.atoms.swap(compactAtoms);
    *workload = std::move(parsed);
    return true;
}

class Bitmap {
public:
    Bitmap() = default;

    explicit Bitmap(std::size_t bitCount, bool fill = false)
        : m_bitCount(bitCount),
          m_words((bitCount + 63) / 64, fill ? ~std::uint64_t(0) : 0)
    {
        ClearPadding();
    }

    void Set(std::size_t index)
    {
        m_words[index >> 6] |= std::uint64_t(1) << (index & 63);
    }

    bool Test(std::size_t index) const
    {
        return index < m_bitCount &&
               (m_words[index >> 6] &
                (std::uint64_t(1) << (index & 63))) != 0;
    }

    std::uint64_t Hash() const
    {
        std::uint64_t hash = 1469598103934665603ULL;
        for (std::uint64_t word : m_words) {
            hash ^= word;
            hash *= 1099511628211ULL;
        }
        hash ^= static_cast<std::uint64_t>(m_bitCount);
        hash *= 1099511628211ULL;
        return hash;
    }

    std::size_t Count() const
    {
        std::size_t count = 0;
        for (std::uint64_t word : m_words) {
#if defined(_MSC_VER)
            count += static_cast<std::size_t>(__popcnt64(word));
#else
            count += static_cast<std::size_t>(__builtin_popcountll(word));
#endif
        }
        return count;
    }

    static std::size_t IntersectionCount(const Bitmap& left,
                                         const Bitmap& right)
    {
        std::size_t count = 0;
        const std::size_t words =
            std::min(left.m_words.size(), right.m_words.size());
        for (std::size_t i = 0; i < words; ++i) {
#if defined(_MSC_VER)
            count += static_cast<std::size_t>(
                __popcnt64(left.m_words[i] & right.m_words[i]));
#else
            count += static_cast<std::size_t>(
                __builtin_popcountll(left.m_words[i] & right.m_words[i]));
#endif
        }
        return count;
    }

    static Bitmap Intersect(const Bitmap& left, const Bitmap& right)
    {
        Bitmap result(left.m_bitCount);
        const std::size_t words =
            std::min(left.m_words.size(), right.m_words.size());
        for (std::size_t i = 0; i < words; ++i) {
            result.m_words[i] = left.m_words[i] & right.m_words[i];
        }
        return result;
    }

    static Bitmap Difference(const Bitmap& left, const Bitmap& right)
    {
        Bitmap result(left.m_bitCount);
        const std::size_t words =
            std::min(left.m_words.size(), right.m_words.size());
        for (std::size_t i = 0; i < words; ++i) {
            result.m_words[i] = left.m_words[i] & ~right.m_words[i];
        }
        for (std::size_t i = words; i < left.m_words.size(); ++i) {
            result.m_words[i] = left.m_words[i];
        }
        result.ClearPadding();
        return result;
    }

    void Or(const Bitmap& other)
    {
        const std::size_t words =
            std::min(m_words.size(), other.m_words.size());
        for (std::size_t i = 0; i < words; ++i) {
            m_words[i] |= other.m_words[i];
        }
    }

private:
    void ClearPadding()
    {
        if (m_words.empty() || (m_bitCount & 63) == 0) return;
        m_words.back() &= ((std::uint64_t(1) << (m_bitCount & 63)) - 1);
    }

    std::size_t m_bitCount = 0;
    std::vector<std::uint64_t> m_words;
};

struct WorkingNode {
    int parent = -1;
    int left = -1;
    int right = -1;
    int splitAtom = -1;
    int depth = 0;
    Bitmap rows;
    std::size_t population = 0;
    std::vector<int> pathAtoms;
};

inline bool QueryRequiresGlobalFallback(
    const Query& query,
    const std::vector<Atom>& atoms,
    const std::vector<std::size_t>& atomPopulations)
{
    for (const Clause& clause : query.clauses) {
        for (int atomId : clause.atoms) {
            const Atom& atom = atoms[static_cast<std::size_t>(atomId)];
            if (atomPopulations[static_cast<std::size_t>(atomId)] == 0 &&
                !(atom.kind == 0 && atom.op == Operator::Equal)) {
                return true;
            }
        }
    }
    return false;
}

inline std::vector<Bitmap> BuildQueryMatches(
    const std::vector<Query>& queries,
    const std::vector<Bitmap>& atomMatches,
    std::size_t sampleRows)
{
    std::vector<Bitmap> queryMatches;
    queryMatches.reserve(queries.size());
    for (const Query& query : queries) {
        Bitmap matches(sampleRows);
        for (const Clause& clause : query.clauses) {
            if (clause.atoms.empty()) continue;
            Bitmap clauseMatches(sampleRows, true);
            for (int atomId : clause.atoms) {
                clauseMatches = Bitmap::Intersect(
                    clauseMatches,
                    atomMatches[static_cast<std::size_t>(atomId)]);
            }
            matches.Or(clauseMatches);
        }
        queryMatches.push_back(std::move(matches));
    }
    return queryMatches;
}

inline double EvaluatePartitionCost(
    const std::vector<int>& activeNodes,
    const std::vector<WorkingNode>& nodes,
    const std::vector<Atom>& atoms,
    const std::vector<Query>& queries,
    const std::vector<Bitmap>& queryMatches,
    const std::vector<std::size_t>& atomPopulations,
    const std::vector<int>& queryIds,
    std::size_t sampleRows,
    double baseQueryCost,
    double scanCost,
    double additionalSubsetCost)
{
    if (sampleRows == 0 || queryIds.empty()) {
        return baseQueryCost + scanCost;
    }
    const double globalCost = baseQueryCost + scanCost;
    double weightedCost = 0.0;
    double totalWeight = 0.0;
    for (int queryId : queryIds) {
        const Query& query = queries[static_cast<std::size_t>(queryId)];
        if (QueryRequiresGlobalFallback(query, atoms, atomPopulations)) {
            weightedCost += query.weight * globalCost;
            totalWeight += query.weight;
            continue;
        }

        int touched = 0;
        std::size_t routedPopulation = 0;
        for (int nodeId : activeNodes) {
            const WorkingNode& node = nodes[static_cast<std::size_t>(nodeId)];
            if (Bitmap::IntersectionCount(
                    queryMatches[static_cast<std::size_t>(queryId)],
                    node.rows) == 0) {
                continue;
            }
            ++touched;
            routedPopulation += node.population;
        }
        if (touched == 0) {
            weightedCost += query.weight * globalCost;
        } else {
            const double routed =
                baseQueryCost +
                scanCost *
                    static_cast<double>(routedPopulation) /
                    static_cast<double>(sampleRows) +
                additionalSubsetCost *
                    static_cast<double>(std::max(0, touched - 1));
            weightedCost += query.weight * std::min(globalCost, routed);
        }
        totalWeight += query.weight;
    }
    return totalWeight > 0.0 ? weightedCost / totalWeight : globalCost;
}

template <typename DistanceAccessor>
std::vector<AffinityEdge> BuildAffinityEdges(std::size_t rowCount,
                                             DistanceAccessor distance)
{
    const std::size_t sampleRows =
        std::min<std::size_t>(rowCount, kPlannerSampleRows);
    const std::size_t affinityRows =
        std::min<std::size_t>(sampleRows, 1024);
    if (affinityRows < 2) return {};

    std::vector<std::size_t> plannerRows(affinityRows);
    std::vector<std::size_t> sourceRows(affinityRows);
    for (std::size_t i = 0; i < affinityRows; ++i) {
        plannerRows[i] =
            (static_cast<long double>(i) * sampleRows) / affinityRows;
        if (plannerRows[i] >= sampleRows) plannerRows[i] = sampleRows - 1;
        sourceRows[i] =
            (static_cast<long double>(plannerRows[i]) * rowCount) / sampleRows;
        if (sourceRows[i] >= rowCount) sourceRows[i] = rowCount - 1;
    }

    const std::size_t neighborCount =
        std::min<std::size_t>(4, affinityRows - 1);
    std::map<std::pair<std::size_t, std::size_t>, double> edgeWeights;
    for (std::size_t i = 0; i < affinityRows; ++i) {
        std::vector<std::pair<double, std::size_t>> nearest;
        nearest.reserve(neighborCount + 1);
        for (std::size_t j = 0; j < affinityRows; ++j) {
            if (i == j) continue;
            const double candidateDistance =
                distance(sourceRows[i], sourceRows[j]);
            if (!std::isfinite(candidateDistance)) continue;
            nearest.emplace_back(candidateDistance, j);
            std::sort(nearest.begin(), nearest.end());
            if (nearest.size() > neighborCount) nearest.pop_back();
        }
        for (std::size_t rank = 0; rank < nearest.size(); ++rank) {
            const std::size_t left =
                std::min(plannerRows[i], plannerRows[nearest[rank].second]);
            const std::size_t right =
                std::max(plannerRows[i], plannerRows[nearest[rank].second]);
            if (left == right) continue;
            const double weight = 1.0 / static_cast<double>(rank + 1);
            double& stored = edgeWeights[std::make_pair(left, right)];
            stored = std::max(stored, weight);
        }
    }

    std::vector<AffinityEdge> edges;
    edges.reserve(edgeWeights.size());
    for (const auto& entry : edgeWeights) {
        AffinityEdge edge;
        edge.first = entry.first.first;
        edge.second = entry.first.second;
        edge.weight = entry.second;
        edges.push_back(edge);
    }
    return edges;
}

inline double SplitBoundaryWeight(const Bitmap& parentRows,
                                  const Bitmap& trueRows,
                                  const std::vector<AffinityEdge>& edges)
{
    double weight = 0.0;
    for (const AffinityEdge& edge : edges) {
        if (!parentRows.Test(edge.first) || !parentRows.Test(edge.second)) {
            continue;
        }
        if (trueRows.Test(edge.first) != trueRows.Test(edge.second)) {
            weight += edge.weight;
        }
    }
    return weight;
}

inline std::string PartitionKey(const std::vector<int>& activeNodes,
                                const std::vector<WorkingNode>& nodes)
{
    std::vector<std::pair<std::uint64_t, std::size_t>> cells;
    cells.reserve(activeNodes.size());
    for (int nodeId : activeNodes) {
        const WorkingNode& node = nodes[static_cast<std::size_t>(nodeId)];
        cells.emplace_back(node.rows.Hash(), node.population);
    }
    std::sort(cells.begin(), cells.end());
    std::ostringstream output;
    for (const auto& cell : cells) {
        output << std::hex << cell.first << ':' << std::dec << cell.second << ';';
    }
    return output.str();
}

template <typename Accessor>
bool BuildPlan(const Workload& workload,
               std::size_t rowCount,
               int columnCount,
               Accessor accessor,
               const std::vector<AffinityEdge>& affinityEdges,
               Plan* output,
               std::string* error,
               std::size_t leafLimitOverride = 0)
{
    if (output == nullptr) return SetError(error, "null subset plan output");
    if (rowCount == 0) return SetError(error, "cannot plan an empty tag table");
    if (columnCount <= 0) return SetError(error, "tag column count must be positive");
    if (workload.queries.empty()) {
        return SetError(error, "cannot plan an empty predicate workload");
    }
    for (const Atom& atom : workload.atoms) {
        if (atom.column < 0 || atom.column >= columnCount) {
            return SetError(error, "predicate column " +
                                      std::to_string(atom.column) +
                                      " is outside the tag row");
        }
    }

    const std::size_t sampleRows =
        std::min<std::size_t>(rowCount, kPlannerSampleRows);
    std::vector<std::size_t> sampledRows(sampleRows);
    for (std::size_t i = 0; i < sampleRows; ++i) {
        sampledRows[i] = static_cast<std::size_t>(
            (static_cast<long double>(i) * rowCount) / sampleRows);
        if (sampledRows[i] >= rowCount) sampledRows[i] = rowCount - 1;
    }

    std::vector<Bitmap> atomMatches(
        workload.atoms.size(), Bitmap(sampleRows));
    for (std::size_t sampleIndex = 0;
         sampleIndex < sampleRows;
         ++sampleIndex) {
        const std::size_t sourceRow = sampledRows[sampleIndex];
        for (std::size_t atomIndex = 0;
             atomIndex < workload.atoms.size();
             ++atomIndex) {
            const Atom& atom = workload.atoms[atomIndex];
            if (EvaluateAtom(atom, accessor(sourceRow, atom.column))) {
                atomMatches[atomIndex].Set(sampleIndex);
            }
        }
    }
    std::vector<std::size_t> atomPopulations(atomMatches.size(), 0);
    for (std::size_t atomIndex = 0;
         atomIndex < atomMatches.size();
         ++atomIndex) {
        atomPopulations[atomIndex] = atomMatches[atomIndex].Count();
    }
    const std::vector<Bitmap> queryMatches =
        BuildQueryMatches(workload.queries, atomMatches, sampleRows);

    std::vector<int> trainingQueries;
    std::vector<int> validationQueries;
    for (std::size_t i = 0; i < workload.queries.size(); ++i) {
        if (StableHash(workload.queries[i].canonical) % 5 == 0) {
            validationQueries.push_back(static_cast<int>(i));
        } else {
            trainingQueries.push_back(static_cast<int>(i));
        }
    }
    if (workload.queries.size() == 1) {
        trainingQueries.clear();
        validationQueries.clear();
        trainingQueries.push_back(0);
        validationQueries.push_back(0);
    } else {
        if (trainingQueries.empty()) {
            trainingQueries.push_back(validationQueries.back());
            validationQueries.pop_back();
        }
        if (validationQueries.empty()) {
            validationQueries.push_back(trainingQueries.back());
            trainingQueries.pop_back();
        }
    }

    const double startupCost = kBaseQueryCostMs;
    const double scanCost = kFullPopulationScanCostMs;
    const double coordinationCost = kAdditionalSubsetCostMs;
    const std::size_t leafLimit =
        leafLimitOverride > 0 ? leafLimitOverride : kPlannerMaxLeaves;
    if (leafLimit > kPlannerMaxLeaves) {
        return SetError(error, "predicate planner leaf limit exceeds 64");
    }
    const double boundaryPenalty = kBoundaryCutCostMs;
    const double subsetOverhead = kSubsetMaintenanceCostMs;
    double totalAffinityWeight = 0.0;
    for (const AffinityEdge& edge : affinityEdges) {
        if (edge.first >= sampleRows || edge.second >= sampleRows ||
            edge.first == edge.second || edge.weight < 0.0 ||
            !std::isfinite(edge.weight)) {
            return SetError(error, "invalid sampled vector-affinity edge");
        }
        totalAffinityWeight += edge.weight;
    }

    std::vector<WorkingNode> nodes;
    WorkingNode root;
    root.rows = Bitmap(sampleRows, true);
    root.population = sampleRows;
    nodes.push_back(std::move(root));

    const std::size_t minimumPopulation =
        std::max<std::size_t>(1, sampleRows / (leafLimit * 8));
    const std::size_t beamWidth = 8;

    struct SearchState {
        std::vector<int> activeNodes;
        double trainingCost = 0.0;
        double validationCost = 0.0;
        double boundaryWeight = 0.0;
        int parentState = -1;
        int splitNode = -1;
        int splitAtom = -1;
        int leftNode = -1;
        int rightNode = -1;
        int maxDepth = 0;
    };
    struct Candidate {
        int parentState = -1;
        int splitNode = -1;
        int splitAtom = -1;
        WorkingNode left;
        WorkingNode right;
        double trainingCost = 0.0;
        double validationCost = 0.0;
        double boundaryWeight = 0.0;
        int maxDepth = 0;
        std::string partitionKey;
    };

    auto objective = [&](double queryCost,
                         double boundaryWeight,
                         std::size_t leaves) {
        const double boundaryFraction =
            totalAffinityWeight > 0.0
                ? boundaryWeight / totalAffinityWeight
                : 0.0;
        return queryCost + boundaryPenalty * boundaryFraction +
               subsetOverhead * static_cast<double>(leaves);
    };
    auto candidateBetter = [](const Candidate& left,
                              const Candidate& right) {
        if (left.trainingCost < right.trainingCost - 1e-12) return true;
        if (left.trainingCost > right.trainingCost + 1e-12) return false;
        if (left.maxDepth != right.maxDepth) {
            return left.maxDepth < right.maxDepth;
        }
        if (left.splitAtom != right.splitAtom) {
            return left.splitAtom < right.splitAtom;
        }
        if (left.splitNode != right.splitNode) {
            return left.splitNode < right.splitNode;
        }
        return left.partitionKey < right.partitionKey;
    };

    std::vector<SearchState> states;
    SearchState rootState;
    rootState.activeNodes.push_back(0);
    rootState.trainingCost = objective(
        EvaluatePartitionCost(
            rootState.activeNodes, nodes, workload.atoms, workload.queries,
            queryMatches, atomPopulations, trainingQueries, sampleRows,
            startupCost, scanCost, coordinationCost),
        0.0,
        1);
    rootState.validationCost = objective(
        EvaluatePartitionCost(
            rootState.activeNodes, nodes, workload.atoms, workload.queries,
            queryMatches, atomPopulations, validationQueries, sampleRows,
            startupCost, scanCost, coordinationCost),
        0.0,
        1);
    states.push_back(std::move(rootState));

    std::vector<int> beam(1, 0);
    std::vector<int> allStateIds(1, 0);
    double bestValidationCost = states[0].validationCost;
    std::size_t staleLeafCounts = 0;
    for (std::size_t leafCount = 1;
         leafCount < leafLimit && !beam.empty();
         ++leafCount) {
        std::vector<Candidate> retained;
        retained.reserve(beamWidth);
        for (int stateId : beam) {
            const SearchState& state =
                states[static_cast<std::size_t>(stateId)];
            for (int splitNode : state.activeNodes) {
                const WorkingNode& parent =
                    nodes[static_cast<std::size_t>(splitNode)];
                if (parent.population < minimumPopulation * 2) continue;
                for (std::size_t atomIndex = 0;
                     atomIndex < workload.atoms.size();
                     ++atomIndex) {
                    if (std::find(parent.pathAtoms.begin(),
                                  parent.pathAtoms.end(),
                                  static_cast<int>(atomIndex)) !=
                        parent.pathAtoms.end()) {
                        continue;
                    }
                    Bitmap trueRows =
                        Bitmap::Intersect(parent.rows, atomMatches[atomIndex]);
                    const std::size_t truePopulation = trueRows.Count();
                    const std::size_t falsePopulation =
                        parent.population - truePopulation;
                    if (truePopulation < minimumPopulation ||
                        falsePopulation < minimumPopulation) {
                        continue;
                    }

                    std::vector<int> childPath = parent.pathAtoms;
                    childPath.push_back(static_cast<int>(atomIndex));
                    WorkingNode left;
                    left.parent = splitNode;
                    left.depth = parent.depth + 1;
                    left.rows =
                        Bitmap::Difference(parent.rows, atomMatches[atomIndex]);
                    left.population = falsePopulation;
                    left.pathAtoms = childPath;
                    WorkingNode right;
                    right.parent = splitNode;
                    right.depth = parent.depth + 1;
                    right.rows = std::move(trueRows);
                    right.population = truePopulation;
                    right.pathAtoms = std::move(childPath);

                    const double addedBoundary = SplitBoundaryWeight(
                        parent.rows, right.rows, affinityEdges);
                    const int leftId = static_cast<int>(nodes.size());
                    nodes.push_back(std::move(left));
                    const int rightId = static_cast<int>(nodes.size());
                    nodes.push_back(std::move(right));
                    std::vector<int> candidateNodes;
                    candidateNodes.reserve(state.activeNodes.size() + 1);
                    for (int activeNode : state.activeNodes) {
                        if (activeNode != splitNode) {
                            candidateNodes.push_back(activeNode);
                        }
                    }
                    candidateNodes.push_back(leftId);
                    candidateNodes.push_back(rightId);
                    std::sort(candidateNodes.begin(), candidateNodes.end());

                    Candidate candidate;
                    candidate.parentState = stateId;
                    candidate.splitNode = splitNode;
                    candidate.splitAtom = static_cast<int>(atomIndex);
                    candidate.boundaryWeight =
                        state.boundaryWeight + addedBoundary;
                    candidate.maxDepth =
                        std::max(state.maxDepth, parent.depth + 1);
                    candidate.trainingCost = objective(
                        EvaluatePartitionCost(
                            candidateNodes, nodes, workload.atoms,
                            workload.queries, queryMatches, atomPopulations,
                            trainingQueries, sampleRows, startupCost,
                            scanCost, coordinationCost),
                        candidate.boundaryWeight,
                        candidateNodes.size());
                    candidate.validationCost = objective(
                        EvaluatePartitionCost(
                            candidateNodes, nodes, workload.atoms,
                            workload.queries, queryMatches, atomPopulations,
                            validationQueries, sampleRows, startupCost,
                            scanCost, coordinationCost),
                        candidate.boundaryWeight,
                        candidateNodes.size());
                    candidate.partitionKey =
                        PartitionKey(candidateNodes, nodes);
                    candidate.right = std::move(nodes.back());
                    nodes.pop_back();
                    candidate.left = std::move(nodes.back());
                    nodes.pop_back();

                    auto duplicate = std::find_if(
                        retained.begin(),
                        retained.end(),
                        [&](const Candidate& existing) {
                            return existing.partitionKey ==
                                   candidate.partitionKey;
                        });
                    if (duplicate != retained.end()) {
                        if (candidateBetter(candidate, *duplicate)) {
                            *duplicate = std::move(candidate);
                        }
                    } else if (retained.size() < beamWidth) {
                        retained.push_back(std::move(candidate));
                    } else {
                        auto worst = std::max_element(
                            retained.begin(), retained.end(), candidateBetter);
                        if (candidateBetter(candidate, *worst)) {
                            *worst = std::move(candidate);
                        }
                    }
                }
            }
        }
        std::sort(retained.begin(), retained.end(), candidateBetter);
        beam.clear();
        for (Candidate& candidate : retained) {
            const SearchState& parentState =
                states[static_cast<std::size_t>(candidate.parentState)];
            const int leftId = static_cast<int>(nodes.size());
            nodes.push_back(std::move(candidate.left));
            const int rightId = static_cast<int>(nodes.size());
            nodes.push_back(std::move(candidate.right));

            SearchState next;
            next.activeNodes.reserve(parentState.activeNodes.size() + 1);
            for (int activeNode : parentState.activeNodes) {
                if (activeNode != candidate.splitNode) {
                    next.activeNodes.push_back(activeNode);
                }
            }
            next.activeNodes.push_back(leftId);
            next.activeNodes.push_back(rightId);
            std::sort(next.activeNodes.begin(), next.activeNodes.end());
            next.trainingCost = candidate.trainingCost;
            next.validationCost = candidate.validationCost;
            next.boundaryWeight = candidate.boundaryWeight;
            next.parentState = candidate.parentState;
            next.splitNode = candidate.splitNode;
            next.splitAtom = candidate.splitAtom;
            next.leftNode = leftId;
            next.rightNode = rightId;
            next.maxDepth = candidate.maxDepth;
            const int stateId = static_cast<int>(states.size());
            states.push_back(std::move(next));
            beam.push_back(stateId);
            allStateIds.push_back(stateId);
        }
        double layerBestValidation = std::numeric_limits<double>::infinity();
        for (int stateId : beam) {
            layerBestValidation = std::min(
                layerBestValidation,
                states[static_cast<std::size_t>(stateId)].validationCost);
        }
        if (layerBestValidation < bestValidationCost - 1e-12) {
            bestValidationCost = layerBestValidation;
            staleLeafCounts = 0;
        } else {
            ++staleLeafCounts;
            if (staleLeafCounts >= kPlannerConvergencePatience) break;
        }
    }

    int selectedState = 0;
    for (int stateId : allStateIds) {
        const SearchState& candidate =
            states[static_cast<std::size_t>(stateId)];
        const SearchState& selected =
            states[static_cast<std::size_t>(selectedState)];
        if (candidate.validationCost < selected.validationCost - 1e-12 ||
            (std::fabs(candidate.validationCost -
                       selected.validationCost) <= 1e-12 &&
             (candidate.activeNodes.size() < selected.activeNodes.size() ||
              (candidate.activeNodes.size() == selected.activeNodes.size() &&
               (candidate.maxDepth < selected.maxDepth ||
                (candidate.maxDepth == selected.maxDepth &&
                 candidate.trainingCost < selected.trainingCost - 1e-12)))))) {
            selectedState = stateId;
        }
    }

    std::vector<int> stateChain;
    for (int stateId = selectedState;
         stateId >= 0;
         stateId = states[static_cast<std::size_t>(stateId)].parentState) {
        stateChain.push_back(stateId);
    }
    std::reverse(stateChain.begin(), stateChain.end());

    Plan plan;
    plan.workload = workload;
    plan.nodes.emplace_back();
    std::unordered_map<int, int> cellToDecisionNode;
    cellToDecisionNode.emplace(0, 0);
    Snapshot rootSnapshot;
    rootSnapshot.leafCount = 1;
    rootSnapshot.trainingCost = states[0].trainingCost;
    rootSnapshot.validationCost = states[0].validationCost;
    rootSnapshot.activeNodes.push_back(0);
    plan.snapshots.push_back(std::move(rootSnapshot));
    for (std::size_t chainIndex = 1;
         chainIndex < stateChain.size();
         ++chainIndex) {
        const SearchState& state =
            states[static_cast<std::size_t>(stateChain[chainIndex])];
        auto parentIt = cellToDecisionNode.find(state.splitNode);
        if (parentIt == cellToDecisionNode.end()) {
            return SetError(error, "beam state cannot be compiled into a tree");
        }
        const int decisionParent = parentIt->second;
        const int decisionLeft = static_cast<int>(plan.nodes.size());
        plan.nodes.emplace_back();
        const int decisionRight = static_cast<int>(plan.nodes.size());
        plan.nodes.emplace_back();
        plan.nodes[static_cast<std::size_t>(decisionParent)].splitAtom =
            state.splitAtom;
        plan.nodes[static_cast<std::size_t>(decisionParent)].left =
            decisionLeft;
        plan.nodes[static_cast<std::size_t>(decisionParent)].right =
            decisionRight;
        cellToDecisionNode.erase(parentIt);
        cellToDecisionNode.emplace(state.leftNode, decisionLeft);
        cellToDecisionNode.emplace(state.rightNode, decisionRight);

        Snapshot snapshot;
        snapshot.leafCount = static_cast<int>(state.activeNodes.size());
        snapshot.trainingCost = state.trainingCost;
        snapshot.validationCost = state.validationCost;
        for (int cell : state.activeNodes) {
            auto decisionIt = cellToDecisionNode.find(cell);
            if (decisionIt == cellToDecisionNode.end()) {
                return SetError(
                    error, "beam cut cannot be compiled into a decision tree");
            }
            snapshot.activeNodes.push_back(decisionIt->second);
        }
        std::sort(snapshot.activeNodes.begin(), snapshot.activeNodes.end());
        plan.snapshots.push_back(std::move(snapshot));
    }
    plan.selectedNodes = plan.snapshots.back().activeNodes;
    plan.nodeToLeaf.assign(plan.nodes.size(), -1);
    for (std::size_t leaf = 0; leaf < plan.selectedNodes.size(); ++leaf) {
        plan.nodeToLeaf[static_cast<std::size_t>(plan.selectedNodes[leaf])] =
            static_cast<int>(leaf);
    }
    plan.sampleRows = sampleRows;
    plan.sourceRows = rowCount;
    plan.startupCost = startupCost;
    plan.scanCost = scanCost;
    plan.coordinationCost = coordinationCost;
    plan.boundaryPenalty = boundaryPenalty;
    plan.subsetOverhead = subsetOverhead;
    const SearchState& selected =
        states[static_cast<std::size_t>(selectedState)];
    plan.selectedBoundaryFraction =
        totalAffinityWeight > 0.0
            ? selected.boundaryWeight / totalAffinityWeight
            : 0.0;
    plan.selectedTrainingCost = selected.trainingCost;
    plan.selectedValidationCost = selected.validationCost;
    *output = std::move(plan);
    return true;
}

template <typename Accessor>
bool BuildPlan(const Workload& workload,
               std::size_t rowCount,
               int columnCount,
               Accessor accessor,
               Plan* output,
               std::string* error)
{
    return BuildPlan(
        workload,
        rowCount,
        columnCount,
        accessor,
        std::vector<AffinityEdge>(),
        output,
        error);
}

template <typename Accessor>
int AssignRow(const Plan& plan, Accessor accessor)
{
    if (plan.nodes.empty()) return -1;
    int nodeId = 0;
    while (nodeId >= 0 &&
           nodeId < static_cast<int>(plan.nodes.size())) {
        const int leafId =
            plan.nodeToLeaf[static_cast<std::size_t>(nodeId)];
        if (leafId >= 0) return leafId;
        const DecisionNode& node =
            plan.nodes[static_cast<std::size_t>(nodeId)];
        if (node.splitAtom < 0 ||
            node.splitAtom >= static_cast<int>(plan.workload.atoms.size())) {
            return -1;
        }
        const Atom& atom =
            plan.workload.atoms[static_cast<std::size_t>(node.splitAtom)];
        nodeId = EvaluateAtom(atom, accessor(atom.column))
                     ? node.right
                     : node.left;
    }
    return -1;
}

inline bool BuildLeafAttributes(const Plan& plan,
                                std::vector<LeafAttributes>* attributes,
                                std::string* error)
{
    if (attributes == nullptr) {
        return SetError(error, "null subset attribute output");
    }
    attributes->clear();
    if (plan.nodes.empty() || plan.nodeToLeaf.size() != plan.nodes.size()) {
        return SetError(error, "invalid decision tree for subset attributes");
    }

    std::vector<LeafConstraint> path;
    std::function<bool(int)> visit = [&](int nodeId) {
        if (nodeId < 0 || nodeId >= static_cast<int>(plan.nodes.size())) {
            return false;
        }
        const int leafId =
            plan.nodeToLeaf[static_cast<std::size_t>(nodeId)];
        if (leafId >= 0) {
            LeafAttributes leaf;
            leaf.leafId = leafId;
            leaf.decisionNode = nodeId;
            leaf.constraints = path;
            attributes->push_back(std::move(leaf));
            return true;
        }
        const DecisionNode& node =
            plan.nodes[static_cast<std::size_t>(nodeId)];
        if (node.splitAtom < 0 ||
            node.splitAtom >= static_cast<int>(plan.workload.atoms.size())) {
            return false;
        }
        LeafConstraint constraint;
        constraint.atom =
            plan.workload.atoms[static_cast<std::size_t>(node.splitAtom)];
        constraint.matches = false;
        path.push_back(constraint);
        if (!visit(node.left)) return false;
        path.back().matches = true;
        if (!visit(node.right)) return false;
        path.pop_back();
        return true;
    };
    if (!visit(0) || attributes->size() != plan.selectedNodes.size()) {
        attributes->clear();
        return SetError(error, "cannot derive all subset attributes");
    }
    std::sort(
        attributes->begin(),
        attributes->end(),
        [](const LeafAttributes& left, const LeafAttributes& right) {
            return left.leafId < right.leafId;
        });
    return true;
}

inline bool SaveLeafAttributes(const std::string& path,
                               const Plan& plan,
                               std::string* error)
{
    std::vector<LeafAttributes> attributes;
    if (!BuildLeafAttributes(plan, &attributes, error)) return false;

    namespace fs = std::filesystem;
    const auto nonce =
        std::chrono::steady_clock::now().time_since_epoch().count();
    const fs::path finalPath(path);
    const fs::path tempPath =
        path + ".tmp." + std::to_string(nonce);
    std::ofstream output(tempPath, std::ios::binary | std::ios::trunc);
    if (!output) {
        return SetError(
            error, "cannot write temporary subset attributes: " +
                       tempPath.string());
    }

    const char magic[8] = {'S', 'P', 'T', 'A', 'T', 'T', '1', '\0'};
    const std::uint32_t version = 1;
    const std::uint32_t leafCount =
        static_cast<std::uint32_t>(attributes.size());
    output.write(magic, sizeof(magic));
    output.write(
        reinterpret_cast<const char*>(&version), sizeof(version));
    output.write(
        reinterpret_cast<const char*>(&leafCount), sizeof(leafCount));
    for (const LeafAttributes& leaf : attributes) {
        const std::int32_t leafId = leaf.leafId;
        const std::int32_t decisionNode = leaf.decisionNode;
        const std::uint32_t constraintCount =
            static_cast<std::uint32_t>(leaf.constraints.size());
        output.write(
            reinterpret_cast<const char*>(&leafId), sizeof(leafId));
        output.write(
            reinterpret_cast<const char*>(&decisionNode),
            sizeof(decisionNode));
        output.write(
            reinterpret_cast<const char*>(&constraintCount),
            sizeof(constraintCount));
        for (const LeafConstraint& constraint : leaf.constraints) {
            const std::int32_t column = constraint.atom.column;
            const std::uint8_t op =
                static_cast<std::uint8_t>(constraint.atom.op);
            const std::uint8_t matches = constraint.matches ? 1 : 0;
            output.write(
                reinterpret_cast<const char*>(&column), sizeof(column));
            output.write(
                reinterpret_cast<const char*>(&constraint.atom.value),
                sizeof(constraint.atom.value));
            output.write(
                reinterpret_cast<const char*>(&op), sizeof(op));
            output.write(
                reinterpret_cast<const char*>(&constraint.atom.kind),
                sizeof(constraint.atom.kind));
            output.write(
                reinterpret_cast<const char*>(&matches), sizeof(matches));
        }
    }
    output.flush();
    if (!output) {
        output.close();
        std::error_code removeError;
        fs::remove(tempPath, removeError);
        return SetError(error, "failed writing subset attributes: " + path);
    }
    output.close();
    std::error_code renameError;
    fs::rename(tempPath, finalPath, renameError);
    if (renameError) {
#ifdef _WIN32
        std::error_code removeError;
        fs::remove(finalPath, removeError);
        renameError.clear();
        fs::rename(tempPath, finalPath, renameError);
#endif
    }
    if (renameError) {
        std::error_code removeError;
        fs::remove(tempPath, removeError);
        return SetError(
            error, "cannot publish subset attributes: " +
                       renameError.message());
    }
    return true;
}

inline bool SavePlan(const std::string& path,
                     const Plan& plan,
                     std::string* error)
{
    if (plan.nodes.empty() || plan.selectedNodes.empty() ||
        plan.selectedNodes.size() > 64) {
        return SetError(error, "cannot save an incomplete predicate subset plan");
    }
    namespace fs = std::filesystem;
    const auto nonce =
        std::chrono::steady_clock::now().time_since_epoch().count();
    const fs::path finalPath(path);
    const fs::path tempPath =
        path + ".tmp." + std::to_string(nonce);
    std::ofstream output(tempPath, std::ios::binary | std::ios::trunc);
    if (!output) {
        return SetError(
            error, "cannot write temporary subset plan: " + tempPath.string());
    }

    const char magic[8] = {'S', 'P', 'T', 'P', 'L', 'N', '1', '\0'};
    const std::uint32_t version = 1;
    const std::int32_t categoricalColumnCount =
        plan.workload.categoricalColumnCount;
    const std::uint64_t sourceRows =
        static_cast<std::uint64_t>(plan.sourceRows);
    const std::uint64_t sampleRows =
        static_cast<std::uint64_t>(plan.sampleRows);
    const std::uint32_t atomCount =
        static_cast<std::uint32_t>(plan.workload.atoms.size());
    const std::uint32_t nodeCount =
        static_cast<std::uint32_t>(plan.nodes.size());
    const std::uint32_t selectedCount =
        static_cast<std::uint32_t>(plan.selectedNodes.size());
    output.write(magic, sizeof(magic));
    output.write(reinterpret_cast<const char*>(&version), sizeof(version));
    output.write(
        reinterpret_cast<const char*>(&categoricalColumnCount),
        sizeof(categoricalColumnCount));
    output.write(
        reinterpret_cast<const char*>(&sourceRows), sizeof(sourceRows));
    output.write(
        reinterpret_cast<const char*>(&sampleRows), sizeof(sampleRows));
    output.write(
        reinterpret_cast<const char*>(&atomCount), sizeof(atomCount));
    output.write(
        reinterpret_cast<const char*>(&nodeCount), sizeof(nodeCount));
    output.write(
        reinterpret_cast<const char*>(&selectedCount), sizeof(selectedCount));
    output.write(
        reinterpret_cast<const char*>(&plan.startupCost),
        sizeof(plan.startupCost));
    output.write(
        reinterpret_cast<const char*>(&plan.coordinationCost),
        sizeof(plan.coordinationCost));
    output.write(
        reinterpret_cast<const char*>(&plan.selectedTrainingCost),
        sizeof(plan.selectedTrainingCost));
    output.write(
        reinterpret_cast<const char*>(&plan.selectedValidationCost),
        sizeof(plan.selectedValidationCost));

    for (const Atom& atom : plan.workload.atoms) {
        const std::int32_t column = atom.column;
        const std::uint8_t op = static_cast<std::uint8_t>(atom.op);
        output.write(
            reinterpret_cast<const char*>(&column), sizeof(column));
        output.write(
            reinterpret_cast<const char*>(&atom.value), sizeof(atom.value));
        output.write(reinterpret_cast<const char*>(&op), sizeof(op));
        output.write(
            reinterpret_cast<const char*>(&atom.kind), sizeof(atom.kind));
    }
    for (const DecisionNode& node : plan.nodes) {
        const std::int32_t values[3] = {
            node.splitAtom, node.left, node.right};
        output.write(
            reinterpret_cast<const char*>(values), sizeof(values));
    }
    for (int selectedNode : plan.selectedNodes) {
        const std::int32_t value = selectedNode;
        output.write(
            reinterpret_cast<const char*>(&value), sizeof(value));
    }
    output.flush();
    if (!output) {
        output.close();
        std::error_code removeError;
        fs::remove(tempPath, removeError);
        return SetError(error, "failed writing subset plan: " + path);
    }
    output.close();
    if (output.fail()) {
        std::error_code removeError;
        fs::remove(tempPath, removeError);
        return SetError(error, "failed closing subset plan: " + path);
    }
    std::error_code renameError;
    fs::rename(tempPath, finalPath, renameError);
    if (renameError) {
#ifdef _WIN32
        std::error_code removeError;
        fs::remove(finalPath, removeError);
        renameError.clear();
        fs::rename(tempPath, finalPath, renameError);
#endif
    }
    if (renameError) {
        std::error_code removeError;
        fs::remove(tempPath, removeError);
        return SetError(
            error, "cannot publish subset plan: " + renameError.message());
    }
    return true;
}

inline bool ValidateSelectedCut(const Plan& plan)
{
    if (plan.nodes.empty() || plan.selectedNodes.empty()) return false;
    std::vector<std::uint8_t> selected(plan.nodes.size(), 0);
    for (int nodeId : plan.selectedNodes) {
        if (nodeId < 0 || nodeId >= static_cast<int>(plan.nodes.size()) ||
            selected[static_cast<std::size_t>(nodeId)] != 0) {
            return false;
        }
        selected[static_cast<std::size_t>(nodeId)] = 1;
    }
    std::vector<std::uint8_t> visiting(plan.nodes.size(), 0);
    std::size_t reachedSelected = 0;
    std::function<bool(int)> visit = [&](int nodeId) {
        if (nodeId < 0 || nodeId >= static_cast<int>(plan.nodes.size())) {
            return false;
        }
        if (selected[static_cast<std::size_t>(nodeId)] != 0) {
            ++reachedSelected;
            return true;
        }
        if (visiting[static_cast<std::size_t>(nodeId)] != 0) return false;
        visiting[static_cast<std::size_t>(nodeId)] = 1;
        const DecisionNode& node =
            plan.nodes[static_cast<std::size_t>(nodeId)];
        const bool valid =
            node.splitAtom >= 0 &&
            node.splitAtom <
                static_cast<int>(plan.workload.atoms.size()) &&
            visit(node.left) &&
            visit(node.right);
        visiting[static_cast<std::size_t>(nodeId)] = 0;
        return valid;
    };
    return visit(0) && reachedSelected == plan.selectedNodes.size();
}

inline bool LoadPlan(const std::string& path,
                     Plan* plan,
                     std::string* error)
{
    if (plan == nullptr) return SetError(error, "null subset plan output");
    std::ifstream input(path, std::ios::binary);
    if (!input) return SetError(error, "cannot open subset plan: " + path);

    char magic[8] = {};
    std::uint32_t version = 0;
    std::int32_t categoricalColumnCount = 0;
    std::uint64_t sourceRows = 0;
    std::uint64_t sampleRows = 0;
    std::uint32_t atomCount = 0;
    std::uint32_t nodeCount = 0;
    std::uint32_t selectedCount = 0;
    Plan loaded;
    input.read(magic, sizeof(magic));
    input.read(reinterpret_cast<char*>(&version), sizeof(version));
    input.read(
        reinterpret_cast<char*>(&categoricalColumnCount),
        sizeof(categoricalColumnCount));
    input.read(reinterpret_cast<char*>(&sourceRows), sizeof(sourceRows));
    input.read(reinterpret_cast<char*>(&sampleRows), sizeof(sampleRows));
    input.read(reinterpret_cast<char*>(&atomCount), sizeof(atomCount));
    input.read(reinterpret_cast<char*>(&nodeCount), sizeof(nodeCount));
    input.read(reinterpret_cast<char*>(&selectedCount), sizeof(selectedCount));
    input.read(
        reinterpret_cast<char*>(&loaded.startupCost),
        sizeof(loaded.startupCost));
    input.read(
        reinterpret_cast<char*>(&loaded.coordinationCost),
        sizeof(loaded.coordinationCost));
    input.read(
        reinterpret_cast<char*>(&loaded.selectedTrainingCost),
        sizeof(loaded.selectedTrainingCost));
    input.read(
        reinterpret_cast<char*>(&loaded.selectedValidationCost),
        sizeof(loaded.selectedValidationCost));
    const char expectedMagic[8] =
        {'S', 'P', 'T', 'P', 'L', 'N', '1', '\0'};
    if (!input ||
        !std::equal(magic, magic + sizeof(magic), expectedMagic) ||
        version != 1 ||
        categoricalColumnCount < 0 ||
        sourceRows == 0 ||
        sampleRows == 0 ||
        sampleRows > sourceRows ||
        atomCount == 0 ||
        atomCount > 1000000 ||
        nodeCount == 0 ||
        nodeCount > 127 ||
        selectedCount == 0 ||
        selectedCount > 64 ||
        selectedCount > nodeCount) {
        return SetError(error, "invalid predicate subset plan header: " + path);
    }

    loaded.workload.categoricalColumnCount = categoricalColumnCount;
    loaded.workload.atoms.resize(atomCount);
    loaded.nodes.resize(nodeCount);
    loaded.selectedNodes.resize(selectedCount);
    for (Atom& atom : loaded.workload.atoms) {
        std::int32_t column = -1;
        std::uint8_t op = 0;
        input.read(reinterpret_cast<char*>(&column), sizeof(column));
        input.read(reinterpret_cast<char*>(&atom.value), sizeof(atom.value));
        input.read(reinterpret_cast<char*>(&op), sizeof(op));
        input.read(reinterpret_cast<char*>(&atom.kind), sizeof(atom.kind));
        if (!input || column < 0 ||
            op > static_cast<std::uint8_t>(Operator::GreaterEqual) ||
            atom.kind > 1) {
            return SetError(error, "invalid atom in subset plan: " + path);
        }
        atom.column = column;
        atom.op = static_cast<Operator>(op);
    }
    for (DecisionNode& node : loaded.nodes) {
        std::int32_t values[3] = {};
        input.read(reinterpret_cast<char*>(values), sizeof(values));
        if (!input) {
            return SetError(error, "truncated subset plan tree: " + path);
        }
        node.splitAtom = values[0];
        node.left = values[1];
        node.right = values[2];
    }
    for (int& selectedNode : loaded.selectedNodes) {
        std::int32_t value = -1;
        input.read(reinterpret_cast<char*>(&value), sizeof(value));
        if (!input) {
            return SetError(error, "truncated subset plan cut: " + path);
        }
        selectedNode = value;
    }
    char trailing = 0;
    if (input.read(&trailing, 1)) {
        return SetError(error, "subset plan has trailing data: " + path);
    }

    loaded.sourceRows = static_cast<std::size_t>(sourceRows);
    loaded.sampleRows = static_cast<std::size_t>(sampleRows);
    loaded.nodeToLeaf.assign(loaded.nodes.size(), -1);
    for (std::size_t leaf = 0;
         leaf < loaded.selectedNodes.size();
         ++leaf) {
        const int nodeId = loaded.selectedNodes[leaf];
        if (nodeId < 0 || nodeId >= static_cast<int>(loaded.nodes.size())) {
            return SetError(error, "invalid selected node in subset plan: " + path);
        }
        loaded.nodeToLeaf[static_cast<std::size_t>(nodeId)] =
            static_cast<int>(leaf);
    }
    loaded.loadedFromFile = true;
    if (!ValidateSelectedCut(loaded)) {
        return SetError(error, "invalid predicate subset plan tree cut: " + path);
    }
    *plan = std::move(loaded);
    return true;
}

inline bool WriteManifest(const std::string& path,
                          const Plan& plan,
                          std::string* error)
{
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    if (!output) return SetError(error, "cannot write subset plan: " + path);
    output << "# sptag_predicate_subset_plan_v1\n"
           << "# source_rows=" << plan.sourceRows << "\n"
           << "# sample_rows=" << plan.sampleRows << "\n"
           << "# atom_count=" << plan.workload.atoms.size() << "\n"
           << "# query_count=" << plan.workload.queries.size() << "\n"
           << "# selected_leaf_count=" << plan.selectedNodes.size() << "\n"
           << "# cost_unit=milliseconds\n"
           << "# base_query_cost=" << std::setprecision(17)
           << plan.startupCost << "\n"
           << "# full_population_scan_cost=" << plan.scanCost << "\n"
           << "# additional_subset_cost=" << plan.coordinationCost << "\n"
           << "# planner=global_beam_hypergraph\n"
           << "# boundary_penalty=" << plan.boundaryPenalty << "\n"
           << "# subset_overhead=" << plan.subsetOverhead << "\n"
           << "# selected_boundary_fraction="
           << plan.selectedBoundaryFraction << "\n"
           << "# selected_training_cost=" << plan.selectedTrainingCost << "\n"
           << "# selected_validation_cost=" << plan.selectedValidationCost << "\n"
           << "# columns=leaf_count\ttraining_cost\tvalidation_cost\tactive_nodes\n";
    for (const Snapshot& snapshot : plan.snapshots) {
        output << snapshot.leafCount << '\t'
               << snapshot.trainingCost << '\t'
               << snapshot.validationCost << '\t';
        for (std::size_t i = 0; i < snapshot.activeNodes.size(); ++i) {
            if (i > 0) output << ',';
            output << snapshot.activeNodes[i];
        }
        output << '\n';
    }
    if (!output) return SetError(error, "failed writing subset plan: " + path);
    return true;
}

} // namespace PredicateSubsetPlanner
} // namespace SPTAG

#endif // _SPTAG_PREDICATE_SUBSET_PLANNER_H_
