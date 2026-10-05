#pragma once

#include "benchmark.h"

#include "pq_flash_index.h"

#include <unordered_map>
#include <unordered_set>

#ifndef THREEWAY_DISKANN_LIBRARY
#error Build with build_diskann_client.py to identify the original linked library
#endif

namespace threeway_diskann
{
namespace fs = std::filesystem;

inline uint64_t kernel_counter(const char *path)
{
    std::ifstream input(path);
    std::string value, extra;
    threeway::require(static_cast<bool>(input >> value) && !(input >> extra),
                      std::string("Cannot read Linux AIO counter: ") + path);
    return threeway::parse_uint(value, path);
}

struct AdmissionParameters
{
    const std::string phase;
    const uint64_t vector_count;
    const uint32_t dimension, top_k;
    const uint64_t cache_nodes;
    const std::vector<threeway::Scenario> &scenarios;
    const std::vector<threeway::Job> &jobs;
};

struct AdmissionFile
{
    fs::path path, resolved;
    uint64_t bytes = 0, device = 0, inode = 0;
    int64_t mtime_ns = 0, ctime_ns = 0;

    static AdmissionFile capture(const fs::path &path)
    {
        struct stat info{};
        threeway::require(::stat(path.c_str(), &info) == 0 && S_ISREG(info.st_mode) && info.st_size >= 0,
                          "Admission requires a readable regular file: " + path.string());
        return {path, fs::canonical(path), static_cast<uint64_t>(info.st_size),
                static_cast<uint64_t>(info.st_dev), static_cast<uint64_t>(info.st_ino),
                static_cast<int64_t>(info.st_mtim.tv_sec) * 1000000000 + info.st_mtim.tv_nsec,
                static_cast<int64_t>(info.st_ctim.tv_sec) * 1000000000 + info.st_ctim.tv_nsec};
    }

    void verify() const
    {
        const auto now = capture(path);
        threeway::require(resolved == now.resolved && bytes == now.bytes && device == now.device &&
                              inode == now.inode && mtime_ns == now.mtime_ns && ctime_ns == now.ctime_ns,
                          "Immutable admission input changed: " + path.string());
    }

    std::string json() const
    {
        std::ostringstream out;
        out << "{\"path\":" << threeway::json_string(path.string())
            << ",\"resolved\":" << threeway::json_string(resolved.string())
            << ",\"bytes\":" << bytes << ",\"device\":" << device << ",\"inode\":" << inode
            << ",\"mtime_ns\":" << mtime_ns << ",\"ctime_ns\":" << ctime_ns << "}";
        return out.str();
    }
};

class AdmissionGuard
{
    struct TemporaryBits
    {
        uint64_t *data;
        size_t bytes;
        explicit TemporaryBits(size_t words) : data(nullptr), bytes(words * sizeof(uint64_t))
        {
            void *mapping = ::mmap(nullptr, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
            threeway::require(mapping != MAP_FAILED, "Cannot allocate temporary native-label membership bitset");
            data = static_cast<uint64_t *>(mapping);
        }
        ~TemporaryBits() { ::munmap(data, bytes); }
        TemporaryBits(const TemporaryBits &) = delete;
        TemporaryBits &operator=(const TemporaryBits &) = delete;
        uint64_t &operator[](size_t word) { return data[word]; }
    };

    const AdmissionParameters &options_;
    const std::function<void(const std::string &)> emit_;
    const threeway::Clock::time_point started_ = threeway::Clock::now();
    std::vector<AdmissionFile> files_;
    std::vector<fs::path> absent_;
    std::vector<std::string> witnesses_;
    std::unordered_map<uint32_t, size_t> label_slots_;
    std::vector<uint32_t> native_labels_;
    std::vector<uint64_t> matching_rows_;
    std::vector<std::unique_ptr<TemporaryBits>> membership_;
    std::map<uint32_t, std::vector<uint32_t>> filtered_starts_;
    std::vector<uint32_t> unfiltered_starts_;
    std::string disk_, label_path_;
    uint64_t points_ = 0, node_bytes_ = 0, nodes_per_sector_ = 0, degree_bound_ = 0, disk_bytes_ = 0;
    uint64_t label_rows_ = 0, label_values_ = 0, label_bytes_ = 0, label_hash_ = 14695981039346656037ULL;
    uint64_t membership_bytes_ = 0, graph_reads_ = 0, graph_bytes_ = 0;
    double label_seconds_ = 0, witness_seconds_ = 0, native_load_seconds_ = 0;
    double pq_distance_bound_ = 0, medoid_distance_bound_ = 0;
    bool label_scan_ = false, native_loaded_ = false, membership_released_ = false, finished_ = false;

