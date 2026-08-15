// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/PredicateWorkload.h"
#include "inc/PredicateWorkloadDNF.h"
#include "inc/Test.h"

#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

namespace {

struct ScopedTempDir {
    ScopedTempDir()
    {
        const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
        path = std::filesystem::temp_directory_path() /
               ("sptag_predicate_workload_" + std::to_string(nonce));
        std::filesystem::create_directories(path);
    }

    ~ScopedTempDir()
    {
        std::error_code ec;
        std::filesystem::remove_all(path, ec);
    }

    std::filesystem::path path;
};

std::string ReadFile(const std::filesystem::path& path)
{
    std::ifstream input(path, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(input),
                       std::istreambuf_iterator<char>());
}

} // namespace

BOOST_AUTO_TEST_SUITE(PredicateWorkloadTest)

BOOST_AUTO_TEST_CASE(GeneratesUniformAclHierarchySample)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "predicate_train.tsv";
    const std::vector<std::uint32_t> tags = {
        1, 10,
        1, 11,
        2, 12,
        2, 13,
        3, 14,
        3, 15,
    };

    SPTAG::PredicateWorkload::SyntheticKeyOptions options;
    options.keyAttribute = "0,1";
    options.sourceId = "hierarchy-test";
    options.keyColumns = {0, 1};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Hierarchy;
    options.samplesPerColumn = 2;
    options.seed = 7;

    SPTAG::PredicateWorkload::TrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE(SPTAG::PredicateWorkload::EnsureSyntheticKeyTrainSet(
        output.string(), tags.data(), 6, 2, options, &summary, &error));
    BOOST_CHECK(!summary.reusedExisting);
    BOOST_CHECK_EQUAL(summary.sampledPredicates, 4);
    BOOST_REQUIRE_EQUAL(summary.sampledDomainValuesPerColumn.size(), 2);
    BOOST_CHECK_EQUAL(summary.sampledDomainValuesPerColumn[0], 2);
    BOOST_CHECK_EQUAL(summary.sampledDomainValuesPerColumn[1], 2);

    std::ifstream input(output);
    BOOST_REQUIRE(input.good());
    std::string line;
    int dataLines = 0;
    int column0Lines = 0;
    int column1Lines = 0;
    double weightSum = 0.0;
    while (std::getline(input, line)) {
        if (line.empty() || line[0] == '#') continue;
        ++dataLines;
        std::istringstream row(line);
        double weight = 0.0;
        double selectivity = 0.0;
        std::uint64_t matchCount = 0;
        std::string predicate;
        BOOST_REQUIRE(row >> weight >> selectivity >> matchCount >> predicate);
        weightSum += weight;
        if (predicate.rfind("0=", 0) == 0) ++column0Lines;
        if (predicate.rfind("1=", 0) == 0) ++column1Lines;
        BOOST_CHECK(selectivity > 0.0);
        BOOST_CHECK(selectivity < 1.0);
        BOOST_CHECK(matchCount > 0);
    }
    BOOST_CHECK_EQUAL(dataLines, 4);
    BOOST_CHECK_EQUAL(column0Lines, 2);
    BOOST_CHECK_EQUAL(column1Lines, 2);
    BOOST_CHECK_CLOSE(weightSum, 1.0, 1e-9);
}

BOOST_AUTO_TEST_CASE(ReusesExistingTrainSet)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "external.tsv";
    {
        std::ofstream existing(output);
        existing << "external predicate workload\n";
    }

    SPTAG::PredicateWorkload::SyntheticKeyOptions options;
    options.keyAttribute = "0";
    options.sourceId = "external-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;

    SPTAG::PredicateWorkload::TrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE(SPTAG::PredicateWorkload::EnsureSyntheticKeyTrainSet(
        output.string(), nullptr, 0, 0, options, &summary, &error));
    BOOST_CHECK(summary.reusedExisting);
    BOOST_CHECK_EQUAL(ReadFile(output), "external predicate workload\n");
}

