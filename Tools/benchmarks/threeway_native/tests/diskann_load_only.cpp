#include "../benchmark.h"
#include "linux_aligned_file_reader.h"
#include "pq_flash_index.h"

int main(int argc, char **argv)
{
    try
    {
        threeway::require(argc == 4, "Usage: diskann_load_only PREFIX NODE EXPECTED_DEGREE");
        const uint32_t node = threeway::u32(threeway::parse_uint(argv[2], "node"), "node", true);
        const uint32_t expected = threeway::u32(threeway::parse_uint(argv[3], "degree"), "degree", true);
        omp_set_dynamic(0);
        omp_set_num_threads(1);
        std::shared_ptr<AlignedFileReader> reader = std::make_shared<LinuxAlignedFileReader>();
        diskann::PQFlashIndex<uint8_t, uint32_t> index(reader, diskann::Metric::L2);
        threeway::require(index.load(1, argv[1]) == 0 && node < index.get_num_points(), "Native load-only probe failed");
        std::vector<uint32_t> neighbors(index.get_max_degree());
        std::vector<uint8_t *> coordinates{nullptr};
        std::vector<std::pair<uint32_t, uint32_t *>> adjacency{{0, neighbors.data()}};
        const auto read = index.read_nodes({node}, coordinates, adjacency);
        threeway::require(read[0] && adjacency[0].first == expected, "Native reader disagrees with fixture degree");
        std::cout << "THREEWAY_NATIVE_LOAD_ONLY {\"native_loaded\":true,\"native_search_invocations\":0,"
                     "\"node\":" << node << ",\"degree\":" << adjacency[0].first << ",\"pid\":" << ::getpid() << "}"
                  << std::endl;
        return 0;
    }
    catch (const std::exception &error)
    {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
