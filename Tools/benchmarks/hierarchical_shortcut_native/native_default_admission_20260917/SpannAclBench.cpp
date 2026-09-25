#include "FullHooks.h"
#include "NativeBatch.h"
#include "NativeNProbeSweep.h"
#include "inc/Core/Common/WorkSpace.h"
#include "inc/CoreInterface.h"
#include "inc/Core/VectorIndex.h"
#include "inc/Helper/SimpleIniReader.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <regex>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

using namespace SPTAG;

namespace {

struct Options
{
    std::string indexDir;
    std::string queryFile;
    std::string truthFile;
    std::string truthDir;
    std::string queryTagsFile;
    std::string queryDNFFile;
    std::string searchIni;
    std::vector<std::string> searchSweepInis;
    std::string valueType = "Float";
    int tagColumn = -1;
    int orTagCount = 0;
    std::vector<int> dnfAndColumns;
    int tenant = 0;
    int topk = 10;
    std::size_t warmup = 200;
    std::size_t maxQueries = 0;
    std::size_t measureOffset = 0;
    bool directSearch = false;
    bool allAclLevels = false;
};

struct NpyHeader
{
    std::string descr;
    std::vector<std::size_t> shape;
    std::size_t dataOffset = 0;
};

struct TruthMatrix
{
    std::vector<std::int64_t> values;
    std::size_t rows = 0;
    std::size_t cols = 0;
};

void Usage(const char* p_program)
{
    std::cerr << "Usage: " << p_program
              << " --index <index-dir> --queries <query.npy>"
              << " (--truth <truth.npy> | --truth-dir <directory>)"
              << " [--query-tags <tags.npy> --tag-column <0..N-1>]"
              << " [--query-tags <tags.npy> --or-tag-count <N>]"
              << " [--dnf-and-cols <col[,col...]>]"
              << " [--query-dnf <length-prefixed-dnf3.npy>]"
              << " [--search-ini <native-search.ini>]"
              << " [--search-sweep-ini <native-search.ini>]..."
              << " [--value-type Float|UInt8]"
              << " [--direct-search] [--tenant 0] [--topk 10]"
              << " [--warmup 200] [--measure-offset 0] [--max-queries N]"
              << " [--all-acl-levels]\n";
}

bool ParseSize(const char* p_text, std::size_t& p_value)
{
    if (p_text == nullptr || *p_text == '\0') return false;
    char* end = nullptr;
    const unsigned long long value = std::strtoull(p_text, &end, 10);
    if (end == p_text || *end != '\0' ||
        value > static_cast<unsigned long long>((std::numeric_limits<std::size_t>::max)())) {
        return false;
    }
    p_value = static_cast<std::size_t>(value);
    return true;
}

bool ParseInt(const char* p_text, int& p_value)
{
    std::size_t value = 0;
    if (!ParseSize(p_text, value) ||
        value > static_cast<std::size_t>((std::numeric_limits<int>::max)())) {
        return false;
    }
    p_value = static_cast<int>(value);
    return true;
}

bool ParseColumnList(const char* p_text, std::vector<int>& p_columns)
{
    if (p_text == nullptr || *p_text == '\0') return false;
    p_columns.clear();
    std::stringstream input(p_text);
    std::string token;
    while (std::getline(input, token, ',')) {
        char* end = nullptr;
        const long value = std::strtol(token.c_str(), &end, 10);
        if (end == token.c_str() || *end != '\0' || value < 0 ||
            value > static_cast<long>((std::numeric_limits<int>::max)())) {
            return false;
        }
        if (std::find(p_columns.begin(), p_columns.end(), static_cast<int>(value)) != p_columns.end()) {
            return false;
        }
        p_columns.push_back(static_cast<int>(value));
    }
    return !p_columns.empty();
}

bool ParseArgs(int p_argc, char** p_argv, Options& p_options)
{
    for (int i = 1; i < p_argc; ++i) {
        const char* arg = p_argv[i];
        if (std::strcmp(arg, "--help") == 0 || std::strcmp(arg, "-h") == 0) {
            Usage(p_argv[0]);
            std::exit(0);
        }
        if (std::strcmp(arg, "--direct-search") == 0) {
            p_options.directSearch = true;
            continue;
        }
        if (std::strcmp(arg, "--all-acl-levels") == 0) {
            p_options.allAclLevels = true;
            continue;
        }
        if (i + 1 >= p_argc) return false;
        const char* value = p_argv[++i];
        if (std::strcmp(arg, "--index") == 0) {
            p_options.indexDir = value;
        } else if (std::strcmp(arg, "--queries") == 0) {
            p_options.queryFile = value;
        } else if (std::strcmp(arg, "--truth") == 0) {
            p_options.truthFile = value;
        } else if (std::strcmp(arg, "--truth-dir") == 0) {
            p_options.truthDir = value;
        } else if (std::strcmp(arg, "--query-tags") == 0) {
            p_options.queryTagsFile = value;
        } else if (std::strcmp(arg, "--query-dnf") == 0) {
            p_options.queryDNFFile = value;
        } else if (std::strcmp(arg, "--search-ini") == 0) {
            p_options.searchIni = value;
        } else if (std::strcmp(arg, "--search-sweep-ini") == 0) {
            p_options.searchSweepInis.emplace_back(value);
        } else if (std::strcmp(arg, "--value-type") == 0) {
            p_options.valueType = value;
        } else if (std::strcmp(arg, "--tag-column") == 0) {
            if (!ParseInt(value, p_options.tagColumn)) return false;
        } else if (std::strcmp(arg, "--or-tag-count") == 0) {
            if (!ParseInt(value, p_options.orTagCount) ||
                p_options.orTagCount <= 0) {
                return false;
            }
        } else if (std::strcmp(arg, "--dnf-and-cols") == 0) {
            if (!ParseColumnList(value, p_options.dnfAndColumns)) return false;
        } else if (std::strcmp(arg, "--tenant") == 0) {
            if (!ParseInt(value, p_options.tenant)) return false;
        } else if (std::strcmp(arg, "--topk") == 0) {
            if (!ParseInt(value, p_options.topk) || p_options.topk <= 0) return false;
        } else if (std::strcmp(arg, "--warmup") == 0) {
            if (!ParseSize(value, p_options.warmup)) return false;
        } else if (std::strcmp(arg, "--max-queries") == 0) {
            if (!ParseSize(value, p_options.maxQueries)) return false;
        } else if (std::strcmp(arg, "--measure-offset") == 0) {
            if (!ParseSize(value, p_options.measureOffset)) return false;
        } else {
            return false;
        }
    }
    const bool hasSingleTruth = !p_options.truthFile.empty() && p_options.truthDir.empty();
    const bool hasAllLevelTruth = p_options.truthFile.empty() && !p_options.truthDir.empty();
    const bool basicOptions = (p_options.valueType == "Float" || p_options.valueType == "UInt8") &&
        !p_options.indexDir.empty() && !p_options.queryFile.empty() &&
        (p_options.allAclLevels ? hasAllLevelTruth : hasSingleTruth);
    const int filterModes =
        (p_options.tagColumn >= 0 ? 1 : 0) +
        (p_options.orTagCount > 0 ? 1 : 0) +
        (!p_options.dnfAndColumns.empty() ? 1 : 0) +
        (!p_options.queryDNFFile.empty() ? 1 : 0);
    if (!basicOptions || filterModes > 1) {
        return false;
    }
    if (p_options.allAclLevels) {
        return !p_options.directSearch && p_options.tagColumn < 0 &&
            p_options.orTagCount == 0 &&
            p_options.dnfAndColumns.empty() &&
            p_options.queryDNFFile.empty() &&
            !p_options.queryTagsFile.empty();
    }
    const bool validFilterInput =
        (filterModes == 0 &&
         p_options.queryTagsFile.empty() &&
         p_options.queryDNFFile.empty()) ||
        (filterModes == 1 &&
         ((!p_options.queryDNFFile.empty() &&
           p_options.queryTagsFile.empty()) ||
          (p_options.queryDNFFile.empty() &&
           !p_options.queryTagsFile.empty())));
    return validFilterInput &&
        (!p_options.directSearch ||
         filterModes == 0);
}

bool ReadNpyHeader(std::ifstream& p_input, NpyHeader& p_header)
{
    char magic[6] = {};
    std::uint8_t major = 0;
    std::uint8_t minor = 0;
    if (!p_input.read(magic, sizeof(magic)) || !p_input.read(reinterpret_cast<char*>(&major), 1) ||
        !p_input.read(reinterpret_cast<char*>(&minor), 1) ||
        std::memcmp(magic, "\x93NUMPY", sizeof(magic)) != 0) {
        return false;
    }

    std::uint32_t headerLength = 0;
    if (major == 1) {
        std::uint16_t length16 = 0;
        if (!p_input.read(reinterpret_cast<char*>(&length16), sizeof(length16))) return false;
        headerLength = length16;
    } else if (major == 2 || major == 3) {
        if (!p_input.read(reinterpret_cast<char*>(&headerLength), sizeof(headerLength))) return false;
    } else {
        return false;
    }

    std::string header(headerLength, '\0');
    if (!p_input.read(header.data(), static_cast<std::streamsize>(header.size()))) return false;
    const std::regex descrPattern("'descr':\\s*'([^']+)'");
    const std::regex shapePattern("'shape':\\s*\\(([^)]*)\\)");
    std::smatch match;
    if (!std::regex_search(header, match, descrPattern)) return false;
    p_header.descr = match[1].str();
    if (!std::regex_search(header, match, shapePattern)) return false;

    std::stringstream shapeStream(match[1].str());
    std::string token;
    while (std::getline(shapeStream, token, ',')) {
        std::stringstream valueStream(token);
        std::size_t value = 0;
        if (valueStream >> value) p_header.shape.push_back(value);
    }
    p_header.dataOffset = static_cast<std::size_t>(p_input.tellg());
    return !p_header.shape.empty();
}

template <typename T>
bool ReadNpyMatrix(const std::string& p_path, const std::string& p_expectedDescr,
                   std::vector<T>& p_values, std::size_t& p_rows, std::size_t& p_cols)
{
    std::ifstream input(p_path, std::ios::binary);
    NpyHeader header;
    if (!input || !ReadNpyHeader(input, header) || header.descr != p_expectedDescr ||
        header.shape.size() != 2 || header.shape[0] == 0 || header.shape[1] == 0) {
        return false;
    }
    p_rows = header.shape[0];
    p_cols = header.shape[1];
    if (p_rows > (std::numeric_limits<std::size_t>::max)() / p_cols ||
        p_rows * p_cols > (std::numeric_limits<std::size_t>::max)() / sizeof(T)) {
        return false;
    }
    p_values.resize(p_rows * p_cols);
    return static_cast<bool>(input.read(
        reinterpret_cast<char*>(p_values.data()),
        static_cast<std::streamsize>(p_values.size() * sizeof(T))));
}

std::size_t RecallHits(const std::vector<std::int64_t>& p_truth, std::size_t p_truthCols,
                       const std::vector<std::int32_t>& p_results, std::size_t p_truthOffset,
                       std::size_t p_queryCount, int p_topk)
{
    std::size_t hits = 0;
    for (std::size_t query = 0; query < p_queryCount; ++query) {
        std::unordered_set<std::int64_t> expected;
        const auto* truth = p_truth.data() + (p_truthOffset + query) * p_truthCols;
        for (std::size_t i = 0; i < p_truthCols && expected.size() < static_cast<std::size_t>(p_topk); ++i) {
            if (truth[i] >= 0) expected.insert(truth[i]);
        }
        std::unordered_set<std::int32_t> seen;
        const auto* result = p_results.data() + query * static_cast<std::size_t>(p_topk);
        for (int i = 0; i < p_topk; ++i) {
            if (result[i] >= 0 && seen.insert(result[i]).second &&
                expected.count(result[i]) != 0) {
                ++hits;
            }
        }
    }
    return hits;
}

struct SearchPoint {
    std::string path;
    std::map<std::string, std::string> parameters;
    int nprobe = 0;
    bool array = false;
};

int ScalarSearchCount(const std::string& value, int minimum)
{
    if (value.find_first_of("[,]") != std::string::npos)
        throw std::runtime_error("Arrays belong only in [SearchSweep] NProbe");
    const auto values = NativeNProbeSweep::Parse(value, minimum);
    if (values.size() != 1) throw std::runtime_error("Expected scalar native search count");
    return values.front();
}

std::vector<SearchPoint> ReadSearchIni(const std::string& p_path)
{
    Helper::IniReader searchIni;
    if (searchIni.LoadIniFile(p_path) != ErrorCode::Success ||
        !searchIni.DoesSectionExist("SearchSSDIndex")) {
        std::cerr << "Invalid native [SearchSSDIndex] INI: " << p_path << "\n";
        throw std::runtime_error("Cannot read native search INI");
    }
    const auto& parameters = searchIni.GetParameters("SearchSSDIndex");
    if (parameters.empty()) {
        std::cerr << "Empty native [SearchSSDIndex] INI: " << p_path << "\n";
        throw std::runtime_error("Empty native search INI");
    }
    const auto probeField = parameters.find("internalresultnum");
    const auto resultField = parameters.find("resultnum");
    if (probeField == parameters.end() || resultField == parameters.end())
        throw std::runtime_error("Native INI must specify InternalResultNum and ResultNum");
    const int resultNum = ScalarSearchCount(resultField->second, 1);
    const int scalarProbe = ScalarSearchCount(probeField->second, resultNum);
    const bool array = searchIni.DoesSectionExist("SearchSweep");
    if (array && (searchIni.GetParameters("SearchSweep").size() != 1 ||
                  !searchIni.DoesParameterExist("SearchSweep", "NProbe")))
        throw std::runtime_error("[SearchSweep] must contain only NProbe");
    const auto probes = array
        ? NativeNProbeSweep::Parse(
              searchIni.GetParameter<std::string>("SearchSweep", "NProbe", ""), resultNum)
        : std::vector<int>{scalarProbe};
    std::vector<SearchPoint> points;
    for (int probe : probes) {
        SearchPoint point{p_path, parameters, probe, array};
        point.parameters["internalresultnum"] = std::to_string(probe);
        if (array) {
            const auto output = point.parameters.find("shortcutoutput");
            if (output == point.parameters.end()) throw std::runtime_error("Missing ShortcutOutput");
            output->second += ".nprobe_" + std::to_string(probe);
        }
        points.push_back(std::move(point));
    }
    return points;
}

void ApplySearchPoint(TenantIndexManager& p_manager, const SearchPoint& point) {
    ShortcutFull::Configure(point.parameters);
    for (const auto& parameter : point.parameters) {
        if (parameter.first.rfind("shortcut", 0) == 0) continue;
        p_manager.SetSearchParam(
            parameter.first.c_str(), parameter.second.c_str(), "SearchSSDIndex");
    }
}

} // namespace

