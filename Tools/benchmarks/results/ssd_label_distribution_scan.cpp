#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

static void require(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}

#pragma pack(push, 1)
struct Header {
    uint32_t magic, version, postings, vectors, dimension, record_bytes, attributes, tail_pages;
    uint64_t generation;
    uint32_t data_page;
};
struct Directory {
    uint32_t page;
    uint16_t offset;
    uint32_t count;
    uint16_t pages;
    uint32_t h_count;
};
struct SupportHeader {
    uint32_t magic, version, bytes, heads, slots, votes, minimum, labels, key_column, attributes;
    uint64_t vectors, generation, fingerprint, extras, extra_bound;
};
struct LabelRecord { uint32_t label, required; uint64_t count; };
#pragma pack(pop)
static_assert(sizeof(Header) == 44 && sizeof(Directory) == 16 && sizeof(SupportHeader) == 80);
struct Posting { uint64_t offset; uint32_t count, h_count; };

static struct stat stat_file(const std::string& path) {
    struct stat value{};
    require(stat(path.c_str(), &value) == 0 && S_ISREG(value.st_mode), "Cannot stat regular input");
    return value;
}

static bool unchanged(const struct stat& a, const struct stat& b) {
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_size == b.st_size &&
        a.st_mtim.tv_sec == b.st_mtim.tv_sec && a.st_mtim.tv_nsec == b.st_mtim.tv_nsec &&
        a.st_ctim.tv_sec == b.st_ctim.tv_sec && a.st_ctim.tv_nsec == b.st_ctim.tv_nsec;
}

template<class T> static void read(std::istream& stream, T* values, size_t count = 1) {
    require(bool(stream.read(reinterpret_cast<char*>(values), sizeof(T) * count)), "Truncated input");
}

static void memory_guard(uint64_t reserve, uint64_t allocation = 0) {
    std::ifstream input("/proc/meminfo");
    std::string line;
    while (std::getline(input, line)) {
        if (line.rfind("MemAvailable:", 0) == 0) {
            const uint64_t available = std::stoull(line.substr(13)) * 1024;
            require(available >= reserve && available - reserve >= allocation,
                    "Insufficient available memory for the configured reserve and scan allocation");
            return;
        }
    }
    throw std::runtime_error("Cannot read available memory");
}

class Reader {
    static constexpr uint64_t Capacity = 64ULL << 20;
    int fd;
    void* buffer = nullptr;
    uint64_t file_bytes, begin = 0, length = 0;
public:
    Reader(const std::string& path, uint64_t bytes) : file_bytes(bytes) {
        require(bytes % 4096 == 0, "SSD file extent is not page aligned");
        fd = open(path.c_str(), O_RDONLY | O_DIRECT);
        require(fd >= 0, "Cannot open read-only direct-IO SSD file");
        if (posix_memalign(&buffer, 4096, Capacity) != 0) {
            close(fd);
            throw std::runtime_error("Cannot allocate aligned read buffer");
        }
    }
    ~Reader() { free(buffer); close(fd); }
    const unsigned char* at(uint64_t offset, uint64_t bytes) {
        require(offset <= file_bytes && bytes <= file_bytes - offset && bytes < Capacity - 4096,
                "Invalid posting payload extent");
        if (offset < begin || offset + bytes > begin + length) {
            begin = offset / 4096 * 4096;
            length = std::min(Capacity, file_bytes - begin);
            ssize_t done;
            do { done = pread(fd, buffer, length, begin); } while (done < 0 && errno == EINTR);
            require(done == static_cast<ssize_t>(length), "Short/failed direct SSD read");
        }
        return static_cast<const unsigned char*>(buffer) + offset - begin;
    }
};

struct Statistic {
    uint64_t postings = 0, occurrences = 0, pure = 0;
    long double sum = 0;
    double minimum = 1, maximum = 0;
    std::array<uint64_t, 100> histogram{};
    void add(uint32_t count, uint32_t total) {
        require(count > 0 && count <= total, "Invalid conditional label frequency");
        const long double fraction = static_cast<long double>(count) / total;
        ++postings;
        occurrences += count;
        pure += count == total;
        sum += fraction;
        minimum = std::min(minimum, static_cast<double>(fraction));
        maximum = std::max(maximum, static_cast<double>(fraction));
        ++histogram[std::min<uint64_t>(99, uint64_t(count) * 100 / total)];
    }
};

