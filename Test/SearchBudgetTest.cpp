// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#include "inc/Core/BKT/Index.h"
#include <cstring>
#include <iostream>

using namespace SPTAG;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Search budget test line " + std::to_string(__LINE__)); } while (false)

void TreeQueueBoundary()
{
    COMMON::BKTNode nodes[] = {COMMON::BKTNode(3), COMMON::BKTNode(0),
        COMMON::BKTNode(1), COMMON::BKTNode(3), COMMON::BKTNode(2), COMMON::BKTNode(-1)};
    nodes[0].childStart = 1; nodes[0].childEnd = 4;
    nodes[2].childStart = 4; nodes[2].childEnd = 5;
    std::vector<char> bytes(3 * sizeof(int) + sizeof(nodes));
    const int header[] = {1, 0, 6};
    std::memcpy(bytes.data(), header, sizeof(header));
    std::memcpy(bytes.data() + sizeof(header), nodes, sizeof(nodes));
    COMMON::BKTree tree;
    CHECK(tree.LoadTrees(bytes.data()) == ErrorCode::Success);
    float values[] = {0, 1, 2, 3};
    COMMON::Dataset<float> data(4, 1, 4, 4, values, true);
    COMMON::QueryResultSet<float> query(values, 4);
    int distanceCalls = 0;
    std::function<float(const float*, const float*, DimensionType)> distance =
        [&](const float* a, const float* b, DimensionType) {
            ++distanceCalls; return (*a - *b) * (*a - *b);
        };
    for (int initial : {0, 1, 2}) {
        COMMON::WorkSpace space;
        space.Initialize(16, 2); space.Reset(1, 4);
        space.m_iNumberOfCheckedLeaves = initial;
        space.m_SPTQueue.insert(NodeDistPair(1, 0));
        space.m_SPTQueue.insert(NodeDistPair(2, 1));
        space.m_SPTQueue.insert(NodeDistPair(3, 3));
        distanceCalls = 0;
        tree.SearchTrees(data, distance, query, space, 1);
        CHECK(space.m_iNumberOfCheckedLeaves == initial + 1);
        CHECK(space.m_NGQueue.size() == 1 && space.m_NGQueue.Top().node == 0);
        CHECK(space.nodeCheckStatus.Contains(0) && !space.nodeCheckStatus.Contains(1));
        CHECK(space.m_SPTQueue.size() == 2 && space.m_SPTQueue.Top().node == 2);
        CHECK(distanceCalls == 0);
        tree.SearchTrees(data, distance, query, space, initial + 2);
        CHECK(space.nodeCheckStatus.Contains(1) && space.nodeCheckStatus.Contains(3));
        CHECK(space.m_iNumberOfCheckedLeaves == initial + 2);
        CHECK(space.m_SPTQueue.size() == 1 && space.m_SPTQueue.Top().node == 4);
        tree.SearchTrees(data, distance, query, space, initial + 3);
        CHECK(space.nodeCheckStatus.Contains(2) && space.m_SPTQueue.empty());
        CHECK(space.m_iNumberOfCheckedLeaves == initial + 3 && distanceCalls == 1);
    }
    COMMON::WorkSpace duplicate;
    duplicate.Initialize(16, 2); duplicate.Reset(1, 4);
    CHECK(!duplicate.CheckAndSet(0));
    duplicate.m_iNumberOfCheckedLeaves = 1;
    duplicate.m_SPTQueue.insert(NodeDistPair(1, 0));
    duplicate.m_SPTQueue.insert(NodeDistPair(2, 1));
    tree.SearchTrees(data, distance, query, duplicate, 1);
    CHECK(duplicate.m_iNumberOfCheckedLeaves == 1 && duplicate.m_NGQueue.empty());
    CHECK(duplicate.m_SPTQueue.size() == 1 && duplicate.m_SPTQueue.Top().node == 2);
    std::cout << "PASS native tree queue: post-leaf boundary, exact/over-limit entry, duplicate leaf, no internal expansion or popped-leaf loss\n";
}

