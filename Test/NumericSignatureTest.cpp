// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Core/Cache/PostingSignature.h"
#include <array>
#include <atomic>
#include <cstdlib>
#include <iostream>
#include <new>
#include <stdexcept>

using namespace SPTAG::Cache;

static std::atomic<std::size_t> allocations{0};
#if defined(__GNUC__) || defined(__clang__)
[[gnu::noinline]]
#endif
static void FreeAllocation(void* pointer) noexcept { std::free(pointer); }

void* operator new(std::size_t size) {
    ++allocations;
    if (void* pointer = std::malloc(size ? size : 1)) return pointer;
    throw std::bad_alloc();
}
void operator delete(void* pointer) noexcept { FreeAllocation(pointer); }
void operator delete(void* pointer, std::size_t) noexcept { FreeAllocation(pointer); }
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete[](void* pointer) noexcept { FreeAllocation(pointer); }
void operator delete[](void* pointer, std::size_t) noexcept { FreeAllocation(pointer); }

#define CHECK(expression) do { \
    if (!(expression)) throw std::runtime_error("Numeric signature line " + std::to_string(__LINE__)); \
} while (false)

struct Summary {
    PostingBitmask categorical;
    HierarchicalPostingMask hierarchical;
    std::vector<NumQuantParam> domains{{0, 20}, {100, 120}};
    std::vector<int> lanes{0, -1, 1, -1};
    std::array<std::uint64_t, 2 * NUM_QUANT_WORDS> numeric{};
    std::vector<std::array<std::uint32_t, 4>> records;

    void Add(const std::array<std::uint32_t, 4>& record) {
        records.push_back(record);
        for (std::size_t column = 0; column < record.size(); ++column) {
            const int lane = lanes[column];
            if (lane < 0) {
                categorical.Insert(record[column]);
                hierarchical.Insert(static_cast<int>(column), record[column]);
            } else {
                NumQuantInsert(numeric.data(), lane, NumQuantBucket(domains[lane], record[column]));
            }
        }
    }

    bool Coarse(const DNFPredicate& predicate) const {
        return predicate.MayMatchCoarseQuant(categorical, numeric.data(), 2,
                                            domains.data(), 2, 2, &lanes);
    }
    bool Hier(const DNFPredicate& predicate) const {
        return predicate.MayMatchHierQuant(hierarchical, numeric.data(), 2,
                                          domains.data(), 2, 2, HierWidths(), &lanes);
    }
    bool Exact(const DNFPredicate& predicate) const {
        for (const auto& record : records)
            if (predicate.Matches(record.data(), static_cast<int>(record.size()))) return true;
        return false;
    }
    void CheckConservative(const DNFPredicate& predicate) const {
        if (Exact(predicate)) CHECK(Coarse(predicate) && Hier(predicate));
    }
};

DNFPredicate Interval(std::uint32_t column, std::uint32_t low, std::uint32_t high) {
    DNFPredicate predicate;
    predicate.clauses.push_back({{{column, low, DNF_GE, 1}, {column, high, DNF_LE, 1}}});
    return predicate;
}

void GapAndDNFTests() {
    Summary summary;
    summary.Add({1, 11, 101, 21});
    summary.Add({15, 11, 115, 21});
    DNFPredicate gap = Interval(0, 5, 10);
    CHECK(!summary.Exact(gap) && !summary.Coarse(gap) && !summary.Hier(gap));
    gap.clauses[0].lits.push_back({1, 11, DNF_EQ, 0});
    CHECK(!summary.Coarse(gap) && !summary.Hier(gap));
    gap.clauses.push_back({{{2, 114, DNF_GE, 1}}});
    CHECK(summary.Exact(gap) && summary.Coarse(gap) && summary.Hier(gap));
    gap = Interval(2, 104, 108);
    CHECK(!summary.Coarse(gap) && !summary.Hier(gap));

    DNFPredicate crossColumn;
    crossColumn.clauses.push_back({{{0, 5, DNF_GE, 1}, {2, 105, DNF_LE, 1}}});
    CHECK(!summary.Exact(crossColumn));
    CHECK(summary.Coarse(crossColumn) && summary.Hier(crossColumn));

    gap = Interval(0, 10, 5);
    CHECK(!summary.Coarse(gap) && !summary.Hier(gap));
    CHECK(gap.MayMatchCoarseQuant(summary.categorical, nullptr, 2,
                                  summary.domains.data(), 2, 2, &summary.lanes));
    CHECK(gap.MayMatchHierQuant(summary.hierarchical, summary.numeric.data(), 2,
                                nullptr, 0, 2, HierWidths(), &summary.lanes));

    const auto before = allocations.load();
    for (int repeat = 0; repeat < 10000; ++repeat) {
        CHECK(!summary.Coarse(gap) && !summary.Hier(gap));
        CHECK(summary.Coarse(crossColumn) && summary.Hier(crossColumn));
    }
    CHECK(allocations.load() == before);
    std::cout << "PASS same-column interval intersection, interleaved schema, DNF OR, "
                 "unknown-metadata conservatism and allocation-free matching\n";
}

