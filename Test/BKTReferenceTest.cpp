// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#include "inc/Core/BKT/Index.h"
#include <fstream>
#include <iostream>

using namespace SPTAG;
using namespace SPTAG::COMMON;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Pinned BKT reference line " + std::to_string(__LINE__)); } while (false)

struct PinnedTree {
    int m_iTreeNumber = 0, m_bfs = 0;
    std::vector<SizeType> m_pTreeStart;
    std::vector<BKTNode> m_pTreeRoots;
    std::shared_ptr<std::shared_timed_mutex> m_lock = std::make_shared<std::shared_timed_mutex>();
    const BKTNode& operator[](SizeType id) const { return m_pTreeRoots.at(id); }
    explicit PinnedTree(const std::string& filename) {
        std::ifstream in(filename, std::ios::binary);
        in.read(reinterpret_cast<char*>(&m_iTreeNumber), sizeof(int));
        CHECK(in && m_iTreeNumber > 0 && m_iTreeNumber < 128);
        m_pTreeStart.resize(m_iTreeNumber);
        in.read(reinterpret_cast<char*>(m_pTreeStart.data()), sizeof(SizeType) * m_iTreeNumber);
        SizeType count = 0;
        in.read(reinterpret_cast<char*>(&count), sizeof(count));
        CHECK(in && count > 0);
        m_pTreeRoots.resize(count);
        in.read(reinterpret_cast<char*>(m_pTreeRoots.data()), sizeof(BKTNode) * count);
        CHECK(in && in.peek() == std::char_traits<char>::eof());
    }
#include "PinnedTree.inc"
};

template<class T> struct PinnedReference {
    PinnedTree m_pTrees;
    Dataset<T> m_pSamples;
    const RelativeNeighborhoodGraph& m_pGraph;
    LabelSet m_deletedID;
    std::shared_ptr<MetadataSet> m_pMetadata;
    std::function<float(const T*, const T*, DimensionType)> m_fComputeDistance;
    int m_iNumberOfInitialDynamicPivots, m_iNumberOfOtherDynamicPivots;
    std::uint64_t distanceCalls = 0;
    explicit PinnedReference(const BKT::Index<T>& index, const std::string& directory)
        : m_pTrees(directory + "/tree.bin"), m_pGraph(index.GetGraph()),
          m_iNumberOfInitialDynamicPivots(std::stoi(index.GetParameter("NumberOfInitialDynamicPivots"))),
          m_iNumberOfOtherDynamicPivots(std::stoi(index.GetParameter("NumberOfOtherDynamicPivots"))) {
        CHECK(index.GetNumDeleted() == 0);
        CHECK(m_pSamples.Load(directory + "/vectors.bin", 1048576, MaxSize) == ErrorCode::Success);
        m_deletedID.Initialize(index.GetNumSamples(), 1048576, MaxSize,
                               LabelSet::InvalidIDBehavior::AlwaysContains);
        m_fComputeDistance = [this, &index](const T* a, const T* b, DimensionType) {
            ++distanceCalls; return index.ComputeDistance(a, b);
        };
    }
    DimensionType GetFeatureDim() const { return m_pSamples.C(); }
    template<bool (*notDeleted)(const LabelSet&, SizeType),
             bool (*isDup)(QueryResultSet<T>&, SizeType, float),
             bool (*checkFilter)(const std::shared_ptr<MetadataSet>&, SizeType,
                                 std::function<bool(const ByteArray&)>)>
    void Search(QueryResultSet<T>&, WorkSpace&, std::function<bool(const ByteArray&)>) const;
    template<bool (*notDeleted)(const LabelSet&, SizeType),
             bool (*isDup)(QueryResultSet<T>&, SizeType, float),
             bool (*checkFilter)(const std::shared_ptr<MetadataSet>&, SizeType,
                                 std::function<bool(const ByteArray&)>)>
    int SearchIterative(QueryResultSet<T>&, WorkSpace&, bool, int) const;
};
#include "PinnedSearch.inc"
#include "PinnedIterator.inc"

static bool Live(const LabelSet& deleted, SizeType id) { return !deleted.Contains(id); }
static bool Add(QueryResultSet<float>& result, SizeType id, float distance) {
    return !result.AddPoint(id, distance);
}
static bool Accept(const std::shared_ptr<MetadataSet>&, SizeType,
                   std::function<bool(const ByteArray&)>) { return true; }

