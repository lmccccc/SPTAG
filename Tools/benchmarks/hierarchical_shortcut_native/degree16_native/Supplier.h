#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <cmath>
#include <cstring>
#include <functional>
#include <limits>
#include <stdexcept>
#include <utility>
#include <vector>

namespace H1Supplier {
struct Stats {
    std::uint64_t graph = 0, parent = 0, child = 0, cache = 0, repeats = 0;
    std::uint64_t starts = 0, pops = 0, expansions = 0, sentinels = 0, trees = 0;
    std::uint64_t calls = 0, returns = 0, neighbors = 0, queued = 0;
    std::uint64_t h2Rows = 0, h3Rows = 0, ascents = 0, members = 0;
    std::uint64_t maxMembers = 0, maxNeighbors = 0, candidates = 0, eligible = 0;
    std::uint64_t rawBefore = 0, effectiveBefore = 0, effectiveAfter = 0, triggers = 0;
    std::uint64_t requested = 0, qualified = 0, maxQualified = 0, fills = 0, h2Fills = 0;
    std::uint64_t h2Exhausted = 0, distanceStops = 0, memberStops = 0, nativeStops = 0, scopeStops = 0;
    std::uint64_t trace = 1469598103934665603ULL;
    std::uint64_t Used() const { return graph + parent + child; }
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
                auto row = children(level + 1, parent);
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
struct BudgetExhausted {};
struct Engine {
    const Model* model = nullptr;
    Stats stats;
    std::uint64_t cap = 0;
    std::uint64_t memberCap = 2048;
    int degree = 16;
    bool enabled = false, trace = false, capture = false, inHelper = false, childCall = false;
    std::vector<std::uint32_t> epochs;
    std::uint32_t epoch = 0;
    std::vector<float> distances;
    std::vector<int> qualifications;
    std::array<std::vector<std::size_t>, 2> cursors;
    std::vector<std::pair<int, float>> evaluated;
    std::vector<int> parentEvaluated;
    std::vector<std::array<std::uint64_t, 9>> audits;
    std::function<float(int)> compute;
    std::function<int(int, float)> admit;
    void Event(std::uint64_t kind, std::uint64_t value) {
        if (trace) {
            stats.trace = (stats.trace ^ kind) * 1099511628211ULL;
            stats.trace = (stats.trace ^ value) * 1099511628211ULL;
        }
    }
    void Reset(const Model& m, std::uint64_t budget, bool supplement, bool profiling,
               bool capturing, std::function<float(int)> distance,
               std::function<int(int, float)> admission) {
        model = &m; cap = budget; enabled = supplement; trace = profiling || capturing;
        capture = capturing; compute = std::move(distance); admit = std::move(admission);
        stats = {}; inHelper = childCall = false;
        if (epochs.size() != static_cast<std::size_t>(m.counts[0])) {
            epochs.assign(m.counts[0], 0); distances.resize(m.counts[0]);
            qualifications.resize(m.counts[0]); epoch = 0;
        }
        if (++epoch == 0) { std::fill(epochs.begin(), epochs.end(), 0); ++epoch; }
        for (int l = 0; l < 2; ++l) cursors[l].assign(m.counts[l + 1], 0);
        evaluated.clear(); parentEvaluated.clear(); audits.clear();
    }
    bool Remaining() const { return stats.Used() < cap; }
    float Score(int id, int kind, bool force) {
        if (id < 0 || id >= model->counts[0]) throw std::runtime_error("Invalid canonical H1");
        bool seen = epochs[id] == epoch;
        if (seen && !force) { ++stats.cache; Event(10 + kind, id); return distances[id]; }
        if (!Remaining()) throw BudgetExhausted{};
        const float d = compute(id);
        if (!(d >= 0) || !std::isfinite(d)) throw std::runtime_error("Invalid native distance");
        if (kind == 0) ++stats.graph;
        else if (kind == 1) ++stats.parent;
        else ++stats.child;
        Event(20 + kind, id);
        if (seen) ++stats.repeats;
        else {
            epochs[id] = epoch; distances[id] = d; ++stats.candidates;
            if (capture) {
                evaluated.emplace_back(id, d);
                if (kind == 1) parentEvaluated.push_back(id);
            }
            qualifications[id] = admit(id, d);
        }
        return d;
    }
    std::vector<std::pair<float, int>> Rank(int level, const std::vector<int>& ids) {
        std::vector<std::pair<float, int>> ranked;
        for (int id : ids) {
            if (id < 0 || id >= model->counts[level]) throw std::runtime_error("Invalid parent ID");
            if (std::any_of(ranked.begin(), ranked.end(), [id](auto p) { return p.second == id; })) continue;
            if (!Remaining()) break;
            ranked.emplace_back(Score(model->canonical[level - 1][id], 1, false), id);
        }
        std::sort(ranked.begin(), ranked.end());
        for (auto p : ranked) Event(30 + level, p.second);
        return ranked;
    }
    template<class Offer, class NativeAllowed>
    void Supply(int current, int rawBefore, int effective, Offer offer, NativeAllowed nativeAllowed) {
        if (effective >= degree) return;
        ++stats.triggers;
        if (!enabled || !Remaining() || stats.members >= memberCap || !nativeAllowed()) return;
        if (inHelper) throw std::runtime_error("Nested or asynchronous supplier");
        inHelper = true; ++stats.calls;
        const int deficit = degree - effective;
        stats.requested += deficit;
        const auto beforeMembers = stats.members, beforeNeighbors = stats.neighbors;
        const auto beforeQualified = stats.qualified, beforeQueued = stats.queued;
        int stop = 4;
        bool h2Done = false;
        struct Finish {
            Engine& e; std::uint64_t m, n, q, queued; int raw, before, need; int& stop; bool& h2;
            ~Finish() {
                e.inHelper = e.childCall = false; ++e.stats.returns;
                e.stats.maxMembers = std::max(e.stats.maxMembers, e.stats.members - m);
                e.stats.maxNeighbors = std::max(e.stats.maxNeighbors, e.stats.neighbors - n);
                e.stats.maxQualified = std::max(e.stats.maxQualified, e.stats.qualified - q);
                if (e.stats.qualified - q == static_cast<unsigned>(need)) {
                    stop = 0; ++e.stats.fills;
                } else if (!e.Remaining()) { stop = 1; ++e.stats.distanceStops; }
                else if (e.stats.members >= e.memberCap) { stop = 2; ++e.stats.memberStops; }
                else if (stop == 3) ++e.stats.nativeStops;
                else ++e.stats.scopeStops;
                if (e.capture) e.audits.push_back({
                    static_cast<unsigned>(raw), static_cast<unsigned>(before), static_cast<unsigned>(need),
                    e.stats.qualified - q, e.stats.neighbors - n, e.stats.queued - queued,
                    e.stats.members - m, static_cast<unsigned>(stop), static_cast<unsigned>(h2)});
                e.Event(40, e.stats.qualified - q);
            }
        } finish{*this, beforeMembers, beforeNeighbors, beforeQualified, beforeQueued,
                 rawBefore, effective, deficit, stop, h2Done};
        int supplied = 0;
        const auto allowed = [&]() {
            if (!nativeAllowed()) { stop = 3; return false; }
            return Remaining() && stats.members < memberCap && supplied < deficit;
        };
        const auto openH2 = [&](int id) {
            if (!allowed()) return;
            auto row = model->children(1, id);
            auto& cursor = cursors[0][id];
            if (cursor == static_cast<std::size_t>(row.second - row.first)) return;
            ++stats.h2Rows; Event(41, id);
            while (cursor < static_cast<std::size_t>(row.second - row.first) && allowed()) {
                int child = row.first[cursor++]; ++stats.members; Event(42, child);
                childCall = true;
                const int result = offer(child);
                childCall = false;
                if (result & 1) ++stats.neighbors;
                if (result & 2) { ++supplied; ++stats.qualified; }
            }
        };
        const auto& owners = model->owners[0][current];
        auto parents = Rank(1, {owners.begin(), owners.end()});
        for (auto parent : parents) {
            if (!allowed()) break;
            openH2(parent.second);
        }
        if (supplied == deficit) { ++stats.h2Fills; return; }
        h2Done = parents.size() == 8 && std::all_of(parents.begin(), parents.end(), [&](auto p) {
            auto row = model->children(1, p.second);
            return cursors[0][p.second] == static_cast<std::size_t>(row.second - row.first);
        });
        if (h2Done) ++stats.h2Exhausted;
        if (!h2Done || !allowed()) return;
        ++stats.ascents;
        std::vector<int> upper;
        for (auto parent : parents) {
            auto& o = model->owners[1][parent.second];
            upper.insert(upper.end(), o.begin(), o.end());
        }
        auto rankedUpper = Rank(2, upper);
        for (auto parent : rankedUpper) {
            if (!allowed()) break;
            auto row = model->children(2, parent.second);
            auto& cursor = cursors[1][parent.second];
            if (cursor == static_cast<std::size_t>(row.second - row.first)) continue;
            ++stats.h3Rows; Event(43, parent.second);
            while (cursor < static_cast<std::size_t>(row.second - row.first) && allowed()) {
                int id = row.first[cursor]; ++stats.members; Event(44, id);
                openH2(id);
                auto childRow = model->children(1, id);
                if (cursors[0][id] == static_cast<std::size_t>(childRow.second - childRow.first)) ++cursor;
                else break;
            }
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
