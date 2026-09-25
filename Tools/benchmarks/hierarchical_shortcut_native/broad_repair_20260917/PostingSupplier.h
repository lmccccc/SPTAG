#pragma once
#include "NativeNeighborHooks.h"
#include "DefaultAdmission.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace NativeReuse {
struct PostingModel {
    using Row = std::pair<const std::uint32_t*, const std::uint32_t*>;
    std::array<int, 3> counts{};
    bool allOwnHeadsValid = false;
    std::uint64_t defaultVectorCount = 0;
    std::array<std::vector<std::array<int, 8>>, 2> owners;
    std::function<Row(int, int)> children;
    void BuildOwners() {
        for (int level = 1; level <= 2; ++level) {
            auto& inverse = owners[level - 1];
            inverse.resize(counts[level - 1]);
            std::vector<int> sizes(counts[level - 1], 0);
            for (int id = 0; id < counts[level]; ++id) {
                const auto row = children(level, id);
                for (auto p = row.first; p != row.second; ++p) {
                    if (*p >= static_cast<unsigned>(counts[level - 1]))
                        throw std::runtime_error("Invalid native supplier owner");
                    auto& out = inverse[*p];
                    auto& size = sizes[*p];
                    if (size == 8 || std::find(out.begin(), out.begin() + size, id) != out.begin() + size)
                        throw std::runtime_error("Duplicate native supplier owner");
                    out[size++] = id;
                }
            }
            for (int size : sizes)
                if (size != 8) throw std::runtime_error("Expected original eight owners");
        }
    }
};

// This object owns posting expansion state only. It has no H1 search heap,
// visited set, distance interception, H1 score cache or navigation loop.
class PostingSupplier {
    PostingModel& model;
    SPTAG::COMMON::NativeNeighborHooks& hooks;
    std::function<bool(int, int)> signature;
    std::function<float(int, int)> distance;
    std::array<std::vector<unsigned char>, 2> state;
    std::array<std::unordered_map<int, float>, 2> parentDistances;
    std::vector<std::vector<std::pair<float, int>>> discovered;
    bool RowAllowed(int level, int id) {
        auto& s = state.at(level - 1).at(id);
        if (s == 2 || s == 3) return false;
        if (s == 1) return true;
        ++hooks.signatureChecks;
        const bool pass = signature(level, id);
        s = pass ? 1 : 3;
        if (!pass) {
            ++hooks.signatureRejects;
            if (hooks.capture) hooks.rejectedRows.emplace_back(level, id);
        }
        return pass;
    }
    bool UpperPending(int id) const {
        const auto& rows = discovered.at(id);
        return std::any_of(rows.begin(), rows.end(), [&](const auto& p) {
            return state[0][p.second] != 2 && state[0][p.second] != 3;
        });
    }
    float ParentDistance(int level, int id) {
        auto& cache = parentDistances.at(level - 1);
        const auto hit = cache.find(id);
        if (hit != cache.end()) return hit->second;
        const float d = distance(level, id);
        if (!std::isfinite(d) || d < 0) throw std::runtime_error("Invalid native parent distance");
        ++hooks.parentDistances;
        cache.emplace(id, d);
        return d;
    }
    std::vector<std::pair<float, int>> Rank(int level, std::vector<int> ids) {
        std::sort(ids.begin(), ids.end());
        ids.erase(std::unique(ids.begin(), ids.end()), ids.end());
        std::vector<std::pair<float, int>> ranked;
        for (int id : ids) {
            if (level == 2 && state[1].at(id) == 2) {
                if (!UpperPending(id)) continue;
            } else if (!RowAllowed(level, id)) continue;
            ranked.emplace_back(ParentDistance(level, id), id);
        }
        std::sort(ranked.begin(), ranked.end());
        return ranked;
    }
public:
    PostingSupplier(PostingModel& m, SPTAG::COMMON::NativeNeighborHooks& h,
                    std::function<bool(int, int)> s, std::function<float(int, int)> d)
        : model(m), hooks(h), signature(std::move(s)), distance(std::move(d)),
          discovered(m.counts[2]) {
        state[0].resize(m.counts[1]);
        state[1].resize(m.counts[2]);
    }
    template<class Eligible>
    void Supply(int head, int deficit, std::unordered_set<int>& identities, Eligible eligible,
                SPTAG::COMMON::NativeDegreeFrame& frame,
                const SPTAG::COMMON::NativeEdgeConsumer& native) {
        if (!native.withinBudget()) return;
        ++hooks.calls;
        struct Return { SPTAG::COMMON::NativeNeighborHooks& h; ~Return() { ++h.returns; } } finish{hooks};
        int added = 0;
        const auto open = [&](int id) {
            if (!RowAllowed(1, id)) return;
            const auto row = model.children(1, id);
            ++hooks.h2Rows;
            std::uint64_t consumed = 0;
            if (native.consumeRow) {
                SPTAG::COMMON::NativeSupplyAccounting accounting{identities, added, frame};
                native.consumeRow(row.first, static_cast<int>(row.second - row.first), accounting);
                consumed = row.second - row.first;
            } else for (auto p = row.first; p != row.second; ++p) {
                ++hooks.members; ++consumed;
                const int child = static_cast<int>(*p);
                if (child < 0 || child >= model.counts[0])
                    throw std::runtime_error("Invalid native H1 child");
                if (identities.insert(child).second && eligible(child)) {
                    ++added;
                    if (hooks.capture) frame.supplied.push_back(child);
                }
                if (child != head) native.consume(child, true);
            }
            state[0][id] = 2;
            if (hooks.capture) hooks.rows.push_back({1, id, consumed, consumed});
        };
        const auto& own = model.owners[0].at(head);
        for (auto parent : Rank(1, {own.begin(), own.end()})) {
            if (!native.withinBudget()) return;
            open(parent.second);
            if (added >= deficit) return;
        }
        if (!std::all_of(own.begin(), own.end(), [&](int id) {
                return state[0][id] == 2 || state[0][id] == 3;
            }) || !native.withinBudget()) return;
        std::vector<int> upper;
        for (int parent : own) {
            const auto& owners = model.owners[1][parent];
            upper.insert(upper.end(), owners.begin(), owners.end());
        }
        for (auto parent : Rank(2, std::move(upper))) {
            if (!native.withinBudget()) return;
            const int id = parent.second;
            auto& choices = discovered[id];
            if (state[1][id] != 2) {
                if (!RowAllowed(2, id)) continue;
                const auto row = model.children(2, id);
                ++hooks.h3Rows;
                std::uint64_t consumed = 0;
                for (auto p = row.first; p != row.second; ++p) {
                    ++hooks.members; ++consumed;
                    if (RowAllowed(1, *p)) choices.emplace_back(ParentDistance(1, *p), *p);
                }
                std::sort(choices.begin(), choices.end());
                state[1][id] = 2;
                if (hooks.capture) hooks.rows.push_back({2, id, consumed, consumed});
            }
            for (auto child : choices) {
                if (!native.withinBudget()) return;
                open(child.second);
                if (added >= deficit) return;
            }
        }
    }
};
}
