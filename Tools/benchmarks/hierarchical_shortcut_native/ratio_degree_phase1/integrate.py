"""Materialize the bounded ratio policy and safe same-policy hot-path changes."""
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[4] / "datasets/sift1m_zipf200_sparse193_numeric"
PARENT = DATA / "toolchains/h1_predicate_degree_searchsweep_20260916/source"


def once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f"Expected one source anchor: {old[:100]}")
    return text.replace(old, new)


def main():
    if (HERE / "Supplier.h").exists():
        raise RuntimeError("Do not overwrite materialized sources")
    t = (PARENT / "AnnService/Supplier.h").read_text()
    t = once(t, "#include <cmath>", "#include <cmath>\n#include <chrono>")
    t = once(t, "int before = 0, after = 0, checkedBefore = 0, checkedAfter = 0;",
             "int before = 0, after = 0, checkedBefore = 0, checkedAfter = 0;\n"
             "    int physical = 0, required = 0;")
    t = once(t, "struct Stats {", """struct Stats {
    std::uint64_t resetNs = 0, degreeNs = 0, qualifyNs = 0, distanceNs = 0, admitNs = 0, helperNs = 0;
    std::uint64_t stateClears = 0, shortRows = 0, queueOffers = 0, queueAccepted = 0, queueRejected = 0;
    std::uint64_t childQueueOffers = 0, childQueueRejected = 0, directCalls = 0;""")
    t = once(t, "struct Model {", """struct PhaseScope {
    using Clock = std::chrono::steady_clock;
    bool enabled;
    std::uint64_t& total;
    Clock::time_point start;
    PhaseScope(bool on, std::uint64_t& output)
        : enabled(on), total(output), start(on ? Clock::now() : Clock::time_point{}) {}
    ~PhaseScope() {
        if (enabled) total += std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start).count();
    }
};
struct Model {""")
    t = once(t, "int degree = 16;", """double retainedRatio = 0.5;
    int minBaseDegree = 16;
    bool optimized = true, profiling = false, helperReady = false;
    std::vector<int> ordinaryScratch;
    int Required(int physical) const {
        return physical < minBaseDegree ? 0 : static_cast<int>(std::ceil(retainedRatio * physical));
    }
    bool Needs(int physical, int eligible) const {
        return physical >= minBaseDegree && eligible < retainedRatio * physical;
    }
    void PrepareHelper() {
        if (helperReady) return;
        for (int l = 0; l < 2; ++l) postingState[l].assign(model->counts[l + 1], 0);
        discoveredH2.resize(model->counts[2]);
        for (auto& row : discoveredH2) row.clear();
        helperReady = true;
        ++stats.stateClears;
    }""")
    t = once(t, "bool profiling, bool capturing,", "bool profileOn, bool capturing,")
    t = once(t, "model = &m; enabled = supplement; trace = profiling || capturing;",
             "model = &m; enabled = supplement; profiling = profileOn; trace = profileOn || capturing;")
    t = once(t, "stats = {}; inHelper = childCall = routingCall = false;",
             "stats = {}; PhaseScope resetClock(profiling, stats.resetNs);\n"
             "        inHelper = childCall = routingCall = false;")
    t = once(t, """        for (int l = 0; l < 2; ++l) postingState[l].assign(m.counts[l + 1], 0);
        discoveredH2.resize(m.counts[2]);
        for (auto& row : discoveredH2) row.clear();""",
             "        helperReady = false;\n        if (!optimized) PrepareHelper();")
    t = once(t, "int Qualify(int id) {", "int Qualify(int id) {\n        PhaseScope clock(profiling, stats.qualifyNs);")
    t = once(t, "bool RowAllowed(int level, int id) {",
             "bool RowAllowed(int level, int id) {\n        PrepareHelper();")
    t = once(t, "const int flag = Qualify(id);",
             'if (id < 0 || id >= model->counts[0]) throw std::runtime_error("Invalid score ID");\n'
             "        const int flag = optimized && qualificationEpochs[id] == epoch ? qualifications[id] : Qualify(id);")
    t = once(t, "const float d = compute(id);", """float d;
        {
            PhaseScope clock(profiling, stats.distanceNs);
            d = compute(id);
        }""")
    t = once(t, "            admit(id, d);", """            {
                PhaseScope clock(profiling, stats.admitNs);
                admit(id, d);
            }""")
    t = once(t, "std::vector<std::pair<float, int>> Rank(int level, std::vector<int> ids) {",
             "std::vector<std::pair<float, int>> Rank(int level, std::vector<int> ids) {\n        PrepareHelper();")
    t = once(t, """void Supply(int current, int rawBefore, int effective, Offer offer, NativeAllowed nativeAllowed) {
        if (effective >= degree) return;""",
             """void Supply(int current, int rawBefore, int effective, int physical, Offer offer, NativeAllowed nativeAllowed) {
        if (!Needs(physical, effective)) return;""")
    t = once(t, "inHelper = true; ++stats.calls;\n        const int deficit = degree - effective;",
             "PhaseScope clock(profiling, stats.helperNs);\n        PrepareHelper();\n"
             "        inHelper = true; ++stats.calls;\n        const int deficit = Required(physical) - effective;")
    start = t.index("class Connectivity {")
    end = t.index("\nstruct Context {", start)
    t = t[:start] + (HERE / "Connectivity.inc").read_text() + t[end:]
    (HERE / "Supplier.h").write_text(t)
    t = (PARENT / "AnnService/NativeAdapter.h").read_text()
    t = once(t, "auto original = bkt->BenchmarkGetDistance();",
             "auto original = bkt->BenchmarkGetDistance();\n"
             "    using NativeDistance = float (*)(const T*, const T*, SPTAG::DimensionType);\n"
             "    const auto direct = original.template target<NativeDistance>();")
    t = once(t, "auto& e = local.engine;", "auto& e = local.engine;\n    e.optimized = ShortcutFull::optimized;")
    t = once(t, "            return original(result->GetQuantizedTarget(), static_cast<const T*>(bkt->GetSample(id)),",
             "            if (e.optimized && direct) {\n"
             "                ++e.stats.directCalls;\n"
             "                return (*direct)(result->GetQuantizedTarget(), static_cast<const T*>(bkt->GetSample(id)),\n"
             "                                 bkt->GetFeatureDim());\n"
             "            }\n"
             "            return original(result->GetQuantizedTarget(), static_cast<const T*>(bkt->GetSample(id)),")
    t = once(t, "const bool admitted = (e.Qualify(id) & 1) != 0;",
             "const bool admitted = ((e.optimized ? e.qualifications[id] : e.Qualify(id)) & 1) != 0;")
    t = once(t, "e.degree = ShortcutFull::effectiveDegree;",
             "e.retainedRatio = ShortcutFull::retainedRatio;\n    e.minBaseDegree = ShortcutFull::minBaseDegree;")
    fields = ("ResetNs", "DegreeNs", "QualifyNs", "DistanceNs", "AdmitNs", "HelperNs", "StateClears",
              "ShortRows", "QueueOffers", "QueueAccepted", "QueueRejected", "ChildQueueOffers",
              "ChildQueueRejected", "DirectCalls")
    t = once(t, "o.invoked = true;", "\n".join(
        f"o.supply{f} = s.{f[0].lower() + f[1:]};" for f in fields) + "\n    o.invoked = true;")
    (HERE / "NativeAdapter.h").write_text(t)
    shutil.copy2(PARENT / "AnnService/Signature.h", HERE / "Signature.h")
    t = (PARENT / "AnnService/FullHooks.h").read_text()
    t = once(t, "inline int effectiveDegree = 16;",
             "inline double retainedRatio = 0.5;\ninline int minBaseDegree = 16;\ninline bool optimized = true;")
    t = once(t, "struct Observation {",
             "struct Observation {\n    std::uint64_t " +
             ", ".join("supply" + f + " = 0" for f in fields) + ";")
    t = once(t, 'p.first != "shortcuteffectivedegree")',
             'p.first != "shortcutretainedratio" && p.first != "shortcutminbasedegree" &&\n'
             '            p.first != "shortcuthotpath")')
    t = once(t, """        effectiveDegree = Unsigned(get("shortcuteffectivedegree"));
        if (effectiveDegree != 16)
            throw std::runtime_error("Require preregistered effective degree16 trigger");""",
             """        std::size_t consumed = 0;
        const auto& ratio = get("shortcutretainedratio");
        retainedRatio = std::stod(ratio, &consumed);
        const auto minimum = Unsigned(get("shortcutminbasedegree"));
        if (consumed != ratio.size() || !std::isfinite(retainedRatio) ||
            retainedRatio <= 0 || retainedRatio > 1 || minimum == 0 ||
            minimum > static_cast<std::uint64_t>((std::numeric_limits<int>::max)()))
            throw std::runtime_error("Invalid retained ratio or minimum physical degree");
        minBaseDegree = static_cast<int>(minimum);
        const auto& hotpath = get("shortcuthotpath");
        if (hotpath != "optimized" && hotpath != "reference")
            throw std::runtime_error("Invalid phase1 hot-path implementation");
        optimized = hotpath == "optimized";""")
    (HERE / "FullHooks.h").write_text(t)
    t = (PARENT / "AnnService/src/Core/BKT/BKTIndex.cpp").read_text()
    t = '#include <optional>\n' + t
    t = once(t, "std::unique_ptr<H1Supplier::Connectivity> connectivity;",
             "std::optional<H1Supplier::Connectivity> connectivity;")
    t = once(t, "connectivity = std::make_unique<H1Supplier::Connectivity>(",
             "connectivity.emplace(")
    t = once(t, "c->engine->Supply(currentLocal, supplierFresh, connectivity->audit.before,",
             "c->engine->Supply(currentLocal, supplierFresh, connectivity->audit.before, connectivity->audit.physical,")
    start = t.index("    if (auto* c = H1Supplier::Active()) {\n        auto& e = *c->engine;\n        e.collectingEntry = false;")
    end = t.index("\n    while (true)", start)
    t = t[:start] + (HERE / "Startup.inc").read_text() + t[end:]
    t = once(t, """                if (p_space.m_Results.insert(
                        routeDistance))""",
             """                if (auto* c = H1Supplier::Active()) {
                    ++c->engine->stats.queueOffers;
                    c->engine->stats.childQueueOffers += c->engine->childCall;
                }
                if (p_space.m_Results.insert(
                        routeDistance))""")
    t = once(t, """                    if (auto* c = H1Supplier::Active())
                        if (c->engine->childCall) ++c->engine->stats.queued;
                }""",
             """                    if (auto* c = H1Supplier::Active()) {
                        ++c->engine->stats.queueAccepted;
                        if (c->engine->childCall) ++c->engine->stats.queued;
                    }
                } else if (auto* c = H1Supplier::Active()) {
                    ++c->engine->stats.queueRejected;
                    c->engine->stats.childQueueRejected += c->engine->childCall;
                }""")
    (HERE / "BKTIndex.cpp").write_text(t)
    t = (PARENT / "Tools/benchmarks/SpannAclBench.cpp").read_text()
    t = once(t, '<< ",\\"after\\":" << a.after << ",\\"calls\\":" << a.calls',
             '<< ",\\"after\\":" << a.after << ",\\"physical\\":" << a.physical\n'
             '                         << ",\\"required\\":" << a.required << ",\\"calls\\":" << a.calls')
    t = once(t, '                     << ",\\"supplyGraph\\":" << o.supplyGraph',
             "".join(f'                     << ",\\"supply{f}\\":" << o.supply{f}\n' for f in fields) +
             '                     << ",\\"supplyGraph\\":" << o.supplyGraph')
    (HERE / "SpannAclBench.cpp").write_text(t)
    print("Materialized ratio policy, lazy supplier state, allocation-free ordinary degree frames, "
          "safe redundant-call removal and instrumented native heap rejection.")


if __name__ == "__main__":
    main()
