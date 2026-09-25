#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <functional>
#include <stdexcept>
#include <utility>
#include <vector>

namespace H1Supplier {
struct Stats {
    std::uint64_t graph = 0, routing = 0, parent = 0, child = 0, cache = 0, repeats = 0;
    std::uint64_t starts = 0, pops = 0, expansions = 0, sentinels = 0, trees = 0;
    std::uint64_t calls = 0, returns = 0, neighbors = 0, queued = 0;
    std::uint64_t h2Rows = 0, h3Rows = 0, ascents = 0, members = 0;
    std::uint64_t maxMembers = 0, maxNeighbors = 0, candidates = 0, eligible = 0;
    std::uint64_t rawBefore = 0, effectiveBefore = 0, effectiveAfter = 0, triggers = 0;
    std::uint64_t requested = 0, qualified = 0, maxQualified = 0, fills = 0;
    std::uint64_t h2Fills = 0, h2Exhausted = 0, nativeStops = 0, scopeStops = 0;
    std::uint64_t signatureChecks = 0, signatureRejects = 0, signatureCache = 0;
    std::uint64_t predicateChecks = 0, predicateRejects = 0, predicateCache = 0;
    std::uint64_t postingSkips = 0, h2Completed = 0, h3Completed = 0;
    std::uint64_t nativeMaxCheck = 0, nativeOvershoot = 0, continuationPops = 0;
    std::uint64_t trace = 1469598103934665603ULL;
    std::uint64_t Used() const { return graph + routing + parent + child; }
};
struct Model {
    using Row = std::pair<const std::uint32_t*, const std::uint32_t*>;
    std::array<int, 3> counts{};
    std::array<std::vector<std::array<int, 8>>, 2> owners;
    std::array<std::vector<int>, 2> canonical;
    std::function<Row(int, int)> children;
    void BuildOwners() {
        for (int level = 0; level < 2; ++level) {
            owners[level].resize(counts[level]);
            std::vector<int> sizes(counts[level], 0);
            for (int parent = 0; parent < counts[level + 1]; ++parent) {
                const auto row = children(level + 1, parent);
                if (!row.first || !row.second || row.second < row.first)
                    throw std::runtime_error("Invalid CSR member range");
                for (auto p = row.first; p != row.second; ++p) {
                    if (*p >= static_cast<unsigned>(counts[level]) || sizes[*p] == 8)
                        throw std::runtime_error("Invalid original eight-owner CSR");
                    auto& o = owners[level][*p];
                    if (std::find(o.begin(), o.begin() + sizes[*p], parent) != o.begin() + sizes[*p])
                        throw std::runtime_error("Duplicate parent membership");
                    o[sizes[*p]++] = parent;
                }
            }
            for (int n : sizes)
                if (n != 8) throw std::runtime_error("Missing direct owner membership");
        }
    }
};
struct Engine {
    const Model* model = nullptr;
    Stats stats;
    int degree = 16;
    bool enabled = false, trace = false, capture = false;
    bool inHelper = false, childCall = false, routingCall = false;
    std::vector<std::uint32_t> epochs, qualificationEpochs;
    std::uint32_t epoch = 0;
    std::vector<float> distances;
    std::vector<int> qualifications;
    // 0 unchecked, 1 signature-pass/unvisited, 2 fully completed, 3 signature-rejected.
    std::array<std::vector<unsigned char>, 2> postingState;
    std::vector<std::pair<int, float>> evaluated;
    std::vector<int> parentEvaluated, predicateRejected;
    std::vector<std::array<std::uint64_t, 9>> audits;
    std::vector<std::array<std::uint64_t, 4>> rowAudits;
    std::vector<std::array<int, 2>> rejectedRows;
    std::function<float(int)> compute;
    std::function<void(int, float)> admit;
    std::function<int(int)> qualify;
    std::function<bool(int, int)> mayMatch;
    void Event(std::uint64_t kind, std::uint64_t value) {
        if (trace) {
            stats.trace = (stats.trace ^ kind) * 1099511628211ULL;
            stats.trace = (stats.trace ^ value) * 1099511628211ULL;
        }
    }
    void Reset(const Model& m, bool supplement, bool profiling, bool capturing,
               std::function<float(int)> distance, std::function<void(int, float)> admission,
               std::function<int(int)> qualification, std::function<bool(int, int)> signature) {
        model = &m; enabled = supplement; trace = profiling || capturing;
        capture = capturing; compute = std::move(distance); admit = std::move(admission);
        qualify = std::move(qualification); mayMatch = std::move(signature);
        stats = {}; inHelper = childCall = routingCall = false;
        if (epochs.size() != static_cast<std::size_t>(m.counts[0])) {
            epochs.assign(m.counts[0], 0); qualificationEpochs.assign(m.counts[0], 0);
            distances.resize(m.counts[0]); qualifications.resize(m.counts[0]); epoch = 0;
        }
        if (++epoch == 0) {
            std::fill(epochs.begin(), epochs.end(), 0);
            std::fill(qualificationEpochs.begin(), qualificationEpochs.end(), 0); ++epoch;
        }
        for (int l = 0; l < 2; ++l) postingState[l].assign(m.counts[l + 1], 0);
        evaluated.clear(); parentEvaluated.clear(); predicateRejected.clear();
        audits.clear(); rowAudits.clear(); rejectedRows.clear();
    }
    int Qualify(int id) {
        if (id < 0 || id >= model->counts[0]) throw std::runtime_error("Invalid canonical H1");
        if (qualificationEpochs[id] == epoch) { ++stats.predicateCache; return qualifications[id]; }
        const int flag = qualify(id);
        if (flag < 0 || flag > 3) throw std::runtime_error("Invalid native eligibility bits");
        qualifications[id] = flag; qualificationEpochs[id] = epoch; ++stats.predicateChecks;
        if (!flag) {
            ++stats.predicateRejects;
            if (capture) predicateRejected.push_back(id);
        }
        Event(60, id); Event(61, flag);
        return flag;
    }
    bool RowAllowed(int level, int id) {
        if (level < 1 || level > 2 || id < 0 || id >= model->counts[level])
            throw std::runtime_error("Invalid parent posting");
        auto& state = postingState[level - 1][id];
        if (state == 2) { ++stats.postingSkips; return false; }
        if (state) { ++stats.signatureCache; return state == 1; }
        ++stats.signatureChecks;
        const bool pass = mayMatch(level, id);
        state = pass ? 1 : 3; Event(62 + level, id); Event(65, pass);
        if (!pass) {
            ++stats.signatureRejects;
            if (capture) rejectedRows.push_back({level, id});
        }
        return pass;
    }
    float Score(int id, int kind, bool force) {
        const int flag = Qualify(id);
        if ((kind == 0 || kind == 2) && !flag)
            throw std::runtime_error("Ineligible candidate reached distance callback");
        const bool seen = epochs[id] == epoch;
        if (seen && !force) { ++stats.cache; Event(10 + kind, id); return distances[id]; }
        const float d = compute(id);
        if (!(d >= 0) || !std::isfinite(d)) throw std::runtime_error("Invalid native distance");
        if (kind == 0) ++stats.graph;
        else if (kind == 1) ++stats.parent;
        else if (kind == 2) ++stats.child;
        else if (kind == 3) ++stats.routing;
        else throw std::runtime_error("Invalid distance role");
        Event(20 + kind, id);
        if (seen) ++stats.repeats;
        else {
            epochs[id] = epoch; distances[id] = d; ++stats.candidates;
            if (capture) {
                evaluated.emplace_back(id, d);
                if (kind == 1) parentEvaluated.push_back(id);
            }
            admit(id, d);
        }
        return d;
    }
    std::vector<std::pair<float, int>> Rank(int level, std::vector<int> ids) {
        std::sort(ids.begin(), ids.end()); ids.erase(std::unique(ids.begin(), ids.end()), ids.end());
        std::vector<std::pair<float, int>> ranked;
        for (int id : ids)
            if (RowAllowed(level, id))
                ranked.emplace_back(Score(model->canonical[level - 1][id], 1, false), id);
        std::sort(ranked.begin(), ranked.end());
        for (auto p : ranked) Event(30 + level, p.second);
        return ranked;
    }
    template<class Offer, class NativeAllowed>
    void Supply(int current, int rawBefore, int effective, Offer offer, NativeAllowed nativeAllowed) {
        if (effective >= degree) return;
        ++stats.triggers;
        if (!enabled || !nativeAllowed()) return;
        if (inHelper) throw std::runtime_error("Nested or asynchronous supplier");
        inHelper = true; ++stats.calls;
        const int deficit = degree - effective;
        stats.requested += deficit;
        const auto beforeMembers = stats.members, beforeNeighbors = stats.neighbors;
        const auto beforeQualified = stats.qualified, beforeQueued = stats.queued;
        bool h2Done = false, nativeStopped = false;
        const auto boundary = [&] {
            const bool allowed = nativeAllowed();
            if (!allowed) nativeStopped = true;
            return allowed;
        };
        struct Finish {
            Engine& e; std::uint64_t m, n, q, queued; int raw, before, need; bool& h2; bool& nativeStop;
            ~Finish() {
                e.inHelper = e.childCall = false; ++e.stats.returns;
                e.stats.maxMembers = std::max(e.stats.maxMembers, e.stats.members - m);
                e.stats.maxNeighbors = std::max(e.stats.maxNeighbors, e.stats.neighbors - n);
                e.stats.maxQualified = std::max(e.stats.maxQualified, e.stats.qualified - q);
                const int stop = e.stats.qualified - q >= static_cast<unsigned>(need) ? 0 :
                                 (nativeStop ? 3 : 4);
                if (!stop) ++e.stats.fills;
                else if (stop == 3) ++e.stats.nativeStops;
                else ++e.stats.scopeStops;
                if (e.capture) e.audits.push_back({
                    static_cast<unsigned>(raw), static_cast<unsigned>(before), static_cast<unsigned>(need),
                    e.stats.qualified - q, e.stats.neighbors - n, e.stats.queued - queued,
                    e.stats.members - m, static_cast<unsigned>(stop), static_cast<unsigned>(h2)});
                e.Event(40, e.stats.qualified - q);
            }
        } finish{*this, beforeMembers, beforeNeighbors, beforeQualified, beforeQueued,
                 rawBefore, effective, deficit, h2Done, nativeStopped};
        const auto filled = [&] { return stats.qualified - beforeQualified >= static_cast<unsigned>(deficit); };
        const auto openH2 = [&](int id) {
            if (!RowAllowed(1, id)) return;
            const auto row = model->children(1, id);
            const auto before = stats.members;
            ++stats.h2Rows; Event(41, id);
            for (auto p = row.first; p != row.second; ++p) {
                ++stats.members; Event(42, *p); childCall = true;
                const int result = offer(static_cast<int>(*p));
                childCall = false;
                if (result & 1) ++stats.neighbors;
                if (result & 2) ++stats.qualified;
            }
            postingState[0][id] = 2; ++stats.h2Completed;
            if (capture) rowAudits.push_back({1, static_cast<unsigned>(id),
                static_cast<std::uint64_t>(row.second - row.first), stats.members - before});
        };
        const auto& own = model->owners[0][current];
        const auto parents = Rank(1, {own.begin(), own.end()});
        for (auto parent : parents) {
            if (!boundary()) break;
            openH2(parent.second);
            if (filled()) { ++stats.h2Fills; return; }
        }
        h2Done = std::all_of(own.begin(), own.end(), [&](int id) {
            const auto state = postingState[0][id]; return state == 2 || state == 3;
        });
        if (h2Done) ++stats.h2Exhausted;
        if (!h2Done || !boundary()) return;
        ++stats.ascents;
        std::vector<int> upper;
        for (int parent : own) {
            const auto& o = model->owners[1][parent];
            upper.insert(upper.end(), o.begin(), o.end());
        }
        for (auto parent : Rank(2, std::move(upper))) {
            if (!boundary()) break;
            if (!RowAllowed(2, parent.second)) continue;
            const auto row = model->children(2, parent.second);
            ++stats.h3Rows; Event(43, parent.second);
            std::uint64_t enumerated = 0;
            for (auto p = row.first; p != row.second; ++p) {
                ++stats.members; ++enumerated; Event(44, *p);
                openH2(static_cast<int>(*p));
            }
            postingState[1][parent.second] = 2; ++stats.h3Completed;
            if (capture) rowAudits.push_back({2, static_cast<unsigned>(parent.second),
                static_cast<std::uint64_t>(row.second - row.first), enumerated});
            if (filled()) return;
        }
    }
};
struct Context {
    Engine* engine = nullptr;
    const void* index = nullptr;
    std::uint64_t nativeChecked = 0;
};
inline Context*& Active() { static thread_local Context* context = nullptr; return context; }
}