template<class T> void FilteredBoundaries()
{
    BKT::Index<T> index;
    for (auto parameter : std::vector<std::pair<const char*, const char*>>{
        {"DistCalcMethod", "L2"}, {"NumberOfThreads", "1"}, {"BKTKmeansK", "8"},
        {"BKTLeafSize", "1"}, {"NeighborhoodSize", "32"}, {"TPTNumber", "1"},
        {"NumberOfInitialDynamicPivots", "50"}, {"NumberOfOtherDynamicPivots", "4"}})
        CHECK(index.SetParameter(parameter.first, parameter.second) == ErrorCode::Success);
    std::vector<T> data(256 * 128);
    for (int i = 0; i < 256; ++i)
        std::fill_n(data.data() + i * 128, 128, static_cast<T>(i));
    std::copy_n(data.data() + 253 * 128, 128, data.data() + 254 * 128);
    std::copy_n(data.data() + 253 * 128, 128, data.data() + 255 * 128);
    CHECK(index.BuildIndex(data.data(), 256, 128) == ErrorCode::Success);
    auto metadata = ByteArray::Alloc(256);
    auto offsets = ByteArray::Alloc(257 * sizeof(std::uint64_t));
    for (std::uint64_t i = 0; i <= 256; ++i) {
        std::memcpy(offsets.Data() + i * sizeof(i), &i, sizeof(i));
        if (i < 256) metadata.Data()[i] = static_cast<std::uint8_t>(i);
    }
    index.SetMetadata(new MemMetadataSet(metadata, offsets, 256));
    CHECK(index.DeleteIndex(254) == ErrorCode::Success);
    using Result = COMMON::QueryResultSet<T>;
    for (int budget : {1, 8, 49, 50, 51}) {
        for (int mode : {0, 1, 2}) {
            auto predicate = [mode](int id) {
                return mode == 1 || (mode == 2 && id >= 250);
            };
            Result filtered(data.data(), 256), posting(data.data(), 256), metadata(data.data(), 256);
            CHECK(index.SearchIndexWithResultFilter(filtered, predicate, budget) == ErrorCode::Success);
            CHECK(index.SearchIndexWithPostingNavigation(posting, predicate, nullptr, budget) == ErrorCode::Success);
            CHECK(index.SearchIndexWithFilter(metadata, [](const ByteArray&) { return false; }, budget) == ErrorCode::Success);
            CHECK(filtered.GetScanned() <= budget && posting.GetScanned() <= budget &&
                  metadata.GetScanned() <= budget);
            for (int rank = 0; rank < 256; ++rank) {
                const auto* a = filtered.GetResult(rank);
                const auto* b = posting.GetResult(rank);
                CHECK(a->VID == b->VID && a->Dist == b->Dist);
                CHECK(a->VID < 0 || (a->VID != 254 && predicate(a->VID)));
            }
        }
        Result ordinary(data.data(), 256), emptyPredicate(data.data(), 256);
        CHECK(index.SearchIndexWithMaxCheck(ordinary, budget) == ErrorCode::Success);
        CHECK(index.SearchIndexWithResultFilter(emptyPredicate, {}, budget) == ErrorCode::Success);
        CHECK(ordinary.GetScanned() == emptyPredicate.GetScanned());
        if (budget == 1) CHECK(ordinary.GetScanned() > budget);
        for (int rank = 0; rank < 256; ++rank)
            CHECK(ordinary.GetResult(rank)->VID == emptyPredicate.GetResult(rank)->VID);
    }
    std::cout << "PASS filtered cap, all/none/sparse predicates, aliases/deletion, empty-predicate ordinary overshoot; bytes=" << sizeof(T) << '\n';
}

int main()
{
    try {
        TreeQueueBoundary();
        FilteredBoundaries<float>();
        FilteredBoundaries<std::uint8_t>();
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
