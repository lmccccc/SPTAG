#include "benchmark.h"
#include "linux_aligned_file_reader.h"
#include "pq_flash_index.h"

namespace
{
class DiskANNORBackend final : public threeway::Backend
{
    const threeway::Options &options_;
    std::shared_ptr<AlignedFileReader> reader_;
    std::unique_ptr<diskann::PQFlashIndex<uint8_t, uint32_t>> index_;
    std::vector<std::vector<uint64_t>> ids_;
    std::vector<uint32_t> labels_;
    uint32_t beam_ = 0, l_ = 0;

  public:
    DiskANNORBackend(const threeway::Options &options, uint32_t threads) : options_(options)
    {
        options.ini.only("DiskANN", {"indexprefix", "lsweep", "beamwidth", "cachenodes", "aioeventsperthread"});
        threeway::require(options.ini.uint_value("DiskANN", "CacheNodes") == 0 &&
                              options.ini.uint_value("DiskANN", "AIOEventsPerThread") == 1024,
                          "OR benchmark requires cache_nodes=0 and native AIO depth 1024");
        for (const auto &job : options.jobs)
        {
            const auto &scenario = options.scenarios.at(job.scenario_index);
            threeway::require(scenario.kind == threeway::ScenarioKind::categorical_or &&
                                  job.L >= std::max<size_t>(options.top_k, scenario.tags.size()),
                              "OR search requires categorical_or and L >= max(K, distinct labels)");
        }
        uint64_t used = 0, maximum = 0;
        threeway::require(bool(std::ifstream("/proc/sys/fs/aio-nr") >> used) &&
                              bool(std::ifstream("/proc/sys/fs/aio-max-nr") >> maximum),
                          "Cannot read current host AIO allowance");
        const uint64_t needed = uint64_t(threads) * 1024;
        threeway::require(used <= maximum && options.aio_reserve <= maximum - used &&
                              needed <= maximum - used - options.aio_reserve,
                          "Insufficient AIO headroom; no thread reduction or global sysctl change");
        std::cout << "THREEWAY_AIO {\"used\":" << used << ",\"maximum\":" << maximum
                  << ",\"requested\":" << needed << ",\"reserve\":" << options.aio_reserve << "}" << std::endl;
        beam_ = threeway::u32(options.ini.uint_value("DiskANN", "BeamWidth"), "DiskANN.BeamWidth");
        reader_ = std::make_shared<LinuxAlignedFileReader>();
        index_ = std::make_unique<diskann::PQFlashIndex<uint8_t, uint32_t>>(reader_, diskann::Metric::L2);
        const auto prefix = options.resolve("DiskANN", "IndexPrefix").string();
        threeway::require(index_->load(threads, prefix.c_str()) == 0, "DiskANN-OR native load failed");
        threeway::require(index_->get_data_dim() == options.dimension &&
                              index_->get_num_points() == options.vector_count,
                          "DiskANN-OR loaded index shape mismatch");
        ids_.assign(threads, std::vector<uint64_t>(options.top_k, UINT64_MAX));
    }

    void configure(const threeway::Job &job, const threeway::Scenario &scenario) override
    {
        l_ = job.L;
        labels_.clear();
        for (const auto tag : scenario.tags)
            labels_.push_back(index_->get_converted_label(std::to_string(tag)));
        threeway::require(std::set<uint32_t>(labels_.begin(), labels_.end()).size() == labels_.size(),
                          "Original camera labels alias after native conversion");
    }

    threeway::SearchStats search(const uint8_t *query, uint32_t, uint32_t worker,
                                 uint32_t *ids, float *distances) override
    {
        auto &native_ids = ids_.at(worker);
        std::fill(native_ids.begin(), native_ids.end(), UINT64_MAX);
        diskann::QueryStats stats{};
        const auto count = index_->cached_beam_search_or(query, options_.top_k, l_, native_ids.data(),
                                                        distances, beam_, labels_, UINT32_MAX, &stats);
        threeway::require(count <= options_.top_k, "DiskANN-OR returned count exceeds K");
        for (size_t k = 0; k < count; ++k)
        {
            threeway::require(native_ids[k] < options_.vector_count, "DiskANN-OR returned invalid original ID");
            ids[k] = static_cast<uint32_t>(native_ids[k]);
        }
        return {static_cast<size_t>(count), static_cast<double>(stats.n_ios)};
    }
};
}

int main(int argc, char **argv)
{
    return threeway::run_main(argc, argv, "Filtered_DiskANN", [](const threeway::Options &options, uint32_t threads) {
        return std::make_unique<DiskANNORBackend>(options, threads);
    });
}
