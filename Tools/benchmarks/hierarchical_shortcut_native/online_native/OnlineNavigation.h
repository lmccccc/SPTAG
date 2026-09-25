#pragma once
#include <array>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <cmath>
#include <functional>
#include <limits>
#include <queue>
#include <stdexcept>
#include <tuple>
#include <vector>

namespace OnlineOwners {
struct Stop {};
struct Model {
    std::array<int, 3> counts{};
    std::array<std::vector<std::array<int, 8>>, 2> owners;
    using Children = std::pair<const std::uint32_t*, const std::uint32_t*>;
    std::function<Children(int, int)> children;
    std::function<std::pair<const int*, int>(int)> graph;

    void BuildOwners() {
        for (int lower = 0; lower < 2; ++lower) {
            owners[lower].resize(counts[lower]);
            std::vector<int> degrees(counts[lower], 0);
            for (int p = 0; p < counts[lower + 1]; ++p) {
                auto row = children(lower + 1, p);
                int previous = -1;
                for (auto it = row.first; it != row.second; ++it) {
                    const int x = *it;
                    if (x < 0 || x >= counts[lower] || x <= previous || degrees[x] >= 8)
                        throw std::runtime_error("Invalid original owner CSR");
                    owners[lower][x][degrees[x]++] = p;
                    previous = x;
                }
            }
            for (int n : degrees)
                if (n != 8) throw std::runtime_error("Current item must have eight direct owners");
        }
    }
};
struct Stats {
    std::uint64_t used = 0, independent = 0, graphCalls = 0, parentCalls = 0, childCalls = 0;
    std::uint64_t cacheHits = 0, graphEntries = 0, memberEntries = 0, batches = 0;
    std::array<std::uint64_t, 2> ups{}, rows{};
    std::uint64_t h1Candidates = 0, trace = 1469598103934665603ULL;
};
class Search {
    Model* model = nullptr;
    std::uint32_t epoch = 0;
    std::array<std::vector<std::uint32_t>, 3> stamps;
    std::array<std::vector<float>, 3> distances;
    std::array<std::vector<std::uint32_t>, 2> upward, scheduled;
    std::array<std::vector<std::size_t>, 2> cursors;
    std::priority_queue<std::pair<float,int>, std::vector<std::pair<float,int>>,
                        std::greater<std::pair<float,int>>> graphQueue;
    using Row = std::tuple<float,int,int>;
    std::priority_queue<Row, std::vector<Row>, std::greater<Row>> rowQueue;
    std::uint64_t budget = 0;
    int batchSize = 16;
    bool online = false, profile = false, trace = false;
    std::function<float(int,int)> distance;
    std::function<void(int,float)> admit;