BOOST_AUTO_TEST_CASE(AtomicModeRejectsGeneratedDNFFile)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "generated_dnf.tsv";
    {
        std::ofstream existing(output);
        existing << "# sptag_predicate_train_set_v2\n";
    }

    SPTAG::PredicateWorkload::SyntheticKeyOptions options;
    options.keyAttribute = "0";
    options.sourceId = "atomic-v2-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;

    SPTAG::PredicateWorkload::TrainSetSummary summary;
    std::string error;
    BOOST_CHECK(!SPTAG::PredicateWorkload::EnsureSyntheticKeyTrainSet(
        output.string(), nullptr, 0, 0, options, &summary, &error));
    BOOST_CHECK(error.find("incompatible format") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(GeneratesNumericRangeQuantiles)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "range.tsv";
    const std::vector<std::uint32_t> tags = {0, 1, 2, 3, 4, 5, 6, 7};

    SPTAG::PredicateWorkload::SyntheticKeyOptions options;
    options.keyAttribute = "0";
    options.sourceId = "range-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Range;
    options.samplesPerColumn = 3;

    SPTAG::PredicateWorkload::TrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE(SPTAG::PredicateWorkload::EnsureSyntheticKeyTrainSet(
        output.string(), tags.data(), 8, 1, options, &summary, &error));
    BOOST_CHECK_EQUAL(summary.sampledPredicates, 3);

    const std::string content = ReadFile(output);
    BOOST_CHECK(content.find("# key_kind=range\n") != std::string::npos);
    BOOST_CHECK(content.find("\t0<=1\n") != std::string::npos);
    BOOST_CHECK(content.find("\t0<=3\n") != std::string::npos);
    BOOST_CHECK(content.find("\t0<=5\n") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(RejectsStaleGeneratedTrainSet)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "generated.tsv";
    const std::vector<std::uint32_t> tags = {1, 1, 2, 2};

    SPTAG::PredicateWorkload::SyntheticKeyOptions options;
    options.keyAttribute = "0";
    options.sourceId = "stale-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.samplesPerColumn = 2;
    options.seed = 11;

    SPTAG::PredicateWorkload::TrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE(SPTAG::PredicateWorkload::EnsureSyntheticKeyTrainSet(
        output.string(), tags.data(), 4, 1, options, &summary, &error));

    ++options.seed;
    BOOST_CHECK(!SPTAG::PredicateWorkload::EnsureSyntheticKeyTrainSet(
        output.string(), tags.data(), 4, 1, options, &summary, &error));
    BOOST_CHECK(error.find("incompatible seed") != std::string::npos);

    --options.seed;
    options.sourceId = "changed-source";
    BOOST_CHECK(!SPTAG::PredicateWorkload::EnsureSyntheticKeyTrainSet(
        output.string(), tags.data(), 4, 1, options, &summary, &error));
    BOOST_CHECK(error.find("incompatible source_id") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(BoundsHighCardinalityLabelSampling)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "labels.tsv";
    std::vector<std::uint32_t> tags(10000);
    for (std::size_t i = 0; i < tags.size(); ++i) {
        tags[i] = static_cast<std::uint32_t>(i);
    }

    SPTAG::PredicateWorkload::SyntheticKeyOptions options;
    options.keyAttribute = "0";
    options.sourceId = "high-cardinality-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.samplesPerColumn = 8;

    SPTAG::PredicateWorkload::TrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE(SPTAG::PredicateWorkload::EnsureSyntheticKeyTrainSet(
        output.string(), tags.data(), tags.size(), 1, options, &summary, &error));
    BOOST_CHECK_EQUAL(summary.sampledPredicates, 8);
    BOOST_REQUIRE_EQUAL(summary.sampledDomainValuesPerColumn.size(), 1);
    BOOST_CHECK_EQUAL(summary.sampledDomainValuesPerColumn[0], 8);
}

BOOST_AUTO_TEST_CASE(GeneratesKeyAnchoredMultiAttributeDNF)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "dnf.tsv";
    std::vector<std::uint32_t> tags;
    for (std::uint32_t row = 0; row < 16; ++row) {
        tags.push_back(row < 8 ? 0 : 1);
        tags.push_back(10 + row / 4);
        tags.push_back(row);
    }

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-hierarchy-test";
    options.keyColumns = {0, 1};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Hierarchy;
    options.predicateColumns = {0, 1, 2};
    options.categoricalColumnCount = 2;
    options.samplesPerAttribute = 8;
    options.statisticsSampleRows = 16;
    options.queryCount = 64;
    options.clauseCountWeights = {{2, 1.0}};
    options.clauseAttributeCountWeights = {{2, 1.0}};
    options.clauseCountWeightsText = "2:1";
    options.clauseAttributeCountWeightsText = "2:1";
    options.seed = 19;

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
            output.string(), tags.data(), 16, 3, options, &summary, &error),
        error);
    BOOST_CHECK_EQUAL(summary.generatedQueries, 64);
    BOOST_CHECK_EQUAL(summary.generatedDNFQueries, 64);
    BOOST_CHECK(summary.uniqueQueries > 0);
    BOOST_CHECK_EQUAL(summary.statisticsSampleRows, 16);

    std::ifstream input(output);
    BOOST_REQUIRE(input.good());
    std::string line;
    int dataLines = 0;
    double weightSum = 0.0;
    while (std::getline(input, line)) {
        if (line.empty() || line[0] == '#') continue;
        ++dataLines;
        std::istringstream row(line);
        double weight = 0.0;
        double selectivity = 0.0;
        std::uint64_t matchCount = 0;
        std::string predicate;
        BOOST_REQUIRE(row >> weight >> selectivity >> matchCount >> predicate);
        weightSum += weight;
        BOOST_CHECK(predicate.find('|') != std::string::npos);

        std::istringstream clauses(predicate);
        std::string clause;
        int clauseCount = 0;
        while (std::getline(clauses, clause, '|')) {
            ++clauseCount;
            BOOST_CHECK(clause.find("2<=") != std::string::npos);
            BOOST_CHECK(
                clause.rfind("0=", 0) == 0 ||
                clause.rfind("1=", 0) == 0);
        }
        BOOST_CHECK_EQUAL(clauseCount, 2);
        BOOST_CHECK(selectivity > 0.0);
        BOOST_CHECK_EQUAL(
            matchCount,
            static_cast<std::uint64_t>(std::llround(selectivity * 16.0)));
    }
    BOOST_CHECK_EQUAL(dataLines, static_cast<int>(summary.uniqueQueries));
    BOOST_CHECK_CLOSE(weightSum, 1.0, 1e-9);
}

