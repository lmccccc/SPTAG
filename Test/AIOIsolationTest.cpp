#include "inc/Helper/AsyncFileReader.h"
#include "inc/Core/SPANN/Index.h"
#include "inc/Core/SPANN/ExtraStaticSearcher.h"
#include "inc/CoreInterface.h"
#include <filesystem>
#include <fstream>
#include <future>
#include <iostream>
#include <optional>

using namespace SPTAG;
using Reader = Helper::AsyncFileIO;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("AIO isolation line " + std::to_string(__LINE__)); } while (false)

static std::shared_ptr<Reader> Open(const std::filesystem::path& path, int contexts, bool shared = false)
{
    auto reader = std::make_shared<Reader>();
    CHECK(reader->SetUseSharedAIOPool(shared));
    CHECK(reader->Initialize(path.c_str(), O_RDONLY, 64, 2, 2, contexts));
    CHECK(reader->UsesSharedAIOPool() == shared);
    CHECK(reader->NumContexts() == (shared ? 4 : contexts));
    return reader;
}

static void Concurrent(const std::vector<std::shared_ptr<Helper::DiskIO>>& files, int workers, bool barrier)
{
    std::mutex mutex;
    std::condition_variable changed;
    int arrived = 0;
    std::vector<std::future<void>> tasks;
    for (int worker = 0; worker < workers; ++worker) {
        tasks.emplace_back(std::async(std::launch::async, [&, worker] {
            auto handlers = files;
            if (worker % 2) std::reverse(handlers.begin(), handlers.end());
            const auto owner = std::this_thread::get_id();
            for (int repeat = 0; repeat < (barrier ? 1 : 4); ++repeat) {
                std::vector<std::vector<char>> buffers(handlers.size(), std::vector<char>(4096));
                std::vector<Helper::AsyncReadRequest> requests(handlers.size());
                int calls = 0;
                for (size_t i = 0; i < requests.size(); ++i) {
                    auto& request = requests[i];
                    request.m_status = static_cast<int>(i << 16); // Deliberately identical workspace IDs.
                    request.m_buffer = buffers[i].data();
                    request.m_readSize = 4096;
                    request.m_offset = ((worker + repeat) % 16) * 4096;
                    request.m_callback = [&, i](bool success) {
                        CHECK(success && std::this_thread::get_id() == owner);
                        for (char value : buffers[i]) CHECK(value == char((worker + repeat) % 16));
                        if (++calls == 1 && barrier) {
                            std::unique_lock<std::mutex> lock(mutex);
                            ++arrived;
                            changed.notify_all();
                            CHECK(changed.wait_for(lock, std::chrono::seconds(10), [&] { return arrived == workers; }));
                        }
                    };
                }
                CHECK(Helper::BatchReadFileAsync(handlers, requests.data(), requests.size()));
                CHECK(calls == static_cast<int>(requests.size()));
            }
        }));
    }
    for (auto& task : tasks) task.get();
    if (barrier) CHECK(arrived == workers);
}

