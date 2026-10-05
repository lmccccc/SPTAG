#include "benchmark.h"

namespace
{
class PrefixBackend final : public threeway::Backend
{
    const threeway::Inputs &inputs_;
    uint64_t counts_[3] = {0, 3, 10};

  public:
    explicit PrefixBackend(const threeway::Inputs &inputs) : inputs_(inputs) {}
    void configure(const threeway::Job &, const threeway::Scenario &) override {}
    threeway::SearchStats search(const uint8_t *, uint32_t query, uint32_t, uint32_t *ids, float *distances) override
    {
        for (uint64_t i = 0; i < counts_[query]; ++i)
        {
            ids[i] = inputs_.truths[0][query * 10 + i];
            const auto delta = static_cast<int64_t>(ids[i]) - query;
            distances[i] = static_cast<float>(64 * delta * delta);
        }
        return {counts_[query], 1.0};
    }
};
}

int main(int argc, char **argv)
{
    try
    {
        threeway::require(argc == 2, "Usage: pipeann_short_results_test FIXTURE.ini");
        threeway::Options options(argv[1], "single", "PipeANN");
        threeway::require(options.allow_short_results && options.query_count == 3 && options.top_k == 10,
                          "Expected explicitly enabled 3-query/top10 bounded fixture");
        threeway::Inputs inputs(options);
        PrefixBackend backend(inputs);
        threeway::Cohort cohort(3, 10);
        threeway::batch(backend, options, inputs, options.jobs.at(0), 3, cohort);
        threeway::Totals total;
        total.add(options, inputs, options.jobs.at(0), 3, cohort);
        threeway::require(total.queries == 3 && total.hits == 13 && total.returned_neighbors == 13 &&
                              total.missing_neighbors == 17 && total.underfilled_queries == 2 &&
                              total.invalid_queries == 0 && cohort.counts == std::vector<uint64_t>({0, 3, 10}),
                          "Actual native returned prefixes were not accounted exactly");
        threeway::require(cohort.ids[0] == UINT32_MAX && std::isnan(cohort.distances[0]),
                          "Unused native buffer tail was rewritten");
        auto malformed = cohort;
        malformed.calls[1].stats.count = 11;
        threeway::Totals overflow;
        overflow.add(options, inputs, options.jobs.at(0), 3, malformed);
        threeway::require(overflow.invalid_queries != 0, "Count above K was accepted");
        malformed = cohort;
        malformed.ids[10] = UINT32_MAX;
        threeway::Totals bad_id;
        bad_id.add(options, inputs, options.jobs.at(0), 3, malformed);
        threeway::require(bad_id.invalid_ids == 1 && bad_id.invalid_queries == 1,
                          "Invalid ID inside native returned prefix was ignored");
        malformed = cohort;
        malformed.ids[11] = malformed.ids[10];
        threeway::Totals duplicate;
        duplicate.add(options, inputs, options.jobs.at(0), 3, malformed);
        threeway::require(duplicate.duplicate_ids == 1 && duplicate.invalid_queries == 1,
                          "Duplicate ID inside native returned prefix was ignored");
        malformed = cohort;
        malformed.distances[10] = std::numeric_limits<float>::quiet_NaN();
        threeway::Totals bad_distance;
        bad_distance.add(options, inputs, options.jobs.at(0), 3, malformed);
        threeway::require(bad_distance.nonfinite_distances == 1 && bad_distance.invalid_queries == 1,
                          "Nonfinite distance inside native returned prefix was ignored");
        options.allow_short_results = false;
        threeway::Totals strict;
        strict.add(options, inputs, options.jobs.at(0), 3, cohort);
        threeway::require(strict.invalid_queries == 2, "Strict legacy full-K behavior was relaxed implicitly");
        options.allow_short_results = true;
        const auto output = options.output_directory / "native/PipeANN/single";
        std::filesystem::create_directories(output);
        threeway::run_job(backend, options, inputs, options.jobs.at(0), output);
        std::cout << "SHORT_RESULTS_UNIT_COMPLETE {\"status\":\"passed\",\"native_index_loaded\":false,"
                     "\"returned_counts\":[0,3,10],\"recall_denominator\":30,\"returned_hits\":13,"
                     "\"invalid_prefixes_rejected\":true,\"raw_tails_unchanged\":true}" << std::endl;
        return 0;
    }
    catch (const std::exception &error)
    {
        std::cerr << "SHORT_RESULTS_UNIT_FAILURE " << error.what() << std::endl;
        return 1;
    }
}
