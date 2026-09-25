#include <NativeSupplier.h>
#include "inc/Core/Common/VersionLabel.h"
#include <iostream>
#include <limits>
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
void FusedParity(SPTAG::BKT::Index<float>& index, const float* target) {
    using namespace SPTAG;
    const auto posting = [](int id) { return id % 4 == 0; };
    const auto traversal = [&](int id) { return posting(id) || id % 3 == 0; };
    for (int budget : {1, 2, 8, 32, 2048}) {
        COMMON::QueryResultSet<float> baseline(target, 24), fused(target, 24);
        COMMON::NativeNeighborHooks old, next;
        old.capture = next.capture = old.diagnostics = next.diagnostics = true;
        old.auditVisited = next.auditVisited = true;
        std::vector<std::pair<int, float>> ownOld, ownNext;
        old.ownPoint = [&](int id, float distance) { ownOld.emplace_back(id, distance); };
        next.ownPoint = [&](int id, float distance) { ownNext.emplace_back(id, distance); };
        next.qualify = [&](int id) {
            const bool p = posting(id);
            return COMMON::NativeEligibility{p || id % 3 == 0, p, true};
        };
        next.pureQualification = true;
        auto noExpand = [](int, const int*, int, bool, const COMMON::NativeEdgeConsumer&) {};
        auto complete = [&](COMMON::NativeDegreeFrame& frame, const int* row, int width,
                                    const COMMON::NativeEdgeConsumer&) {
            int d = 0, e = 0;
            for (int i = 0; i < width && row[i] >= 0; ++i) {
                if (row[i] >= index.GetNumSamples() || row[i] == frame.head ||
                    std::find(row, row + i, row[i]) != row + i) continue;
                ++d;
                e += traversal(row[i]);
            }
            Check(frame.physical == d && frame.eligible == e);
        };
        next.expand = noExpand;
        next.completeOrdinary = complete;
        Check(index.SearchIndexWithNativeHooks(baseline, old, posting, traversal, budget) == ErrorCode::Success);
        Check(index.SearchIndexWithNativeHooks(fused, next, posting, traversal, budget) == ErrorCode::Success);
        Check(old.visitedEdges == next.visitedEdges);
        Check(ownOld == ownNext && old.checked == next.checked && old.evaluated == next.evaluated);
        Check(old.queueOffers == next.queueOffers && old.queueAccepted == next.queueAccepted &&
              old.queueRejected == next.queueRejected);
        for (int i = 0; i < 24; ++i)
            Check(baseline.GetResult(i)->VID == fused.GetResult(i)->VID &&
                  baseline.GetResult(i)->Dist == fused.GetResult(i)->Dist);
    }
}
int main() {
    using namespace SPTAG;
    COMMON::OptHashPosVector qualifications;
    qualifications.Init(16, 2);
    qualifications.EnableNativeQualification(true);
    unsigned char flags = 0;
    Check(!qualifications.GetNativeQualification(1, flags) && !qualifications.Contains(1));
    for (int id = 0; id < 40; ++id) {
        qualifications.CheckAndSet(id);
        const COMMON::NativeEligibility value{bool(id % 2), bool(id % 3), true, true, bool(id % 5)};
        qualifications.PutNativeQualification(id, value.Bits());
    }
    const auto initialSlots = qualifications.NativeQualificationSlots();
    qualifications.DoubleSize();
    Check(qualifications.NativeQualificationSlots() == 2 * initialSlots);
    for (int id = 0; id < 40; ++id) {
        Check(qualifications.GetNativeQualification(id, flags));
        const COMMON::NativeEligibility value{bool(id % 2), bool(id % 3), true, true, bool(id % 5)};
        Check(flags == value.Bits());
    }
    qualifications.clear();
    Check(!qualifications.GetNativeQualification(1, flags) && !qualifications.Contains(1));
    qualifications.EnableNativeQualification(false);
    Check(qualifications.NativeQualificationSlots() == 0);
    int deficitScratch[4];
    COMMON::NativeDeficitIdentities deficitIDs(deficitScratch, 4, false);
    deficitIDs.Seed(0); deficitIDs.Seed(1);
    int deficitAdded = 0, consumedDeficitMembers = 0, selectedRows = 0;
    for (const auto& row : std::vector<std::vector<int>>{{2, 3}, {2, 3, 4, 5, 6}, {7, 8}}) {
        ++selectedRows;
        for (int id : row) {
            ++consumedDeficitMembers;
            if (deficitIDs.NeedsQualification(id, deficitAdded, 2) && id >= 3 && id <= 5) {
                deficitIDs.Accept(id, deficitAdded, 2);
                ++deficitAdded;
            }
        }
        if (deficitAdded >= 2) break;
    }
    Check(deficitAdded == 2 && selectedRows == 2 && consumedDeficitMembers == 7 && deficitIDs.Stored() == 4);
    auto referenceCallable = [] {};
    static_assert(!std::is_constructible<COMMON::NativeFunctionRef<void()>,
                  decltype(referenceCallable)&&>::value, "Function views must reject temporaries");
    using NativeReuse::AllHeadsOwnValid;
    using NativeReuse::DefaultAdmissionAllowed;
    COMMON::VersionLabel versions;
    versions.Initialize(3, 8, 8);
    auto allLive = [&] { return AllHeadsOwnValid(3, [&](int id) { return !versions.Deleted(id); }); };
    Check(allLive());
    versions.SetVersion(1, 0xfe);
    Check(!allLive());
    Check(!AllHeadsOwnValid(0, [](int) { return true; }));
    Check(!AllHeadsOwnValid(3, [](int id) { return id != 2; }));
    Check(DefaultAdmissionAllowed(false, true, true, 24, 10, .5, 16));
    Check(DefaultAdmissionAllowed(false, true, true, 24, 10, 1, 16));
    Check(!DefaultAdmissionAllowed(true, true, true, 24, 10, .5, 16));
    Check(!DefaultAdmissionAllowed(false, false, true, 24, 10, .5, 16));
    Check(!DefaultAdmissionAllowed(false, true, false, 24, 10, .5, 16));
    Check(!DefaultAdmissionAllowed(false, true, true, 9, 10, .5, 16));
    Check(!DefaultAdmissionAllowed(false, true, true, 24, 10, 1.01, 16));
    Check(!DefaultAdmissionAllowed(false, true, true, 24, 10,
                                   std::numeric_limits<double>::quiet_NaN(), 16));
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
    FusedParity(index, data.data() + 9 * 8);
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
    COMMON::QueryResultSet<float> bounded(data.data(), 1);
    const auto initialPivots = index.GetParameter("NumberOfInitialDynamicPivots");
    index.SetParameter("NumberOfInitialDynamicPivots", "1");
    COMMON::NativeNeighborHooks boundHook;
    std::set<int> notified, qualified;
    boundHook.ownPoint = [&](int id, float) { notified.insert(id); };
    Check(index.SearchIndexWithNativeHooks(bounded, boundHook,
          [&](int id) { qualified.insert(id); return true; }, [](int) { return true; }) == ErrorCode::Success);
    Check(bounded.GetResult(0)->VID == 0 && bounded.GetResult(0)->Dist == 0);
    Check(notified.size() > qualified.size());
    index.SetParameter("NumberOfInitialDynamicPivots", initialPivots.c_str());
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
    const auto beforeBatchPivots = index.GetParameter("NumberOfInitialDynamicPivots");
    index.SetParameter("NumberOfInitialDynamicPivots", "1");
    bool batchExceededBoundary = false, batchReusedQualification = false;
    for (int budget : {1, 2, 8, 32, 2048}) {
        COMMON::NativeNeighborHooks scalar, batch;
        scalar.capture = batch.capture = scalar.diagnostics = batch.diagnostics = true;
        scalar.auditVisited = batch.auditVisited = true;
        std::vector<std::pair<int, float>> scalarOwn, batchOwn;
        scalar.ownPoint = [&](int id, float d) { scalarOwn.emplace_back(id, d); };
        batch.ownPoint = [&](int id, float d) { batchOwn.emplace_back(id, d); };
        const auto posting = [](int id) { return id % 4 == 0; };
        const auto traversal = [&](int id) { return posting(id) || id % 3 == 0; };
        batch.qualify = [&](int id) {
            return COMMON::NativeEligibility{traversal(id), posting(id), true};
        };
        batch.pureQualification = true;
        auto allowRow = [](int, int) { return true; };
        auto parentDistance = [](int, int id) { return float(id); };
        NativeReuse::PostingSupplier scalarSupply(model, scalar, allowRow, parentDistance);
        NativeReuse::PostingSupplier batchSupply(model, batch, allowRow, parentDistance);
        const auto supplyRow = [&](NativeReuse::PostingSupplier& supplier, bool useBatch, int head,
                                   const COMMON::NativeEdgeConsumer& native) {
            auto consumer = native;
            if (!useBatch) consumer.consumeRow = {};
            int scratch[65];
            COMMON::NativeDeficitIdentities ids(scratch, 65, true);
            ids.Seed(head);
            COMMON::NativeDegreeFrame frame{};
            supplier.Supply(head, 1, ids, traversal, frame, consumer);
        };
        auto scalarExpand = [&](int head, const int*, int, bool, const COMMON::NativeEdgeConsumer& native) {
            supplyRow(scalarSupply, false, head, native);
        };
        auto batchExpand = [&](int head, const int*, int, bool, const COMMON::NativeEdgeConsumer& native) {
            supplyRow(batchSupply, true, head, native);
        };
        scalar.expand = scalarExpand;
        batch.expand = batchExpand;
        COMMON::QueryResultSet<float> scalarResult(data.data() + 9 * 8, 24), batchResult(data.data() + 9 * 8, 24);
        Check(index.SearchIndexWithNativeHooks(scalarResult, scalar, posting, traversal, budget) == ErrorCode::Success);
        Check(index.SearchIndexWithNativeHooks(batchResult, batch, posting, traversal, budget) == ErrorCode::Success);
        Check(scalar.visitedEdges == batch.visitedEdges && scalarOwn == batchOwn);
        Check(scalar.checked == batch.checked && scalar.evaluated == batch.evaluated);
        Check(scalar.members == batch.members && scalar.h2Rows == batch.h2Rows);
        Check(scalar.queueOffers == batch.queueOffers && scalar.queueAccepted == batch.queueAccepted);
        if (batch.batchRows) {
            batchExceededBoundary |= batch.checked > budget;
            batchReusedQualification |= batch.csrTraversalReuse > 0;
            for (const auto& completed : batch.rows) Check(completed.size == completed.consumed);
        }
        for (int i = 0; i < 24; ++i)
            Check(scalarResult.GetResult(i)->VID == batchResult.GetResult(i)->VID &&
                  scalarResult.GetResult(i)->Dist == batchResult.GetResult(i)->Dist);
    }
    Check(batchExceededBoundary && batchReusedQualification);
    index.SetParameter("NumberOfInitialDynamicPivots", beforeBatchPivots.c_str());
    std::vector<std::shared_ptr<TestCatalog>> catalogs{
        std::make_shared<TestCatalog>(TestCatalog{&index, 16}),
        std::make_shared<TestCatalog>(TestCatalog{&index, 8})};
    COMMON::QueryResultSet<float> nativeOwn(data.data() + 9 * 8, 24);
    Check(index.SearchIndex(nativeOwn) == ErrorCode::Success);
    Check(nativeOwn.GetResult(0)->VID == 9 && nativeOwn.GetResult(0)->Dist == 0);
    for (bool capture : {false, true}) {
        ShortcutFull::mode = "supplier";
        ShortcutFull::capture = capture;
        ShortcutFull::profile = false;
        COMMON::QueryResultSet<float> defaults(data.data() + 9 * 8, 24);
        Check(NativeReuse::Search(&index, &defaults, &model, catalogs, postings,
              [](int) { Check(false); return false; },
              [](int, const float*) { Check(false); return false; },
              [](int) { Check(false); return true; },
              [](int, int) { Check(false); return true; }, true) == ErrorCode::Success);
        const auto& n = ShortcutFull::Last().native;
        if (capture) Check(n.defaultAdmission && !n.filteredAdmission && n.rowEligibilityEvaluations == 0 &&
              n.calls == 0 && n.members == 0 && n.parentDistances == 0 &&
              n.checked == nativeOwn.GetScanned());
        for (int i = 0; i < 24; ++i)
            Check(defaults.GetResult(i)->VID == nativeOwn.GetResult(i)->VID &&
                  defaults.GetResult(i)->Dist == nativeOwn.GetResult(i)->Dist);
        if (capture) for (const auto& f : n.frames) Check(f.physical == f.eligible && !f.calls);
        Check(defaults.GetScanned() == nativeOwn.GetScanned());
    }
    for (auto boundary : std::vector<std::pair<int, int>>{
            {32,32}, {32,16}, {32,15}, {32,0}, {20,10}, {20,9}, {16,8}, {16,7},
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
    FusedParity(index, data.data());
    COMMON::NativeNeighborHooks rowHook;
    rowHook.capture = true;
    int checked = 0;
    auto consume = [&](int, bool full) { Check(full); ++checked; };
    auto within = [&] { return checked < 1; };
    auto checkedCount = [&] { return checked; };
    COMMON::NativeEdgeConsumer native{consume, within, checkedCount};
    auto allow = [](int, int) { return true; };
    auto parentDist = [](int, int id) { return float(id); };
    NativeReuse::PostingSupplier supplier(model, rowHook, allow, parentDist);
    int identityScratch[65];
    COMMON::NativeDeficitIdentities identities(identityScratch, 65, true);
    identities.Seed(0);
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
    FusedParity(collapsed, collapsedData.data());
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
    for (int budget : {1, 8, 2048}) {
        COMMON::QueryResultSet<float> tied(collapsedData.data(), 24), tiedObserved(collapsedData.data(), 24);
        Check(collapsed.SearchIndexWithMaxCheck(tied, budget) == ErrorCode::Success);
        COMMON::NativeNeighborHooks defaults;
        defaults.diagnostics = true;
        Check(collapsed.SearchIndexWithNativeHooks(tiedObserved, defaults, {}, {}, budget) == ErrorCode::Success);
        Check(defaults.checked == tied.GetScanned() && !defaults.filteredAdmission);
        for (int i = 0; i < 24; ++i)
            Check(tied.GetResult(i)->VID == tiedObserved.GetResult(i)->VID &&
                  tied.GetResult(i)->Dist == tiedObserved.GetResult(i)->Dist);
    }
    Check(AllHeadsOwnValid(count, [&](int id) { return index.ContainSample(id); }));
    Check(index.DeleteIndex(SizeType(9)) == ErrorCode::Success);
    Check(index.GetNumDeleted() == 1 &&
          !AllHeadsOwnValid(count, [&](int id) { return index.ContainSample(id); }));
    FusedParity(index, data.data() + 9 * 8);
    std::cout << "Native primitive/single-heap parity; predicate-before-terminal-distance; "
                 "ratio boundaries including startup/odd/short/empty; signature-before-distance; "
                 "complete row beyond deficit and native boundary; default admission without callbacks; "
                 "own-only selected native result; deleted-version/graph certificate rejection; "
                 "collapsed ties and native budget parity; fused visited decisions including rejected IDs, "
                 "own notifications, terminal distances, native queues and results exact at five budgets passed\n";
}
