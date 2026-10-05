#include "../benchmark.h"
#include "linux_aligned_file_reader.h"
#include "pq_flash_index.h"

namespace
{
using Index = diskann::PQFlashIndex<uint8_t, uint32_t>;

// Explicit-instantiation access calls the actual linked parser without changing
// any native header, substituting a reference loop, or loading an index.
template <typename Tag, typename Tag::type Member> struct NativeAccess
{
    friend typename Tag::type access(Tag) { return Member; }
};
struct Metadata
{
    using type = void (Index::*)(const std::string &, uint32_t &, uint32_t &);
    friend type access(Metadata);
};
struct Parser
{
    using type = void (Index::*)(std::basic_istream<char> &, size_t &);
    friend type access(Parser);
};
struct Offsets
{
    using type = uint32_t *Index::*;
    friend type access(Offsets);
};
struct Counts
{
    using type = uint32_t *Index::*;
    friend type access(Counts);
};
struct Labels
{
    using type = uint32_t *Index::*;
    friend type access(Labels);
};
template struct NativeAccess<Metadata, &Index::get_label_file_metadata>;
template struct NativeAccess<Parser, &Index::parse_label_file>;
template struct NativeAccess<Offsets, &Index::_pts_to_label_offsets>;
template struct NativeAccess<Counts, &Index::_pts_to_label_counts>;
template struct NativeAccess<Labels, &Index::_pts_to_labels>;

void run(const std::string &input, bool emit_values, uint32_t repetition)
{
    std::shared_ptr<AlignedFileReader> reader = std::make_shared<LinuxAlignedFileReader>();
    Index index(reader, diskann::Metric::L2);
    uint32_t metadata_rows = 0, metadata_labels = 0;
    const auto metadata_start = threeway::Clock::now();
    (index.*access(Metadata{}))(input, metadata_rows, metadata_labels);
    const auto metadata_seconds = std::chrono::duration<double>(threeway::Clock::now() - metadata_start).count();
    std::istringstream stream(input);
    size_t parsed_rows = 0;
    const auto parser_start = threeway::Clock::now();
    (index.*access(Parser{}))(stream, parsed_rows);
    const auto parser_seconds = std::chrono::duration<double>(threeway::Clock::now() - parser_start).count();
    const uint32_t *offsets = index.*access(Offsets{});
    const uint32_t *counts = index.*access(Counts{});
    const uint32_t *labels = index.*access(Labels{});
    uint64_t checksum = 14695981039346656037ULL, parsed_labels = 0;
    std::ostringstream values;
    if (emit_values)
        values << ",\"offsets\":[";
    for (size_t row = 0; row < parsed_rows; ++row)
    {
        if (emit_values)
            values << (row ? "," : "") << offsets[row];
        parsed_labels += counts[row];
        checksum = (checksum ^ offsets[row]) * 1099511628211ULL;
        checksum = (checksum ^ counts[row]) * 1099511628211ULL;
        for (uint32_t label = 0; label < counts[row]; ++label)
            checksum = (checksum ^ labels[offsets[row] + label]) * 1099511628211ULL;
    }
    if (emit_values)
    {
        values << "],\"counts\":[";
        for (size_t row = 0; row < parsed_rows; ++row)
            values << (row ? "," : "") << counts[row];
        values << "],\"labels\":[";
        for (size_t label = 0; label < parsed_labels; ++label)
            values << (label ? "," : "") << labels[label];
        values << "]";
    }
    threeway::require(metadata_rows == parsed_rows && metadata_labels == parsed_labels,
                      "Native metadata and parser disagree");
    threeway::require(stream.tellg() == 0 && !stream.fail(), "Original parser did not reset its input stream");
    std::cout << std::setprecision(17)
              << "LOADER_PARSER {\"metadata_rows\":" << metadata_rows
              << ",\"metadata_labels\":" << metadata_labels << ",\"parsed_rows\":" << parsed_rows
              << ",\"parsed_labels\":" << parsed_labels << ",\"input_bytes\":" << input.size()
              << ",\"checksum\":" << checksum << ",\"repetition\":" << repetition
              << ",\"metadata_seconds\":" << metadata_seconds << ",\"parser_seconds\":" << parser_seconds
              << ",\"stream_reset\":true,\"native_parser\":true,\"index_loaded\":false"
                 ",\"native_search_invocations\":0"
              << values.str() << "}" << std::endl;
}
} // namespace

int main(int argc, char **argv)
{
    try
    {
        threeway::require(argc >= 3, "Usage: parser-probe semantics FILE | scale ROWS REPETITIONS");
        const std::string mode = argv[1];
        if (mode == "semantics")
        {
            threeway::require(argc == 3 && std::filesystem::file_size(argv[2]) <= 4096,
                              "Parser semantic fixture must be bounded to 4096 bytes");
            std::ifstream stream(argv[2], std::ios::binary);
            threeway::require(stream.is_open(), "Cannot open semantic fixture");
            std::string input((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
            run(input, true, 0);
        }
        else
        {
            threeway::require(mode == "scale" && argc == 4, "Unknown bounded parser probe mode");
            const uint64_t rows = threeway::parse_uint(argv[2], "rows");
            const uint64_t repetitions = threeway::parse_uint(argv[3], "repetitions");
            threeway::require(rows <= 256000 && rows > 0 && repetitions <= 5 && repetitions > 0,
                              "Scaling probe exceeds its explicit small-data bounds");
            std::string input;
            input.reserve(rows * 4);
            for (uint64_t row = 0; row < rows; ++row)
                input += std::to_string(row % 201) + "\n";
            for (uint32_t repetition = 0; repetition < repetitions; ++repetition)
                run(input, false, repetition);
        }
        return 0;
    }
    catch (const std::exception &error)
    {
        std::cout << "LOADER_PARSER_ERROR " << threeway::json_string(error.what()) << std::endl;
        return 2;
    }
}