    void remember(const fs::path &path)
    {
        for (const auto &file : files_)
            if (file.path == path)
                return;
        files_.push_back(AdmissionFile::capture(path));
    }

    void require_absent(const fs::path &path, const std::string &reason)
    {
        threeway::require(!fs::exists(path), reason + ": " + path.string());
        absent_.push_back(path);
    }

    static uint32_t native_uint(const std::string &token, const std::string &context)
    {
        return threeway::u32(threeway::parse_uint(threeway::trim(token), context), context, true);
    }

    template <typename T>
    static std::vector<T> embedded(const fs::path &path, uint64_t offset, uint32_t rows, uint32_t cols)
    {
        const uint64_t count = threeway::checked_product(rows, cols, "Admission embedded matrix");
        const uint64_t size = threeway::checked_product(count, sizeof(T), "Admission embedded matrix");
        const uint64_t extent = fs::file_size(path);
        threeway::require(offset <= extent && extent - offset >= 8 && size <= extent - offset - 8 &&
                              count <= std::vector<T>().max_size(),
                          "Invalid embedded native metadata extent: " + path.string());
        std::ifstream input(path, std::ios::binary);
        input.seekg(static_cast<std::streamoff>(offset));
        uint32_t shape[2]{};
        input.read(reinterpret_cast<char *>(shape), 8);
        threeway::require(input && shape[0] == rows && shape[1] == cols,
                          "Invalid embedded native metadata shape: " + path.string());
        std::vector<T> values(static_cast<size_t>(count));
        input.read(reinterpret_cast<char *>(values.data()), static_cast<std::streamsize>(size));
        threeway::require(static_cast<bool>(input), "Truncated native metadata: " + path.string());
        return values;
    }

    static float upper_float(long double value)
    {
        threeway::require(std::isfinite(value) && value >= 0 &&
                              value < std::numeric_limits<float>::max(),
                          "Cannot prove finite native medoid/PQ distances for all UInt8 queries");
        const float rounded = static_cast<float>(value);
        const float upper = std::nextafter(rounded, std::numeric_limits<float>::infinity());
        threeway::require(std::isfinite(upper) && upper < std::numeric_limits<float>::max(),
                          "Native distance bound can reach the medoid-selection sentinel");
        return upper;
    }