int Run(int argc, char** argv)
{
    Options options;
    if (!ParseArgs(argc, argv, options)) {
        Usage(argv[0]);
        return 2;
    }
    std::vector<SearchPoint> defaults, searchPoints;
    if (!options.searchIni.empty()) defaults = ReadSearchIni(options.searchIni);
    for (const auto& path : options.searchSweepInis) {
        auto points = ReadSearchIni(path);
        searchPoints.insert(searchPoints.end(), points.begin(), points.end());
    }
    if (searchPoints.empty()) searchPoints = defaults;
    if (searchPoints.empty()) throw std::runtime_error("A native search INI is required");
    if (!options.searchSweepInis.empty() && !defaults.empty() && defaults.front().array)
        throw std::runtime_error("Put [SearchSweep] in the active --search-sweep-ini, not the base INI");
    std::set<std::string> outputs;
    for (auto& point : searchPoints) {
        if (ScalarSearchCount(point.parameters.at("resultnum"), 1) != options.topk)
            throw std::runtime_error("Native ResultNum must match --topk");
        if (searchPoints.size() > 1 && !point.array)
            point.parameters.at("shortcutoutput") += ".nprobe_" + std::to_string(point.nprobe);
        if (!outputs.insert(point.parameters.at("shortcutoutput")).second)
            throw std::runtime_error("Batch probes would overwrite a capture file");
    }

    std::vector<float> queries;
    std::vector<std::uint32_t> queryTags;
    std::vector<std::uint32_t> queryDNF;
    std::size_t queryCount = 0, dimension = 0;
    std::size_t tagCount = 0, tagCols = 0;
    std::size_t dnfCount = 0, dnfCols = 0;
    const bool requiresQueryTags =
        options.allAclLevels || options.tagColumn >= 0 ||
        options.orTagCount > 0 ||
        !options.dnfAndColumns.empty();
    if (!ReadNpyMatrix(options.queryFile, "<f4", queries, queryCount, dimension) ||
        (requiresQueryTags &&
         (!ReadNpyMatrix(options.queryTagsFile, "<u4", queryTags, tagCount, tagCols) ||
          tagCount != queryCount ||
          (options.allAclLevels && tagCols < 4) ||
          (options.tagColumn >= 0 && static_cast<std::size_t>(options.tagColumn) >= tagCols) ||
          (options.orTagCount > 0 &&
           static_cast<std::size_t>(options.orTagCount) > tagCols) ||
          std::any_of(options.dnfAndColumns.begin(), options.dnfAndColumns.end(),
                      [tagCols](int column) { return static_cast<std::size_t>(column) >= tagCols; })))) {
        std::cerr << "Invalid query, truth, or tag npy input\n";
        return 1;
    }
    if (!options.queryDNFFile.empty()) {
        constexpr std::uint32_t kDNF3Magic =
            0x444E4633U;
        if (!ReadNpyMatrix(
                options.queryDNFFile, "<u4",
                queryDNF, dnfCount, dnfCols) ||
            dnfCount != queryCount ||
            dnfCols < 2) {
            std::cerr << "Invalid per-query DNF input\n";
            return 1;
        }
        for (std::size_t query = 0;
             query < dnfCount; ++query) {
            const std::uint32_t* row =
                queryDNF.data() + query * dnfCols;
            if (row[0] == 0 ||
                row[0] >= dnfCols ||
                row[1] != kDNF3Magic) {
                std::cerr
                    << "Invalid length-prefixed DNF3 row "
                    << query << "\n";
                return 1;
            }
        }
    }

    const char* allLevelNames[] = {"unfilter", "org", "dept", "team", "project"};
    std::vector<TruthMatrix> truthMatrices;
    truthMatrices.reserve(options.allAclLevels ? 5 : 1);
    const auto loadTruth = [&](const std::string& p_path) {
        truthMatrices.emplace_back();
        TruthMatrix& matrix = truthMatrices.back();
        if (!ReadNpyMatrix(p_path, "<i8", matrix.values, matrix.rows, matrix.cols) ||
            matrix.rows != queryCount ||
            matrix.cols < static_cast<std::size_t>(options.topk)) {
            truthMatrices.pop_back();
            return false;
        }
        return true;
    };
    if (options.allAclLevels) {
        for (const char* level : allLevelNames) {
            if (!loadTruth(
                    options.truthDir + "/groundtruth_" + level + "_local_ids.npy")) {
                std::cerr << "Invalid ground truth for " << level << "\n";
                return 1;
            }
        }
    } else if (!loadTruth(options.truthFile)) {
        std::cerr << "Invalid ground truth input\n";
        return 1;
    }

    if (options.measureOffset >= queryCount) {
        std::cerr << "Measurement offset exceeds available queries\n";
        return 1;
    }
    const std::size_t availableQueries = queryCount - options.measureOffset;
    const std::size_t measuredQueries = options.maxQueries == 0
        ? availableQueries
        : (std::min)(options.maxQueries, availableQueries);
    if (measuredQueries == 0) return 1;

    std::vector<std::uint8_t> uint8Queries;
    if (options.valueType == "UInt8") {
        uint8Queries.reserve(queries.size());
        for (float value : queries) {
            if (!std::isfinite(value) || value < 0.0f || value > 255.0f ||
                std::trunc(value) != value) {
                std::cerr << "UInt8 query input contains a non-integral or out-of-range value\n";
                return 1;
            }
            uint8Queries.push_back(static_cast<std::uint8_t>(value));
        }
    }

    TenantIndexManager manager(
        static_cast<DimensionType>(dimension), "SPANN", options.valueType.c_str());
    if (!defaults.empty()) ApplySearchPoint(manager, defaults.front());
    if (!manager.LoadAll(options.indexDir.c_str())) {
        std::cerr << "LoadAll failed: " << options.indexDir << "\n";
        return 1;
    }
    if (NativeBatch::indexLoadCalls != 1) throw std::runtime_error("Expected exactly one native LoadAll");
    std::cout << "NATIVE_INDEX_READY load_calls=" << NativeBatch::indexLoadCalls << "\n";

    struct Scenario
    {
        const char* level;
        int tagColumn;
        int orTagCount;
        const std::vector<int>* dnfAndColumns;
        bool queryDNF;
        const TruthMatrix* truth;
    };

    std::vector<Scenario> scenarios;
    if (options.allAclLevels) {
        scenarios = {
            {"unfilter", -1, 0, nullptr, false, &truthMatrices[0]},
            {"org", 0, 0, nullptr, false, &truthMatrices[1]},
            {"dept", 1, 0, nullptr, false, &truthMatrices[2]},
            {"team", 2, 0, nullptr, false, &truthMatrices[3]},
            {"project", 3, 0, nullptr, false, &truthMatrices[4]},
        };
    } else {
        scenarios.push_back(
            {!options.queryDNFFile.empty()
                 ? "dnf"
                 : options.orTagCount > 0
                 ? "or"
                 : (options.tagColumn < 0 ? "unfilter" : "custom"),
             options.tagColumn,
             options.orTagCount,
             &options.dnfAndColumns,
             !options.queryDNFFile.empty(),
             &truthMatrices.front()});
    }

    const std::size_t warmup = (std::min)(options.warmup, queryCount);
    std::size_t probePosition = 0;
    for (const auto& searchPoint : searchPoints) {
        ApplySearchPoint(manager, searchPoint);
        // Heap::clear only grows capacity. Drop the cached search workspace so
        // descending probe order cannot retain a previous larger result heap.
        COMMON::ThreadLocalWorkSpaceFactory<COMMON::WorkSpace>::m_workspace.reset();
        ++NativeBatch::workspaceResets;
        if (NativeBatch::indexLoadCalls != 1) throw std::runtime_error("Probe change reloaded the index");
        const std::string& activeSearchIni = searchPoint.path;
        std::cout << "NATIVE_PROBE_BEGIN nprobe=" << searchPoint.nprobe
                  << " position=" << probePosition << " load_calls=" << NativeBatch::indexLoadCalls
                  << " workspace_resets=" << NativeBatch::workspaceResets << "\n";
        for (const Scenario& scenario : scenarios) {

            auto search = [&](std::size_t p_queryIndex) {
                ShortcutFull::Last() = {};

                const ByteArray queryBytes = options.valueType == "UInt8"
                    ? ByteArray(
                        uint8Queries.data() + p_queryIndex * dimension, dimension, false)
                    : ByteArray(
                        reinterpret_cast<std::uint8_t*>(queries.data() + p_queryIndex * dimension),
                        dimension * sizeof(float), false);
                if (options.directSearch) {
                    return manager.Search(queryBytes, options.tenant, options.topk);
                }
                if (scenario.orTagCount > 0) {
                    auto* tags = queryTags.data() +
                        p_queryIndex * tagCols;
                    const ByteArray tagBytes(
                        reinterpret_cast<std::uint8_t*>(tags),
                        static_cast<std::size_t>(
                            scenario.orTagCount) *
                            sizeof(std::uint32_t),
                        false);
                    return manager.SearchWithPredicate(
                        queryBytes, options.tenant,
                        options.topk, tagBytes,
                        scenario.orTagCount);
                }
                if (scenario.queryDNF) {
                    auto* row = queryDNF.data() +
                        p_queryIndex * dnfCols;
                    const ByteArray dnfBytes(
                        reinterpret_cast<std::uint8_t*>(
                            row + 1),
                        static_cast<size_t>(row[0]) *
                            sizeof(std::uint32_t),
                        false);
                    return manager.SearchWithPredicate(
                        queryBytes, options.tenant,
                        options.topk, dnfBytes, -1);
                }
                if (scenario.dnfAndColumns != nullptr && !scenario.dnfAndColumns->empty()) {
                    constexpr std::uint32_t kDNF3Magic = 0x444E4633U;
                    std::vector<std::uint32_t> dnf;
                    dnf.reserve(3 + scenario.dnfAndColumns->size() * 4);
                    dnf.push_back(kDNF3Magic);
                    dnf.push_back(1); // one OR clause
                    dnf.push_back(static_cast<std::uint32_t>(scenario.dnfAndColumns->size()));
                    for (int column : *scenario.dnfAndColumns) {
                        dnf.push_back(0); // categorical
                        dnf.push_back(static_cast<std::uint32_t>(column));
                        dnf.push_back(SPTAG::Cache::DNF_EQ);
                        dnf.push_back(queryTags[
                            p_queryIndex * tagCols + static_cast<std::size_t>(column)]);
                    }
                    const ByteArray dnfBytes(
                        reinterpret_cast<std::uint8_t*>(dnf.data()),
                        dnf.size() * sizeof(std::uint32_t),
                        false);
                    return manager.SearchWithPredicate(
                        queryBytes, options.tenant, options.topk, dnfBytes, -1);
                }
                std::uint32_t predicate[] = {
                    0x444E4633U, 1, 1, 0,
                    static_cast<std::uint32_t>(scenario.tagColumn), SPTAG::Cache::DNF_EQ,
                    scenario.tagColumn >= 0 ? queryTags[
                        p_queryIndex * tagCols + static_cast<std::size_t>(scenario.tagColumn)] : 0};
                const ByteArray tagBytes(
                    reinterpret_cast<std::uint8_t*>(predicate),
                    scenario.tagColumn >= 0 ? sizeof(predicate) : 0, false);
                return manager.SearchWithPredicate(
                    queryBytes,
                    options.tenant,
                    options.topk,
                    tagBytes,
                    scenario.tagColumn >= 0 ? -1 : 0);
            };

            std::vector<ShortcutFull::Observation> validation;
            std::vector<std::pair<int, float>> validationResults;
            if (ShortcutFull::capture) {
                for (std::size_t i = 0; i < measuredQueries; ++i) {
                    auto r = search(options.measureOffset + i);
                    if (!r || !ShortcutFull::Last().invoked)
                        throw std::runtime_error("Full-query validation search failed");
                    validation.push_back(std::move(ShortcutFull::Last()));
                    for (int j = 0; j < options.topk; ++j)
                        validationResults.emplace_back(r->GetResult(j)->VID, r->GetResult(j)->Dist);
                }
                ShortcutFull::capture = false;
            }
            for (std::size_t i = 0; i < warmup; ++i) {
                search(i);
            }

            std::vector<std::int32_t> resultIds(
                measuredQueries * static_cast<std::size_t>(options.topk), -1);

            std::vector<float> resultDistances(resultIds.size(), MaxDist);
            std::vector<ShortcutFull::Observation> observations(measuredQueries);
            std::size_t failed = 0;
            std::uint64_t readPostings = 0;
            std::uint64_t matchedPostings = 0;
            std::uint64_t uniqueMatchedPostings = 0;
            std::uint64_t scannedVectors = 0;
            std::uint64_t matchedVectors = 0;
            std::uint64_t dedupSkippedVectors = 0;
            std::uint64_t uniqueMatchedVectors = 0;
            std::uint64_t distanceComputations = 0;
            std::uint64_t postingPageReads = 0;
            std::uint64_t postingLogicalBytes = 0;
            std::uint64_t postingPhysicalBytes = 0;
            const auto start = std::chrono::steady_clock::now();
            for (std::size_t i = 0; i < measuredQueries; ++i) {
                const auto result = search(options.measureOffset + i);

                if (!ShortcutFull::Last().invoked) throw std::runtime_error("Full H1 hook not invoked");
                observations[i] = std::move(ShortcutFull::Last());
                const auto postingStats = VectorIndex::GetThreadLocalPostingScanStats();
                readPostings += postingStats.m_readPostings;
                matchedPostings += postingStats.m_matchedPostings;
                uniqueMatchedPostings += postingStats.m_uniqueMatchedPostings;
                scannedVectors += postingStats.m_scannedVectors;
                matchedVectors += postingStats.m_matchedVectors;
                dedupSkippedVectors += postingStats.m_dedupSkippedVectors;
                uniqueMatchedVectors += postingStats.m_uniqueMatchedVectors;
                postingPageReads += postingStats.m_postingPageReads;
                postingLogicalBytes += postingStats.m_postingLogicalBytes;
                postingPhysicalBytes += postingStats.m_postingPhysicalBytes;
                if (result == nullptr) {
                    ++failed;
                    continue;
                }
                distanceComputations += static_cast<std::uint64_t>(
                    (std::max)(result->GetScanned(), 0));
                const int count = (std::min)(result->GetResultNum(), options.topk);
                for (int j = 0; j < count; ++j) {
                    const auto* item = result->GetResult(j);
                    if (item != nullptr && item->VID >= 0 &&
                        item->VID <= static_cast<SizeType>((std::numeric_limits<std::int32_t>::max)())) {
                        resultIds[
                            i * static_cast<std::size_t>(options.topk) + static_cast<std::size_t>(j)] =

                            static_cast<std::int32_t>(item->VID);
                        resultDistances[i * options.topk + j] = item->Dist;
                    }
                }
            }
            const auto finish = std::chrono::steady_clock::now();
            const double elapsed = std::chrono::duration<double>(finish - start).count();

            std::ofstream dump(ShortcutFull::output, std::ios::binary);
            if (!dump) throw std::runtime_error("Cannot write shortcut query results");
            std::uint64_t calls = 0, adjacency = 0, shortcutCalls = 0, exhausted = 0;
            for (std::size_t i = 0; i < measuredQueries; ++i) {
                auto& o = observations[i];
                if (!validation.empty()) {
                    const auto& v = validation[i];
                    const auto& a = v.native;
                    const auto& b = o.native;
                    if (a.calls != b.calls || a.parentDistances != b.parentDistances ||
                        a.members != b.members || a.checked != b.checked ||
                        a.h2Rows != b.h2Rows || a.h3Rows != b.h3Rows ||
                        !a.originalDistanceFunction || !b.originalDistanceFunction ||
                        a.defaultAdmission != b.defaultAdmission || a.filteredAdmission != b.filteredAdmission)
                        throw std::runtime_error("Capture changed native injection work or distance primitive");
                    if (ShortcutFull::profile && (a.headDistances != b.headDistances ||
                        a.routingDistances != b.routingDistances || a.childDistances != b.childDistances))
                        throw std::runtime_error("Profile changed native distance work");
                    o = v;
                    for (int j = 0; j < options.topk; ++j) {
                        auto position = i * options.topk + j;
                        if (validationResults[position].first != resultIds[position] ||
                            validationResults[position].second != resultDistances[position])
                            throw std::runtime_error("Capture changed final native results");
                    }
                }
                const auto& n = o.native;
                calls += n.headDistances + n.routingDistances + n.childDistances + n.parentDistances;
                adjacency += n.queueOffers;
                shortcutCalls += n.childDistances;
                dump << "{\"query\":" << i
                     << ",\"work_source\":\"untimed_native_capture\""
                     << ",\"native_raw_distance_function\":" << n.originalDistanceFunction
                     << ",\"native_default_admission\":" << n.defaultAdmission
                     << ",\"native_filtered_admission\":" << n.filteredAdmission
                     << ",\"row_eligibility_evaluations\":" << n.rowEligibilityEvaluations
                     << ",\"native_head_distances\":" << n.headDistances
                     << ",\"native_routing_distances\":" << n.routingDistances
                     << ",\"native_child_distances\":" << n.childDistances
                     << ",\"supplier_parent_distances\":" << n.parentDistances
                     << ",\"supplier_calls\":" << n.calls
                     << ",\"supplier_returns\":" << n.returns
                     << ",\"supplier_members\":" << n.members
                     << ",\"supplier_h2_rows\":" << n.h2Rows
                     << ",\"supplier_h3_rows\":" << n.h3Rows
                     << ",\"signature_checks\":" << n.signatureChecks
                     << ",\"signature_rejects\":" << n.signatureRejects
                     << ",\"native_checked\":" << n.checked
                     << ",\"queue_offers\":" << n.queueOffers
                     << ",\"queue_accepted\":" << n.queueAccepted
                     << ",\"queue_rejected\":" << n.queueRejected
                     << ",\"degree_frames\":[";
                for (size_t j = 0; j < n.frames.size(); ++j) {
                    if (j) dump << ",";
                    const auto& a = n.frames[j];
                    dump << "{\"head\":" << a.head << ",\"d\":" << a.physical
                         << ",\"e\":" << a.eligible << ",\"required\":" << a.required
                         << ",\"startup\":" << a.startup << ",\"calls\":" << a.calls
                         << ",\"checked_before\":" << a.before << ",\"checked_after\":" << a.after
                         << ",\"ordinary\":[";
                    for (size_t k = 0; k < a.ordinary.size(); ++k) {
                        if (k) dump << ",";
                        dump << "[" << a.ordinary[k].first << "," << a.ordinary[k].second << "]";
                    }
                    dump << "],\"supplied\":[";
                    for (size_t k = 0; k < a.supplied.size(); ++k) {
                        if (k) dump << ",";
                        dump << a.supplied[k];
                    }
                    dump << "]}";
                }
                dump << "],\"rows\":[";
                for (size_t j = 0; j < n.rows.size(); ++j) {
                    if (j) dump << ",";
                    const auto& r = n.rows[j];
                    dump << "[" << r.level << "," << r.id << "," << r.size << "," << r.consumed << "]";
                }
                dump << "],\"rejected_rows\":[";
                for (size_t j = 0; j < n.rejectedRows.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[" << n.rejectedRows[j].first << "," << n.rejectedRows[j].second << "]";
                }
                dump << "],\"evaluated_h1\":[";
                for (size_t j = 0; j < n.evaluated.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[" << n.evaluated[j].first << "," << std::setprecision(9)
                         << n.evaluated[j].second << "]";
                }
                dump << "],\"own_ids\":[";
                for (size_t j = 0; j < o.ownIDs.size(); ++j) {
                    if (j) dump << ",";
                    dump << o.ownIDs[j];
                }

                dump << "],\"heads\":[";
                for (std::size_t j = 0; j < o.heads.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[" << o.heads[j] << "," << std::setprecision(9) << o.distances[j] << "]";
                }
                dump << "],\"results\":[";
                for (int j = 0; j < options.topk; ++j) {
                    if (j) dump << ",";
                    dump << "[" << resultIds[i * options.topk + j] << ","
                         << std::setprecision(9) << resultDistances[i * options.topk + j] << "]";
                }
                dump << "]}\n";
            }
            if (!dump) throw std::runtime_error("Shortcut query result write failed");
            std::cout << "SHORTCUT_WORK calls=" << calls << " adjacency=" << adjacency
                      << " shortcut=" << shortcutCalls << " exhausted=" << exhausted << "\n";
            const std::size_t hits = RecallHits(
                scenario.truth->values,
                scenario.truth->cols,
                resultIds,
                options.measureOffset,
                measuredQueries,
                options.topk);
            const double recall = static_cast<double>(hits) /
                static_cast<double>(measuredQueries * static_cast<std::size_t>(options.topk));
            const auto perQuery = [measuredQueries](std::uint64_t value) {
                return static_cast<double>(value) / static_cast<double>(measuredQueries);
            };
            const auto ratio = [](std::uint64_t numerator, std::uint64_t denominator) {
                return denominator == 0 ? 0.0 : static_cast<double>(numerator) / static_cast<double>(denominator);
            };
            if (dedupSkippedVectors > scannedVectors) {
                throw std::runtime_error("Duplicate scan count exceeds scanned records");
            }
            const std::uint64_t uniqueScannedVectors =
                scannedVectors - dedupSkippedVectors;

            std::cout << "{"
                      << "\"engine\":\"static_per_tag_bkt\","
                      << "\"nprobe\":" << searchPoint.nprobe << ","
                      << "\"probe_position\":" << probePosition << ","
                      << "\"probe_count\":" << searchPoints.size() << ","
                      << "\"index_load_count\":" << NativeBatch::indexLoadCalls << ","
                      << "\"sweep_execution\":\"" << (searchPoint.array ? "single_load_nprobe_array" : "single_probe") << "\","
                      << "\"native_index_load_calls\":" << NativeBatch::indexLoadCalls << ","
                      << "\"native_workspace_resets\":" << NativeBatch::workspaceResets << ","
                      << "\"capture_file\":\"" << ShortcutFull::output << "\","
                      << "\"level\":\"" << scenario.level << "\","
                      << "\"queries\":" << measuredQueries << ","
                      << "\"measure_offset\":" << options.measureOffset << ","
                      << "\"value_type\":\"" << options.valueType << "\","
                      << "\"search_ini\":\"" << activeSearchIni << "\","
                      << "\"search_api\":\"" << (options.directSearch ? "Search" : "SearchWithPredicate") << "\","
                      << "\"filter_column\":" << scenario.tagColumn << ","
                      << "\"or_tag_count\":" << scenario.orTagCount << ","
                      << "\"dnf_and_columns\":"
                      << (scenario.dnfAndColumns == nullptr ? 0 : scenario.dnfAndColumns->size()) << ","
                      << "\"query_dnf\":"
                      << (scenario.queryDNF ? "true" : "false") << ","
                      << "\"recall\":" << recall << ","
                      << "\"qps\":" << static_cast<double>(measuredQueries) / elapsed << ","
                      << "\"mean_latency_ms\":" << 1000.0 * elapsed / measuredQueries << ","
                      << "\"postings_per_query\":" << perQuery(readPostings) << ","
                      << "\"contributing_postings_per_query\":" << perQuery(matchedPostings) << ","
                      << "\"unique_matched_postings_per_query\":" << perQuery(uniqueMatchedPostings) << ","
                      << "\"scanned_vectors_per_query\":" << perQuery(scannedVectors) << ","
                      << "\"unique_scanned_vectors_per_query\":" << perQuery(uniqueScannedVectors) << ","
                      << "\"matched_vectors_per_query\":"
                      << perQuery(matchedVectors) << ","
                      << "\"unique_matched_vectors_per_query\":" << perQuery(uniqueMatchedVectors) << ","
                      << "\"distance_computations_per_query\":"
                      << perQuery(distanceComputations) << ","
                      << "\"dedup_skipped_vectors_per_query\":"
                      << perQuery(dedupSkippedVectors) << ","
                      << "\"match_rate\":" << ratio(matchedVectors, uniqueScannedVectors) << ","
                      << "\"unique_match_rate\":" << ratio(uniqueMatchedVectors, uniqueScannedVectors) << ","
                      << "\"unique_vectors_per_loaded_posting\":"
                      << ratio(uniqueMatchedVectors, readPostings) << ","
                      << "\"unique_vectors_per_contributing_posting\":"
                      << ratio(uniqueMatchedVectors, uniqueMatchedPostings) << ","
                      << "\"scanned_occurrence_to_unique_ratio\":"
                      << ratio(scannedVectors, uniqueScannedVectors) << ","
                      << "\"posting_page_reads_per_query\":" << perQuery(postingPageReads) << ","
                      << "\"posting_logical_bytes_per_query\":" << perQuery(postingLogicalBytes) << ","
                      << "\"posting_physical_bytes_per_query\":" << perQuery(postingPhysicalBytes) << ","
                      << "\"failed_queries\":" << failed
                      << "}\n";
        }
        ++probePosition;
    }
    return 0;
}

int main(int argc, char** argv) {
    try {
        return Run(argc, argv);
    } catch (const std::exception& error) {
        std::cerr << "Native batch error: " << error.what() << "\n";
        return 2;
    }
}
