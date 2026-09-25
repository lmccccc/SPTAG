#include "NativeSupplier.h"
#include <iostream>
#include <set>

void CheckAt(bool value, int line) {
    if (!value) throw std::runtime_error("Native-reuse fixture failed at line " + std::to_string(line));
}
#define Check(value) CheckAt((value), __LINE__)
struct TestPosting {
    std::vector<std::vector<std::uint32_t>> rows;
    const std::uint32_t* Begin(int i) const { return rows[i].data(); }
    const std::uint32_t* End(int i) const { return rows[i].data() + rows[i].size(); }
};
struct TestCatalog {
    SPTAG::BKT::Index<float>* index;
    int count;
    int Count() const { return count; }
    const void* GetVector(int id) const { return index->GetSample(id); }
};
int main() {
    using namespace SPTAG;
    constexpr int count = 128;
    BKT::Index<float> index;
    index.SetParameter("DistCalcMethod", "L2");
    index.SetParameter("NumberOfThreads", "1");
    index.SetParameter("MaxCheck", "2048");
    index.SetParameter("BKTKmeansK", "8");
    index.SetParameter("NeighborhoodSize", "32");
    index.SetParameter("TPTNumber", "1");
    std::vector<float> data(count * 8);
    for (int i = 0; i < count; ++i)
        for (int j = 0; j < 8; ++j) data[i * 8 + j] = float(i * (j + 1));
    Check(index.BuildIndex(data.data(), count, 8) == ErrorCode::Success);
    COMMON::QueryResultSet<float> original(data.data(), 24), observed(data.data(), 24);
    Check(index.SearchIndex(original) == ErrorCode::Success);
    COMMON::NativeNeighborHooks hook;
    hook.diagnostics = hook.capture = true;
    Check(index.SearchIndexWithNativeHooks(observed, hook, {}, {}) == ErrorCode::Success);
    Check(hook.originalDistanceFunction);
    for (int i = 0; i < 24; ++i)
        Check(original.GetResult(i)->VID == observed.GetResult(i)->VID &&
              original.GetResult(i)->Dist == observed.GetResult(i)->Dist);
    std::set<int> ownOnly;
    COMMON::NativeNeighborHooks admission;
    admission.diagnostics = true;
    admission.ownPoint = [&](int id, float distance) {
        Check(distance == index.ComputeDistance(data.data() + 9 * 8, index.GetSample(id)));
        if (id % 3 == 0 && id % 2 != 0) ownOnly.insert(id);
    };
    COMMON::QueryResultSet<float> postingsOnly(data.data() + 9 * 8, 24);
    Check(index.SearchIndexWithNativeHooks(postingsOnly, admission,
          [](int id) { return id % 2 == 0; },
          [](int id) { return id % 2 == 0 || id % 3 == 0; }) == ErrorCode::Success);
    Check(ownOnly.count(9) == 1);
    for (int i = 0; i < 24; ++i)
        Check(postingsOnly.GetResult(i)->VID < 0 || postingsOnly.GetResult(i)->VID % 2 == 0);
    COMMON::NativeNeighborHooks none;
    none.diagnostics = none.capture = true;
    COMMON::QueryResultSet<float> empty(data.data(), 24);
    Check(index.SearchIndexWithNativeHooks(empty, none, [](int) { return false; },
          [](int) { return false; }) == ErrorCode::Success);
    Check(none.headDistances == 0 && none.childDistances == 0 && empty.GetResult(0)->VID < 0);

    std::vector<TestPosting> postings(2);
    postings[0].rows.resize(16); postings[1].rows.resize(8);
    for (int p = 0; p < 16; ++p)
        for (int id = (p / 8) * 64; id < (p / 8 + 1) * 64; ++id)
            postings[0].rows[p].push_back(id);
    for (auto& row : postings[1].rows)
        for (int id = 0; id < 16; ++id) row.push_back(id);
    NativeReuse::PostingModel model;
    model.counts = {count, 16, 8};
    model.children = [&](int level, int id) -> NativeReuse::PostingModel::Row {
        return {postings[level - 1].Begin(id), postings[level - 1].End(id)};
    };
    model.BuildOwners();
    std::vector<std::shared_ptr<TestCatalog>> catalogs{
        std::make_shared<TestCatalog>(TestCatalog{&index, 16}),
        std::make_shared<TestCatalog>(TestCatalog{&index, 8})};
    for (auto boundary : std::vector<std::pair<int, int>>{
            {32,32}, {32,16}, {32,15}, {20,10}, {20,9}, {16,8}, {16,7},
            {15,15}, {15,1}, {0,0}, {17,8}, {17,9}}) {
        const auto [d, e] = boundary;
        for (int head = 0; head < count; ++head)
            for (int slot = 0; slot < index.GetNeighborhoodSize(); ++slot)
                index.GetMutableGraph()[head][slot] = slot < d ? 64 + slot : -1;
        ShortcutFull::mode = "supplier"; ShortcutFull::capture = true;
        ShortcutFull::profile = false;
        const auto allowed = [&](int id) { return id == 0 || (id >= 64 && id < 64 + e); };
        COMMON::QueryResultSet<float> query(data.data(), 24);
        Check(NativeReuse::Search(&index, &query, &model, catalogs, postings, allowed,
              [](int, const float*) { return false; }, allowed,
              [](int, int) { return false; }) == ErrorCode::Success);
        const auto& n = ShortcutFull::Last().native;
        Check(n.originalDistanceFunction && n.parentDistances == 0 && n.members == 0);
        bool saw = false;
        for (const auto& frame : n.frames) {
            Check(!frame.calls || (frame.physical >= 16 && 2 * frame.eligible < frame.physical));
            if (frame.head == 0) {
                saw = true;
                Check(frame.physical == d && frame.eligible == e);
                Check(bool(frame.calls) == (d >= 16 && 2 * e < d));
            }
        }
        Check(saw);
    }
    auto* row = index.GetMutableGraph()[0];
    row[0] = 64; row[1] = 64; row[2] = 0; row[3] = count + 9;
    row[4] = 65; row[5] = -1; row[6] = 66;
    COMMON::QueryResultSet<float> malformedTail(data.data(), 24);
    const auto twoAllowed = [](int id) { return id == 0 || id == 64; };
    Check(NativeReuse::Search(&index, &malformedTail, &model, catalogs, postings, twoAllowed,
          [](int, const float*) { return false; }, twoAllowed,
          [](int, int) { return false; }) == ErrorCode::Success);
    bool checkedRow = false;
    for (const auto& f : ShortcutFull::Last().native.frames)
        if (f.head == 0) {
            checkedRow = true;
            Check(f.physical == 2 && f.eligible == 1 && !f.calls);
        }
    Check(checkedRow);
    COMMON::NativeNeighborHooks rowHook;
    rowHook.capture = true;
    int checked = 0;
    COMMON::NativeEdgeConsumer native{[&](int, bool full) { Check(full); ++checked; },
        [&] { return checked < 1; }, [&] { return checked; }};
    NativeReuse::PostingSupplier supplier(model, rowHook,
        [](int, int) { return true; }, [](int, int id) { return float(id); });
    std::unordered_set<int> identities{0};
    COMMON::NativeDegreeFrame frame{};
    supplier.Supply(0, 1, identities, [](int) { return true; }, frame, native);
    Check(rowHook.calls == 1 && rowHook.returns == 1 && rowHook.members == 64 &&
          rowHook.h2Rows == 1 && checked == 63 && frame.supplied.size() == 63);
    BKT::Index<float> collapsed;
    collapsed.SetParameter("DistCalcMethod", "L2");
    collapsed.SetParameter("NumberOfThreads", "1");
    collapsed.SetParameter("MaxCheck", "2048");
    collapsed.SetParameter("BKTKmeansK", "8");
    collapsed.SetParameter("BKTLambdaFactor", "0");
    collapsed.SetParameter("NeighborhoodSize", "32");
    collapsed.SetParameter("TPTNumber", "1");
    constexpr int collapsedCount = 1024;
    std::vector<float> collapsedData(collapsedCount * 8);
    for (int i = 0; i < collapsedCount; ++i)
        for (int j = 0; j < 8; ++j) collapsedData[i * 8 + j] = float(j + 1);
    Check(collapsed.BuildIndex(collapsedData.data(), collapsedCount, 8) == ErrorCode::Success);
    int collapsedRows = 0;
    for (int i = 0; i < collapsedCount; ++i)
        collapsedRows += collapsed.GetGraph()[i][collapsed.GetNeighborhoodSize() - 1] < -1;
    Check(collapsedRows > 0);
    COMMON::NativeNeighborHooks ownGroup;
    std::set<int> groupOwn;
    ownGroup.ownPoint = [&](int id, float distance) {
        Check(distance == collapsed.ComputeDistance(collapsedData.data() + 9 * 8, collapsed.GetSample(id)));
        groupOwn.insert(id);
    };
    COMMON::QueryResultSet<float> groupResult(collapsedData.data() + 9 * 8, 24);
    Check(collapsed.SearchIndexWithNativeHooks(groupResult, ownGroup, [](int) { return false; },
          [](int) { return true; }) == ErrorCode::Success);
    Check(groupOwn.count(9) == 1 && groupResult.GetResult(0)->VID < 0);
    std::cout << "Native primitive/single-heap parity; predicate-before-terminal-distance; "
                 "ratio boundaries including startup/odd/short/empty; signature-before-distance; "
                 "complete row beyond deficit and native boundary passed\n";
}
