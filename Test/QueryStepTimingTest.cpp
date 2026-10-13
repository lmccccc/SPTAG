#include "inc/Core/Common/QueryStepTiming.h"
#include "inc/Core/BKT/Index.h"
#include "inc/Helper/AsyncFileReader.h"
#include <iostream>
#include <numeric>
#include <thread>
#ifdef SPTAG_QUERY_STEP_TIMING
#include <unistd.h>
#endif

using namespace SPTAG;
#define CHECK(value) do { if (!(value)) throw std::runtime_error("Step timing check: " #value); } while (false)

int main() {
    try {
        int calls = 0;
        CHECK(SPTAG_STEP_CALL(Distance, ++calls) == 1 && calls == 1);
#ifdef SPTAG_QUERY_STEP_TIMING
        COMMON::StepTiming::Query query(true);
        {
            SPTAG_STEP_SCOPE(H1);
            std::this_thread::sleep_for(std::chrono::microseconds(50));
            {
                SPTAG_STEP_SCOPE(Hierarchy);
                std::this_thread::sleep_for(std::chrono::microseconds(50));
            }
            std::thread other([] { CHECK(COMMON::StepTiming::current == nullptr); });
            other.join();
        }
        auto values = query.Finish();
        CHECK(values[COMMON::StepTiming::H1] > 0 && values[COMMON::StepTiming::Hierarchy] > 0);
        CHECK(COMMON::StepTiming::current == nullptr);
        {
            COMMON::StepTiming::Query inactive(false);
            SPTAG_STEP_SCOPE(Distance);
            CHECK(COMMON::StepTiming::current == nullptr);
            CHECK(inactive.Finish() == COMMON::StepTiming::Values{});
        }
        try {
            COMMON::StepTiming::Query exceptional(true);
            SPTAG_STEP_SCOPE(H1);
            throw std::runtime_error("Expected unwind");
        } catch (const std::runtime_error&) {
            CHECK(COMMON::StepTiming::current == nullptr);
        }

        BKT::Index<std::uint8_t> index;
        for (const auto& item : std::vector<std::pair<const char*, const char*>>{
            {"DistCalcMethod", "L2"}, {"NumberOfThreads", "1"}, {"MaxCheck", "16"},
            {"BKTKmeansK", "4"}, {"BKTLeafSize", "4"}, {"NeighborhoodSize", "16"},
            {"TPTNumber", "1"}, {"RefineIterations", "1"}})
            CHECK(index.SetParameter(item.first, item.second) == ErrorCode::Success);
        std::vector<std::uint8_t> data(128 * 128);
        for (int i = 0; i < 128; ++i) std::fill_n(data.data() + 128 * i, 128, i);
        CHECK(index.BuildIndex(data.data(), 128, 128) == ErrorCode::Success);
        COMMON::QueryResultSet<std::uint8_t> plain(data.data(), 10), timed(data.data(), 10);
        CHECK(index.SearchIndex(plain) == ErrorCode::Success);
        COMMON::StepTiming::Query native(true);
        CHECK(index.SearchIndex(timed) == ErrorCode::Success);
        values = native.Finish();
        CHECK(values[COMMON::StepTiming::H1] > 0);
        for (int i = 0; i < 10; ++i)
            CHECK(plain.GetResult(i)->VID == timed.GetResult(i)->VID &&
                  plain.GetResult(i)->Dist == timed.GetResult(i)->Dist);

        CHECK(Helper::SharedAIOPool::Instance().Initialize(1, 64));
        char path[] = "/tmp/sptag-step-timing-XXXXXX";
        const int fd = mkstemp(path);
        CHECK(fd >= 0);
        std::vector<char> bytes(4096, 'x'), result(4096);
        CHECK(write(fd, bytes.data(), bytes.size()) == static_cast<ssize_t>(bytes.size()));
        close(fd);
        auto file = std::make_shared<Helper::AsyncFileIO>();
        const bool opened = file->Initialize(path, O_RDONLY, 64, 2, 2, 1);
        unlink(path);
        CHECK(opened);
        std::vector<std::shared_ptr<Helper::DiskIO>> handlers{file};
        Helper::AsyncReadRequest request;
        request.m_offset = 0;
        request.m_readSize = result.size();
        request.m_buffer = result.data();
        request.m_status = 0;
        request.m_callback = [](bool success) { CHECK(success); };
        COMMON::StepTiming::Query io(true);
        CHECK(Helper::BatchReadFileAsync(handlers, &request, 1));
        values = io.Finish();
        CHECK(result == bytes && values[COMMON::StepTiming::AIOSubmit] > 0 &&
              values[COMMON::StepTiming::AIOWait] > 0 && values[COMMON::StepTiming::AIOLock] > 0);
#else
        COMMON::StepTiming::Query disabled(false);
        const auto values = disabled.Finish();
        CHECK(std::accumulate(values.begin(), values.end(), std::uint64_t{0}) == 0);
#endif
        std::cout << "Query step timing checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
