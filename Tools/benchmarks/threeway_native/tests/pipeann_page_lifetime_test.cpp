#include "ssd_index.h"

#include <array>
#include <atomic>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <thread>

namespace
{
constexpr uint32_t dimension = 16;
constexpr uint32_t degree = 4;

void require(bool value, const char *message)
{
    if (!value)
        throw std::runtime_error(message);
}

std::array<uint8_t, dimension> vector_for(uint32_t id)
{
    std::array<uint8_t, dimension> result{};
    for (uint32_t d = 0; d < dimension; ++d)
        result[d] = id == 1 ? 255 : static_cast<uint8_t>((id * 11 + d) % 251);
    return result;
}

class OrderedNeighbors final : public pipeann::AbstractNeighbor<uint8_t>
{
  public:
    explicit OrderedNeighbors(uint32_t count) : AbstractNeighbor(pipeann::Metric::L2)
    {
        npoints = count;
        data_size = 1;
        data.resize(count);
    }

    uint64_t query_ctx_size() override { return 256; }
    std::string get_name() override { return "bounded-lifetime-scheduling-fixture"; }

    void compute_dists(pipeann::QueryBuffer *buffer, const uint32_t *ids, uint64_t count) override
    {
        for (uint64_t i = 0; i < count; ++i)
            buffer->aligned_dist_scratch[i] = ids[i] == 1 ? 1000000.0f : static_cast<float>(ids[i]);
    }
};

class TagZero final : public pipeann::Selector
{
  public:
    Selector *copy() const override { return new TagZero; }
    double estimate_selectivity(const pipeann::Attributes &) override { return 0.5; }
    double estimate_precision(const pipeann::Attributes &) override { return 0.5; }
    uint32_t estimate_prefilter_reads(const pipeann::Attributes &) override { return 0; }
    uint32_t estimate_infilter_reads(const pipeann::Attributes &) override { return 0; }
    void prepare_in_filter(const pipeann::Attributes &, AlignedFileReader *) override {}
    bool is_member_approx(uint32_t, const pipeann::Attributes &) override { return true; }
    bool is_member(uint32_t, const pipeann::Attributes &, const pipeann::Attributes &attributes) override
    {
        return attributes.get(0) == pipeann::Attribute{0};
    }
    pipeann::VectorIDList pre_filter(const pipeann::Attributes &, AlignedFileReader *, bool) override
    {
        throw std::runtime_error("This fixture exercises the native pipelined traversal, not prefilter");
    }
};

class ScheduledReader final : public AlignedFileReader
{
    struct Context
    {
        std::vector<IORequest *> pending;
        uint64_t completed = 0;
        uint64_t polls = 0;
        bool released_first_batch = false;
    };

    const std::vector<char> &pages_;
    bool hold_first_batch_;

    int complete(Context &context)
    {
        require(++context.polls < 1000000, "Native fixture did not make progress");
        if (hold_first_batch_ && context.completed != 0 && !context.released_first_batch)
        {
            if (context.pending.size() < 4)
                return 0;
            context.released_first_batch = true;
        }
        const auto count = context.pending.size();
        for (auto *request : context.pending)
        {
            require(request->offset + request->len <= pages_.size(), "Native read outside bounded fixture");
            std::memcpy(request->buf, pages_.data() + request->offset, request->len);
            request->finished = true;
            ++context.completed;
        }
        context.pending.clear();
        return static_cast<int>(count);
    }

