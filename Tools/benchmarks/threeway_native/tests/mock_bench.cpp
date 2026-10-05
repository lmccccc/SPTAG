#include "../benchmark.h"

#include <atomic>
#include <thread>

namespace
{
class MockBackend final : public threeway::Backend
{
    const threeway::Options &options_;
    const uint32_t max_threads_;
    std::vector<uint8_t> base_;
    std::vector<std::vector<uint32_t>> truth_;
    std::unique_ptr<std::atomic<uint64_t>[]> query_counts_;
    std::atomic<uint64_t> calls_{0}, job_calls_{0}, workers_{0};
    size_t scenario_ = 0;
    uint64_t configurations_ = 0, delay_us_ = 0, fail_after_ = 0;
    bool native_io_ = true;
    std::string mode_;

  public:
    MockBackend(const threeway::Options &options, uint32_t max_threads)
        : options_(options), max_threads_(max_threads),
          base_(threeway::read_matrix<uint8_t>(options.vectors, static_cast<uint32_t>(options.vector_count), options.dimension)),
          truth_(options.scenarios.size()), query_counts_(new std::atomic<uint64_t>[options.query_count])
    {
        threeway::require(options.vector_count <= 20000, "Mock backend is restricted to tiny fixtures");
        delay_us_ = options.ini.uint_value("Mock", "DelayMicroseconds");
        fail_after_ = options.ini.uint_value("Mock", "FailAfterCalls");
        mode_ = options.ini.require("Mock", "Mode");
        native_io_ = options.ini.bool_value("Mock", "NativeIO");
        for (uint32_t q = 0; q < options.query_count; ++q)
            query_counts_[q] = 0;
        for (const auto &job : options.jobs)
            if (truth_[job.scenario_index].empty())
                truth_[job.scenario_index] = threeway::read_matrix<uint32_t>(
                    options.prepared_directory / ("gt_" + options.scenarios[job.scenario_index].name + ".u32bin"),
                    options.query_count, options.top_k);
    }
    ~MockBackend() override
    {
        std::cout << "MOCK_SUMMARY {\"loads\":1,\"max_threads\":" << max_threads_
                  << ",\"configurations\":" << configurations_ << ",\"calls\":" << calls_
                  << ",\"worker_mask\":" << workers_ << ",\"query_counts\":[";
        for (uint32_t q = 0; q < options_.query_count; ++q)
            std::cout << (q ? "," : "") << query_counts_[q];
        std::cout << "]}" << std::endl;
    }
    void configure(const threeway::Job &job, const threeway::Scenario &scenario) override
    {
        threeway::require(mode_ != "recall_dip" || scenario.kind == threeway::ScenarioKind::unfilter,
                          "The recall-dip mock requires the unfiltered fixture");
        scenario_ = job.scenario_index;
        ++configurations_;
        job_calls_ = 0;
    }
    threeway::SearchStats search(const uint8_t *query, uint32_t query_index, uint32_t worker_id,
                                 uint32_t *ids, float *distances) override
    {
        ++calls_;
        ++query_counts_[query_index];
        workers_.fetch_or(uint64_t{1} << worker_id);
        const uint64_t ordinal = job_calls_.fetch_add(1);
        if (delay_us_)
            std::this_thread::sleep_for(std::chrono::microseconds(delay_us_));
        const bool fail = ordinal >= fail_after_;
        if (fail && mode_ == "throw")
            throw std::runtime_error("mock native query exception");
        if (fail && mode_ == "throw_nonstandard")
            throw 17;
        const bool recall_dip = mode_ == "recall_dip" &&
            ordinal >= static_cast<uint64_t>(options_.warmup_queries) + options_.query_count &&
            ordinal < static_cast<uint64_t>(options_.warmup_queries) + 2 * options_.query_count;
        uint32_t alternate = 0;
        for (uint32_t k = 0; k < options_.top_k; ++k)
        {
            ids[k] = truth_[scenario_][static_cast<size_t>(query_index) * options_.top_k + k];
            if (recall_dip)
            {
                const auto begin = truth_[scenario_].begin() + static_cast<size_t>(query_index) * options_.top_k;
                while (std::find(begin, begin + options_.top_k, alternate) != begin + options_.top_k)
                    ++alternate;
                ids[k] = alternate++;
            }
            const auto *vector = base_.data() + static_cast<size_t>(ids[k]) * options_.dimension;
            float distance = 0;
            for (uint32_t d = 0; d < options_.dimension; ++d)
            {
                const float delta = static_cast<float>(query[d]) - vector[d];
                distance += delta * delta;
            }
            distances[k] = distance;
        }
        if (fail)
        {
            if (mode_ == "invalid_id")
                ids[0] = UINT32_MAX;
            else if (mode_ == "duplicate")
                ids[1] = ids[0];
            else if (mode_ == "nonmatch")
                ids[0] = 1;
            else if (mode_ == "nonfinite")
                distances[0] = std::numeric_limits<float>::infinity();
            else if (mode_ == "underfill")
                return {options_.top_k - 1, 7.0};
        }
        return {options_.top_k, native_io_ ? std::optional<double>(7.0) : std::nullopt};
    }
};
} // namespace

int main(int argc, char **argv)
{
    return threeway::run_main(argc, argv, "Filtered_DiskANN", [](const threeway::Options &options, uint32_t threads) {
        return std::make_unique<MockBackend>(options, threads);
    });
}
