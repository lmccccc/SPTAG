#include "NativeAdapter.h"
#include <iostream>
#include <memory>
#include <set>

void CheckAt(bool b, int line) {
    if (!b) throw std::runtime_error("Supplier fixture failed at line " + std::to_string(line));
}
#define Check(...) CheckAt((__VA_ARGS__), __LINE__)
struct Posting {
    std::vector<std::vector<std::uint32_t>> rows;
    int ReplicaCount() const { return 8; }
    const std::uint32_t* Begin(int id) const { return rows[id].data(); }
    const std::uint32_t* End(int id) const { return rows[id].data() + rows[id].size(); }
};
struct Catalog {
    SPTAG::BKT::Index<float>* index;
    std::vector<int> ids;
    int Count() const { return static_cast<int>(ids.size()); }
    const void* GetVector(int id) const { return index->GetSample(ids[id]); }
};
std::vector<Posting> Postings(int n) {
    std::vector<Posting> p(2);
    p[0].rows.resize(16); p[1].rows.resize(8);
    for (int row = 0; row < 16; ++row)
        for (int id = (row / 8) * (n / 2); id < (row / 8 + 1) * (n / 2); ++id)
            p[0].rows[row].push_back(id);
    for (auto& row : p[1].rows) {
        for (int id = 8; id < 16; ++id) row.push_back(id);
        for (int id = 0; id < 8; ++id) row.push_back(id);
    }
    return p;
}
void SupplierFixtures() {
    auto p = Postings(64);
    H1Supplier::Model m;
    m.counts = {64, 16, 8};
    for (int i = 0; i < 16; ++i) m.canonical[0].push_back(i < 8 ? i : 32 + i - 8);
    for (int i = 0; i < 8; ++i) m.canonical[1].push_back(i);
    m.children = [&](int l, int id) -> H1Supplier::Model::Row { return {p[l - 1].Begin(id), p[l - 1].End(id)}; };
    m.BuildOwners();
    H1Supplier::Engine e;
    int q = 0;
    std::set<int> admitted;
    auto reset = [&](int cap) {
        admitted.clear();
        e.Reset(m, cap, true, true, true,
                [&](int id) { return float((id - q) * (id - q)); },
                [&](int id, float) { Check(admitted.insert(id).second); });
    };
    reset(2000);
    auto order = e.Rank(1, {0, 1, 2, 3, 4, 5, 6, 7});
    Check(order.front().second == 0 && order.back().second == 7);
    q = 7; reset(2000);
    order = e.Rank(1, {0, 1, 2, 3, 4, 5, 6, 7});
    Check(order.front().second == 7 && order.back().second == 0);
    auto top = e.Rank(2, {0, 1, 2, 3, 4, 5, 6, 7});
    Check(top.front().second == 7);
    q = 0; reset(2000);
    Check(e.Rank(2, {0, 1, 2, 3, 4, 5, 6, 7}).front().second == 0);
    std::set<int> visited;
    for (int i = 0; i < 32; ++i) visited.insert(i);
    auto offer = [&](int id) {
        if (!visited.insert(id).second) return false;
        e.Score(id, 2, false); ++e.stats.queued; return true;
    };
    e.Supply(0, offer);
    Check(e.stats.neighbors == 8 && e.stats.h3Rows > 0 && e.stats.ascents == 1);
    Check(e.stats.calls == e.stats.returns && !e.inHelper);
    Check(e.stats.maxMembers <= 72 && e.stats.maxNeighbors <= 8 && e.stats.cache > 0);
    auto cursors = e.cursors;
    e.Supply(0, offer);
    for (int l = 0; l < 2; ++l)
        for (std::size_t i = 0; i < cursors[l].size(); ++i) Check(e.cursors[l][i] >= cursors[l][i]);
    Check(e.stats.maxMembers <= 72 && e.stats.maxNeighbors <= 8);
    reset(2);
    e.Score(0, 0, true); e.Score(0, 0, true);
    Check(e.stats.Used() == 2 && e.stats.repeats == 1 && admitted.size() == 1);
    Check(e.Score(0, 1, false) == 0 && e.stats.Used() == 2 && e.stats.cache == 1);
    bool rejected = false;
    try { e.Score(1, 1, false); } catch (const H1Supplier::BudgetExhausted&) { rejected = true; }
    Check(rejected);
    e.Supply(0, offer); Check(e.stats.calls == 0);
}
void NativeFixtures() {
    using namespace SPTAG;
    BKT::Index<float> index;
    index.SetParameter("DistCalcMethod", "L2");
    index.SetParameter("NumberOfThreads", "1");
    index.SetParameter("MaxCheck", "128");
    index.SetParameter("BKTKmeansK", "8");
    index.SetParameter("NeighborhoodSize", "16");
    index.SetParameter("TPTNumber", "1");
    constexpr int count = 1024;
    std::vector<float> data(count * 8);
    for (int i = 0; i < count; ++i)
        for (int j = 0; j < 8; ++j) data[i * 8 + j] = ((i / 16) * 13 + j * 19 + (i / 16) * j) % 251;
    Check(index.BuildIndex(data.data(), count, 8) == ErrorCode::Success);
    auto postings = Postings(count);
    std::vector<std::vector<std::uint64_t>> maps(2);
    std::vector<std::shared_ptr<Catalog>> catalogs;
    for (int i = 0; i < 16; ++i) maps[0].push_back(i < 8 ? i : count / 2 + i - 8);
    for (int i = 0; i < 8; ++i) maps[1].push_back(i);
    for (int l = 0; l < 2; ++l) {
        auto catalog = std::make_shared<Catalog>(); catalog->index = &index;
        for (auto id : maps[l]) catalog->ids.push_back(l == 0 ? id : maps[0][id]);
        catalogs.push_back(catalog);
    }
    COMMON::QueryResultSet<float> original(data.data() + 20 * 8, 24);
    Check(index.SearchIndex(original) == ErrorCode::Success);
    std::uint64_t sentinels = 0;
    for (std::string mode : {"control", "supplier"})
        for (int cap : {1, 8, 64, 2000}) {
            std::uint64_t spatialTrace = 0;
            std::vector<std::pair<int, float>> evaluated;
            for (bool filter : {false, true})
                for (bool profile : {false, true}) {
                    ShortcutFull::mode = mode; ShortcutFull::cap = cap;
                    ShortcutFull::profile = profile; ShortcutFull::capture = true;
                    ShortcutFull::Last() = {};
                    std::set<int> own;
                    COMMON::QueryResultSet<float> result(data.data() + 20 * 8, 24);
                    H1Supplier::NativeSearch(&index, &result, catalogs, postings, maps,
                        [&](int id) { return !filter || id % 2 == 0; },
                        [&](int id, const float* d) { Check(d && own.insert(id).second); return false; });
                    const auto& o = ShortcutFull::Last();
                    Check(o.supplyStarts == 1 && o.supplyCalls == o.supplyReturns);
                    Check(o.supplyGraph + o.supplyParent + o.supplyChild <= static_cast<unsigned>(cap));
                    Check(own.size() == o.evaluated.size());
                    if (spatialTrace) Check(o.supplyTrace == spatialTrace && o.evaluated == evaluated);
                    spatialTrace = o.supplyTrace; evaluated = o.evaluated;
                    if (profile) Check(o.calls == o.supplyGraph + o.supplyParent + o.supplyChild);
                    sentinels += o.supplySentinels;
                    for (int i = 0; i < result.GetResultNum(); ++i)
                        Check(result.GetResult(i)->VID < 0 || !filter || result.GetResult(i)->VID % 2 == 0);
                }
        }
    Check(sentinels > 0);
    COMMON::QueryResultSet<float> restored(data.data() + 20 * 8, 24);
    Check(index.SearchIndex(restored) == ErrorCode::Success);
    for (int i = 0; i < 24; ++i)
        Check(original.GetResult(i)->VID == restored.GetResult(i)->VID &&
              original.GetResult(i)->Dist == restored.GetResult(i)->Dist);
}
int main() {
    SupplierFixtures(); NativeFixtures();
    std::cout << "PASS: query-ranked eight owners at both layers, H3 neighbor supply, quotas/cursors, "
                 "canonical parent admission/cache, repeated real calls, unified caps, one native H1 "
                 "search, negative sentinels, predicate/count parity and restored original baseline\n";
}
