#include "NativeAdapter.h"
#include <iostream>
#include <numeric>
#include <set>

void CheckAt(bool ok, int line) {
    if (!ok) throw std::runtime_error("Full-row fixture failed at line " + std::to_string(line));
}
#define Check(condition) CheckAt((condition), __LINE__)
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
void SignatureFixtures() {
    using namespace SPTAG::SPANN;
    LimitedTagSupport support;
    Check(support.Initialize(32, 8, 1, 0, 1, 1));
    Check(support.SetTagVectorCounts(100, {{1, 40}, {2, 30}, {3, 30}}));
    std::vector<SecondLevelHeadPostings> layers(1);
    std::vector<std::uint64_t> offsets, maps;
    std::vector<std::uint32_t> members;
    std::vector<SecondLevelHeadPostings::Signature> signatures(8);
    for (int row = 0; row < 8; ++row) {
        offsets.push_back(members.size()); maps.push_back(row);
        for (int id = 0; id < 32; ++id) members.push_back(id);
        signatures[row].Clear(); signatures[row].Insert(row == 0 ? 2 : 3);
    }
    offsets.push_back(members.size());
    std::string error;
    Check(layers[0].Initialize(32, 8, 8, 1, 1, 0, 1, maps, offsets, members, signatures, &error));
    auto dnf = BuildHierarchyQuerySignature({1, 2}, support, layers);
    Check(H1Supplier::PostingMayMatch(dnf, layers[0], 0));
    auto one = BuildHierarchyQuerySignature({1}, support, layers);
    Check(!H1Supplier::PostingMayMatch(one, layers[0], 0));
    auto numeric = BuildHierarchyQuerySignature({}, support, layers);
    Check(!numeric.Popcount() && H1Supplier::PostingMayMatch(numeric, layers[0], 1));
    auto unrepresented = BuildHierarchyQuerySignature({1, 99}, support, layers);
    Check(!unrepresented.Popcount() && H1Supplier::PostingMayMatch(unrepresented, layers[0], 1));
}
void SupplierFixtures(int n) {
    auto p = Postings(n);
    H1Supplier::Model m;
    m.counts = {n, 16, 8};
    for (int i = 0; i < 16; ++i) m.canonical[0].push_back(i < 8 ? i : n / 2 + i - 8);
    for (int i = 0; i < 8; ++i) m.canonical[1].push_back(i);
    m.children = [&](int l, int id) -> H1Supplier::Model::Row {
        return {p[l - 1].Begin(id), p[l - 1].End(id)};
    };
    m.BuildOwners();
    H1Supplier::Engine e;
    std::set<int> visited, admitted;
    std::vector<int> realCalls(n);
    int query = 0, checked = 0;
    bool nativeOpen = true;
    std::function<int(int)> qualify = [](int) { return 3; };
    std::function<bool(int, int)> signature = [](int, int) { return true; };
    auto reset = [&] {
        visited.clear(); admitted.clear(); checked = 0; nativeOpen = true;
        std::fill(realCalls.begin(), realCalls.end(), 0);
        e.Reset(m, true, true, true,
            [&](int id) { ++realCalls[id]; return float((id - query) * (id - query)); },
            [&](int id, float) { Check(admitted.insert(id).second); }, qualify, signature);
    };
    auto offer = [&](int id) {
        if (!visited.insert(id).second) return 0;
        if (!e.Qualify(id)) return 1;
        e.Score(id, 2, false); ++checked; ++e.stats.queued;
        if (checked >= 20) nativeOpen = false;
        return 3;
    };
    auto allowed = [&] { return nativeOpen; };
    reset();
    Check(e.Rank(1, {0, 1, 2, 3, 4, 5, 6, 7}).front().second == 0);
    query = 7; reset();
    Check(e.Rank(1, {0, 1, 2, 3, 4, 5, 6, 7}).front().second == 7);
    query = n / 2 - 1; reset();
    e.Supply(0, 16, 16, offer, allowed); Check(e.stats.calls == 0);
    e.Supply(0, 15, 15, offer, allowed);
    Check(e.stats.requested == 1 && e.stats.qualified == static_cast<unsigned>(n / 2));
    Check(visited.count(query) && e.distances[query] == 0);
    Check(e.stats.h2Rows == 1 && e.stats.h2Completed == 1 && e.stats.h3Rows == 0 && !nativeOpen);
    if (n > 6400) {
        Check(e.stats.Used() > 3200 && e.stats.members > 2048);
        return;
    }
    const int selectedRow = static_cast<int>(e.rowAudits.front()[1]);
    const auto before = e.stats.Used();
    Check(!e.RowAllowed(1, selectedRow));
    Check(e.stats.Used() == before && e.stats.postingSkips == 1);
    Check(e.stats.h2Rows == 1 && e.stats.h2Completed == 1 && e.stats.h3Rows == 0);
    Check(e.stats.members == static_cast<unsigned>(n / 2) && checked == n / 2);
    Check(e.rowAudits[0][2] == e.rowAudits[0][3]);
    query = 0;
    signature = [](int level, int id) { return level == 1 && id == 0; };
    qualify = [](int id) { return id == 20 ? 0 : (id % 2 ? 1 : 2); };
    reset(); e.Supply(0, 15, 15, offer, allowed);
    Check(e.stats.signatureRejects == 7 && e.stats.h2Rows == 1);
    Check(realCalls[20] == 0 && e.stats.predicateRejects > 0);
    Check(e.stats.parent == 1 && e.stats.qualified == 31);
    for (int id = 1; id < 8; ++id) Check(e.postingState[0][id] == 3);
    signature = [](int, int) { return false; };
    reset(); e.Supply(0, 0, 0, offer, allowed);
    Check(e.stats.Used() == 0 && e.stats.members == 0 && e.stats.signatureRejects == 16);
    signature = [](int level, int id) { return level == 2 || id >= 8; };
    qualify = [](int id) { return id >= 32 ? 3 : 0; };
    reset(); e.Supply(0, 0, 0, offer, allowed);
    Check(e.stats.h2Exhausted == 1 && e.stats.h3Rows == 1 && e.stats.h3Completed == 1);
    Check(e.stats.h2Completed == 8 && e.stats.qualified == 32);
    Check(e.stats.members == 8 * 32 + 16);
    Check(e.rowAudits.back()[0] == 2 && e.rowAudits.back()[2] == 16 &&
          e.rowAudits.back()[3] == 16);
    std::set<std::pair<int, int>> uniqueRows;
    for (const auto& row : e.rowAudits)
        Check(uniqueRows.emplace(row[0], row[1]).second && row[2] == row[3]);
    Check(!e.RowAllowed(1, 8));
    Check(e.stats.postingSkips > 0 && e.stats.calls == e.stats.returns && !e.inHelper);
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
        for (int j = 0; j < 8; ++j) data[i * 8 + j] = float((i / 2 + j * 3) % 37);
    Check(index.BuildIndex(data.data(), count, 8) == ErrorCode::Success);
    auto postings = Postings(count);
    std::vector<std::vector<std::uint64_t>> maps(2);
    for (int i = 0; i < 16; ++i) maps[0].push_back(i < 8 ? i : count / 2 + i - 8);
    for (int i = 0; i < 8; ++i) maps[1].push_back(i);
    std::vector<std::shared_ptr<Catalog>> catalogs;
    for (int l = 0; l < 2; ++l) {
        auto c = std::make_shared<Catalog>(); c->index = &index;
        for (auto id : maps[l]) c->ids.push_back(l == 0 ? id : maps[0][id]);
        catalogs.push_back(c);
    }
    COMMON::QueryResultSet<float> original(data.data() + 20 * 8, 24);
    Check(index.SearchIndex(original) == ErrorCode::Success);
    std::uint64_t sentinels = 0;
    for (std::string mode : {"control", "supplier"})
        for (bool filter : {false, true}) {
            std::uint64_t trace = 0;
            std::vector<std::pair<int, float>> evaluated;
            for (bool profile : {false, true}) {
                ShortcutFull::mode = mode; ShortcutFull::profile = profile; ShortcutFull::capture = true;
                ShortcutFull::Last() = {};
                std::set<int> own;
                COMMON::QueryResultSet<float> result(data.data() + 20 * 8, 24);
                H1Supplier::NativeSearch(&index, &result, catalogs, postings, maps,
                    [&](int id) { return !filter || id % 2 == 0; },
                    [&](int id, const float* d) { Check(d && own.insert(id).second); return false; },
                    [&](int id, bool post) { return (post ? 1 : 0) | ((!filter || id % 3 == 0) ? 2 : 0); },
                    [](int, int) { return true; });
                const auto& o = ShortcutFull::Last();
                Check(o.supplyStarts == 1 && o.supplyCalls == o.supplyReturns);
                Check(o.supplyH2Rows == o.supplyH2Completed && o.supplyH3Rows == o.supplyH3Completed);
                Check(own.size() == o.evaluated.size());
                if (trace) Check(trace == o.supplyTrace && evaluated == o.evaluated);
                trace = o.supplyTrace; evaluated = o.evaluated;
                if (profile) Check(o.calls == o.supplyGraph + o.supplyRouting + o.supplyParent + o.supplyChild);
                sentinels += o.supplySentinels;
                for (int i = 0; i < result.GetResultNum(); ++i)
                    Check(result.GetResult(i)->VID < 0 || !filter || result.GetResult(i)->VID % 2 == 0);
            }
        }
    Check(sentinels > 0);
    ShortcutFull::mode = "supplier";
    ShortcutFull::profile = true;
    ShortcutFull::capture = true;
    ShortcutFull::Last() = {};
    COMMON::QueryResultSet<float> rejected(data.data() + 20 * 8, 24);
    H1Supplier::NativeSearch(&index, &rejected, catalogs, postings, maps,
        [](int) { return false; }, [](int, const float*) { return false; },
        [](int, bool) { return 0; }, [](int, int) { return false; });
    const auto& rejectedWork = ShortcutFull::Last();
    Check(rejectedWork.supplyGraph == 0 && rejectedWork.supplyChild == 0 &&
          rejectedWork.supplyParent == 0 && rejectedWork.supplyCalls == 0);
    Check(rejectedWork.graphChecked == 128 && rejectedWork.supplyRejectedLeaves > 0 &&
          rejectedWork.supplyPredicateChecks < count);
    COMMON::QueryResultSet<float> restored(data.data() + 20 * 8, 24);
    Check(index.SearchIndex(restored) == ErrorCode::Success);
    for (int i = 0; i < 24; ++i)
        Check(original.GetResult(i)->VID == restored.GetResult(i)->VID &&
              original.GetResult(i)->Dist == restored.GetResult(i)->Dist);
}
int main() {
    SignatureFixtures(); SupplierFixtures(64); SupplierFixtures(8192); NativeFixtures();
    std::cout << "PASS: uncapped >3200 distances/>2048 members; signature-before-rank/scan; "
                 "DNF OR/numeric/domain fallback; predicate-before-distance; full >16-member row "
                 "including closer last member; no posting rescan; H2 then full H3 children; "
                 "native row-boundary overshoot; one H1 frontier; negative sentinels; "
                 "count parity and restored original baseline\n";
}