    // A finite strict upper bound excludes native best_medoid's fallback ID 0.
    // Bounds round outward after every float operation, also covering FMA use.
    void certify_start_selection(const std::string &prefix, uint32_t chunks)
    {
        const fs::path pivots = prefix + "_pq_pivots.bin";
        remember(pivots);
        std::ifstream metadata(pivots, std::ios::binary);
        uint32_t shape[2]{};
        metadata.read(reinterpret_cast<char *>(shape), 8);
        threeway::require(metadata && (shape[0] == 4 || shape[0] == 5) && shape[1] == 1,
                          "Unsupported native PQ offsets metadata");
        const auto offsets = embedded<uint64_t>(pivots, 0, shape[0], 1);
        threeway::require(offsets[0] == METADATA_SIZE, "Native PQ pivots must begin at METADATA_SIZE");
        for (size_t i = 1; i < offsets.size(); ++i)
            threeway::require(offsets[i] > offsets[i - 1], "Nonmonotone PQ metadata offsets");
        threeway::require(offsets.back() == fs::file_size(pivots), "PQ metadata file extent differs");
        const auto table = embedded<float>(pivots, offsets[0], 256, options_.dimension);
        const auto centroid = embedded<float>(pivots, offsets[1], options_.dimension, 1);
        const auto chunk_offsets = embedded<uint32_t>(pivots, offsets[shape[0] == 4 ? 2 : 3], chunks + 1, 1);
        threeway::require(chunk_offsets.front() == 0 && chunk_offsets.back() == options_.dimension,
                          "PQ chunks do not cover the declared dimensions");
        for (size_t i = 1; i < chunk_offsets.size(); ++i)
            threeway::require(chunk_offsets[i] > chunk_offsets[i - 1], "Empty/nonmonotone PQ chunk");
        std::vector<float> query_bound(options_.dimension);
        for (uint32_t d = 0; d < options_.dimension; ++d)
        {
            threeway::require(std::isfinite(centroid[d]), "Nonfinite PQ centroid");
            query_bound[d] = upper_float(255.0L + std::abs(static_cast<long double>(centroid[d])));
        }
        const fs::path rotation = pivots.string() + "_rotation_matrix.bin";
        if (fs::exists(rotation))
        {
            remember(rotation);
            const auto matrix = threeway::read_matrix<float>(rotation, options_.dimension, options_.dimension);
            std::vector<float> rotated(options_.dimension);
            for (uint32_t d = 0; d < options_.dimension; ++d)
            {
                float sum = 0;
                for (uint32_t j = 0; j < options_.dimension; ++j)
                {
                    const float coefficient = matrix[static_cast<size_t>(j) * options_.dimension + d];
                    threeway::require(std::isfinite(coefficient), "Nonfinite PQ rotation coefficient");
                    const float product = upper_float(static_cast<long double>(query_bound[j]) * std::abs(coefficient));
                    sum = upper_float(static_cast<long double>(sum) + product);
                }
                // Leave reduction-order margin as well as per-operation rounding.
                rotated[d] = upper_float(2.0L * sum);
            }
            query_bound.swap(rotated);
        }
        else
            absent_.push_back(rotation);
        float total = 0;
        for (uint32_t c = 0; c < chunks; ++c)
        {
            float chunk = 0;
            for (uint32_t d = chunk_offsets[c]; d < chunk_offsets[c + 1]; ++d)
            {
                long double maximum = 0;
                for (uint32_t center = 0; center < 256; ++center)
                {
                    const float value = table[static_cast<size_t>(center) * options_.dimension + d];
                    threeway::require(std::isfinite(value), "Nonfinite PQ pivot");
                    maximum = std::max(maximum, std::abs(static_cast<long double>(value)));
                }
                const float difference = upper_float(maximum + query_bound[d]);
                const float square = upper_float(static_cast<long double>(difference) * difference);
                chunk = upper_float(static_cast<long double>(chunk) + square);
            }
            total = upper_float(static_cast<long double>(total) + chunk);
        }
        // dimension*epsilon<0.008, so this also covers reassociated reductions.
        pq_distance_bound_ = upper_float(2.0L * total);

        const fs::path centroids = disk_ + "_centroids.bin";
        const fs::path medoids = disk_ + "_medoids.bin";
        if (fs::exists(centroids) && fs::exists(medoids))
        {
            remember(centroids);
            const auto rows = threeway::matrix_shape<uint32_t>(medoids).rows;
            const auto values = threeway::read_matrix<float>(centroids, rows, options_.dimension);
            long double maximum = 0;
            for (uint32_t row = 0; row < rows; ++row)
            {
                long double sum = 0;
                for (uint32_t d = 0; d < options_.dimension; ++d)
                {
                    const float value = values[static_cast<size_t>(row) * options_.dimension + d];
                    threeway::require(std::isfinite(value), "Nonfinite native start centroid");
                    const long double difference = 255.0L + std::abs(static_cast<long double>(value));
                    sum += difference * difference;
                }
                maximum = std::max(maximum, sum);
            }
            // The UInt8 accumulator bound below also gives dimension*epsilon<0.008.
            // Factor two bounds multiplication/reduction roundoff for any SIMD order.
            medoid_distance_bound_ = upper_float(2 * maximum);
        }
        else
        {
            if (!fs::exists(centroids))
                absent_.push_back(centroids);
            medoid_distance_bound_ = upper_float(2.0L * options_.dimension * 65025);
        }
    }

