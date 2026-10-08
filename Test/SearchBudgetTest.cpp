// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#include "inc/Core/BKT/Index.h"
#include "inc/Core/SPANN/RetainedOriginalPostings.h"
#include <cstring>
#include <iostream>
#include <chrono>
#include <filesystem>

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
    for (int initial : {0, 1, 2}) {
        COMMON::WorkSpace space;
        space.Initialize(16, 2); space.Reset(1, 4);
        space.m_bConstructionSearch = true;
        space.m_iNumberOfCheckedLeaves = initial;
        space.m_SPTQueue.insert(NodeDistPair(1, 0));
        space.m_SPTQueue.insert(NodeDistPair(2, 1));
        tree.SearchTrees(data, distance, query, space, 50);
        CHECK(space.m_iNumberOfCheckedLeaves == (std::max)(1, initial));
        CHECK(space.m_NGQueue.size() == (initial == 0 ? 1 : 0));
        CHECK(space.m_SPTQueue.size() == (initial == 0 ? 1 : 2));
        space.Reset(1, 4);
        CHECK(!space.m_bConstructionSearch);
    }
    std::cout << "PASS native tree queue: post-leaf boundary, exact/over-limit entry, duplicate leaf, no internal expansion or popped-leaf loss\n";
}

// Control flow from 5619bb1 BKTIndex.cpp:299-390, independent of Index::Search.
template<class T>
void UpstreamSearch(BKT::Index<T>& index, COMMON::BKTree& tree,
    const COMMON::Dataset<T>& samples, COMMON::QueryResultSet<T>& query,
    const std::function<bool(int)>& predicate, int budget, int pivots, bool searchDeleted)
{
    COMMON::WorkSpace space;
    space.Initialize((std::max)(16, budget), 2);
    space.Reset(budget, query.GetResultNum());
    const std::function<float(const T*, const T*, DimensionType)> distance =
        [&](const T* a, const T* b, DimensionType) { return index.ComputeDistance(a, b); };
    const auto live = [&](int id) { return searchDeleted || index.ContainSample(id); };
    auto& graph = index.GetMutableGraph();
    const int last = graph.m_iNeighborhoodSize - 1;
    tree.InitSearchTrees(samples, distance, query, space);
    tree.SearchTrees(samples, distance, query, space, pivots);
    while (!space.m_NGQueue.empty()) {
        const auto current = space.m_NGQueue.pop();
        int id = current.node;
        const auto* edges = graph[id];
        if (current.distance <= query.worstDist()) {
            if (edges[last] < -1) {
                const auto& group = tree[-2 - edges[last]];
                int position = -group.childStart;
                do {
                    if (live(id) && predicate(id) && !query.AddPoint(id, current.distance)) break;
                    if (position <= 0) break;
                    id = tree[position].centerid;
                } while (position++ < group.childEnd);
            } else if (live(id) && predicate(id)) {
                query.AddPoint(id, current.distance);
            }
        } else if (live(id) && (current.distance > space.m_Results.worst() ||
                               space.m_iNumberOfCheckedLeaves > budget)) {
            break;
        }
        for (int edge = 0; edge <= last; ++edge) {
            const int neighbor = edges[edge];
            if (neighbor < 0) break;
            if (space.CheckAndSet(neighbor)) continue;
            const float value = distance(query.GetQuantizedTarget(), samples[neighbor], samples.C());
            ++space.m_iNumberOfCheckedLeaves;
            if (space.m_Results.insert(value))
                space.m_NGQueue.insert(NodeDistPair(neighbor, value));
        }
        if (space.m_NGQueue.Top().distance > space.m_SPTQueue.Top().distance)
            tree.SearchTrees(samples, distance, query, space, 4 + space.m_iNumberOfCheckedLeaves);
    }
    query.SortResult();
    query.SetScanned(space.m_iNumberOfCheckedLeaves);
}