BOOST_AUTO_TEST_CASE(ReusesArbitraryExternalDNFTrainSet)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "external_dnf.tsv";
    {
        std::ofstream existing(output);
        existing << "1\t0.5\t8\t2<=100|0=1&1=7\n";
    }

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "external-dnf-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0, 1, 2};

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE(SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), nullptr, 16, 3, options, &summary, &error));
    BOOST_CHECK(summary.reusedExisting);
    BOOST_CHECK_EQUAL(
        ReadFile(output), "1\t0.5\t8\t2<=100|0=1&1=7\n");
}

BOOST_AUTO_TEST_CASE(GeneratesDisjointRangeKeyDNF)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "range_dnf.tsv";
    std::vector<std::uint32_t> tags;
    for (std::uint32_t row = 0; row < 16; ++row) {
        tags.push_back(row % 2);
        tags.push_back(row);
    }

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-range-test";
    options.keyColumns = {1};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Range;
    options.predicateColumns = {1};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 4;
    options.statisticsSampleRows = 16;
    options.queryCount = 32;
    options.clauseCountWeights = {{2, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "2:1";
    options.clauseAttributeCountWeightsText = "1:1";
    options.seed = 23;

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
            output.string(), tags.data(), 16, 2, options, &summary, &error),
        error);

    const std::string content = ReadFile(output);
    BOOST_CHECK(content.find("# key_kind=range\n") != std::string::npos);
    BOOST_CHECK(content.find("|") != std::string::npos);
    BOOST_CHECK(content.find("1>") != std::string::npos);
    BOOST_CHECK(content.find("1<=") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(RejectsStaleDNFConfiguration)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "dnf.tsv";
    const std::vector<std::uint32_t> tags = {0, 0, 1, 1};

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-stale-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 2;
    options.statisticsSampleRows = 4;
    options.queryCount = 8;
    options.clauseCountWeights = {{1, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "1:1";
    options.clauseAttributeCountWeightsText = "1:1";

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE(SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), tags.data(), 4, 1, options, &summary, &error));

    options.queryCount = 9;
    BOOST_CHECK(!SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), tags.data(), 4, 1, options, &summary, &error));
    BOOST_CHECK(error.find("incompatible query_count") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(RejectsPreMarginalV4Format)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "pre_marginal_v4.tsv";
    {
        std::ofstream existing(output);
        existing << "# sptag_predicate_train_set_v4\n";
    }

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "pre-marginal-v4-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 2;
    options.statisticsSampleRows = 4;
    options.queryCount = 1;
    options.clauseCountWeights = {{1, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "1:1";
    options.clauseAttributeCountWeightsText = "1:1";

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_CHECK(!SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), nullptr, 4, 1, options, &summary, &error));
    BOOST_CHECK(error.find("incompatible format") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(RejectsTruncatedGeneratedDNFFile)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "truncated.tsv";
    const std::vector<std::uint32_t> tags = {0, 0, 1, 1};

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-truncated-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 2;
    options.statisticsSampleRows = 4;
    options.queryCount = 1;
    options.clauseCountWeights = {{1, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "1:1";
    options.clauseAttributeCountWeightsText = "1:1";

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE(SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), tags.data(), 4, 1, options, &summary, &error));

    std::istringstream generated(ReadFile(output));
    std::ofstream truncated(output, std::ios::trunc);
    std::string line;
    while (std::getline(generated, line)) {
        if (!line.empty() && line[0] == '#') truncated << line << '\n';
    }
    truncated.close();

    BOOST_CHECK(!SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), tags.data(), 4, 1, options, &summary, &error));
    BOOST_CHECK(error.find("incomplete rows") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(RejectsModifiedGeneratedPredicateContent)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "modified.tsv";
    const std::vector<std::uint32_t> tags = {0, 0, 1, 1};

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-modified-content-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 2;
    options.statisticsSampleRows = 4;
    options.queryCount = 1;
    options.clauseCountWeights = {{1, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "1:1";
    options.clauseAttributeCountWeightsText = "1:1";

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE(SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), tags.data(), 4, 1, options, &summary, &error));

    std::string content = ReadFile(output);
    std::size_t lineBegin = 0;
    while (lineBegin < content.size() && content[lineBegin] == '#') {
        lineBegin = content.find('\n', lineBegin);
        BOOST_REQUIRE(lineBegin != std::string::npos);
        ++lineBegin;
    }
    const std::size_t lineEnd = content.find('\n', lineBegin);
    const std::size_t predicateBegin =
        content.rfind('\t', lineEnd) + 1;
    content.replace(
        predicateBegin, lineEnd - predicateBegin, "999=1");
    {
        std::ofstream modified(output, std::ios::binary | std::ios::trunc);
        modified << content;
    }

    BOOST_CHECK(!SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), tags.data(), 4, 1, options, &summary, &error));
    BOOST_CHECK(error.find("invalid predicate content") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(RejectsModifiedGeneratedSelectivity)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "modified_selectivity.tsv";
    const std::vector<std::uint32_t> tags = {0, 0, 1, 1};

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-modified-selectivity-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 2;
    options.statisticsSampleRows = 4;
    options.queryCount = 1;
    options.clauseCountWeights = {{1, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "1:1";
    options.clauseAttributeCountWeightsText = "1:1";

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE(SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), tags.data(), 4, 1, options, &summary, &error));

    std::string content = ReadFile(output);
    std::size_t lineBegin = 0;
    while (lineBegin < content.size() && content[lineBegin] == '#') {
        lineBegin = content.find('\n', lineBegin);
        BOOST_REQUIRE(lineBegin != std::string::npos);
        ++lineBegin;
    }
    const std::size_t firstTab = content.find('\t', lineBegin);
    const std::size_t secondTab = content.find('\t', firstTab + 1);
    content.replace(
        firstTab + 1,
        secondTab - firstTab - 1,
        "0.50000000000000011");
    {
        std::ofstream modified(output, std::ios::binary | std::ios::trunc);
        modified << content;
    }

    BOOST_CHECK(!SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), tags.data(), 4, 1, options, &summary, &error));
    BOOST_CHECK(error.find("invalid predicate content") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(RejectsTrailingDNFSeparators)
{
    const std::unordered_set<int> predicateColumns = {0};
    const std::unordered_set<int> keyColumns = {0};
    SPTAG::PredicateWorkload::Detail::ParsedPredicateShape shape;
    BOOST_CHECK(!SPTAG::PredicateWorkload::Detail::ParseGeneratedPredicateShape(
        "0=1|",
        predicateColumns,
        keyColumns,
        SPTAG::PredicateWorkload::KeyKind::Label,
        1,
        &shape));
    BOOST_CHECK(!SPTAG::PredicateWorkload::Detail::ParseGeneratedPredicateShape(
        "0=1&",
        predicateColumns,
        keyColumns,
        SPTAG::PredicateWorkload::KeyKind::Label,
        1,
        &shape));
}

BOOST_AUTO_TEST_CASE(PreservesConfiguredDNFShapeQuotas)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "quota.tsv";
    std::vector<std::uint32_t> tags;
    for (std::uint32_t row = 0; row < 40; ++row) {
        tags.push_back(row % 4);
    }

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-quota-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 4;
    options.statisticsSampleRows = 40;
    options.queryCount = 10;
    options.clauseCountWeights = {{1, 1.0}, {2, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "1:1,2:1";
    options.clauseAttributeCountWeightsText = "1:1";
    options.seed = 29;

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
            output.string(), tags.data(), 40, 1, options, &summary, &error),
        error);
    BOOST_CHECK_EQUAL(summary.generatedQueries, 10);
    BOOST_CHECK_EQUAL(summary.generatedDNFQueries, 5);
    const std::string content = ReadFile(output);
    BOOST_CHECK(content.find("# realized_clause_counts=1:5,2:5\n") !=
                std::string::npos);
}