  public:
    ScheduledReader(const std::vector<char> &pages, bool hold) : pages_(pages), hold_first_batch_(hold) {}
    void *get_ctx(int) override
    {
        static thread_local Context context;
        require(context.pending.empty(), "Previous native query left unfinished IO");
        context = Context{};
        return &context;
    }
    void register_buf(void *, uint64_t, int) override {}
    void register_thread(int) override {}
    void deregister_thread() override {}
    void deregister_all_threads() override {}
    void open(const std::string &, bool writes, bool create) override
    {
        require(!writes && !create, "Bounded native reader is read-only");
    }
    void close() override {}
    int send_read(IORequest &request, void *ctx) override
    {
        request.finished = false;
        static_cast<Context *>(ctx)->pending.push_back(&request);
        return 0;
    }
    int send_read(std::vector<IORequest> &requests, void *ctx) override
    {
        for (auto &request : requests)
            send_read(request, ctx);
        return 0;
    }
    int poll(void *ctx) override { return complete(*static_cast<Context *>(ctx)); }
    void poll_alloc(void *, std::vector<uint64_t> *) override
    {
        throw std::runtime_error("Write-cache path is forbidden in this fixture");
    }
    void send_io(IORequest &request, void *ctx, bool write) override
    {
        require(!write, "Native fixture cannot write");
        send_read(request, ctx);
    }
    void send_io(std::vector<IORequest> &requests, void *ctx, bool write) override
    {
        require(!write, "Native fixture cannot write");
        send_read(requests, ctx);
    }
    void read(std::vector<IORequest> &, void *) override
    {
        throw std::runtime_error("Unexpected synchronous read in pipelined fixture");
    }
    void read_fd(int, std::vector<IORequest> &, void *) override
    {
        throw std::runtime_error("Unexpected sidecar read in pipelined fixture");
    }
    void read_alloc(std::vector<IORequest> &, void *, std::vector<uint64_t> *) override
    {
        throw std::runtime_error("Unexpected cache allocation in pipelined fixture");
    }
    void write(std::vector<IORequest> &, void *) override { throw std::runtime_error("Unexpected write"); }
    void write_fd(int, std::vector<IORequest> &, void *) override { throw std::runtime_error("Unexpected write"); }
};

struct Fixture
{
    uint32_t count;
    std::filesystem::path prefix;
    std::vector<char> pages;

