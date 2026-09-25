#pragma once
#include "FullHooks.h"
#include "Supplier.h"
#include "Signature.h"
#include "inc/Core/BKT/Index.h"

namespace H1Supplier {
inline bool Enabled() { return ShortcutFull::mode == "supplier" || ShortcutFull::mode == "control"; }
template<class T, class Catalogs, class Postings, class Maps, class Admit, class Own, class Qualify, class Signature>
void NativeSearch(SPTAG::BKT::Index<T>* bkt, SPTAG::COMMON::QueryResultSet<T>* result,
                  const Catalogs& catalogs, const Postings& postings, const Maps& maps,
                  Admit admission, Own own, Qualify qualify, Signature signature) {
    struct Local { const void* key = nullptr; Model model; Engine engine; };
    static thread_local Local local;
    if (local.key != bkt) {
        local = Local{};
        if (postings.size() != 2 || catalogs.size() != 2 || maps.size() != 2)
            throw std::runtime_error("Supplier requires original H2/H3 catalogs and maps");
        auto& m = local.model;
        m.counts = {bkt->GetNumSamples(), catalogs[0]->Count(), catalogs[1]->Count()};
        m.children = [&postings](int level, int id) -> Model::Row {
            return {postings[level - 1].Begin(id), postings[level - 1].End(id)};
        };
        for (int level = 0; level < 2; ++level) {
            if (postings[level].ReplicaCount() != 8 || maps[level].size() != static_cast<size_t>(m.counts[level + 1]))
                throw std::runtime_error("Wrong supplier representative maps/replicas");
            for (int id = 0; id < m.counts[level + 1]; ++id) {
                auto lower = maps[level][id];
                if (lower >= static_cast<size_t>(m.counts[level]))
                    throw std::runtime_error("Out-of-range adjacent-layer representative");
                int canonical = level == 0 ? static_cast<int>(lower) : m.canonical[0][lower];
                if (std::memcmp(bkt->GetSample(canonical), catalogs[level]->GetVector(id),
                                sizeof(T) * bkt->GetFeatureDim()) != 0)
                    throw std::runtime_error("Parent representative is not its mapped H1 vector");
                m.canonical[level].push_back(canonical);
            }
        }
        m.BuildOwners();
        local.key = bkt;
    }
    auto original = bkt->BenchmarkGetDistance();
    auto& e = local.engine;
    std::uint64_t independentCalls = 0;
    result->Reset();
    e.Reset(local.model, ShortcutFull::mode == "supplier",
            ShortcutFull::profile, ShortcutFull::capture,
        [&](int id) {
            if (ShortcutFull::profile) ++independentCalls;
            return original(result->GetQuantizedTarget(), static_cast<const T*>(bkt->GetSample(id)),
                            bkt->GetFeatureDim());
        },
        [&](int id, float d) {
            own(id, &d);
            const bool admitted = (e.Qualify(id) & 1) != 0;
            if (admitted) { ++e.stats.eligible; result->AddPoint(id, d); }
        },
        [&](int id) { return qualify(id, admission(id)); }, signature);
    e.degree = ShortcutFull::effectiveDegree;
    Context context{&e, bkt, 0};
    ShortcutBench::State native;
    native.profile = ShortcutFull::profile;
    if (Active() || ShortcutBench::active) throw std::runtime_error("Nested native supplier query");
    Active() = &context; ShortcutBench::active = &native;
    const auto base = reinterpret_cast<std::uintptr_t>(bkt->GetSample(0));
    const std::size_t stride = sizeof(T) * bkt->GetFeatureDim();
    bkt->BenchmarkRestore([&](const T*, const T* sample, SPTAG::DimensionType) {
        auto address = reinterpret_cast<std::uintptr_t>(sample);
        if (address < base || (address - base) % stride)
            throw std::runtime_error("Unmapped native H1 callback");
        auto id = (address - base) / stride;
        if (id >= static_cast<std::size_t>(bkt->GetNumSamples()) || bkt->GetSample(id) != sample)
            throw std::runtime_error("Invalid native H1 sample identity");
        return e.Score(static_cast<int>(id), e.routingCall ? 3 : (e.childCall ? 2 : 0), !e.childCall);
    });
    try {
        SPTAG::COMMON::QueryResultSet<T> spatial(result->GetTarget(), result->GetResultNum());
        if (bkt->SearchIndexWithTraversalFilter(spatial,
                [&](int id) { return e.Qualify(id) != 0; }, 0) != SPTAG::ErrorCode::Success)
            throw std::runtime_error("Native H1 search failed");
    } catch (...) {
        bkt->BenchmarkRestore(std::move(original));
        Active() = nullptr; ShortcutBench::active = nullptr;
        throw;
    }
    bkt->BenchmarkRestore(std::move(original));
    Active() = nullptr; ShortcutBench::active = nullptr;
    const auto& s = e.stats;
    if (s.starts != 1 || e.inHelper || s.calls != s.returns ||
        s.h2Rows != s.h2Completed || s.h3Rows != s.h3Completed ||
        (ShortcutFull::profile && independentCalls != s.Used()))
        throw std::runtime_error("Supplier continuation/full-row/ledger invariant failed");
    result->SortResult();
    result->SetScanned(static_cast<int>(context.nativeChecked));
    auto& o = ShortcutFull::Last();
    o.invoked = true; o.calls = ShortcutFull::profile ? s.Used() : 0;
    o.graphChecked = context.nativeChecked;
    o.adjacency = native.adjacency; o.csrDistances = s.parent + s.child;
    o.csrAssignments = s.members; o.graphChecked = context.nativeChecked;
    o.exhausted = s.Used() == ShortcutFull::cap;
    o.supplyGraph = s.graph; o.supplyParent = s.parent; o.supplyChild = s.child;
    o.supplyCache = s.cache; o.supplyRepeats = s.repeats;
    o.supplyStarts = s.starts; o.supplyPops = s.pops; o.supplyExpansions = s.expansions;
    o.supplySentinels = s.sentinels; o.supplyTrees = s.trees;
    o.supplyCalls = s.calls; o.supplyReturns = s.returns;
    o.supplyNeighbors = s.neighbors; o.supplyQueued = s.queued;
    o.supplyH2Rows = s.h2Rows; o.supplyH3Rows = s.h3Rows; o.supplyAscents = s.ascents;
    o.supplyMembers = s.members; o.supplyMaxMembers = s.maxMembers;
    o.supplyMaxNeighbors = s.maxNeighbors; o.supplyCandidates = s.candidates;
    o.supplyEligible = s.eligible; o.supplyTrace = s.trace;
    o.supplyRawBefore = s.rawBefore; o.supplyEffectiveBefore = s.effectiveBefore;
    o.supplyEffectiveAfter = s.effectiveAfter; o.supplyTriggers = s.triggers;
    o.supplyRequested = s.requested; o.supplyQualified = s.qualified;
    o.supplyMaxQualified = s.maxQualified; o.supplyFills = s.fills; o.supplyH2Fills = s.h2Fills;
    o.supplyH2Exhausted = s.h2Exhausted; o.supplyNativeStops = s.nativeStops;
    o.supplyScopeStops = s.scopeStops;
    o.supplyRouting = s.routing; o.supplySignatureChecks = s.signatureChecks;
    o.supplySignatureRejects = s.signatureRejects; o.supplySignatureCache = s.signatureCache;
    o.supplyPredicateChecks = s.predicateChecks; o.supplyPredicateRejects = s.predicateRejects;
    o.supplyPredicateCache = s.predicateCache; o.supplyPostingSkips = s.postingSkips;
    o.supplyH2Completed = s.h2Completed; o.supplyH3Completed = s.h3Completed;
    o.supplyNativeMaxCheck = s.nativeMaxCheck; o.supplyNativeOvershoot = s.nativeOvershoot;
    o.supplyContinuationPops = s.continuationPops;
    o.supplyRejectedLeaves = s.rejectedLeaves;
    if (ShortcutFull::capture) {
        o.evaluated = e.evaluated;
        o.parentEvaluated = e.parentEvaluated;
        for (auto p : e.evaluated) o.qualifications.push_back(e.qualifications[p.first]);
        o.degreeAudits = e.audits;
        o.rowAudits = e.rowAudits; o.rejectedRows = e.rejectedRows;
        o.predicateRejected = e.predicateRejected;
    }
}
}