BOOST_AUTO_TEST_CASE(UsesAutomaticDNFShapePolicy)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "auto_policy.tsv";
    std::vector<std::uint32_t> tags;
    for (std::uint32_t key = 0; key < 8; ++key) {
        for (std::uint32_t value = 0; value < 8; ++value) {
            tags.push_back(key);
            tags.push_back(value);
        }
    }

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-auto-policy-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0, 1};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 8;
    options.statisticsSampleRows = 64;
    options.queryCount = 6;
    options.seed = 37;

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
            output.string(), tags.data(), 64, 2, options, &summary, &error),
        error);
    BOOST_CHECK_EQUAL(summary.generatedQueries, 6);
    BOOST_CHECK_EQUAL(summary.generatedDNFQueries, 2);

    const std::string content = ReadFile(output);
    BOOST_CHECK(content.find(
                    "# clause_count_weights="
                    "auto_equal_clause_budget_feasible_1,2\n") !=
                std::string::npos);
    BOOST_CHECK(content.find(
                    "# clause_attribute_count_weights="
                    "auto_uniform_nonempty_attribute_masks\n") !=
                std::string::npos);
    BOOST_CHECK(content.find("# realized_clause_counts=1:4,2:2\n") !=
                std::string::npos);
    BOOST_CHECK(content.find(
                    "# realized_clause_attribute_counts=1:5,2:3\n") !=
                std::string::npos);
}