int main(int argc, char** argv)
{
    try {
        CHECK(argc == 3);
        std::shared_ptr<VectorIndex> loaded;
        CHECK(VectorIndex::LoadIndex(argv[1], loaded) == ErrorCode::Success);
        auto* index = dynamic_cast<BKT::Index<float>*>(loaded.get());
        CHECK(index && !index->m_pQuantizer);
        CHECK(index->SetParameter("NumberOfThreads", "1") == ErrorCode::Success);
        PinnedReference<float> reference(*index, argv[1]);
        Dataset<float> queries;
        CHECK(queries.Load(std::string(argv[2]), 1048576, MaxSize) == ErrorCode::Success);
        CHECK(queries.R() >= 1000 && queries.C() == index->GetFeatureDim());
        std::ofstream raw("reference-controls.tsv");
        raw << "budget\tnprobe\tquery\tchecked\tactual_checked\tdistances\tactual_distances\tadaptive_exit\tovershoot\n";
        std::uint64_t compared = 0, adaptive = 0, overshoot = 0;
        for (int budget : {1, 8, 32, 2048, 4096}) {
            for (int k : {1, 16, 24, 48, 96, 192, 384}) {
                std::uint64_t early = 0, beyond = 0;
                for (int q = 0; q < 1000; ++q) {
                    QueryResultSet<float> expected(queries[q], k), actual(queries[q], k);
                    WorkSpace space;
                    space.Initialize(std::max(16, budget), 4);
                    space.Reset(budget, k);
                    reference.distanceCalls = 0;
                    reference.Search<Live, Add, Accept>(expected, space, nullptr);
                    GraphAccessStats stats;
                    {
                        ScopedGraphAccessStats scope(&stats);
                        CHECK(index->SearchIndexWithMaxCheck(actual, budget) == ErrorCode::Success);
                    }
                    const bool earlyExit = !space.m_NGQueue.empty() &&
                        space.m_iNumberOfCheckedLeaves <= budget;
                    const bool capOvershoot = space.m_iNumberOfCheckedLeaves > budget;
                    early += earlyExit; beyond += capOvershoot;
                    raw << budget << '\t' << k << '\t' << q << '\t'
                        << space.m_iNumberOfCheckedLeaves << '\t' << actual.GetScanned() << '\t'
                        << reference.distanceCalls << '\t' << stats.m_distanceCalls << '\t'
                        << earlyExit << '\t' << capOvershoot << '\n';
                    if (actual.GetScanned() != space.m_iNumberOfCheckedLeaves ||
                        stats.m_distanceCalls != reference.distanceCalls)
                        throw std::runtime_error("Reference work differs: budget=" + std::to_string(budget) +
                            " k=" + std::to_string(k) + " query=" + std::to_string(q));
                    for (int i = 0; i < k; ++i) {
                        CHECK(actual.GetResult(i)->VID == expected.GetResult(i)->VID);
                        CHECK(actual.GetResult(i)->Dist == expected.GetResult(i)->Dist);
                    }
                    ++compared;
                }
                adaptive += early; overshoot += beyond;
                std::cout << "{\"budget\":" << budget << ",\"nprobe\":" << k
                          << ",\"queries\":1000,\"exact_ids_distances_checked_calls\":true"
                          << ",\"adaptive_exits\":" << early << ",\"overshoots\":" << beyond << "}\n";
            }
        }
        CHECK(raw && adaptive > 0 && overshoot > 0);
        std::cout << "PASS pinned official native bodies: " << compared
                  << " searches, adaptive=" << adaptive << ", overshoot=" << overshoot << '\n';
        std::uint64_t rounds = 0;
        for (int budget : {1, 32, 2048}) for (int batch : {1, 24}) {
            CHECK(index->SetParameter("MaxCheck", std::to_string(budget).c_str()) == ErrorCode::Success);
            for (int q = 0; q < 100; ++q) {
                WorkSpace expectedSpace;
                expectedSpace.Initialize(std::max(16, budget), 4);
                expectedSpace.Reset(budget, batch);
                auto actualSpace = std::make_unique<WorkSpace>();
                actualSpace->Initialize(std::max(16, budget), 4);
                actualSpace->Reset(budget, batch);
                for (int step = 0; step < 8; ++step) {
                    QueryResultSet<float> expected(queries[q], batch), actual(queries[q], batch);
                    const int count = reference.SearchIterative<Live, Add, Accept>(
                        expected, expectedSpace, step == 0, batch);
                    int actualCount = 0;
                    CHECK(index->SearchIndexIterativeNext(actual, actualSpace.get(), batch,
                        actualCount, step == 0, false) == ErrorCode::Success);
                    CHECK(count == actualCount);
                    CHECK(expectedSpace.m_iNumberOfCheckedLeaves == actualSpace->m_iNumberOfCheckedLeaves);
                    CHECK(expectedSpace.m_relaxedMono == actualSpace->m_relaxedMono);
                    CHECK(expectedSpace.m_SPTQueue.size() == actualSpace->m_SPTQueue.size());
                    CHECK(expectedSpace.m_NGQueue.size() == actualSpace->m_NGQueue.size());
                    for (int i = 0; i < batch; ++i) {
                        CHECK(expected.GetResult(i)->VID == actual.GetResult(i)->VID);
                        CHECK(expected.GetResult(i)->Dist == actual.GetResult(i)->Dist);
                    }
                    ++rounds;
                }
                CHECK(index->SearchIndexIterativeEnd(std::move(actualSpace)) == ErrorCode::Success);
            }
        }
        std::cout << "PASS pinned official iterator: " << rounds
                  << " rounds, exact IDs/distances/checked/queue sizes/relaxed monotonicity\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
