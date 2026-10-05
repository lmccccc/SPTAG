#include "diskann_loader_policy.h"
#include "linux_aligned_file_reader.h"

namespace
{
namespace fs = std::filesystem;
using threeway_diskann::AdmissionGuard;
using threeway_diskann::AdmissionParameters;
using threeway_diskann::kernel_counter;

void check_aio(const threeway::Options &options, uint32_t threads, bool emit = true)
{
    const uint64_t events = options.ini.uint_value("DiskANN", "AIOEventsPerThread");
    threeway::require(events == 1024, "Original DiskANN requires AIOEventsPerThread=1024; queue limits are not tunable");
    const uint64_t maximum = kernel_counter("/proc/sys/fs/aio-max-nr");
    const uint64_t used = kernel_counter("/proc/sys/fs/aio-nr");
    const uint64_t needed = threeway::checked_product(threads, events, "DiskANN AIO reservation");
    threeway::require(used <= maximum && options.aio_reserve <= maximum - used &&
                          needed <= maximum - used - options.aio_reserve,
                      "Insufficient CURRENT HOST AIO headroom before native load: used=" + std::to_string(used) +
                          " max=" + std::to_string(maximum) + " requested=" + std::to_string(needed) +
                          " reserve=" + std::to_string(options.aio_reserve) +
                          "; re-plan explicitly, no hidden thread reduction or sysctl change");
    if (emit)
        std::cout << "THREEWAY_AIO {\"used\":" << used << ",\"maximum\":" << maximum
                  << ",\"requested\":" << needed << ",\"reserve\":" << options.aio_reserve << "}" << std::endl;
}

class DiskANNBackend final : public threeway::Backend
{
    const threeway::Options &options_;
    threeway_diskann::LoaderPolicy loader_policy_;
    std::shared_ptr<AlignedFileReader> reader_;
    std::unique_ptr<diskann::PQFlashIndex<uint8_t, uint32_t>> index_;
    std::vector<std::vector<uint64_t>> worker_ids_;
    std::map<uint32_t, uint32_t> labels_;
    uint32_t beam_ = 0, current_l_ = 0, current_label_ = 0;
    bool filtered_ = false, admitted_ = false;

  public:
    DiskANNBackend(const threeway::Options &options, uint32_t max_threads) : options_(options)
    {
        const AdmissionParameters parameters{options.phase, options.vector_count, options.dimension, options.top_k,
                                             options.ini.uint_value("DiskANN", "CacheNodes"),
                                             options.scenarios, options.jobs};
        AdmissionGuard guard(parameters, [&](const std::string &record) {
            threeway::write_text(options.native_directory() / "diskann-admission.json", record + "\n");
            std::cout << "THREEWAY_ADMISSION " << record << std::endl;
        });
        try
        {
            options.ini.only("DiskANN", {"indexprefix", "lsweep", "beamwidth", "cachenodes", "aioeventsperthread",
                                         "library", "sourcedirectory", "benchmarkbinary", "sourcerevision",
                                         "loaderpolicy"});
            loader_policy_.authenticate(options.ini.has("DiskANN", "LoaderPolicy")
                                            ? fs::path(options.ini.require("DiskANN", "LoaderPolicy"))
                                            : fs::path{});
            if (options.ini.has("DiskANN", "SourceRevision"))
                threeway::require(options.ini.require("DiskANN", "SourceRevision") == THREEWAY_DISKANN_REVISION,
                                  "DiskANN.SourceRevision differs from the pinned original library build");
            threeway::require(fs::equivalent(options.resolve("DiskANN", "Library"),
                                            THREEWAY_DISKANN_ORIGINAL_LIBRARY),
                              "DiskANN.Library differs from the original algorithm-base archive");
            threeway::require(fs::equivalent(options.resolve("DiskANN", "SourceDirectory"), THREEWAY_DISKANN_SOURCE),
                              "DiskANN.SourceDirectory differs from the original source used to compile this client");
            for (const auto &job : options.jobs)
                threeway::require(options.scenarios[job.scenario_index].kind != threeway::ScenarioKind::mixed_dnf,
                                  "Original Filtered-DiskANN supports single categorical labels, not mixed_dnf; "
                                  "mark this point unsupported instead of substituting union/postfilter searches");
            beam_ = threeway::u32(options.ini.uint_value("DiskANN", "BeamWidth"), "DiskANN.BeamWidth");
            const std::string prefix = options.resolve("DiskANN", "IndexPrefix").string();
            check_aio(options, max_threads, false);
            guard.prepare(prefix, beam_);
            guard.prove_reachability();
            worker_ids_.assign(max_threads, std::vector<uint64_t>(options.top_k, UINT64_MAX));
            reader_ = std::make_shared<LinuxAlignedFileReader>();
            index_ = std::make_unique<diskann::PQFlashIndex<uint8_t, uint32_t>>(reader_, diskann::Metric::L2);
            check_aio(options, max_threads);
            const auto load_start = threeway::Clock::now();
            threeway::require(index_->load(max_threads, prefix.c_str()) == 0, "Original DiskANN native load failed");
            guard.native_loaded(std::chrono::duration<double>(threeway::Clock::now() - load_start).count());
            threeway::require(index_->get_data_dim() == options.dimension &&
                                  index_->get_num_points() == options.vector_count,
                              "Loaded DiskANN index shape mismatch");
            for (const auto &entry : guard.external_labels)
            {
                const uint32_t native = index_->get_converted_label(std::to_string(entry.first));
                threeway::require(native == entry.second, "Native label conversion differs from admission metadata");
                labels_.emplace(entry.first, native);
            }
            guard.verify_files();
            loader_policy_.verify();
            guard.finish();
            admitted_ = true;
        }
        catch (const std::exception &error)
        {
            guard.finish(error.what());
            throw;
        }
    }

    void configure(const threeway::Job &job, const threeway::Scenario &scenario) override
    {
        current_l_ = job.L;
        filtered_ = scenario.kind == threeway::ScenarioKind::categorical;
        current_label_ = filtered_ ? labels_.at(scenario.tag) : 0;
    }

    threeway::SearchStats search(const uint8_t *query, uint32_t, uint32_t worker_id,
                                 uint32_t *ids, float *distances) override
    {
        threeway::require(admitted_, "Stock DiskANN search cannot run before admission");
        auto &native_ids = worker_ids_.at(worker_id);
        std::fill(native_ids.begin(), native_ids.end(), UINT64_MAX);
        diskann::QueryStats stats;
        index_->cached_beam_search(query, options_.top_k, current_l_, native_ids.data(), distances, beam_,
                                   filtered_, current_label_, false, &stats);
        // Preflight excludes stock K-copy underfill. Retain native counters as
        // a defensive invariant; they are not a substitute for that proof.
        const size_t count = static_cast<size_t>(std::min<uint64_t>(
            options_.top_k, static_cast<uint64_t>(stats.n_ios) + stats.n_cache_hits));
        for (size_t k = 0; k < count; ++k)
            ids[k] = native_ids[k] < UINT32_MAX ? static_cast<uint32_t>(native_ids[k]) : UINT32_MAX;
        return {count, static_cast<double>(stats.n_ios)};
    }
};
} // namespace

int main(int argc, char **argv)
{
    return threeway::run_main(argc, argv, "Filtered_DiskANN", [](const threeway::Options &options, uint32_t threads) {
        return std::make_unique<DiskANNBackend>(options, threads);
    });
}