    void scan_labels()
    {
        const auto started = threeway::Clock::now();
        struct ScanTimer
        {
            threeway::Clock::time_point start;
            double &seconds;
            ~ScanTimer() { seconds = std::chrono::duration<double>(threeway::Clock::now() - start).count(); }
        } timer{started, label_seconds_};
        std::ifstream input(label_path_, std::ios::binary);
        threeway::require(input.is_open(), "Cannot open native label metadata");
        label_scan_ = true;
        uint64_t token = 0;
        bool digits = false, trailing_space = false, line_has_label = false;
        const auto finish_token = [&] {
            threeway::require(digits && label_rows_ < points_, "Empty native label token or excess label rows");
            ++label_values_;
            threeway::require(label_values_ <= UINT32_MAX, "Native uint32 label-count metadata would overflow");
            const auto found = label_slots_.find(static_cast<uint32_t>(token));
            if (found != label_slots_.end())
            {
                auto &word = (*membership_[found->second])[static_cast<size_t>(label_rows_ / 64)];
                const uint64_t bit = uint64_t{1} << (label_rows_ % 64);
                if (!(word & bit))
                    ++matching_rows_[found->second];
                word |= bit;
            }
            token = 0;
            digits = trailing_space = false;
            line_has_label = true;
        };
        std::array<char, 1024 * 1024> buffer{};
        while (input)
        {
            input.read(buffer.data(), buffer.size());
            const size_t count = static_cast<size_t>(input.gcount());
            label_bytes_ += count;
            for (size_t i = 0; i < count; ++i)
            {
                const unsigned char ch = static_cast<unsigned char>(buffer[i]);
                label_hash_ = (label_hash_ ^ ch) * 1099511628211ULL;
                if (ch >= '0' && ch <= '9')
                {
                    threeway::require(!trailing_space, "Junk after a native label number");
                    token = token * 10 + ch - '0';
                    threeway::require(token <= UINT32_MAX, "Native label is outside uint32 range");
                    digits = true;
                }
                else if (ch == ',' || ch == '\n')
                {
                    finish_token();
                    if (ch == '\n')
                    {
                        ++label_rows_;
                        line_has_label = false;
                    }
                }
                else if (ch == ' ' || ch == '\t' || ch == '\r')
                    trailing_space = trailing_space || digits;
                else
                    throw std::runtime_error("Noncanonical native label token; refusing ambiguous std::stoul input");
            }
        }
        threeway::require(input.eof() && !digits && !line_has_label && label_rows_ == points_ &&
                              label_bytes_ == fs::file_size(label_path_),
                          "Native label rows/extent/final newline differ from native point count");
        label_seconds_ = std::chrono::duration<double>(threeway::Clock::now() - started).count();
    }

    bool matches(uint32_t id, const threeway::Scenario &scenario) const
    {
        if (scenario.kind == threeway::ScenarioKind::unfilter)
            return true;
        const size_t slot = label_slots_.at(external_labels.at(scenario.tag));
        return ((*membership_[slot])[id / 64] & (uint64_t{1} << (id % 64))) != 0;
    }

    static void read_at(int fd, uint64_t offset, void *data, size_t bytes)
    {
        auto *out = static_cast<char *>(data);
        while (bytes)
        {
            const ssize_t count = ::pread(fd, out, bytes, static_cast<off_t>(offset));
            if (count < 0 && errno == EINTR)
                continue;
            threeway::require(count > 0, "Truncated/unreadable native graph witness record");
            bytes -= static_cast<size_t>(count);
            offset += static_cast<uint64_t>(count);
            out += count;
        }
    }