void BoundariesAndConservatism() {
    constexpr auto maximum = (std::numeric_limits<std::uint32_t>::max)();
    const std::vector<std::uint32_t> values{0, 1, 2, 5, 9, 10, 11, 19, 20, 21, 31, 63,
                                          maximum - 1, maximum};
    const std::array<NumQuantParam, 4> domains{{{0, maximum}, {10, 20}, {5, 5}, {0, 63}}};
    for (const auto& domain : domains) {
        for (std::uint32_t first : values) {
            for (std::uint32_t second : values) {
                for (std::uint8_t firstOp = DNF_EQ; firstOp <= DNF_GE; ++firstOp) {
                    for (std::uint8_t secondOp = DNF_EQ; secondOp <= DNF_GE; ++secondOp) {
                        DNFClause clause{{{0, first, firstOp, 1}, {0, second, secondOp, 1}}};
                        int low = -1, high = -1;
                        const bool possible = DNFPredicate::NumericRangeBuckets(clause, 0, domain, low, high);
                        for (std::uint32_t value : values) {
                            if (DNFEvalOp(firstOp, value, first) &&
                                DNFEvalOp(secondOp, value, second)) {
                                const int bucket = NumQuantBucket(domain, value);
                                CHECK(possible && low <= bucket && bucket <= high);
                            }
                        }
                        std::swap(clause.lits[0], clause.lits[1]);
                        int reversedLow = -1, reversedHigh = -1;
                        CHECK(possible == DNFPredicate::NumericRangeBuckets(
                            clause, 0, domain, reversedLow, reversedHigh));
                        if (possible) CHECK(low == reversedLow && high == reversedHigh);
                    }
                }
            }
        }
    }
    const NumQuantParam domain{0, maximum};
    for (const auto& clause : std::vector<DNFClause>{
             {{{0, 0, DNF_LT, 1}}},
             {{{0, maximum, DNF_GT, 1}}},
             {{{0, 0, DNF_GT, 1}, {0, 1, DNF_LT, 1}}},
             {{{0, 7, DNF_EQ, 1}, {0, 8, DNF_EQ, 1}}},
             {{{0, 7, DNF_GT, 1}, {0, 7, DNF_LE, 1}}}}) {
        int low = -1, high = -1;
        CHECK(!DNFPredicate::NumericRangeBuckets(clause, 0, domain, low, high));
    }

    Summary summary;
    summary.Add({0, 11, 100, 21});
    summary.Add({5, 12, 108, 22});
    summary.Add({20, 13, 120, 23});
    for (std::uint32_t low = 0; low <= 21; ++low) {
        for (std::uint32_t high = 0; high <= 21; ++high) {
            DNFPredicate predicate = Interval(0, low, high);
            summary.CheckConservative(predicate);
            predicate.clauses[0].lits.push_back({1, 12, DNF_EQ, 0});
            summary.CheckConservative(predicate);
            predicate.clauses[0].lits.push_back({2, 109, DNF_LT, 1});
            summary.CheckConservative(predicate);
            predicate.clauses.push_back({{{0, 20, DNF_EQ, 1}, {3, 23, DNF_EQ, 0}}});
            CHECK(summary.Exact(predicate) && summary.Coarse(predicate) && summary.Hier(predicate));
        }
    }
    std::cout << "PASS 19600 paired range/domain cases, strict uint32 boundaries, "
                 "constant and clamped domains, mixed-clause no-false-negative checks\n";
}

int main() {
    try {
        GapAndDNFTests();
        BoundariesAndConservatism();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