struct Group {
    uint64_t nonempty = 0, records = 0;
    std::vector<Statistic> labels;
    explicit Group(size_t count) : labels(count) {}
    void add(uint32_t total, const std::vector<uint32_t>& counts, const std::vector<uint32_t>& touched) {
        if (!total) { require(touched.empty(), "Nonempty labels in empty posting"); return; }
        ++nonempty;
        records += total;
        uint64_t sum = 0;
        for (uint32_t label : touched) {
            labels[label].add(counts[label], total);
            sum += counts[label];
        }
        require(sum == total, "Per-posting label accounting failed");
    }
    void json(const std::vector<LabelRecord>& source) const {
        uint64_t sum = 0;
        std::cout << "{\"nonempty_postings\":" << nonempty << ",\"stored_records\":" << records << ",\"labels\":[";
        for (size_t i = 0; i < labels.size(); ++i) {
            const auto& s = labels[i];
            sum += s.occurrences;
            require(std::accumulate(s.histogram.begin(), s.histogram.end(), uint64_t(0)) == s.postings,
                    "Histogram accounting failed");
            std::cout << (i ? "," : "") << "{\"label\":" << source[i].label
                      << ",\"source_vector_count\":" << source[i].count
                      << ",\"postings_with_label\":" << s.postings << ",\"stored_label_occurrences\":" << s.occurrences
                      << ",\"pure_postings\":" << s.pure << ",\"mean_proportion\":";
            if (s.postings) std::cout << s.sum / s.postings;
            else std::cout << "null";
            std::cout << ",\"min_proportion\":";
            if (s.postings) std::cout << s.minimum;
            else std::cout << "null";
            std::cout << ",\"max_proportion\":";
            if (s.postings) std::cout << s.maximum;
            else std::cout << "null";
            std::cout << ",\"histogram_counts\":[";
            for (size_t j = 0; j < s.histogram.size(); ++j)
                std::cout << (j ? "," : "") << s.histogram[j];
            std::cout << "]}";
        }
        require(sum == records, "Group label occurrence accounting failed");
        std::cout << "]}";
    }
};