    Fixture(const std::filesystem::path &root, uint32_t rows)
        : count(rows), prefix(root / ("fixture-" + std::to_string(rows))), pages((rows + 1) * SECTOR_LEN)
    {
        pipeann::Attributes attributes;
        attributes.set(0, {0});
        const auto attr_bytes = attributes.serialized_size();
        const uint64_t node_bytes = dimension + (1 + 2 * degree) * sizeof(uint32_t) + attr_bytes;
        pipeann::SSDIndexMetadata<uint8_t> meta(rows, dimension, 0, node_bytes, 1, degree, attr_bytes, 0);
        for (uint32_t id = 0; id < rows; ++id)
        {
            char *page = pages.data() + (id + 1) * SECTOR_LEN;
            const auto vector = vector_for(id);
            std::memcpy(page, vector.data(), vector.size());
            pipeann::DiskNode<uint8_t> node(page, id, meta);
            if (id == 0)
            {
                node.nnbrs = degree;
                const uint32_t neighbors[degree] = {2, 3, 4, 1};
                std::memcpy(node.nbrs, neighbors, sizeof(neighbors));
            }
            else if (id >= 2 && id + 3 < rows)
            {
                node.nnbrs = 1;
                node.nbrs[0] = id + 3;
            }
            node.n_dense_nbrs = 0;
            attributes.set(0, {id == 1 ? 109u : 0u});
            attributes.serialize(static_cast<char *>(node.attrs));
        }
        const auto path = prefix.string() + "_disk.index";
        require(!std::filesystem::exists(path), "Never overwrite a native fixture");
        {
            std::ofstream stream(path, std::ios::binary);
            stream.write(pages.data(), static_cast<std::streamsize>(pages.size()));
            require(static_cast<bool>(stream), "Cannot write native fixture");
        }
        meta.save_to_disk_index(path);
    }
};

uint64_t run_case(const Fixture &fixture, bool held, const std::string &mode, uint32_t workers)
{
    auto scheduled = std::make_shared<ScheduledReader>(fixture.pages, held);
    std::shared_ptr<AlignedFileReader> reader = scheduled;
    OrderedNeighbors neighbors(fixture.count);
    pipeann::IndexBuildParameters parameters;
    parameters.max_nthreads = workers;
    parameters.L = fixture.count;
    pipeann::SSDIndex<uint8_t> index(pipeann::Metric::L2, reader, &neighbors, false, &parameters);
    require(index.load(fixture.prefix.c_str(), false) == 0, "Bounded native load failed");
    std::atomic<uint64_t> invalid{0}, searches{0}, returned{0};
    std::vector<std::thread> threads;
    std::vector<std::exception_ptr> errors(workers);
    for (uint32_t worker = 0; worker < workers; ++worker)
        threads.emplace_back([&, worker] {
            try
            {
                const std::array<uint8_t, dimension> query{};
                TagZero selector;
                pipeann::Attributes attributes;
                attributes.set(0, {0});
                for (uint32_t repeat = 0; repeat < 2; ++repeat)
                {
                    std::vector<uint32_t> ids(fixture.count, UINT32_MAX);
                    std::vector<float> distances(fixture.count, -1);
                    pipeann::QueryStats stats{};
                    size_t count = 0;
                    if (mode == "unfilter")
                        count = index.pipe_search(query.data(), fixture.count, 0, fixture.count,
                                                  ids.data(), distances.data(), 32, &stats);
                    else if (mode == "infilter")
                        count = index.spec_infilter_search(query.data(), fixture.count, fixture.count, fixture.count,
                                                           &selector, attributes, ids.data(), distances.data(), 32,
                                                           &stats);
                    else
                        count = index.spec_postfilter_search(query.data(), fixture.count, fixture.count, fixture.count,
                                                             &selector, attributes, ids.data(), distances.data(), 32,
                                                             &stats);
                    ++searches;
                    returned += count;
                    for (size_t i = 0; i < count; ++i)
                    {
                        require(ids[i] < fixture.count, "Native fixture returned out-of-range ID");
                        const auto vector = vector_for(ids[i]);
                        uint32_t exact = 0;
                        for (auto value : vector)
                            exact += uint32_t(value) * value;
                        if (distances[i] != exact || (mode != "unfilter" && ids[i] == 1))
                            ++invalid;
                    }
                }
            }
            catch (...)
            {
                errors[worker] = std::current_exception();
            }
        });
    for (auto &thread : threads)
        thread.join();
    for (const auto &error : errors)
        if (error)
            std::rethrow_exception(error);
    std::cout << "PIPEANN_LIFETIME_CASE {\"rows\":" << fixture.count << ",\"held_first_batch\":"
              << (held ? "true" : "false") << ",\"mode\":\"" << mode << "\",\"workers\":" << workers
              << ",\"searches\":" << searches << ",\"returned\":" << returned << ",\"invalid_pairs\":"
              << invalid << "}" << std::endl;
    return invalid;
}
} // namespace

int main(int argc, char **argv)
{
    try
    {
        require(argc == 2, "Usage: pipeann_page_lifetime_test FRESH_FIXTURE_DIRECTORY");
        const std::filesystem::path root(argv[1]);
        require(!std::filesystem::exists(root), "Fixture directory must be fresh");
        std::filesystem::create_directories(root);
        uint64_t invalid = 0;
        for (uint32_t rows : {32u, 260u})
        {
            const Fixture fixture(root, rows);
            for (bool held : {false, true})
                for (const std::string mode : {"unfilter", "postfilter", "infilter"})
                    for (uint32_t workers : {1u, 4u})
                        invalid += run_case(fixture, held, mode, workers);
        }
        std::cout << "PIPEANN_LIFETIME_COMPLETE {\"invalid_pairs\":" << invalid
                  << ",\"production_index_loaded\":false,\"io\":\"deterministic bounded scheduled reader\"}"
                  << std::endl;
        return invalid ? 1 : 0;
    }
    catch (const std::exception &error)
    {
        std::cerr << "PIPEANN_LIFETIME_FAILURE " << error.what() << std::endl;
        return 2;
    }
}