static void FailureOwnership(const std::filesystem::path& file)
{
    std::vector<std::shared_ptr<Helper::DiskIO>> readers{Open(file, 1), Open(file, 1)};
    std::vector<char> a(4096), b(4096);
    Helper::AsyncReadRequest requests[2];
    requests[0].m_buffer = a.data();
    requests[0].m_readSize = a.size();
    requests[1].m_status = 1 << 16;
    requests[1].m_buffer = b.data();
    requests[1].m_readSize = b.size();
    requests[1].m_offset = UINT64_MAX; // The second file's submission fails after the first succeeds.
    int completed = 0;
    requests[0].m_callback = [&](bool success) { CHECK(success); ++completed; };
    CHECK(!Helper::BatchReadFileAsync(readers, requests, 2));
    CHECK(completed == 1);
    requests[1].m_offset = 0;
    CHECK(Helper::BatchReadFileAsync(readers, requests, 2));
    CHECK(completed == 2);
    requests[0].m_callback = [](bool) { throw std::runtime_error("callback failure"); };
    bool threw = false;
    try { Helper::BatchReadFileAsync(readers, requests, 2); }
    catch (const std::runtime_error& e) { threw = std::string(e.what()) == "callback failure"; }
    CHECK(threw);
    requests[0].m_callback = {};
    CHECK(Helper::BatchReadFileAsync(readers, requests, 2));
    requests[1].m_offset = 16 * 4096; // EOF must report failure, not scan stale bytes.
    bool shortRead = false;
    requests[1].m_callback = [&](bool success) { shortRead = !success; };
    CHECK(!Helper::BatchReadFileAsync(readers, requests, 2) && shortRead);
    {
        std::vector<Helper::AsyncReadRequest> batch(512);
        std::vector<char> buffers(batch.size() * 4096);
        int callbacks = 0;
        for (size_t i = 0; i < batch.size(); ++i) {
            batch[i].m_buffer = buffers.data() + i * 4096;
            batch[i].m_readSize = 4096;
            batch[i].m_callback = [&](bool success) {
                CHECK(success);
                if (++callbacks == 1) throw std::runtime_error("large batch callback");
            };
        }
        bool drained = false;
        try { Helper::BatchReadFileAsync(readers, batch.data(), batch.size()); }
        catch (const std::runtime_error& e) { drained = std::string(e.what()) == "large batch callback"; }
        CHECK(drained && callbacks == 512);
    }
    Concurrent(readers, 8, false);
    {
        auto reader = Open(file, 2);
        std::vector<std::future<void>> tasks;
        for (int i = 0; i < 12; ++i) {
            tasks.emplace_back(std::async(std::launch::async, [&, i] {
                char buffer[4096];
                Helper::AsyncReadRequest request;
                request.m_buffer = buffer;
                request.m_readSize = sizeof(buffer);
                request.m_offset = i * sizeof(buffer);
                if (i % 2) {
                    CHECK(reader->BatchReadFile(&request, 1, std::chrono::seconds(2)) == 1);
                } else {
                    std::vector<std::shared_ptr<Helper::DiskIO>> handlers{reader};
                    CHECK(Helper::BatchReadFileAsync(handlers, &request, 1));
                }
                for (char value : buffer) CHECK(value == char(i));
            }));
        }
        for (auto& task : tasks) task.get();
    }
    {
        auto reader = Open(file, 1);
        auto held = reader->AcquirePrivateContext();
        auto waiting = std::async(std::launch::async, [&] { return reader->AcquirePrivateContext(); });
        CHECK(waiting.wait_for(std::chrono::milliseconds(50)) == std::future_status::timeout);
        reader->InvalidateContext(held.Context());
        CHECK(waiting.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
        CHECK(waiting.get().Context() < 0 && !reader->Available());
    }
}

static void NativeLoad(const std::filesystem::path& directory)
{
    const auto tenant = directory / "tenant_0";
    CHECK(std::filesystem::create_directory(tenant));
    constexpr int count = 512, dim = 8;
    std::vector<float> data(count * dim);
    for (int i = 0; i < count; ++i)
        for (int j = 0; j < dim; ++j) data[i * dim + j] = float((i * 17 + j * 31) % 509);
    auto index = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
    const auto set = [&](const char* section, const char* name, const char* value) {
        CHECK(index->SetParameter(name, value, section) == ErrorCode::Success);
    };
    set("Base", "DistCalcMethod", "L2");
    set("Base", "IndexAlgoType", "BKT");
    set("Base", "IndexDirectory", tenant.c_str());
    set("SelectHead", "isExecute", "true");
    set("SelectHead", "SelectHeadType", "Random");
    set("SelectHead", "Ratio", "0.125");
    set("SelectHead", "NumberOfThreads", "1");
    set("BuildHead", "isExecute", "true");
    set("BuildHead", "NumberOfThreads", "1");
    set("BuildHead", "NeighborhoodSize", "16");
    set("BuildHead", "RefineIterations", "1");
    set("BuildSSDIndex", "isExecute", "true");
    set("BuildSSDIndex", "BuildSsdIndex", "true");
    set("BuildSSDIndex", "Storage", "STATIC");
    set("BuildSSDIndex", "NumberOfThreads", "1");
    set("BuildSSDIndex", "InternalResultNum", "32");
    set("BuildSSDIndex", "ReplicaCount", "2");
    set("BuildSSDIndex", "PostingPageLimit", "8");
    set("SearchSSDIndex", "NumberOfThreads", "1");
    set("SearchSSDIndex", "InternalResultNum", "32");
    CHECK(index->BuildIndex(data.data(), count, dim, false, true) == ErrorCode::Success);
    CHECK(index->SaveIndex(tenant.string()) == ErrorCode::Success);
    index.reset();
    const auto savedConfig = [&] {
        std::ifstream stream(tenant / "indexloader.ini", std::ios::binary);
        return std::string(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
    };
    const auto savedBytes = savedConfig();
    {
        std::ofstream manifest(directory / "manifest.txt");
        manifest << "dimension 8\nunified_storage 1\ntenant 0 512 0 64 "
            << static_cast<int>(TenantIndexType::SPANN) << '\n';
        CHECK(bool(manifest));
    }
    const auto legacy = directory / "legacy";
    CHECK(std::filesystem::create_directory(legacy));
    std::filesystem::copy(tenant, legacy / "tenant_0", std::filesystem::copy_options::recursive);
    std::filesystem::copy_file(directory / "manifest.txt", legacy / "manifest.txt");
    {
        std::istringstream source(savedBytes);
        std::ofstream target(legacy / "tenant_0/indexloader.ini");
        std::string line;
        while (std::getline(source, line)) {
            auto lower = line;
            std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) { return std::tolower(c); });
            if (lower.find("usesharedaiopool") == std::string::npos) target << line << '\n';
        }
        CHECK(bool(target));
    }
    const auto originalFactory = SPANN::f_createAsyncIO;
    struct Restore {
        std::function<std::shared_ptr<Helper::DiskIO>()> factory;
        ~Restore() { SPANN::f_createAsyncIO = factory; }
    } restore{originalFactory};
    std::vector<std::weak_ptr<Reader>> opened;
    SPANN::f_createAsyncIO = [&] {
        auto reader = std::make_shared<Reader>();
        opened.push_back(reader);
        return reader;
    };
    std::vector<BasicResult> reference;
    for (int contexts : {1, 48, 0}) {
        opened.clear();
        std::shared_ptr<VectorIndex> loaded;
        CHECK(VectorIndex::LoadIndex(tenant.string(), loaded, [&](VectorIndex& target, const Helper::IniReader&) {
            if (target.SetParameter("UseSharedAIOPool", contexts ? "false" : "true", "SearchSSDIndex") != ErrorCode::Success)
                return ErrorCode::Fail;
            return target.SetParameter("AIOContextCount", std::to_string(contexts).c_str(), "SearchSSDIndex");
        }) == ErrorCode::Success);
        CHECK(!opened.empty());
        for (auto weak : opened) {
            auto reader = weak.lock();
            if (reader && reader->Available()) {
                CHECK(reader->UsesSharedAIOPool() == (contexts == 0));
                CHECK(reader->NumContexts() == (contexts ? contexts : 4));
            }
        }
        COMMON::QueryResultSet<float> result(data.data(), 10);
        CHECK(loaded->SearchIndex(result) == ErrorCode::Success);
        if (reference.empty())
            for (int i = 0; i < 10; ++i) reference.push_back(*result.GetResult(i));
        for (int i = 0; i < 10; ++i) {
            CHECK(result.GetResult(i)->VID == reference[i].VID);
            CHECK(result.GetResult(i)->Dist == reference[i].Dist);
        }
        CHECK(loaded->SetParameter("AIOContextCount", "3", "SearchSSDIndex") != ErrorCode::Success);
        CHECK(loaded->SetParameter("UseSharedAIOPool", contexts ? "true" : "false", "SearchSSDIndex") != ErrorCode::Success);
    }
    for (int mode : {0, 1, -1, -2}) {
        const bool shared = mode == 1 || mode == -2;
        opened.clear();
        TenantIndexManager manager(dim, "SPANN", "Float");
        if (mode >= 0)
            CHECK(manager.SetSearchParamChecked("UseSharedAIOPool", shared ? "true" : "false", "SearchSSDIndex"));
        CHECK(manager.SetSearchParamChecked("NumberOfThreads", "48", "SearchSSDIndex"));
        CHECK(manager.LoadAll((mode == -2 ? legacy : directory).c_str()));
        const auto result = manager.Search(ByteArray(reinterpret_cast<std::uint8_t*>(data.data()),
            dim * sizeof(float), false), 0, 10);
        CHECK(result && result->GetResultNum() == 10);
        for (int i = 0; i < 10; ++i) {
            CHECK(result->GetResult(i)->VID == reference[i].VID);
            CHECK(result->GetResult(i)->Dist == reference[i].Dist);
        }
        CHECK(!opened.empty());
        for (auto weak : opened) {
            auto reader = weak.lock();
            if (reader && reader->Available()) {
                CHECK(reader->UsesSharedAIOPool() == shared);
                CHECK(reader->NumContexts() == (shared ? 4 : 48));
            }
        }
        CHECK(!manager.SetSearchParamChecked("UseSharedAIOPool", shared ? "false" : "true", "SearchSSDIndex"));
        CHECK(manager.UnloadTenant(0));
        opened.clear();
        CHECK(manager.Search(ByteArray(reinterpret_cast<std::uint8_t*>(data.data()), dim * sizeof(float), false), 0, 10));
        CHECK(!opened.empty());
        for (auto weak : opened) {
            auto reader = weak.lock();
            if (reader && reader->Available()) {
                CHECK(reader->UsesSharedAIOPool() == shared);
                CHECK(reader->NumContexts() == (shared ? 4 : 48));
            }
        }
    }
    CHECK(savedConfig() == savedBytes);
}