template<class T> void UpstreamBoundaries()
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
    const auto folder = std::filesystem::current_path() / ("upstream_budget_" +
        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    CHECK(!std::filesystem::exists(folder));
    CHECK(index.SaveIndex(folder.string()) == ErrorCode::Success);
    COMMON::BKTree tree;
    CHECK(tree.LoadTrees((folder / "tree.bin").string()) == ErrorCode::Success);
    COMMON::Dataset<T> samples(256, 128, 256, 256, data.data(), true);
    using Result = COMMON::QueryResultSet<T>;
    const auto same = [](Result& a, Result& b) {
        if (a.GetScanned() != b.GetScanned())
            throw std::runtime_error("Upstream/current checked leaves differ: " +
                std::to_string(a.GetScanned()) + "/" + std::to_string(b.GetScanned()));
        for (int rank = 0; rank < a.GetResultNum(); ++rank)
            CHECK(a.GetResult(rank)->VID == b.GetResult(rank)->VID &&
                  a.GetResult(rank)->Dist == b.GetResult(rank)->Dist);
    };
    for (int pivots : {1, 50}) {
        CHECK(index.SetParameter("NumberOfInitialDynamicPivots",
            std::to_string(pivots).c_str()) == ErrorCode::Success);
        for (int budget : {1, 8, 49, 50, 51}) {
            for (bool searchDeleted : {false, true}) {
                for (int queryId : {0, 127, 255}) {
                    for (int capacity : {16, 256}) {
                        for (int mode : {0, 1, 2, 3}) {
                            const std::function<bool(int)> predicate = [mode](int id) {
                                return mode == 1 || (mode == 2 && id >= 250) ||
                                    (mode == 3 && id % 7 == 3);
                            };
                            const T* target = data.data() + queryId * 128;
                            Result reference(target, capacity), filtered(target, capacity),
                                posting(target, capacity), metadataResult(target, capacity);
                            Result construction(target, capacity);
                            CHECK(index.SearchIndexForConstruction(construction, predicate, budget) == ErrorCode::Success);
                            CHECK(construction.GetScanned() > 0 && construction.GetScanned() <= budget);
                            for (int rank = 0; rank < capacity; ++rank) {
                                const auto* result = construction.GetResult(rank);
                                if (result->VID < 0) {
                                    CHECK(result->VID == -1 && result->Dist == MaxDist);
                                } else {
                                    CHECK(index.ContainSample(result->VID) && predicate(result->VID));
                                    CHECK(result->Dist == index.ComputeDistance(target, index.GetSample(result->VID)));
                                }
                            }
                            UpstreamSearch(index, tree, samples, reference, predicate, budget, pivots, searchDeleted);
                            CHECK(index.SearchIndexWithResultFilter(filtered, predicate, budget, searchDeleted) == ErrorCode::Success);
                            CHECK(index.SearchIndexWithPostingNavigation(posting, predicate, nullptr, budget, searchDeleted) == ErrorCode::Success);
                            CHECK(index.SearchIndexWithFilter(metadataResult,
                                [&](const ByteArray& bytes) { return predicate(bytes.Data()[0]); },
                                budget, searchDeleted) == ErrorCode::Success);
                            same(reference, filtered);
                            same(reference, posting);
                            same(reference, metadataResult);
                            if (mode == 1) {
                                Result ordinary(target, capacity), emptyPredicate(target, capacity);
                                CHECK(index.SearchIndexWithMaxCheck(ordinary, budget, searchDeleted) == ErrorCode::Success);
                                CHECK(index.SearchIndexWithResultFilter(emptyPredicate, {}, budget, searchDeleted) == ErrorCode::Success);
                                same(reference, ordinary);
                                same(reference, emptyPredicate);
                                if (budget == 1) CHECK(ordinary.GetScanned() > budget);
                            }
                        }
                    }
                }
            }
        }
    }
    std::filesystem::remove_all(folder);
    std::cout << "PASS upstream control-flow oracle, exact IDs/distances/checks, all/none/sparse predicates, metadata, aliases/deletion and workspace reuse; bytes=" << sizeof(T) << '\n';
}

void ConstructionCandidates()
{
    BKT::Index<float> index;
    for (auto parameter : std::vector<std::pair<const char*, const char*>>{
        {"DistCalcMethod", "L2"}, {"NumberOfThreads", "1"}, {"BKTKmeansK", "8"},
        {"BKTLeafSize", "8"}, {"NeighborhoodSize", "32"}, {"TPTNumber", "1"},
        {"RefineIterations", "1"}, {"MaxCheck", "32"}, {"NumberOfInitialDynamicPivots", "50"}})
        CHECK(index.SetParameter(parameter.first, parameter.second) == ErrorCode::Success);
    std::vector<float> data(4096 * 16);
    for (int row = 0; row < 4096; ++row)
        for (int col = 0; col < 16; ++col)
            data[row * 16 + col] = float((row * 31 + col * 17) % 4093);
    CHECK(index.BuildIndex(data.data(), 4096, 16) == ErrorCode::Success);
    for (const char* bfs : {"0", "2"}) {
        CHECK(index.SetParameter("EnableBfs", bfs) == ErrorCode::Success);
        for (int matches : {4096, 256, 64, 16, 1, 0}) {
            COMMON::QueryResultSet<float> query(data.data(), 64);
            CHECK(index.SearchIndexForConstruction(query, [matches](int id) { return id < matches; }) == ErrorCode::Success);
            CHECK(query.GetScanned() > 0 && query.GetScanned() <= 32);
            for (int rank = 0; rank < 64; ++rank)
                CHECK(query.GetResult(rank)->VID < matches);
        }
    }
    using Work = Helper::BuildProgress::Work;
    const std::vector<SizeType> supported{0, 1, 2, 3};
    for (bool expanded : {false, true}) {
        Work direct;
        COMMON::QueryResultSet<float> all(data.data(), 4);
        CHECK(SPANN::SearchLimitedTagPostingCandidates(index, supported, expanded, all,
            [](auto&) { throw std::runtime_error("Small support must not search ANN"); return ErrorCode::Fail; },
            direct) == ErrorCode::Success);
        CHECK(direct.direct == 1 && direct.exactDistances == 4 && direct.checked == 0 && direct.fallbacks == 0);
        for (int i = 0; i < 4; ++i) CHECK(all.GetResult(i)->VID == i);
        Work fallback;
        COMMON::QueryResultSet<float> empty(data.data(), 2);
        CHECK(SPANN::SearchLimitedTagPostingCandidates(index, supported, expanded, empty,
            [](auto& query) { query.SetScanned(1); return ErrorCode::Success; }, fallback) == ErrorCode::Success);
        CHECK(fallback.direct == 0 && fallback.checked == 1 &&
            fallback.fallbacks == 1 && fallback.exactDistances == 4);
        CHECK(empty.GetResult(0)->VID == 0 && empty.GetResult(1)->VID == (expanded ? 1 : -1));
        Work rejected;
        CHECK(SPANN::SearchLimitedTagPostingCandidates(index, {}, expanded, empty,
            [](auto&) { return ErrorCode::Success; }, rejected) == ErrorCode::Fail);
    }
    COMMON::QueryResultSet<float> invalid(data.data(), 64);
    CHECK(index.SearchIndexForConstruction(invalid, {}, -1) == ErrorCode::FailedParseValue);
    std::cout << "PASS 4096-node underfill capped at 32, BFS, tiny support direct path, exact fallback and missing-support failure\n";
}

int main()
{
    try {
        TreeQueueBoundary();
        UpstreamBoundaries<float>();
        UpstreamBoundaries<std::uint8_t>();
        ConstructionCandidates();
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