    void Hash(std::uint64_t value) {
        if (trace) { stats.trace ^= value; stats.trace *= 1099511628211ULL; }
    }
    void Schedule(int level, int id, float d) {
        if (scheduled[level - 1][id] == epoch) return;
        scheduled[level - 1][id] = epoch;
        cursors[level - 1][id] = 0;
        rowQueue.emplace(d, level, id);
    }
    void Up(int level, int id) {
        if (!online || level == 2 || upward[level][id] == epoch) return;
        upward[level][id] = epoch;
        ++stats.ups[level];
        std::array<std::pair<float,int>, 8> ranked;
        int pos = 0;
        for (int p : model->owners[level][id])
            ranked[pos++] = {Score(level + 1, p, 1), p};
        std::sort(ranked.begin(), ranked.end());
        // Do not expand a partially ranked owner list if the budget ends above.
        for (auto p : ranked) Schedule(level + 1, p.second, p.first);
    }
    void GraphStep() {
        const auto current = graphQueue.top();
        graphQueue.pop();
        Hash(0xA0000000ULL + current.second);
        bool improving = false;
        auto row = model->graph(current.second);
        for (int j = 0; j < row.second && row.first[j] >= 0; ++j) {
            ++stats.graphEntries;
            int id = row.first[j];
            if (stamps[0][id] == epoch) { ++stats.cacheHits; continue; }
            float d = Score(0, id, 0);
            improving = improving || d < current.first;
        }
        if (!improving) Up(0, current.second);
    }
    void RowStep() {
        const auto current = rowQueue.top();
        rowQueue.pop();
        const float d = std::get<0>(current);
        const int level = std::get<1>(current), id = std::get<2>(current);
        auto row = model->children(level, id);
        auto& cursor = cursors[level - 1][id];
        const std::size_t size = row.second - row.first;
        if (cursor == 0) ++stats.rows[level - 1];
        ++stats.batches;
        Hash(0xB0000000ULL + level); Hash(id); Hash(cursor);
        const std::size_t end = std::min(size, cursor + batchSize);
        bool improving = false;
        while (cursor < end) {
            if (stats.memberEntries >= budget * 16) throw Stop{};
            const int child = row.first[cursor++];
            ++stats.memberEntries;
            const bool fresh = stamps[level - 1][child] != epoch;
            const float childDistance = Score(level - 1, child, 2);
            improving = improving || (fresh && childDistance < d);
            if (level > 1) Schedule(level - 1, child, childDistance);
        }
        if (cursor < size) rowQueue.emplace(d, level, id);
        if (!improving) Up(level, id);
    }
public:
    Stats stats;
    void Reset(Model& input, std::uint64_t cap, int batch, bool enabled, bool counted, bool capture,
               std::function<float(int,int)> score, std::function<void(int,float)> admission) {
        if (model != &input) {
            model = &input;
            epoch = 0;
            for (int l = 0; l < 3; ++l) {
                stamps[l].assign(input.counts[l], 0);
                distances[l].resize(input.counts[l]);
                if (l < 2) upward[l].assign(input.counts[l], 0);
                if (l > 0) {
                    scheduled[l - 1].assign(input.counts[l], 0);
                    cursors[l - 1].resize(input.counts[l]);
                }
            }
        }
        if (++epoch == 0) throw std::runtime_error("Online query epoch exhausted");
        graphQueue = {}; rowQueue = {};
        stats = {};
        budget = cap; batchSize = batch; online = enabled; profile = counted;
        trace = counted || capture; distance = std::move(score); admit = std::move(admission);
    }
    float Score(int level, int id, int kind, bool forceCallback = false) {
        if (id < 0 || id >= model->counts[level]) throw std::runtime_error("Invalid spatial item");
        const bool cached = stamps[level][id] == epoch;
        if (cached && !forceCallback) { ++stats.cacheHits; return distances[level][id]; }
        if (stats.used == budget) throw Stop{};
        ++stats.used;
        if (profile) ++stats.independent;
        const float d = distance(level, id);
        if (!(d >= 0) || !std::isfinite(d)) throw std::runtime_error("Invalid spatial distance");
        if (kind == 0) ++stats.graphCalls;
        else if (kind == 1) ++stats.parentCalls;
        else ++stats.childCalls;
        std::uint32_t bits; std::memcpy(&bits, &d, sizeof(bits));
        Hash(level); Hash(id); Hash(bits); Hash(kind);
        if (cached) {
            if (distances[level][id] != d) throw std::runtime_error("Repeated native distance changed");
            return d;
        }
        stamps[level][id] = epoch; distances[level][id] = d;
        if (level == 0) {
            ++stats.h1Candidates;
            admit(id, d);
            graphQueue.emplace(d, id);
        }
        return d;
    }
    bool Cached(int id) const { return stamps[0][id] == epoch; }
    void Run() {
        try {
            while (stats.used < budget && (!graphQueue.empty() || !rowQueue.empty())) {
                if (!graphQueue.empty()) GraphStep();
                if (stats.used < budget && !rowQueue.empty()) RowStep();
            }
        } catch (const Stop&) {}
        if (stats.used != stats.graphCalls + stats.parentCalls + stats.childCalls ||
            (profile && stats.independent != stats.used))
            throw std::runtime_error("Online actual-distance accounting failed");
    }
};
}