BOOST_AUTO_TEST_CASE(RejectsAttributeQuotasBeyondPerQueryCapacity)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "mask_capacity.tsv";
    const std::vector<std::uint32_t> tags = {
        0, 1,
        0, 1,
        0, 1,
        0, 1,
    };

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-mask-capacity-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0, 1};
    options.categoricalColumnCount = 2;
    options.samplesPerAttribute = 2;
    options.statisticsSampleRows = 4;
    options.queryCount = 1;
    options.clauseCountWeights = {{2, 1.0}};
    options.clauseAttributeCountWeights = {{2, 1.0}};
    options.clauseCountWeightsText = "2:1";
    options.clauseAttributeCountWeightsText = "2:1";
    options.seed = 43;

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_CHECK(!SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), tags.data(), 4, 2, options, &summary, &error));
    BOOST_CHECK(error.find("attribute-count quotas cannot be assigned") !=
                std::string::npos);
}

BOOST_AUTO_TEST_CASE(SamplesNearCapacityClausesWithoutReplacement)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "without_replacement.tsv";
    const std::vector<std::uint32_t> tags = {0, 1, 2, 3};

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-without-replacement-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 4;
    options.statisticsSampleRows = 4;
    options.queryCount = 32;
    options.clauseCountWeights = {{4, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "4:1";
    options.clauseAttributeCountWeightsText = "1:1";
    options.seed = 47;

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
            output.string(), tags.data(), 4, 1, options, &summary, &error),
        error);
    BOOST_CHECK_EQUAL(summary.generatedDNFQueries, 32);
    const std::string content = ReadFile(output);
    BOOST_CHECK(content.find("# realized_clause_counts=4:32\n") !=
                std::string::npos);
    BOOST_CHECK(content.find("\t1\t4\t0=0|0=1|0=2|0=3\n") !=
                std::string::npos);
}

