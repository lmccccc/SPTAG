#include <NativeAdapter.h>
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
        return 7;
    };
    auto allowed = [&] { return nativeOpen; };
    reset();
    Check(e.Rank(1, {0, 1, 2, 3, 4, 5, 6, 7}).front().second == 0);
    query = 7; reset();
    Check(e.Rank(1, {0, 1, 2, 3, 4, 5, 6, 7}).front().second == 7);
    query = n / 2 - 1; reset();
    e.Supply(0, 16, 16, 32, offer, allowed); Check(e.stats.calls == 0);
    e.Supply(0, 15, 15, 32, offer, allowed);
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
    reset(); e.Supply(0, 15, 15, 32, offer, allowed);
    Check(e.stats.signatureRejects == 7 && e.stats.h2Rows == 1);
    Check(realCalls[20] == 0 && e.stats.predicateRejects > 0);
    Check(e.stats.parent == 1 && e.stats.qualified == 31);
    for (int id = 1; id < 8; ++id) Check(e.postingState[0][id] == 3);
    signature = [](int, int) { return false; };
    reset(); e.Supply(0, 0, 0, 32, offer, allowed);
    Check(e.stats.Used() == 0 && e.stats.members == 0 && e.stats.signatureRejects == 16);
    signature = [](int level, int id) { return level == 2 || id >= 8; };
    qualify = [](int id) { return id >= 32 ? 3 : 0; };
    query = 39;
    reset(); e.Supply(0, 0, 0, 32, offer, allowed);
    Check(e.stats.h2Exhausted == 1 && e.stats.h3Rows == 1 && e.stats.h3Completed == 1);
    Check(e.stats.h2Completed == 1 && e.stats.qualified == 32);
    Check(e.stats.lowerMembers == 32 && e.stats.upperMembers == 16 && e.stats.members == 48);
    Check(e.stats.discovered == 8 && e.rowAudits[0][0] == 2 && e.rowAudits[0][2] == 16);
    Check(e.rowAudits[1][0] == 1 && e.rowAudits[1][1] == 15);
    const auto upper = static_cast<int>(e.rowAudits[0][1]);
    Check(e.postingState[1][upper] == 2 && e.UpperPending(upper));
    Check(e.discoveredH2[upper].front().second == 15);
    nativeOpen = true;
    e.Supply(0, 0, 0, 32, offer, allowed);
    Check(e.stats.discoveryReuse > 0 && e.stats.h2Completed == 8);
    Check(!e.UpperPending(upper) && e.stats.qualified == 32);
    Check(e.stats.lowerMembers == 8 * 32 && e.stats.upperMembers == 8 * 16);
    std::set<std::pair<int, int>> uniqueRows;
    for (const auto& row : e.rowAudits)
        Check(uniqueRows.emplace(row[0], row[1]).second && row[2] == row[3]);
    Check(!e.RowAllowed(1, 8));
    Check(e.stats.postingSkips > 0 && e.stats.calls == e.stats.returns && !e.inHelper);
}
void ConnectivityFixtures() {
    auto p = Postings(64);
    H1Supplier::Model m;
    m.counts = {64, 16, 8};
    for (int i = 0; i < 16; ++i) m.canonical[0].push_back(i < 8 ? i : 32 + i - 8);
    for (int i = 0; i < 8; ++i) m.canonical[1].push_back(i);
    m.children = [&](int l, int id) -> H1Supplier::Model::Row {
        return {p[l - 1].Begin(id), p[l - 1].End(id)};
    };
    m.BuildOwners();
    for (bool optimized : {false, true}) {
        H1Supplier::Engine e;
        e.optimized = optimized;
        int checked = 0, eligible = 32;
        bool signatures = false;
        std::array<bool, 64> flags{};
        const auto reset = [&] {
            checked = 0;
            e.Reset(m, true, true, true, [](int id) { return float(id * id); },
                    [](int, float) {}, [&](int id) { return flags[id] ? 3 : 0; },
                    [&](int, int) { return signatures; });
        };
        for (auto test : std::vector<std::array<int, 3>>{
                {32,32,0}, {32,16,0}, {32,15,1}, {20,10,0}, {20,9,1},
                {16,8,0}, {16,7,1}, {15,15,0}, {15,1,0}, {0,0,0},
                {17,8,1}, {17,9,0}, {31,15,1}, {31,16,0}}) {
            const int d = test[0], valid = test[1];
            for (int visitedCount : {0, 20, 32}) {
                flags.fill(false);
                for (int id = 1; id <= valid; ++id) flags[id] = true;
                reset();
                std::vector<int> row;
                for (int id = 1; id <= d; ++id) row.push_back(id);
                row.push_back(-1);
                {
                    H1Supplier::Connectivity c(e, 0, 0, row.data(), row.size(), &checked);
                    e.stats.ordinaryVisited = (std::min)(d, visitedCount);
                    Check(c.audit.physical == d && c.audit.before == valid);
                    Check(c.audit.required == (d < 16 ? 0 : (d + 1) / 2));
                    e.Supply(0, 0, c.audit.before, c.audit.physical,
                             [](int) { throw std::runtime_error("Signature-rejected scope scanned"); return 0; },
                             [] { return true; });
                    Check(e.stats.calls == static_cast<unsigned>(test[2]));
                    Check(e.stats.Used() == 0 && e.stats.members == 0);
                }
                Check(e.connectivityAudits.back().before == valid);
            }
        }
        flags.fill(true); reset();
        int malformed[] = {0, 1, 1, 64, 2, -2, 3};
        {
            H1Supplier::Connectivity c(e, 0, 0, malformed, 7, &checked);
            Check(c.audit.physical == 2 && c.audit.before == 2 && c.audit.required == 0);
            Check(c.audit.ordinary == std::vector<int>({1,2}) && c.audit.stopSlot == 5 &&
                  c.audit.terminator == -2);
            e.Supply(0, 0, c.audit.before, c.audit.physical, [](int) { return 0; }, [] { return true; });
            Check(e.stats.calls == 0);
        }
        signatures = true; flags.fill(true);
        for (int id = 8; id <= 16; ++id) flags[id] = false;
        reset();
        std::vector<int> row(16);
        std::iota(row.begin(), row.end(), 1);
        {
            H1Supplier::Connectivity c(e, 1, 0, row.data(), row.size(), &checked);
            Check(c.audit.physical == 16 && c.audit.before == 7 && c.audit.required == 8);
            e.Supply(0, 0, c.audit.before, c.audit.physical, [&](int id) {
                return c.AddSupplied(id) ? 4 : 0;
            }, [] { return true; });
            Check(e.stats.calls == 1 && e.stats.qualified == 0 && e.stats.child == 0);
            Check(e.stats.h2Completed == 1 && e.stats.h3Rows == 0 && e.stats.members == 32);
            Check(c.audit.after == 22 && !c.AddSupplied(17) && !c.AddSupplied(1) && !c.AddSupplied(0));
            Check(!e.RowAllowed(1, static_cast<int>(e.rowAudits[0][1])));
        }
        flags.fill(false); signatures = false; reset(); checked = 2048;
        {
            H1Supplier::Connectivity c(e, 1, 0, row.data(), row.size(), &checked);
            Check(c.audit.physical == 16 && c.audit.before == 0 && c.audit.checkedBefore == 2048);
            e.Supply(0, 0, c.audit.before, c.audit.physical, [](int) { return 0; }, [] { return false; });
            Check(e.stats.calls == 0);
        }
    }
}

