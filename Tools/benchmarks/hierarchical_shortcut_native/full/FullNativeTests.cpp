#include "FullHooks.h"
#include "inc/Core/BKT/Index.h"
#include "inc/Core/Common/QueryResultSet.h"
#include <filesystem>
#include <iostream>

using namespace SPTAG;

void Require(bool condition) {
    if (!condition) throw std::runtime_error("Full native fixture failed");
}
template<class Function> void Reject(Function function) {
    bool rejected = false;
    try { function(); } catch (const std::exception&) { rejected = true; }
    Require(rejected);
}

int main() {
    Require(ShortcutFull::Unsigned("2000") == 2000);
    for (const auto* invalid : {"", "-1", "+1", " 1", "1x"})
        Reject([&] { ShortcutFull::Unsigned(invalid); });
    Require(ShortcutFull::Boolean("true") && !ShortcutFull::Boolean("false"));
    Reject([] { ShortcutFull::Boolean("yes"); });
    Reject([] { ShortcutFull::Configure(std::map<std::string, std::string>{}); });
    Reject([] { ShortcutFull::LoadEdges("missing-shortcut-fixture.bin"); });

    const std::string file = "full-hook-fixture.bin";
    Require(!std::filesystem::exists(file));
    struct Cleanup {
        std::string file;
        ~Cleanup() { std::filesystem::remove(file); }
    } cleanup{file};
    const auto writeOverlay = [&](int target, bool trailing, bool truncated) {
        std::ofstream out(file, std::ios::binary);
        auto write = [&](const auto* p, std::size_t n) {
            out.write(reinterpret_cast<const char*>(p), n * sizeof(*p));
        };
        const int n = 160091, bound = 8;
        out.write("H13EDGE1", 8); write(&n, 1); write(&bound, 1);
        std::vector<std::uint64_t> offsets(n + 1, 1);
        offsets[0] = 0;
        write(offsets.data(), offsets.size());
        if (!truncated) write(&target, 1);
        if (trailing) out.put('x');
    };
    writeOverlay(1, false, false);
    ShortcutFull::LoadEdges(file);
    Require(ShortcutFull::edges[0] == std::vector<int>{1});
    for (int invalid : {-1, 0, 160091}) {
        writeOverlay(invalid, false, false);
        Reject([&] { ShortcutFull::LoadEdges(file); });
    }
    writeOverlay(1, true, false);
    Reject([&] { ShortcutFull::LoadEdges(file); });
    writeOverlay(1, false, true);
    Reject([&] { ShortcutFull::LoadEdges(file); });

    BKT::Index<float> index;
    index.SetParameter("DistCalcMethod", "L2");
    index.SetParameter("NumberOfThreads", "1");
    index.SetParameter("MaxCheck", "64");
    index.SetParameter("BKTKmeansK", "8");
    index.SetParameter("NeighborhoodSize", "16");
    index.SetParameter("TPTNumber", "1");
    std::vector<float> data(128 * 8);
    for (int i = 0; i < 128; ++i)
        for (int j = 0; j < 8; ++j)
            data[i * 8 + j] = (i * 13 + j * 19 + i * j) % 251;
    Require(index.BuildIndex(data.data(), 128, 8) == ErrorCode::Success);
    std::vector<std::vector<int>> overlay(128);
    for (int i = 0; i < 128; ++i) overlay[i] = {(i + 64) % 128, (i + 65) % 128};
    using Results = std::vector<std::pair<int, float>>;
    const auto search = [&](int budget, int policy, bool profile) {
        ShortcutBench::State state;
        state.remaining = budget;
        state.profile = profile;
        state.rewire = policy == 2;
        if (policy) state.edges = &overlay;
        ShortcutBench::active = &state;
        auto original = index.BenchmarkDistance(budget != 0);
        COMMON::QueryResultSet<float> query(data.data() + 3 * 8, 24);
        Require(index.SearchIndex(query) == ErrorCode::Success);
        index.BenchmarkRestore(std::move(original));
        ShortcutBench::active = nullptr;
        if (profile && budget) Require(state.used == budget - state.remaining);
        if (state.exhausted) Require(state.remaining == 0);
        Results result;
        for (int i = 0; i < 24; ++i)
            result.emplace_back(query.GetResult(i)->VID, query.GetResult(i)->Dist);
        return result;
    };
    const Results baseline = search(0, 0, true);
    for (int policy = 0; policy < 3; ++policy)
        for (int budget : {1, 8, 64, 100000}) {
            Require(search(budget, policy, false) == search(budget, policy, true));
            Require(search(0, 0, true) == baseline);
        }
    COMMON::QueryResultSet<float> restored(data.data() + 3 * 8, 24);
    Require(index.SearchIndex(restored) == ErrorCode::Success);
    for (int i = 0; i < 24; ++i)
        Require(baseline[i] == std::make_pair(restored.GetResult(i)->VID, restored.GetResult(i)->Dist));
    std::cout << "PASS: malformed overlay/controls, native caps 1/8/64/100000, "
                 "all-policy counter parity, callback accounting, workspace and callback restoration\n";
}
