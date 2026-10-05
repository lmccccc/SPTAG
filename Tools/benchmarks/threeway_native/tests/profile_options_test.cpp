#include "../benchmark.h"

int main(int argc, char **argv)
{
    try
    {
        threeway::reject_environment_overrides();
        threeway::require(argc == 4, "Usage: profile_options_test profile.ini single|throughput ENGINE");
        const threeway::Options options(argv[1], argv[2], argv[3]);
        std::cout << "{\"engine\":" << threeway::json_string(options.engine)
                  << ",\"phase\":" << threeway::json_string(options.phase)
                  << ",\"vector_count\":" << options.vector_count
                  << ",\"query_count\":" << options.query_count
                  << ",\"warmup_queries\":" << options.warmup_queries
                  << ",\"top_k\":" << options.top_k
                  << ",\"scenario_count\":" << options.scenarios.size()
                  << ",\"job_count\":" << options.jobs.size()
                  << ",\"max_threads\":" << options.max_threads()
                  << ",\"output_directory\":" << threeway::json_string(options.output_directory.string())
                  << ",\"prepared_directory\":" << threeway::json_string(options.prepared_directory.string())
                  << ",\"first_control\":" << options.jobs.front().L << "}" << std::endl;
        return 0;
    }
    catch (const std::exception &error)
    {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