    std::vector<uint32_t> neighbors(int fd, uint32_t id)
    {
        threeway::require(id < points_, "Witness node outside native point count");
        const uint64_t sector = 1 + (nodes_per_sector_ ? id / nodes_per_sector_ :
                                   static_cast<uint64_t>(id) * ((node_bytes_ + 4095) / 4096));
        const uint64_t within = nodes_per_sector_ ? (id % nodes_per_sector_) * node_bytes_ : 0;
        const uint64_t offset = sector * 4096 + within;
        threeway::require(offset <= disk_bytes_ && node_bytes_ <= disk_bytes_ - offset,
                          "Witness node exceeds native disk layout extent");
        uint32_t degree = 0;
        read_at(fd, offset + options_.dimension, &degree, 4);
        ++graph_reads_;
        graph_bytes_ += 4;
        threeway::require(degree <= degree_bound_, "Native witness adjacency exceeds record degree bound");
        std::vector<uint32_t> result(degree);
        read_at(fd, offset + options_.dimension + 4, result.data(), result.size() * 4);
        graph_bytes_ += result.size() * 4;
        for (uint32_t neighbor : result)
            threeway::require(neighbor < points_, "Native witness edge leaves the declared graph");
        return result;
    }

    void witness(int fd, const threeway::Scenario &scenario, uint32_t start)
    {
        const uint64_t reads_before = graph_reads_;
        std::vector<uint32_t> ids, parents;
        std::string error;
        try
        {
            threeway::require(start < points_ && matches(start, scenario),
                              "Native start medoid is out of range or lacks its declared native label");
            ids.push_back(start);
            parents.push_back(start);
            std::unordered_set<uint32_t> seen{start};
            std::unordered_set<uint32_t> checked;
            for (size_t cursor = 0; cursor < ids.size() && ids.size() < options_.top_k; ++cursor)
            {
                checked.insert(ids[cursor]);
                for (uint32_t next : neighbors(fd, ids[cursor]))
                    if (matches(next, scenario) && seen.insert(next).second)
                    {
                        ids.push_back(next);
                        parents.push_back(ids[cursor]);
                        if (ids.size() == options_.top_k)
                            break;
                    }
            }
            threeway::require(ids.size() >= options_.top_k,
                              "Reachable native admissible component has fewer than TopK distinct nodes");
            for (uint32_t id : ids)
                if (!checked.count(id))
                    neighbors(fd, id);
        }
        catch (const std::exception &failure)
        {
            error = failure.what();
        }
        std::ostringstream out;
        out << "{\"scenario\":" << threeway::json_string(scenario.name) << ",\"start\":" << start
            << ",\"native_label\":";
        if (scenario.kind == threeway::ScenarioKind::unfilter)
            out << "null";
        else
            out << external_labels.at(scenario.tag);
        out << ",\"required\":" << options_.top_k << ",\"reachable_witness_nodes\":" << ids.size()
            << ",\"graph_records_read\":" << graph_reads_ - reads_before
            << ",\"status\":" << threeway::json_string(error.empty() ? "admitted" : "rejected")
            << ",\"error\":" << threeway::json_string(error) << ",\"nodes\":[";
        for (size_t i = 0; i < ids.size(); ++i)
            out << (i ? "," : "") << ids[i];
        out << "],\"parent_nodes\":[";
        for (size_t i = 0; i < parents.size(); ++i)
            out << (i ? "," : "") << parents[i];
        out << "]}";
        witnesses_.push_back(out.str());
        threeway::require(error.empty(), scenario.name + " start " + std::to_string(start) + ": " + error);
    }

    void release_membership()
    {
        membership_.clear();
        membership_.shrink_to_fit();
        membership_released_ = true;
    }

  public:
    std::map<uint32_t, uint32_t> external_labels;

    AdmissionGuard(const AdmissionParameters &options, std::function<void(const std::string &)> emit)
        : options_(options), emit_(std::move(emit))
    {
    }

