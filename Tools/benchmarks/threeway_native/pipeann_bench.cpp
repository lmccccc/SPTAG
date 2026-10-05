#include "benchmark.h"

#include "filter/dsl_compiler.h"
#include "linux_aligned_file_reader.h"
#include "nbr/nbr.h"
#include "ssd_index.h"

#if !defined(READ_ONLY_TESTS) || !defined(NO_MAPPING) || !defined(USE_URING) || !defined(USE_TCMALLOC)
#error PipeANN requires the frozen READ_ONLY_TESTS/NO_MAPPING/uring/tcmalloc build
#endif
#ifndef THREEWAY_PIPEANN_SOURCE
#error Build with build_pipeann_client.py to authenticate the frozen PipeANN library
#endif

namespace
{
namespace fs = std::filesystem;
using threeway::require;

void check_executable_copy(const fs::path &source)
{
    const fs::path executing = "/proc/self/exe";
    require(fs::is_regular_file(source), "PipeANN.BenchmarkBinary is not a regular file");
    if (fs::equivalent(source, executing))
        return;
    require(fs::file_size(source) == fs::file_size(executing),
            "PipeANN.BenchmarkBinary does not match this executing client");
    std::ifstream expected(source, std::ios::binary), actual(executing, std::ios::binary);
    require(expected.is_open() && actual.is_open(), "Cannot verify the PipeANN runtime executable copy");
    std::array<char, 65536> left{}, right{};
    while (expected)
    {
        expected.read(left.data(), left.size());
        actual.read(right.data(), right.size());
        require(expected.gcount() == actual.gcount() &&
                    std::memcmp(left.data(), right.data(), static_cast<size_t>(expected.gcount())) == 0,
                "PipeANN.BenchmarkBinary does not match this executing client");
    }
    require(expected.eof() && actual.eof() && !expected.bad() && !actual.bad(),
            "Cannot completely read the PipeANN runtime executable copy");
}

void check_binding(const fs::path &path, const pipeann::Attribute &values, uint32_t queries, bool label)
{
    const uint64_t nnz = static_cast<uint64_t>(queries) * values.size();
    require(fs::is_regular_file(path) && fs::file_size(path) == 24 + (uint64_t(queries) + 1) * 8 + nnz * 8,
            "Missing or incorrectly sized PipeANN binding: " + path.string());
    std::ifstream input(path, std::ios::binary);
    int64_t header[3]{};
    input.read(reinterpret_cast<char *>(header), sizeof(header));
    require(header[0] == queries && header[1] == (label ? 201 : int64_t(values.size())) &&
                header[2] == static_cast<int64_t>(nnz), "PipeANN binding header mismatch: " + path.string());
    for (uint64_t row = 0; row <= queries; ++row)
    {
        int64_t offset = -1;
        input.read(reinterpret_cast<char *>(&offset), sizeof(offset));
        require(offset == static_cast<int64_t>(row * values.size()),
                "PipeANN binding row offsets mismatch: " + path.string());
    }
    for (uint64_t item = 0; item < nnz; ++item)
    {
        int32_t index = -1;
        input.read(reinterpret_cast<char *>(&index), sizeof(index));
        require(index == static_cast<int64_t>(label ? values[item % values.size()] : item % values.size()),
                "PipeANN binding original label/column mismatch: " + path.string());
    }
    for (uint64_t item = 0; item < nnz; ++item)
    {
        const uint32_t expected = label ? 1 : values[item % values.size()];
        float value = -1;
        input.read(reinterpret_cast<char *>(&value), sizeof(value));
        require(static_cast<double>(value) == expected,
                "PipeANN binding value/range endpoint mismatch: " + path.string());
    }
    require(static_cast<bool>(input), "Cannot read PipeANN binding: " + path.string());
}

void check_filter_config(const threeway::Options &options, const threeway::Scenario &scenario)
{
    const auto path = options.resolve("Scenario." + scenario.name, "FilterConfig");
    std::ifstream input(path);
    require(input.is_open(), "Cannot open PipeANN FilterConfig: " + path.string());
    picojson::value actual;
    const auto error = picojson::parse(actual, input);
    require(error.empty(), "Invalid PipeANN FilterConfig: " + error);
    const auto store = [](const char *name, uint32_t key, const char *type, const char *file) {
        return picojson::value(picojson::object{
            {"name", picojson::value(name)}, {"key", picojson::value(double(key))},
            {"type", picojson::value(type)}, {"file", picojson::value(file)}});
    };
    picojson::array stores{store("tag", options.categorical_column, "label", "base.label.0")};
    picojson::object bindings;
    std::string expression;
    if (scenario.kind == threeway::ScenarioKind::categorical)
    {
        require(scenario.tag <= 200, "PipeANN categorical bindings must preserve original labels 0..200");
        expression = "tag = $$tag";
        bindings["tag"] = picojson::value(scenario.name + ".spmat");
        check_binding(scenario.name + ".spmat", {scenario.tag}, options.query_count, true);
    }
    else
    {
        require(scenario.rare_tag <= 200 && scenario.regular_tag <= 200 && scenario.upper_inclusive < UINT32_MAX,
                "PipeANN DNF must use original labels and a representable exclusive range endpoint");
        stores.push_back(store("num", options.numeric_column, "range", "base.label.1"));
        expression = "tag = $$rare OR (tag = $$regular AND num = $$range)";
        bindings = {{"rare", picojson::value("mixed_dnf_rare.spmat")},
                    {"regular", picojson::value("mixed_dnf_regular.spmat")},
                    {"range", picojson::value("mixed_dnf_range.spmat")}};
        check_binding("mixed_dnf_rare.spmat", {scenario.rare_tag}, options.query_count, true);
        check_binding("mixed_dnf_regular.spmat", {scenario.regular_tag}, options.query_count, true);
        check_binding("mixed_dnf_range.spmat", {0, scenario.upper_inclusive + 1}, options.query_count, false);
    }
    const picojson::value expected(picojson::object{
        {"attr_indexes", picojson::value(stores)}, {"filter", picojson::value(expression)},
        {"bindings", picojson::value(bindings)}});
    require(actual == expected, "PipeANN FilterConfig disagrees with the INI predicate: " + path.string());
}

void check_sidecars(const threeway::Options &options, const std::string &prefix, bool numeric)
{
    for (const auto *suffix : {".label.0", ".label.0.filter"})
        require(fs::is_regular_file("base" + std::string(suffix)) &&
                    fs::equivalent("base" + std::string(suffix), prefix + suffix),
                "Prepared PipeANN label sidecar does not belong to IndexPrefix: " + std::string(suffix));
    require(threeway::matrix_shape<uint8_t>("base.label.0.filter").rows == options.vector_count,
            "PipeANN label Bloom sidecar row count mismatch");
    if (!numeric)
        return;
    for (const auto *suffix : {".label.1", ".label.1.quantize"})
        require(fs::is_regular_file("base" + std::string(suffix)) &&
                    fs::equivalent("base" + std::string(suffix), prefix + suffix),
                "Prepared PipeANN numeric sidecar does not belong to IndexPrefix: " + std::string(suffix));
    std::ifstream input("base.label.1.quantize", std::ios::binary);
    uint32_t buckets = 0, rows = 0;
    input.read(reinterpret_cast<char *>(&buckets), sizeof(buckets));
    require(input && buckets > 0 && buckets <= 256, "Invalid PipeANN numeric quantization header");
    input.seekg((uint64_t(buckets) + 1) * sizeof(uint32_t), std::ios::cur);
    input.read(reinterpret_cast<char *>(&rows), sizeof(rows));
    require(input && rows == options.vector_count &&
                fs::file_size("base.label.1.quantize") == 8 + (uint64_t(buckets) + 1) * 4 + rows,
            "PipeANN numeric quantization sidecar row count/extent mismatch");
}

struct Filter
{
    std::map<uint32_t, std::unique_ptr<pipeann::AttrIndex>> stores;
    std::unique_ptr<pipeann::Selector> selector;
    std::vector<pipeann::Attributes> queries;