static void scan(const std::string& ssd, const std::string& support, uint64_t reserve) {
    const uint32_t endian = 1;
    require(*reinterpret_cast<const unsigned char*>(&endian) == 1, "Little-endian host required");
    memory_guard(reserve);
    const auto before = stat_file(ssd), support_before = stat_file(support);
    std::ifstream input(ssd, std::ios::binary), labels_input(support, std::ios::binary);
    Header header{};
    SupportHeader metadata{};
    read(input, &header);
    read(labels_input, &metadata);
    require(header.magic == 0x314d5453 && header.version == 3 && header.dimension == 128 &&
            header.attributes == 2 && header.record_bytes == 140 && header.generation != 0 &&
            header.tail_pages == 0, "Expected raw UInt8/128/2-attribute STM1 v3 without legacy tails");
    require(metadata.magic == 0x3153544c && metadata.version == 5 && metadata.bytes == 80 &&
            metadata.heads == header.postings && metadata.attributes == 2 && metadata.key_column == 0 &&
            metadata.vectors == header.vectors && metadata.generation == header.generation &&
            metadata.labels > 0 && metadata.labels <= 4096 && metadata.slots > 0 && metadata.slots <= 4096,
            "Incompatible LTS1 v5 label provenance");
    const uint64_t label_offset = metadata.bytes + uint64_t(metadata.heads) * (metadata.slots + metadata.attributes) * 4;
    require(metadata.extras <= static_cast<uint64_t>(support_before.st_size) / 4, "Invalid extra-label extent");
    const uint64_t expected_support = label_offset + uint64_t(metadata.labels) * sizeof(LabelRecord) +
        (uint64_t(metadata.heads) + 1) * 8 + metadata.extras * 4;
    require(expected_support == static_cast<uint64_t>(support_before.st_size), "Wrong LTS1 v5 extent");
    labels_input.seekg(label_offset);
    std::vector<LabelRecord> source(metadata.labels);
    read(labels_input, source.data(), source.size());
    uint64_t source_count = 0;
    for (size_t i = 0; i < source.size(); ++i) {
        require(source[i].label == i && source[i].count > 0 && source[i].count <= header.vectors,
                "This bounded scanner requires dense declared labels with valid global counts");
        source_count += source[i].count;
    }
    require(source_count == header.vectors, "Global label counts do not cover the corpus");
    const uint64_t data_offset = uint64_t(header.data_page) * 4096;
    require(header.postings > 0 && uint64_t(header.postings) * sizeof(Posting) < (4ULL << 30) &&
            sizeof(Header) + uint64_t(header.postings) * sizeof(Directory) <= data_offset,
            "Invalid/oversized posting directory");
    memory_guard(reserve, uint64_t(header.postings) * sizeof(Posting) + (80ULL << 20));
    std::vector<Posting> postings;
    postings.reserve(header.postings);
    std::vector<Directory> block(65536);
    uint64_t total_records = 0, total_h = 0, empty = 0;
    for (uint64_t start = 0; start < header.postings; start += block.size()) {
        const size_t count = std::min<uint64_t>(block.size(), header.postings - start);
        read(input, block.data(), count);
        for (size_t j = 0; j < count; ++j) {
            const auto& d = block[j];
            require(d.offset < 4096 && d.h_count <= d.count, "Invalid H/O directory counts");
            if (!d.count) {
                require(!d.page && !d.offset && !d.pages && !d.h_count, "Invalid empty directory entry");
                ++empty;
                continue;
            }
            const uint64_t bytes = uint64_t(d.count) * header.record_bytes;
            const uint64_t offset = data_offset + uint64_t(d.page) * 4096 + d.offset;
            require((d.offset + bytes + 4095) / 4096 == d.pages &&
                    offset <= static_cast<uint64_t>(before.st_size) &&
                    bytes <= static_cast<uint64_t>(before.st_size) - offset, "Invalid posting storage extent");
            postings.push_back({offset, d.count, d.h_count});
            total_records += d.count;
            total_h += d.h_count;
        }
    }
    std::cerr << "{\"stage\":\"sorting\",\"nonempty_postings\":" << postings.size()
              << ",\"stored_records\":" << total_records << "}\n" << std::flush;
    std::sort(postings.begin(), postings.end(), [](const Posting& a, const Posting& b) { return a.offset < b.offset; });
    for (size_t i = 1; i < postings.size(); ++i)
        require(postings[i].offset >= postings[i - 1].offset + uint64_t(postings[i - 1].count) * header.record_bytes,
                "Overlapping physical posting payloads");
    memory_guard(reserve);
    Reader reader(ssd, before.st_size);
    Group physical(source.size()), h(source.size()), o(source.size());
    std::vector<uint32_t> counts(source.size()), hcounts(source.size()), ocounts(source.size());
    std::vector<uint32_t> touched, htouched, otouched;
    const auto started = std::chrono::steady_clock::now();
    auto last_report = started;
    for (size_t i = 0; i < postings.size(); ++i) {
        const auto& posting = postings[i];
        const auto* payload = reader.at(posting.offset, uint64_t(posting.count) * header.record_bytes);
        for (uint32_t row = 0; row < posting.count; ++row) {
            uint32_t id, label;
            std::memcpy(&id, payload + uint64_t(row) * header.record_bytes, 4);
            std::memcpy(&label, payload + uint64_t(row) * header.record_bytes + 4, 4);
            require(id < header.vectors && label < source.size(), "Invalid embedded original ID/label");
            if (!counts[label]++) touched.push_back(label);
            if (row < posting.h_count) {
                if (!hcounts[label]++) htouched.push_back(label);
            } else {
                if (!ocounts[label]++) otouched.push_back(label);
            }
        }
        physical.add(posting.count, counts, touched);
        h.add(posting.h_count, hcounts, htouched);
        o.add(posting.count - posting.h_count, ocounts, otouched);
        for (auto label : touched) counts[label] = 0;
        for (auto label : htouched) hcounts[label] = 0;
        for (auto label : otouched) ocounts[label] = 0;
        touched.clear(); htouched.clear(); otouched.clear();
        if ((i + 1) % 100000 == 0) {
            const auto now = std::chrono::steady_clock::now();
            if (now - last_report >= std::chrono::seconds(10)) {
                memory_guard(reserve);
                std::cerr << "{\"stage\":\"scanning\",\"processed_postings\":" << i + 1
                          << ",\"total_postings\":" << postings.size()
                          << ",\"file_position\":" << posting.offset
                          << ",\"file_bytes\":" << before.st_size
                          << ",\"elapsed_seconds\":" << std::chrono::duration<double>(now - started).count()
                          << "}\n" << std::flush;
                last_report = now;
            }
        }
    }
    require(physical.records == total_records && h.records == total_h && o.records + h.records == total_records,
            "Final directory/record counts disagree");
    require(unchanged(before, stat_file(ssd)) && unchanged(support_before, stat_file(support)),
            "Input identity changed during scan");
    std::cout << std::setprecision(17)
              << "{\"schema_version\":1,\"state\":\"complete\",\"population\":\"all_persisted_SSD_records\","
              << "\"zero_policy\":\"exclude_postings_without_the_label\","
              << "\"mean_definition\":\"unweighted mean of label_count/posting_record_count over positive postings\","
              << "\"primary_group\":\"physical_H_plus_O\",\"directory_entries\":" << header.postings
              << ",\"empty_directory_entries\":" << empty << ",\"corpus_vectors\":" << header.vectors
              << ",\"categorical_column\":0,\"generation_fingerprint\":\"" << header.generation
              << "\",\"distribution\":{\"unit\":\"fraction\",\"bin_semantics\":\"[left,right), last bin includes 1\","
              << "\"edges\":[";
    for (unsigned i = 0; i <= 100; ++i) std::cout << (i ? "," : "") << i / 100.0;
    std::cout << "]},\"groups\":{\"physical_H_plus_O\":";
    physical.json(source);
    std::cout << ",\"H_prefix\":";
    h.json(source);
    std::cout << ",\"O_suffix\":";
    o.json(source);
    std::cout << "}}\n";
    require(bool(std::cout), "Cannot write result JSON");
}

int main(int argc, char** argv) {
    try {
        require(argc == 4, "Usage: scanner SPTAGFullList.bin limited_tag_support.bin reserve_MiB");
        const std::string value(argv[3]);
        require(!value.empty() && value.find_first_not_of("0123456789") == std::string::npos,
                "reserve_MiB must be a nonnegative integer");
        const uint64_t reserve = std::stoull(value);
        require(reserve <= (std::numeric_limits<uint64_t>::max() >> 20), "reserve_MiB is too large");
        scan(argv[1], argv[2], reserve << 20);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "SSD label distribution failed: " << error.what() << '\n';
        return 1;
    }
}
