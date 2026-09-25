#include "OnlineNavigation.h"
#include <iostream>
#include <set>

using namespace OnlineOwners;
void Require(bool v) { if (!v) throw std::runtime_error("Online owner fixture failed"); }
int main() {
    std::array<int, 16> graph;
    for (int i = 0; i < 16; ++i) graph[i] = (i + 1) % 16;
    std::vector<std::uint32_t> h1(16), h2(8);
    for (int i = 0; i < 16; ++i) h1[i] = i;
    for (int i = 0; i < 8; ++i) h2[i] = i;
    std::vector<std::pair<int,int>> rowVisits;
    Model model;
    model.counts = {16,8,8};
    bool query = false;
    model.children = [&](int level, int parent) -> Model::Children {
        if (query) rowVisits.emplace_back(level, parent);
        auto& row = level == 1 ? h1 : h2;
        return {row.data(), row.data() + row.size()};
    };
    model.graph = [&](int id) { return std::make_pair(&graph[id], 1); };
    model.BuildOwners();
    query = true;
    Search engine;
    auto execute = [&](int cap, bool online, bool profile, bool rejectAll, bool reverseQuery) {
        rowVisits.clear();
        std::set<std::pair<int,int>> calls;
        std::vector<int> offered, accepted;
        engine.Reset(model, cap, 2, online, profile, true,
            [&](int level, int id) {
                Require(calls.emplace(level, id).second);
                return level == 0 ? float(10 + id) :
                    level == 1 ? float(10 + 10 * (reverseQuery ? id : 7 - id)) :
                                 float(1 + (reverseQuery ? id : 7 - id));
            },
            [&](int id, float) {
                offered.push_back(id);
                if (!rejectAll && id % 2 == 0) accepted.push_back(id);
            });
        engine.Score(0, 0, 0);
        engine.Run();
        Require(engine.stats.used == calls.size() && engine.stats.used <= unsigned(cap));
        Require(offered.size() == engine.stats.h1Candidates);
        Require(std::set<int>(offered.begin(), offered.end()).size() == offered.size());
        if (rejectAll) Require(accepted.empty());
        return engine.stats;
    };
    for (int cap : {1, 8, 17, 32, 64, 128}) {
        for (bool enabled : {false, true}) {
            auto a = execute(cap, enabled, false, false, false);
            auto b = execute(cap, enabled, true, true, false);
            Require(a.trace == b.trace && a.used == b.used);
            Require(a.graphCalls == b.graphCalls && a.parentCalls == b.parentCalls &&
                    a.childCalls == b.childCalls);
            if (!enabled) Require(a.parentCalls == 0 && a.childCalls == 0 && a.memberEntries == 0);
        }
    }
    auto normal = execute(128, true, true, false, false);
    Require(rowVisits.front() == std::make_pair(1, 7));
    auto firstH3 = std::find_if(rowVisits.begin(), rowVisits.end(), [](auto p) { return p.first == 2; });
    Require(firstH3 != rowVisits.end() && firstH3->second == 7);
    Require(normal.rows[0] == 8 && normal.rows[1] == 8);
    Require(normal.memberEntries == 8 * 16 + 8 * 8);
    Require(normal.batches == normal.memberEntries / 2);
    Require(normal.used == 32 && normal.h1Candidates == 16 && normal.cacheHits > 0);
    execute(128, true, true, false, true);
    Require(rowVisits.front() == std::make_pair(1, 0));
    firstH3 = std::find_if(rowVisits.begin(), rowVisits.end(), [](auto p) { return p.first == 2; });
    Require(firstH3 != rowVisits.end() && firstH3->second == 0);
    int callbacks = 0, admissions = 0;
    engine.Reset(model, 3, 2, false, true, true,
        [&](int, int) { ++callbacks; return 1.0f; },
        [&](int, float) { ++admissions; });
    engine.Score(0, 0, 0, true);
    engine.Score(0, 0, 0, true);
    engine.Score(0, 0, 0);
    Require(callbacks == 2 && admissions == 1 && engine.stats.used == 2 &&
            engine.stats.independent == 2 && engine.stats.cacheHits == 1);
    query = false;
    h1.pop_back();
    bool failed = false;
    try { model.BuildOwners(); } catch (const std::runtime_error&) { failed = true; }
    Require(failed);
    std::cout << "PASS: eight direct owners, QUERY-ranked H2/H3 order, retained alternatives, "
                 "bounded cursor batches, exact cache/budget charges, all-evaluated-H1 admission, "
                 "predicate/count-mode invariant traversal, malformed ownership rejection\n";
}