    void prepare(const std::string &prefix, uint32_t beam)
    {
        threeway::check_native_host();
        threeway::require(options_.cache_nodes == 0,
                          "Admission proof requires the registered CacheNodes=0 path");
        threeway::require(static_cast<uint64_t>(options_.dimension) * 65025 <= UINT32_MAX,
                          "UInt8 L2 dimension can overflow the pinned native uint32 accumulator");
        for (const auto &job : options_.jobs)
            threeway::require(job.L >= options_.top_k, "Admission proof requires every planned L>=TopK");
        disk_ = prefix + "_disk.index";
        label_path_ = disk_ + "_labels.txt";
        remember(disk_);
        require_absent(disk_ + "_pq_pivots.bin", "Disk PQ is outside this admission proof");
        require_absent(disk_ + "_universal_label.txt", "Universal labels are outside this admission proof");
        const fs::path dummy = disk_ + "_dummy_map.txt";
        if (fs::exists(dummy))
        {
            remember(dummy);
            threeway::require(fs::file_size(dummy) == 0, "Nonempty dummy-ID remapping is unsupported by admission");
        }
        else
            absent_.push_back(dummy);
        const fs::path compressed = prefix + "_pq_compressed.bin";
        remember(compressed);
        const auto codes = threeway::matrix_shape<uint8_t>(compressed);
        points_ = codes.rows;
        threeway::require(points_ == options_.vector_count && codes.cols <= options_.dimension &&
                              codes.cols <= MAX_PQ_CHUNKS,
                          "Native/original point count or PQ width mismatch without dummy remapping");
        const auto values = embedded<uint64_t>(disk_, 0, 9, 1);
        disk_bytes_ = fs::file_size(disk_);
        node_bytes_ = values[3];
        nodes_per_sector_ = values[4];
        threeway::require(values[0] == points_ && values[1] == options_.dimension && values[2] < points_ &&
                              values[5] == 0 && values[7] == 0 && values[8] == disk_bytes_,
                          "Admission requires matching native shape, frozen=0, reorder=0 and exact extent");
        threeway::require(node_bytes_ >= static_cast<uint64_t>(options_.dimension) + 4 &&
                              (node_bytes_ - options_.dimension - 4) % 4 == 0,
                          "Invalid native node-record geometry");
        degree_bound_ = (node_bytes_ - options_.dimension - 4) / 4;
        threeway::require(degree_bound_ <= diskann::defaults::MAX_GRAPH_DEGREE,
                          "Native graph degree exceeds the pinned core bound");
        const uint64_t sectors_per_node = (node_bytes_ + 4095) / 4096;
        const uint64_t expected_sectors = node_bytes_ <= 4096 ?
            1 + (points_ + (4096 / node_bytes_) - 1) / (4096 / node_bytes_) :
            1 + points_ * sectors_per_node;
        threeway::require(nodes_per_sector_ == (node_bytes_ <= 4096 ? 4096 / node_bytes_ : 0) &&
                              disk_bytes_ == expected_sectors * 4096 &&
                              static_cast<uint64_t>(beam) * sectors_per_node <= diskann::defaults::MAX_N_SECTOR_READS,
                          "Native graph sector layout/extent or beam bound mismatch");

        const fs::path medoids = disk_ + "_medoids.bin";
        if (fs::exists(medoids))
        {
            remember(medoids);
            const auto shape = threeway::matrix_shape<uint32_t>(medoids);
            threeway::require(shape.cols == 1 && shape.rows <= points_, "Invalid native medoid matrix");
            unfiltered_starts_ = threeway::read_matrix<uint32_t>(medoids, shape.rows, 1);
        }
        else
        {
            absent_.push_back(medoids);
            unfiltered_starts_.push_back(static_cast<uint32_t>(values[2]));
        }
        for (uint32_t start : unfiltered_starts_)
            threeway::require(start < points_, "Native unfiltered medoid is outside the index");
        certify_start_selection(prefix, codes.cols);

        std::set<uint32_t> tags;
        for (const auto &job : options_.jobs)
            if (options_.scenarios[job.scenario_index].kind == threeway::ScenarioKind::categorical)
                tags.insert(options_.scenarios[job.scenario_index].tag);
        if (fs::exists(label_path_))
        {
            remember(label_path_);
            const fs::path map_path = disk_ + "_labels_map.txt";
            remember(map_path);
            std::ifstream mapping(map_path);
            std::map<std::string, uint32_t> label_map;
            std::string line;
            while (std::getline(mapping, line))
            {
                const auto fields = threeway::split(line, '\t');
                threeway::require(fields.size() == 2 && !fields[0].empty(), "Invalid native label-map row");
                threeway::require(label_map.emplace(fields[0], native_uint(fields[1], "Native label map")).second,
                                  "Duplicate/ambiguous native label-map key");
            }
            threeway::require(mapping.eof(), "Unreadable native label map");
            for (uint32_t tag : tags)
            {
                const auto found = label_map.find(std::to_string(tag));
                threeway::require(found != label_map.end(), "Planned categorical label is absent from native map");
                external_labels.emplace(tag, found->second);
                if (!label_slots_.count(found->second))
                {
                    label_slots_.emplace(found->second, native_labels_.size());
                    native_labels_.push_back(found->second);
                    matching_rows_.push_back(0);
                    membership_.push_back(std::make_unique<TemporaryBits>(static_cast<size_t>((points_ + 63) / 64)));
                    membership_bytes_ += membership_.back()->bytes;
                }
            }
            scan_labels();
        }
        else
        {
            absent_.push_back(label_path_);
            threeway::require(tags.empty(), "Categorical admission requires native label metadata");
        }
        const fs::path filtered = disk_ + "_labels_to_medoids.txt";
        if (fs::exists(filtered))
        {
            remember(filtered);
            std::ifstream input(filtered);
            std::string line;
            while (std::getline(input, line))
            {
                auto fields = threeway::split(line, ',');
                if (!fields.empty() && fields.back().empty())
                    fields.pop_back();
                threeway::require(fields.size() >= 2, "Empty/malformed native label medoid list");
                const uint32_t label = native_uint(fields[0], "Native medoid label");
                std::vector<uint32_t> starts;
                for (size_t i = 1; i < fields.size(); ++i)
                {
                    const uint32_t start = native_uint(fields[i], "Native start medoid");
                    threeway::require(start < points_, "Native filtered medoid is outside the index");
                    starts.push_back(start);
                }
                threeway::require(filtered_starts_.emplace(label, std::move(starts)).second,
                                  "Duplicate/ambiguous native label-to-medoid row");
            }
            threeway::require(input.eof(), "Unreadable native medoid metadata");
        }
        else
        {
            absent_.push_back(filtered);
            threeway::require(tags.empty(), "Categorical admission requires native label-to-medoid metadata");
        }
        verify_files();
    }