BOOST_AUTO_TEST_CASE(RejectsClausesWithoutMarginalMatches)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "no_marginal.tsv";
    const std::vector<std::uint32_t> tags = {
        0, 1,
        0, 1,
        0, 1,
        0, 1,
    };

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-no-marginal-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0, 1};
    options.categoricalColumnCount = 2;
    options.samplesPerAttribute = 2;
    options.statisticsSampleRows = 4;
    options.queryCount = 1;
    options.clauseCountWeights = {{2, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}, {2, 1.0}};
    options.clauseCountWeightsText = "2:1";
    options.clauseAttributeCountWeightsText = "1:1,2:1";
    options.seed = 53;

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_CHECK(!SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), tags.data(), 4, 2, options, &summary, &error));
    BOOST_CHECK(error.find("cannot generate a sample-supported query") !=
                std::string::npos);
}

BOOST_AUTO_TEST_CASE(GeneratesPredicatesWithoutKey)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "optional_key.tsv";
    std::vector<std::uint32_t> tags;
    for (std::uint32_t row = 0; row < 16; ++row) {
        tags.push_back(row % 2);
        tags.push_back(10 + row % 4);
    }

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-optional-key-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0, 1};
    options.categoricalColumnCount = 2;
    options.samplesPerAttribute = 4;
    options.statisticsSampleRows = 16;
    options.queryCount = 32;
    options.clauseCountWeights = {{1, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "1:1";
    options.clauseAttributeCountWeightsText = "1:1";
    options.seed = 41;

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
            output.string(), tags.data(), 16, 2, options, &summary, &error),
        error);

    const std::string content = ReadFile(output);
    BOOST_CHECK(content.find("# key_clause_policy=optional\n") !=
                std::string::npos);
    BOOST_CHECK(content.find("\t0=") != std::string::npos);
    BOOST_CHECK(content.find("\t1=") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(IgnoresZeroQuotaShapes)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "zero_quota.tsv";
    const std::vector<std::uint32_t> tags = {0, 0, 1, 1};

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-zero-quota-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 2;
    options.statisticsSampleRows = 4;
    options.queryCount = 1;
    options.clauseCountWeights = {{1, 1.0}, {64, 1e-300}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "1:1,64:1e-300";
    options.clauseAttributeCountWeightsText = "1:1";

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
            output.string(), tags.data(), 4, 1, options, &summary, &error),
        error);
    BOOST_CHECK_EQUAL(summary.generatedDNFQueries, 0);
    BOOST_CHECK(ReadFile(output).find("# realized_clause_counts=1:1\n") !=
                std::string::npos);
}