void NativeFixtures() {
    using namespace SPTAG;
    BKT::Index<float> index;
    index.SetParameter("DistCalcMethod", "L2");
    index.SetParameter("NumberOfThreads", "1");
    index.SetParameter("MaxCheck", "128");
    index.SetParameter("BKTKmeansK", "8");
    index.SetParameter("NeighborhoodSize", "32");
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

    const int startupWidth = index.GetNeighborhoodSize();
    std::vector<int> beforeStartup(count * startupWidth);
    for (int id = 0; id < count; ++id)
        for (int slot = 0; slot < startupWidth; ++slot) {
            beforeStartup[id * startupWidth + slot] = index.GetGraph()[id][slot];
            index.GetMutableGraph()[id][slot] = (id + slot + 1) % count;
        }
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
          rejectedWork.supplyParent == 0 && rejectedWork.supplyCalls == 1);
    Check(rejectedWork.graphChecked == 128 && rejectedWork.supplyRejectedLeaves > 0 &&
          rejectedWork.supplyPredicateChecks < count);
    Check(rejectedWork.supplyStartupChecks == 1 && rejectedWork.supplyStartupBlocked == 0);
    index.SetParameter("MaxCheck", "1");
    ShortcutFull::Last() = {};
    COMMON::QueryResultSet<float> nativeBoundary(data.data() + 20 * 8, 24);
    H1Supplier::NativeSearch(&index, &nativeBoundary, catalogs, postings, maps,
        [](int) { return false; }, [](int, const float*) { return false; },
        [](int, bool) { return 0; }, [](int, int) { return false; });
    Check(ShortcutFull::Last().supplyStartupBlocked == 1 &&
          ShortcutFull::Last().supplyCalls == 0 && ShortcutFull::Last().graphChecked == 1);
    index.SetParameter("MaxCheck", "2048");
    ShortcutFull::mode = "control";
    ShortcutFull::Last() = {};
    COMMON::QueryResultSet<float> noSeed(data.data() + 20 * 8, 24);
    H1Supplier::NativeSearch(&index, &noSeed, catalogs, postings, maps,
        [](int) { return false; }, [](int, const float*) { return false; },
        [](int, bool) { return 0; }, [](int, int) { return false; });
    Check(ShortcutFull::Last().supplyEntryBefore == 0);
    const auto initialIDs = ShortcutFull::Last().entryIDs;
    int reachableMember = count - 1;
    while (reachableMember >= 0 &&
           std::find(initialIDs.begin(), initialIDs.end(), reachableMember) != initialIDs.end())
        --reachableMember;
    Check(reachableMember >= 0);
    std::vector<std::array<std::int64_t, 8>> startup;
    for (bool profile : {false, true}) {
        ShortcutFull::mode = "supplier"; ShortcutFull::profile = profile;
        ShortcutFull::Last() = {};
        COMMON::QueryResultSet<float> seeded(data.data() + 20 * 8, 24);
        H1Supplier::NativeSearch(&index, &seeded, catalogs, postings, maps,
            [&](int id) { return id == reachableMember; }, [](int, const float*) { return false; },
            [&](int id, bool post) { return (post ? 1 : 0) | (id == reachableMember ? 2 : 0); },
            [](int, int) { return true; });
        const auto& o = ShortcutFull::Last();
        Check(o.supplyEntryBefore == 0 && o.supplyStartupCalls == 1 && o.supplyStartupAfter > 0);
        Check(o.supplyStarts == 1 && o.supplyContinuationPops > 0 &&
              seeded.GetResult(0)->VID == reachableMember);
        Check(o.startupAudits.size() == 1 && o.startupAudits[0][0] >= 0);
        Check(std::find(initialIDs.begin(), initialIDs.end(), o.startupAudits[0][0]) != initialIDs.end());
        if (!startup.empty()) Check(startup == o.startupAudits);
        startup = o.startupAudits;
    }
    ShortcutFull::Last() = {};
    COMMON::QueryResultSet<float> allRejected(data.data() + 20 * 8, 24);
    H1Supplier::NativeSearch(&index, &allRejected, catalogs, postings, maps,
        [](int) { return false; }, [](int, const float*) { return false; },
        [](int, bool) { return 0; }, [](int, int) { return false; });
    const auto& empty = ShortcutFull::Last();
    Check(empty.supplyStarts == 1 && empty.supplyStartupCalls == 1 && empty.supplyStartupAfter == 0);
    Check(empty.supplyMembers == 0 && empty.supplyParent == 0 && empty.supplyChild == 0 &&
          empty.supplyPops == 0 && allRejected.GetResult(0)->VID == -1);
    index.SetParameter("MaxCheck", "128");
    for (int id = 0; id < count; ++id)
        for (int slot = 0; slot < startupWidth; ++slot)
            index.GetMutableGraph()[id][slot] = beforeStartup[id * startupWidth + slot];
    COMMON::QueryResultSet<float> restored(data.data() + 20 * 8, 24);
    Check(index.SearchIndex(restored) == ErrorCode::Success);
    for (int i = 0; i < 24; ++i)
        Check(original.GetResult(i)->VID == restored.GetResult(i)->VID &&
              original.GetResult(i)->Dist == restored.GetResult(i)->Dist);
    const int width = index.GetNeighborhoodSize();
    Check(width == 32);
    std::vector<int> saved(count * width);
    for (int id = 0; id < count; ++id)
        for (int slot = 0; slot < width; ++slot) {
            saved[id * width + slot] = index.GetGraph()[id][slot];
            index.GetMutableGraph()[id][slot] = (id + slot + 1) % count;
        }
    index.SetParameter("NumberOfInitialDynamicPivots", "1");
    for (bool profile : {false, true}) {
        ShortcutFull::mode = "supplier"; ShortcutFull::profile = profile;
        ShortcutFull::Last() = {};
        COMMON::QueryResultSet<float> connected(data.data() + 20 * 8, 24);
        H1Supplier::NativeSearch(&index, &connected, catalogs, postings, maps,
            [](int) { return true; }, [](int, const float*) { return false; },
            [](int, bool) { return 3; }, [](int, int) { return true; });
        const auto& o = ShortcutFull::Last();
        Check(o.supplyStarts == 1 && o.supplyEntryBefore < 16 &&
              o.supplyStartupConnected == 1 && o.supplyStartupCalls == 0 &&
              o.supplyCalls == 0 && o.supplyParent == 0 && o.supplyChild == 0);
        Check(o.supplyPops > 0 && !o.connectivityAudits.empty());
        for (const auto& a : o.connectivityAudits)
            Check(a.before == 32 && a.after == 32 && a.calls == 0);
    }

    for (int id = 0; id < count; ++id)
        for (int slot = 0; slot < width; ++slot)
            index.GetMutableGraph()[id][slot] = slot < 15 ? (id + slot + 1) % count : -1;
    ShortcutFull::Last() = {};
    COMMON::QueryResultSet<float> shortScope(data.data() + 20 * 8, 24);
    H1Supplier::NativeSearch(&index, &shortScope, catalogs, postings, maps,
        [](int) { return false; }, [](int, const float*) { return false; },
        [](int, bool) { return 0; }, [](int, int) { return true; });
    Check(ShortcutFull::Last().supplyCalls == 0 && ShortcutFull::Last().supplyMembers == 0 &&
          shortScope.GetResult(0)->VID == -1 && ShortcutFull::Last().supplyStarts == 1);
    for (int id = 0; id < count; ++id)
        for (int slot = 0; slot < width; ++slot)
            index.GetMutableGraph()[id][slot] = saved[id * width + slot];
}
int main() {
    SignatureFixtures(); SupplierFixtures(64); SupplierFixtures(8192); ConnectivityFixtures(); NativeFixtures();
    std::cout << "PASS: uncapped >3200 distances/>2048 members; signature-before-rank/scan; "
                 "DNF OR/numeric/domain fallback; predicate-before-distance; full >16-member row "
                 "including closer last member; no posting rescan; H3 row not atomic subtree; "
                 "query-ranked H2 choices retained and reused without upper rereads; "
                 "empty initial H1 seeded from reachable anchor; all-rejected scope terminates; "
                 "native row-boundary overshoot; one H1 frontier; negative sentinels; "
                 "count parity and restored original baseline\n";
    std::cout << "PASS: 32 eligible ordinary neighbors with 0/20/32 visited never supply; "
                 "ratio boundaries, short-row suppression, duplicates, self, invalid and sentinels; "
                 "visited supplied connectivity deduplicates without distance; "
                 "native connected startup expands ordinary edges without posting fallback\n";
}
