// Offline diagnostics only: never loads an index or executes a routed search.
#include "inc/Helper/SimpleIniReader.h"
#include "inc/Helper/VectorSetReader.h"
#include "inc/Core/Common/DistanceUtils.h"

#include <algorithm>
#include <charconv>
#include <cctype>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <unistd.h>
#include <vector>

namespace fs = std::filesystem;
using SPTAG::Helper::IniReader;

namespace {
void Require(bool good, const std::string& why)
{
    if (!good) throw std::runtime_error(why);
}

std::string Trim(const std::string& s)
{
    const auto begin = s.find_first_not_of(" \t\r\n");
    return begin == std::string::npos ? "" : s.substr(begin, s.find_last_not_of(" \t\r\n") - begin + 1);
}

int Integer(const std::string& s)
{
    int value = 0;
    auto parsed = std::from_chars(s.data(), s.data() + s.size(), value);
    Require(!s.empty() && parsed.ec == std::errc() && parsed.ptr == s.data() + s.size(),
            "Invalid integer: " + s);
    return value;
}

std::string Required(const IniReader& ini, const std::string& section, const std::string& key)
{
    auto value = Trim(ini.GetParameter<std::string>(section, key, ""));
    Require(!value.empty(), "Missing [" + section + "] " + key);
    return value;
}

std::vector<std::string> Split(const std::string& value)
{
    std::vector<std::string> parts;
    std::size_t begin = 0;
    do {
        const auto end = value.find(',', begin);
        auto part = Trim(value.substr(begin, end == std::string::npos ? end : end - begin));
        Require(!part.empty(), "Empty comma-separated item");
        parts.push_back(part);
        if (end == std::string::npos) break;
        begin = end + 1;
    } while (true);
    return parts;
}

struct Config {
    std::string heads, queries;
    int queryCount, dim, nprobe, warmup, threads;
    std::vector<std::string> names, paths;
    std::vector<std::string> catalogs, postings;
    int routingBeam = 0;
};

Config ReadConfig(const std::string& path)
{
    IniReader ini;
    Require(ini.LoadIniFile(path) == SPTAG::ErrorCode::Success, "Cannot load native INI: " + path);
    // IniReader exposes keys, but not section names.
    std::ifstream input(path);
    for (std::string line; std::getline(input, line);) {
        line = Trim(line);
        if (line.empty() || line[0] == ';' || line[0] != '[') continue;
        std::transform(line.begin(), line.end(), line.begin(),
                       [](unsigned char c) { return std::tolower(c); });
        Require(line == "[quality]" || line == "[logs]" || line == "[hierarchy]",
                "Unsupported INI section: " + line);
    }
    Require(!input.bad(), "INI read failed");
    const std::set<std::string> qualityKeys = {
        "headvectors", "queries", "querycount", "dim", "nprobe", "threads", "warmup", "measureoffset"};
    const std::set<std::string> logKeys = {"names", "paths"};
    for (const auto& entry : ini.GetParameters("quality"))
        Require(qualityKeys.count(entry.first), "Unsupported Quality key: " + entry.first);
    for (const auto& entry : ini.GetParameters("logs"))
        Require(logKeys.count(entry.first), "Unsupported Logs key: " + entry.first);
    const std::set<std::string> hierarchyKeys = {"catalogs", "postings", "routingbeam"};
    for (const auto& entry : ini.GetParameters("hierarchy"))
        Require(hierarchyKeys.count(entry.first), "Unsupported Hierarchy key: " + entry.first);
    auto optionalInt = [&](const char* key, int fallback) {
        return Integer(Trim(ini.GetParameter<std::string>("quality", key, std::to_string(fallback))));
    };
    Config c;
    c.heads = Required(ini, "quality", "headvectors");
    c.queries = Required(ini, "quality", "queries");
    Require(c.heads.find(',') == std::string::npos && c.queries.find(',') == std::string::npos,
            "Exactly one native vector file per input is supported");
    c.heads = fs::absolute(c.heads).string();
    c.queries = fs::absolute(c.queries).string();
    c.queryCount = Integer(Required(ini, "quality", "querycount"));
    c.dim = Integer(Required(ini, "quality", "dim"));
    c.nprobe = Integer(Required(ini, "quality", "nprobe"));
    c.warmup = optionalInt("warmup", 1000);
    c.threads = optionalInt("threads", 1);
    Require(c.dim == 128, "Only Float128 inputs are supported");
    Require(c.queryCount > 0 && c.warmup >= 0 &&
            c.queryCount <= std::numeric_limits<int>::max() - c.warmup, "Invalid query count/warmup");
    Require(c.nprobe > 0 && c.nprobe <= 256, "Nprobe must be in [1,256]");
    Require(c.threads > 0, "Threads must be positive");
    Require(optionalInt("measureoffset", 0) == 0, "Only MeasureOffset=0 is supported");
    c.names = Split(Required(ini, "logs", "names"));
    c.paths = Split(Required(ini, "logs", "paths"));
    Require(c.names.size() == c.paths.size(), "Logs Names/Paths count mismatch");
    std::set<std::string> names, paths;
    for (std::size_t i = 0; i < c.names.size(); ++i) {
        Require(std::regex_match(c.names[i], std::regex("[A-Za-z0-9_-]+")) &&
                names.insert(c.names[i]).second, "Invalid or duplicate log name");
        Require(fs::path(c.paths[i]).is_absolute() && paths.insert(c.paths[i]).second,
                "Log paths must be distinct absolute paths");
    }
    if (!ini.GetParameters("hierarchy").empty()) {
        c.catalogs = Split(Required(ini, "hierarchy", "catalogs"));
        c.postings = Split(Required(ini, "hierarchy", "postings"));
        c.routingBeam = Integer(Required(ini, "hierarchy", "routingbeam"));
        Require(c.catalogs.size() == 2 && c.postings.size() == 2 &&
                c.routingBeam > 0 && c.routingBeam <= c.nprobe,
                "Hierarchy audit requires H2/H3 catalogs, two CSRs, and beam in [1,Nprobe]");
        for (auto* paths : {&c.catalogs, &c.postings})
            for (auto& p : *paths) p = fs::absolute(p).string();
    }
    return c;
}

// The native XVEC reader stages its conversion under cwd. Isolate its random
// filename in an exclusively created project-local directory, never a system temp path.
struct ReaderDirectory {
    fs::path original = fs::current_path();
    fs::path owned = original / ("headquality-reader-" + std::to_string(::getpid()));
    ReaderDirectory()
    {
        Require(fs::create_directory(owned), "Refusing to reuse reader directory: " + owned.string());
        fs::current_path(owned);
    }
    ~ReaderDirectory()
    {
        std::error_code ignored;
        fs::current_path(original, ignored);
        fs::remove(owned / "tempfolder", ignored);
        fs::remove(owned, ignored);
    }
};

std::shared_ptr<SPTAG::VectorSet> ReadVectors(const std::string& path, bool heads, const Config& c)
{
    Require(fs::is_regular_file(path), "Missing vector file: " + path);
    const auto bytes = fs::file_size(path);
    const std::uintmax_t header = heads ? 8 : 0;
    const std::uintmax_t stride = 4 * (c.dim + (heads ? 0 : 1));
    Require(bytes > header && (bytes - header) % stride == 0, "Invalid native vector file size: " + path);
    const auto rows = (bytes - header) / stride;
    Require(rows < static_cast<std::uintmax_t>(std::numeric_limits<int>::max()), "Too many vector rows");
    Require(heads ? rows >= static_cast<unsigned>(c.nprobe) : rows >= static_cast<unsigned>(c.queryCount),
            "Insufficient vector rows");
    ReaderDirectory directory;
    auto options = std::make_shared<SPTAG::Helper::ReaderOptions>(
        SPTAG::VectorValueType::Float, c.dim,
        heads ? SPTAG::VectorFileType::DEFAULT : SPTAG::VectorFileType::XVEC, "|", c.threads, false);
    auto reader = SPTAG::Helper::VectorSetReader::CreateInstance(options);
    Require(reader && reader->LoadFile(path) == SPTAG::ErrorCode::Success, "Native LoadFile failed: " + path);
    // Header inspection is also through the native reader. Bound the subsequent
    // allocation by the file size rather than trusting an arbitrary row header.
    auto shape = reader->GetVectorSet(0, 0);
    Require(shape && shape->Dimension() == c.dim, "Native vector dimension mismatch");
    auto vectors = reader->GetVectorSet(0, heads ? static_cast<int>(rows + 1) : c.queryCount);
    Require(vectors && vectors->Available() && vectors->Dimension() == c.dim &&
            vectors->GetValueType() == SPTAG::VectorValueType::Float &&
            vectors->Count() == (heads ? static_cast<int>(rows) : c.queryCount),
            "Native vector shape/type mismatch");
    const auto* values = static_cast<const float*>(vectors->GetData());
    for (std::size_t i = 0; i < static_cast<std::size_t>(vectors->Count()) * c.dim; ++i)
        Require(std::isfinite(values[i]), "Nonfinite vector component");
    return vectors;
}

struct Head { int id; double logged; };
using Rows = std::vector<std::vector<Head>>;

Rows ReadLog(const std::string& path, const Config& c, int headCount)
{
    std::ifstream input(path);
    Require(input.is_open(), "Cannot open dump log: " + path);
    const int expected = c.warmup + c.queryCount;
    std::vector<bool> seen(expected, false);
    Rows measured(c.queryCount);
    const std::regex header("^DUMPHEADS q=([0-9]+) bundle=([01]) twoLayer=([01]) n=([0-9]+) :(.*)$");
    const std::regex item("^([0-9]+):([0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)$");
    std::size_t lineNumber = 0;
    for (std::string line; std::getline(input, line);) {
        ++lineNumber;
        const auto marker = line.find("DUMPHEADS");
        if (marker == std::string::npos) continue;
        const auto context = path + ":" + std::to_string(lineNumber);
        std::smatch match;
        line = Trim(line.substr(marker));
        Require(std::regex_match(line, match, header), "Malformed DUMPHEADS header at " + context);
        const int q = Integer(match[1]), n = Integer(match[4]);
        Require(q >= 0 && q < expected && !seen[q], "Duplicate/out-of-range dump query at " + context);
        Require(n <= c.nprobe, "Dump n exceeds Nprobe at " + context);
        seen[q] = true;
        std::istringstream tokens(match[5]);
        std::vector<Head> row;
        std::set<int> ids;
        for (std::string token; tokens >> token;) {
            Require(std::regex_match(token, match, item), "Malformed head token at " + context);
            const int id = Integer(match[1]);
            const double distance = std::stod(match[2]);
            Require(id >= 0 && id < headCount && ids.insert(id).second,
                    "Duplicate/out-of-range local head ID at " + context);
            Require(std::isfinite(distance) && distance >= 0, "Invalid logged distance at " + context);
            Require(row.empty() || distance >= row.back().logged, "Unsorted head distances at " + context);
            row.push_back({id, distance});
        }
        Require(row.size() <= static_cast<std::size_t>(n), "More head tokens than n at " + context);
        if (q >= c.warmup) measured[q - c.warmup] = std::move(row);
    }
    Require(!input.bad(), "Dump log read failed: " + path);
    Require(std::all_of(seen.begin(), seen.end(), [](bool x) { return x; }),
            "Missing expected dump queries (including warmup): " + path);
    return measured;
}

std::string Number(double value)
{
    if (!std::isfinite(value)) return "null";
    std::ostringstream out;
    out << std::setprecision(17) << value;
    return out.str();
}

struct Aggregate {
    int underfilled = 0, empty = 0, count = 0, hits = 0, thresholdHits = 0, tieHits = 0;
    double selectedDistance = 0, oracleDistance = 0, meanExcess = 0, rank = 0;
    int nonempty = 0;
    std::vector<int> intersections;
};

#include "HeadRoutingAudit.h"

void Analyze(const Config& c)
{
    auto heads = ReadVectors(c.heads, true, c);
    auto queries = ReadVectors(c.queries, false, c);
    const int headCount = heads->Count(), L = c.nprobe;
    std::vector<Rows> logs;
    for (const auto& path : c.paths) logs.push_back(ReadLog(path, c, headCount));
    std::vector<Aggregate> totals(logs.size());
    for (auto& total : totals) total.intersections.resize(logs.size());
    std::vector<float> distances(headCount);
    std::vector<int> order(headCount), ranks(headCount);
    auto distance = SPTAG::COMMON::DistanceCalcSelector<float>(SPTAG::DistCalcMethod::L2);
    Require(distance != nullptr, "Native Float L2 selector unavailable");
    std::unique_ptr<HeadRoutingAudit> routing;
    if (c.routingBeam) routing = std::make_unique<HeadRoutingAudit>(c, heads);
    const double nan = std::numeric_limits<double>::quiet_NaN();
    for (int q = 0; q < c.queryCount; ++q) {
        const auto* query = static_cast<const float*>(queries->GetVector(q));
        // Parallelize the shared distance array, not queries/head-sized workspaces.
#pragma omp parallel for num_threads(c.threads) schedule(static)
        for (int h = 0; h < headCount; ++h)
            distances[h] = distance(query, static_cast<const float*>(heads->GetVector(h)), c.dim);
        for (float d : distances) Require(std::isfinite(d) && d >= 0, "Invalid native L2 distance");
        std::iota(order.begin(), order.end(), 0);
        std::sort(order.begin(), order.end(), [&](int a, int b) {
            return distances[a] < distances[b] || (distances[a] == distances[b] && a < b);
        });
        for (int r = 0; r < headCount; ++r) ranks[order[r]] = r + 1;
        if (routing) routing->Query(q, query, order, distances, logs);
        const float boundary = distances[order[L - 1]];
        auto lower = [&](float d) {
            return std::lower_bound(order.begin(), order.end(), d,
                                    [&](int h, float v) { return distances[h] < v; }) - order.begin();
        };
        auto upper = [&](float d) {
            return std::upper_bound(order.begin(), order.end(), d,
                                    [&](float v, int h) { return v < distances[h]; }) - order.begin();
        };
        const int strict = lower(boundary), boundaryTies = upper(boundary) - strict;
        double oracleSum = 0;
        for (int r = 0; r < L; ++r) oracleSum += distances[order[r]];
        for (std::size_t i = 0; i < logs.size(); ++i) {
            const auto& row = logs[i][q];
            auto& total = totals[i];
            int hits = 0, strictHits = 0, boundaryHits = 0;
            double selectedSum = 0, rankSum = 0, sameCountSum = 0;
            std::set<int> ids;
            std::ostringstream selected;
            selected << '[';
            for (std::size_t j = 0; j < row.size(); ++j) {
                const auto& h = row[j];
                const float d = distances[h.id];
                // std::to_string prints six decimal places; allow half a final
                // decimal plus double parsing roundoff, not percent-level error.
                const double tolerance = 0.00000051 + 8 * std::numeric_limits<double>::epsilon() * std::abs(d);
                Require(std::abs(h.logged - d) <= tolerance,
                        "Logged/native Float L2 mismatch: " + c.names[i] + " query=" +
                        std::to_string(q) + " hid=" + std::to_string(h.id));
                ids.insert(h.id);
                hits += ranks[h.id] <= L;
                strictHits += d < boundary;
                boundaryHits += d == boundary;
                selectedSum += d;
                rankSum += ranks[h.id];
                sameCountSum += distances[order[j]];
                if (j) selected << ',';
                selected << "{\"id\":" << h.id << ",\"logged_distance\":" << Number(h.logged)
                         << ",\"distance\":" << Number(d) << ",\"rank\":" << ranks[h.id]
                         << ",\"rank_min\":" << lower(d) + 1 << ",\"rank_max\":" << upper(d) << '}';
            }
            selected << ']';
            const int count = row.size();
            const int tieHits = strictHits + std::min(boundaryHits, L - strict);
            const double mean = count ? selectedSum / count : nan;
            const double excess = count ? (selectedSum - sameCountSum) / count : nan;
            total.count += count;
            total.hits += hits;
            total.thresholdHits += strictHits + boundaryHits;
            total.tieHits += tieHits;
            total.underfilled += count < L;
            total.empty += count == 0;
            total.selectedDistance += selectedSum;
            total.oracleDistance += oracleSum;
            total.rank += rankSum;
            if (count) { ++total.nonempty; total.meanExcess += excess; }
            std::ostringstream overlaps;
            overlaps << '{';
            bool first = true;
            for (std::size_t other = 0; other < logs.size(); ++other) {
                if (other == i) continue;
                int intersection = 0;
                for (const auto& h : logs[other][q]) intersection += ids.count(h.id);
                total.intersections[other] += intersection;
                const int unionCount = count + logs[other][q].size() - intersection;
                if (!first) overlaps << ',';
                first = false;
                overlaps << '"' << c.names[other] << "\":{\"intersection_count\":" << intersection
                         << ",\"intersection_over_nprobe\":" << Number(double(intersection) / L)
                         << ",\"jaccard\":" << Number(unionCount ? double(intersection) / unionCount : nan) << '}';
            }
            overlaps << '}';
            std::cout << "HEAD_QUALITY_QUERY {\"name\":\"" << c.names[i]
                      << "\",\"nprobe\":" << L << ",\"queryid\":" << q
                      << ",\"dump_queryid\":" << q + c.warmup << ",\"count\":" << count
                      << ",\"underfilled\":" << (count < L ? "true" : "false")
                      << ",\"exact_head_recall\":" << Number(double(hits) / L)
                      << ",\"threshold_coverage\":" << Number(double(strictHits + boundaryHits) / L)
                      << ",\"tie_adjusted_head_recall\":" << Number(double(tieHits) / L)
                      << ",\"selected_head_mean_distance\":" << Number(mean)
                      << ",\"oracle_topL_mean_distance\":" << Number(oracleSum / L)
                      << ",\"mean_distance_excess_vs_topL\":" << Number(mean - oracleSum / L)
                      << ",\"oracle_selected_count_mean_distance\":" << Number(count ? sameCountSum / count : nan)
                      << ",\"mean_distance_excess_vs_same_count\":" << Number(excess)
                      << ",\"boundary_distance\":" << Number(boundary)
                      << ",\"strictly_nearer_count\":" << strict
                      << ",\"boundary_tie_count\":" << boundaryTies
                      << ",\"boundary_slots\":" << L - strict
                      << ",\"selected_strictly_nearer_count\":" << strictHits
                      << ",\"selected_boundary_count\":" << boundaryHits
                      << ",\"selected\":" << selected.str() << ",\"overlap\":" << overlaps.str() << "}\n";
        }
    }
    for (std::size_t i = 0; i < totals.size(); ++i) {
        const auto& t = totals[i];
        const double denominator = double(c.queryCount) * L;
        std::cout << "HEAD_QUALITY_SUMMARY {\"name\":\"" << c.names[i]
                  << "\",\"nprobe\":" << L << ",\"query_count\":" << c.queryCount
                  << ",\"head_count\":" << headCount << ",\"warmup\":" << c.warmup
                  << ",\"threads\":" << c.threads << ",\"count\":" << t.count
                  << ",\"underfilled_queries\":" << t.underfilled << ",\"empty_queries\":" << t.empty
                  << ",\"exact_head_recall\":" << Number(t.hits / denominator)
                  << ",\"threshold_coverage\":" << Number(t.thresholdHits / denominator)
                  << ",\"tie_adjusted_head_recall\":" << Number(t.tieHits / denominator)
                  << ",\"selected_head_mean_distance\":" << Number(t.count ? t.selectedDistance / t.count : nan)
                  << ",\"oracle_topL_mean_distance\":" << Number(t.oracleDistance / denominator)
                  << ",\"mean_distance_excess_vs_same_count\":" << Number(t.nonempty ? t.meanExcess / t.nonempty : nan)
                  << ",\"selected_head_mean_rank\":" << Number(t.count ? t.rank / t.count : nan)
                  << ",\"distance_metric\":\"native_float_squared_L2\""
                  << ",\"rank_order\":\"distance_then_local_id_1_based\""
                  << ",\"ties\":\"exact_float_equality; threshold_coverage_can_hide_missing_strict_neighbors\""
                  << ",\"overlap\":{";
        bool first = true;
        for (std::size_t other = 0; other < totals.size(); ++other) {
            if (other == i) continue;
            if (!first) std::cout << ',';
            first = false;
            std::cout << '"' << c.names[other] << "\":{\"intersection_count\":" << t.intersections[other]
                      << ",\"mean_intersection_over_nprobe\":" << Number(t.intersections[other] / denominator) << '}';
        }
        std::cout << "}}\n";
    }
    if (routing) routing->Summary();
    std::cout.flush();
    Require(bool(std::cout), "Failed writing quality output");
}
} // namespace

int main(int argc, char** argv)
{
    try {
        Require(argc == 3 && std::string(argv[1]) == "--config", "Usage: headqualitybench --config <native.ini>");
        Analyze(ReadConfig(argv[2]));
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "HEAD_QUALITY_ERROR " << e.what() << '\n';
        return 1;
    }
}