    Filter(const fs::path &path, uint64_t vectors, uint32_t query_count)
    {
        auto loaded = pipeann::dsl::load_filter_from_json(path.string(), vectors);
        selector.reset(std::get<0>(loaded));
        queries = std::move(std::get<1>(loaded));
        for (auto &store : std::get<2>(loaded))
            stores.emplace(store.first, std::unique_ptr<pipeann::AttrIndex>(store.second));
        require(selector && queries.size() == query_count, "PipeANN filter binding count mismatch");
        for (const auto &store : stores)
            require((::fcntl(store.second->fd, F_GETFL) & O_ACCMODE) == O_RDONLY,
                    "PipeANN attribute descriptor is not read-only");
    }
};

class PipeANNBackend final : public threeway::Backend
{
    const threeway::Options &options_;
    std::shared_ptr<AlignedFileReader> reader_;
    std::unique_ptr<pipeann::AbstractNeighbor<uint8_t>> neighbor_;
    std::unique_ptr<pipeann::SSDIndex<uint8_t>> index_;
    std::unique_ptr<Filter> filter_;
    std::string filter_scenario_;
    uint32_t width_ = 0, memory_l_ = 0, current_l_ = 0, max_threads_ = 0;
    bool filtered_ = false;

  public:
    PipeANNBackend(const threeway::Options &options, uint32_t max_threads)
        : options_(options), max_threads_(max_threads)
    {
        options.ini.only("PipeANN", {"indexprefix", "benchmarkbinary", "sourcedirectory", "lsweep", "pipelinewidth",
                                     "searchmode", "unfilteredmemoryl", "filteredmemoryl", "filtermode"});
        require(fs::equivalent(options.resolve("PipeANN", "SourceDirectory"), THREEWAY_PIPEANN_SOURCE),
                "PipeANN.SourceDirectory differs from the authenticated frozen core");
        if (options.ini.has("PipeANN", "BenchmarkBinary"))
            check_executable_copy(options.resolve("PipeANN", "BenchmarkBinary"));
        width_ = threeway::u32(options.ini.uint_value("PipeANN", "PipelineWidth"), "PipeANN.PipelineWidth");
        memory_l_ = threeway::u32(options.ini.uint_value("PipeANN", "UnfilteredMemoryL"), "PipeANN.UnfilteredMemoryL");
        require(width_ == 32 && options.ini.uint_value("PipeANN", "SearchMode") == PIPE_SEARCH &&
                    memory_l_ == 10 && options.ini.uint_value("PipeANN", "FilteredMemoryL") == 0 &&
                    threeway::lower(options.ini.require("PipeANN", "FilterMode")) == "auto",
                "PipeANN baseline requires pipeline_width=32, search_mode=2, unfilter mem_L=10, filtered auto/mem_L=0");
        require(options.attribute_columns == 2 && options.categorical_column == 0 && options.numeric_column == 1,
                "PipeANN index attributes must retain the original categorical/numeric columns 0,1");
        fs::current_path(options.prepared_directory);
        const std::string prefix = options.resolve("PipeANN", "IndexPrefix").string();
        bool need_memory = false, need_filter = false, need_numeric = false;
        std::set<size_t> checked;
        for (const auto &job : options.jobs)
        {
            if (!checked.insert(job.scenario_index).second)
                continue;
            const auto &scenario = options.scenarios[job.scenario_index];
            need_memory |= scenario.kind == threeway::ScenarioKind::unfilter;
            need_filter |= scenario.kind != threeway::ScenarioKind::unfilter;
            need_numeric |= scenario.kind == threeway::ScenarioKind::mixed_dnf;
            if (scenario.kind != threeway::ScenarioKind::unfilter)
                check_filter_config(options, scenario);
        }
        require(fs::is_regular_file(prefix + "_disk.index") && fs::is_regular_file(prefix + "_pq_pivots.bin"),
                "PipeANN disk index or original PQ pivots are missing");
        require(threeway::matrix_shape<uint8_t>(prefix + "_pq_compressed.bin").rows == options.vector_count,
                "PipeANN PQ sidecar differs from the original vector count");
        pipeann::SSDIndexMetadata<uint8_t> metadata;
        metadata.load_from_disk_index(prefix + "_disk.index");
        require(metadata.npoints == options.vector_count && metadata.data_dim == options.dimension &&
                    (!need_filter || metadata.attr_size > 0), "PipeANN native index shape/attributes mismatch");
        if (need_filter)
            check_sidecars(options, prefix, need_numeric);
        uint64_t memory_entries = 0;
        if (need_memory)
        {
            const std::string memory = prefix + "_mem.index";
            require(fs::is_regular_file(memory) && fs::is_regular_file(memory + ".tags"),
                    "Missing matching PipeANN memory-entry index/tags; mem_L=0 fallback is forbidden");
            pipeann::SSDIndexMetadata<uint8_t> memory_metadata;
            memory_metadata.load_from_disk_index(memory);
            const auto tags = threeway::matrix_shape<uint32_t>(memory + ".tags");
            const double sampled_mean = static_cast<double>(options.vector_count) * 0.01;
            require(memory_metadata.npoints >= memory_l_ && memory_metadata.npoints <= options.vector_count &&
                        std::abs(static_cast<double>(memory_metadata.npoints) - sampled_mean) <=
                            std::max(1.0, 12 * std::sqrt(sampled_mean * 0.99)) &&
                        memory_metadata.data_dim == options.dimension && tags.rows == memory_metadata.npoints &&
                        memory_metadata.range > 0 && memory_metadata.range <= 32 &&
                        memory_metadata.attr_size == 0 && tags.cols == 1,
                    "PipeANN memory-entry metadata/tags must match the original 1%/R32 native sample");
            memory_entries = memory_metadata.npoints;
        }
        reader_ = std::make_shared<LinuxAlignedFileReader>();
        neighbor_.reset(pipeann::get_nbr_handler<uint8_t>(pipeann::Metric::L2, "pq"));
        pipeann::IndexBuildParameters parameters;
        parameters.max_nthreads = max_threads;
        index_ = std::make_unique<pipeann::SSDIndex<uint8_t>>(
            pipeann::Metric::L2, reader_, neighbor_.get(), true, &parameters);
        require(index_->load(prefix.c_str(), false) == 0, "Frozen PipeANN native index load failed");
        require((::fcntl(reader_->fd, F_GETFL) & O_ACCMODE) == O_RDONLY,
                "PipeANN SSD descriptor is not read-only");
        if (need_memory)
            index_->load_mem_index(prefix + "_mem.index");
        std::cout << "THREEWAY_PIPEANN {\"shared_index_loads\":1,\"max_nthreads\":" << max_threads
                  << ",\"memory_entries\":" << memory_entries << ",\"neighbor\":\"pq\",\"backend\":\"uring\","
                  << "\"allocator\":\"tcmalloc\",\"library_sha256\":\"" << THREEWAY_PIPEANN_LIBRARY_SHA256
                  << "\",\"io_counter\":\"native QueryStats.n_ios (vector SSD reads)\"}" << std::endl;
    }