    void prove_reachability()
    {
        const auto started = threeway::Clock::now();
        const int fd = ::open(disk_.c_str(), O_RDONLY | O_CLOEXEC);
        threeway::require(fd >= 0, "Cannot open read-only native graph for bounded witnesses");
        try
        {
            std::string first_failure;
            std::set<size_t> planned;
            for (const auto &job : options_.jobs)
                planned.insert(job.scenario_index);
            for (size_t id : planned)
            {
                const auto &scenario = options_.scenarios[id];
                const std::vector<uint32_t> *starts = &unfiltered_starts_;
                if (scenario.kind == threeway::ScenarioKind::categorical)
                {
                    const uint32_t label = external_labels.at(scenario.tag);
                    const auto found = filtered_starts_.find(label);
                    threeway::require(found != filtered_starts_.end() && !found->second.empty(),
                                      "Planned label has no native start medoids");
                    starts = &found->second;
                }
                const std::set<uint32_t> distinct(starts->begin(), starts->end());
                for (uint32_t start : distinct)
                {
                    try
                    {
                        witness(fd, scenario, start);
                    }
                    catch (const std::exception &error)
                    {
                        if (first_failure.empty())
                            first_failure = error.what();
                    }
                }
            }
            threeway::require(first_failure.empty(), first_failure);
        }
        catch (...)
        {
            ::close(fd);
            witness_seconds_ = std::chrono::duration<double>(threeway::Clock::now() - started).count();
            throw;
        }
        ::close(fd);
        witness_seconds_ = std::chrono::duration<double>(threeway::Clock::now() - started).count();
        verify_files();
    }

    void verify_files() const
    {
        for (const auto &file : files_)
            file.verify();
        for (const auto &path : absent_)
            threeway::require(!fs::exists(path), "Absent native admission sidecar appeared: " + path.string());
    }