BOOST_AUTO_TEST_CASE(KeepsDenseQueriesForPlannerCosting)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "dense.tsv";
    const std::vector<std::uint32_t> tags = {0, 0, 1, 1};

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-dense-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 2;
    options.statisticsSampleRows = 4;
    options.queryCount = 1;
    options.clauseCountWeights = {{2, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "2:1";
    options.clauseAttributeCountWeightsText = "1:1";

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
            output.string(), tags.data(), 4, 1, options, &summary, &error),
        error);
    const std::string content = ReadFile(output);
    BOOST_CHECK(content.find("# density_filter=none\n") != std::string::npos);
    BOOST_CHECK(content.find("\t1\t4\t") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(UsesIndependentDiscoveryAndEvaluationSamples)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "independent.tsv";
    std::vector<std::uint32_t> tags(200);
    for (std::size_t row = 0; row < tags.size(); ++row) {
        tags[row] = static_cast<std::uint32_t>(row);
    }

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-independent-sample-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0};
    options.categoricalColumnCount = 1;
    options.samplesPerAttribute = 8;
    options.statisticsSampleRows = 20;
    options.queryCount = 1;
    options.clauseCountWeights = {{1, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}};
    options.clauseCountWeightsText = "1:1";
    options.clauseAttributeCountWeightsText = "1:1";
    options.seed = 31;

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_CHECK(!SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
        output.string(), tags.data(), tags.size(), 1, options, &summary, &error));
    BOOST_CHECK(error.find("fixed shape") != std::string::npos);
}

BOOST_AUTO_TEST_CASE(RetriesAttributeAssignmentsWithinFixedShape)
{
    ScopedTempDir temp;
    const std::filesystem::path output = temp.path / "assignment.tsv";
    const std::vector<std::uint32_t> tags = {
        0, 0,
        0, 0,
        1, 1,
        1, 2,
    };

    SPTAG::PredicateWorkload::SyntheticDNFOptions options;
    options.sourceId = "dnf-assignment-test";
    options.keyColumns = {0};
    options.keyKind = SPTAG::PredicateWorkload::KeyKind::Label;
    options.predicateColumns = {0, 1};
    options.categoricalColumnCount = 2;
    options.samplesPerAttribute = 3;
    options.statisticsSampleRows = 4;
    options.queryCount = 1;
    options.clauseCountWeights = {{2, 1.0}};
    options.clauseAttributeCountWeights = {{1, 1.0}, {2, 1.0}};
    options.clauseCountWeightsText = "2:1";
    options.clauseAttributeCountWeightsText = "1:1,2:1";
    options.seed = 4;

    SPTAG::PredicateWorkload::DNFTrainSetSummary summary;
    std::string error;
    BOOST_REQUIRE_MESSAGE(
        SPTAG::PredicateWorkload::EnsureSyntheticDNFTrainSet(
            output.string(), tags.data(), 4, 2, options, &summary, &error),
        error);
    BOOST_CHECK_EQUAL(summary.generatedDNFQueries, 1);
    const std::string content = ReadFile(output);
    BOOST_CHECK(content.find('|') != std::string::npos);
    BOOST_CHECK(content.find('&') != std::string::npos);
}

BOOST_AUTO_TEST_SUITE_END()