    void configure(const threeway::Job &job, const threeway::Scenario &scenario) override
    {
        require(job.threads <= max_threads_ && job.L >= options_.top_k, "PipeANN job exceeds its resident plan");
        current_l_ = job.L;
        filtered_ = scenario.kind != threeway::ScenarioKind::unfilter;
        if (filtered_ && filter_scenario_ != scenario.name)
        {
            // Selectors borrow their attribute stores. Release the old cohort's
            // stores before loading another, never duplicate them per worker.
            filter_.reset();
            filter_ = std::make_unique<Filter>(options_.resolve("Scenario." + scenario.name, "FilterConfig"),
                                               options_.vector_count, options_.query_count);
            filter_scenario_ = scenario.name;
        }
    }

    threeway::SearchStats search(const uint8_t *query, uint32_t query_index, uint32_t worker_id,
                                 uint32_t *ids, float *distances) override
    {
        require(worker_id < max_threads_ && query_index < options_.query_count && current_l_ != 0,
                "PipeANN query outside the configured resident cohort");
        pipeann::QueryStats stats{};
        const size_t count = filtered_
            ? index_->spec_filter_search(query, options_.top_k, current_l_, filter_->selector.get(),
                                         filter_->queries[query_index], ids, distances, width_, &stats,
                                         nullptr, pipeann::N_FILTER_TYPES)
            : index_->pipe_search(query, options_.top_k, memory_l_, current_l_, ids, distances, width_, &stats);
        return {count, static_cast<double>(stats.n_ios)};
    }
};
} // namespace

int main(int argc, char **argv)
{
    return threeway::run_main(argc, argv, "PipeANN", [](const threeway::Options &options, uint32_t threads) {
        for (char **entry = ::environ; *entry; ++entry)
        {
            const std::string name(*entry, std::strchr(*entry, '='));
            require(name.rfind("PIPEANN_", 0) != 0 && name.rfind("OMP_", 0) != 0 &&
                        name.rfind("GOMP_", 0) != 0 && name.rfind("KMP_", 0) != 0,
                    "Runtime environment override is forbidden: " + name + "; use the benchmark INI");
        }
        return std::make_unique<PipeANNBackend>(options, threads);
    });
}