    void native_loaded(double seconds)
    {
        native_loaded_ = true;
        native_load_seconds_ = seconds;
    }

    std::map<uint32_t, uint64_t> native_label_counts() const
    {
        std::map<uint32_t, uint64_t> result;
        for (size_t i = 0; i < native_labels_.size(); ++i)
            result.emplace(native_labels_[i], matching_rows_[i]);
        return result;
    }

    void release_temporary_membership()
    {
        release_membership();
    }

    void finish(const std::string &error = "")
    {
        if (finished_)
            return;
        release_membership();
        std::ostringstream out;
        out.imbue(std::locale::classic());
        out << std::setprecision(17)
            << "{\"schema_version\":1,\"engine\":\"Filtered_DiskANN\""
               ",\"guard\":\"bounded-all-native-starts-v1\",\"source_revision\":"
            << threeway::json_string(THREEWAY_DISKANN_REVISION) << ",\"phase\":"
            << threeway::json_string(options_.phase) << ",\"status\":"
            << threeway::json_string(error.empty() ? "admitted" : "rejected")
            << ",\"error\":" << threeway::json_string(error) << ",\"pid\":" << ::getpid()
            << ",\"top_k\":" << options_.top_k << ",\"native_points\":" << points_
            << ",\"assumptions_verified\":" << (error.empty() ? "true" : "false")
            << ",\"required_all_planned_L_at_least_K\":true,\"required_cache_nodes\":0,\"required_frozen_points\":0"
               ",\"required_reorder\":false,\"required_universal_labels\":false,\"required_nonempty_dummy_map\":false"
               ",\"native_io_limit\":4294967295,\"native_search_invocations\":0,\"warmup_queries\":0,\"measured_queries\":0"
            << ",\"native_index_loaded\":" << (native_loaded_ ? "true" : "false")
            << ",\"label_source\":" << threeway::json_string(label_path_)
            << ",\"extra_label_read_passes\":" << (label_scan_ ? 1 : 0)
            << ",\"label_rows\":" << label_rows_ << ",\"label_values\":" << label_values_
            << ",\"label_bytes\":" << label_bytes_ << ",\"label_read_seconds\":" << label_seconds_
            << ",\"label_fnv1a64\":\"" << std::hex << std::setw(16) << std::setfill('0') << label_hash_ << std::dec
            << "\",\"temporary_membership_bytes\":" << membership_bytes_
            << ",\"temporary_membership_released\":" << (membership_released_ ? "true" : "false")
            << ",\"graph_records_read\":" << graph_reads_ << ",\"graph_bytes_read\":" << graph_bytes_
            << ",\"witness_seconds\":" << witness_seconds_ << ",\"native_load_seconds\":" << native_load_seconds_
            << ",\"pq_distance_upper_bound\":" << pq_distance_bound_
            << ",\"medoid_distance_upper_bound\":" << medoid_distance_bound_
            << ",\"finite_start_selection_verified\":"
            << (pq_distance_bound_ > 0 && medoid_distance_bound_ > 0 ? "true" : "false")
            << ",\"witness_io\":\"bounded O_RDONLY pread of native adjacency; timed reader remains native O_DIRECT\""
            << ",\"elapsed_seconds\":" << std::chrono::duration<double>(threeway::Clock::now() - started_).count()
            << ",\"planned_native_labels\":[";
        for (size_t i = 0; i < native_labels_.size(); ++i)
            out << (i ? "," : "") << "{\"label\":" << native_labels_[i] << ",\"rows\":" << matching_rows_[i] << "}";
        out << "],\"sources\":[";
        for (size_t i = 0; i < files_.size(); ++i)
            out << (i ? "," : "") << files_[i].json();
        out << "],\"absent_sources\":[";
        for (size_t i = 0; i < absent_.size(); ++i)
            out << (i ? "," : "") << threeway::json_string(absent_[i].string());
        out << "],\"witnesses\":[";
        for (size_t i = 0; i < witnesses_.size(); ++i)
            out << (i ? "," : "") << witnesses_[i];
        out << "]}";
        emit_(out.str());
        finished_ = true;
    }
};
} // namespace threeway_diskann