int main()
{
    const auto directory = std::filesystem::current_path() / ("aio-isolation-" + std::to_string(getpid()));
    try {
        CHECK(std::filesystem::create_directory(directory));
        const auto file = directory / "pages.bin";
        {
            std::ofstream output(file, std::ios::binary);
            for (int i = 0; i < 16; ++i) {
                const std::string page(4096, char(i));
                output.write(page.data(), page.size());
            }
            CHECK(bool(output));
        }
        Reader missing;
        CHECK(missing.SetUseSharedAIOPool(true));
        CHECK(!missing.Initialize(file.c_str(), O_RDONLY, 64, 2, 2, 1));
        CHECK(Helper::SharedAIOPool::Instance().Initialize(4, 1024));
        {
            auto reader = Open(file, 2);
            CHECK(!reader->SetUseSharedAIOPool(true));
            std::optional<Reader::ContextLease> first(reader->AcquirePrivateContext());
            std::optional<Reader::ContextLease> second(reader->AcquirePrivateContext());
            CHECK(first->Context() != second->Context());
            auto third = std::async(std::launch::async, [&] { return reader->AcquirePrivateContext(); });
            CHECK(third.wait_for(std::chrono::milliseconds(50)) == std::future_status::timeout);
            first.reset();
            CHECK(third.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
            auto lease = third.get();
            CHECK(lease.Context() != second->Context());
        }
        Concurrent({Open(file, 48), Open(file, 48)}, 48, true);
        Concurrent({Open(file, 2), Open(file, 2)}, 12, false);
        Concurrent({Open(file, 2, true), Open(file, 2, true)}, 12, false);
        FailureOwnership(file);
        NativeLoad(directory);
        std::filesystem::remove_all(directory);
        std::cout << "PASS explicit AIO isolation, 48-way callback overlap, leases, failures, native reloads\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << "\nFixture retained at " << directory << '\n';
        return 1;
    }
}
