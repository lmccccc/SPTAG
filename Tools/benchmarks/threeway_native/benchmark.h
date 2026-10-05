#pragma once

#include <algorithm>
#include <array>
#include <cerrno>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <exception>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <limits>
#include <locale>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include <fcntl.h>
#include <omp.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

// This client layer never changes an engine's search algorithm. One factory call
// owns the resident index; configure runs only between drained OpenMP cohorts.
// Timed throughput includes dispatch, validation, recall and histogram reduction,
// but not result-file writes. Single-cohort validation is outside its timer.
namespace threeway
{
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

inline void require(bool condition, const std::string &message)
{
    if (!condition)
        throw std::runtime_error(message);
}

inline std::string trim(const std::string &value)
{
    const auto first = value.find_first_not_of(" \t\r\n");
    return first == std::string::npos ? std::string() :
                                     value.substr(first, value.find_last_not_of(" \t\r\n") - first + 1);
}

inline std::string lower(std::string value)
{
    for (char &c : value)
        if (c >= 'A' && c <= 'Z')
            c = static_cast<char>(c - 'A' + 'a');
    return value;
}

inline bool identifier(const std::string &value)
{
    return !value.empty() && std::all_of(value.begin(), value.end(), [](unsigned char c) {
               return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                      (c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.';
           });
}

inline uint64_t parse_uint(const std::string &value, const std::string &context)
{
    require(!value.empty() && value.front() >= '0' && value.front() <= '9',
            context + ": expected an unsigned decimal integer");
    uint64_t result = 0;
    const auto parsed = std::from_chars(value.data(), value.data() + value.size(), result, 10);
    require(parsed.ec == std::errc() && parsed.ptr == value.data() + value.size(),
            context + ": invalid unsigned integer: " + value);
    return result;
}

inline uint32_t u32(uint64_t value, const std::string &context, bool allow_zero = false)
{
    require((allow_zero || value != 0) && value <= std::numeric_limits<uint32_t>::max(),
            context + ": integer outside permitted uint32 range");
    return static_cast<uint32_t>(value);
}

inline double parse_double(const std::string &value, const std::string &context)
{
    // Reject C hexadecimal floats, infinities, NaNs and partially parsed values.
    require(!value.empty() && value.find_first_not_of("0123456789+-.eE") == std::string::npos,
            context + ": expected a finite decimal number");
    std::istringstream input(value);
    input.imbue(std::locale::classic());
    double result = 0;
    input >> std::noskipws >> result;
    require(input && input.peek() == std::char_traits<char>::eof() && std::isfinite(result),
            context + ": invalid finite decimal number: " + value);
    return result;
}

inline void validate_sha256(const std::string &value, const std::string &context)
{
    require(value.size() == 64 && std::all_of(value.begin(), value.end(), [](unsigned char c) {
                return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
            }), context + ": expected a 64-digit SHA-256 digest");
}

inline void reject_environment_overrides()
{
    constexpr const char *prefixes[] = {"SPTAG_", "SPANN_", "PIPEANN_", "DISKANN_", "FILTERED_DISKANN_",
                                      "OMP_", "GOMP_", "KMP_", "MKL_", "OPENBLAS_"};
    const std::set<std::string> exact{"LD_PRELOAD", "ADDITIONAL_DEFINITIONS", "CXXFLAGS", "CPPFLAGS"};
    std::set<std::string> forbidden;
    for (char **entry = ::environ; entry && *entry; ++entry)
    {
        const std::string assignment(*entry);
        const std::string name = assignment.substr(0, assignment.find('='));
        bool banned = exact.count(name) != 0;
        for (const auto *prefix : prefixes)
            banned = banned || name.compare(0, std::strlen(prefix), prefix) == 0;
        if (banned)
            forbidden.insert(name);
    }
    std::string names;
    for (const auto &name : forbidden)
        names += (names.empty() ? "" : ", ") + name;
    require(forbidden.empty(), "Remove benchmark environment overrides: " + names);
}

inline std::vector<std::string> split(const std::string &value, char delimiter)
{
    std::vector<std::string> result;
    size_t start = 0;
    do
    {
        const size_t end = value.find(delimiter, start);
        result.push_back(trim(value.substr(start, end == std::string::npos ? end : end - start)));
        if (end == std::string::npos)
            break;
        start = end + 1;
    } while (true);
    return result;
}

class Ini
{
    std::map<std::string, std::map<std::string, std::string>> sections_;

  public:
    explicit Ini(const fs::path &path)
    {
        std::ifstream input(path);
        threeway::require(input.is_open(), "Cannot open INI: " + path.string());
        std::string line, current;
        size_t number = 0;
        while (std::getline(input, line))
        {
            ++number;
            const std::string context = path.string() + ":" + std::to_string(number);
            threeway::require(line.find('\0') == std::string::npos, context + ": NUL in INI");
            line = trim(line);
            if (line.empty() || line.front() == ';')
                continue;
            if (line.front() == '[')
            {
                threeway::require(line.size() > 2 && line.back() == ']', context + ": malformed section");
                current = lower(trim(line.substr(1, line.size() - 2)));
                threeway::require(identifier(current), context + ": invalid section name");
                threeway::require(sections_.emplace(current, std::map<std::string, std::string>{}).second,
                                  context + ": duplicate section " + current);
                continue;
            }
            const auto equals = line.find('=');
            threeway::require(!current.empty() && equals != std::string::npos, context + ": expected key=value in a section");
            const std::string key = lower(trim(line.substr(0, equals)));
            const std::string value = trim(line.substr(equals + 1));
            threeway::require(identifier(key) && !value.empty(), context + ": empty or malformed key/value");
            threeway::require(sections_.at(current).emplace(key, value).second, context + ": duplicate key " + key);
        }
        threeway::require(input.eof(), "Error reading INI: " + path.string());
    }

    bool has(const std::string &section_name, const std::string &key) const
    {
        const auto section = sections_.find(lower(section_name));
        return section != sections_.end() && section->second.count(lower(key));
    }

    bool has_section(const std::string &name) const
    {
        return sections_.count(lower(name)) != 0;
    }

    const std::map<std::string, std::string> &section(const std::string &name) const
    {
        const auto found = sections_.find(lower(name));
        threeway::require(found != sections_.end(), "Missing INI section [" + name + "]");
        return found->second;
    }

    const std::string &require(const std::string &section_name, const std::string &key) const
    {
        const auto &values = section(section_name);
        const auto found = values.find(lower(key));
        threeway::require(found != values.end(), "Missing INI [" + section_name + "] " + key);
        return found->second;
    }

    std::string get(const std::string &section_name, const std::string &key, const std::string &fallback) const
    {
        return has(section_name, key) ? require(section_name, key) : fallback;
    }

    uint64_t uint_value(const std::string &section_name, const std::string &key) const
    {
        return parse_uint(require(section_name, key), "[" + section_name + "] " + key);
    }

    double double_value(const std::string &section_name, const std::string &key) const
    {
        return parse_double(require(section_name, key), "[" + section_name + "] " + key);
    }

    bool bool_value(const std::string &section_name, const std::string &key) const
    {
        const std::string value = lower(require(section_name, key));
        threeway::require(value == "true" || value == "false" || value == "1" || value == "0",
                          "[" + section_name + "] " + key + ": expected true/false/1/0");
        return value == "true" || value == "1";
    }

    void only(const std::string &section_name, const std::set<std::string> &keys) const
    {
        for (const auto &item : section(section_name))
            threeway::require(keys.count(item.first), "Unknown INI [" + section_name + "] key " + item.first);
    }
};

inline fs::path absolute_path(const fs::path &path, const fs::path &relative_to)
{
    return fs::absolute(path.is_absolute() ? path : relative_to / path).lexically_normal();
}

inline std::vector<uint32_t> uint_list(const Ini &ini, const std::string &section, const std::string &key,
                                     bool allow_zero = false)
{
    std::vector<uint32_t> result;
    std::set<uint32_t> distinct;
    for (const auto &value : split(ini.require(section, key), ','))
    {
        const uint32_t parsed = u32(parse_uint(value, section + "." + key), section + "." + key, allow_zero);
        require(distinct.insert(parsed).second, section + "." + key + ": duplicate list value");
        result.push_back(parsed);
    }
    return result;
}

enum class ScenarioKind
{
    unfilter,
    categorical,
    mixed_dnf
};

struct Scenario
{
    std::string name;
    ScenarioKind kind = ScenarioKind::unfilter;
    uint32_t tag = 0, rare_tag = 0, regular_tag = 0, upper_inclusive = 0;
    uint64_t candidate_count = 0;
};

struct Job
{
    size_t scenario_index = 0, control_index = 0, thread_index = 0;
    uint32_t L = 0, threads = 0, repeat = 0;
};

struct Options
{
    Ini ini;
    std::string engine, phase;
    fs::path profile_path, output_directory, prepared_directory, vectors, queries, attributes;
    uint64_t vector_count = 0;
    uint32_t dimension = 0, attribute_columns = 0, categorical_column = 0, numeric_column = 0;
    uint32_t query_count = 0, warmup_queries = 0, top_k = 0, single_repeats = 0, throughput_repeats = 0;
    double minimum_seconds = 0;
    uint64_t aio_reserve = 0;
    std::vector<uint32_t> controls, throughput_threads;
    std::vector<Scenario> scenarios;
    std::vector<Job> jobs;

    Options(const fs::path &profile, std::string selected_phase, std::string selected_engine)
        : ini(profile), engine(std::move(selected_engine)), phase(std::move(selected_phase)),
          profile_path(fs::absolute(profile).lexically_normal())
    {
        reject_environment_overrides();
        require(phase == "single" || phase == "throughput", "Phase must be single or throughput");
        require(engine == "Filtered_DiskANN" || engine == "SPTAG_adaptive" || engine == "PipeANN",
                "Unknown engine identifier: " + engine);
        ini.only("Dataset", {"vectors", "queries", "attributes", "vectorcount", "dimension", "attributecolumns",
                             "categoricalcolumn", "numericcolumn", "querypayloadsha256"});
        ini.only("Run", {"outputdirectory", "prepareddirectory", "builddirectory", "buildcontrollerpid", "pollseconds",
                         "resourceintervalseconds", "minimumfreediskgib", "minimumfreememorygib", "maxchildrssgib", "rscript"});
        ini.only("Benchmark", {"querycount", "warmupqueries", "topk", "singlerepeats", "scenarios"});
        ini.only("Throughput", {"threads", "repeats", "minimumseconds", "recalltargets", "aioreserve", "cpunodes",
                                "memorynodes", "resourcepolicy"});
        ini.only("Single", {"cpunodes", "memorynodes"});
        // Controller/provenance fields travel unchanged with the copied profile.
        // They are validated metadata, never native data/search overrides.
        if (ini.has("Dataset", "QueryPayloadSHA256"))
            validate_sha256(ini.require("Dataset", "QueryPayloadSHA256"), "Dataset.QueryPayloadSHA256");
        if (ini.has("Run", "BuildControllerPID"))
            u32(ini.uint_value("Run", "BuildControllerPID"), "Run.BuildControllerPID");
        for (const std::string key : {"PollSeconds", "ResourceIntervalSeconds", "MaxChildRSSGiB"})
            if (ini.has("Run", key))
                require(ini.double_value("Run", key) > 0, "Run." + key + " must be positive");
        for (const std::string key : {"MinimumFreeDiskGiB", "MinimumFreeMemoryGiB"})
            if (ini.has("Run", key))
                require(ini.double_value("Run", key) >= 0, "Run." + key + " must be nonnegative");
        if (ini.has("Throughput", "ResourcePolicy"))
            require(ini.require("Throughput", "ResourcePolicy") == "current-host-limit",
                    "Only Throughput.ResourcePolicy=current-host-limit is permitted");
        if (ini.has_section("History"))
        {
            ini.only("History", {"summary", "summarysha256", "registration", "registrationsha256",
                                 "assemblymanifest", "assemblymanifestsha256"});
            for (const std::string key : {"SummarySHA256", "RegistrationSHA256", "AssemblyManifestSHA256"})
                if (ini.has("History", key))
                    validate_sha256(ini.require("History", key), "History." + key);
        }
        const auto base = profile_path.parent_path();
        const auto path = [&](const std::string &section, const std::string &key) {
            return absolute_path(ini.require(section, key), base);
        };
        vectors = path("Dataset", "Vectors");
        queries = path("Dataset", "Queries");
        attributes = path("Dataset", "Attributes");
        output_directory = path("Run", "OutputDirectory");
        prepared_directory = path("Run", "PreparedDirectory");
        vector_count = ini.uint_value("Dataset", "VectorCount");
        require(vector_count > 0 && vector_count < std::numeric_limits<uint32_t>::max(),
                "Dataset.VectorCount must fit uint32 IDs with UINT32_MAX reserved as invalid");
        dimension = u32(ini.uint_value("Dataset", "Dimension"), "Dataset.Dimension");
        attribute_columns = u32(ini.uint_value("Dataset", "AttributeColumns"), "Dataset.AttributeColumns");
        categorical_column = u32(ini.uint_value("Dataset", "CategoricalColumn"), "Dataset.CategoricalColumn", true);
        numeric_column = u32(ini.uint_value("Dataset", "NumericColumn"), "Dataset.NumericColumn", true);
        require(categorical_column < attribute_columns && numeric_column < attribute_columns &&
                    categorical_column != numeric_column,
                "Attribute column indices must be distinct and in range");
        query_count = u32(ini.uint_value("Benchmark", "QueryCount"), "Benchmark.QueryCount");
        warmup_queries = u32(ini.uint_value("Benchmark", "WarmupQueries"), "Benchmark.WarmupQueries");
        top_k = u32(ini.uint_value("Benchmark", "TopK"), "Benchmark.TopK");
        require(top_k <= vector_count, "TopK exceeds VectorCount");
        single_repeats = u32(ini.uint_value("Benchmark", "SingleRepeats"), "Benchmark.SingleRepeats");
        throughput_repeats = u32(ini.uint_value("Throughput", "Repeats"), "Throughput.Repeats");
        minimum_seconds = ini.double_value("Throughput", "MinimumSeconds");
        require(minimum_seconds > 0, "Throughput.MinimumSeconds must be positive");
        aio_reserve = ini.uint_value("Throughput", "AIOReserve");
        throughput_threads = uint_list(ini, "Throughput", "Threads");
        for (uint32_t threads : throughput_threads)
            require(threads <= static_cast<uint32_t>(std::numeric_limits<int>::max()),
                    "Thread count exceeds OpenMP integer range");
        for (const auto &target : split(ini.require("Throughput", "RecallTargets"), ','))
        {
            const double value = parse_double(target, "Throughput.RecallTargets");
            require(value > 0 && value <= 1, "RecallTargets must be in (0,1]");
        }
        for (const std::string section : {"Single", "Throughput"})
        {
            uint_list(ini, section, "CPUNodes", true);
            uint_list(ini, section, "MemoryNodes", true);
        }
        const std::string engine_section = engine == "Filtered_DiskANN" ? "DiskANN" :
                                           engine == "SPTAG_adaptive"  ? "SPANN" : "PipeANN";
        controls = uint_list(ini, engine_section, engine == "SPTAG_adaptive" ? "NProbe" : "LSweep");
        if (engine != "SPTAG_adaptive")
            for (uint32_t value : controls)
                require(value >= top_k, engine_section + ".LSweep entries must be >= TopK");
        std::set<std::string> names;
        for (const auto &name : split(ini.require("Benchmark", "Scenarios"), ','))
        {
            require(identifier(name) && name.find('.') == std::string::npos && name == lower(name),
                    "Scenario names must be lowercase identifiers without dots");
            require(names.insert(name).second, "Duplicate scenario: " + name);
            Scenario scenario;
            scenario.name = name;
            const std::string section = "Scenario." + name;
            const std::string kind = lower(ini.require(section, "Kind"));
            std::set<std::string> keys{"kind", "truth", "candidatecount"};
            if (kind == "unfilter")
                scenario.kind = ScenarioKind::unfilter;
            else if (kind == "categorical")
            {
                scenario.kind = ScenarioKind::categorical;
                keys.insert({"tag", "filterconfig"});
                scenario.tag = u32(ini.uint_value(section, "Tag"), section + ".Tag", true);
            }
            else if (kind == "mixed_dnf")
            {
                scenario.kind = ScenarioKind::mixed_dnf;
                keys.insert({"raretag", "regulartag", "upperinclusive", "filterconfig"});
                scenario.rare_tag = u32(ini.uint_value(section, "RareTag"), section + ".RareTag", true);
                scenario.regular_tag = u32(ini.uint_value(section, "RegularTag"), section + ".RegularTag", true);
                scenario.upper_inclusive = u32(ini.uint_value(section, "UpperInclusive"), section + ".UpperInclusive", true);
                require(scenario.rare_tag != scenario.regular_tag, section + ": DNF tags must be distinct");
            }
            else
                throw std::runtime_error(section + ": unsupported Kind " + kind);
            ini.only(section, keys);
            ini.require(section, "Truth");
            scenario.candidate_count = ini.uint_value(section, "CandidateCount");
            require(scenario.candidate_count >= top_k && scenario.candidate_count <= vector_count,
                    section + ": CandidateCount must be between TopK and VectorCount");
            if (scenario.kind == ScenarioKind::unfilter)
                require(scenario.candidate_count == vector_count, section + ": unfiltered CandidateCount differs");
            scenarios.push_back(scenario);
        }
        read_plan();
    }

    fs::path resolve(const std::string &section, const std::string &key) const
    {
        return absolute_path(ini.require(section, key), profile_path.parent_path());
    }

    fs::path native_directory() const
    {
        return output_directory / "native" / engine / phase;
    }

    uint32_t max_threads() const
    {
        uint32_t result = 0;
        for (const Job &job : jobs)
            result = std::max(result, job.threads);
        return result;
    }

  private:
    void read_plan()
    {
        const fs::path path = output_directory / "plans" / (engine + "." + phase + ".tsv");
        std::ifstream input(path);
        require(input.is_open(), "Cannot open plan: " + path.string());
        std::string line;
        require(static_cast<bool>(std::getline(input, line)), "Empty plan: " + path.string());
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        require(line == "scenario\tcontrol_index\tthread_index\trepeat", "Invalid plan TSV header");
        std::set<std::tuple<size_t, uint32_t, uint32_t, uint32_t>> distinct;
        size_t number = 1;
        while (std::getline(input, line))
        {
            ++number;
            if (!line.empty() && line.back() == '\r')
                line.pop_back();
            const std::string context = path.string() + ":" + std::to_string(number);
            const auto fields = split(line, '\t');
            require(fields.size() == 4, context + ": expected exactly four TSV fields");
            Job job;
            auto scenario = std::find_if(scenarios.begin(), scenarios.end(),
                                         [&](const Scenario &item) { return item.name == fields[0]; });
            require(scenario != scenarios.end(), context + ": undeclared scenario " + fields[0]);
            job.scenario_index = static_cast<size_t>(scenario - scenarios.begin());
            const uint64_t control = parse_uint(fields[1], context);
            const uint64_t thread = parse_uint(fields[2], context);
            require(control < controls.size(), context + ": control_index out of range");
            require(phase == "single" ? thread == 0 : thread < throughput_threads.size(),
                    context + ": thread_index out of range (single uses ordinal 0 for one thread)");
            job.control_index = static_cast<size_t>(control);
            job.thread_index = static_cast<size_t>(thread);
            job.repeat = u32(parse_uint(fields[3], context), context);
            require(job.repeat <= (phase == "single" ? single_repeats : throughput_repeats),
                    context + ": repeat out of range");
            job.L = controls[job.control_index];
            job.threads = phase == "single" ? 1 : throughput_threads[job.thread_index];
            require(distinct.emplace(job.scenario_index, job.L, job.threads, job.repeat).second,
                    context + ": duplicate job");
            jobs.push_back(job);
        }
        require(input.eof() && !jobs.empty(), "Plan must contain at least one valid job");
    }
};

struct SearchStats
{
    size_t count = 0;
    std::optional<double> ios;
};

class Backend
{
  public:
    virtual ~Backend() = default;
    virtual void configure(const Job &job, const Scenario &scenario) = 0;
    virtual SearchStats search(const uint8_t *query, uint32_t query_index, uint32_t worker_id,
                               uint32_t *ids, float *distances) = 0;
};

using BackendFactory = std::function<std::unique_ptr<Backend>(const Options &, uint32_t max_threads)>;

inline uint64_t checked_product(uint64_t left, uint64_t right, const std::string &context)
{
    require(right == 0 || left <= std::numeric_limits<uint64_t>::max() / right, context + ": size overflow");
    return left * right;
}

inline void check_native_host()
{
    const uint32_t marker = 1;
    require(*reinterpret_cast<const uint8_t *>(&marker) == 1 && sizeof(float) == 4 &&
                std::numeric_limits<float>::is_iec559,
            "Native matrices require a little-endian IEEE float32 host");
}

struct MatrixShape
{
    uint32_t rows = 0, cols = 0;
};

template <typename T> inline MatrixShape matrix_shape(const fs::path &path)
{
    check_native_host();
    std::ifstream input(path, std::ios::binary);
    require(input.is_open(), "Cannot open matrix: " + path.string());
    MatrixShape shape;
    input.read(reinterpret_cast<char *>(&shape.rows), 4);
    input.read(reinterpret_cast<char *>(&shape.cols), 4);
    require(input && shape.rows && shape.cols, "Invalid native matrix header: " + path.string());
    const uint64_t bytes = checked_product(checked_product(shape.rows, shape.cols, path.string()), sizeof(T), path.string());
    require(bytes <= std::numeric_limits<uint64_t>::max() - 8 && fs::file_size(path) == bytes + 8,
            "Native matrix extent differs from its header: " + path.string());
    return shape;
}

template <typename T>
inline std::vector<T> read_matrix(const fs::path &path, uint32_t rows, uint32_t cols)
{
    const auto shape = matrix_shape<T>(path);
    require(shape.rows == rows && shape.cols == cols, "Unexpected matrix shape: " + path.string());
    const uint64_t count = checked_product(rows, cols, path.string());
    require(count <= std::vector<T>().max_size(), "Matrix is too large for resident buffer");
    std::vector<T> result(static_cast<size_t>(count));
    std::ifstream input(path, std::ios::binary);
    input.seekg(8);
    input.read(reinterpret_cast<char *>(result.data()), static_cast<std::streamsize>(count * sizeof(T)));
    require(static_cast<bool>(input), "Truncated matrix payload: " + path.string());
    return result;
}

class Attributes
{
    const Options &options_;
    void *data_ = MAP_FAILED;
    size_t bytes_ = 0;

  public:
    explicit Attributes(const Options &options) : options_(options)
    {
        check_native_host();
        const uint64_t bytes = checked_product(checked_product(options.vector_count, options.attribute_columns,
                                                               "Attributes"), sizeof(uint32_t), "Attributes");
        require(bytes <= std::numeric_limits<size_t>::max(), "Attribute mapping exceeds address space");
        bytes_ = static_cast<size_t>(bytes);
        const int fd = ::open(options.attributes.c_str(), O_RDONLY | O_CLOEXEC);
        require(fd >= 0, "Cannot open headerless attributes: " + options.attributes.string());
        struct stat st{};
        if (::fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size < 0 || static_cast<uint64_t>(st.st_size) != bytes)
        {
            ::close(fd);
            throw std::runtime_error("Attributes must be HEADERLESS uint32 with exactly VectorCount*AttributeColumns*4 bytes");
        }
        data_ = ::mmap(nullptr, bytes_, PROT_READ, MAP_PRIVATE, fd, 0);
        const int saved_errno = errno;
        ::close(fd);
        require(data_ != MAP_FAILED, "Cannot mmap attributes: " + std::string(std::strerror(saved_errno)));
    }

    Attributes(const Attributes &) = delete;
    Attributes &operator=(const Attributes &) = delete;
    ~Attributes()
    {
        if (data_ != MAP_FAILED)
            ::munmap(data_, bytes_);
    }

    bool matches(uint32_t id, const Scenario &scenario) const
    {
        if (id >= options_.vector_count)
            return false;
        if (scenario.kind == ScenarioKind::unfilter)
            return true;
        const auto *row = static_cast<const uint32_t *>(data_) + static_cast<size_t>(id) * options_.attribute_columns;
        const uint32_t tag = row[options_.categorical_column];
        if (scenario.kind == ScenarioKind::categorical)
            return tag == scenario.tag;
        return tag == scenario.rare_tag ||
               (tag == scenario.regular_tag && row[options_.numeric_column] <= scenario.upper_inclusive);
    }
};

struct Inputs
{
    Attributes attributes;
    std::vector<uint8_t> queries;
    std::vector<std::vector<uint32_t>> truths;

    explicit Inputs(const Options &options) : attributes(options), truths(options.scenarios.size())
    {
        const auto base = matrix_shape<uint8_t>(options.vectors);
        require(base.rows == options.vector_count && base.cols == options.dimension, "Dataset.Vectors shape mismatch");
        const auto original = matrix_shape<uint8_t>(options.queries);
        require(original.cols == options.dimension && original.rows >= options.query_count, "Dataset.Queries shape mismatch");
        queries = read_matrix<uint8_t>(options.prepared_directory / "query.u8bin", options.query_count, options.dimension);
        std::ifstream source(options.queries, std::ios::binary);
        source.seekg(8);
        std::array<char, 65536> buffer{};
        size_t offset = 0;
        while (offset < queries.size())
        {
            const size_t count = std::min(buffer.size(), queries.size() - offset);
            source.read(buffer.data(), static_cast<std::streamsize>(count));
            require(source && std::memcmp(buffer.data(), queries.data() + offset, count) == 0,
                    "Prepared query.u8bin must equal the EXACT FIRST QueryCount original vectors");
            offset += count;
        }
        std::set<size_t> needed;
        for (const Job &job : options.jobs)
            needed.insert(job.scenario_index);
        for (size_t index : needed)
        {
            const auto &scenario = options.scenarios[index];
            auto &truth = truths[index];
            truth = read_matrix<uint32_t>(options.prepared_directory / ("gt_" + scenario.name + ".u32bin"),
                                         options.query_count, options.top_k);
            for (uint32_t q = 0; q < options.query_count; ++q)
                for (uint32_t k = 0; k < options.top_k; ++k)
                {
                    const size_t offset = static_cast<size_t>(q) * options.top_k;
                    require(attributes.matches(truth[offset + k], scenario),
                            "Ground truth contains out-of-range/nonmatching ID: " + scenario.name);
                    require(std::find(truth.begin() + offset, truth.begin() + offset + k, truth[offset + k]) ==
                                truth.begin() + offset + k,
                            "Ground truth contains duplicate ID: " + scenario.name);
                }
        }
    }
};

inline std::string json_string(const std::string &value)
{
    std::ostringstream output;
    output << '"';
    for (unsigned char c : value)
    {
        if (c == '"' || c == '\\')
            output << '\\' << c;
        else if (c < 32)
            output << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<unsigned>(c) << std::dec;
        else
            output << c;
    }
    output << '"';
    return output.str();
}

class ExclusiveFile
{
    int fd_ = -1;
    fs::path path_;

  public:
    explicit ExclusiveFile(fs::path path) : path_(std::move(path))
    {
        fd_ = ::open(path_.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0644);
        require(fd_ >= 0, "Cannot create no-clobber output " + path_.string() + ": " + std::strerror(errno));
    }
    ExclusiveFile(const ExclusiveFile &) = delete;
    ExclusiveFile &operator=(const ExclusiveFile &) = delete;
    ~ExclusiveFile()
    {
        if (fd_ >= 0)
            ::close(fd_);
    }
    void write(const void *data, size_t bytes)
    {
        const auto *cursor = static_cast<const char *>(data);
        while (bytes)
        {
            const ssize_t count = ::write(fd_, cursor, std::min(bytes, static_cast<size_t>(1U << 30)));
            if (count < 0 && errno == EINTR)
                continue;
            require(count > 0, "Cannot write output: " + path_.string());
            cursor += count;
            bytes -= static_cast<size_t>(count);
        }
    }
    void finish()
    {
        require(::fsync(fd_) == 0, "Cannot flush output: " + path_.string());
        const int fd = fd_;
        fd_ = -1;
        require(::close(fd) == 0, "Cannot close output: " + path_.string());
    }
};

inline void write_text(const fs::path &path, const std::string &text)
{
    ExclusiveFile file(path);
    file.write(text.data(), text.size());
    file.finish();
}

template <typename T>
inline void write_matrix(const fs::path &path, const std::vector<T> &values, uint32_t rows, uint32_t cols)
{
    require(values.size() == checked_product(rows, cols, "Output"), "Output matrix shape mismatch");
    ExclusiveFile file(path);
    file.write(&rows, 4);
    file.write(&cols, 4);
    file.write(values.data(), values.size() * sizeof(T));
    file.finish();
}

// Nearest-rank quantiles use upper edges of logarithmic bins, 256 per octave.
// For >= 2^-10 us, overestimation is < 2^(1/256)-1 (<0.272%); smaller
// observations share a <1ns bucket. Storage is fixed at 15,361 uint64 counters.
class LatencyHistogram
{
    static constexpr int minimum_exponent = -10, maximum_exponent = 50, bins_per_octave = 256;
    std::array<uint64_t, (maximum_exponent - minimum_exponent) * bins_per_octave + 1> bins_{};
    uint64_t count_ = 0;

  public:
    static std::string method()
    {
        return "nearest-rank upper-edge log2 histogram; 256 bins/octave; relative bin width <0.272%; sub-1ns bucket";
    }
    void add(double microseconds)
    {
        require(std::isfinite(microseconds) && microseconds >= 0 &&
                    microseconds < std::exp2(maximum_exponent), "Latency outside bounded histogram range");
        size_t index = 0;
        if (microseconds >= std::exp2(minimum_exponent))
            index = 1 + static_cast<size_t>(std::floor((std::log2(microseconds) - minimum_exponent) * bins_per_octave));
        ++bins_.at(index);
        ++count_;
    }
    double quantile(double probability) const
    {
        if (!count_)
            return 0;
        const uint64_t rank = static_cast<uint64_t>(std::ceil(static_cast<long double>(count_) * probability));
        uint64_t total = 0;
        for (size_t index = 0; index < bins_.size(); ++index)
        {
            total += bins_[index];
            if (total >= rank)
                return std::exp2(minimum_exponent + static_cast<double>(index) / bins_per_octave);
        }
        throw std::runtime_error("Histogram count mismatch");
    }
};

struct Call
{
    SearchStats stats;
    double latency_us = 0;
    bool completed = false;
    std::exception_ptr error;
};

struct Cohort
{
    std::vector<uint32_t> ids;
    std::vector<float> distances;
    std::vector<Call> calls;
    Cohort(uint32_t rows, uint32_t cols)
        : ids(static_cast<size_t>(checked_product(rows, cols, "Cohort")), UINT32_MAX),
          distances(ids.size(), std::numeric_limits<float>::quiet_NaN()), calls(rows)
    {
    }
};

inline void batch(Backend &backend, const Options &options, const Inputs &inputs, const Job &job,
                  uint32_t count, Cohort &cohort)
{
#pragma omp parallel for schedule(dynamic, 1) num_threads(job.threads)
    for (int64_t i = 0; i < static_cast<int64_t>(count); ++i)
    {
        const auto query = static_cast<uint32_t>(i);
        const size_t offset = static_cast<size_t>(query) * options.top_k;
        Call &call = cohort.calls[query];
        call = Call{};
        std::fill_n(cohort.ids.data() + offset, options.top_k, UINT32_MAX);
        std::fill_n(cohort.distances.data() + offset, options.top_k, std::numeric_limits<float>::quiet_NaN());
        try
        {
            const auto start = Clock::now();
            call.stats = backend.search(inputs.queries.data() + static_cast<size_t>(query) * options.dimension,
                                        query, static_cast<uint32_t>(omp_get_thread_num()),
                                        cohort.ids.data() + offset, cohort.distances.data() + offset);
            call.latency_us = std::chrono::duration<double, std::micro>(Clock::now() - start).count();
            call.completed = true;
        }
        catch (...)
        {
            // Exception objects and output slots are query-owned, not shared.
            call.error = std::current_exception();
        }
    }
}

struct Totals
{
    uint64_t queries = 0, hits = 0, invalid_queries = 0, invalid_ids = 0, duplicate_ids = 0;
    uint64_t nonmatching_ids = 0, nonfinite_distances = 0, underfilled_queries = 0, query_exceptions = 0;
    uint64_t io_observations = 0;
    long double latency_us = 0, ios = 0;
    LatencyHistogram histogram;

    void add(const Options &options, const Inputs &inputs, const Job &job, uint32_t count, const Cohort &cohort)
    {
        const Scenario &scenario = options.scenarios[job.scenario_index];
        const auto &truth = inputs.truths[job.scenario_index];
        require(queries <= UINT64_MAX - count, "Measured query counter overflow");
        for (uint32_t q = 0; q < count; ++q)
        {
            const auto &call = cohort.calls[q];
            bool invalid = !call.completed;
            query_exceptions += static_cast<bool>(call.error);
            if (call.completed)
            {
                ++queries;
                latency_us += call.latency_us;
                histogram.add(call.latency_us);
                if (call.stats.ios)
                {
                    if (!std::isfinite(*call.stats.ios) || *call.stats.ios < 0)
                        invalid = true;
                    else
                    {
                        ios += *call.stats.ios;
                        ++io_observations;
                    }
                }
                if (call.stats.count != options.top_k)
                {
                    ++underfilled_queries;
                    invalid = true;
                }
            }
            uint64_t query_hits = 0;
            const size_t offset = static_cast<size_t>(q) * options.top_k;
            for (uint32_t k = 0; k < options.top_k; ++k)
            {
                const uint32_t id = cohort.ids[offset + k];
                const bool bad_id = id >= options.vector_count;
                const bool duplicate = std::find(cohort.ids.begin() + offset, cohort.ids.begin() + offset + k, id) !=
                                       cohort.ids.begin() + offset + k;
                const bool nonmatching = !bad_id && !inputs.attributes.matches(id, scenario);
                const bool nonfinite = !std::isfinite(cohort.distances[offset + k]);
                invalid_ids += bad_id;
                duplicate_ids += duplicate;
                nonmatching_ids += nonmatching;
                nonfinite_distances += nonfinite;
                invalid = invalid || bad_id || duplicate || nonmatching || nonfinite;
                if (!bad_id && !duplicate && !nonmatching &&
                    std::find(truth.begin() + offset, truth.begin() + offset + options.top_k, id) !=
                        truth.begin() + offset + options.top_k)
                    ++query_hits;
            }
            invalid_queries += invalid;
            if (!invalid)
                hits += query_hits;
        }
    }
};

inline std::string stem(const Options &options, const Job &job)
{
    return options.scenarios[job.scenario_index].name + ".L" + std::to_string(job.L) + ".T" +
           std::to_string(job.threads) + ".r" + std::to_string(job.repeat);
}

inline void save_pair(const fs::path &directory, const std::string &name, const Cohort &cohort,
                      uint32_t rows, uint32_t cols)
{
    write_matrix(directory / (name + ".ids.u32bin"), cohort.ids, rows, cols);
    write_matrix(directory / (name + ".distances.f32bin"), cohort.distances, rows, cols);
}

inline void rethrow_query_error(const Cohort &cohort, uint32_t count)
{
    for (uint32_t q = 0; q < count; ++q)
        if (cohort.calls[q].error)
            std::rethrow_exception(cohort.calls[q].error);
}

inline void check_team(uint32_t threads)
{
    int actual = 0;
#pragma omp parallel num_threads(threads)
    {
#pragma omp single
        actual = omp_get_num_threads();
    }
    require(actual == static_cast<int>(threads), "OpenMP supplied " + std::to_string(actual) +
                " threads; profile plan requires " + std::to_string(threads) + " (no hidden reductions allowed)");
}

inline void reject_output_symlinks(const fs::path &path)
{
    fs::path current;
    for (const auto &component : path)
    {
        current /= component;
        std::error_code error;
        const auto status = fs::symlink_status(current, error);
        if (error == std::errc::no_such_file_or_directory)
            break;
        require(!error, "Cannot inspect native output path: " + current.string());
        require(!fs::is_symlink(status), "Symlinks are forbidden in native output paths: " + current.string());
    }
}

inline void run_job(Backend &backend, const Options &options, const Inputs &inputs, const Job &job,
                    const fs::path &directory)
{
    omp_set_num_threads(static_cast<int>(job.threads));
    check_team(job.threads);
    backend.configure(job, options.scenarios[job.scenario_index]);
    Cohort current(options.query_count, options.top_k), first(options.query_count, options.top_k);
    uint32_t warmed = 0;
    while (warmed < options.warmup_queries)
    {
        const uint32_t count = std::min(options.query_count, options.warmup_queries - warmed);
        batch(backend, options, inputs, job, count, current);
        Totals warmup;
        warmup.add(options, inputs, job, count, current);
        if (warmup.invalid_queries)
        {
            // Preserve the complete fixed-size buffer, including sentinel slots
            // if the last warmup batch was shorter than a cohort.
            save_pair(directory, stem(options, job) + ".failed_warmup", current, options.query_count, options.top_k);
            rethrow_query_error(current, count);
            throw std::runtime_error("Native warmup produced invalid/underfilled results for " + stem(options, job));
        }
        warmed += count;
    }

    Totals total;
    uint64_t passes = 0;
    double elapsed = 0;
    double first_recall = 0, last_recall = 0, recall_min_batch = 1;
    const auto start = Clock::now();
    do
    {
        batch(backend, options, inputs, job, options.query_count, current);
        if (options.phase == "single")
            elapsed = std::chrono::duration<double>(Clock::now() - start).count();
        const uint64_t previous_hits = total.hits;
        total.add(options, inputs, job, options.query_count, current);
        last_recall = static_cast<double>(total.hits - previous_hits) / options.query_count / options.top_k;
        recall_min_batch = std::min(recall_min_batch, last_recall);
        if (!passes)
        {
            first.ids = current.ids;
            first.distances = current.distances;
            first_recall = last_recall;
        }
        ++passes;
        if (options.phase == "throughput")
            elapsed = std::chrono::duration<double>(Clock::now() - start).count();
    } while (!total.invalid_queries && options.phase == "throughput" && elapsed < options.minimum_seconds);
    require(elapsed > 0, "Nonpositive timed interval");

    const std::string name = stem(options, job);
    save_pair(directory, name + ".first", first, options.query_count, options.top_k);
    save_pair(directory, name + ".last", current, options.query_count, options.top_k);
    std::ostringstream result;
    result.imbue(std::locale::classic());
    result << std::setprecision(17)
           << "{\"schema_version\":1,\"engine\":" << json_string(options.engine)
           << ",\"phase\":" << json_string(options.phase)
           << ",\"scenario\":" << json_string(options.scenarios[job.scenario_index].name)
           << ",\"L\":" << job.L << ",\"threads\":" << job.threads << ",\"repeat\":" << job.repeat
           << ",\"queries\":" << total.queries << ",\"cohort_queries\":" << options.query_count
           << ",\"passes\":" << passes << ",\"warmup_queries\":" << warmed
           << ",\"elapsed_seconds\":" << elapsed << ",\"qps\":" << total.queries / elapsed
           << ",\"recall\":" << (total.queries ? static_cast<double>(total.hits) / total.queries / options.top_k : 0)
           << ",\"first_recall\":" << first_recall << ",\"last_recall\":" << last_recall
           << ",\"recall_min_batch\":" << recall_min_batch
           << ",\"mean_latency_us\":" << (total.queries ? static_cast<double>(total.latency_us / total.queries) : 0)
           << ",\"p50_latency_us\":" << total.histogram.quantile(0.5)
           << ",\"p99_latency_us\":" << total.histogram.quantile(0.99)
           << ",\"mean_ios\":";
    if (total.queries && total.io_observations == total.queries)
        result << static_cast<double>(total.ios / total.queries);
    else
        result << "null";
    result << ",\"invalid_queries\":" << total.invalid_queries
           << ",\"invalid_ids\":" << total.invalid_ids << ",\"duplicate_ids\":" << total.duplicate_ids
           << ",\"nonmatching_ids\":" << total.nonmatching_ids << ",\"nonfinite_distances\":" << total.nonfinite_distances
           << ",\"underfilled_queries\":" << total.underfilled_queries << ",\"query_exceptions\":" << total.query_exceptions
           << ",\"first_ids\":" << json_string((directory / (name + ".first.ids.u32bin")).string())
           << ",\"first_distances\":" << json_string((directory / (name + ".first.distances.f32bin")).string())
           << ",\"last_ids\":" << json_string((directory / (name + ".last.ids.u32bin")).string())
           << ",\"last_distances\":" << json_string((directory / (name + ".last.distances.f32bin")).string())
           << ",\"latency_quantile_method\":" << json_string(LatencyHistogram::method()) << "}";
    write_text(directory / (name + ".result.json"), result.str() + "\n");
    std::cout << "THREEWAY_RESULT " << result.str() << std::endl;
    if (total.invalid_queries)
    {
        rethrow_query_error(current, options.query_count);
        throw std::runtime_error("Native measured output invalid/underfilled for " + name +
                                 "; saved first/last evidence, no result repair");
    }
}

inline int run_main(int argc, char **argv, const std::string &engine, const BackendFactory &factory)
{
    fs::path created_directory;
    try
    {
        reject_environment_overrides();
        require(argc == 3, "Usage: <binary> <profile.ini> <single|throughput>");
        const Options options(argv[1], argv[2], engine);
        reject_output_symlinks(options.native_directory());
        require(!fs::exists(options.native_directory()), "Native output directory must be fresh: " +
                                                            options.native_directory().string());
        const Inputs inputs(options);
        omp_set_dynamic(0);
        omp_set_max_active_levels(1);
        omp_set_num_threads(static_cast<int>(options.max_threads()));
        check_team(options.max_threads());
        const fs::path directory = options.native_directory();
        fs::create_directories(directory.parent_path());
        require(::mkdir(directory.c_str(), 0755) == 0, "Cannot create fresh native output directory: " + directory.string());
        created_directory = directory;
        const auto load_start = Clock::now();
        std::unique_ptr<Backend> backend = factory(options, options.max_threads());
        require(static_cast<bool>(backend), "Backend factory returned null");
        std::ostringstream ready;
        ready << std::setprecision(17) << "{\"schema_version\":1,\"engine\":" << json_string(engine)
              << ",\"phase\":" << json_string(options.phase) << ",\"job_count\":" << options.jobs.size()
              << ",\"max_threads\":" << options.max_threads() << ",\"pid\":" << ::getpid()
              << ",\"load_seconds\":" << std::chrono::duration<double>(Clock::now() - load_start).count() << "}";
        write_text(directory / "ready.json", ready.str() + "\n");
        std::cout << "THREEWAY_READY " << ready.str() << std::endl;
        for (const Job &job : options.jobs)
            run_job(*backend, options, inputs, job, directory);
        const std::string completion = "{\"schema_version\":1,\"engine\":" + json_string(engine) +
            ",\"phase\":" + json_string(options.phase) + ",\"status\":\"completed\",\"job_count\":" +
            std::to_string(options.jobs.size()) + ",\"completed_jobs\":" + std::to_string(options.jobs.size()) + "}";
        write_text(directory / "completion.json", completion + "\n");
        std::cout << "THREEWAY_COMPLETE " << completion << std::endl;
        return 0;
    }
    catch (const std::exception &error)
    {
        const std::string failure = "{\"schema_version\":1,\"engine\":" + json_string(engine) +
                                    ",\"status\":\"failed\",\"error\":" + json_string(error.what()) + "}";
        if (!created_directory.empty())
        {
            try
            {
                write_text(created_directory / "failure.json", failure + "\n");
            }
            catch (const std::exception &save_error)
            {
                std::cerr << "Cannot save failure evidence: " << save_error.what() << '\n';
            }
        }
        std::cerr << "THREEWAY_FAILURE " << failure << std::endl;
        return 1;
    }
    catch (...)
    {
        const std::string failure = "{\"schema_version\":1,\"engine\":" + json_string(engine) +
                                    ",\"status\":\"failed\",\"error\":\"non-standard native exception\"}";
        if (!created_directory.empty())
        {
            try
            {
                write_text(created_directory / "failure.json", failure + "\n");
            }
            catch (...)
            {
                std::cerr << "Cannot save non-standard exception evidence\n";
            }
        }
        std::cerr << "THREEWAY_FAILURE " << failure << std::endl;
        return 1;
    }
}
} // namespace threeway
