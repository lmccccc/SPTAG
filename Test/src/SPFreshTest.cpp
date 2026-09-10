// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Core/Common/CommonUtils.h"
#include "inc/Core/Common/DistanceUtils.h"
#include "inc/Core/Common/QueryResultSet.h"
#include "inc/Core/BKT/Index.h"
#include "inc/Core/KDT/Index.h"
#include "inc/Core/SPANN/Index.h"
#include "inc/Core/SPANN/ExtraDynamicSearcher.h"
#include "inc/Core/SPANN/LimitedTagSupport.h"
#include "inc/Core/SPANN/HeadNodeMetadata.h"
#include "inc/Core/SPANN/SecondLevelHierarchy.h"
#include "inc/Core/SPANN/SPANNResultIterator.h"
#include "inc/Core/VectorIndex.h"
#include "inc/Core/Common/IQuantizer.h"
#include "inc/Core/Common/PQQuantizer.h"
#include "inc/Helper/AtomicFile.h"
#include "inc/Helper/DiskIO.h"
#include "inc/Helper/HeadCrossEdges.h"
#include "inc/Helper/SimpleIniReader.h"
#include "inc/Helper/VectorSetReader.h"
#include "inc/Helper/StringConvert.h"
#include "inc/Quantizer/Training.h"
#include "inc/Test.h"
#include "inc/ScopedEnvironmentVariable.h"
#include "inc/TestDataGenerator.h"

#include <algorithm>
#include <atomic>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <memory>
#include <numeric>
#include <string>
#include <thread>
#include <ctime>
#include <tuple>
#include <unordered_map>
#include <vector>

using namespace SPTAG;

namespace SPTAG
{
namespace SPANN
{
template <typename T>
ErrorCode SearchSecondLevelHierarchy(
    COMMON::QueryResultSet<T>& p_results,
    int p_resultBudget,
    int p_maxCheck,
    double p_probeRatio,
    const Cache::PostingBitmask&,
    const std::function<bool(SizeType)>& p_headAdmission,
    const std::shared_ptr<VectorIndex>& p_distanceIndex,
    const std::vector<std::shared_ptr<VectorIndex>>& p_indexes,
    const std::vector<std::shared_ptr<VectorSet>>& p_catalogs,
    const std::vector<SecondLevelHeadPostings>& p_postings,
    SecondLevelHierarchySearchStats& p_stats,
    std::string* p_workLog = nullptr,
    bool p_profile = false,
    const LimitedTagSupport* = nullptr,
    const std::function<bool(SizeType, const float*)>& p_headPointCandidate = nullptr,
    SecondLevelHierarchyDetail::SearchWorkspace* p_workspace = nullptr,
    bool p_batchVectorPrefetch = false,
    bool = false)
{
    return SearchSecondLevelHierarchy(
        p_results, p_resultBudget, p_maxCheck, p_probeRatio,
        p_headAdmission, p_distanceIndex, p_indexes, p_catalogs,
        p_postings, p_stats, p_workLog, p_profile,
        p_headPointCandidate, p_workspace, p_batchVectorPrefetch);
}
}
}

namespace SPFreshTest
{
SizeType N = 10000;
DimensionType M = 100;
int K = 10;
int queries = 10;

std::shared_ptr<VectorSet> ConvertToFloatVectorSet(const std::shared_ptr<VectorSet>& src)
{
    if (!src)
        return nullptr;

    if (src->GetValueType() == VectorValueType::Float)
        return src;

    SizeType count = src->Count();
    DimensionType dim = src->Dimension();
    ByteArray bytes = ByteArray::Alloc(sizeof(float) * (size_t)count * (size_t)dim);
    float* out = reinterpret_cast<float*>(bytes.Data());

    switch (src->GetValueType())
    {
    case VectorValueType::Int8:
    {
        auto* in = reinterpret_cast<const std::int8_t*>(src->GetData());
        for (size_t i = 0; i < (size_t)count * (size_t)dim; ++i)
            out[i] = static_cast<float>(in[i]);
        break;
    }
    case VectorValueType::UInt8:
    {
        auto* in = reinterpret_cast<const std::uint8_t*>(src->GetData());
        for (size_t i = 0; i < (size_t)count * (size_t)dim; ++i)
            out[i] = static_cast<float>(in[i]);
        break;
    }
    case VectorValueType::Int16:
    {
        auto* in = reinterpret_cast<const std::int16_t*>(src->GetData());
        for (size_t i = 0; i < (size_t)count * (size_t)dim; ++i)
            out[i] = static_cast<float>(in[i]);
        break;
    }
    default:
        return nullptr;
    }

    return std::make_shared<BasicVectorSet>(bytes, VectorValueType::Float, dim, count);
}

std::shared_ptr<COMMON::IQuantizer> EnsurePQQuantizer(const std::string& quantizerFile,
                                                     const std::shared_ptr<VectorSet>& trainVectors,
                                                     DimensionType quantizedDim,
                                                     int threadNum)
{
    if (!trainVectors)
        return nullptr;

    std::shared_ptr<COMMON::IQuantizer> quantizer;
    if (fileexists(quantizerFile.c_str())) {
        auto ptr = SPTAG::f_createIO();
        if (ptr->Initialize(quantizerFile.c_str(), std::ios::binary | std::ios::in))
        {
            quantizer = COMMON::IQuantizer::LoadIQuantizer(ptr);
            BOOST_REQUIRE(quantizer != nullptr);
            return quantizer;
        }
    }

    if (quantizedDim <= 0 || (trainVectors->Dimension() % quantizedDim) != 0)
        return nullptr;

    auto options = std::make_shared<QuantizerOptions>(
        trainVectors->Count(), false, 0.0f, QuantizerType::PQQuantizer, quantizerFile, quantizedDim, "", "");
    options->m_dimension = trainVectors->Dimension();
    options->m_threadNum = threadNum;
    options->m_inputValueType = VectorValueType::Float;
    options->m_trainingSamples = trainVectors->Count();

    ByteArray pq_vector_array = ByteArray::Alloc(sizeof(std::uint8_t) * (size_t)quantizedDim * (size_t)trainVectors->Count());
    auto pq_vectors = std::make_shared<BasicVectorSet>(pq_vector_array, VectorValueType::UInt8, quantizedDim, trainVectors->Count());

    auto codebooks = TrainPQQuantizer<float>(options, trainVectors, pq_vectors);
    if (!codebooks)
        return nullptr;

    quantizer = std::make_shared<COMMON::PQQuantizer<float>>(
        quantizedDim, 256, trainVectors->Dimension() / quantizedDim, false, std::move(codebooks));

    auto fp = SPTAG::f_createIO();
    if (fp != nullptr && fp->Initialize(quantizerFile.c_str(), std::ios::binary | std::ios::out))
        quantizer->SaveQuantizer(fp);

    return quantizer;
}

template <typename T>
std::shared_ptr<VectorIndex> BuildIndex(const std::string &outDirectory, std::shared_ptr<VectorSet> vecset,
                                        std::shared_ptr<MetadataSet> metaset, const std::string &distMethod = "L2", int searchthread = 2)
{
    auto vecIndex = VectorIndex::CreateInstance(IndexAlgoType::SPANN, GetEnumValueType<T>());
    int maxthreads = std::thread::hardware_concurrency();
    int postingLimit = 4 * sizeof(T);
    std::string configuration = R"(
        [Base]
            DistCalcMethod=)" + distMethod + R"(
            IndexAlgoType=BKT
            ValueType=)" + Helper::Convert::ConvertToString(GetEnumValueType<T>()) + 
                                R"(
            Dim=)" + std::to_string(M) +
                                R"(
            IndexDirectory=)" + outDirectory +
                                R"(

        [SelectHead]
            isExecute=true
            NumberOfThreads=)" + std::to_string(maxthreads) + R"(
            SelectHeadType=BKT
            SelectThreshold=0
            SplitFactor=0
            SplitThreshold=0
            Ratio=0.2

        [BuildHead]
            isExecute=true
            NumberOfThreads=)" + std::to_string(maxthreads) + R"(

        [BuildSSDIndex]
            isExecute=true
            BuildSsdIndex=true
            InternalResultNum=64
            SearchInternalResultNum=64
            NumberOfThreads=)" + std::to_string(maxthreads) + R"(
	        PostingPageLimit=)" + std::to_string(postingLimit) + R"(
            SearchPostingPageLimit=)" + std::to_string(postingLimit) + R"(
            TmpDir=tmpdir
            Storage=FILEIO
            SpdkBatchSize=64
            ExcludeHead=false
            ResultNum=10
            SearchThreadNum=)" + std::to_string(searchthread) + R"(
            Update=true
            SteadyState=true
            InsertThreadNum=1
            AppendThreadNum=1
            ReassignThreadNum=0
            DisableReassign=false
            ReassignK=64
            LatencyLimit=50.0
            SearchDuringUpdate=true
            MergeThreshold=10
            Sampling=4
            BufferLength=)" + std::to_string(postingLimit) + R"(
            InPlace=true
            StartFileSizeGB=1
            OneClusterCutMax=true
            ConsistencyCheck=true
            ChecksumCheck=true
            ChecksumInRead=false
            AsyncMergeInSearch=false
            DeletePercentageForRefine=0.4
            AsyncAppendQueueSize=0
            AllowZeroReplica=false
        )";

    std::shared_ptr<Helper::DiskIO> buffer(new Helper::SimpleBufferIO());
    Helper::IniReader reader;
    if (!buffer->Initialize(configuration.data(), std::ios::in, configuration.size()))
        return nullptr;
    if (ErrorCode::Success != reader.LoadIni(buffer))
        return nullptr;

    std::string sections[] = {"Base", "SelectHead", "BuildHead", "BuildSSDIndex"};
    for (const auto &sec : sections)
    {
        auto params = reader.GetParameters(sec.c_str());
        for (const auto &[key, val] : params)
        {
            vecIndex->SetParameter(key.c_str(), val.c_str(), sec.c_str());
        }
    }

    auto buildStatus = vecIndex->BuildIndex(vecset, metaset, true, false, false);
    if (buildStatus != ErrorCode::Success)
        return nullptr;

    return vecIndex;
}

template <typename T>
std::shared_ptr<VectorIndex> BuildLargeIndex(const std::string &outDirectory, std::string &pvecset,
                                        std::string& pmetaset, std::string& pmetaidx, const std::string &distMethod = "L2",
                                        int searchthread = 2, int insertthread = 2, std::shared_ptr<COMMON::IQuantizer> quantizer = nullptr, std::string quantizerFilePath = "quantizer.bin")
{
    auto vecIndex = VectorIndex::CreateInstance(IndexAlgoType::SPANN, GetEnumValueType<T>());
    int maxthreads = std::thread::hardware_concurrency();
    int postingLimit = 4 * sizeof(T);
    std::string configuration = R"(
        [Base]
            DistCalcMethod=)" + distMethod + R"(
            IndexAlgoType=BKT
            VectorPath=)" + pvecset + R"(
            ValueType=)" + Helper::Convert::ConvertToString(GetEnumValueType<T>()) +
                                R"(
            Dim=)" + std::to_string(M) +
                                R"(
            IndexDirectory=)" + outDirectory +
                                R"(

        [SelectHead]
            isExecute=true
            NumberOfThreads=)" + std::to_string(maxthreads) + R"(
            SelectHeadType=BKT
            SelectThreshold=0
            SplitFactor=0
            SplitThreshold=0
            Ratio=0.2

        [BuildHead]
            isExecute=true
            AddCountForRebuild=10000
            NumberOfThreads=)" + std::to_string(maxthreads) + R"(

        [BuildSSDIndex]
            isExecute=true
            BuildSsdIndex=true
            InternalResultNum=64
            SearchInternalResultNum=64
            NumberOfThreads=)" + std::to_string(maxthreads) + R"(
	        PostingPageLimit=)" + std::to_string(postingLimit) +
                                R"(
            SearchPostingPageLimit=)" +
                                std::to_string(postingLimit) + R"(
            TmpDir=tmpdir
            Storage=FILEIO
            SpdkBatchSize=64
            ExcludeHead=false
            ResultNum=10
            SearchThreadNum=)" + std::to_string(searchthread) + R"(
            Update=true
            SteadyState=true
            InsertThreadNum=1
            AppendThreadNum=)" + std::to_string(insertthread) + R"(
            ReassignThreadNum=0
            DisableReassign=false
            ReassignK=64
            LatencyLimit=50.0
            SearchDuringUpdate=true
            MergeThreshold=10
            Sampling=4
            BufferLength=)" + std::to_string(postingLimit) +  R"(
            InPlace=true
            StartFileSizeGB=1
            OneClusterCutMax=true
            ConsistencyCheck=false
            ChecksumCheck=false
            ChecksumInRead=false
            AsyncMergeInSearch=false
            DeletePercentageForRefine=0.4
            AsyncAppendQueueSize=0
            AllowZeroReplica=false
        )";

    std::shared_ptr<Helper::DiskIO> buffer(new Helper::SimpleBufferIO());
    Helper::IniReader reader;
    if (!buffer->Initialize(configuration.data(), std::ios::in, configuration.size()))
        return nullptr;
    if (ErrorCode::Success != reader.LoadIni(buffer))
        return nullptr;

    std::string sections[] = {"Base", "SelectHead", "BuildHead", "BuildSSDIndex"};
    for (const auto &sec : sections)
    {
        auto params = reader.GetParameters(sec.c_str());
        for (const auto &[key, val] : params)
        {
            vecIndex->SetParameter(key.c_str(), val.c_str(), sec.c_str());
        }
    }

    if (quantizer)
    {
        vecIndex->SetParameter("QuantizerFilePath", quantizerFilePath.c_str(), "Base");
        vecIndex->SetQuantizer(quantizer);
        vecIndex->SetQuantizerADC(false);
        vecIndex->SetParameter("Dim", std::to_string(quantizer->GetNumSubvectors()).c_str(), "Base");
    }
    auto buildStatus = vecIndex->BuildIndex();
    if (buildStatus != ErrorCode::Success)
        return nullptr;

    vecIndex->SetMetadata(new SPTAG::FileMetadataSet(pmetaset, pmetaidx));
    return vecIndex;
}

template <typename T>
std::vector<QueryResult> SearchOnly(std::shared_ptr<VectorIndex> &vecIndex, std::shared_ptr<VectorSet> &queryset, int k)
{
    std::vector<QueryResult> res(queryset->Count(), QueryResult(nullptr, k, true));

    auto t1 = std::chrono::high_resolution_clock::now();
    for (SizeType i = 0; i < queryset->Count(); i++)
    {
        res[i].SetTarget(queryset->GetVector(i));
        vecIndex->SearchIndex(res[i]);
    }
    auto t2 = std::chrono::high_resolution_clock::now();

    float avgUs =
        std::chrono::duration_cast<std::chrono::microseconds>(t2 - t1).count() / static_cast<float>(queryset->Count());
    SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Avg search time: %.2fus/query\n", avgUs);

    return res;
}

template <typename T>
float EvaluateRecall(const std::vector<QueryResult> &res, std::shared_ptr<VectorIndex> &vecIndex,
                     std::shared_ptr<VectorSet> &queryset, std::shared_ptr<VectorSet> &truth,
                     std::shared_ptr<VectorSet> &baseVec, std::shared_ptr<VectorSet> &addVec, SizeType baseCount, int recallK, int k, int batch, int totalbatches = -1)
{
    if (!truth)
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Truth data is null. Cannot compute recall.\n");
        return 0.0f;
    }

    recallK = min(recallK, static_cast<int>(truth->Dimension()));
    float totalRecall = 0.0f;
    float eps = 1e-4f;
    int distbase = (totalbatches + 1) * queryset->Count();
    for (SizeType i = 0; i < queryset->Count(); ++i)
    {
        const SizeType *truthNN = reinterpret_cast<const SizeType *>(truth->GetVector(i + batch * queryset->Count()));
        float *truthD = nullptr;
        if (truth->Count() == 2 * distbase)
        {
            truthD = reinterpret_cast<float *>(truth->GetVector(distbase + i + batch * queryset->Count()));
        }
        for (int j = 0; j < recallK; ++j)
        {
            SizeType truthVid = truthNN[j];
            float truthDist = MaxDist;
            if (baseVec != nullptr && addVec != nullptr)
                truthDist = (truthVid < baseCount)
                    ? vecIndex->ComputeDistance(queryset->GetVector(i), baseVec->GetVector(truthVid))
                    : vecIndex->ComputeDistance(queryset->GetVector(i), addVec->GetVector(truthVid - baseCount));
            else if (truthD)
            {
                truthDist = truthD[j];
            }
            
            for (int l = 0; l < k; ++l)
            {
                const auto result = res[i].GetResult(l);
                if (truthVid == result->VID ||
                    std::fabs(truthDist - result->Dist) <= eps * (std::fabs(truthDist) + eps))
                {
                    totalRecall += 1.0f;
                    break;
                }
            }
        }
    }

    float avgRecall = totalRecall / (queryset->Count() * recallK);
    SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Recall %d@%d = %.4f\n", recallK, k, avgRecall);
    return avgRecall;
}

template <typename T>
float Search(std::shared_ptr<VectorIndex> &vecIndex, std::shared_ptr<VectorSet> &queryset,
             std::shared_ptr<VectorSet> &baseVec, std::shared_ptr<VectorSet> &addVec, int k,
             std::shared_ptr<VectorSet> &truth, SizeType baseCount, int batch = 0)
{
    auto results = SearchOnly<T>(vecIndex, queryset, k);
    return EvaluateRecall<T>(results, vecIndex, queryset, truth, baseVec, addVec, baseCount, k, k, batch);
}

template <typename ValueType>
void InsertVectors(SPANN::Index<ValueType> *p_index, int insertThreads, int step,
                   std::shared_ptr<VectorSet> addset, std::shared_ptr<MetadataSet> &metaset, int start = 0)
{
    SPANN::Options &p_opts = *(p_index->GetOptions());
    p_index->ForceCompaction();
    p_index->GetDBStat();

    std::vector<std::thread> threads;

    int printstep = step / 50;
    std::atomic_size_t vectorsSent(start);
    auto func = [&]() {
        size_t index = start;
        while (true)
        {
            index = vectorsSent.fetch_add(1);
            if (index < start + step)
            {
                if ((index % (printstep - 1)) == 0)
                {
                    SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Sent %.2lf%%...\n", index * 100.0 / step);
                }
                ByteArray p_meta = metaset->GetMetadata((SizeType)index);
                std::uint64_t *offsets = new std::uint64_t[2]{0, p_meta.Length()};
                std::shared_ptr<MetadataSet> meta(new MemMetadataSet(
                    p_meta, ByteArray((std::uint8_t *)offsets, 2 * sizeof(std::uint64_t), true), 1));
                // For quantized index, pass GetFeatureDim() which returns reconstruct dimension
                ErrorCode ret = p_index->AddIndex(addset->GetVector((SizeType)index), 1, addset->Dimension(), meta, true);
                if (ret != ErrorCode::Success)
                {
                    SPTAGLIB_LOG(Helper::LogLevel::LL_Error,
                                 "AddIndex failed. VID:%zu Dim:%d IndexDim:%d Storage:%s Error:%d\n",
                                 index,
                                 addset->Dimension(),
                                 p_index->GetFeatureDim(),
                                 p_index->GetParameter("Storage", "BuildSSDIndex").c_str(),
                                 static_cast<int>(ret));
                }
                BOOST_REQUIRE(ret == ErrorCode::Success);
            }
            else
            {
                return;
            }
        }
    };
    for (int j = 0; j < insertThreads; j++)
    {
        threads.emplace_back(func);
    }
    for (auto &thread : threads)
    {
        thread.join();
    }

    while (!p_index->AllFinished())
    {
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
}


template <typename T>
void BenchmarkQueryPerformance(std::shared_ptr<VectorIndex> &index, std::shared_ptr<VectorSet> &queryset,
                               std::shared_ptr<VectorSet> &truth, const std::string &truthPath,
                               SizeType baseVectorCount, int topK, int searchK, int numThreads, int numQueries, int batches, int totalbatches,
                               std::ostream &benchmarkData, std::string prefix = "")
{
    // Benchmark: Query performance with detailed latency stats
    std::vector<float> latencies(numQueries);
    std::atomic_size_t queriesSent(0);
    std::vector<QueryResult> results(numQueries);

    for (int i = 0; i < numQueries; i++)
    {
        results[i] = QueryResult((const T *)queryset->GetVector(i), searchK, false);
    }

    std::vector<std::thread> threads;
    threads.reserve(numThreads);

    auto batchStart = std::chrono::high_resolution_clock::now();

    for (int i = 0; i < numThreads; i++)
    {
        threads.emplace_back([&]() {
            size_t qid;
            while ((qid = queriesSent.fetch_add(1)) < numQueries)
            {
                auto t1 = std::chrono::high_resolution_clock::now();
                index->SearchIndex(results[qid]);
                auto t2 = std::chrono::high_resolution_clock::now();
                latencies[qid] = std::chrono::duration_cast<std::chrono::microseconds>(t2 - t1).count() / 1000.0f;
            }
        });
    }

    for (auto &thread : threads)
        thread.join();

    auto batchEnd = std::chrono::high_resolution_clock::now();
    float batchLatency =
        std::chrono::duration_cast<std::chrono::microseconds>(batchEnd - batchStart).count() / 1000000.0f;

    // Calculate statistics
    float mean = 0, minLat = (std::numeric_limits<float>::max)(), maxLat = 0;
    for (int i = 0; i < numQueries; i++)
    {
        mean += latencies[i];
        minLat = (std::min)(minLat, latencies[i]);
        maxLat = (std::max)(maxLat, latencies[i]);
    }
    mean /= numQueries;

    std::sort(latencies.begin(), latencies.end());
    float p50 = latencies[static_cast<size_t>(numQueries * 0.50)];
    float p90 = latencies[static_cast<size_t>(numQueries * 0.90)];
    float p95 = latencies[static_cast<size_t>(numQueries * 0.95)];
    float p99 = latencies[static_cast<size_t>(numQueries * 0.99)];
    float qps = numQueries / batchLatency;

    BOOST_TEST_MESSAGE("  Queries: " << numQueries);
    BOOST_TEST_MESSAGE("  Mean Latency: " << mean << " ms");
    BOOST_TEST_MESSAGE("  P50 Latency:  " << p50 << " ms");
    BOOST_TEST_MESSAGE("  P90 Latency:  " << p90 << " ms");
    BOOST_TEST_MESSAGE("  P95 Latency:  " << p95 << " ms");
    BOOST_TEST_MESSAGE("  P99 Latency:  " << p99 << " ms");
    BOOST_TEST_MESSAGE("  Min Latency:  " << minLat << " ms");
    BOOST_TEST_MESSAGE("  Max Latency:  " << maxLat << " ms");
    BOOST_TEST_MESSAGE("  QPS:          " << qps);

    // Collect JSON data for Benchmark
    benchmarkData << std::fixed << std::setprecision(4);
    benchmarkData << prefix << "{\n";
    benchmarkData << prefix << "      \"numQueries\": " << numQueries << ",\n";
    benchmarkData << prefix << "      \"meanLatency\": " << mean << ",\n";
    benchmarkData << prefix << "      \"p50\": " << p50 << ",\n";
    benchmarkData << prefix << "      \"p90\": " << p90 << ",\n";
    benchmarkData << prefix << "      \"p95\": " << p95 << ",\n";
    benchmarkData << prefix << "      \"p99\": " << p99 << ",\n";
    benchmarkData << prefix << "      \"minLatency\": " << minLat << ",\n";
    benchmarkData << prefix << "      \"maxLatency\": " << maxLat << ",\n";
    benchmarkData << prefix << "      \"qps\": " << qps << ",\n";
    

    // Recall evaluation (if truth file provided)
    if (!truth || truthPath.empty() || truthPath == "none")
    {
        BOOST_TEST_MESSAGE("  Recall evaluation skipped (no truth data)");
        benchmarkData << prefix << "      \"recall\": null\n";
        benchmarkData << prefix << "    }";
        return;
    }

    BOOST_TEST_MESSAGE("Checking for truth file: " << truthPath);
    std::shared_ptr<VectorSet> pvecset, paddvecset;
    float avgRecall = EvaluateRecall<T>(results, index, queryset, truth, pvecset, paddvecset, baseVectorCount, topK, searchK, batches, totalbatches);
    BOOST_TEST_MESSAGE("  Recall" << topK << "@" << searchK << " = " << (avgRecall * 100.0f) << "%");
    BOOST_TEST_MESSAGE("  (Evaluated on " << numQueries << " queries against base vectors)");
    benchmarkData << std::fixed << std::setprecision(4);
    benchmarkData << prefix << "      \"recall\": {\n";
    benchmarkData << prefix << "        \"recallAtK\": " << avgRecall << ",\n";
    benchmarkData << prefix << "        \"k\": " << topK << ",\n";
    benchmarkData << prefix << "        \"numQueries\": " << numQueries << "\n";
    benchmarkData << prefix << "      }\n";
    benchmarkData << prefix << "    }";
}

ErrorCode QuantizeVectors(const std::shared_ptr<COMMON::IQuantizer>& quantizer,
                     const std::shared_ptr<VectorSet>& srcVectors,
                     ByteArray& quantizedBytes)
{
    if (!quantizer || !srcVectors)
        return ErrorCode::Fail;

    int maxthreads = std::thread::hardware_concurrency();
    std::vector<std::thread> threads;
    threads.reserve(maxthreads);
    std::atomic_size_t vectorsSent(0);
    auto func = [&]() {
        size_t index = 0;
        while (true)
        {
            index = vectorsSent.fetch_add(1);
            if (index < srcVectors->Count())
            {
                quantizer->QuantizeVector(srcVectors->GetVector(index), quantizedBytes.Data() + index * (size_t)(quantizer->GetNumSubvectors()), false);
            }
            else
            {
                return;
            }
        }
    };
    for (int j = 0; j < maxthreads; j++)
    {
        threads.emplace_back(func);
    }
    for (auto &thread : threads)
    {
        thread.join();
    }
    return ErrorCode::Success;
}

template <typename T>
void RunBenchmark(const std::string &vectorPath, const std::string &queryPath, const std::string &truthPath,
                  DistCalcMethod distMethod, const std::string &indexPath, int dimension, int baseVectorCount,
                  int insertVectorCount, int deleteVectorCount, int batches, int topK, int numThreads, int numQueries,
                  const std::string &outputFile = "output.json", const bool rebuild = true, const int resume = -1,
                  const std::string &quantizerFilePath = std::string(""), int quantizedDim = 0)
{
    int oldM = M, oldK = K, oldN = N, oldQueries = queries;
    N = baseVectorCount;
    queries = numQueries;
    M = dimension;
    K = topK;
    std::string dist = Helper::Convert::ConvertToString(distMethod);
    int insertBatchSize = insertVectorCount / max(batches, 1);
    int deleteBatchSize = deleteVectorCount / max(batches, 1);

    // Variables to collect JSON output data
    std::ostringstream tmpbenchmark;

    // Generate test data
    bool generateTruth = !(truthPath.empty() || truthPath == "none");
    bool enableQuantization = !quantizerFilePath.empty();
    std::string pvecset, paddset, pqueryset, ptruth, pmeta, pmetaidx, paddmeta, paddmetaidx;
    TestUtils::TestDataGenerator<T> generator(N, queries, M, K, dist, insertVectorCount, false, vectorPath, queryPath);
    generator.RunLargeBatches(pvecset, pmeta, pmetaidx, paddset, paddmeta, paddmetaidx, pqueryset, N, insertBatchSize,
                              deleteBatchSize, batches, ptruth, generateTruth);

    std::ofstream jsonFile(outputFile);
    BOOST_REQUIRE(jsonFile.is_open());

    jsonFile << std::fixed << std::setprecision(4);

    // Get current timestamp
    auto time_t_now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::tm tm_now;
#if defined(_MSC_VER)
    localtime_s(&tm_now, &time_t_now);
#else
    localtime_r(&time_t_now, &tm_now);
#endif

    std::ostringstream timestampStream;
    timestampStream << std::put_time(&tm_now, "%Y-%m-%dT%H:%M:%S");
    std::string timestamp = timestampStream.str();

    jsonFile << "{\n";
    jsonFile << "  \"timestamp\": \"" << timestamp << "\",\n";
    jsonFile << "  \"config\": {\n";
    jsonFile << "    \"vectorPath\": \"" << vectorPath << "\",\n";
    jsonFile << "    \"queryPath\": \"" << queryPath << "\",\n";
    jsonFile << "    \"truthPath\": \"" << truthPath << "\",\n";
    jsonFile << "    \"indexPath\": \"" << indexPath << "\",\n";
    jsonFile << "    \"quantizerPath\": \"" << quantizerFilePath << "\",\n";
    jsonFile << "    \"ValueType\": \"" << Helper::Convert::ConvertToString(GetEnumValueType<T>()) << "\",\n";
    jsonFile << "    \"dimension\": " << dimension << ",\n";
    jsonFile << "    \"baseVectorCount\": " << baseVectorCount << ",\n";
    jsonFile << "    \"insertVectorCount\": " << insertVectorCount << ",\n";
    jsonFile << "    \"DeleteVectorCount\": " << deleteVectorCount << ",\n";
    jsonFile << "    \"BatchNum\": " << batches << ",\n";
    jsonFile << "    \"topK\": " << topK << ",\n";
    jsonFile << "    \"numQueries\": " << numQueries << ",\n";
    jsonFile << "    \"numThreads\": " << numThreads << ",\n";
    jsonFile << "    \"DistMethod\": \"" << Helper::Convert::ConvertToString(distMethod) << "\"\n";
    jsonFile << "  },\n";
    jsonFile << "  \"results\": {\n";

    int SearchK = enableQuantization? topK * 4 : topK;
    std::shared_ptr<VectorIndex> index;
    std::shared_ptr<COMMON::IQuantizer> quantizer;
    
    // Build initial index
    BOOST_TEST_MESSAGE("\n=== Building Index ===");
    if (rebuild || !direxists(indexPath.c_str())) {
        std::filesystem::remove_all(indexPath);
        auto buildstart = std::chrono::high_resolution_clock::now();

        if (enableQuantization)
        {
            auto vectorOptions = std::shared_ptr<Helper::ReaderOptions>(
                new Helper::ReaderOptions(GetEnumValueType<T>(), M, VectorFileType::DEFAULT));
            auto baseReader = Helper::VectorSetReader::CreateInstance(vectorOptions);
            BOOST_REQUIRE(ErrorCode::Success == baseReader->LoadFile(pvecset));
            auto baseVectorsRaw = baseReader->GetVectorSet();

            auto baseVectorsFloat = ConvertToFloatVectorSet(baseVectorsRaw);
            BOOST_REQUIRE(baseVectorsFloat != nullptr);

            if (quantizedDim <= 0) quantizedDim = dimension / 2;
            BOOST_REQUIRE(quantizedDim > 0 && (dimension % quantizedDim) == 0);

            quantizer = EnsurePQQuantizer(quantizerFilePath, baseVectorsFloat, (DimensionType)quantizedDim, numThreads);
            BOOST_REQUIRE(quantizer != nullptr);

            std::string pquanvecset = "perftest_quanvectors.bin";
            {
                ByteArray quantizedBaseBytes = ByteArray::Alloc((size_t)baseVectorCount * (size_t)quantizer->GetNumSubvectors());
                BOOST_REQUIRE(QuantizeVectors(quantizer, baseVectorsFloat, quantizedBaseBytes) == ErrorCode::Success);
                auto quantizedBase = std::make_shared<BasicVectorSet>(quantizedBaseBytes, VectorValueType::UInt8, quantizer->GetNumSubvectors(), baseVectorCount);
                quantizedBase->Save(pquanvecset);
            }

            index = BuildLargeIndex<uint8_t>(indexPath, pquanvecset, pmeta, pmetaidx, dist, numThreads, numThreads, quantizer);
            BOOST_REQUIRE(index != nullptr);
            index->SetQuantizerADC(true);
        }
        else
        {
            index = BuildLargeIndex<T>(indexPath, pvecset, pmeta, pmetaidx, dist, numThreads, numThreads);
            BOOST_REQUIRE(index != nullptr);
        }

        auto buildend = std::chrono::high_resolution_clock::now();
        double buildseconds =
            std::chrono::duration_cast<std::chrono::microseconds>(buildend - buildstart).count() / 1000000.0f;
        jsonFile << "    \"build timeSeconds\": " << buildseconds << ",\n";
        BOOST_TEST_MESSAGE("Index built successfully with " << baseVectorCount << " vectors");
    }
    else
    {
        BOOST_REQUIRE(VectorIndex::LoadIndex(indexPath, index) == ErrorCode::Success);
        BOOST_REQUIRE(index != nullptr);
    }

    auto vectorOptions = std::shared_ptr<Helper::ReaderOptions>(
        new Helper::ReaderOptions(GetEnumValueType<T>(), M, VectorFileType::DEFAULT));

    auto queryReader = Helper::VectorSetReader::CreateInstance(vectorOptions);
    if (!fileexists(pqueryset.c_str()) || ErrorCode::Success != queryReader->LoadFile(pqueryset))
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Cannot find or load %s. Using random generation!\n",
                     pqueryset.c_str());
        return;
    }
    auto queryset = queryReader->GetVectorSet();
    if (enableQuantization)
    {
        if (!quantizer)
        {
            quantizer = index->GetQuantizer();
        }
        BOOST_REQUIRE(quantizer != nullptr);
        queryset = ConvertToFloatVectorSet(queryset);
    }

    auto addReader = Helper::VectorSetReader::CreateInstance(vectorOptions);
    if (!fileexists(paddset.c_str()) || ErrorCode::Success != addReader->LoadFile(paddset))
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Cannot find or load %s. Using random generation!\n", paddset.c_str());
        return;
    }

    std::shared_ptr<VectorSet> truth;
    if (generateTruth)
    {
        auto opts = std::make_shared<Helper::ReaderOptions>(GetEnumValueType<float>(), K, VectorFileType::DEFAULT);
        auto reader = Helper::VectorSetReader::CreateInstance(opts);
        if (ErrorCode::Success != reader->LoadFile(ptruth))
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to read file %s\n", ptruth.c_str());
            return;
        }
        truth = reader->GetVectorSet();
    }

    // Benchmark 0: Query performance before insertions
    BOOST_TEST_MESSAGE("\n=== Benchmark 0: Query Before Insertions ===");
    BenchmarkQueryPerformance<T>(index, queryset, truth, truthPath, baseVectorCount, topK, SearchK,
                                 numThreads, numQueries, 0, batches, tmpbenchmark);
    jsonFile << "    \"benchmark0_query_before_insert\": ";
    BenchmarkQueryPerformance<T>(index, queryset, truth, truthPath, baseVectorCount, topK, SearchK,
                                 numThreads, numQueries, 0, batches, jsonFile);
    jsonFile << ",\n";
    jsonFile.flush();

    BOOST_REQUIRE(index->SaveIndex(indexPath) == ErrorCode::Success);
    index = nullptr;


    // Benchmark 1: Insert performance
    if (insertBatchSize > 0)
    {
        BOOST_TEST_MESSAGE("\n=== Benchmark 1: Insert Performance ===");
        {
            jsonFile << "    \"benchmark1_insert\": {\n";
            std::string prevPath = indexPath;
            if (resume >= 0)
            {
                prevPath = indexPath + "_" + std::to_string(resume);
            }
            for (int iter = resume + 1; iter < batches; iter++)
            {
                jsonFile << "      \"batch_" << iter + 1 << "\": {\n";

                std::string clonePath = indexPath + "_" + std::to_string(iter);
                if (std::filesystem::exists(clonePath))
                {
                    std::filesystem::remove_all(clonePath);
                }
                std::shared_ptr<VectorIndex> prevIndex, cloneIndex;
                auto start = std::chrono::high_resolution_clock::now();
                BOOST_REQUIRE(VectorIndex::LoadIndex(prevPath, prevIndex) == ErrorCode::Success);
                auto end = std::chrono::high_resolution_clock::now();
                BOOST_REQUIRE(prevIndex != nullptr);
                BOOST_REQUIRE(prevIndex->Check() == ErrorCode::Success);

                double seconds =
                    std::chrono::duration_cast<std::chrono::microseconds>(end - start).count() / 1000000.0f;
                int vectorCount = prevIndex->GetNumSamples();
                BOOST_TEST_MESSAGE("  Load Time: " << seconds << " seconds");
                BOOST_TEST_MESSAGE("  Index vectors after reload: " << vectorCount);

                // Collect JSON data for Benchmark 4
                jsonFile << "        \"Load timeSeconds\": " << seconds << ",\n";
                jsonFile << "        \"Load vectorCount\": " << vectorCount << ",\n";

                start = std::chrono::high_resolution_clock::now();
                cloneIndex = prevIndex->Clone(clonePath);
                end = std::chrono::high_resolution_clock::now();
                seconds = std::chrono::duration_cast<std::chrono::microseconds>(end - start).count() / 1000000.0f;
                jsonFile << "        \"Clone timeSeconds\": " << seconds << ",\n";
                
                prevIndex = nullptr;
                
                // If using quantization, update dimension after clone
                if (enableQuantization)
                {
                    cloneIndex->SetParameter("Dim", std::to_string(quantizer->GetNumSubvectors()).c_str(), "Base");
                }
                
                ErrorCode cloneret = cloneIndex->Check();
                BOOST_REQUIRE(cloneret == ErrorCode::Success);
                SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Cloned index from %s to %s, check:%d, time: %f seconds\n",
                             prevPath.c_str(), clonePath.c_str(), (int)(cloneret == ErrorCode::Success), seconds);

                int insertStart = iter * insertBatchSize;
                {
                    std::shared_ptr<VectorSet> addset = addReader->GetVectorSet(insertStart, insertStart + insertBatchSize);
                    ByteArray quantizedAddBytes;
                    if (enableQuantization) {
                        auto addFloat = ConvertToFloatVectorSet(addset);
                        BOOST_REQUIRE(addFloat != nullptr);
                        quantizedAddBytes = ByteArray::Alloc((size_t)addFloat->Count() * (size_t)(quantizer->GetNumSubvectors()));
                        BOOST_REQUIRE(QuantizeVectors(quantizer, addFloat, quantizedAddBytes) == ErrorCode::Success);
                        addset = std::make_shared<BasicVectorSet>(quantizedAddBytes,
                                                                 VectorValueType::UInt8,
                                                                 quantizer->GetNumSubvectors(),
                                                                 addFloat->Count());
                    }
                    std::shared_ptr<MetadataSet> addmetaset(new MemMetadataSet(paddmeta, paddmetaidx, cloneIndex->m_iDataBlockSize,
                                           cloneIndex->m_iDataCapacity, 10, insertStart, insertBatchSize), std::default_delete<MemMetadataSet>());
                    start = std::chrono::high_resolution_clock::now();
                    InsertVectors<T>(static_cast<SPANN::Index<T> *>(cloneIndex.get()), numThreads, insertBatchSize,
                                     addset, addmetaset, 0);
                    end = std::chrono::high_resolution_clock::now();
                }
                seconds =
                    std::chrono::duration_cast<std::chrono::microseconds>(end - start).count() / 1000000.0f;
                double throughput = insertBatchSize / seconds;

                BOOST_TEST_MESSAGE("  Inserted: " << insertBatchSize << " vectors");
                BOOST_TEST_MESSAGE("  Time: " << seconds << " seconds");
                BOOST_TEST_MESSAGE("  Throughput: " << throughput << " vectors/sec");

                // Collect JSON data for Benchmark 1               
                jsonFile << "        \"inserted\": " << insertBatchSize << ",\n";
                jsonFile << "        \"insert timeSeconds\": " << seconds << ",\n";
                jsonFile << "        \"insert throughput\": " << throughput << ",\n";

                if (deleteBatchSize > 0)
                {
                    std::vector<std::thread> threads;
                    threads.reserve(numThreads);

                    int startidx = iter * deleteBatchSize;
                    std::atomic_size_t vectorsSent(startidx);
                    int totaldeleted = startidx + deleteBatchSize;
                    int printstep = deleteBatchSize / 50;
                    auto func = [&]() {
                        size_t idx = startidx;
                        while (true)
                        {
                            idx = vectorsSent.fetch_add(1);
                            if (idx < totaldeleted)
                            {
                                if ((idx % (printstep - 1)) == 0)
                                {
                                    SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Sent %.2lf%%...\n",
                                                 (idx - startidx) * 100.0 / deleteBatchSize);
                                }
                                BOOST_REQUIRE(cloneIndex->DeleteIndex(idx) == ErrorCode::Success);
                            }
                            else
                            {
                                return;
                            }
                        }
                    };

                    start = std::chrono::high_resolution_clock::now();
                    for (int j = 0; j < numThreads; j++)
                    {
                        threads.emplace_back(func);
                    }
                    for (auto &thread : threads)
                    {
                        thread.join();
                    }
                    end = std::chrono::high_resolution_clock::now();
                    double seconds =
                        std::chrono::duration_cast<std::chrono::microseconds>(end - start).count() / 1000000.0f;
                    double throughput = deleteBatchSize / seconds;

                    jsonFile << "        \"deleted\": " << deleteVectorCount << ",\n";
                    jsonFile << "        \"delete timeSeconds\": " << seconds << ",\n";
                    jsonFile << "        \"delete throughput\": " << throughput << ",\n";
                }

                BOOST_TEST_MESSAGE("\n=== Benchmark 2: Query After Insertions and Deletions ===");
                jsonFile << "        \"search\":";
                BenchmarkQueryPerformance<T>(cloneIndex, queryset, truth, truthPath, baseVectorCount, topK, SearchK, numThreads,
                                             numQueries, iter + 1, batches, tmpbenchmark, "    ");
                BenchmarkQueryPerformance<T>(cloneIndex, queryset, truth, truthPath, baseVectorCount,
                                             topK, SearchK, numThreads, numQueries, iter + 1, batches, jsonFile, "    ");
                jsonFile << ",\n";

                start = std::chrono::high_resolution_clock::now();
                BOOST_REQUIRE(cloneIndex->SaveIndex(clonePath) == ErrorCode::Success);
                end = std::chrono::high_resolution_clock::now();

                seconds = std::chrono::duration_cast<std::chrono::microseconds>(end - start).count() / 1000000.0f;
                BOOST_TEST_MESSAGE("  Save Time: " << seconds << " seconds");
                BOOST_TEST_MESSAGE("  Save completed successfully");

                // Collect JSON data for Benchmark 3
                jsonFile << "        \"save timeSeconds\": " << seconds << "\n";

                if (iter != batches - 1)
                    jsonFile << "      },\n";
                else
                    jsonFile << "      }\n";

                cloneIndex = nullptr;
                prevPath = clonePath;
                jsonFile.flush();

                if (iter > 0)
                    std::filesystem::remove_all(indexPath + "_" + std::to_string(iter - 1));
            }
        }
        jsonFile << "    }\n";
    }

    jsonFile << "  }\n";
    jsonFile << "}\n";
    jsonFile.close();

    M = oldM;
    K = oldK;
    N = oldN;
    queries = oldQueries;
}

} // namespace SPFreshTest

bool CompareFilesWithLogging(const std::filesystem::path &file1, const std::filesystem::path &file2)
{
    std::ifstream f1(file1, std::ios::binary);
    std::ifstream f2(file2, std::ios::binary);

    if (!f1.is_open() || !f2.is_open())
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Failed to open one of the files:\n  %s\n  %s\n",
                     file1.string().c_str(), file2.string().c_str());
        return false;
    }

    // Check file sizes first
    f1.seekg(0, std::ios::end);
    f2.seekg(0, std::ios::end);
    if (f1.tellg() != f2.tellg())
    {
        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "File size differs: %s\n", file1.filename().string().c_str());
        return false;
    }

    f1.seekg(0, std::ios::beg);
    f2.seekg(0, std::ios::beg);

    const int bufferSize = 4096; // Adjust buffer size as needed
    std::vector<char> buffer1(bufferSize);
    std::vector<char> buffer2(bufferSize);

    while (f1.read(buffer1.data(), bufferSize) && f2.read(buffer2.data(), bufferSize))
    {
        if (std::memcmp(buffer1.data(), buffer2.data(), f1.gcount()) != 0)
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "File mismatch at: %s\n", file1.filename().string().c_str());
            return false; // Mismatch found
        }
    }

    return true;
}

bool CompareDirectoriesWithLogging(const std::filesystem::path &dir1, const std::filesystem::path &dir2,
                                   const std::unordered_set<std::string> &exceptions = {})
{
    std::map<std::string, std::filesystem::path> files1, files2;

    for (const auto &entry : std::filesystem::recursive_directory_iterator(dir1))
    {
        if (entry.is_regular_file())
        {
            files1[std::filesystem::relative(entry.path(), dir1).string()] = entry.path();
        }
    }

    for (const auto &entry : std::filesystem::recursive_directory_iterator(dir2))
    {
        if (entry.is_regular_file())
        {
            files2[std::filesystem::relative(entry.path(), dir2).string()] = entry.path();
        }
    }

    bool matched = true;

    for (const auto &[relPath, filePath1] : files1)
    {
        if (exceptions.count(relPath))
            continue;

        auto it = files2.find(relPath);
        if (it == files2.end())
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Missing in %s: %s\n", dir2.string().c_str(), relPath.c_str());
            matched = false;
            continue;
        }
        if (!CompareFilesWithLogging(filePath1, it->second))
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "File end differs: %s\n", filePath1.filename().string().c_str());
            matched = false;
        }
    }

    for (const auto &[relPath, _] : files2)
    {
        if (exceptions.count(relPath))
            continue;
        if (files1.find(relPath) == files1.end())
        {
            SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Extra in %s: %s\n", dir2.string().c_str(), relPath.c_str());
            matched = false;
        }
    }

    return matched;
}

void NormalizeVector(float *embedding, int dimension)
{
    // get magnitude
    float magnitude = 0.0f;
    {
        float sum = 0.0;
        for (int i = 0; i < dimension; i++)
        {
            sum += embedding[i] * embedding[i];
        }
        magnitude = std::sqrt(sum);
    }

    // normalized target vector
    for (int i = 0; i < dimension; i++)
    {
        embedding[i] /= magnitude;
    }
}

template <typename T>
std::shared_ptr<VectorSet> get_embeddings(uint32_t row_id, uint32_t end_id, uint32_t embedding_dim,
                                          uint32_t array_index)
{
    uint32_t count = end_id - row_id;
    ByteArray vec = ByteArray::Alloc(sizeof(T) * count * embedding_dim);
    for (uint32_t rid = 0; rid < count; rid++)
    {
        for (int idx = 0; idx < embedding_dim; ++idx)
        {
            ((T *)vec.Data())[rid * embedding_dim + idx] = (row_id + rid) * 17 + idx * 19 + (array_index + 1) * 23;
        }
        NormalizeVector(((T *)vec.Data()) + rid * embedding_dim, embedding_dim);
    }
    return std::make_shared<BasicVectorSet>(vec, GetEnumValueType<T>(), embedding_dim, count);
}

BOOST_AUTO_TEST_SUITE(SPFreshTest)

BOOST_AUTO_TEST_CASE(TestLoadAndSave)
{
    using namespace SPFreshTest;

    // Prepare test data using TestDataGenerator
    std::shared_ptr<VectorSet> vecset, queryset, truth, addvecset, addtruth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.Run(vecset, metaset, queryset, truth, addvecset, addmetaset, addtruth);

    auto originalIndex = BuildIndex<int8_t>("original_index", vecset, metaset);
    BOOST_REQUIRE(originalIndex != nullptr);
    BOOST_REQUIRE(originalIndex->SaveIndex("original_index") == ErrorCode::Success);
    originalIndex = nullptr;

    std::shared_ptr<VectorIndex> loadedIndex;
    BOOST_REQUIRE(VectorIndex::LoadIndex("original_index", loadedIndex) == ErrorCode::Success);
    BOOST_REQUIRE(loadedIndex != nullptr);
    BOOST_REQUIRE(loadedIndex->SaveIndex("loaded_and_saved_index") == ErrorCode::Success);
    loadedIndex = nullptr;

    std::unordered_set<std::string> exceptions = {"indexloader.ini"};

    // Compare files in both directories
    BOOST_REQUIRE_MESSAGE(CompareDirectoriesWithLogging("original_index", "loaded_and_saved_index", exceptions),
                          "Saved index does not match loaded-then-saved index");

    std::filesystem::remove_all("original_index");
    std::filesystem::remove_all("loaded_and_saved_index");
}

BOOST_AUTO_TEST_CASE(TestReopenIndexRecall)
{
    using namespace SPFreshTest;

    std::shared_ptr<VectorSet> vecset, queryset, truth, addvecset, addtruth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.Run(vecset, metaset, queryset, truth, addvecset, addmetaset, addtruth);

    auto originalIndex = BuildIndex<int8_t>("original_index", vecset, metaset);
    BOOST_REQUIRE(originalIndex != nullptr);
    float recall1 = Search<int8_t>(originalIndex, queryset, vecset, addvecset, K, truth, N);
    BOOST_REQUIRE(originalIndex->SaveIndex("original_index") == ErrorCode::Success);    
    originalIndex = nullptr;

    std::shared_ptr<VectorIndex> loadedOnce;
    BOOST_REQUIRE(VectorIndex::LoadIndex("original_index", loadedOnce) == ErrorCode::Success);
    BOOST_REQUIRE(loadedOnce != nullptr);
    BOOST_REQUIRE(loadedOnce->SaveIndex("reopened_index") == ErrorCode::Success);
    loadedOnce = nullptr;

    std::shared_ptr<VectorIndex> loadedTwice;
    BOOST_REQUIRE(VectorIndex::LoadIndex("reopened_index", loadedTwice) == ErrorCode::Success);
    BOOST_REQUIRE(loadedTwice != nullptr);
    float recall2 = Search<int8_t>(loadedTwice, queryset, vecset, addvecset, K, truth, N);
    loadedTwice = nullptr;

    BOOST_REQUIRE_MESSAGE(std::fabs(recall1 - recall2) < 0.02, "Recall mismatch between original and reopened index");

    std::filesystem::remove_all("original_index");
    std::filesystem::remove_all("reopened_index");
}

BOOST_AUTO_TEST_CASE(TestInsertAndSearch)
{
    using namespace SPFreshTest;

    // Prepare test data using TestDataGenerator
    std::shared_ptr<VectorSet> vecset, queryset, truth, addvecset, addtruth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.Run(vecset, metaset, queryset, truth, addvecset, addmetaset, addtruth);

    // Build base index
    auto index = BuildIndex<int8_t>("insert_test_index", vecset, metaset);
    BOOST_REQUIRE(index != nullptr);
    BOOST_REQUIRE(index->SaveIndex("insert_test_index") == ErrorCode::Success);
    index = nullptr;

    std::shared_ptr<VectorIndex> loadedOnce;
    BOOST_REQUIRE(VectorIndex::LoadIndex("insert_test_index", loadedOnce) == ErrorCode::Success);
    BOOST_REQUIRE(loadedOnce != nullptr);

    InsertVectors<int8_t>(static_cast<SPANN::Index<int8_t> *>(loadedOnce.get()), 2, 1000, addvecset, addmetaset);
    SearchOnly<int8_t>(loadedOnce, queryset, K);
    loadedOnce = nullptr;

    std::filesystem::remove_all("insert_test_index");
}

BOOST_AUTO_TEST_CASE(StaticBundleMetadataRootBuildAndReload)
{
    constexpr SizeType baseCount = 128;
    constexpr DimensionType dimension = 8;
    constexpr int tagCount = 4;
    const std::string indexDirectory = "static_bundle_metadata_root_index";
    std::filesystem::remove_all(indexDirectory);
    std::filesystem::create_directories(indexDirectory);

    ByteArray bytes = ByteArray::Alloc(
        sizeof(float) * static_cast<size_t>(baseCount) * static_cast<size_t>(dimension));
    auto* data = reinterpret_cast<float*>(bytes.Data());
    std::vector<std::uint32_t> tags(static_cast<size_t>(baseCount) * tagCount);
    std::vector<std::vector<SizeType>> nodeAssignments(2);
    for (SizeType row = 0; row < baseCount; ++row) {
        const int node = static_cast<int>(row % 2);
        nodeAssignments[static_cast<size_t>(node)].push_back(row);
        for (DimensionType dim = 0; dim < dimension; ++dim) {
            data[static_cast<size_t>(row) * dimension + dim] =
                static_cast<float>(node * 100 + row / 2 + dim);
        }
        for (int tag = 0; tag < tagCount; ++tag) {
            tags[static_cast<size_t>(row) * tagCount + tag] =
                static_cast<std::uint32_t>(node * 10 + tag);
        }
    }
    auto vectors = std::make_shared<BasicVectorSet>(
        bytes, VectorValueType::Float, dimension, baseCount);

    const auto configure = [&](const std::shared_ptr<VectorIndex>& target) {
        const auto set = [&](const char* section, const char* key, const std::string& value) {
            BOOST_REQUIRE(target->SetParameter(key, value.c_str(), section) == ErrorCode::Success);
        };
        set("Base", "DistCalcMethod", "L2");
        set("Base", "IndexAlgoType", "BKT");
        set("Base", "ValueType", "Float");
        set("Base", "Dim", std::to_string(dimension));
        set("Base", "IndexDirectory", indexDirectory);
        set("SelectHead", "isExecute", "true");
        set("SelectHead", "SelectHeadType", "BKT");
        set("SelectHead", "Ratio", "0.25");
        set("SelectHead", "NumberOfThreads", "1");
        set("SelectHead", "SelectThreshold", "0");
        set("SelectHead", "SplitFactor", "0");
        set("SelectHead", "SplitThreshold", "0");
        set("BuildHead", "isExecute", "true");
        set("BuildHead", "NumberOfThreads", "1");
        set("BuildSSDIndex", "isExecute", "true");
        set("BuildSSDIndex", "BuildSsdIndex", "true");
        set("BuildSSDIndex", "Storage", "STATIC");
        set("BuildSSDIndex", "InternalResultNum", "8");
        set("BuildSSDIndex", "SearchInternalResultNum", "8");
        set("BuildSSDIndex", "NumberOfThreads", "1");
        set("BuildSSDIndex", "PostingPageLimit", "1");
        set("BuildSSDIndex", "SearchPostingPageLimit", "1");
        set("BuildSSDIndex", "SSDIndexFileNum", "1");
        set("BuildSSDIndex", "ReplicaCount", "2");
        set("BuildSSDIndex", "TailReplicaCount", "1");
        set("BuildSSDIndex", "UnfilterTailBufferLength", "-1");
        set("BuildSSDIndex", "CrossEdges", "1");
        set("BuildSSDIndex", "CrossExtraEdges", "2");
        set("BuildSSDIndex", "ExcludeHead", "true");
        set("BuildSSDIndex", "NumTagsPerVec", std::to_string(tagCount));
        set("BuildSSDIndex", "StaticACLTagCols", std::to_string(tagCount));
    };

    ScopedEnvironmentVariable persistSelectHead("SPTAG_PERSIST_SELECTHEAD", "1");
    ScopedEnvironmentVariable resumeBuild("SPTAG_RESUME_BUILD", nullptr);
    auto index = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
    BOOST_REQUIRE(index != nullptr);
    configure(index);

    auto* spann = dynamic_cast<SPANN::ISPANNIndex*>(index.get());
    BOOST_REQUIRE(spann != nullptr);
    auto* typedSpann = dynamic_cast<SPANN::Index<float>*>(index.get());
    BOOST_REQUIRE(typedSpann != nullptr);
    spann->SetVectorTags(tags.data(), baseCount, tagCount);
    spann->SetNodeVectorAssignments(nodeAssignments);
    spann->SetPrimaryNodeVectorAssignments(nodeAssignments);
    BOOST_REQUIRE(index->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::Success);

    auto root = spann->GetMemoryIndex();
    auto* metadataRoot = dynamic_cast<KDT::Index<float>*>(root.get());
    BOOST_REQUIRE(metadataRoot != nullptr);
    BOOST_CHECK(metadataRoot->IsMetadataOnly());
    BOOST_CHECK_GT(metadataRoot->GetNumSamples(), 1);
    BOOST_CHECK(!std::filesystem::exists(indexDirectory + "/SPTAGHeadVectors.bin"));
    BOOST_CHECK(std::filesystem::exists(indexDirectory + "/HeadIndex/node_0/vectors.bin"));
    BOOST_CHECK(std::filesystem::exists(indexDirectory + "/HeadIndex/node_1/vectors.bin"));

    COMMON::QueryResultSet<float> query(data, 10);
    BOOST_REQUIRE(index->SearchIndex(query) == ErrorCode::Success);
    BOOST_CHECK_GE(query.GetResult(0)->VID, 0);
    const SizeType fullPostingScans = query.GetScanned();

    COMMON::QueryResultSet<float> repeatedQuery(data, 10);
    BOOST_REQUIRE(index->SearchIndex(repeatedQuery) == ErrorCode::Success);
    BOOST_CHECK_GE(repeatedQuery.GetResult(0)->VID, 0);
    BOOST_CHECK_EQUAL(repeatedQuery.GetScanned(), fullPostingScans);

    {
        VectorIndex::ThreadLocalSearchContext filterContext;
        filterContext.m_active = true;
        filterContext.m_queryTags.assign(tags.begin(), tags.begin() + tagCount);
        VectorIndex::ThreadLocalSearchContextGuard filterGuard(
            std::move(filterContext));
        COMMON::QueryResultSet<float> filteredQuery(data, 10);
        BOOST_REQUIRE(index->SearchIndex(filteredQuery) == ErrorCode::Success);
        for (int rank = 0; rank < filteredQuery.GetResultNum(); ++rank) {
            const auto* result = filteredQuery.GetResult(rank);
            if (result == nullptr || result->VID < 0) break;
            BOOST_CHECK_EQUAL(result->VID % 2, 0);
        }
    }

    // STATIC snapshots deliberately reject arbitrary metadata callbacks, but the
    // empty callback still exercises the metadata-root all-bundle routing fallback.
    COMMON::QueryResultSet<float> genericRoutingQuery(data, 10);
    std::function<bool(const ByteArray&)> noMetadataFilter;
    BOOST_REQUIRE(index->SearchIndexWithFilter(
                      genericRoutingQuery,
                      noMetadataFilter,
                      0) == ErrorCode::Success);
    BOOST_CHECK_GE(genericRoutingQuery.GetResult(0)->VID, 0);

    const std::string headDirectory = indexDirectory + "/HeadIndex";
    const std::string nodeGraph = headDirectory + "/node_0/graph.bin";
    const std::string crossEdges = headDirectory + "/head_cross_edges.bin";
    BOOST_REQUIRE(std::filesystem::exists(indexDirectory + "/head_select_state.bin"));
    BOOST_REQUIRE(std::filesystem::exists(nodeGraph));
    BOOST_REQUIRE(std::filesystem::exists(headDirectory + "/head_bundle_manifest.bin"));
    BOOST_REQUIRE(std::filesystem::exists(headDirectory + "/head_metaonly.bin"));
    BOOST_REQUIRE(std::filesystem::exists(crossEdges));
    BOOST_CHECK_GT(std::filesystem::file_size(crossEdges), 0);
    BOOST_CHECK_GT(typedSpann->GetInlineHeadCrossEdgeSize(), 0);
    BOOST_CHECK_GT(typedSpann->GetInlineHeadCrossEdgeTotal(), 0);
    BOOST_CHECK_GT(typedSpann->GetInlineHeadLocatorLocalBits(), 0);
    const auto nodeGraphWriteTime = std::filesystem::last_write_time(nodeGraph);
    const auto crossEdgesWriteTime = std::filesystem::last_write_time(crossEdges);

    index.reset();
    root.reset();
    BOOST_REQUIRE(std::filesystem::remove(headDirectory + "/head_bundle_manifest.bin"));
    BOOST_REQUIRE(std::filesystem::remove(headDirectory + "/head_metaonly.bin"));
    std::filesystem::remove(indexDirectory + "/SPTAGFullList.bin");
    std::filesystem::remove(indexDirectory + "/DeletedIDs.bin");
    std::filesystem::remove(indexDirectory + "/indexloader.ini");

    resumeBuild.Set("1");
    auto resumed = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
    BOOST_REQUIRE(resumed != nullptr);
    configure(resumed);
    auto* resumedSpann = dynamic_cast<SPANN::ISPANNIndex*>(resumed.get());
    BOOST_REQUIRE(resumedSpann != nullptr);
    resumedSpann->SetVectorTags(tags.data(), baseCount, tagCount);
    resumedSpann->SetNodeVectorAssignments(nodeAssignments);
    resumedSpann->SetPrimaryNodeVectorAssignments(nodeAssignments);
    BOOST_REQUIRE(resumed->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::Success);
    BOOST_CHECK(std::filesystem::last_write_time(nodeGraph) == nodeGraphWriteTime);
    auto* resumedMetadataRoot = dynamic_cast<KDT::Index<float>*>(
        resumedSpann->GetMemoryIndex().get());
    BOOST_REQUIRE(resumedMetadataRoot != nullptr);
    BOOST_CHECK(resumedMetadataRoot->IsMetadataOnly());
    BOOST_REQUIRE(std::filesystem::exists(headDirectory + "/head_bundle_manifest.bin"));
    BOOST_REQUIRE(std::filesystem::exists(headDirectory + "/head_metaonly.bin"));
    BOOST_REQUIRE(std::filesystem::exists(crossEdges));
    BOOST_CHECK(std::filesystem::last_write_time(crossEdges) == crossEdgesWriteTime);

    resumed.reset();
    BOOST_REQUIRE(std::filesystem::remove(headDirectory + "/head_bundle_manifest.bin"));
    BOOST_REQUIRE(std::filesystem::remove(headDirectory + "/head_metaonly.bin"));
    std::filesystem::remove(indexDirectory + "/SPTAGFullList.bin");
    std::filesystem::remove(indexDirectory + "/DeletedIDs.bin");
    std::filesystem::remove(indexDirectory + "/indexloader.ini");
    {
        std::fstream corrupt(crossEdges, std::ios::in | std::ios::out | std::ios::binary);
        BOOST_REQUIRE(corrupt.good());
        const std::uint32_t invalidMagic = 0;
        corrupt.write(
            reinterpret_cast<const char*>(&invalidMagic), sizeof(invalidMagic));
        BOOST_REQUIRE(corrupt.good());
    }

    auto recovered = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
    BOOST_REQUIRE(recovered != nullptr);
    configure(recovered);
    auto* recoveredSpann = dynamic_cast<SPANN::ISPANNIndex*>(recovered.get());
    BOOST_REQUIRE(recoveredSpann != nullptr);
    recoveredSpann->SetVectorTags(tags.data(), baseCount, tagCount);
    recoveredSpann->SetNodeVectorAssignments(nodeAssignments);
    recoveredSpann->SetPrimaryNodeVectorAssignments(nodeAssignments);
    BOOST_REQUIRE(recovered->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::Success);
    BOOST_CHECK(std::filesystem::last_write_time(nodeGraph) == nodeGraphWriteTime);
    std::ifstream rebuiltCrossEdges(crossEdges, std::ios::binary);
    std::uint32_t rebuiltMagic = 0;
    rebuiltCrossEdges.read(
        reinterpret_cast<char*>(&rebuiltMagic), sizeof(rebuiltMagic));
    BOOST_REQUIRE(rebuiltCrossEdges.good());
    BOOST_CHECK_EQUAL(rebuiltMagic, Helper::kHeadCrossEdgesMagic);
    auto* recoveredTyped = dynamic_cast<SPANN::Index<float>*>(recovered.get());
    BOOST_REQUIRE(recoveredTyped != nullptr);
    BOOST_CHECK_GT(recoveredTyped->GetInlineHeadCrossEdgeSize(), 0);
    BOOST_CHECK_GT(recoveredTyped->GetInlineHeadCrossEdgeTotal(), 0);
    BOOST_CHECK_GT(recoveredTyped->GetInlineHeadLocatorLocalBits(), 0);

    const auto nodeGraphSizeBeforeSave = std::filesystem::file_size(nodeGraph);
    BOOST_REQUIRE(recovered->SaveIndex(indexDirectory) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(std::filesystem::file_size(nodeGraph), nodeGraphSizeBeforeSave);
    recovered.reset();

    const std::string crossEdgesBackup = crossEdges + ".runtime-inline-test";
    std::filesystem::rename(crossEdges, crossEdgesBackup);
    std::shared_ptr<VectorIndex> noCrossReload;
    BOOST_REQUIRE(
        VectorIndex::LoadIndex(indexDirectory, noCrossReload) == ErrorCode::Success);
    auto* noCrossTyped =
        dynamic_cast<SPANN::Index<float>*>(noCrossReload.get());
    BOOST_REQUIRE(noCrossTyped != nullptr);
    BOOST_CHECK_EQUAL(noCrossTyped->GetInlineHeadCrossEdgeSize(), 0);
    BOOST_CHECK_EQUAL(noCrossTyped->GetInlineHeadCrossEdgeTotal(), 0);
    BOOST_CHECK_EQUAL(noCrossTyped->GetInlineHeadLocatorLocalBits(), 0);
    COMMON::QueryResultSet<float> noCrossQuery(data, 10);
    BOOST_REQUIRE(noCrossReload->SearchIndex(noCrossQuery) == ErrorCode::Success);
    BOOST_CHECK_GE(noCrossQuery.GetResult(0)->VID, 0);
    noCrossReload.reset();
    std::filesystem::rename(crossEdgesBackup, crossEdges);

    {
        std::ofstream orderedMarker(
            indexDirectory + "/ordered_page_starts.bin",
            std::ios::binary | std::ios::trunc);
        BOOST_REQUIRE(orderedMarker.good());
        const std::uint32_t marker = 0;
        orderedMarker.write(reinterpret_cast<const char*>(&marker), sizeof(marker));
        BOOST_REQUIRE(orderedMarker.good());
    }
    std::shared_ptr<VectorIndex> reloaded;
    BOOST_REQUIRE(VectorIndex::LoadIndex(indexDirectory, reloaded) == ErrorCode::Success);
    auto* reloadedSpann = dynamic_cast<SPANN::ISPANNIndex*>(reloaded.get());
    BOOST_REQUIRE(reloadedSpann != nullptr);
    auto* reloadedTyped = dynamic_cast<SPANN::Index<float>*>(reloaded.get());
    BOOST_REQUIRE(reloadedTyped != nullptr);
    BOOST_CHECK_GT(reloadedTyped->GetInlineHeadCrossEdgeSize(), 0);
    BOOST_CHECK_GT(reloadedTyped->GetInlineHeadCrossEdgeTotal(), 0);
    BOOST_CHECK_GT(reloadedTyped->GetInlineHeadLocatorLocalBits(), 0);
    auto* reloadedRoot = dynamic_cast<KDT::Index<float>*>(
        reloadedSpann->GetMemoryIndex().get());
    BOOST_REQUIRE(reloadedRoot != nullptr);
    BOOST_CHECK(reloadedRoot->IsMetadataOnly());

    COMMON::QueryResultSet<float> reloadedQuery(data, 10);
    BOOST_REQUIRE(reloaded->SearchIndex(reloadedQuery) == ErrorCode::Success);
    BOOST_CHECK_GE(reloadedQuery.GetResult(0)->VID, 0);
    BOOST_CHECK(reloaded->SetParameter("UnfilterPureDistanceScanPercent", "50",
        "SearchSSDIndex") == ErrorCode::FailedParseValue);
    std::filesystem::remove_all(indexDirectory);
}

BOOST_AUTO_TEST_CASE(StaticHybridBuildMembershipAndReload)
{
    constexpr SizeType baseCount = 256;
    constexpr DimensionType dimension = 8;
    constexpr int tagCount = 3;
    const std::string indexDirectory =
        "static_hybrid_route_index";
    std::filesystem::remove_all(indexDirectory);
    std::filesystem::create_directories(
        indexDirectory);

    ByteArray bytes = ByteArray::Alloc(
        sizeof(float) *
        static_cast<size_t>(baseCount) *
        static_cast<size_t>(dimension));
    auto* data = reinterpret_cast<float*>(
        bytes.Data());
    std::vector<std::uint32_t> tags(
        static_cast<size_t>(baseCount) *
        tagCount);
    std::vector<std::vector<SizeType>>
        nodeAssignments(1);
    for (SizeType row = 0; row < baseCount;
         ++row) {
        const int label = row % 2;
        nodeAssignments[0].push_back(row);
        for (DimensionType dim = 0;
             dim < dimension; ++dim) {
            data[static_cast<size_t>(row) *
                     dimension +
                 dim] =
                static_cast<float>(
                    (row * (dim + 3) +
                     dim * 17) %
                    (baseCount + 1)) /
                static_cast<float>(baseCount + 1);
        }
        tags[static_cast<size_t>(row) *
                 tagCount] =
            static_cast<std::uint32_t>(label);
        tags[static_cast<size_t>(row) *
                 tagCount +
             1] =
            static_cast<std::uint32_t>(
                (row / 2) % 8);
        tags[static_cast<size_t>(row) *
                 tagCount +
             2] =
            static_cast<std::uint32_t>(row / 2);
    }
    auto vectors =
        std::make_shared<BasicVectorSet>(
            bytes, VectorValueType::Float,
            dimension, baseCount);

    const auto configure =
        [&](const std::shared_ptr<VectorIndex>&
                target) {
            const auto set =
                [&](const char* section,
                    const char* key,
                    const std::string& value) {
                    BOOST_REQUIRE(
                        target->SetParameter(
                            key, value.c_str(),
                            section) ==
                        ErrorCode::Success);
                };
            set("Base", "DistCalcMethod", "L2");
            set("Base", "IndexAlgoType", "BKT");
            set("Base", "ValueType", "Float");
            set("Base", "Dim",
                std::to_string(dimension));
            set("Base", "IndexDirectory",
                indexDirectory);
            set("SelectHead", "isExecute",
                "true");
            set("SelectHead", "SelectHeadType",
                "BKT");
            set("SelectHead", "Ratio", "0.25");
            set("SelectHead", "BKTLambdaFactor",
                "-1");
            set("SelectHead", "SelectThreshold",
                "50");
            set("SelectHead", "SplitFactor", "6");
            set("SelectHead", "SplitThreshold",
                "100");
            set("SelectHead", "NumberOfThreads",
                "1");
            set("BuildHead", "isExecute", "true");
            set("BuildHead", "NeighborhoodSize",
                "32");
            set("BuildHead", "RefineIterations",
                "3");
            set("BuildHead", "MaxCheck", "512");
            set("BuildHead",
                "MaxCheckForRefineGraph", "512");
            set("BuildHead", "BKTLambdaFactor",
                "-1");
            set("BuildHead", "NumberOfThreads",
                "1");
            set("BuildSSDIndex", "isExecute",
                "true");
            set("BuildSSDIndex",
                "BuildSsdIndex", "true");
            set("BuildSSDIndex", "Storage",
                "STATIC");
            set("BuildSSDIndex",
                "InternalResultNum", "16");
            set("BuildSSDIndex",
                "SearchInternalResultNum", "8");
            set("BuildSSDIndex",
                "NumberOfThreads", "2");
            set("BuildSSDIndex",
                "PostingPageLimit", "2");
            set("BuildSSDIndex",
                "SearchPostingPageLimit", "2");
            set("BuildSSDIndex",
                "SSDIndexFileNum", "1");
            set("BuildSSDIndex", "ReplicaCount",
                "3");
            set("BuildSSDIndex",
                "TailReplicaCount", "2");
            set("BuildSSDIndex",
                "UnfilterTailBufferLength",
                "-1");
            set("BuildSSDIndex", "CrossEdges",
                "0");
            set("BuildSSDIndex",
                "CrossExtraEdges", "4");
            set("BuildSSDIndex", "ExcludeHead",
                "true");
            set("BuildSSDIndex",
                "NumTagsPerVec",
                std::to_string(tagCount));
            set("BuildSSDIndex",
                "StaticACLTagCols", "2");
            set("BuildSSDIndex",
                "EnableHybridDistance", "true");
            set("BuildSSDIndex",
                "HybridVectorWeight", "1");
            set("BuildSSDIndex",
                "HybridCategoricalCols",
                "0,1");
            set("BuildSSDIndex",
                "HybridCategoricalWeights",
                "100,100");
            set("BuildSSDIndex",
                "HybridNumericCols", "2");
            set("BuildSSDIndex",
                "HybridNumericWeights",
                "0.01");
            set("BuildSSDIndex",
                "HybridCandidateCount", "32");
        };

    auto index = VectorIndex::CreateInstance(
        IndexAlgoType::SPANN,
        VectorValueType::Float);
    BOOST_REQUIRE(index != nullptr);
    configure(index);
    auto* spann =
        dynamic_cast<SPANN::ISPANNIndex*>(
            index.get());
    BOOST_REQUIRE(spann != nullptr);
    spann->SetVectorTags(
        tags.data(), baseCount, tagCount);
    spann->SetNodeVectorAssignments(
        nodeAssignments);
    spann->SetPrimaryNodeVectorAssignments(
        nodeAssignments);
    BOOST_REQUIRE(
        index->BuildIndex(
            vectors, nullptr, true, false,
            false) == ErrorCode::Success);

    const std::string primaryPostings =
        indexDirectory +
        "/SPTAGFullList.bin";
    const std::string hybridStats =
        primaryPostings + ".hybrid.stats";
    const std::string hybridGraph =
        indexDirectory +
        "/HeadIndex/head_cross_edges.bin";
    BOOST_REQUIRE(
        std::filesystem::exists(
            primaryPostings));
    BOOST_CHECK(
        !std::filesystem::exists(
            indexDirectory +
            "/SPTAGHybridList.bin"));
    BOOST_REQUIRE(
        std::filesystem::exists(hybridStats));
    BOOST_REQUIRE(
        std::filesystem::exists(hybridGraph));
    Helper::HybridHeadCrossEdgesExtension
        crossExtension{};
    {
        std::ifstream crossEdges(
            hybridGraph, std::ios::binary);
        BOOST_REQUIRE(crossEdges.good());
        Helper::HeadCrossEdgesHeader header{};
        crossEdges.read(
            reinterpret_cast<char*>(&header),
            sizeof(header));
        crossEdges.read(
            reinterpret_cast<char*>(
                &crossExtension),
            sizeof(crossExtension));
        BOOST_REQUIRE(crossEdges.good());
        BOOST_CHECK_EQUAL(
            header.version,
            Helper::kHybridHeadCrossEdgesVersion);
        BOOST_CHECK_EQUAL(
            header.reserved,
            Helper::kHybridHeadCrossEdgesMarker);
        BOOST_CHECK_NE(
            crossExtension.generationFingerprint,
            0);
        BOOST_CHECK_NE(
            crossExtension.contentFingerprint,
            0);
    }
    auto* typed =
        dynamic_cast<SPANN::Index<float>*>(
            index.get());
    BOOST_REQUIRE(typed != nullptr);
    {
        std::ifstream posting(
            primaryPostings, std::ios::binary);
        BOOST_REQUIRE(posting.good());
        std::array<std::int32_t, 11> header{};
        posting.read(
            reinterpret_cast<char*>(
                header.data()),
            static_cast<std::streamsize>(
                header.size() *
                sizeof(std::int32_t)));
        BOOST_REQUIRE(posting.good());
        BOOST_REQUIRE_EQUAL(
            static_cast<std::uint32_t>(
                header[0]),
            0x314d5453U);
        BOOST_REQUIRE_EQUAL(header[1], 3);
        BOOST_REQUIRE_GT(header[5], 0);
        struct ListLayout {
            std::int32_t page = 0;
            std::uint16_t offset = 0;
            std::int32_t total = 0;
            std::uint16_t pages = 0;
            std::int32_t pure = 0;
        };
        std::vector<ListLayout> layouts(
            static_cast<size_t>(
                header[2]));
        for (auto& layout : layouts) {
            posting.read(
                reinterpret_cast<char*>(
                    &layout.page),
                sizeof(layout.page));
            posting.read(
                reinterpret_cast<char*>(
                    &layout.offset),
                sizeof(layout.offset));
            posting.read(
                reinterpret_cast<char*>(
                    &layout.total),
                sizeof(layout.total));
            posting.read(
                reinterpret_cast<char*>(
                    &layout.pages),
                sizeof(layout.pages));
            posting.read(
                reinterpret_cast<char*>(
                    &layout.pure),
                sizeof(layout.pure));
            BOOST_REQUIRE(posting.good());
            BOOST_REQUIRE_GE(
                layout.pure, 0);
            BOOST_REQUIRE_LE(
                layout.pure,
                layout.total);
        }
        std::size_t pureRecords = 0;
        std::size_t tailRecords = 0;
        for (size_t head = 0;
             head < layouts.size(); ++head) {
            const auto& layout = layouts[head];
            if (layout.total == 0) continue;
            const std::uint64_t offset =
                (static_cast<std::uint64_t>(
                     header[10]) +
                 static_cast<std::uint64_t>(
                     layout.page)) *
                    PageSize +
                layout.offset;
            std::vector<char> records(
                static_cast<size_t>(
                    layout.total) *
                static_cast<size_t>(
                    header[5]));
            posting.seekg(
                static_cast<std::streamoff>(
                    offset));
            posting.read(
                records.data(),
                static_cast<std::streamsize>(
                    records.size()));
            BOOST_REQUIRE(posting.good());
            std::unordered_set<std::int32_t>
                pureVIDs;
            std::unordered_set<std::int32_t>
                tailVIDs;
            const SizeType headVectorID =
                typed->GetGlobalVID(
                    static_cast<SizeType>(head));
            BOOST_REQUIRE_GE(headVectorID, 0);
            float previousDistance = -1.0f;
            for (int record = 0;
                 record < layout.total;
                 ++record) {
                std::int32_t vectorID = -1;
                std::memcpy(
                    &vectorID,
                    records.data() +
                        static_cast<size_t>(
                            record) *
                            static_cast<size_t>(
                                header[5]),
                    sizeof(vectorID));
                if (record < layout.pure) {
                    ++pureRecords;
                    BOOST_CHECK(
                        pureVIDs
                            .insert(vectorID)
                            .second);
                } else {
                    ++tailRecords;
                    BOOST_CHECK(
                        tailVIDs
                            .insert(vectorID)
                            .second);
                }
                float vectorDistance = 0.0f;
                for (DimensionType dim = 0;
                     dim < dimension; ++dim) {
                    const float delta =
                        data[static_cast<size_t>(
                                 headVectorID) *
                                 dimension +
                             dim] -
                        data[static_cast<size_t>(
                                 vectorID) *
                                 dimension +
                             dim];
                    vectorDistance += delta * delta;
                }
                float orderedDistance = vectorDistance;
                if (record < layout.pure) {
                    for (int column = 0;
                         column < 2; ++column) {
                        if (tags[
                                static_cast<size_t>(
                                    headVectorID) *
                                    tagCount +
                                column] !=
                            tags[
                                static_cast<size_t>(
                                    vectorID) *
                                    tagCount +
                                column]) {
                            orderedDistance += 100.0f;
                        }
                    }
                    const auto headNumeric =
                        tags[static_cast<size_t>(
                                 headVectorID) *
                                 tagCount +
                             2];
                    const auto vectorNumeric =
                        tags[static_cast<size_t>(
                                 vectorID) *
                                 tagCount +
                             2];
                    orderedDistance +=
                        0.01f *
                        static_cast<float>(
                            headNumeric > vectorNumeric
                                ? headNumeric -
                                      vectorNumeric
                                : vectorNumeric -
                                      headNumeric);
                }
                if (record == layout.pure) {
                    previousDistance = -1.0f;
                }
                BOOST_CHECK_GE(
                    orderedDistance + 1e-5f,
                    previousDistance);
                previousDistance = orderedDistance;
            }
        }
        BOOST_CHECK_GT(pureRecords, 0);
        BOOST_CHECK_GT(tailRecords, 0);
    }
    BOOST_REQUIRE(
        typed->GetDiskIndex() != nullptr);
    BOOST_CHECK(
        typed->GetDiskIndex()
            ->HasHybridPurePostings());
    BOOST_CHECK_EQUAL(
        typed->GetHeadBundleNodes().size(),
        1);
    BOOST_CHECK_EQUAL(
        typed->GetInlineHeadCrossEdgeSize(),
        16);
    BOOST_CHECK_GT(
        typed->GetInlineHeadCrossEdgeTotal(),
        0);
    BOOST_CHECK(
        typed->InlineHeadEdgesAreHybrid());
    std::shared_ptr<VectorIndex> persistedHead;
    BOOST_REQUIRE(
        VectorIndex::LoadIndex(
            indexDirectory + "/" +
                typed->GetHeadBundleNodes()
                    .front()
                    .headIndexRelativePath,
            persistedHead) ==
        ErrorCode::Success);
    auto* bktHead =
        dynamic_cast<BKT::Index<float>*>(
            persistedHead.get());
    BOOST_REQUIRE(bktHead != nullptr);
    BOOST_CHECK_EQUAL(
        bktHead->GetMutableGraph()
            .m_iNeighborhoodSize,
        32);
    const int globalHeadCount =
        static_cast<int>(
            typed->TotalHeadSampleCount());
    BOOST_REQUIRE_GT(globalHeadCount, 0);
    for (int head = 0;
         head < globalHeadCount; ++head) {
        const SizeType vectorID =
            typed->GetGlobalVID(head);
        BOOST_REQUIRE_GE(vectorID, 0);
        BOOST_REQUIRE_LT(vectorID, baseCount);
        VectorIndex::ThreadLocalSearchContext
            context;
        context.m_active = true;
        Cache::DNFClause clause;
        clause.lits.push_back(
            {0,
             tags[static_cast<size_t>(
                      vectorID) *
                  tagCount],
             Cache::DNF_EQ, 0});
        clause.lits.push_back(
            {1,
             tags[static_cast<size_t>(
                      vectorID) *
                      tagCount +
                  1],
             Cache::DNF_EQ, 0});
        const std::uint32_t numeric =
            tags[static_cast<size_t>(
                     vectorID) *
                     tagCount +
                 2];
        clause.lits.push_back(
            {2, numeric, Cache::DNF_GE, 1});
        clause.lits.push_back(
            {2, numeric, Cache::DNF_LE, 1});
        context.m_dnf.clauses.push_back(
            clause);
        VectorIndex::
            ThreadLocalSearchContextGuard
                guard(std::move(context));
        COMMON::QueryResultSet<float> query(
            data +
                static_cast<size_t>(vectorID) *
                    dimension,
            1);
        BOOST_REQUIRE(
            index->SearchIndex(query) ==
            ErrorCode::Success);
        BOOST_CHECK_EQUAL(
            query.GetResult(0)->VID,
            vectorID);
    }

    COMMON::QueryResultSet<float>
        unfilteredHybridCosts(data, 10);
    BOOST_REQUIRE(
        index->SearchIndex(
            unfilteredHybridCosts) ==
        ErrorCode::Success);
    COMMON::QueryResultSet<float>
        unfilteredOriginalCosts(data, 10);
    BOOST_REQUIRE(
        index->SearchIndex(
            unfilteredOriginalCosts) ==
        ErrorCode::Success);
    for (int rank = 0; rank < 10; ++rank) {
        BOOST_CHECK_EQUAL(
            unfilteredHybridCosts
                .GetResult(rank)
                ->VID,
            unfilteredOriginalCosts
                .GetResult(rank)
                ->VID);
        BOOST_CHECK_CLOSE(
            unfilteredHybridCosts
                .GetResult(rank)
                ->Dist,
            unfilteredOriginalCosts
                .GetResult(rank)
                ->Dist,
            0.0001);
    }

    const auto runFiltered =
        [&]() {
            VectorIndex::ThreadLocalSearchContext
                context;
            context.m_active = true;
            Cache::DNFClause clause;
            clause.lits.push_back(
                {0, 0, Cache::DNF_EQ, 0});
            clause.lits.push_back(
                {1, 0, Cache::DNF_EQ, 0});
            clause.lits.push_back(
                {2, 0, Cache::DNF_GE, 1});
            clause.lits.push_back(
                {2, 64, Cache::DNF_LE, 1});
            context.m_dnf.clauses.push_back(
                clause);
            VectorIndex::
                ThreadLocalSearchContextGuard
                    guard(std::move(context));
            COMMON::QueryResultSet<float>
                query(data, 5);
            BOOST_REQUIRE(
                index->SearchIndex(query) ==
                ErrorCode::Success);
            BOOST_REQUIRE_GE(
                query.GetResult(0)->VID, 0);
            for (int rank = 0; rank < 5;
                 ++rank) {
                const SizeType vectorID =
                    query.GetResult(rank)->VID;
                if (vectorID < 0) break;
                BOOST_CHECK_EQUAL(
                    tags[static_cast<size_t>(
                             vectorID) *
                         tagCount],
                    0);
                BOOST_CHECK_EQUAL(
                    tags[static_cast<size_t>(
                             vectorID) *
                             tagCount +
                         1],
                    0);
                BOOST_CHECK_LE(
                    tags[static_cast<size_t>(
                             vectorID) *
                             tagCount +
                         2],
                    64);
            }
        };
    runFiltered();
    {
        VectorIndex::ThreadLocalSearchContext context;
        context.m_active = true;
        Cache::DNFClause clause;
        clause.lits.push_back(
            {0, 1, Cache::DNF_EQ, 0});
        clause.lits.push_back(
            {1, 0, Cache::DNF_EQ, 0});
        context.m_dnf.clauses.push_back(clause);
        VectorIndex::ThreadLocalSearchContextGuard
            guard(std::move(context));
        COMMON::QueryResultSet<float> query(
            data + dimension, 5);
        BOOST_REQUIRE(
            index->SearchIndex(query) ==
            ErrorCode::Success);
        BOOST_REQUIRE_GE(
            query.GetResult(0)->VID, 0);
        for (int rank = 0; rank < 5; ++rank) {
            const SizeType vectorID =
                query.GetResult(rank)->VID;
            if (vectorID < 0) break;
            BOOST_CHECK_EQUAL(
                tags[static_cast<size_t>(
                         vectorID) *
                     tagCount],
                1);
            BOOST_CHECK_EQUAL(
                tags[static_cast<size_t>(
                         vectorID) *
                         tagCount +
                     1],
                0);
        }
    }

    BOOST_REQUIRE(
        index->SaveIndex(indexDirectory) ==
        ErrorCode::Success);
    index.reset();
    std::shared_ptr<VectorIndex> reloaded;
    BOOST_REQUIRE(
        VectorIndex::LoadIndex(
            indexDirectory, reloaded) ==
        ErrorCode::Success);
    auto* reloadedTyped =
        dynamic_cast<SPANN::Index<float>*>(
            reloaded.get());
    BOOST_REQUIRE(reloadedTyped != nullptr);
    BOOST_CHECK(
        reloadedTyped->GetDiskIndex()
            ->HasHybridPurePostings());
    COMMON::QueryResultSet<float> reloadQuery(
        data, 10);
    BOOST_REQUIRE(
        reloaded->SearchIndex(reloadQuery) ==
        ErrorCode::Success);
    for (int rank = 0; rank < 10; ++rank) {
        BOOST_CHECK_EQUAL(
            reloadQuery.GetResult(rank)->VID,
            unfilteredOriginalCosts
                .GetResult(rank)
                ->VID);
    }
    {
        VectorIndex::ThreadLocalSearchContext context;
        context.m_active = true;
        Cache::DNFClause clause;
        clause.lits.push_back(
            {0, 1, Cache::DNF_EQ, 0});
        context.m_dnf.clauses.push_back(clause);
        VectorIndex::ThreadLocalSearchContextGuard
            guard(std::move(context));
        COMMON::QueryResultSet<float> query(
            data + dimension, 5);
        BOOST_REQUIRE(
            reloaded->SearchIndex(query) ==
            ErrorCode::Success);
        BOOST_REQUIRE_GE(
            query.GetResult(0)->VID, 0);
        for (int rank = 0; rank < 5; ++rank) {
            const SizeType vectorID =
                query.GetResult(rank)->VID;
            if (vectorID < 0) break;
            BOOST_CHECK_EQUAL(
                tags[static_cast<size_t>(
                        vectorID) *
                    tagCount],
                1);
        }
    }

    std::ifstream memoryConfigFile(
        indexDirectory + "/indexloader.ini",
        std::ios::binary);
    BOOST_REQUIRE(memoryConfigFile.good());
    const std::string memoryConfig(
        (std::istreambuf_iterator<char>(
            memoryConfigFile)),
        std::istreambuf_iterator<char>());
    std::vector<ByteArray> memoryBlobs;
    std::shared_ptr<VectorIndex> memoryLoaded;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            memoryConfig, memoryBlobs,
            memoryLoaded) == ErrorCode::Fail);
    reloaded.reset();

    const std::string configPath =
        indexDirectory + "/indexloader.ini";
    const auto withConfigValue =
        [](std::string config,
           const std::string& key,
           const std::string& value) {
            const std::string marker =
                key + "=";
            const size_t markerPosition =
                config.find(marker);
            BOOST_REQUIRE_NE(
                markerPosition,
                std::string::npos);
            const size_t begin =
                markerPosition + marker.size();
            size_t end =
                config.find('\n', begin);
            if (end == std::string::npos) {
                end = config.size();
            }
            if (end > begin &&
                config[end - 1] == '\r') {
                --end;
            }
            config.replace(
                begin, end - begin, value);
            return config;
        };
    const auto writeConfig =
        [&](const std::string& config) {
            std::ofstream output(
                configPath,
                std::ios::binary |
                    std::ios::trunc);
            BOOST_REQUIRE(output.good());
            output.write(
                config.data(),
                static_cast<std::streamsize>(
                    config.size()));
            BOOST_REQUIRE(output.good());
        };
    writeConfig(withConfigValue(
        memoryConfig,
        "HybridCategoricalWeights",
        "101,100"));
    std::shared_ptr<VectorIndex>
        rejectedDistanceConfig;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory,
            rejectedDistanceConfig) ==
        ErrorCode::Fail);
    writeConfig(memoryConfig);
    writeConfig(withConfigValue(
        memoryConfig, "EnableHybridDistance",
        "false"));
    std::shared_ptr<VectorIndex>
        rejectedDisabledHybrid;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
        indexDirectory,
        rejectedDisabledHybrid) ==
        ErrorCode::Fail);
    writeConfig(memoryConfig);
    writeConfig(withConfigValue(
        memoryConfig, "SSDIndex",
        "SPTAGFullList.bin.hybrid.stats"));
    std::shared_ptr<VectorIndex> derivedAlias;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory, derivedAlias) ==
        ErrorCode::Fail);
    writeConfig(memoryConfig);

    {
        std::fstream posting(
            primaryPostings,
            std::ios::in | std::ios::out |
                std::ios::binary);
        BOOST_REQUIRE(posting.good());
        const std::int32_t legacyVersion = 1;
        posting.seekp(sizeof(std::int32_t));
        posting.write(
            reinterpret_cast<const char*>(
                &legacyVersion),
            sizeof(legacyVersion));
        BOOST_REQUIRE(posting.good());
    }
    std::shared_ptr<VectorIndex> legacyPostingLayout;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory,
        legacyPostingLayout) ==
        ErrorCode::Fail);
    {
        std::fstream posting(
            primaryPostings,
            std::ios::in | std::ios::out |
                std::ios::binary);
        BOOST_REQUIRE(posting.good());
        const std::int32_t currentVersion = 2;
        posting.seekp(sizeof(std::int32_t));
        posting.write(
            reinterpret_cast<const char*>(
                &currentVersion),
            sizeof(currentVersion));
        BOOST_REQUIRE(posting.good());
    }

    {
        std::fstream crossEdges(
        hybridGraph,
        std::ios::in | std::ios::out |
            std::ios::binary);
        BOOST_REQUIRE(crossEdges.good());
        const std::int32_t conventionalMarker = 0;
        crossEdges.seekp(
        offsetof(
            Helper::HeadCrossEdgesHeader,
            reserved));
        crossEdges.write(
        reinterpret_cast<const char*>(
            &conventionalMarker),
        sizeof(conventionalMarker));
        BOOST_REQUIRE(crossEdges.good());
    }
    std::shared_ptr<VectorIndex> rejectedMarker;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
        indexDirectory, rejectedMarker) ==
        ErrorCode::Fail);
    {
        std::fstream crossEdges(
        hybridGraph,
        std::ios::in | std::ios::out |
            std::ios::binary);
        BOOST_REQUIRE(crossEdges.good());
        const std::int32_t hybridMarker =
        Helper::kHybridHeadCrossEdgesMarker;
        crossEdges.seekp(
        offsetof(
            Helper::HeadCrossEdgesHeader,
            reserved));
        crossEdges.write(
        reinterpret_cast<const char*>(
            &hybridMarker),
        sizeof(hybridMarker));
        BOOST_REQUIRE(crossEdges.good());
    }
    {
        std::fstream crossEdges(
            hybridGraph,
            std::ios::in | std::ios::out |
                std::ios::binary);
        BOOST_REQUIRE(crossEdges.good());
        const std::uint64_t staleGeneration =
            crossExtension.generationFingerprint ^
            0x9e3779b97f4a7c15ULL;
        crossEdges.seekp(
            sizeof(Helper::HeadCrossEdgesHeader) +
            offsetof(
                Helper::HybridHeadCrossEdgesExtension,
                generationFingerprint));
        crossEdges.write(
            reinterpret_cast<const char*>(
                &staleGeneration),
            sizeof(staleGeneration));
        BOOST_REQUIRE(crossEdges.good());
    }
    std::shared_ptr<VectorIndex>
        rejectedCrossGeneration;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory,
            rejectedCrossGeneration) ==
        ErrorCode::Fail);
    {
        std::fstream crossEdges(
            hybridGraph,
            std::ios::in | std::ios::out |
                std::ios::binary);
        BOOST_REQUIRE(crossEdges.good());
        crossEdges.seekp(
            sizeof(Helper::HeadCrossEdgesHeader) +
            offsetof(
                Helper::HybridHeadCrossEdgesExtension,
                generationFingerprint));
        crossEdges.write(
            reinterpret_cast<const char*>(
                &crossExtension
                     .generationFingerprint),
            sizeof(
                crossExtension
                    .generationFingerprint));
        BOOST_REQUIRE(crossEdges.good());
    }
    std::streampos mutatedEntryPosition =
        std::streampos(-1);
    Helper::HeadCrossEdgeEntry originalEntry{};
    {
        std::fstream crossEdges(
            hybridGraph,
            std::ios::in | std::ios::out |
                std::ios::binary);
        BOOST_REQUIRE(crossEdges.good());
        Helper::HeadCrossEdgesHeader bodyHeader{};
        Helper::HybridHeadCrossEdgesExtension
            bodyExtension{};
        crossEdges.read(
            reinterpret_cast<char*>(&bodyHeader),
            sizeof(bodyHeader));
        crossEdges.read(
            reinterpret_cast<char*>(&bodyExtension),
            sizeof(bodyExtension));
        BOOST_REQUIRE(crossEdges.good());

        std::int32_t mutationSource = -1;
        std::vector<std::int32_t> sourceVIDs;
        std::vector<std::int32_t> mutationTargets;
        for (std::int32_t record = 0;
             record < bodyHeader.totalHeads;
             ++record) {
            std::int32_t sourceVID = -1;
            std::int32_t edgeCount = 0;
            crossEdges.read(
                reinterpret_cast<char*>(&sourceVID),
                sizeof(sourceVID));
            crossEdges.read(
                reinterpret_cast<char*>(&edgeCount),
                sizeof(edgeCount));
            BOOST_REQUIRE(crossEdges.good());
            BOOST_REQUIRE(edgeCount >= 0);
            BOOST_REQUIRE(
                edgeCount <=
                bodyHeader.maxEdgesPerHead);
            sourceVIDs.push_back(sourceVID);

            const std::streampos firstEntry =
                crossEdges.tellg();
            std::vector<Helper::HeadCrossEdgeEntry>
                entries(
                    static_cast<size_t>(edgeCount));
            if (edgeCount > 0) {
                crossEdges.read(
                    reinterpret_cast<char*>(
                        entries.data()),
                    static_cast<std::streamsize>(
                        entries.size() *
                        sizeof(entries.front())));
                BOOST_REQUIRE(crossEdges.good());
            }
            if (mutatedEntryPosition ==
                    std::streampos(-1) &&
                !entries.empty()) {
                mutatedEntryPosition = firstEntry;
                mutationSource = sourceVID;
                originalEntry = entries.front();
                mutationTargets.reserve(entries.size());
                for (const auto& entry : entries) {
                    mutationTargets.push_back(
                        entry.neighborGlobalVID);
                }
            }
        }
        BOOST_REQUIRE(
            mutatedEntryPosition !=
            std::streampos(-1));

        std::int32_t replacementTarget = -1;
        for (const std::int32_t sourceVID :
             sourceVIDs) {
            if (sourceVID != mutationSource &&
                sourceVID !=
                    originalEntry.neighborGlobalVID &&
                std::find(
                    mutationTargets.begin(),
                    mutationTargets.end(),
                    sourceVID) ==
                    mutationTargets.end()) {
                replacementTarget = sourceVID;
                break;
            }
        }
        BOOST_REQUIRE(replacementTarget >= 0);
        Helper::HeadCrossEdgeEntry mutatedEntry =
            originalEntry;
        mutatedEntry.neighborGlobalVID =
            replacementTarget;
        crossEdges.clear();
        crossEdges.seekp(mutatedEntryPosition);
        crossEdges.write(
            reinterpret_cast<const char*>(
                &mutatedEntry),
            sizeof(mutatedEntry));
        BOOST_REQUIRE(crossEdges.good());
    }
    std::shared_ptr<VectorIndex> rejectedEdgeBody;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory,
            rejectedEdgeBody) ==
        ErrorCode::Fail);
    {
        std::fstream crossEdges(
            hybridGraph,
            std::ios::in | std::ios::out |
                std::ios::binary);
        BOOST_REQUIRE(crossEdges.good());
        crossEdges.seekp(mutatedEntryPosition);
        crossEdges.write(
            reinterpret_cast<const char*>(
                &originalEntry),
            sizeof(originalEntry));
        BOOST_REQUIRE(crossEdges.good());
    }
    {
        std::fstream stats(
        hybridStats,
        std::ios::in | std::ios::out |
            std::ios::binary);
        BOOST_REQUIRE(stats.good());
        const std::uint64_t staleGeneration =
        0x123456789abcdef0ULL;
        stats.seekp(
        offsetof(
            SPANN::HybridRoutingStatsHeader,
            m_generationFingerprint));
        stats.write(
        reinterpret_cast<const char*>(
            &staleGeneration),
        sizeof(staleGeneration));
        BOOST_REQUIRE(stats.good());
    }
    std::shared_ptr<VectorIndex> rejectedGeneration;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
        indexDirectory,
        rejectedGeneration) ==
        ErrorCode::Fail);
    std::filesystem::remove_all(indexDirectory);
}

BOOST_AUTO_TEST_CASE(StaticDistanceOrderSerialization)
{
    constexpr SizeType vectorCount = 4;
    constexpr DimensionType dimension = 2;
    ByteArray bytes = ByteArray::Alloc(
        sizeof(float) * static_cast<size_t>(vectorCount) * dimension);
    auto* data = reinterpret_cast<float*>(bytes.Data());
    for (SizeType vid = 0; vid < vectorCount; ++vid) {
        data[static_cast<size_t>(vid) * dimension] = static_cast<float>(vid);
        data[static_cast<size_t>(vid) * dimension + 1] = static_cast<float>(vid + 10);
    }
    auto vectors = std::make_shared<BasicVectorSet>(
        bytes, VectorValueType::Float, dimension, vectorCount);

    const auto edge = [](SizeType node, float distance, SizeType tonode) {
        Edge value;
        value.node = node;
        value.distance = distance;
        value.tonode = tonode;
        return value;
    };
    SPANN::Selection selections(vectorCount, ".");
    selections.m_selections = {
        edge(0, 4.0f, 2),
        edge(0, 1.0f, 1),
        edge(0, 4.0f, 0),
        edge(1, 0.0f, 3),
    };
    std::sort(
        selections.m_selections.begin(),
        selections.m_selections.end(),
        SPANN::Selection::g_edgeComparer);

    SPANN::ExtraStaticSearcher<float> searcher;
    const std::string posting = searcher.GetPostingListFullData(
        0, 3, selections, vectors);
    constexpr size_t stride = sizeof(int) + sizeof(float) * dimension;
    BOOST_REQUIRE_EQUAL(posting.size(), 3 * stride);

    const std::array<int, 3> expectedVIDs = {1, 0, 2};
    for (size_t record = 0; record < expectedVIDs.size(); ++record) {
        int vid = -1;
        std::memcpy(&vid, posting.data() + record * stride, sizeof(vid));
        BOOST_CHECK_EQUAL(vid, expectedVIDs[record]);

        std::array<float, dimension> serialized {};
        std::memcpy(
            serialized.data(),
            posting.data() + record * stride + sizeof(vid),
            sizeof(serialized));
        BOOST_CHECK_EQUAL(serialized[0], data[static_cast<size_t>(vid) * dimension]);
        BOOST_CHECK_EQUAL(serialized[1], data[static_cast<size_t>(vid) * dimension + 1]);
    }

    SPANN::ExtraWorkSpace::PostingReadRange range;
    range.SetContiguousRecordRange(0, 0, 9, static_cast<int>(stride));
    BOOST_CHECK_EQUAL(range.m_scanBegin, 0);
    BOOST_CHECK_EQUAL(range.m_scanEnd, 9);
    BOOST_CHECK_EQUAL(range.m_secondScanBegin, -1);
    BOOST_CHECK_EQUAL(range.m_secondScanEnd, -1);
    BOOST_CHECK_EQUAL(range.ScanCount(), 9);
    int offset = 3;
    BOOST_CHECK(range.NormalizeScanOffset(offset));
    BOOST_CHECK_EQUAL(offset, 3);
}

BOOST_AUTO_TEST_CASE(LimitedTagSupportPersistenceValidation)
{
    const std::string path =
        "limited_tag_support_validation.bin";
    std::filesystem::remove(path);
    SPANN::LimitedTagSupport support;
    BOOST_REQUIRE(support.Initialize(
        4, 2, 2, 0, 1,
        0x123456789abcdef0ULL));
    BOOST_REQUIRE(support.SetHeadTags(
        0, {10U, 20U}));
    BOOST_REQUIRE(support.SetHeadTags(
        1, {10U, 30U}));
    BOOST_REQUIRE(support.SetHeadTags(
        2, {20U, 30U}));
    BOOST_REQUIRE(support.SetHeadTags(
        3, {10U}));
    BOOST_REQUIRE(support.SetTagVectorCounts(
        10, {{10U, 4}, {20U, 3}, {30U, 3}}));
    const std::array<std::uint32_t, 4> ownTags = {
        10U, 10U, 20U, 10U};
    for (SizeType head = 0;
         head < static_cast<SizeType>(ownTags.size());
         ++head)
    {
        BOOST_REQUIRE(support.SetHeadAttributes(
            head, &ownTags[static_cast<size_t>(head)], 1));
    }
    BOOST_CHECK(!support.SetHeadTags(
        3, {10U, 10U}));
    std::string error;
    BOOST_REQUIRE(support.Save(path, &error));

    SPANN::LimitedTagSupport loaded;
    BOOST_REQUIRE(loaded.Load(
        path, 4, 2, 2,
        0, 1,
        0x123456789abcdef0ULL, &error));
    BOOST_CHECK_EQUAL(
        loaded.OwnTag(0), 10U);
    BOOST_CHECK(loaded.Supports(0, 10U));
    BOOST_CHECK(loaded.Supports(0, 20U));
    BOOST_CHECK(!loaded.Supports(0, 30U));
    BOOST_CHECK(loaded.HasTagVectorCounts());
    BOOST_CHECK_EQUAL(loaded.VectorCount(), 10U);
    BOOST_CHECK_EQUAL(loaded.TagVectorCount(10U), 4U);
    BOOST_CHECK(loaded.TagSelectivityInRange(
        20U, 0.29f, 0.30f));
    BOOST_CHECK(!loaded.TagSelectivityInRange(
        20U, 0.30f, 1.0f));
    BOOST_REQUIRE(loaded.HeadAttributes(2) != nullptr);
    BOOST_CHECK_EQUAL(loaded.HeadAttributes(2)[0], 20U);

    const std::string legacyPath =
        "limited_tag_support_v1_validation.bin";
    std::filesystem::remove(legacyPath);
    {
        const std::vector<std::uint32_t> legacyTags = {
            10U, 20U,
            10U, 30U,
            20U, 30U,
            10U, SPANN::LimitedTagSupport::EmptyTag};
        SPANN::LimitedTagSupport::HeaderV1 header;
        header.m_headCount = 4;
        header.m_slotsPerHead = 2;
        header.m_voteHeadCount = 2;
        header.m_minHeadCount = 2;
        header.m_tagCount = 3;
        header.m_generationFingerprint =
            0x123456789abcdef0ULL;
        std::uint64_t fingerprint =
            1469598103934665603ULL;
        const auto* bytes =
            reinterpret_cast<const std::uint8_t*>(
                legacyTags.data());
        for (size_t byte = 0;
             byte < legacyTags.size() *
                        sizeof(std::uint32_t);
             ++byte)
        {
            fingerprint ^= bytes[byte];
            fingerprint *= 1099511628211ULL;
        }
        header.m_bodyFingerprint = fingerprint;
        std::ofstream output(
            legacyPath,
            std::ios::binary | std::ios::trunc);
        BOOST_REQUIRE(output.good());
        output.write(
            reinterpret_cast<const char*>(&header),
            sizeof(header));
        output.write(
            reinterpret_cast<const char*>(
                legacyTags.data()),
            static_cast<std::streamsize>(
                legacyTags.size() *
                sizeof(std::uint32_t)));
        output.close();
        BOOST_REQUIRE(output.good());
    }
    SPANN::LimitedTagSupport legacy;
    BOOST_REQUIRE(legacy.Load(
        legacyPath, 4, 2, 2, 0, 1,
        0x123456789abcdef0ULL, &error));
    for (SizeType head = 0; head < 4; ++head)
    {
        BOOST_REQUIRE(
            legacy.HeadAttributes(head) != nullptr);
        BOOST_CHECK_EQUAL(
            legacy.HeadAttributes(head)[0],
            legacy.OwnTag(head));
    }
    std::filesystem::remove(legacyPath);

    const std::string legacyV2Path =
        "limited_tag_support_v2_validation.bin";
    std::filesystem::remove(legacyV2Path);
    {
        const std::vector<std::uint32_t> legacyTags = {
            10U, 20U,
            10U, 30U,
            20U, 30U,
            10U, SPANN::LimitedTagSupport::EmptyTag};
        const std::vector<std::uint32_t> legacyAttributes(
            ownTags.begin(), ownTags.end());
        SPANN::LimitedTagSupport::HeaderV2 header;
        header.m_headCount = 4;
        header.m_slotsPerHead = 2;
        header.m_voteHeadCount = 2;
        header.m_minHeadCount = 2;
        header.m_tagCount = 3;
        header.m_keyColumn = 0;
        header.m_attributeCount = 1;
        header.m_generationFingerprint =
            0x123456789abcdef0ULL;
        std::uint64_t fingerprint =
            1469598103934665603ULL;
        const auto appendFingerprint =
            [&](const std::vector<std::uint32_t>& values) {
                const auto* bytes =
                    reinterpret_cast<
                        const std::uint8_t*>(
                        values.data());
                for (size_t byte = 0;
                     byte < values.size() *
                                sizeof(std::uint32_t);
                     ++byte)
                {
                    fingerprint ^= bytes[byte];
                    fingerprint *= 1099511628211ULL;
                }
            };
        appendFingerprint(legacyTags);
        appendFingerprint(legacyAttributes);
        header.m_bodyFingerprint = fingerprint;
        std::ofstream output(
            legacyV2Path,
            std::ios::binary | std::ios::trunc);
        BOOST_REQUIRE(output.good());
        output.write(
            reinterpret_cast<const char*>(&header),
            sizeof(header));
        output.write(
            reinterpret_cast<const char*>(
                legacyTags.data()),
            static_cast<std::streamsize>(
                legacyTags.size() *
                sizeof(std::uint32_t)));
        output.write(
            reinterpret_cast<const char*>(
                legacyAttributes.data()),
            static_cast<std::streamsize>(
                legacyAttributes.size() *
                sizeof(std::uint32_t)));
        output.close();
        BOOST_REQUIRE(output.good());
    }
    SPANN::LimitedTagSupport legacyV2;
    BOOST_REQUIRE(legacyV2.Load(
        legacyV2Path, 4, 2, 2, 0, 1,
        0x123456789abcdef0ULL, &error));
    BOOST_CHECK(!legacyV2.HasTagVectorCounts());
    BOOST_CHECK_EQUAL(
        legacyV2.HeadAttributes(2)[0], 20U);
    std::filesystem::remove(legacyV2Path);

    const auto rejects =
        [&](SizeType heads, int slots,
            int minimum,
            std::uint64_t generation) {
            SPANN::LimitedTagSupport rejected;
            std::string rejectedError;
            return !rejected.Load(
                path, heads, slots,
                minimum, 0, 1, generation,
                &rejectedError);
        };
    BOOST_CHECK(rejects(
        5, 2, 2,
        0x123456789abcdef0ULL));
    BOOST_CHECK(rejects(
        4, 3, 2,
        0x123456789abcdef0ULL));
    BOOST_CHECK(rejects(
        4, 2, 3,
        0x123456789abcdef0ULL));
    BOOST_CHECK(rejects(
        4, 2, 2,
        0x123456789abcdef1ULL));

    {
        std::fstream output(
            path,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(output.good());
        output.seekp(
            7 * sizeof(std::uint32_t),
            std::ios::beg);
        const std::uint32_t invalidTagCount =
            (std::numeric_limits<
                std::uint32_t>::max)();
        output.write(
            reinterpret_cast<const char*>(
                &invalidTagCount),
            sizeof(invalidTagCount));
        BOOST_REQUIRE(output.good());
    }
    SPANN::LimitedTagSupport invalidTagCount;
    BOOST_CHECK(!invalidTagCount.Load(
        path, 4, 2, 2,
        0, 1,
        0x123456789abcdef0ULL, &error));
    BOOST_REQUIRE(support.Save(path, &error));

    {
        std::fstream output(
            path,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(output.good());
        output.seekp(
            static_cast<std::streamoff>(
                sizeof(SPANN::LimitedTagSupport::Header) +
                8 * sizeof(std::uint32_t)),
            std::ios::beg);
        const std::uint32_t invalidOwnAttribute = 99U;
        output.write(
            reinterpret_cast<const char*>(
                &invalidOwnAttribute),
            sizeof(invalidOwnAttribute));
        BOOST_REQUIRE(output.good());
    }
    SPANN::LimitedTagSupport invalidAttribute;
    BOOST_CHECK(!invalidAttribute.Load(
        path, 4, 2, 2, 0, 1,
        0x123456789abcdef0ULL, &error));
    BOOST_REQUIRE(support.Save(path, &error));

    {
        std::fstream output(
            path,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(output.good());
        std::uint32_t invalidMagic = 0;
        output.write(
            reinterpret_cast<const char*>(
                &invalidMagic),
            sizeof(invalidMagic));
        BOOST_REQUIRE(output.good());
    }
    SPANN::LimitedTagSupport invalidMagic;
    BOOST_CHECK(!invalidMagic.Load(
        path, 4, 2, 2,
        0, 1,
        0x123456789abcdef0ULL, &error));
    std::filesystem::remove(path);
}

BOOST_AUTO_TEST_CASE(LimitedTagBaseSupportFollowsActualRNGNotSearchRanks)
{
    ByteArray bytes = ByteArray::Alloc(4 * sizeof(float));
    const float values[] = {1.0f, 1.1f, -2.0f, 0.0f};
    std::memcpy(bytes.Data(), values, sizeof(values));
    auto full = std::make_shared<BasicVectorSet>(bytes, VectorValueType::Float, 1, 4);
    auto heads = std::make_shared<BasicVectorSet>(bytes, VectorValueType::Float, 1, 3);
    auto headIndex = VectorIndex::CreateInstance(IndexAlgoType::BKT, VectorValueType::Float);
    BOOST_REQUIRE(headIndex->SetParameter("DistCalcMethod", "L2") == ErrorCode::Success);
    BOOST_REQUIRE(headIndex->SetParameter("NumberOfThreads", "1") == ErrorCode::Success);
    BOOST_REQUIRE(headIndex->BuildIndex(heads, nullptr, true, false, false) == ErrorCode::Success);
    SPANN::ExtraStaticSearcher<float> searcher;
    int searches = 0;
    searcher.SetHeadPlacementSearch([&](const float*, int count,
        const std::function<bool(SizeType)>&,
        COMMON::QueryResultSet<float>& results) {
        ++searches;
        BOOST_REQUIRE_EQUAL(count, 3);
        for (int rank = 0; rank < 3; ++rank)
        {
            results.GetResult(rank)->VID = rank;
            results.GetResult(rank)->Dist = values[rank] * values[rank];
        }
        return ErrorCode::Success;
    });
    SPANN::Options options;
    options.m_replicaCount = 2;
    options.m_excludehead = true;
    options.m_rngFactor = 1;
    options.m_iSSDNumberOfThreads = 1;
    SPANN::Selection original(0, ".", "retained_o_rng_selection");
    std::vector<std::atomic<std::uint8_t>> replicas(4);
    std::vector<std::atomic_int> counts(3);
    for (auto& value : replicas) value = 0;
    for (auto& value : counts) value = 0;
    std::uint64_t checked = 0;
    BOOST_REQUIRE(searcher.BuildCompactOriginalSelections(
        original, replicas, counts, {{0, 0}, {1, 1}, {2, 2}}, full,
        headIndex, 4, 3, options, checked));
    BOOST_CHECK_EQUAL(searches, 1);
    BOOST_REQUIRE_EQUAL(original.m_selections.size(), 2U);
    BOOST_CHECK_EQUAL(counts[0].load(), 1);
    BOOST_CHECK_EQUAL(counts[1].load(), 0);
    BOOST_CHECK_EQUAL(counts[2].load(), 1);
    SPANN::LimitedTagSupport support;
    BOOST_REQUIRE(support.Initialize(3, 2, 1, 0, 1, 12345));
    BOOST_REQUIRE(SPANN::SelectRetainedOriginalBaseTags(
        support, {10, 20, 30}, original.m_selections, {1, 0, 1}, 4,
        [](SizeType vid) { return static_cast<std::uint32_t>((vid + 1) * 10); }));
    BOOST_CHECK(support.Supports(0, 40));
    BOOST_CHECK(!support.Supports(1, 40)); // rank two was rejected by actual RNG
    BOOST_CHECK(support.Supports(2, 40));  // rank three survived actual RNG
    BOOST_CHECK_EQUAL(searches, 1);       // support selection did not navigate
}

namespace
{
class HierarchyTestIndex : public KDT::Index<float>
{
public:
    explicit HierarchyTestIndex(std::vector<float> p_values)
        : m_values(std::move(p_values)) {}

    const void* GetSample(SizeType p_id) const override
    {
        return p_id >= 0 && static_cast<size_t>(p_id) < m_values.size()
            ? &m_values[static_cast<size_t>(p_id)] : nullptr;
    }
    SizeType GetNumSamples() const override
    {
        return static_cast<SizeType>(m_values.size());
    }
    DimensionType GetFeatureDim() const override { return 1; }
    float ComputeDistance(const void* p_left, const void* p_right) const override
    {
        ++m_distanceCalls;
        const float delta =
            *static_cast<const float*>(p_left) - *static_cast<const float*>(p_right);
        return delta * delta;
    }
    ErrorCode SearchIndex(QueryResult& p_results, bool = false) const override
    {
        return SearchTestPoints(p_results, p_results.GetResultNum(), nullptr);
    }
    ErrorCode SearchIndexWithMaxCheck(
        QueryResult& p_results, int p_maxCheck, bool = false) const override
    {
        return SearchTestPoints(p_results, p_maxCheck, nullptr);
    }
    ErrorCode SearchIndexWithResultFilter(
        QueryResult& p_results, std::function<bool(SizeType)> p_filter,
        int p_maxCheck, bool = false) const override
    {
        ++m_resultFilterCalls;
        return SearchTestPoints(p_results, p_maxCheck, p_filter);
    }
    mutable std::vector<int> m_probes;
    mutable size_t m_distanceCalls = 0;
    mutable size_t m_resultFilterCalls = 0;

private:
    ErrorCode SearchTestPoints(
        QueryResult& p_results, int p_maxCheck,
        const std::function<bool(SizeType)>& p_filter) const
    {
        if (p_maxCheck <= 0) return ErrorCode::Fail;
        m_probes.push_back(p_results.GetResultNum());
        std::vector<std::pair<float, SizeType>> sorted;
        for (SizeType id = 0; id < GetNumSamples(); ++id)
            if (!p_filter || p_filter(id))
                sorted.emplace_back(ComputeDistance(p_results.GetTarget(), GetSample(id)), id);
        std::sort(sorted.begin(), sorted.end());
        p_results.Reset();
        for (int rank = 0;
             rank < p_results.GetResultNum() && static_cast<size_t>(rank) < sorted.size();
             ++rank)
            p_results.SetResult(rank, sorted[rank].second, sorted[rank].first);
        p_results.SetScanned(1);
        return ErrorCode::Success;
    }
    std::vector<float> m_values;
};

std::shared_ptr<VectorSet> HierarchyTestCatalog(const std::vector<float>& p_values)
{
    ByteArray bytes = ByteArray::Alloc(p_values.size() * sizeof(float));
    std::memcpy(bytes.Data(), p_values.data(), bytes.Length());
    return std::make_shared<BasicVectorSet>(
        bytes, VectorValueType::Float, 1, static_cast<SizeType>(p_values.size()));
}

SPANN::SecondLevelHeadPostings HierarchyTestPostings(
    SizeType p_lowerCount, int p_replicas, const std::vector<std::uint64_t>& p_samples,
    std::vector<std::uint64_t> p_offsets,
    std::vector<SPANN::SecondLevelHeadPostings::Member> p_members,
    std::vector<SPANN::SecondLevelHeadPostings::Signature> p_signatures)
{
    SPANN::SecondLevelHeadPostings postings;
    std::string error;
    BOOST_REQUIRE_MESSAGE(postings.Initialize(
        p_lowerCount, static_cast<SizeType>(p_samples.size()), p_replicas,
        1, 1, 0.0, 1.0, p_samples, std::move(p_offsets),
        std::move(p_members), std::move(p_signatures), &error), error);
    return postings;
}
}

BOOST_AUTO_TEST_CASE(HierarchyPlacementRanksAllChildrenBeforeBeamSelection)
{
    constexpr SizeType count = 130;
    std::vector<float> values(count);
    std::iota(values.begin(), values.end(), 100.0f);
    values[count - 2] = 1.0f;
    values[count - 1] = 0.0f;
    auto heads = std::make_shared<HierarchyTestIndex>(values);
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {nullptr, top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {
        HierarchyTestCatalog(values), HierarchyTestCatalog({0, 1})};
    using Member = SPANN::SecondLevelHeadPostings::Member;
    std::vector<std::vector<std::uint64_t>> offsets(2);
    std::vector<std::vector<Member>> members(2);
    for (SizeType head = 0; head < count; ++head)
    {
        offsets[0].push_back(members[0].size());
        members[0].push_back(head);
        members[0].push_back((head + 1) % count);
        members[1].push_back(head);
    }
    offsets[0].push_back(members[0].size());
    members[1].resize(2 * count);
    std::iota(members[1].begin() + count, members[1].end(), Member{0});
    offsets[1] = {0, count, 2 * count};
    const float target = 0;

    for (bool reversed : {false, true})
    {
        if (reversed)
        {
            std::reverse(members[1].begin(), members[1].begin() + count);
            std::reverse(members[1].begin() + count, members[1].end());
        }
        for (int resultCount : {2, 96})
        {
            const int beam = (std::max)(64, resultCount);
            std::vector<int> checks(count, 0);
            const std::function<bool(SizeType)> filter = reversed
                ? std::function<bool(SizeType)>([&](SizeType head) {
                    ++checks[head];
                    return true;
                }) : nullptr;
            heads->m_distanceCalls = 0;
            COMMON::QueryResultSet<float> results(&target, resultCount);
            BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchyForPlacement(
                &target, resultCount, filter, results, heads, indexes,
                catalogs, offsets, members) == ErrorCode::Success);
            for (int rank = 0; rank < resultCount; ++rank)
            {
                const SizeType expected = rank < 2 ? count - 1 - rank : rank - 2;
                BOOST_CHECK_EQUAL(results.GetResult(rank)->VID, expected);
                BOOST_CHECK_EQUAL(results.GetResult(rank)->Dist, values[expected] * values[expected]);
            }
            BOOST_CHECK_EQUAL(heads->m_distanceCalls, count + beam + 1);
            BOOST_CHECK_EQUAL(results.GetScanned(), 1 + 2 * count + 2 * beam);
            BOOST_CHECK_EQUAL(top->m_probes.back(), beam);
            for (int checked : checks) BOOST_CHECK_LE(checked, 1);
            BOOST_CHECK_EQUAL(std::accumulate(checks.begin(), checks.end(), 0),
                              reversed ? beam + 1 : 0);
        }
    }

    std::vector<int> checks(count, 0);
    COMMON::QueryResultSet<float> filtered(&target, 2);
    heads->m_distanceCalls = 0;
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchyForPlacement(
        &target, 2, [&](SizeType head) { ++checks[head]; return head == count - 2; },
        filtered, heads, indexes, catalogs, offsets, members) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(filtered.GetResult(0)->VID, count - 2);
    BOOST_CHECK_EQUAL(filtered.GetResult(1)->VID, -1);
    BOOST_CHECK_EQUAL(heads->m_distanceCalls, count + 1);
    BOOST_CHECK_EQUAL(std::accumulate(checks.begin(), checks.end(), 0), 65);
    for (int checked : checks) BOOST_CHECK_LE(checked, 1);

    COMMON::QueryResultSet<float> rejected(&target, 2);
    heads->m_distanceCalls = 0;
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchyForPlacement(
        &target, 2, [](SizeType) { return false; }, rejected,
        heads, indexes, catalogs, offsets, members) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(rejected.GetResult(0)->VID, -1);
    BOOST_CHECK_EQUAL(heads->m_distanceCalls, count);

    auto h2 = std::make_shared<HierarchyTestIndex>(values);
    COMMON::QueryResultSet<float> twoLevels(&target, 2);
    heads->m_distanceCalls = 0;
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchyForPlacement(
        &target, 2, nullptr, twoLevels, heads, {h2}, {catalogs.front()},
        {offsets.front()}, {members.front()}) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(twoLevels.GetResult(0)->VID, count - 1);
    BOOST_CHECK_EQUAL(twoLevels.GetResult(1)->VID, count - 2);
    BOOST_CHECK_EQUAL(heads->m_distanceCalls, 65U);
    BOOST_CHECK_EQUAL(twoLevels.GetScanned(), 129);

    auto invalidMembers = members;
    invalidMembers.back().back() = count;
    COMMON::QueryResultSet<float> replay(&target, 2);
    BOOST_CHECK(SPANN::SearchSecondLevelHierarchyForPlacement(
        &target, 2, nullptr, replay, heads, indexes, catalogs,
        offsets, invalidMembers) == ErrorCode::Fail);
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchyForPlacement(
        &target, 2, nullptr, replay, heads, indexes, catalogs,
        offsets, members) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(replay.GetResult(0)->VID, count - 1);
    BOOST_CHECK_EQUAL(replay.GetResult(1)->VID, count - 2);
}

BOOST_AUTO_TEST_CASE(HierarchyMembershipAppliesAfterDistanceScoring)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    Signature query, other, both;
    query.Insert(7);
    other.Insert(8);
    both.MergeOR(query);
    both.MergeOR(other);
    BOOST_REQUIRE(!other.MayIntersect(query));
    auto heads = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1, 2, 3});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{0});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {nullptr, top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {
        HierarchyTestCatalog({0, 1, 2, 3}), HierarchyTestCatalog({0})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(4, 1, {0, 1, 2, 3}, {0, 1, 2, 3, 4},
            {0, 1, 2, 3}, {other, other, query, query}),
        HierarchyTestPostings(4, 1, {0}, {0, 4}, {0, 1, 2, 3}, {both})};
    const float target = 0;
    COMMON::QueryResultSet<float> results(&target, 2);
    SPANN::SecondLevelHierarchySearchStats stats;
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        results, 2, 64, 1.0, query, [](SizeType p_id) { return p_id >= 2; },
        heads, indexes, catalogs, postings, stats) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(heads->m_distanceCalls, 6U);
    BOOST_CHECK_EQUAL(stats.m_layerCandidates[1], 4U);
    BOOST_CHECK_EQUAL(stats.m_layerEligible[1], 4U);
    BOOST_CHECK_EQUAL(stats.m_layerDistances[0], 2U);
    BOOST_CHECK_EQUAL(results.GetResult(0)->VID, -1);
    BOOST_CHECK_EQUAL(results.GetResult(1)->VID, -1);
}

BOOST_AUTO_TEST_CASE(HierarchySignaturesNeverAffectTraversal)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    Signature query, other, both;
    query.Insert(7);
    other.Insert(9);
    both.MergeOR(query);
    both.MergeOR(other);
    auto heads = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1, 2, 3, 4, 5});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 2, 4});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {nullptr, top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {
        HierarchyTestCatalog({0, 2, 4}), HierarchyTestCatalog({0, 2, 4})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(6, 1, {0, 2, 4}, {0, 2, 4, 6},
            {0, 1, 2, 3, 4, 5}, {other, query, query}),
        HierarchyTestPostings(3, 2, {0, 1, 2}, {0, 1, 4, 6},
            {0, 0, 1, 2, 1, 2}, {other, both, query})};
    const float target = 0;
    SPANN::SecondLevelHierarchyDetail::SearchWorkspace workspace;
    for (bool graphPruning : {false, true})
    {
        COMMON::QueryResultSet<float> results(&target, 3);
        SPANN::SecondLevelHierarchySearchStats stats;
        BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
            results, 3, 64, 1.0, query, [](SizeType) { return true; },
            heads, indexes, catalogs, postings, stats, nullptr, false, nullptr,
            nullptr, &workspace, false, graphPruning) == ErrorCode::Success);
        BOOST_CHECK_EQUAL(stats.m_layerEligible[2], 3U);
        BOOST_CHECK_EQUAL(stats.m_layerCandidates[1], 3U);
        BOOST_CHECK_EQUAL(stats.m_layerEligible[1], 3U);
        for (int rank = 0; rank < 3; ++rank)
            BOOST_CHECK_EQUAL(results.GetResult(rank)->VID, rank);
    }
    BOOST_CHECK_EQUAL(top->m_resultFilterCalls, 0U);
    COMMON::QueryResultSet<float> unfiltered(&target, 3);
    SPANN::SecondLevelHierarchySearchStats stats;
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        unfiltered, 3, 64, 1.0, Signature(), [](SizeType) { return true; },
        heads, indexes, catalogs, postings, stats, nullptr, false, nullptr,
        nullptr, &workspace, false, true) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(top->m_resultFilterCalls, 0U);
    BOOST_CHECK_EQUAL(stats.m_layerEligible[2], 3U);
    BOOST_CHECK_EQUAL(stats.m_layerEligible[1], 3U);
}

BOOST_AUTO_TEST_CASE(HierarchyTopProbeNeverExceedsLayerBudget)
{
    auto heads = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1, 2, 3});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1, 2, 3});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {nullptr, top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {
        HierarchyTestCatalog({0, 1, 2, 3}), HierarchyTestCatalog({0, 1, 2, 3})};
    const auto layer = HierarchyTestPostings(
        4, 1, {0, 1, 2, 3}, {0, 1, 2, 3, 4}, {0, 1, 2, 3},
        std::vector<SPANN::SecondLevelHeadPostings::Signature>(4));
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {layer, layer};
    const float target = 0;
    COMMON::QueryResultSet<float> results(&target, 2);
    SPANN::SecondLevelHierarchySearchStats stats;
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        results, 2, 64, 1.0, Cache::PostingBitmask(), [](SizeType) { return false; },
        heads, indexes, catalogs, postings, stats) == ErrorCode::Success);
    BOOST_REQUIRE(!top->m_probes.empty());
    for (int probe : top->m_probes) BOOST_CHECK_LE(probe, 2);
    BOOST_CHECK_LE(stats.m_topProbe, 2);
    BOOST_CHECK(stats.m_layerTimes.empty());
}

BOOST_AUTO_TEST_CASE(HierarchyHighestLayerUsesUnfilteredNativeBudget)
{
    class BoundedTop : public HierarchyTestIndex
    {
    public:
        BoundedTop() : HierarchyTestIndex({0, 1, 2, 3}) {}
        ErrorCode SearchIndexWithResultFilter(
            QueryResult& results, std::function<bool(SizeType)> admission,
            int maxCheck, bool = false) const override
        {
            ++calls;
            m_probes.push_back(results.GetResultNum());
            results.Reset();
            auto& heap = static_cast<COMMON::QueryResultSet<float>&>(results);
            int checked = 0;
            for (SizeType id = 0; id < GetNumSamples() && checked < maxCheck; ++id)
            {
                const float distance = ComputeDistance(results.GetTarget(), GetSample(id));
                ++checked;
                if (admission(id)) heap.AddPoint(id, distance);
            }
            heap.SortResult();
            results.SetScanned(checked);
            finished = true;
            return ErrorCode::Success;
        }
        mutable int calls = 0;
        mutable bool finished = false;
    };
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    Signature query, other;
    query.Insert(7);
    other.Insert(9);
    BOOST_REQUIRE(!other.MayIntersect(query));
    for (int levels : {1, 4})
    {
        for (int maxCheck : {2, 3, 4})
        {
            auto top = std::make_shared<BoundedTop>();
            auto heads = std::make_shared<HierarchyTestIndex>(std::vector<float>{100, 50, 4, 3});
            std::vector<std::shared_ptr<VectorIndex>> indexes(static_cast<size_t>(levels));
            indexes.back() = top;
            std::vector<std::shared_ptr<VectorSet>> catalogs(
                static_cast<size_t>(levels), HierarchyTestCatalog({0, 1, 2, 3}));
            const auto layer = HierarchyTestPostings(
                4, 1, {0, 1, 2, 3}, {0, 1, 2, 3, 4}, {0, 1, 2, 3},
                {other, other, query, query});
            std::vector<SPANN::SecondLevelHeadPostings> postings(static_cast<size_t>(levels), layer);
            const float target = 0;
            COMMON::QueryResultSet<float> raw(&target, 2), results(&target, 2);
            BOOST_REQUIRE(top->SearchIndexWithMaxCheck(raw, maxCheck) == ErrorCode::Success);
            BOOST_CHECK_EQUAL(raw.GetResult(0)->VID, 0);
            BOOST_CHECK_EQUAL(raw.GetResult(1)->VID, 1);
            top->m_probes.clear();
            top->m_distanceCalls = 0;
            SPANN::SecondLevelHierarchySearchStats stats;
            std::string workLog;
            std::array<int, 4> pointVisits{};
            BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
                results, 2, maxCheck, 0.5, query, [](SizeType) { return true; },
                heads, indexes, catalogs, postings, stats, &workLog, false, nullptr,
                [&](SizeType head, const float* distance) {
                    BOOST_REQUIRE(distance != nullptr);
                    ++pointVisits[static_cast<size_t>(head)];
                    BOOST_CHECK_EQUAL(*distance, 10000.0f);
                    return false;
                }) == ErrorCode::Success);
            BOOST_CHECK_EQUAL(top->calls, 0);
            BOOST_CHECK(!top->finished);
            BOOST_REQUIRE_EQUAL(top->m_probes.size(), 1U);
            BOOST_CHECK_EQUAL(top->m_probes.front(), 2);
            BOOST_CHECK_EQUAL(top->m_distanceCalls, 4U);
            BOOST_CHECK_EQUAL(stats.m_graphScanned, 1U);
            BOOST_CHECK_LE(stats.m_layerCandidates.back(), 2U);
            BOOST_CHECK_EQUAL(pointVisits[0], 1);
            BOOST_CHECK_EQUAL(pointVisits[1], 0);
            BOOST_CHECK_EQUAL(pointVisits[2], 0);
            BOOST_CHECK_EQUAL(pointVisits[3], 0);
            BOOST_CHECK_EQUAL(results.GetResult(0)->VID, 0);
            BOOST_CHECK_EQUAL(results.GetResult(1)->VID, -1);
            BOOST_CHECK_NE(workLog.find("level=H" + std::to_string(levels + 1)), std::string::npos);
            BOOST_CHECK_EQUAL(workLog.find("graph_result_filter"), std::string::npos);
        }
    }
}

BOOST_AUTO_TEST_CASE(HierarchyStoredSignaturesDoNotSkipDistances)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    Signature query;
    query.Insert(7);
    auto heads = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {HierarchyTestCatalog({0, 1})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(2, 1, {0, 1}, {0, 1, 2}, {0, 1}, {Signature(), query})};
    const float target = 0;
    COMMON::QueryResultSet<float> results(&target, 2);
    SPANN::SecondLevelHierarchySearchStats stats;
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        results, 2, 64, 1.0, query, [](SizeType p_id) { return p_id == 1; },
        heads, indexes, catalogs, postings, stats) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(stats.m_uniqueScanned, 2U);
    BOOST_CHECK_EQUAL(results.GetResult(0)->VID, 1);
    BOOST_CHECK_EQUAL(results.GetResult(1)->VID, -1);
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        results, 2, 64, 1.0, Signature(), [](SizeType) { return true; },
        heads, indexes, catalogs, postings, stats) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(stats.m_uniqueScanned, 2U);
    BOOST_CHECK_EQUAL(results.GetResult(0)->VID, 0);
    BOOST_CHECK_EQUAL(results.GetResult(1)->VID, 1);
}

BOOST_AUTO_TEST_CASE(HierarchyStoredSignaturesDoNotAffectTraversal)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    Signature query, other;
    query.Insert(7);
    other.Insert(9);
    auto heads = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1, 2});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1, 2});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {
        HierarchyTestCatalog({0, 1, 2})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(
            3, 1, {0, 1, 2}, {0, 1, 2, 3}, {0, 1, 2},
            {Signature(), query, other})};
    const float target = 0;
    for (const Signature& ignoredSignature : {Signature(), query, other})
    {
        COMMON::QueryResultSet<float> results(&target, 3);
        SPANN::SecondLevelHierarchySearchStats stats;
        top->m_probes.clear();
        heads->m_distanceCalls = 0;
        BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
            results, 3, 64, 1.0, ignoredSignature,
            [](SizeType head) { return head == 2; },
            heads, indexes, catalogs, postings, stats) == ErrorCode::Success);
        BOOST_REQUIRE_EQUAL(top->m_probes.size(), 1U);
        BOOST_CHECK_EQUAL(top->m_resultFilterCalls, 0U);
        BOOST_CHECK_EQUAL(stats.m_layerCandidates.back(), 3U);
        BOOST_CHECK_EQUAL(stats.m_layerDistances.front(), 3U);
        BOOST_CHECK_EQUAL(heads->m_distanceCalls, 3U);
        BOOST_CHECK_EQUAL(results.GetResult(0)->VID, 2);
        BOOST_CHECK_EQUAL(results.GetResult(1)->VID, -1);
    }
}

BOOST_AUTO_TEST_CASE(HierarchyDeduplicatesAndRanksAllChildren)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    auto heads = std::make_shared<HierarchyTestIndex>(
        std::vector<float>{100, 100, 50, 50, 1, -1});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{1});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {nullptr, top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {
        HierarchyTestCatalog({100, 50, 1}), HierarchyTestCatalog({1})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(6, 2, {0, 2, 4}, {0, 4, 8, 12},
            {0, 1, 4, 5, 0, 1, 2, 3, 2, 3, 4, 5}, std::vector<Signature>(3)),
        HierarchyTestPostings(3, 1, {2}, {0, 3}, {0, 1, 2}, std::vector<Signature>(1))};
    for (float target : {0.0f, 101.0f})
    {
        COMMON::QueryResultSet<float> results(&target, 3);
        SPANN::SecondLevelHierarchySearchStats stats;
        std::string workLog;
        heads->m_distanceCalls = 0;
        BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
            results, 3, 64, 1.0, Cache::PostingBitmask(), [](SizeType) { return true; },
            heads, indexes, catalogs, postings, stats, &workLog, true) == ErrorCode::Success);
        BOOST_CHECK_EQUAL(heads->m_distanceCalls, 9U);
        BOOST_CHECK_EQUAL(results.GetResult(0)->VID, target == 0.0f ? 4 : 0);
        BOOST_CHECK_EQUAL(results.GetResult(1)->VID, target == 0.0f ? 5 : 1);
        BOOST_CHECK_EQUAL(results.GetResult(2)->VID, 2);
        BOOST_REQUIRE_EQUAL(stats.m_layerTimes.size(), 3U);
        SPANN::SecondLevelHierarchyLayerTimes sum;
        for (const auto& times : stats.m_layerTimes)
        {
            sum.m_graphMs += times.m_graphMs;
            sum.m_mergeMs += times.m_mergeMs;
            sum.m_tagMs += times.m_tagMs;
            sum.m_vectorMs += times.m_vectorMs;
            sum.m_sortMs += times.m_sortMs;
        }
        BOOST_CHECK_SMALL(sum.m_graphMs - stats.m_graphMs, 1e-9);
        BOOST_CHECK_SMALL(sum.m_mergeMs - stats.m_mergeMs, 1e-9);
        BOOST_CHECK_SMALL(sum.m_tagMs - stats.m_tagMs, 1e-9);
        BOOST_CHECK_SMALL(sum.m_vectorMs - stats.m_vectorMs, 1e-9);
        BOOST_CHECK_SMALL(sum.m_sortMs - stats.m_sortMs, 1e-9);
        BOOST_CHECK(workLog.find("level=H3") != std::string::npos);
        BOOST_CHECK(workLog.find("graph_ms=") != std::string::npos);
    }
}

BOOST_AUTO_TEST_CASE(HierarchyVisitedBitmapResetsTouchedWords)
{
    COMMON::VisitedBitmap workspace;
    const std::array<SizeType, 8> ids = {0, 1, 63, 64, 65, 127, 128, 129};
    workspace.ResetSeen(130);
    for (int repeat = 0; repeat < 3; ++repeat)
    {
        for (SizeType id : ids)
        {
            BOOST_CHECK(!workspace.CheckAndSet(id));
            BOOST_CHECK(workspace.CheckAndSet(id));
        }
        workspace.ResetSeen(2);
        BOOST_CHECK(!workspace.CheckAndSet(0));
        BOOST_CHECK(!workspace.CheckAndSet(1));
        workspace.ResetSeen(130);
    }
    workspace.ResetSeen(260);
    BOOST_CHECK(!workspace.CheckAndSet(259));
    workspace.ResetSeen(1);
    BOOST_CHECK(!workspace.CheckAndSet(0));
    workspace.ResetSeen(260);
    BOOST_CHECK(!workspace.CheckAndSet(259));
}

BOOST_AUTO_TEST_CASE(HierarchyLayerVisitedStateIsLocalAndReusable)
{
    constexpr SizeType count = 73;
    SPANN::SecondLevelHierarchyDetail::SearchWorkspace hierarchy;
    hierarchy.m_layers.resize(3);
    COMMON::WorkSpace graph;
    graph.Initialize(128, 2);
    const std::array<SizeType, 5> ids = {0, 1, 63, 64, 72};
    for (int repeat = 0; repeat < 3; ++repeat)
    {
        for (auto& layer : hierarchy.m_layers) layer.Reset(count);
        graph.Reset(128, 8);
        graph.PrepareResultCheckStatus();
        for (SizeType id : ids)
        {
            for (auto& layer : hierarchy.m_layers)
            {
                BOOST_CHECK(!layer.m_seen.CheckAndSet(id));
                BOOST_CHECK(layer.m_seen.CheckAndSet(id));
            }
            BOOST_CHECK(!graph.CheckAndSet(id));
            BOOST_CHECK(graph.CheckAndSet(id));
            BOOST_CHECK(graph.Contains(id));
            BOOST_CHECK(!graph.CheckResultAndSet(id));
            BOOST_CHECK(graph.CheckResultAndSet(id));
        }
        hierarchy.m_layers[1].Reset(2);
        BOOST_CHECK(!hierarchy.m_layers[1].m_seen.CheckAndSet(0));
        BOOST_CHECK(!hierarchy.m_layers[1].m_seen.CheckAndSet(1));
        for (SizeType id : ids)
        {
            BOOST_CHECK(hierarchy.m_layers[0].m_seen.Contains(id));
            BOOST_CHECK(hierarchy.m_layers[2].m_seen.Contains(id));
            BOOST_CHECK(graph.CheckAndSet(id));
        }
        graph.PrepareResultCheckStatus();
        for (SizeType id : ids)
        {
            BOOST_CHECK(graph.Contains(id));
            BOOST_CHECK(!graph.CheckResultAndSet(id));
        }
        for (auto& layer : hierarchy.m_layers)
        {
            layer.m_frontier.reserve(16);
            layer.m_selected.reserve(8);
            const auto frontierCapacity = layer.m_frontier.capacity();
            const auto selectedCapacity = layer.m_selected.capacity();
            layer.Push(1, 1);
            layer.m_selected.push_back(1);
            layer.m_nextParent = 1;
            layer.Reset(count);
            BOOST_CHECK_EQUAL(layer.m_frontier.capacity(), frontierCapacity);
            BOOST_CHECK_EQUAL(layer.m_selected.capacity(), selectedCapacity);
            BOOST_CHECK(layer.m_frontier.empty());
            BOOST_CHECK(layer.m_selected.empty());
            BOOST_CHECK_EQUAL(layer.m_nextParent, 0U);
            for (SizeType id : ids) BOOST_CHECK(!layer.m_seen.Contains(id));
        }
    }
}

BOOST_AUTO_TEST_CASE(HierarchyWorkspaceReusesChangingLayerDomains)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    SPANN::SecondLevelHierarchyDetail::SearchWorkspace workspace;
    for (SizeType count : {130, 2, 260, 3})
    {
        std::vector<float> values(static_cast<size_t>(count));
        std::iota(values.begin(), values.end(), 0.0f);
        auto heads = std::make_shared<HierarchyTestIndex>(values);
        std::vector<std::shared_ptr<VectorSet>> catalogs = {
            HierarchyTestCatalog({0, static_cast<float>(count - 1)})};
        std::vector<std::shared_ptr<VectorIndex>> indexes = {
            std::make_shared<HierarchyTestIndex>(std::vector<float>{0, static_cast<float>(count - 1)})};
        std::vector<SPANN::SecondLevelHeadPostings::Member> members(static_cast<size_t>(count));
        std::iota(members.begin(), members.end(), 0);
        std::vector<SPANN::SecondLevelHeadPostings> postings = {
            HierarchyTestPostings(count, 1, {0, static_cast<std::uint64_t>(count - 1)},
                {0, static_cast<std::uint64_t>(count / 2), static_cast<std::uint64_t>(count)},
                members, std::vector<Signature>(2))};
        if (count > 3)
        {
            indexes.front() = nullptr;
            indexes.push_back(std::make_shared<HierarchyTestIndex>(std::vector<float>{0}));
            catalogs.push_back(HierarchyTestCatalog({0}));
            postings.push_back(HierarchyTestPostings(2, 1, {0}, {0, 2}, {0, 1}, {Signature()}));
        }
        const float target = static_cast<float>(count - 1);
        COMMON::QueryResultSet<float> results(&target, 2);
        SPANN::SecondLevelHierarchySearchStats stats;
        std::vector<int> admissions(static_cast<size_t>(count), 0);
        BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
            results, 2, 64, 1.0, Signature(), [](SizeType) { return true; },
            heads, indexes, catalogs, postings, stats, nullptr, false, nullptr,
            [&](SizeType id, const float* distance) {
                BOOST_REQUIRE(distance != nullptr);
                ++admissions[static_cast<size_t>(id)];
                return false;
            },
            &workspace) == ErrorCode::Success);
        BOOST_REQUIRE_EQUAL(workspace.m_layers.size(), postings.size() + 1);
        BOOST_CHECK_EQUAL(results.GetResult(0)->VID, count - 1);
        BOOST_CHECK_EQUAL(results.GetResult(1)->VID, count - 2);
        for (int admission : admissions) BOOST_CHECK_EQUAL(admission, 1);
        for (size_t layer = 1; layer < workspace.m_layers.size(); ++layer)
        {
            const auto& state = workspace.m_layers[layer];
            BOOST_CHECK_EQUAL(state.m_nextParent, state.m_selected.size());
            BOOST_CHECK_LE(state.m_selected.size(), 2U);
            for (const auto& candidate : state.m_frontier)
                BOOST_CHECK(std::find(state.m_selected.begin(), state.m_selected.end(),
                    candidate.second) == state.m_selected.end());
        }
    }
}

BOOST_AUTO_TEST_CASE(HierarchyScoresLayersIndependentlyAndOnlyAdmitsH1Points)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    auto heads = std::make_shared<HierarchyTestIndex>(
        std::vector<float>{100, 100, 50, 50, 1, -1});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{1});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {nullptr, top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {
        HierarchyTestCatalog({100, 50, 1}), HierarchyTestCatalog({1})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(6, 2, {0, 2, 4}, {0, 4, 8, 12},
            {0, 1, 4, 5, 0, 1, 2, 3, 2, 3, 4, 5}, std::vector<Signature>(3)),
        HierarchyTestPostings(3, 1, {2}, {0, 3}, {0, 1, 2}, {Signature()})};
    SPANN::SecondLevelHierarchyDetail::SearchWorkspace workspace;
    for (float target : {0.0f, 101.0f})
    {
        std::array<int, 6> pointVisits{};
        COMMON::QueryResultSet<float> results(&target, 3);
        COMMON::QueryResultSet<float> acceptedPoints(&target, 2);
        SPANN::SecondLevelHierarchySearchStats stats;
        heads->m_distanceCalls = 0;
        BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
            results, 3, 64, 1.0, Signature(), [](SizeType) { return true; },
            heads, indexes, catalogs, postings, stats, nullptr, false, nullptr,
            [&](SizeType head, const float* distance) {
                BOOST_REQUIRE(distance != nullptr);
                BOOST_REQUIRE_GE(head, 0);
                BOOST_REQUIRE_LT(head, 6);
                ++pointVisits[static_cast<size_t>(head)];
                const float delta = target - *static_cast<const float*>(heads->GetSample(head));
                BOOST_CHECK_EQUAL(*distance, delta * delta);
                if (head != 4) acceptedPoints.AddPoint(head, *distance);
                return false;
            }, &workspace) == ErrorCode::Success);
        BOOST_CHECK_EQUAL(heads->m_distanceCalls, 9U);
        for (int visits : pointVisits) BOOST_CHECK_EQUAL(visits, 1);
        BOOST_CHECK_EQUAL(stats.m_layerDistances[1], 3U);
        BOOST_CHECK_EQUAL(stats.m_layerDistances[0], 6U);
        BOOST_CHECK_EQUAL(results.GetResult(0)->VID, target == 0.0f ? 4 : 0);
        BOOST_CHECK_EQUAL(results.GetResult(1)->VID, target == 0.0f ? 5 : 1);
        BOOST_CHECK_EQUAL(results.GetResult(2)->VID, 2);
        acceptedPoints.SortResult();
        BOOST_CHECK_EQUAL(acceptedPoints.GetResult(0)->VID, target == 0.0f ? 5 : 0);
    }
}

BOOST_AUTO_TEST_CASE(HierarchyFixedBudgetDoesNotWidenSavedFrontier)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    auto heads = std::make_shared<HierarchyTestIndex>(
        std::vector<float>{0, 0.1f, 1, 1.1f});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {nullptr, top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {
        HierarchyTestCatalog({0, 1}), HierarchyTestCatalog({0, 1})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(4, 1, {0, 2}, {0, 2, 4},
            {0, 1, 2, 3}, std::vector<Signature>(2)),
        HierarchyTestPostings(2, 1, {0, 1}, {0, 1, 2},
            {0, 1}, std::vector<Signature>(2))};
    const float target = 0;
    COMMON::QueryResultSet<float> results(&target, 2);
    SPANN::SecondLevelHierarchySearchStats stats;
    std::array<int, 4> admissionCalls{};
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        results, 2, 1, 0.5, Signature(),
        [&](SizeType id) { ++admissionCalls[static_cast<size_t>(id)]; return id % 2 == 0; },
        heads, indexes, catalogs, postings, stats) == ErrorCode::Success);
    BOOST_REQUIRE_EQUAL(top->m_probes.size(), 1U);
    BOOST_CHECK_EQUAL(stats.m_graphScanned, 1U);
    BOOST_CHECK_EQUAL(stats.m_iterations, 1);
    BOOST_CHECK_EQUAL(stats.m_layerAssignments[0], 2U);
    BOOST_CHECK_EQUAL(stats.m_layerAssignments[1], 1U);
    BOOST_CHECK_EQUAL(admissionCalls[0], 1);
    BOOST_CHECK_EQUAL(admissionCalls[1], 1);
    BOOST_CHECK_EQUAL(admissionCalls[2], 0);
    BOOST_CHECK_EQUAL(admissionCalls[3], 0);
    for (auto retained : stats.m_layerRetained) BOOST_CHECK_LE(retained, 2U);
    BOOST_CHECK_EQUAL(results.GetResult(0)->VID, 0);
    BOOST_CHECK_EQUAL(results.GetResult(1)->VID, -1);
}

BOOST_AUTO_TEST_CASE(HierarchyUpperPointsNeverEnterFinalCandidates)
{
    class ScoredTopIndex : public HierarchyTestIndex
    {
    public:
        ScoredTopIndex() : HierarchyTestIndex({0, 10}) {}
        ErrorCode SearchIndexWithMaxCheck(
            QueryResult& results, int maxCheck, bool = false) const override
        {
            if (maxCheck <= 0) return ErrorCode::Fail;
            ComputeDistance(results.GetTarget(), GetSample(0));
            const float returnedDistance = ComputeDistance(results.GetTarget(), GetSample(1));
            results.Reset();
            results.SetResult(0, 1, returnedDistance);
            results.SetScanned(1);
            return ErrorCode::Success;
        }
    };
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    auto heads = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1, 10});
    auto top = std::make_shared<ScoredTopIndex>();
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {HierarchyTestCatalog({0, 10})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(3, 1, {0, 2}, {0, 2, 3}, {0, 1, 2},
            std::vector<Signature>(2))};
    const float target = 0;
    COMMON::QueryResultSet<float> routing(&target, 1), points(&target, 1);
    SPANN::SecondLevelHierarchySearchStats stats;
    std::array<int, 3> pointVisits{};
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        routing, 1, 1, 1.0, Signature(), [](SizeType) { return true; },
        heads, indexes, catalogs, postings, stats, nullptr, false, nullptr,
        [&](SizeType head, const float* distance) {
            BOOST_REQUIRE(distance != nullptr);
            ++pointVisits[static_cast<size_t>(head)];
            points.AddPoint(head, *distance);
            return false;
        }) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(routing.GetResult(0)->VID, 2);
    BOOST_CHECK_EQUAL(points.GetResult(0)->VID, 2);
    BOOST_CHECK_EQUAL(pointVisits[0], 0);
    BOOST_CHECK_EQUAL(pointVisits[1], 0);
    BOOST_CHECK_EQUAL(pointVisits[2], 1);
    BOOST_CHECK_EQUAL(heads->m_distanceCalls, 1U);
    BOOST_CHECK_EQUAL(top->m_distanceCalls, 2U);
    BOOST_CHECK_EQUAL(stats.m_layerCandidates.back(), 1U);
    BOOST_CHECK_EQUAL(stats.m_layerAssignments.front(), 1U);
}

BOOST_AUTO_TEST_CASE(HierarchyH1PointAndPostingAdmissionStayIndependent)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    auto heads = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1, 2, 3});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {HierarchyTestCatalog({0, 1})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(4, 2, {0, 1}, {0, 4, 8}, {0, 1, 2, 3, 0, 1, 2, 3},
            std::vector<Signature>(2))};
    const float target = 0;
    COMMON::QueryResultSet<float> routing(&target, 2), points(&target, 2);
    SPANN::SecondLevelHierarchySearchStats stats;
    std::array<int, 4> pointVisits{};
    const auto admit = [&](SizeType head, const float* knownDistance) {
        ++pointVisits[static_cast<size_t>(head)];
        BOOST_REQUIRE(knownDistance != nullptr);
        if (head >= 2) return false;
        points.AddPoint(head, *knownDistance);
        return false;
    };
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        routing, 2, 64, 1.0, Signature(),
        [](SizeType head) { return head == 1 || head == 2; },
        heads, indexes, catalogs, postings, stats, nullptr, false, nullptr,
        admit) == ErrorCode::Success);
    points.SortResult();
    BOOST_CHECK_EQUAL(routing.GetResult(0)->VID, 1);
    BOOST_CHECK_EQUAL(routing.GetResult(1)->VID, 2);
    BOOST_CHECK_EQUAL(points.GetResult(0)->VID, 0);
    BOOST_CHECK_EQUAL(points.GetResult(1)->VID, 1);
    for (int visits : pointVisits) BOOST_CHECK_EQUAL(visits, 1);
    BOOST_CHECK_EQUAL(heads->m_distanceCalls, 4U);
    BOOST_CHECK_EQUAL(stats.m_layerDistances.front(), 4U);
    BOOST_CHECK_EQUAL(stats.m_layerAssignments.front(), 8U);
    BOOST_CHECK_EQUAL(stats.m_layerEligible.front(), 2U);

    SPANN::LimitedTagSupport support;
    BOOST_REQUIRE(support.Initialize(4, 2, 1, 0, 1, 71));
    for (SizeType head = 0; head < 4; ++head)
    {
        const std::uint32_t own = head < 2 ? 7 : 9;
        BOOST_REQUIRE(support.SetHeadAttributes(head, &own, 1));
        BOOST_REQUIRE(support.SetHeadTags(head, {own}));
    }
    Signature querySignature, descendants;
    querySignature.Insert(7);
    descendants.Insert(7);
    descendants.Insert(9);
    const std::vector<SPANN::SecondLevelHeadPostings> filteredPostings = {
        HierarchyTestPostings(4, 2, {0, 1}, {0, 4, 8}, {0, 1, 2, 3, 0, 1, 2, 3},
            std::vector<Signature>(2, descendants))};
    SPANN::SecondLevelHierarchyDetail::SearchWorkspace workspace;
    routing.Reset();
    points.Reset();
    pointVisits.fill(0);
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        routing, 2, 64, 1.0, querySignature,
        [](SizeType head) { return head == 1 || head == 2; },
        heads, indexes, catalogs, filteredPostings, stats, nullptr, false, &support,
        admit, &workspace) == ErrorCode::Success);
    points.SortResult();
    BOOST_CHECK_EQUAL(points.GetResult(0)->VID, 0);
    BOOST_CHECK_EQUAL(points.GetResult(1)->VID, 1);
    BOOST_CHECK_EQUAL(routing.GetResult(0)->VID, 1);
    BOOST_CHECK_EQUAL(routing.GetResult(1)->VID, 2);
    BOOST_CHECK_EQUAL(pointVisits[0], 1);
    BOOST_CHECK_EQUAL(pointVisits[1], 1);
    BOOST_CHECK_EQUAL(pointVisits[2], 1);
    BOOST_CHECK_EQUAL(pointVisits[3], 1);
    BOOST_CHECK(workspace.m_layers.front().m_seen.CheckAndSet(3));
    BOOST_CHECK_EQUAL(stats.m_layerDistances.front(), 4U);
}

BOOST_AUTO_TEST_CASE(HierarchyUnderfilledMembershipDoesNotWiden)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    auto heads = std::make_shared<HierarchyTestIndex>(
        std::vector<float>{0, 0.1f, 1, 1.1f, 0.01f, 2.1f});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1, 2});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {HierarchyTestCatalog({0, 1, 2})};
    Signature query;
    query.Insert(7);
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(6, 1, {0, 2, 4}, {0, 2, 4, 6}, {0, 1, 2, 3, 4, 5},
            std::vector<Signature>(3, query))};
    SPANN::LimitedTagSupport support;
    BOOST_REQUIRE(support.Initialize(6, 2, 1, 0, 1, 71));
    for (SizeType head = 0; head < 6; ++head)
    {
        const std::uint32_t tag = head == 2 || head == 4 ? 7 : 9;
        BOOST_REQUIRE(support.SetHeadAttributes(head, &tag, 1));
        BOOST_REQUIRE(support.SetHeadTags(head, {tag}));
    }
    BOOST_REQUIRE(support.SetTagVectorCounts(6, {{7, 2}, {9, 4}}));
    BOOST_REQUIRE(support.Finalize());
    BOOST_REQUIRE(support.TagHeads().at(7) == std::vector<SizeType>({2, 4}));
    const float target = 0;
    for (bool allFiltered : {false, true})
    {
        COMMON::QueryResultSet<float> results(&target, 2), points(&target, 2);
        SPANN::SecondLevelHierarchySearchStats stats;
        SPANN::SecondLevelHierarchyDetail::SearchWorkspace workspace;
        std::array<int, 6> pointVisits{};
        std::array<int, 6> admissionCalls{};
        top->m_probes.clear();
        heads->m_distanceCalls = 0;
        BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
            results, 2, 1, 0.5, query,
            [&](SizeType head) {
                ++admissionCalls[static_cast<size_t>(head)];
                return !allFiltered && support.Supports(head, 7);
            },
            heads, indexes, catalogs, postings, stats, nullptr, false, &support,
            [&](SizeType head, const float* distance) {
                ++pointVisits[static_cast<size_t>(head)];
                if (!allFiltered &&
                    support.Supports(head, 7))
                {
                    BOOST_REQUIRE(distance != nullptr);
                    points.AddPoint(head, *distance);
                }
                return false;
            }, &workspace) == ErrorCode::Success);
        points.SortResult();
        BOOST_CHECK_EQUAL(top->m_probes.size(), 1U);
        BOOST_CHECK_EQUAL(stats.m_iterations, 1);
        BOOST_CHECK_EQUAL(stats.m_graphScanned, 1U);
        BOOST_CHECK_EQUAL(stats.m_uniqueScanned, 2U);
        BOOST_CHECK_EQUAL(stats.m_layerAssignments.front(), 2U);
        BOOST_CHECK_EQUAL(stats.m_layerBudgets.back(), 1U);
        BOOST_CHECK_EQUAL(stats.m_layerDistances.front(), 2U);
        BOOST_CHECK_EQUAL(heads->m_distanceCalls, 2U);
        BOOST_CHECK_EQUAL(admissionCalls[0], 1);
        BOOST_CHECK_EQUAL(admissionCalls[1], 1);
        BOOST_CHECK_EQUAL(admissionCalls[2], 0);
        BOOST_CHECK_EQUAL(admissionCalls[3], 0);
        BOOST_CHECK_EQUAL(admissionCalls[4], 0);
        BOOST_CHECK_EQUAL(pointVisits[0], 1);
        BOOST_CHECK_EQUAL(pointVisits[1], 1);
        BOOST_CHECK_EQUAL(pointVisits[2], 0);
        BOOST_CHECK_EQUAL(pointVisits[4], 0);
        BOOST_CHECK_EQUAL(results.GetResult(0)->VID, -1);
        BOOST_CHECK_EQUAL(results.GetResult(1)->VID, -1);
        BOOST_CHECK_EQUAL(points.GetResult(0)->VID, -1);
        BOOST_CHECK_EQUAL(points.GetResult(1)->VID, -1);
    }
}

BOOST_AUTO_TEST_CASE(HierarchyFixedBeamDoesNotRefillFilteredResults)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    Signature signature;
    signature.Insert(7);
    auto heads = std::make_shared<HierarchyTestIndex>(
        std::vector<float>{0, 10, 1, 2, -0.05f, 3});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{0, 1});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {nullptr, top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {
        HierarchyTestCatalog({0, 10, 1, 2}), HierarchyTestCatalog({0, 1})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(6, 1, {0, 1, 2, 3}, {0, 1, 3, 4, 6},
            {0, 1, 4, 2, 3, 5}, {Signature(), signature, signature, signature}),
        HierarchyTestPostings(4, 1, {0, 2}, {0, 2, 4},
            {0, 1, 2, 3}, {signature, signature})};
    const float target = 0;
    COMMON::QueryResultSet<float> results(&target, 2);
    SPANN::SecondLevelHierarchySearchStats stats;
    std::array<int, 6> admissionCalls{};
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        results, 2, 1, 0.5, signature,
        [&](SizeType id) {
            ++admissionCalls[static_cast<size_t>(id)];
            return id == 2 || id == 3 || id == 4;
        }, heads, indexes, catalogs, postings, stats) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(stats.m_iterations, 1);
    BOOST_CHECK_EQUAL(top->m_probes.size(), 1U);
    BOOST_CHECK_EQUAL(stats.m_layerRetained[1], 1U);
    BOOST_CHECK_EQUAL(stats.m_layerAssignments[0], 1U);
    BOOST_CHECK_EQUAL(admissionCalls[0], 1);
    BOOST_CHECK_EQUAL(admissionCalls[4], 0);
    BOOST_CHECK_EQUAL(admissionCalls[3], 0);
    BOOST_CHECK_EQUAL(results.GetResult(0)->VID, -1);
    BOOST_CHECK_EQUAL(results.GetResult(1)->VID, -1);
}

BOOST_AUTO_TEST_CASE(BKTBoundedSearchPreservesNativeWorkspaceReuse)
{
    constexpr SizeType count = 64;
    constexpr DimensionType dimension = 4;
    std::vector<float> vectors(static_cast<size_t>(count) * dimension);
    for (SizeType id = 0; id < count; ++id)
        for (DimensionType column = 0; column < dimension; ++column)
            vectors[static_cast<size_t>(id) * dimension + column] =
                static_cast<float>((id * (column + 3) + column * 11) % 67);
    auto index = std::make_shared<BKT::Index<float>>();
    BOOST_REQUIRE(index->SetParameter("DistCalcMethod", "L2") == ErrorCode::Success);
    BOOST_REQUIRE(index->SetParameter("NumberOfThreads", "1") == ErrorCode::Success);
    BOOST_REQUIRE(index->SetParameter("NeighborhoodSize", "16") == ErrorCode::Success);
    BOOST_REQUIRE(index->SetParameter("RefineIterations", "1") == ErrorCode::Success);
    BOOST_REQUIRE(index->BuildIndex(vectors.data(), count, dimension, true, false) ==
        ErrorCode::Success);
    for (SizeType query : {0, 19})
    {
        const float* target = vectors.data() + static_cast<size_t>(query) * dimension;
        COMMON::QueryResultSet<float> plain(target, 8), replay(target, 8);
        COMMON::QueryResultSet<float> filtered(target, 8), filteredReplay(target, 8);
        const auto keepEven = [](SizeType id) { return id % 2 == 0; };
        BOOST_REQUIRE(index->SearchIndexWithMaxCheck(plain, 32) == ErrorCode::Success);
        BOOST_REQUIRE(index->SearchIndexWithResultFilter(filtered, keepEven, 32) == ErrorCode::Success);
        const auto firstIteratorBatch = [&]() {
            auto iterator = index->GetIterator(target, false, nullptr, 32);
            BOOST_REQUIRE(iterator != nullptr);
            auto batch = iterator->Next(8);
            BOOST_REQUIRE(batch != nullptr);
            BOOST_REQUIRE_EQUAL(batch->GetResultNum(), 8);
            std::array<std::pair<SizeType, float>, 8> points;
            for (int rank = 0; rank < 8; ++rank)
                points[static_cast<size_t>(rank)] =
                    {batch->GetResult(rank)->VID, batch->GetResult(rank)->Dist};
            iterator->Close();
            return points;
        };
        const auto iterated = firstIteratorBatch();
        auto workspace = index->RentWorkSpace(4, nullptr, 16);
        workspace->PrepareResultCheckStatus();
        for (SizeType id = 0; id < count; ++id)
        {
            BOOST_CHECK(!workspace->CheckAndSet(id));
            BOOST_CHECK(!workspace->CheckResultAndSet(id));
        }
        BOOST_REQUIRE(index->SearchIndexIterativeEnd(std::move(workspace)) == ErrorCode::Success);
        BOOST_REQUIRE(index->SearchIndexWithMaxCheck(replay, 32) == ErrorCode::Success);
        BOOST_REQUIRE(index->SearchIndexWithResultFilter(
            filteredReplay, keepEven, 32) == ErrorCode::Success);
        const auto iteratedReplay = firstIteratorBatch();
        BOOST_CHECK_EQUAL(plain.GetScanned(), replay.GetScanned());
        BOOST_CHECK_EQUAL(plain.GetScanned(), filtered.GetScanned());
        BOOST_CHECK_EQUAL(filtered.GetScanned(), filteredReplay.GetScanned());
        for (int rank = 0; rank < 8; ++rank)
        {
            BOOST_CHECK_EQUAL(plain.GetResult(rank)->VID, replay.GetResult(rank)->VID);
            BOOST_CHECK_EQUAL(plain.GetResult(rank)->Dist, replay.GetResult(rank)->Dist);
            BOOST_CHECK_EQUAL(filtered.GetResult(rank)->VID, filteredReplay.GetResult(rank)->VID);
            BOOST_CHECK_EQUAL(filtered.GetResult(rank)->Dist, filteredReplay.GetResult(rank)->Dist);
            BOOST_CHECK_EQUAL(iterated[static_cast<size_t>(rank)].first,
                iteratedReplay[static_cast<size_t>(rank)].first);
            BOOST_CHECK_EQUAL(iterated[static_cast<size_t>(rank)].second,
                iteratedReplay[static_cast<size_t>(rank)].second);
        }
    }
}

BOOST_AUTO_TEST_CASE(NativeTopResultAdmissionSharesBudgetWithInitialPivots)
{
    constexpr SizeType count = 64;
    constexpr DimensionType dimension = 4;
    std::vector<float> values(static_cast<size_t>(count) * dimension);
    for (SizeType id = 0; id < count; ++id)
        for (DimensionType column = 0; column < dimension; ++column)
            values[static_cast<size_t>(id) * dimension + column] = id * (column + 1.0f);
    for (IndexAlgoType algorithm : {IndexAlgoType::BKT, IndexAlgoType::KDT})
    {
        auto index = VectorIndex::CreateInstance(algorithm, VectorValueType::Float);
        for (const auto& option : std::vector<std::pair<const char*, const char*>>{
                 {"DistCalcMethod", "L2"}, {"NumberOfThreads", "1"},
                 {"NeighborhoodSize", "8"}, {"RefineIterations", "1"},
                 {"NumberOfInitialDynamicPivots", "64"}, {"NumberOfOtherDynamicPivots", "4"}})
            BOOST_REQUIRE(index->SetParameter(option.first, option.second) == ErrorCode::Success);
        if (algorithm == IndexAlgoType::BKT)
        {
            BOOST_REQUIRE(index->SetParameter("BKTKmeansK", "4") == ErrorCode::Success);
            BOOST_REQUIRE(index->SetParameter("BKTLambdaFactor", "1") == ErrorCode::Success);
        }
        else
            BOOST_REQUIRE(index->SetParameter("KDTNumber", "4") == ErrorCode::Success);
        BOOST_REQUIRE(index->BuildIndex(values.data(), count, dimension, true, false) == ErrorCode::Success);
        COMMON::QueryResultSet<float> plain(values.data(), 2), replay(values.data(), 2);
        BOOST_REQUIRE(index->SearchIndexWithMaxCheck(plain, 1) == ErrorCode::Success);
        BOOST_CHECK_LE(plain.GetScanned(), 1);
        for (int maxCheck : {1, 4, 64})
        {
            BOOST_REQUIRE(index->SetParameter(
                "NumberOfInitialDynamicPivots", maxCheck == 64 ? "1" : "64") == ErrorCode::Success);
            COMMON::QueryResultSet<float> reference(values.data(), 2);
            BOOST_REQUIRE(index->SearchIndexWithMaxCheck(reference, maxCheck) == ErrorCode::Success);
            COMMON::QueryResultSet<float> filtered(values.data(), 2);
            std::array<int, count> visits{};
            BOOST_REQUIRE(index->SearchIndexWithResultFilter(
                filtered, [&](SizeType id) {
                    ++visits[static_cast<size_t>(id)];
                    return id == count - 1;
                }, maxCheck) == ErrorCode::Success);
            BOOST_CHECK_LE(filtered.GetScanned(), maxCheck);
            BOOST_CHECK_GT(filtered.GetScanned(), 0);
            BOOST_CHECK_EQUAL(filtered.GetScanned(), reference.GetScanned());
            for (int visited : visits) BOOST_CHECK_LE(visited, 1);
            BOOST_CHECK_EQUAL(filtered.GetResult(1)->VID, -1);
            BOOST_CHECK_EQUAL(filtered.GetResult(0)->VID,
                visits.back() > 0 ? count - 1 : -1);
            COMMON::QueryResultSet<float> empty(values.data(), 2);
            BOOST_REQUIRE(index->SearchIndexWithResultFilter(
                empty, [](SizeType) { return false; }, maxCheck) == ErrorCode::Success);
            BOOST_CHECK_LE(empty.GetScanned(), maxCheck);
            BOOST_CHECK_EQUAL(empty.GetScanned(), reference.GetScanned());
            BOOST_CHECK_LT(empty.GetResult(0)->VID, 0);
            BOOST_CHECK_LT(empty.GetResult(1)->VID, 0);
        }
        BOOST_REQUIRE(index->SetParameter("NumberOfInitialDynamicPivots", "64") == ErrorCode::Success);
        BOOST_REQUIRE(index->SearchIndexWithMaxCheck(replay, 1) == ErrorCode::Success);
        BOOST_CHECK_EQUAL(plain.GetScanned(), replay.GetScanned());
        for (int rank = 0; rank < 2; ++rank)
        {
            BOOST_CHECK_EQUAL(plain.GetResult(rank)->VID, replay.GetResult(rank)->VID);
            BOOST_CHECK_EQUAL(plain.GetResult(rank)->Dist, replay.GetResult(rank)->Dist);
        }
        ByteArray bytes = ByteArray::Alloc(values.size() * sizeof(float));
        std::memcpy(bytes.Data(), values.data(), bytes.Length());
        auto catalog = std::make_shared<BasicVectorSet>(
            bytes, VectorValueType::Float, dimension, count);
        std::vector<std::uint64_t> samples, offsets;
        std::vector<SPANN::SecondLevelHeadPostings::Member> members;
        using Signature = SPANN::SecondLevelHeadPostings::Signature;
        Signature query, other;
        query.Insert(7);
        other.Insert(9);
        std::vector<Signature> signatures(static_cast<size_t>(count), other);
        signatures.back() = query;
        for (SizeType id = 0; id < count; ++id)
        {
            samples.push_back(id);
            offsets.push_back(id);
            members.push_back(id);
        }
        offsets.push_back(count);
        const auto layer = HierarchyTestPostings(count, 1, samples, offsets, members, signatures);
        for (int levels : {1, 4})
        {
            std::vector<std::shared_ptr<VectorIndex>> indexes(static_cast<size_t>(levels));
            indexes.back() = index;
            std::vector<std::shared_ptr<VectorSet>> catalogs(static_cast<size_t>(levels), catalog);
            std::vector<SPANN::SecondLevelHeadPostings> postings(static_cast<size_t>(levels), layer);
            COMMON::QueryResultSet<float> results(values.data(), 2);
            SPANN::SecondLevelHierarchySearchStats stats;
            BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
                results, 2, count, 0.5, query, [](SizeType) { return true; },
                index, indexes, catalogs, postings, stats) == ErrorCode::Success);
            BOOST_CHECK_EQUAL(results.GetResult(0)->VID, 0);
            BOOST_CHECK_EQUAL(results.GetResult(1)->VID, -1);
            BOOST_CHECK_LE(stats.m_graphScanned, static_cast<std::uint64_t>(count));
            BOOST_CHECK_EQUAL(stats.m_topProbe, 2);
            for (const auto distances : stats.m_layerDistances) BOOST_CHECK_EQUAL(distances, 1U);
        }
    }
}

BOOST_AUTO_TEST_CASE(HierarchyInitialBeamKeepsTheH1PostingTarget)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    auto heads = std::make_shared<HierarchyTestIndex>(
        std::vector<float>{0, 0.1f, 1, 1.1f, 2, 2.1f, 3, 3.1f});
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{0});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {nullptr, top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {
        HierarchyTestCatalog({0, 1, 2, 3}), HierarchyTestCatalog({0})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(8, 1, {0, 2, 4, 6}, {0, 2, 4, 6, 8},
            {0, 1, 2, 3, 4, 5, 6, 7}, std::vector<Signature>(4)),
        HierarchyTestPostings(4, 1, {0}, {0, 4}, {0, 1, 2, 3}, std::vector<Signature>(1))};
    const float target = 0;
    COMMON::QueryResultSet<float> results(&target, 2);
    SPANN::SecondLevelHierarchySearchStats stats;
    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        results, 2, 64, 0.5, Signature(), [](SizeType) { return true; },
        heads, indexes, catalogs, postings, stats) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(stats.m_iterations, 1);
    BOOST_CHECK_EQUAL(stats.m_layerBudgets[1], 1U);
    BOOST_CHECK_EQUAL(stats.m_layerBudgets[0], 2U);
    BOOST_CHECK_EQUAL(results.GetResult(0)->VID, 0);
    BOOST_CHECK_EQUAL(results.GetResult(1)->VID, 1);

    BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
        results, 2, 64, 0.5, Signature(), [](SizeType id) { return id % 2 == 0; },
        heads, indexes, catalogs, postings, stats) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(stats.m_iterations, 1);
    BOOST_CHECK_EQUAL(stats.m_layerBudgets[1], 1U);
    BOOST_CHECK_EQUAL(stats.m_layerBudgets[0], 2U);
    BOOST_CHECK_EQUAL(results.GetResult(0)->VID, 0);
    BOOST_CHECK_EQUAL(results.GetResult(1)->VID, -1);
    for (int probe : top->m_probes) BOOST_CHECK_LE(probe, 2);

    BOOST_CHECK(SPANN::SearchSecondLevelHierarchy(
        results, 2, 64, 0.0, Signature(), [](SizeType) { return true; },
        heads, indexes, catalogs, postings, stats) == ErrorCode::Fail);
}

BOOST_AUTO_TEST_CASE(HierarchyRollingPrefetchWrapsWithoutChangingNearestResults)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    std::vector<float> values(130);
    std::vector<SPANN::SecondLevelHeadPostings::Member> members(130);
    for (size_t i = 0; i < values.size(); ++i)
    {
        values[i] = static_cast<float>(values.size() - 1 - i);
        members[i] = static_cast<SPANN::SecondLevelHeadPostings::Member>(i);
    }
    auto heads = std::make_shared<HierarchyTestIndex>(values);
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{129});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {HierarchyTestCatalog({129})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(130, 1, {0}, {0, 130}, members, {Signature()})};
    const float target = 0;
    COMMON::QueryResultSet<float> results(&target, 3);
    SPANN::SecondLevelHierarchySearchStats stats;
    for (bool batchPrefetch : {false, true})
    {
        heads->m_distanceCalls = 0;
        BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
            results, 3, 64, 1.0, Signature(), [](SizeType) { return true; },
            heads, indexes, catalogs, postings, stats, nullptr, false, nullptr,
            nullptr, nullptr, batchPrefetch) == ErrorCode::Success);
        BOOST_CHECK_EQUAL(heads->m_distanceCalls, 130U);
        for (int rank = 0; rank < 3; ++rank)
        {
            BOOST_CHECK_EQUAL(results.GetResult(rank)->VID, 129 - rank);
            BOOST_CHECK_EQUAL(results.GetResult(rank)->Dist, static_cast<float>(rank * rank));
        }
    }
}

BOOST_AUTO_TEST_CASE(HierarchyBoundedRankingMatchesNativeHeap)
{
    using Signature = SPANN::SecondLevelHeadPostings::Signature;
    std::vector<float> values(65);
    std::vector<SPANN::SecondLevelHeadPostings::Member> members(values.size());
    for (size_t i = 0; i < values.size(); ++i)
    {
        values[i] = static_cast<float>(static_cast<int>(i % 9) - 4);
        members[i] = static_cast<SPANN::SecondLevelHeadPostings::Member>(i);
    }
    values[63] = std::numeric_limits<float>::infinity();
    values[64] = std::numeric_limits<float>::quiet_NaN();
    auto heads = std::make_shared<HierarchyTestIndex>(values);
    auto top = std::make_shared<HierarchyTestIndex>(std::vector<float>{-4});
    const std::vector<std::shared_ptr<VectorIndex>> indexes = {top};
    const std::vector<std::shared_ptr<VectorSet>> catalogs = {HierarchyTestCatalog({-4})};
    const std::vector<SPANN::SecondLevelHeadPostings> postings = {
        HierarchyTestPostings(65, 1, {0}, {0, 65}, members, {Signature()})};
    for (float target : {0.0f, 3.5f})
    {
        for (int budget : {1, 3, 16, 32, 65, 7})
        {
            COMMON::QueryResultSet<float> expected(&target, budget);
            for (auto member : members)
            {
                const SizeType id = static_cast<SizeType>(member);
                expected.AddPoint(id, heads->ComputeDistance(&target, heads->GetSample(id)));
            }
            expected.SortResult();
            COMMON::QueryResultSet<float> results(&target, budget);
            SPANN::SecondLevelHierarchySearchStats stats;
            BOOST_REQUIRE(SPANN::SearchSecondLevelHierarchy(
                results, budget, 64, 1.0, Signature(), [](SizeType) { return true; },
                heads, indexes, catalogs, postings, stats) == ErrorCode::Success);
            for (int rank = 0; rank < budget; ++rank)
            {
                BOOST_CHECK_EQUAL(results.GetResult(rank)->VID, expected.GetResult(rank)->VID);
                BOOST_CHECK_EQUAL(results.GetResult(rank)->Dist, expected.GetResult(rank)->Dist);
            }
        }
    }
}

BOOST_AUTO_TEST_CASE(SecondLevelPostingSignaturesPersistAndFilter)
{
    using Postings =
        SPANN::SecondLevelHeadPostings;
    using Signature = Postings::Signature;
    const std::string path =
        "second_level_posting_signature_unit.bin";
    std::filesystem::remove(path);

    std::uint64_t firstIDFingerprint =
        Postings::BeginIDFingerprint();
    for (std::uint64_t id :
         {101ULL, 103ULL, 107ULL, 109ULL}) {
        firstIDFingerprint =
            Postings::AddIDFingerprint(
                firstIDFingerprint, id);
    }
    const std::uint64_t supportFingerprint =
        0x123456789abcdef0ULL;
    const std::vector<std::uint64_t>
        secondToFirst = {0, 2};
    std::vector<Signature> signatures(2);
    signatures[0].Insert(7);
    signatures[0].Insert(8);

    Postings postings;
    std::string error;
    BOOST_REQUIRE(postings.Initialize(
        4, 2, 1, firstIDFingerprint,
        supportFingerprint, 0.001, 0.02,
        secondToFirst,
        {0, 2, 4}, {0, 1, 2, 3},
        signatures, &error));
    Cache::PostingBitmask query;
    query.Insert(7);
    BOOST_REQUIRE(
        postings.SignatureAt(0) != nullptr);
    BOOST_REQUIRE(
        postings.SignatureAt(1) != nullptr);
    BOOST_CHECK(
        postings.SignatureAt(0)
            ->MayIntersect(query));
    BOOST_CHECK(
        !postings.SignatureAt(1)
             ->MayIntersect(query));
    BOOST_REQUIRE(postings.Save(path, &error));

    Postings loaded;
    BOOST_REQUIRE(loaded.Load(
        path, 4, 2, 1, firstIDFingerprint,
        supportFingerprint, 0.001, 0.02,
        postings.GenerationFingerprint(),
        secondToFirst, &error));
    BOOST_CHECK_EQUAL(
        loaded.SignatureMinSelectivity(), 0.001);
    BOOST_CHECK_EQUAL(
        loaded.SignatureMaxSelectivity(), 0.02);
    Postings persistedDomain;
    BOOST_REQUIRE(persistedDomain.LoadPersisted(
        path, 4, 2, 1, firstIDFingerprint, supportFingerprint,
        postings.GenerationFingerprint(), secondToFirst, &error));
    BOOST_CHECK_EQUAL(persistedDomain.SignatureMinSelectivity(), 0.001);
    BOOST_CHECK_EQUAL(persistedDomain.SignatureMaxSelectivity(), 0.02);
    BOOST_CHECK(!persistedDomain.LoadPersisted(
        path, 4, 2, 1, firstIDFingerprint, supportFingerprint,
        postings.GenerationFingerprint() + 1, secondToFirst, &error));
    BOOST_CHECK(
        loaded.SignatureAt(0)
            ->MayIntersect(query));
    BOOST_CHECK(
        !loaded.SignatureAt(1)
             ->MayIntersect(query));
    Postings mismatchedRange;
    BOOST_CHECK(!mismatchedRange.Load(
        path, 4, 2, 1, firstIDFingerprint,
        supportFingerprint, 0.002f, 0.02f,
        postings.GenerationFingerprint(),
        secondToFirst, &error));

    const std::string legacyPath = path + ".v2";
    Postings fullRange;
    BOOST_REQUIRE(fullRange.Initialize(
        4, 2, 1, firstIDFingerprint,
        supportFingerprint, 0.0, 1.0,
        secondToFirst,
        {0, 2, 4}, {0, 1, 2, 3},
        signatures, &error));
    BOOST_REQUIRE(fullRange.Save(legacyPath, &error));
    Postings::Header fullRangeHeader;
    std::vector<char> legacyBody;
    {
        std::ifstream input(
            legacyPath,
            std::ios::binary | std::ios::ate);
        BOOST_REQUIRE(input.good());
        const auto bytes = input.tellg();
        BOOST_REQUIRE_GT(
            bytes,
            static_cast<std::streamoff>(
                sizeof(fullRangeHeader)));
        legacyBody.resize(
            static_cast<size_t>(
                bytes -
                static_cast<std::streamoff>(
                    sizeof(fullRangeHeader))));
        input.seekg(0);
        input.read(
            reinterpret_cast<char*>(
                &fullRangeHeader),
            sizeof(fullRangeHeader));
        input.read(
            legacyBody.data(),
            static_cast<std::streamsize>(
                legacyBody.size()));
        BOOST_REQUIRE(input.good());
    }
    Postings::HeaderV2 legacyHeader;
    legacyHeader.m_firstLevelHeadCount =
        fullRangeHeader.m_firstLevelHeadCount;
    legacyHeader.m_secondLevelHeadCount =
        fullRangeHeader.m_secondLevelHeadCount;
    legacyHeader.m_replicaCount =
        fullRangeHeader.m_replicaCount;
    legacyHeader.m_memberBytes =
        fullRangeHeader.m_memberBytes;
    legacyHeader.m_signatureBytes =
        fullRangeHeader.m_signatureBytes;
    legacyHeader.m_memberCount =
        fullRangeHeader.m_memberCount;
    legacyHeader.m_firstLevelIDFingerprint =
        fullRangeHeader
            .m_firstLevelIDFingerprint;
    legacyHeader.m_secondLevelIDFingerprint =
        fullRangeHeader
            .m_secondLevelIDFingerprint;
    legacyHeader.m_limitedTagSupportFingerprint =
        fullRangeHeader
            .m_limitedTagSupportFingerprint;
    legacyHeader.m_bodyFingerprint =
        fullRangeHeader.m_bodyFingerprint;
    const auto hashBytes = [](
        std::uint64_t hash,
        const void* data,
        size_t bytes) {
        const auto* begin =
            static_cast<const std::uint8_t*>(data);
        for (size_t offset = 0;
             offset < bytes; ++offset)
        {
            hash ^= begin[offset];
            hash *= 1099511628211ULL;
        }
        return hash;
    };
    std::uint64_t legacyGeneration =
        1469598103934665603ULL;
    legacyGeneration = hashBytes(
        legacyGeneration,
        &legacyHeader.m_firstLevelHeadCount,
        sizeof(legacyHeader.m_firstLevelHeadCount));
    legacyGeneration = hashBytes(
        legacyGeneration,
        &legacyHeader.m_secondLevelHeadCount,
        sizeof(legacyHeader.m_secondLevelHeadCount));
    legacyGeneration = hashBytes(
        legacyGeneration,
        &legacyHeader.m_replicaCount,
        sizeof(legacyHeader.m_replicaCount));
    legacyGeneration = hashBytes(
        legacyGeneration,
        &legacyHeader.m_signatureBytes,
        sizeof(legacyHeader.m_signatureBytes));
    legacyGeneration = hashBytes(
        legacyGeneration,
        &legacyHeader.m_memberCount,
        sizeof(legacyHeader.m_memberCount));
    legacyGeneration = hashBytes(
        legacyGeneration,
        &legacyHeader.m_firstLevelIDFingerprint,
        sizeof(
            legacyHeader
                .m_firstLevelIDFingerprint));
    legacyGeneration = hashBytes(
        legacyGeneration,
        &legacyHeader.m_secondLevelIDFingerprint,
        sizeof(
            legacyHeader
                .m_secondLevelIDFingerprint));
    legacyGeneration = hashBytes(
        legacyGeneration,
        &legacyHeader
             .m_limitedTagSupportFingerprint,
        sizeof(
            legacyHeader
                .m_limitedTagSupportFingerprint));
    legacyGeneration = hashBytes(
        legacyGeneration,
        &legacyHeader.m_bodyFingerprint,
        sizeof(legacyHeader.m_bodyFingerprint));
    legacyHeader.m_generationFingerprint =
        legacyGeneration;
    {
        std::ofstream output(
            legacyPath,
            std::ios::binary | std::ios::trunc);
        BOOST_REQUIRE(output.good());
        output.write(
            reinterpret_cast<const char*>(
                &legacyHeader),
            sizeof(legacyHeader));
        output.write(
            legacyBody.data(),
            static_cast<std::streamsize>(
                legacyBody.size()));
        BOOST_REQUIRE(output.good());
    }
    Postings loadedLegacy;
    BOOST_REQUIRE(loadedLegacy.Load(
        legacyPath, 4, 2, 1,
        firstIDFingerprint,
        supportFingerprint, 0.0, 1.0,
        legacyGeneration, secondToFirst,
        &error));
    BOOST_CHECK_EQUAL(
        loadedLegacy.SignatureMinSelectivity(),
        0.0);
    BOOST_CHECK_EQUAL(
        loadedLegacy.SignatureMaxSelectivity(),
        1.0);
    Postings legacyRangeMismatch;
    BOOST_CHECK(!legacyRangeMismatch.Load(
        legacyPath, 4, 2, 1,
        firstIDFingerprint,
        supportFingerprint, 0.001, 0.02,
        legacyGeneration, secondToFirst,
        &error));
    std::filesystem::remove(legacyPath);

    {
        std::fstream corrupt(
            path,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(corrupt.good());
        const std::uint64_t invalidMemberCount =
            (std::numeric_limits<
                std::uint64_t>::max)();
        const std::streamoff memberCountOffset =
            static_cast<std::streamoff>(
                offsetof(
                    Postings::Header,
                    m_memberCount));
        corrupt.seekp(memberCountOffset);
        corrupt.write(
            reinterpret_cast<const char*>(
                &invalidMemberCount),
            sizeof(invalidMemberCount));
        BOOST_REQUIRE(corrupt.good());
    }
    Postings invalidCount;
    BOOST_CHECK(!invalidCount.Load(
        path, 4, 2, 1, firstIDFingerprint,
        supportFingerprint, 0.001f, 0.02f,
        postings.GenerationFingerprint(),
        secondToFirst, &error));

    BOOST_REQUIRE(postings.Save(path, &error));
    {
        std::fstream corrupt(
            path,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(corrupt.good());
        corrupt.seekg(-1, std::ios::end);
        char byte = 0;
        corrupt.read(&byte, 1);
        corrupt.clear();
        corrupt.seekp(-1, std::ios::end);
        byte ^= 0x5a;
        corrupt.write(&byte, 1);
        BOOST_REQUIRE(corrupt.good());
    }
    Postings invalidSignature;
    BOOST_CHECK(!invalidSignature.Load(
        path, 4, 2, 1, firstIDFingerprint,
        supportFingerprint, 0.001f, 0.02f,
        postings.GenerationFingerprint(),
        secondToFirst, &error));
    std::filesystem::remove(path);
}

BOOST_AUTO_TEST_CASE(AttributeOrganizationRemoved)
{
    SPANN::Options options;
    for (const char* section : {"MultiTenant", "SelectHead", "BuildSSDIndex"}) {
        for (const char* name : {"ACLCols", "hierlevelwidths", "PivotForceNodeCount",
                                 "DisablePivotEstimator", "RoutingCols", "PerVectorTagsFile"}) {
            BOOST_CHECK(options.SetParameter(section, name, "0") == ErrorCode::FailedParseValue);
        }
    }
    BOOST_CHECK(options.SetParameter("SelectHead", "SelectHeadType", "pertagbkt") ==
                ErrorCode::FailedParseValue);
    BOOST_CHECK(options.SetParameter("SelectHead", "SelectHeadType", "BKT") == ErrorCode::Success);
    BOOST_CHECK(options.SetParameter("BuildSSDIndex", "StaticACLTagCols", "1") == ErrorCode::Success);
    BOOST_CHECK(options.SetParameter("BuildSSDIndex", "LimitedTagColumn", "0") == ErrorCode::Success);
}

BOOST_AUTO_TEST_CASE(RedundantFilteringParametersAreRejected)
{
    SPANN::Options options;
    auto index = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
    BOOST_REQUIRE(index != nullptr);
    for (const char* key : {"NumericCols", "EnableExtremeSparseTag", "ExtremeSparseTagMinCount",
                            "ExtremeSparseTagFile", "LogExtremeSparseTagRoute", "FilterKeepCross",
                            "DisableCrossSubgraph", "UnifiedNprobeBudget", "MultiNodeBudgetKeepRatio",
                            "LogUExtra", "PostingQuantBits", "HybridGraphDegree", "EnableHierPostingFilter",
                            "LimitedTagVoteHeadCount", "LimitedTagMaxExpandedPostingPages",
                            "LimitedTagMaxExtraSupports", "limitedtagmaxextrasupports",
                            "SparseFallbackMaxHeads", "SparseFallbackMaxPostingPages",
                            "sparsefallbackmaxheads", "sparsefallbackmaxpostingpages",
                            "HierarchyRouteSelectivityThreshold", "SecondLevelRouteSelectivityThreshold",
                            "hierarchyrouteselectivitythreshold", "secondlevelrouteselectivitythreshold",
                            "BKTSeed", "TPTSeed", "HierarchySignatureMinSelectivity",
                            "HierarchySignatureMaxSelectivity", "SecondLevelSignatureMinSelectivity",
                            "SecondLevelSignatureMaxSelectivity", "EnableUnfilterTail",
                            "UnfilterPurePages", "UnfilterExtraTailPages", "UnfilterPureDistanceScanPercent",
                            "AblateUExtra", "AblateTail", "ForceDenseTagSearch", "DirectSparseMaxPostings",
                            "EnableAdaptiveFilteredNprobe", "FilteredSearchNprobeSafety",
                            "FilteredSearchTargetRecall", "FilteredSearchCoverageExponent",
                            "HeadNavigationMode", "HierarchyGraphSignaturePruning"}) {
        BOOST_CHECK(options.SetParameter("BuildSSDIndex", key, "0") == ErrorCode::FailedParseValue);
        BOOST_CHECK(options.SetParameter("SearchSSDIndex", key, "1") == ErrorCode::FailedParseValue);
        BOOST_CHECK(index->SetParameter(key, "0", "BuildHead") == ErrorCode::FailedParseValue);
        BOOST_CHECK(index->SetParameter(key, "1", "SearchSSDIndex") == ErrorCode::FailedParseValue);
    }
    for (const char* key : {"CrossEdges", "CrossExtraEdges", "DualPoolAugment",
                            "DualPoolExtraRatio", "UExtraIDFile"})
        BOOST_CHECK(options.SetParameter("MultiTenant", key, "0") == ErrorCode::FailedParseValue);
    BOOST_CHECK(options.SetParameter("BuildSSDIndex", "CrossEdges", "false") == ErrorCode::Success);
    BOOST_CHECK(options.SetParameter("SelectHead", "DualPoolAugment", "false") == ErrorCode::Success);
    BOOST_REQUIRE(options.SetParameter("BuildSSDIndex", "HierarchyMaxCheck", "19") == ErrorCode::Success);
    BOOST_REQUIRE(options.SetParameter("BuildSSDIndex", "SearchInternalResultNum", "9") == ErrorCode::Success);
    BOOST_CHECK_EQUAL(options.m_secondLevelMaxCheck, 19);
    BOOST_CHECK_EQUAL(options.m_searchInternalResultNum, 9);
}

BOOST_AUTO_TEST_CASE(SpatialHeadSelectionIgnoresAttributePartitions)
{
    const std::string directory = "attribute_independent_selection";
    struct Cleanup {
        std::string path;
        ~Cleanup() { std::error_code ec; std::filesystem::remove_all(path, ec); }
    } cleanup{directory};
    std::filesystem::remove_all(directory);
    std::filesystem::create_directory(directory);
    constexpr SizeType count = 256;
    constexpr DimensionType dim = 8;
    auto bytes = ByteArray::Alloc(sizeof(float) * count * dim);
    auto* data = reinterpret_cast<float*>(bytes.Data());
    for (SizeType row = 0; row < count; ++row)
        for (DimensionType col = 0; col < dim; ++col)
            data[row * dim + col] = static_cast<float>((row * 17 + col * 31) % 4093);
    auto vectors = std::make_shared<BasicVectorSet>(bytes, VectorValueType::Float, dim, count);
    std::vector<std::uint32_t> tags(count * 2);
    std::vector<char> previous;
    for (int variant = 0; variant < 2; ++variant) {
        for (SizeType row = 0; row < count; ++row) {
            tags[row * 2] = variant == 0 ? row % 4 : 1000 + row;
            tags[row * 2 + 1] = variant == 0 ? row : count - row;
        }
        auto index = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
        const auto set = [&](const char* section, const char* key, const char* value) {
            BOOST_REQUIRE(index->SetParameter(key, value, section) == ErrorCode::Success);
        };
        set("Base", "DistCalcMethod", "L2");
        set("Base", "IndexDirectory", directory.c_str());
        set("SelectHead", "isExecute", "true");
        set("SelectHead", "SelectHeadType", "BKT");
        set("SelectHead", "Ratio", "0.25");
        set("SelectHead", "NumberOfThreads", "1");
        set("SelectHead", "BKTLambdaFactor", "1");
        set("SelectHead", "MinHeadsPerTag", "0");
        set("SelectHead", "SaveBKT", "false");
        set("BuildHead", "isExecute", "false");
        set("BuildSSDIndex", "isExecute", "false");
        auto* spann = dynamic_cast<SPANN::ISPANNIndex*>(index.get());
        BOOST_REQUIRE(spann != nullptr);
        spann->SetVectorTags(tags.data(), count, 2);
        std::srand(19);
        BOOST_REQUIRE(index->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::Success);
        std::ifstream input(directory + "/SPTAGHeadVectorIDs.bin", std::ios::binary);
        BOOST_REQUIRE(input.good());
        std::vector<char> selected((std::istreambuf_iterator<char>(input)), {});
        BOOST_REQUIRE_GT(selected.size(), 8);
        if (variant == 1) BOOST_CHECK(selected == previous);
        previous = std::move(selected);
        BOOST_CHECK(!std::filesystem::exists(directory + "/HeadIndex/tag_node_index.bin"));
        BOOST_CHECK(!std::filesystem::exists(directory + "/HeadIndex/node_1"));
    }
}

BOOST_AUTO_TEST_CASE(HierarchySharedRatioFiveLevelsRoundTrip)
{
    const std::string directory = "hierarchy_shared_ratio_test";
    struct Cleanup {
        std::string path;
        ~Cleanup() { std::error_code ec; std::filesystem::remove_all(path, ec); }
    } cleanup{directory};
    std::filesystem::remove_all(directory);
    std::filesystem::create_directory(directory);
    constexpr SizeType count = 8192;
    constexpr DimensionType dim = 8;
    auto bytes = ByteArray::Alloc(sizeof(float) * count * dim);
    auto* data = reinterpret_cast<float*>(bytes.Data());
    std::vector<std::uint32_t> tags(count, 0);
    for (SizeType row = 0; row < count; ++row)
    {
        tags[row] = row % 4;
        for (DimensionType col = 0; col < dim; ++col)
            data[row * dim + col] = static_cast<float>((row * 17 + col * 31) % 65521);
    }
    auto vectors = std::make_shared<BasicVectorSet>(bytes, VectorValueType::Float, dim, count);
    auto index = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
    const auto set = [&](const char* section, const char* key, const char* value) {
        BOOST_REQUIRE(index->SetParameter(key, value, section) == ErrorCode::Success);
    };
    set("Base", "DistCalcMethod", "L2");
    set("Base", "IndexAlgoType", "BKT");
    set("Base", "IndexDirectory", directory.c_str());
    set("SelectHead", "isExecute", "true");
    set("SelectHead", "SelectHeadType", "Random");
    set("SelectHead", "Ratio", "0.12");
    set("SelectHead", "HierarchyEnabled", "true");
    set("SelectHead", "HierarchyLevels", "5");
    set("SelectHead", "HierarchyReplicaCount", "2");
    set("SelectHead", "BuildH1Graph", "false");
    set("SelectHead", "NumberOfThreads", "1");
    set("SelectHead", "BKTLambdaFactor", "1");
    set("SelectHead", "SelectThreshold", "10");
    set("SelectHead", "SplitFactor", "6");
    set("SelectHead", "SplitThreshold", "25");
    set("BuildHead", "isExecute", "true");
    set("BuildHead", "NumberOfThreads", "1");
    set("BuildHead", "NeighborhoodSize", "16");
    set("BuildHead", "RefineIterations", "1");
    set("BuildSSDIndex", "isExecute", "true");
    set("BuildSSDIndex", "BuildSsdIndex", "true");
    set("BuildSSDIndex", "Storage", "STATIC");
    set("BuildSSDIndex", "NumberOfThreads", "1");
    set("BuildSSDIndex", "EnableLimitedTagPosting", "true");
    set("BuildSSDIndex", "LimitedTagMinHeadCount", "1");
    set("BuildSSDIndex", "NumTagsPerVec", "1");
    set("BuildSSDIndex", "StaticACLTagCols", "1");
    set("BuildSSDIndex", "ExcludeHead", "true");
    set("BuildSSDIndex", "CrossEdges", "0");
    set("BuildSSDIndex", "ReplicaCount", "2");
    set("BuildSSDIndex", "PostingPageLimit", "8");
    set("BuildSSDIndex", "InternalResultNum", "32");
    set("BuildSSDIndex", "HierarchyMaxCheck", "64");
    set("SearchSSDIndex", "HierarchyMaxCheck", "192");
    set("SearchSSDIndex", "HierarchyInitialProbeRatio", "0.666666");
    auto* spann = dynamic_cast<SPANN::Index<float>*>(index.get());
    BOOST_REQUIRE(spann != nullptr);
    spann->SetVectorTags(tags.data(), count, 1);
    set("SelectHead", "SecondLevelRatio", "0.15");
    BOOST_CHECK(index->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::FailedParseValue);
    set("SelectHead", "SecondLevelRatio", "0.12");
    set("SelectHead", "Count", "32");
    BOOST_CHECK(index->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::FailedParseValue);
    set("SelectHead", "Count", "0");
    set("SelectHead", "HierarchyLevels", "1");
    BOOST_CHECK(index->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::FailedParseValue);
    set("SelectHead", "HierarchyLevels", "5");
    BOOST_REQUIRE(index->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::Success);
    BOOST_REQUIRE(index->SaveIndex(directory) == ErrorCode::Success);
    BOOST_CHECK_SMALL(spann->GetOptions()->m_ratio - 0.12, 1e-12);
    SizeType previous = count;
    for (int level = 0; level < 5; ++level)
    {
        const std::string file = directory + "/" + (level == 0
            ? "SPTAGHeadVectorIDs.bin"
            : "SPTAGSecondLevelHeadVectorIDs.bin" +
                (level == 1 ? std::string() : ".level" + std::to_string(level)));
        std::ifstream ids(file, std::ios::binary);
        SizeType actual = 0;
        DimensionType columns = 0;
        BOOST_REQUIRE(ids.read(reinterpret_cast<char*>(&actual), sizeof(actual)));
        BOOST_REQUIRE(ids.read(reinterpret_cast<char*>(&columns), sizeof(columns)));
        BOOST_REQUIRE_EQUAL(columns, 1);
        BOOST_REQUIRE_GT(actual, 0);
        BOOST_REQUIRE_LE(actual, previous);
        BOOST_CHECK_EQUAL(std::filesystem::file_size(file),
            sizeof(actual) + sizeof(columns) + static_cast<size_t>(actual) * sizeof(std::uint64_t));
        const auto expected = std::max(1.0, std::round(previous * 0.12));
        BOOST_CHECK_LE(std::abs(static_cast<double>(actual) - expected), std::max(2.0, expected * 0.25));
        previous = static_cast<SizeType>(actual);
    }
    const std::string loaderPath = directory + "/indexloader.ini";
    std::ifstream input(loaderPath);
    const std::string config((std::istreambuf_iterator<char>(input)), {});
    input.close();
    BOOST_CHECK_EQUAL(config.find("SecondLevelRatio="), std::string::npos);
    BOOST_CHECK_EQUAL(config.find("SelectSecondLevel="), std::string::npos);
    BOOST_CHECK_EQUAL(config.find("SecondLevelMaxCheck="), std::string::npos);
    BOOST_CHECK_EQUAL(config.find("SparseFallbackMaxHeads="), std::string::npos);
    BOOST_CHECK_EQUAL(config.find("LimitedTagMaxExtraSupports="), std::string::npos);
    BOOST_CHECK_EQUAL(config.find("SparseFallbackMaxPostingPages="), std::string::npos);
    BOOST_CHECK_EQUAL(config.find("HierarchyRouteSelectivityThreshold="), std::string::npos);
    BOOST_CHECK_EQUAL(config.find("SecondLevelRouteSelectivityThreshold="), std::string::npos);
    BOOST_CHECK_NE(config.find("HierarchyLevels=5"), std::string::npos);
    Helper::IniReader persisted;
    BOOST_REQUIRE(persisted.LoadIniFile(loaderPath) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(persisted.GetParameter("BuildSSDIndex", "HierarchyMaxCheck", 0), 64);
    BOOST_CHECK_EQUAL(persisted.GetParameter("SearchSSDIndex", "HierarchyMaxCheck", 0), 192);
    index.reset();
    BOOST_REQUIRE(VectorIndex::LoadIndex(directory, index) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(index->GetParameter("Ratio", "SelectHead"), "0.120000");
    BOOST_CHECK_EQUAL(index->GetParameter("HierarchyMaxCheck", "SearchSSDIndex"), "192");
    BOOST_REQUIRE(index->SaveIndex(directory) == ErrorCode::Success);
    index.reset();

    for (const char* section : {"BuildSSDIndex", "SearchSSDIndex"})
    {
        const std::string header = std::string("[") + section + "]\n";
        const auto position = config.find(header);
        BOOST_REQUIRE_NE(position, std::string::npos);
        for (const char* key : {"SparseFallbackMaxHeads", "SparseFallbackMaxPostingPages",
                               "HierarchyRouteSelectivityThreshold", "SecondLevelRouteSelectivityThreshold",
                               "hierarchyrouteselectivitythreshold", "secondlevelrouteselectivitythreshold"})
        {
            for (const char* value : {"0", "1"})
            {
                std::string rejected = config;
                rejected.insert(position + header.size(), std::string(key) + "=" + value + "\n");
                {
                    std::ofstream output(loaderPath, std::ios::trunc);
                    output << rejected;
                    BOOST_REQUIRE(output.good());
                }
                BOOST_CHECK(VectorIndex::LoadIndex(directory, index) == ErrorCode::FailedParseValue);
                index.reset();
            }
        }
    }

    const auto legacyConfig = [&](const char* ratio) {
        std::string legacy = config;
        for (const auto& names : std::vector<std::pair<std::string, std::string>>{
                 {"HierarchyEnabled", "SelectSecondLevel"},
                 {"HierarchyLevels", "SecondLevelHierarchyLevels"},
                 {"HierarchyMaxCheck", "SecondLevelMaxCheck"},
                 {"HierarchyInitialProbeRatio", "SecondLevelInitialProbeRatio"}})
        {
            size_t pos = 0;
            while ((pos = legacy.find(names.first + "=", pos)) != std::string::npos)
            {
                legacy.replace(pos, names.first.size(), names.second);
                pos += names.second.size();
            }
        }
        legacy.insert(legacy.find("[SelectHead]\n") + std::strlen("[SelectHead]\n"),
            std::string("SecondLevelRatio=") + ratio + "\n");
        std::ofstream output(loaderPath, std::ios::trunc);
        output << legacy;
    };
    legacyConfig("0.12");
    BOOST_REQUIRE(VectorIndex::LoadIndex(directory, index) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(index->GetParameter("HierarchyLevels", "SelectHead"), "5");
    BOOST_CHECK_EQUAL(index->GetParameter("HierarchyMaxCheck", "SearchSSDIndex"), "192");
    BOOST_REQUIRE(index->SaveIndex(directory) == ErrorCode::Success);
    index.reset();
    {
        std::ifstream saved(loaderPath);
        const std::string canonical((std::istreambuf_iterator<char>(saved)), {});
        BOOST_CHECK_EQUAL(canonical.find("SecondLevelRatio="), std::string::npos);
        BOOST_CHECK_EQUAL(canonical.find("SecondLevelMaxCheck="), std::string::npos);
    }
    for (const char* ratio : {"0.15", "nan", "invalid"})
    {
        legacyConfig(ratio);
        BOOST_CHECK(VectorIndex::LoadIndex(directory, index) == ErrorCode::FailedParseValue);
        index.reset();
    }
    std::string mixed = config;
    mixed.insert(mixed.find("[SelectHead]\n") + std::strlen("[SelectHead]\n"),
        "SelectSecondLevel=false\nSecondLevelHierarchyLevels=2\n");
    mixed.insert(mixed.find("[SearchSSDIndex]\n") + std::strlen("[SearchSSDIndex]\n"),
        "SecondLevelMaxCheck=112\n");
    {
        std::ofstream output(loaderPath, std::ios::trunc);
        output << mixed;
    }
    BOOST_REQUIRE(VectorIndex::LoadIndex(directory, index) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(index->GetParameter("HierarchyEnabled", "SelectHead"), "true");
    BOOST_CHECK_EQUAL(index->GetParameter("HierarchyLevels", "SelectHead"), "5");
    BOOST_CHECK_EQUAL(index->GetParameter("HierarchyMaxCheck", "SearchSSDIndex"), "192");
}

BOOST_AUTO_TEST_CASE(HierarchyLegacyRatioConstraintIsOrderIndependent)
{
    for (bool legacyFirst : {false, true})
    {
        SPANN::Options options;
        options.SetParameter("SelectHead", "HierarchyEnabled", "true");
        if (legacyFirst) options.SetParameter("SelectHead", "SecondLevelRatio", "0.12");
        options.SetParameter("SelectHead", "Ratio", "0.12");
        if (!legacyFirst) options.SetParameter("SelectHead", "SecondLevelRatio", "0.12");
        BOOST_CHECK(options.ValidateHierarchyRatio());
        BOOST_CHECK(options.GetParameter("SelectHead", "SecondLevelRatio").empty());
        options.SetParameter("SelectHead", "Ratio", "0.15");
        BOOST_CHECK(!options.ValidateHierarchyRatio());
        options.SetParameter("SelectHead", "HierarchyEnabled", "false");
        BOOST_CHECK(options.ValidateHierarchyRatio());
    }
}

BOOST_AUTO_TEST_CASE(SecondLevelResumeRebuildsPartialGraph)
{
    constexpr SizeType baseCount = 128;
    constexpr DimensionType dimension = 8;
    const std::string indexDirectory =
        "second_level_resume_index";
    std::filesystem::remove_all(indexDirectory);
    std::filesystem::create_directories(
        indexDirectory);

    ByteArray bytes = ByteArray::Alloc(
        sizeof(float) *
        static_cast<size_t>(baseCount) *
        dimension);
    auto* data =
        reinterpret_cast<float*>(bytes.Data());
    std::vector<std::uint32_t> tags(
        static_cast<size_t>(baseCount));
    for (SizeType row = 0; row < baseCount;
         ++row) {
        tags[static_cast<size_t>(row)] =
            static_cast<std::uint32_t>(row % 4);
        for (DimensionType dim = 0;
             dim < dimension; ++dim) {
            data[static_cast<size_t>(row) *
                     dimension +
                 dim] =
                static_cast<float>(
                    (row * 17 + dim * 31) % 251) /
                251.0f;
        }
    }
    auto vectors =
        std::make_shared<BasicVectorSet>(
            bytes, VectorValueType::Float,
            dimension, baseCount);

    const auto configure =
        [&](const std::shared_ptr<VectorIndex>&
                target) {
            const auto set =
                [&](const char* section,
                    const char* key,
                    const std::string& value) {
                    BOOST_REQUIRE(
                        target->SetParameter(
                            key, value.c_str(),
                            section) ==
                        ErrorCode::Success);
                };
            set("Base", "DistCalcMethod", "L2");
            set("Base", "IndexAlgoType", "BKT");
            set("Base", "ValueType", "Float");
            set("Base", "Dim",
                std::to_string(dimension));
            set("Base", "IndexDirectory",
                indexDirectory);
            set("SelectHead", "isExecute", "true");
            set("SelectHead", "SelectHeadType", "BKT");
            set("SelectHead", "Ratio", "0.25");
            set("SelectHead", "NumberOfThreads", "1");
            set("SelectHead", "SelectThreshold", "20");
            set("SelectHead", "SplitFactor", "4");
            set("SelectHead", "SplitThreshold", "40");
            set("SelectHead", "SelectSecondLevel",
                "true");
            set("SelectHead",
                "SecondLevelReplicaCount", "2");
            set("BuildHead", "isExecute", "true");
            set("BuildHead", "NeighborhoodSize", "16");
            set("BuildHead", "RefineIterations", "1");
            set("BuildHead", "MaxCheck", "512");
            set("BuildHead",
                "MaxCheckForRefineGraph", "512");
            set("BuildHead", "NumberOfThreads", "1");
            set("BuildSSDIndex", "isExecute", "true");
            set("BuildSSDIndex", "BuildSsdIndex",
                "true");
            set("BuildSSDIndex", "Storage", "STATIC");
            set("BuildSSDIndex", "InternalResultNum",
                "8");
            set("BuildSSDIndex",
                "SearchInternalResultNum", "8");
            set("BuildSSDIndex", "NumberOfThreads",
                "2");
            set("BuildSSDIndex", "MaxCheck", "512");
            set("BuildSSDIndex", "PostingPageLimit",
                "1");
            set("BuildSSDIndex",
                "SearchPostingPageLimit", "1");
            set("BuildSSDIndex", "SSDIndexFileNum",
                "1");
            set("BuildSSDIndex", "ReplicaCount", "2");
            set("BuildSSDIndex", "RNGFactor", "100");
            set("BuildSSDIndex",
                "TailReplicaCount", "0");
            set("BuildSSDIndex",
                "UnfilterTailBufferLength", "0");
            set("BuildSSDIndex", "CrossEdges", "0");
            set("BuildSSDIndex", "ExcludeHead",
                "true");
            set("BuildSSDIndex", "NumTagsPerVec",
                "1");
            set("BuildSSDIndex", "StaticACLTagCols",
                "1");
            set("BuildSSDIndex",
                "EnableLimitedTagPosting", "true");
            set("BuildSSDIndex",
                "LimitedTagMinHeadCount", "2");
        };
    const auto configureData =
        [&](const std::shared_ptr<VectorIndex>&
                target) {
            auto* spann =
                dynamic_cast<SPANN::ISPANNIndex*>(
                    target.get());
            BOOST_REQUIRE(spann != nullptr);
            spann->SetVectorTags(
                tags.data(), baseCount, 1);
        };

    ScopedEnvironmentVariable persistSelectHead(
        "SPTAG_PERSIST_SELECTHEAD", "1");
    ScopedEnvironmentVariable resumeBuild(
        "SPTAG_RESUME_BUILD", nullptr);
    auto index = VectorIndex::CreateInstance(
        IndexAlgoType::SPANN,
        VectorValueType::Float);
    BOOST_REQUIRE(index != nullptr);
    configure(index);
    configureData(index);
    BOOST_REQUIRE(
        index->BuildIndex(
            vectors, nullptr, true, false,
            false) == ErrorCode::Success);

    const std::string firstGraph =
        indexDirectory +
        "/HeadIndex/graph.bin";
    const std::string secondGraph =
        indexDirectory +
        "/SecondLevelHeadIndex/graph.bin";
    BOOST_REQUIRE(std::filesystem::exists(
        indexDirectory +
        "/head_select_state.bin"));
    BOOST_REQUIRE(std::filesystem::exists(
        firstGraph));
    BOOST_REQUIRE(std::filesystem::exists(
        secondGraph));
    index.reset();

    BOOST_REQUIRE(
        std::filesystem::remove(secondGraph));
    resumeBuild.Set("1");
    auto resumed = VectorIndex::CreateInstance(
        IndexAlgoType::SPANN,
        VectorValueType::Float);
    BOOST_REQUIRE(resumed != nullptr);
    configure(resumed);
    configureData(resumed);
    BOOST_REQUIRE(
        resumed->BuildIndex(
            vectors, nullptr, true, false,
            false) == ErrorCode::Success);
    BOOST_CHECK(std::filesystem::exists(
        firstGraph));
    BOOST_CHECK(std::filesystem::exists(
        secondGraph));

    resumed.reset();
    for (const auto& change : std::vector<std::pair<const char*, const char*>>{
             {"Ratio", "0.12"}, {"HierarchyLevels", "5"}})
    {
        auto incompatible = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
        configure(incompatible);
        configureData(incompatible);
        BOOST_REQUIRE(incompatible->SetParameter(change.first, change.second, "SelectHead") == ErrorCode::Success);
        BOOST_CHECK(incompatible->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::FailedParseValue);
    }
    const auto checkpoint = std::filesystem::path(indexDirectory) / "head_select_state.bin";
    std::filesystem::resize_file(checkpoint, std::filesystem::file_size(checkpoint) -
        sizeof(std::uint32_t) - sizeof(double) - sizeof(std::int32_t));
    auto legacyCheckpoint = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
    configure(legacyCheckpoint);
    configureData(legacyCheckpoint);
    BOOST_CHECK(legacyCheckpoint->BuildIndex(
        vectors, nullptr, true, false, false) == ErrorCode::FailedParseValue);
    legacyCheckpoint.reset();
    std::filesystem::remove_all(indexDirectory);
}

BOOST_AUTO_TEST_CASE(HierarchyHeadOnlyMembershipMayUnderfill)
{
    constexpr SizeType count = 512;
    constexpr DimensionType dimension = 4;
    const std::filesystem::path directory = "hierarchy_head_only_index";
    struct Cleanup
    {
        std::filesystem::path path;
        ~Cleanup() { std::error_code error; std::filesystem::remove_all(path, error); }
    } cleanup{directory};
    for (int keyColumn : {0, 1, 3, Cache::HIER_LEVELS})
    {
        std::filesystem::remove_all(directory);
        std::filesystem::create_directory(directory);
        ByteArray bytes = ByteArray::Alloc(static_cast<size_t>(count) * dimension * sizeof(float));
        auto* data = reinterpret_cast<float*>(bytes.Data());
        const int columns = keyColumn + 1;
        std::vector<std::uint32_t> tags(static_cast<size_t>(count) * columns, 0);
        for (SizeType row = 0; row < count; ++row)
        {
            tags[static_cast<size_t>(row) * columns + keyColumn] = 1000 + row;
            for (DimensionType column = 0; column < dimension; ++column)
                data[static_cast<size_t>(row) * dimension + column] =
                    static_cast<float>(row * (column + 1));
        }
        auto vectors = std::make_shared<BasicVectorSet>(
            bytes, VectorValueType::Float, dimension, count);
        auto index = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
        const auto set = [&](const char* section, const char* name, const std::string& value) {
            BOOST_REQUIRE(index->SetParameter(name, value.c_str(), section) == ErrorCode::Success);
        };
        set("Base", "DistCalcMethod", "L2");
        set("Base", "IndexAlgoType", "BKT");
        set("Base", "IndexDirectory", directory.string());
        set("SelectHead", "isExecute", "true");
        set("SelectHead", "SelectHeadType", "Random");
        set("SelectHead", "Ratio", "0.999999");
        set("SelectHead", "NumberOfThreads", "1");
        set("SelectHead", "SelectSecondLevel", "true");
        set("SelectHead", "SecondLevelHierarchyLevels", "3");
        set("SelectHead", "SecondLevelReplicaCount", "2");
        set("SelectHead", "BuildH1Graph", "false");
        set("BuildHead", "isExecute", "true");
        set("BuildHead", "NumberOfThreads", "1");
        set("BuildHead", "NeighborhoodSize", "16");
        set("BuildHead", "RefineIterations", "1");
        set("BuildHead", "BKTLambdaFactor", "1");
        set("BuildSSDIndex", "isExecute", "true");
        set("BuildSSDIndex", "BuildSsdIndex", "true");
        set("BuildSSDIndex", "Storage", "STATIC");
        set("BuildSSDIndex", "NumberOfThreads", "1");
        set("BuildSSDIndex", "InternalResultNum", "16");
        set("BuildSSDIndex", "ReplicaCount", "2");
        set("BuildSSDIndex", "PostingPageLimit", "1");
        set("BuildSSDIndex", "TailReplicaCount", "0");
        set("BuildSSDIndex", "UnfilterTailBufferLength", "0");
        set("BuildSSDIndex", "CrossEdges", "0");
        set("BuildSSDIndex", "ExcludeHead", "true");
        set("BuildSSDIndex", "NumTagsPerVec", std::to_string(columns));
        set("BuildSSDIndex", "StaticACLTagCols", std::to_string(columns));
        if (keyColumn == 1)
            set("BuildSSDIndex", "ColumnTypes", "numeric,categorical");
        if (keyColumn == 3)
            set("BuildSSDIndex", "ColumnTypes", "numeric,categorical,numeric,categorical");
        set("BuildSSDIndex", "EnableLimitedTagPosting", "true");
        set("BuildSSDIndex", "LimitedTagColumn", std::to_string(keyColumn));
        set("BuildSSDIndex", "LimitedTagSlotsPerHead", "2");
        set("BuildSSDIndex", "LimitedTagMinHeadCount", "1");
        auto* spann = dynamic_cast<SPANN::ISPANNIndex*>(index.get());
        BOOST_REQUIRE(spann != nullptr);
        spann->SetVectorTags(tags.data(), count, columns);
        BOOST_REQUIRE(index->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::Success);
        if (keyColumn == 1 || keyColumn == 3) {
            set("SearchSSDIndex", "InternalResultNum", std::to_string(count));
            set("SearchSSDIndex", "HierarchyMaxCheck", std::to_string(count));
            VectorIndex::ThreadLocalSearchContext context;
            context.m_dnf.clauses.push_back({{
                {static_cast<std::uint32_t>(keyColumn), 1000 + count - 1, Cache::DNF_EQ, 0},
                {0, 0, Cache::DNF_EQ, 1}}});
            context.m_limitedTagMembershipEligible = true;
            context.m_limitedTagQueryValues = {1000 + count - 1};
            VectorIndex::ThreadLocalSearchContextGuard guard(std::move(context));
            COMMON::QueryResultSet<float> results(data, 1);
            BOOST_REQUIRE(index->SearchIndex(results) == ErrorCode::Success);
            BOOST_CHECK_EQUAL(results.GetResult(0)->VID, count - 1);
        }
        BOOST_REQUIRE(index->SaveIndex(directory.string()) == ErrorCode::Success);
        index.reset();
        BOOST_REQUIRE(VectorIndex::LoadIndex(directory.string(), index) == ErrorCode::Success);
        auto* loaded = dynamic_cast<SPANN::Index<float>*>(index.get());
        BOOST_REQUIRE(loaded != nullptr);
        BOOST_REQUIRE(loaded->HasRoutingOnlyHierarchy());
        BOOST_REQUIRE_EQUAL(loaded->GetMemoryIndex()->GetNumSamples(), count);
        for (SizeType head = 0; head < count; ++head)
            BOOST_REQUIRE(!loaded->GetDiskIndex()->CheckValidPosting(head));
        set("SearchSSDIndex", "SecondLevelMaxCheck", "1024");
        set("SearchSSDIndex", "SecondLevelInitialProbeRatio", "0.5");
        const auto search = [&](bool pureRoute, std::uint32_t tag) {
            VectorIndex::ThreadLocalSearchContext context;
            context.m_queryTags = {tag};
            context.m_limitedTagMembershipEligible = pureRoute;
            if (pureRoute) context.m_limitedTagQueryValues = {tag};
            VectorIndex::ThreadLocalSearchContextGuard guard(std::move(context));
            COMMON::QueryResultSet<float> results(data, 1);
            BOOST_REQUIRE(index->SearchIndex(results) == ErrorCode::Success);
            return results.GetResult(0)->VID;
        };
        set("SearchSSDIndex", "InternalResultNum", std::to_string(count));
        BOOST_CHECK_LT(search(true, 1000 + count - 1), 0);
        set("SearchSSDIndex", "InternalResultNum", "8");
        BOOST_CHECK_LT(search(true, 1000 + count - 1), 0);
        BOOST_CHECK_EQUAL(search(true, 1000), 0);
        BOOST_CHECK_LT(search(true, 1000 + count - 1), 0);
        if (keyColumn > 1) BOOST_CHECK_EQUAL(search(false, 0), 0);
        if (keyColumn == 1) BOOST_CHECK_LT(search(false, 0), 0);
        if (keyColumn == 1 || keyColumn == 3) {
            set("SearchSSDIndex", "InternalResultNum", std::to_string(count));
            VectorIndex::ThreadLocalSearchContext context;
            Cache::DNFPredicate predicate;
            Cache::DNFClause clause;
            clause.lits.push_back({static_cast<std::uint32_t>(keyColumn), 1000 + count - 1, Cache::DNF_EQ, 0});
            clause.lits.push_back({0, 0, Cache::DNF_EQ, 1});
            predicate.clauses.push_back(clause);
            context.m_dnf = predicate;
            context.m_limitedTagMembershipEligible = true;
            context.m_limitedTagQueryValues = {1000 + count - 1};
            VectorIndex::ThreadLocalSearchContextGuard guard(std::move(context));
            COMMON::QueryResultSet<float> results(data, 1);
            BOOST_REQUIRE(index->SearchIndex(results) == ErrorCode::Success);
            BOOST_CHECK_LT(results.GetResult(0)->VID, 0);
        }
        {
            VectorIndex::ThreadLocalSearchContext context;
            context.m_queryTags = {1000 + count};
            context.m_limitedTagMembershipEligible = true;
            context.m_limitedTagQueryValues = context.m_queryTags;
            VectorIndex::ThreadLocalSearchContextGuard guard(std::move(context));
            COMMON::QueryResultSet<float> results(data, 3);
            BOOST_REQUIRE(index->SearchIndex(results) == ErrorCode::Success);
            for (int rank = 0; rank < 3; ++rank)
                BOOST_CHECK_LT(results.GetResult(rank)->VID, 0);
        }
    }
}

BOOST_AUTO_TEST_CASE(TagSchemaOriginalColumnsAndValidation)
{
    const auto schema = TagSchema::Parse("num,cate,numeric,categorical");
    BOOST_CHECK_EQUAL(schema.text, "numeric,categorical,numeric,categorical");
    BOOST_CHECK(schema.categorical == std::vector<int>({1, 3}));
    BOOST_CHECK(schema.numeric == std::vector<int>({0, 2}));
    BOOST_CHECK_EQUAL(schema.NumericLane(2), 1);
    BOOST_CHECK_EQUAL(schema.NumericLane(1), -1);
    Cache::HierarchicalPostingMask hierarchy;
    hierarchy.Clear();
    hierarchy.Insert(3, 7);
    Cache::PostingBitmask coarse;
    coarse.Insert(7);
    std::vector<Cache::NumQuantParam> domains(2);
    domains[0].lo = 0; domains[0].hi = 100;
    domains[1].lo = 1000; domains[1].hi = 2000;
    std::vector<std::uint64_t> quant(2 * Cache::NUM_QUANT_WORDS, 0);
    Cache::NumQuantInsert(quant.data(), 0, Cache::NumQuantBucket(domains[0], 5));
    Cache::NumQuantInsert(quant.data(), 1, Cache::NumQuantBucket(domains[1], 1500));
    Cache::DNFPredicate predicate;
    predicate.clauses.push_back({{{3, 7, Cache::DNF_EQ, 0}, {2, 1400, Cache::DNF_GE, 1}}});
    BOOST_CHECK(predicate.MayMatchHierQuant(hierarchy, quant.data(), 2, domains.data(), 2,
        2, Cache::HierWidths(), &schema.numericLanes));
    BOOST_CHECK(predicate.MayMatchCoarseQuant(coarse, quant.data(), 2, domains.data(), 2,
        2, &schema.numericLanes));
    predicate.clauses[0].lits[1].op = Cache::DNF_LE;
    BOOST_CHECK(!predicate.MayMatchHierQuant(hierarchy, quant.data(), 2, domains.data(), 2,
        2, Cache::HierWidths(), &schema.numericLanes));
    BOOST_CHECK(!predicate.MayMatchCoarseQuant(coarse, quant.data(), 2, domains.data(), 2,
        2, &schema.numericLanes));
    for (const auto* bad : {"cate,", ",numeric", "category", "cate,,num", "numeric,unknown"})
        BOOST_CHECK_THROW(TagSchema::Parse(bad), std::invalid_argument);
    BOOST_CHECK_THROW(TagSchema::Parse("cate,num", 3), std::invalid_argument);
    SPANN::Options options;
    options.m_numTagsPerVec = 4;
    options.m_columnTypes = schema.text;
    options.m_enableLimitedTagPosting = true;
    options.m_limitedTagColumn = 3;
    BOOST_REQUIRE(options.ValidateTagSchema());
    BOOST_REQUIRE(options.ValidateTagSchema(true));
    options.m_columnTypes = "categorical,numeric,numeric,categorical";
    BOOST_CHECK(!options.ValidateTagSchema(true));
    options.m_columnTypes = schema.text;
    options.m_limitedTagColumn = 0;
    BOOST_CHECK(!options.ValidateTagSchema());
    options.m_limitedTagColumn = 4;
    BOOST_CHECK(!options.ValidateTagSchema());
}

BOOST_AUTO_TEST_CASE(LimitedTagNativeBuildAndSearchPageLimits)
{
    constexpr SizeType count = 512;
    constexpr DimensionType dimension = 32;
    constexpr int recordBytes = 140;
    const std::filesystem::path directory = "limited_tag_native_limits";
    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { std::error_code error; std::filesystem::remove_all(path, error); }
    } cleanup{directory};
    std::filesystem::create_directories(directory);
    ByteArray bytes = ByteArray::Alloc(count * dimension * sizeof(float));
    auto* data = reinterpret_cast<float*>(bytes.Data());
    std::vector<std::uint32_t> tags(count * 2);
    std::uint32_t random = 0x12345678;
    for (SizeType vid = 0; vid < count; ++vid) {
        tags[vid * 2] = 7;
        tags[vid * 2 + 1] = vid;
        for (int column = 0; column < dimension; ++column) {
            random ^= random << 13;
            random ^= random >> 17;
            random ^= random << 5;
            data[vid * dimension + column] = random & 1 ? 1.0f : -1.0f;
        }
    }
    auto vectors = std::make_shared<BasicVectorSet>(
        bytes, VectorValueType::Float, dimension, count);
    const auto build = [&](const char* name, bool limited, int pages,
                           int vectorLimit = 118, bool dense = false) {
        const auto path = directory / name;
        std::filesystem::create_directories(path);
        auto index = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
        const auto set = [&](const char* section, const char* key, const std::string& value) {
            BOOST_REQUIRE(index->SetParameter(key, value.c_str(), section) == ErrorCode::Success);
        };
        set("Base", "IndexDirectory", path.string());
        set("Base", "DistCalcMethod", "L2");
        set("Base", "IndexAlgoType", "BKT");
        set("SelectHead", "isExecute", "true");
        set("SelectHead", "SelectHeadType", "Random");
        set("SelectHead", "Ratio", dense ? "0.015625" : "0.0625");
        set("SelectHead", "NumberOfThreads", "1");
        set("BuildHead", "isExecute", "true");
        set("BuildHead", "NumberOfThreads", "1");
        set("BuildHead", "NeighborhoodSize", "32");
        set("BuildHead", "RefineIterations", "2");
        set("BuildHead", "BKTLambdaFactor", "1");
        set("BuildSSDIndex", "isExecute", "true");
        set("BuildSSDIndex", "BuildSsdIndex", "true");
        set("BuildSSDIndex", "Storage", "STATIC");
        set("BuildSSDIndex", "NumberOfThreads", "1");
        set("BuildSSDIndex", "InternalResultNum", "32");
        set("BuildSSDIndex", "MaxCheck", "1024");
        set("BuildSSDIndex", "ReplicaCount", "8");
        set("BuildSSDIndex", "RNGFactor", "100");
        set("BuildSSDIndex", "PostingPageLimit", std::to_string(pages));
        set("BuildSSDIndex", "PostingVectorLimit", std::to_string(vectorLimit));
        set("BuildSSDIndex", "SearchPostingPageLimit", "2");
        set("BuildSSDIndex", "ExcludeHead", "true");
        set("BuildSSDIndex", "NumTagsPerVec", "2");
        set("BuildSSDIndex", "StaticACLTagCols", "1");
        set("BuildSSDIndex", "ColumnTypes", "categorical,numeric");
        set("BuildSSDIndex", "EnableLimitedTagPosting", limited ? "true" : "false");
        set("BuildSSDIndex", "EnableLimitedTagSupportExpansion",
            limited && dense ? "true" : "false");
        set("BuildSSDIndex", "LimitedTagSlotsPerHead", "1");
        set("BuildSSDIndex", "LimitedTagMinHeadCount", "1");
        set("BuildSSDIndex", "TailReplicaCount", "0");
        set("BuildSSDIndex", "UnfilterTailBufferLength", "0");
        set("BuildSSDIndex", "CrossEdges", "0");
        auto* spann = dynamic_cast<SPANN::ISPANNIndex*>(index.get());
        BOOST_REQUIRE(spann != nullptr);
        spann->SetVectorTags(tags.data(), count, 2);
        const std::string seed = dense ? "rescue" : "limited";
        if (std::string(name) != seed) {
            // Reuse one realized head index: clock-reseeded TPT builds are not
            // comparable even after a test-only srand reset.
            std::filesystem::copy(directory / seed / "HeadIndex", path / "HeadIndex",
                std::filesystem::copy_options::recursive);
            std::filesystem::copy_file(directory / seed / "SPTAGHeadVectorIDs.bin",
                path / "SPTAGHeadVectorIDs.bin");
            set("SelectHead", "isExecute", "false");
            set("BuildHead", "isExecute", "false");
        }
        const auto status = index->BuildIndex(vectors, nullptr, true, false, false);
        BOOST_REQUIRE(status == ErrorCode::Success);
        if (pages > 0) {
            auto* typed = dynamic_cast<SPANN::Index<float>*>(index.get());
            BOOST_REQUIRE(typed != nullptr);
            BOOST_CHECK_EQUAL(typed->GetOptions()->m_postingPageLimit,
                vectorLimit == 118 ? 5 : pages);
        }
        set("SearchSSDIndex", "SearchPostingPageLimit", "2");
        set("SearchSSDIndex", "PostingPageLimit", "1");
        BOOST_REQUIRE(index->SaveIndex(path.string()) == ErrorCode::Success);
        index.reset();
        BOOST_REQUIRE(VectorIndex::LoadIndex(path.string(), index) == ErrorCode::Success);
        return index;
    };
    auto limited = build("limited", true, 3);
    auto original = build("original", false, 3);
    auto unlimited = build("unlimited", true, 0);
    auto* typed = dynamic_cast<SPANN::Index<float>*>(limited.get());
    auto* originalTyped = dynamic_cast<SPANN::Index<float>*>(original.get());
    auto* unlimitedTyped = dynamic_cast<SPANN::Index<float>*>(unlimited.get());
    BOOST_REQUIRE(typed && originalTyped && unlimitedTyped);
    auto disk = typed->GetDiskIndex();
    auto headIndex = typed->GetMemoryIndex();
    BOOST_CHECK_EQUAL(typed->GetOptions()->m_searchPostingPageLimit, 1);
    BOOST_CHECK_EQUAL(limited->GetParameter("PostingPageLimit", "SearchSSDIndex"), "1");
    BOOST_CHECK_EQUAL(limited->GetParameter("SearchPostingPageLimit", "SearchSSDIndex"), "1");
    BOOST_CHECK_EQUAL(limited->GetParameter("PostingPageLimit", "BuildSSDIndex"), "3");
    const SizeType heads = headIndex->GetNumSamples();
    BOOST_REQUIRE_EQUAL(heads, originalTyped->GetMemoryIndex()->GetNumSamples());
    BOOST_REQUIRE_EQUAL(heads, unlimitedTyped->GetMemoryIndex()->GetNumSamples());
    bool hCut = false, oCut = false;
    SizeType probeHead = -1;
    std::vector<bool> hSeen(count, false);
    for (SizeType head = 0; head < heads; ++head) {
        BOOST_REQUIRE_EQUAL(typed->GetGlobalVID(head), originalTyped->GetGlobalVID(head));
        BOOST_REQUIRE_EQUAL(typed->GetGlobalVID(head), unlimitedTyped->GetGlobalVID(head));
        const int pure = disk->GetPostingVectorCount(head, true);
        const int full = disk->GetPostingVectorCount(head, false);
        const int uncutH = unlimitedTyped->GetDiskIndex()->GetPostingVectorCount(head, true);
        const int uncutO = unlimitedTyped->GetDiskIndex()->GetPostingVectorCount(head, false) - uncutH;
        BOOST_CHECK_EQUAL(pure, (std::min)(146, uncutH));
        BOOST_CHECK_EQUAL(full - pure, (std::min)(146, uncutO));
        hCut |= pure < uncutH;
        oCut |= full - pure < uncutO;
        if (pure > 87 && full - pure > 87) probeHead = head;
        std::string actual, baseline, uncut;
        BOOST_REQUIRE(disk->GetWritePosting(nullptr, head, actual, false) == ErrorCode::Success);
        BOOST_REQUIRE(originalTyped->GetDiskIndex()->GetWritePosting(
            nullptr, head, baseline, false) == ErrorCode::Success);
        BOOST_REQUIRE(unlimitedTyped->GetDiskIndex()->GetWritePosting(
            nullptr, head, uncut, false) == ErrorCode::Success);
        BOOST_REQUIRE_EQUAL(actual.size(), static_cast<size_t>(full) * recordBytes);
        BOOST_CHECK_EQUAL(actual.substr(pure * recordBytes), baseline);
        BOOST_CHECK_EQUAL(actual.substr(0, pure * recordBytes), uncut.substr(0, pure * recordBytes));
        BOOST_CHECK_EQUAL(actual.substr(pure * recordBytes),
            uncut.substr(uncutH * recordBytes, (full - pure) * recordBytes));
        for (int row = 0; row < full; ++row) {
            SizeType vid;
            std::memcpy(&vid, actual.data() + row * recordBytes, sizeof(vid));
            BOOST_REQUIRE_GE(vid, 0);
            BOOST_REQUIRE_LT(vid, count);
            BOOST_CHECK_EQUAL(std::memcmp(actual.data() + row * recordBytes + sizeof(vid),
                tags.data() + vid * 2, 2 * sizeof(std::uint32_t)), 0);
            BOOST_CHECK_EQUAL(std::memcmp(actual.data() + row * recordBytes + 12,
                data + vid * dimension, dimension * sizeof(float)), 0);
            if (row < pure) hSeen[vid] = true;
        }
    }
    BOOST_CHECK(hCut);
    BOOST_CHECK(oCut);
    BOOST_REQUIRE_GE(probeHead, 0);
    for (SizeType head = 0; head < heads; ++head) hSeen[typed->GetGlobalVID(head)] = true;
    BOOST_CHECK(std::all_of(hSeen.begin(), hSeen.end(), [](bool seen) { return seen; }));

    const int pure = disk->GetPostingVectorCount(probeHead, true);
    const int full = disk->GetPostingVectorCount(probeHead, false);
    std::string records;
    BOOST_REQUIRE(disk->GetWritePosting(nullptr, probeHead, records, false) == ErrorCode::Success);
    for (int pageLimit : {1, 2, 0}) {
        BOOST_REQUIRE(limited->SetParameter("PostingPageLimit",
            std::to_string(pageLimit).c_str(), "SearchSSDIndex") == ErrorCode::Success);
        BOOST_CHECK_EQUAL(typed->GetOptions()->m_searchPostingPageLimit, pageLimit);
        for (int mode = 0; mode < 4; ++mode) {
            SPANN::ExtraWorkSpace workspace;
            disk->InitWorkSpace(&workspace);
            workspace.m_postingIDs = {probeHead};
            std::uint32_t tag = 7;
            workspace.m_useHybridPure = mode == 0;
            if (mode == 0 || mode == 2) {
                workspace.m_queryTags = &tag;
                workspace.m_numQueryTags = 1;
            }
            Cache::DNFPredicate dnf;
            if (mode == 3) {
                Cache::DNFClause clause;
                clause.lits.push_back({1, 100, Cache::DNF_GE, 1});
                clause.lits.push_back({1, 300, Cache::DNF_LE, 1});
                dnf.clauses.push_back(clause);
                workspace.m_dnf = &dnf;
            }
            workspace.m_scanFullPostingForFilter = mode >= 2;
            COMMON::QueryResultSet<float> results(data, count);
            SPANN::SearchStats stats{};
            BOOST_REQUIRE(disk->SearchIndex(&workspace, results, headIndex, &stats,
                nullptr, nullptr) == ErrorCode::Success);
            BOOST_REQUIRE_EQUAL(workspace.m_postingReadRanges.size(), 1);
            const auto& range = workspace.m_postingReadRanges.front();
            BOOST_CHECK_EQUAL(stats.m_diskAccessCount, range.m_readPageCount);
            BOOST_CHECK_EQUAL(range.m_scanBegin, mode == 0 ? 0 : pure);
            BOOST_CHECK_LE(range.m_scanEnd, mode == 0 ? pure : full);
            if (pageLimit > 0) {
                BOOST_CHECK_LE(range.m_readPageCount, pageLimit);
                BOOST_CHECK_LT(range.m_scanEnd, mode == 0 ? pure : full);
            } else BOOST_CHECK_EQUAL(range.m_scanEnd, mode == 0 ? pure : full);
            std::set<SizeType> expected, actual;
            for (int row = range.m_scanBegin; row < range.m_scanEnd; ++row) {
                SizeType vid;
                std::memcpy(&vid, records.data() + row * recordBytes, sizeof(vid));
                if (mode != 3 || (vid >= 100 && vid <= 300)) expected.insert(vid);
            }
            for (int rank = 0; rank < results.GetResultNum(); ++rank)
                if (results.GetResult(rank)->VID >= 0) actual.insert(results.GetResult(rank)->VID);
            BOOST_CHECK_EQUAL_COLLECTIONS(actual.begin(), actual.end(), expected.begin(), expected.end());
        }
    }
    auto rescued = build("rescue", true, 1, 0, true);
    auto rescueUncut = build("rescue_uncut", true, 0, 0, true);
    auto rescueOriginal = build("rescue_original", false, 1, 0, true);
    auto* rescuedTyped = dynamic_cast<SPANN::Index<float>*>(rescued.get());
    auto* rescueUncutTyped = dynamic_cast<SPANN::Index<float>*>(rescueUncut.get());
    auto* rescueOriginalTyped = dynamic_cast<SPANN::Index<float>*>(rescueOriginal.get());
    BOOST_REQUIRE(rescuedTyped && rescueUncutTyped && rescueOriginalTyped);
    const auto rescueDisk = rescuedTyped->GetDiskIndex();
    const auto rescueHeads = rescuedTyped->GetMemoryIndex();
    const SizeType denseHeads = rescueHeads->GetNumSamples();
    BOOST_REQUIRE_EQUAL(denseHeads, 8);
    std::vector<unsigned> normalCopies(count, 0), finalCopies(count, 0);
    std::vector<bool> denseIsHead(count, false);
    std::vector<std::pair<float, SizeType>> nearest(count, {MaxDist, MaxSize});
    std::vector<std::string> originals(denseHeads);
    for (SizeType head = 0; head < denseHeads; ++head) {
        BOOST_REQUIRE_EQUAL(rescuedTyped->GetGlobalVID(head), rescueUncutTyped->GetGlobalVID(head));
        BOOST_REQUIRE_EQUAL(rescuedTyped->GetGlobalVID(head), rescueOriginalTyped->GetGlobalVID(head));
        denseIsHead[rescuedTyped->GetGlobalVID(head)] = true;
        BOOST_REQUIRE(rescueUncutTyped->GetDiskIndex()->GetWritePosting(
            nullptr, head, originals[head], false) == ErrorCode::Success);
        const int uncutPure = rescueUncutTyped->GetDiskIndex()->GetPostingVectorCount(head, true);
        for (int row = 0; row < uncutPure; ++row) {
            SizeType vid;
            std::memcpy(&vid, originals[head].data() + row * recordBytes, sizeof(vid));
            if (row < 29) ++normalCopies[vid];
            nearest[vid] = (std::min)(nearest[vid], std::make_pair(
                rescueHeads->ComputeDistance(data + vid * dimension, rescueHeads->GetSample(head)), head));
        }
    }
    std::uint64_t generation = 0;
    const auto* rescueOptions = rescuedTyped->GetOptions();
    BOOST_REQUIRE(Helper::Convert::ConvertStringTo<std::uint64_t>(
        rescueOptions->m_limitedTagGenerationFingerprint.c_str(), generation));
    SPANN::LimitedTagSupport rescueSupport;
    std::string supportError;
    BOOST_REQUIRE(rescueSupport.Load((directory / "rescue" / rescueOptions->m_limitedTagSupportFile).string(),
        denseHeads, 1, 1, 0, 2, generation, &supportError));
    BOOST_CHECK_EQUAL(rescueSupport.ExtraSupportCount(), 0U);
    BOOST_CHECK_EQUAL(rescueOptions->Schema().text, "categorical,numeric");
    unsigned rescuedRecords = 0;
    SizeType rescueProbe = -1;
    for (SizeType head = 0; head < denseHeads; ++head) {
        const int h = rescueDisk->GetPostingVectorCount(head, true);
        const int total = rescueDisk->GetPostingVectorCount(head, false);
        const int normalH = (std::min)(29,
            rescueUncutTyped->GetDiskIndex()->GetPostingVectorCount(head, true));
        BOOST_CHECK_GE(h, normalH);
        BOOST_CHECK_LE(total - h, 29);
        std::string actual, baseline;
        BOOST_REQUIRE(rescueDisk->GetWritePosting(nullptr, head, actual, false) == ErrorCode::Success);
        BOOST_REQUIRE(rescueOriginalTyped->GetDiskIndex()->GetWritePosting(
            nullptr, head, baseline, false) == ErrorCode::Success);
        BOOST_CHECK_EQUAL(actual.substr(h * recordBytes), baseline);
        BOOST_CHECK_EQUAL(actual.substr(0, normalH * recordBytes),
            originals[head].substr(0, normalH * recordBytes));
        std::pair<float, SizeType> previous{-1, -1};
        for (int row = 0; row < total; ++row) {
            const char* record = actual.data() + row * recordBytes;
            SizeType vid;
            std::memcpy(&vid, record, sizeof(vid));
            BOOST_REQUIRE_GE(vid, 0);
            BOOST_REQUIRE_LT(vid, count);
            BOOST_CHECK(!denseIsHead[vid]);
            BOOST_CHECK_EQUAL(std::memcmp(record + sizeof(vid), tags.data() + vid * 2, 8), 0);
            BOOST_CHECK_EQUAL(std::memcmp(record + 12, data + vid * dimension, dimension * sizeof(float)), 0);
            if (row >= h) continue;
            ++finalCopies[vid];
            BOOST_CHECK(rescueSupport.Supports(head, tags[vid * 2]));
            if (row < normalH) continue;
            ++rescuedRecords;
            rescueProbe = head;
            BOOST_CHECK_EQUAL(normalCopies[vid], 0U);
            BOOST_CHECK_EQUAL(nearest[vid].second, head);
            const auto current = std::make_pair(nearest[vid].first, vid);
            BOOST_CHECK(previous <= current);
            previous = current;
        }
    }
    unsigned zeroBefore = 0, zeroAfter = 0;
    for (SizeType vid = 0; vid < count; ++vid) {
        if (denseIsHead[vid]) { BOOST_CHECK_EQUAL(finalCopies[vid], 0U); continue; }
        zeroBefore += normalCopies[vid] == 0;
        zeroAfter += finalCopies[vid] == 0;
        BOOST_CHECK_EQUAL(finalCopies[vid], normalCopies[vid] == 0 ? 1U : normalCopies[vid]);
    }
    BOOST_CHECK_GT(zeroBefore, 0U);
    BOOST_CHECK_EQUAL(zeroAfter, 0U);
    BOOST_CHECK_EQUAL(rescuedRecords, zeroBefore);
    BOOST_REQUIRE_GE(rescueProbe, 0);
    SPANN::ExtraWorkSpace rescueWorkspace;
    rescueDisk->InitWorkSpace(&rescueWorkspace);
    rescueWorkspace.m_postingIDs = {rescueProbe};
    rescueWorkspace.m_useHybridPure = true;
    COMMON::QueryResultSet<float> rescueResults(data, count);
    SPANN::SearchStats rescueStats{};
    BOOST_REQUIRE(rescueDisk->SearchIndex(&rescueWorkspace, rescueResults, rescueHeads,
        &rescueStats, nullptr, nullptr) == ErrorCode::Success);
    BOOST_REQUIRE_EQUAL(rescueWorkspace.m_postingReadRanges.size(), 1);
    const auto& rescuedRange = rescueWorkspace.m_postingReadRanges.front();
    BOOST_CHECK_LE(rescuedRange.m_readPageCount, 1);
    BOOST_CHECK_LE(rescuedRange.m_scanEnd, 29);
    BOOST_CHECK_LT(rescuedRange.m_scanEnd, rescueDisk->GetPostingVectorCount(rescueProbe, true));
    const auto exportedPath = directory / "rescue_export";
    BOOST_REQUIRE(rescued->SaveIndex(exportedPath.string()) == ErrorCode::Success);
    std::shared_ptr<VectorIndex> exported;
    BOOST_REQUIRE(VectorIndex::LoadIndex(exportedPath.string(), exported) == ErrorCode::Success);
    auto* exportedTyped = dynamic_cast<SPANN::Index<float>*>(exported.get());
    BOOST_REQUIRE(exportedTyped);
    for (SizeType head = 0; head < denseHeads; ++head) {
        std::string actual, saved;
        BOOST_REQUIRE(rescueDisk->GetWritePosting(nullptr, head, actual, false) == ErrorCode::Success);
        BOOST_REQUIRE(exportedTyped->GetDiskIndex()->GetWritePosting(
            nullptr, head, saved, false) == ErrorCode::Success);
        BOOST_CHECK_EQUAL(actual, saved);
        BOOST_CHECK_EQUAL(rescueDisk->GetPostingVectorCount(head, true),
            exportedTyped->GetDiskIndex()->GetPostingVectorCount(head, true));
    }
}

BOOST_AUTO_TEST_CASE(StaticLimitedTagOExpansionFixedHeads)
{
    constexpr SizeType count = 512;
    constexpr DimensionType dimension = 16;
    const std::filesystem::path directory = "limited_tag_o_expansion_index";
    struct Cleanup
    {
        std::filesystem::path path;
        ~Cleanup() { std::error_code error; std::filesystem::remove_all(path, error); }
    } cleanup{directory};
    ByteArray bytes = ByteArray::Alloc(static_cast<size_t>(count) * dimension * sizeof(float));
    auto* data = reinterpret_cast<float*>(bytes.Data());
    std::vector<std::uint32_t> tags(static_cast<size_t>(count));
    for (SizeType row = 0; row < count; ++row)
    {
        tags[static_cast<size_t>(row)] = 1000 + row;
        for (DimensionType column = 0; column < dimension; ++column)
            data[static_cast<size_t>(row) * dimension + column] =
                static_cast<float>(row * (column + 1));
    }
    auto vectors = std::make_shared<BasicVectorSet>(
        bytes, VectorValueType::Float, dimension, count);
    for (const std::string ratio : {"0.25", "1", "0.015625"})
    {
        const bool allHeads = ratio == "1";
        const bool overPageBudget = ratio == "0.015625";
        std::filesystem::remove_all(directory);
        std::filesystem::create_directory(directory);
        auto index = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
        const auto set = [&](const char* section, const char* name, const std::string& value) {
            BOOST_REQUIRE(index->SetParameter(name, value.c_str(), section) == ErrorCode::Success);
        };
        set("Base", "DistCalcMethod", "L2");
        set("Base", "IndexAlgoType", "BKT");
        set("Base", "IndexDirectory", directory.string());
        set("SelectHead", "isExecute", "true");
        set("SelectHead", "SelectHeadType", "Random");
        set("SelectHead", "Ratio", allHeads ? "0.999999" : ratio);
        set("SelectHead", "MinHeadsPerTag", "0");
        set("SelectHead", "NumberOfThreads", "1");
        set("SelectHead", "SelectSecondLevel", "true");
        set("SelectHead", "SecondLevelHierarchyLevels", "3");
        set("SelectHead", "SecondLevelReplicaCount", "2");
        set("SelectHead", "BuildH1Graph", "false");
        set("BuildHead", "isExecute", "true");
        set("BuildHead", "NumberOfThreads", "1");
        set("BuildHead", "NeighborhoodSize", "16");
        set("BuildHead", "RefineIterations", "1");
        set("BuildHead", "BKTLambdaFactor", "1");
        set("BuildSSDIndex", "isExecute", "true");
        set("BuildSSDIndex", "BuildSsdIndex", "true");
        set("BuildSSDIndex", "Storage", "STATIC");
        set("BuildSSDIndex", "NumberOfThreads", "1");
        set("BuildSSDIndex", "InternalResultNum", "16");
        set("BuildSSDIndex", "MaxCheck", "1024");
        set("BuildSSDIndex", "ReplicaCount", "2");
        set("BuildSSDIndex", "PostingPageLimit", "1");
        set("BuildSSDIndex", "PostingVectorLimit", overPageBudget ? "0" : "118");
        set("BuildSSDIndex", "SearchPostingPageLimit", "1");
        set("BuildSSDIndex", "TailReplicaCount", "0");
        set("BuildSSDIndex", "UnfilterTailBufferLength", "0");
        set("BuildSSDIndex", "CrossEdges", "0");
        set("BuildSSDIndex", "ExcludeHead", "true");
        set("BuildSSDIndex", "NumTagsPerVec", "1");
        set("BuildSSDIndex", "StaticACLTagCols", "1");
        set("BuildSSDIndex", "EnableLimitedTagPosting", "true");
        set("BuildSSDIndex", "LimitedTagSlotsPerHead", "2");
        set("BuildSSDIndex", "LimitedTagMinHeadCount", "16");
        set("BuildSSDIndex", "EnableLimitedTagSupportExpansion", "true");
        auto* spann = dynamic_cast<SPANN::ISPANNIndex*>(index.get());
        BOOST_REQUIRE(spann != nullptr);
        spann->SetVectorTags(tags.data(), count, 1);
        if (!allHeads && !overPageBudget)
        {
            set("SelectHead", "MinHeadsPerTag", "1");
            BOOST_CHECK(index->BuildIndex(vectors, nullptr, true, false, false) ==
                ErrorCode::FailedParseValue);
            set("SelectHead", "MinHeadsPerTag", "0");
        }
        const auto built = index->BuildIndex(vectors, nullptr, true, false, false);
        if (overPageBudget)
        {
            BOOST_CHECK(built != ErrorCode::Success);
            continue;
        }
        BOOST_REQUIRE(built == ErrorCode::Success);
        BOOST_REQUIRE(index->SaveIndex(directory.string()) == ErrorCode::Success);
        index.reset();
        BOOST_REQUIRE(VectorIndex::LoadIndex(directory.string(), index) == ErrorCode::Success);
        auto* loaded = dynamic_cast<SPANN::Index<float>*>(index.get());
        BOOST_REQUIRE(loaded != nullptr);
        BOOST_REQUIRE(loaded->HasRoutingOnlyHierarchy());
        const auto headIndex = loaded->GetMemoryIndex();
        const auto disk = loaded->GetDiskIndex();
        const auto* options = loaded->GetOptions();
        const SizeType heads = headIndex->GetNumSamples();
        BOOST_CHECK_EQUAL(heads, allHeads ? count : count / 4);
        BOOST_CHECK_EQUAL(options->m_minHeadsPerTag, 0);
        BOOST_CHECK(options->m_enableLimitedTagSupportExpansion);
        std::uint64_t generation = 0;
        BOOST_REQUIRE(Helper::Convert::ConvertStringTo<std::uint64_t>(
            options->m_limitedTagGenerationFingerprint.c_str(), generation));
        SPANN::LimitedTagSupport support;
        std::string error;
        BOOST_REQUIRE(support.Load((directory / options->m_limitedTagSupportFile).string(),
            heads, 2, 16, 0, 1, generation, &error));
        BOOST_CHECK(support.HasExpansion());
        if (allHeads) BOOST_CHECK_EQUAL(support.ExtraSupportCount(), 0U);
        else BOOST_CHECK_GT(support.ExtraSupportCount(), 0U);
        std::vector<Cache::PostingBitmask> signatures;
        BOOST_REQUIRE(SPANN::CollectHierarchyHeadSignatures(*headIndex, support, signatures));
        std::vector<unsigned> hCopies(static_cast<size_t>(count), 0);
        std::vector<bool> isHead(static_cast<size_t>(count), false);
        for (SizeType head = 0; head < heads; ++head)
        {
            const auto vid = loaded->GetGlobalVID(head);
            BOOST_REQUIRE_GE(vid, 0);
            BOOST_REQUIRE_LT(vid, count);
            isHead[static_cast<size_t>(vid)] = true;
        }
        const size_t stride = sizeof(SizeType) + sizeof(std::uint32_t) + dimension * sizeof(float);
        for (SizeType head = 0; head < heads; ++head)
        {
            const int pure = disk->GetPostingVectorCount(head, true);
            const int full = disk->GetPostingVectorCount(head, false);
            BOOST_REQUIRE_GE(pure, 0);
            BOOST_REQUIRE_GE(full, pure);
            std::string records;
            if (full != 0)
                BOOST_REQUIRE(disk->GetWritePosting(nullptr, head, records, false) == ErrorCode::Success);
            BOOST_REQUIRE_EQUAL(records.size(), static_cast<size_t>(full) * stride);
            std::unordered_set<std::uint32_t> originalTags;
            Cache::PostingBitmask expected;
            expected.Insert(support.OwnTag(head));
            for (int row = 0; row < full; ++row)
            {
                SizeType vid = -1;
                std::uint32_t tag = 0;
                std::memcpy(&vid, records.data() + static_cast<size_t>(row) * stride, sizeof(vid));
                std::memcpy(&tag, records.data() + static_cast<size_t>(row) * stride +
                    sizeof(vid), sizeof(tag));
                BOOST_REQUIRE_GE(vid, 0);
                BOOST_REQUIRE_LT(vid, count);
                BOOST_CHECK(!isHead[static_cast<size_t>(vid)]);
                BOOST_CHECK_EQUAL(tag, tags[static_cast<size_t>(vid)]);
                if (row < pure)
                {
                    BOOST_CHECK(support.Supports(head, tag));
                    ++hCopies[static_cast<size_t>(vid)];
                    expected.Insert(tag);
                }
                else originalTags.insert(tag);
            }
            for (std::size_t slot = 0; slot < support.ExtraTagCount(head); ++slot)
                BOOST_CHECK_EQUAL(originalTags.count(support.ExtraTagData(head)[slot]), 1U);
            for (int word = 0; word < Cache::PS_BITMASK_WORDS; ++word)
                BOOST_CHECK_EQUAL(signatures[static_cast<size_t>(head)].bits[word], expected.bits[word]);
        }
        for (SizeType vid = 0; vid < count; ++vid)
        {
            const auto copies = hCopies[static_cast<size_t>(vid)];
            if (isHead[static_cast<size_t>(vid)]) BOOST_CHECK_EQUAL(copies, 0U);
            else { BOOST_CHECK_GT(copies, 0U); BOOST_CHECK_LE(copies, 2U); }
        }
        set("SearchSSDIndex", "InternalResultNum", std::to_string(heads));
        set("SearchSSDIndex", "SecondLevelMaxCheck", "1024");
        for (SizeType vid = 0; vid < count; vid += 7)
        {
            VectorIndex::ThreadLocalSearchContext context;
            context.m_queryTags = {tags[static_cast<size_t>(vid)]};
            context.m_limitedTagMembershipEligible = true;
            context.m_limitedTagQueryValues = context.m_queryTags;
            VectorIndex::ThreadLocalSearchContextGuard guard(std::move(context));
            COMMON::QueryResultSet<float> results(data, 1);
            BOOST_REQUIRE(index->SearchIndex(results) == ErrorCode::Success);
            BOOST_CHECK_EQUAL(results.GetResult(0)->VID, vid);
        }
        COMMON::QueryResultSet<float> unfiltered(data + 37 * dimension, 1);
        BOOST_REQUIRE(index->SearchIndex(unfiltered) == ErrorCode::Success);
        BOOST_CHECK_EQUAL(unfiltered.GetResult(0)->VID, 37);
        BOOST_CHECK(index->DeleteIndex(static_cast<SizeType>(0)) != ErrorCode::Success);
    }
}

BOOST_AUTO_TEST_CASE(StaticLimitedTagBuildSearchReloadAndCorruption)
{
    constexpr SizeType baseCount = 256;
    constexpr DimensionType dimension = 128;
    constexpr int distinctTags = 4;
    const std::string indexDirectory =
        "static_limited_tag_index";
    const std::string randomIndexDirectory =
        "static_limited_tag_h2_random_index";
    std::filesystem::remove_all(indexDirectory);
    std::filesystem::remove_all(randomIndexDirectory);
    std::filesystem::create_directories(
        indexDirectory);
    std::filesystem::create_directories(
        randomIndexDirectory);

    ByteArray bytes = ByteArray::Alloc(
        sizeof(float) *
        static_cast<size_t>(baseCount) *
        dimension);
    auto* data = reinterpret_cast<float*>(
        bytes.Data());
    std::vector<std::uint32_t> tags(
        static_cast<size_t>(baseCount));
    for (SizeType row = 0; row < baseCount; ++row) {
        std::uint32_t mixed =
            static_cast<std::uint32_t>(row) +
            0x9e3779b9u;
        mixed ^= mixed >> 16;
        mixed *= 0x7feb352du;
        mixed ^= mixed >> 15;
        mixed *= 0x846ca68bu;
        mixed ^= mixed >> 16;
        tags[static_cast<size_t>(row)] =
            mixed % distinctTags;
        for (DimensionType dim = 0;
             dim < dimension; ++dim) {
            data[static_cast<size_t>(row) *
                     dimension +
                 dim] =
                static_cast<float>(
                    (row * (dim + 5) +
                     dim * 29) %
                    263) /
                263.0f;
        }
    }
    auto vectors =
        std::make_shared<BasicVectorSet>(
            bytes, VectorValueType::Float,
            dimension, baseCount);

    const auto configure =
        [&](const std::shared_ptr<VectorIndex>&
                target) {
            // Legacy SelectHead uses the C RNG; keep each fixture build independent.
            std::srand(1);
            const auto set =
                [&](const char* section,
                    const char* key,
                    const std::string& value) {
                    BOOST_REQUIRE(
                        target->SetParameter(
                            key, value.c_str(),
                            section) ==
                        ErrorCode::Success);
                };
            set("Base", "DistCalcMethod", "L2");
            set("Base", "IndexAlgoType", "BKT");
            set("Base", "ValueType", "Float");
            set("Base", "Dim",
                std::to_string(dimension));
            set("Base", "IndexDirectory",
                indexDirectory);
            set("SelectHead", "isExecute", "true");
            set("SelectHead", "SelectHeadType", "BKT");
            set("SelectHead", "Ratio", "0.25");
            set("SelectHead", "BKTLambdaFactor", "-1");
            set("SelectHead", "SelectThreshold", "20");
            set("SelectHead", "SplitFactor", "4");
            set("SelectHead", "SplitThreshold", "40");
            set("SelectHead", "NumberOfThreads", "1");
            set("BuildHead", "isExecute", "true");
            set("BuildHead", "NeighborhoodSize", "32");
            set("BuildHead", "RefineIterations", "2");
            set("BuildHead", "MaxCheck", "1024");
            set("BuildHead",
                "MaxCheckForRefineGraph", "1024");
            set("BuildHead", "BKTLambdaFactor", "-1");
            set("BuildHead", "NumberOfThreads", "1");
            set("BuildSSDIndex", "isExecute", "true");
            set("BuildSSDIndex", "BuildSsdIndex", "true");
            set("BuildSSDIndex", "Storage", "STATIC");
            set("BuildSSDIndex", "InternalResultNum", "32");
            set("BuildSSDIndex",
                "SearchInternalResultNum", "16");
            set("BuildSSDIndex", "NumberOfThreads", "2");
            set("BuildSSDIndex", "MaxCheck", "4096");
            set("BuildSSDIndex", "PostingPageLimit", "1");
            // This fixture verifies exhaustive scan/dedup, not a page-limited recall budget.
            set("BuildSSDIndex",
                "SearchPostingPageLimit", "0");
            set("BuildSSDIndex", "SSDIndexFileNum", "1");
            set("BuildSSDIndex", "ReplicaCount", "8");
            set("BuildSSDIndex", "RNGFactor", "100");
            set("BuildSSDIndex", "TailReplicaCount", "0");
            set("BuildSSDIndex",
                "UnfilterTailBufferLength", "0");
            set("BuildSSDIndex", "CrossEdges", "0");
            set("BuildSSDIndex", "ExcludeHead", "true");
            set("BuildSSDIndex", "NumTagsPerVec", "1");
            set("BuildSSDIndex",
                "StaticACLTagCols", "0");
            set("BuildSSDIndex",
                "EnableHybridDistance", "false");
            set("BuildSSDIndex",
                "EnableLimitedTagPosting", "true");
            set("BuildSSDIndex",
                "LimitedTagMinHeadCount", "8");
        };

    const auto verifyDefaultTwoSlots = [&]() {
        auto defaultIndex =
            VectorIndex::CreateInstance(
                IndexAlgoType::SPANN,
                VectorValueType::Float);
        BOOST_REQUIRE(defaultIndex != nullptr);
        configure(defaultIndex);
        BOOST_REQUIRE(
            defaultIndex->SetParameter(
                "SelectSecondLevel", "true",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_REQUIRE(
            defaultIndex->SetParameter(
                "Ratio", "0.25",
                "SelectHead") ==
            ErrorCode::Success);
        auto* defaultSPANN =
            dynamic_cast<SPANN::ISPANNIndex*>(
                defaultIndex.get());
        auto* defaultTyped =
            dynamic_cast<SPANN::Index<float>*>(
                defaultIndex.get());
        BOOST_REQUIRE(defaultSPANN != nullptr);
        BOOST_REQUIRE(defaultTyped != nullptr);
        BOOST_CHECK_EQUAL(
            defaultTyped->GetOptions()
                ->m_limitedTagSlotsPerHead,
            2);
        defaultSPANN->SetVectorTags(
            tags.data(), baseCount, 1);
        BOOST_REQUIRE(
            defaultIndex->BuildIndex(
                vectors, nullptr, true, false,
                false) == ErrorCode::Success);
        BOOST_REQUIRE(
            defaultIndex->SaveIndex(
                indexDirectory) ==
            ErrorCode::Success);

        const SizeType defaultHeadCount =
            defaultSPANN->GetMemoryIndex()
                ->GetNumSamples();
        std::ifstream secondLevelIDs(
            indexDirectory +
                "/SPTAGSecondLevelHeadVectorIDs.bin",
            std::ios::binary);
        BOOST_REQUIRE(secondLevelIDs.good());
        SizeType secondLevelCount = 0;
        DimensionType secondLevelIDDimension = 0;
        secondLevelIDs.read(
            reinterpret_cast<char*>(&secondLevelCount),
            sizeof(secondLevelCount));
        secondLevelIDs.read(
            reinterpret_cast<char*>(
                &secondLevelIDDimension),
            sizeof(secondLevelIDDimension));
        BOOST_REQUIRE(secondLevelIDs.good());
        BOOST_CHECK_GT(secondLevelCount, 0);
        BOOST_CHECK_LT(
            secondLevelCount, defaultHeadCount);
        BOOST_CHECK_EQUAL(
            secondLevelIDDimension, 1);
        std::vector<std::uint64_t> secondLevelHeadIDs(
            static_cast<size_t>(secondLevelCount));
        secondLevelIDs.read(
            reinterpret_cast<char*>(
                secondLevelHeadIDs.data()),
            static_cast<std::streamsize>(
                secondLevelHeadIDs.size() *
                sizeof(std::uint64_t)));
        BOOST_REQUIRE(secondLevelIDs.good());
        BOOST_CHECK(std::is_sorted(
            secondLevelHeadIDs.begin(),
            secondLevelHeadIDs.end()));

        std::ifstream secondLevelVectors(
            indexDirectory +
                "/SPTAGSecondLevelHeadVectors.bin",
            std::ios::binary);
        BOOST_REQUIRE(secondLevelVectors.good());
        SizeType secondLevelVectorCount = 0;
        DimensionType secondLevelVectorDimension = 0;
        secondLevelVectors.read(
            reinterpret_cast<char*>(
                &secondLevelVectorCount),
            sizeof(secondLevelVectorCount));
        secondLevelVectors.read(
            reinterpret_cast<char*>(
                &secondLevelVectorDimension),
            sizeof(secondLevelVectorDimension));
        BOOST_REQUIRE(secondLevelVectors.good());
        BOOST_CHECK_EQUAL(
            secondLevelVectorCount, secondLevelCount);
        BOOST_CHECK_EQUAL(
            secondLevelVectorDimension, dimension);
        std::vector<float> secondLevelVectorData(
            static_cast<size_t>(secondLevelVectorCount) *
            dimension);
        secondLevelVectors.read(
            reinterpret_cast<char*>(
                secondLevelVectorData.data()),
            static_cast<std::streamsize>(
                secondLevelVectorData.size() *
                sizeof(float)));
        BOOST_REQUIRE(secondLevelVectors.good());
        for (SizeType h2 = 0;
             h2 < secondLevelCount; ++h2) {
            const auto h1 = secondLevelHeadIDs[
                static_cast<size_t>(h2)];
            BOOST_REQUIRE_LT(
                h1,
                static_cast<std::uint64_t>(
                    defaultHeadCount));
            const auto* h1Vector =
                reinterpret_cast<const float*>(
                    defaultSPANN->GetMemoryIndex()
                        ->GetSample(
                            static_cast<SizeType>(h1)));
            BOOST_REQUIRE(h1Vector != nullptr);
            BOOST_CHECK_EQUAL_COLLECTIONS(
                secondLevelVectorData.begin() +
                    static_cast<size_t>(h2) *
                        dimension,
                secondLevelVectorData.begin() +
                    (static_cast<size_t>(h2) + 1) *
                        dimension,
                h1Vector, h1Vector + dimension);
        }

        auto randomIndex =
            VectorIndex::CreateInstance(
                IndexAlgoType::SPANN,
                VectorValueType::Float);
        BOOST_REQUIRE(randomIndex != nullptr);
        configure(randomIndex);
        BOOST_REQUIRE(
            randomIndex->SetParameter(
                "IndexDirectory",
                randomIndexDirectory.c_str(),
                "Base") ==
            ErrorCode::Success);
        BOOST_REQUIRE(
            randomIndex->SetParameter(
                "SelectHeadType", "Random",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_REQUIRE(
            randomIndex->SetParameter(
                "SelectSecondLevel", "true",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_REQUIRE(
            randomIndex->SetParameter(
                "Ratio", "0.25",
                "SelectHead") ==
            ErrorCode::Success);
        auto* randomSPANN =
            dynamic_cast<SPANN::ISPANNIndex*>(
                randomIndex.get());
        BOOST_REQUIRE(randomSPANN != nullptr);
        randomSPANN->SetVectorTags(
            tags.data(), baseCount, 1);
        BOOST_REQUIRE(
            randomIndex->BuildIndex(
                vectors, nullptr, true, false,
                false) == ErrorCode::Success);
        std::ifstream randomIDs(
            randomIndexDirectory +
                "/SPTAGSecondLevelHeadVectorIDs.bin",
            std::ios::binary);
        SizeType randomH2Count = 0;
        DimensionType randomIDDimension = 0;
        randomIDs.read(
            reinterpret_cast<char*>(&randomH2Count),
            sizeof(randomH2Count));
        randomIDs.read(
            reinterpret_cast<char*>(
                &randomIDDimension),
            sizeof(randomIDDimension));
        std::vector<std::uint64_t> randomH2IDs(
            static_cast<size_t>(randomH2Count));
        randomIDs.read(
            reinterpret_cast<char*>(
                randomH2IDs.data()),
            static_cast<std::streamsize>(
                randomH2IDs.size() *
                sizeof(std::uint64_t)));
        BOOST_REQUIRE(randomIDs.good());
        BOOST_CHECK(std::is_sorted(
            randomH2IDs.begin(),
            randomH2IDs.end()));
        std::ifstream randomVectors(
            randomIndexDirectory +
                "/SPTAGSecondLevelHeadVectors.bin",
            std::ios::binary);
        SizeType randomVectorCount = 0;
        DimensionType randomVectorDimension = 0;
        randomVectors.read(
            reinterpret_cast<char*>(
                &randomVectorCount),
            sizeof(randomVectorCount));
        randomVectors.read(
            reinterpret_cast<char*>(
                &randomVectorDimension),
            sizeof(randomVectorDimension));
        std::vector<float> randomH2Vectors(
            static_cast<size_t>(randomVectorCount) *
            dimension);
        randomVectors.read(
            reinterpret_cast<char*>(
                randomH2Vectors.data()),
            static_cast<std::streamsize>(
                randomH2Vectors.size() *
                sizeof(float)));
        BOOST_REQUIRE(randomVectors.good());
        for (SizeType h2 = 0;
             h2 < randomH2Count; ++h2) {
            const auto h1 = randomH2IDs[
                static_cast<size_t>(h2)];
            const auto* h1Vector =
                reinterpret_cast<const float*>(
                    randomSPANN->GetMemoryIndex()
                        ->GetSample(
                            static_cast<SizeType>(h1)));
            BOOST_REQUIRE(h1Vector != nullptr);
            BOOST_CHECK_EQUAL_COLLECTIONS(
                randomH2Vectors.begin() +
                    static_cast<size_t>(h2) *
                        dimension,
                randomH2Vectors.begin() +
                    (static_cast<size_t>(h2) + 1) *
                        dimension,
                h1Vector, h1Vector + dimension);
        }
        randomIndex.reset();

        auto disabledIndex =
            VectorIndex::CreateInstance(
                IndexAlgoType::SPANN,
                VectorValueType::Float);
        BOOST_REQUIRE(disabledIndex != nullptr);
        configure(disabledIndex);
        BOOST_REQUIRE(
            disabledIndex->SetParameter(
                "IndexDirectory",
                randomIndexDirectory.c_str(),
                "Base") ==
            ErrorCode::Success);
        auto* disabledSPANN =
            dynamic_cast<SPANN::ISPANNIndex*>(
                disabledIndex.get());
        BOOST_REQUIRE(disabledSPANN != nullptr);
        disabledSPANN->SetVectorTags(
            tags.data(), baseCount, 1);
        BOOST_REQUIRE(
            disabledIndex->BuildIndex(
                vectors, nullptr, true, false,
                false) == ErrorCode::Success);
        BOOST_CHECK(
            !std::filesystem::exists(
                randomIndexDirectory +
                "/SPTAGSecondLevelHeadVectors.bin"));
        BOOST_CHECK(
            !std::filesystem::exists(
                randomIndexDirectory +
                "/SPTAGSecondLevelHeadVectorIDs.bin"));
        disabledIndex.reset();

        SPANN::Options removedOptions;
        for (const char* name : {"ACLCols", "HierLevelWidths", "PivotForceNodeCount",
                                 "DisablePivotEstimator", "RoutingCols", "PerVectorTagsFile"}) {
            BOOST_CHECK(removedOptions.SetParameter("SelectHead", name, "1") ==
                        ErrorCode::FailedParseValue);
        }
        BOOST_CHECK(removedOptions.SetParameter("SelectHead", "SelectHeadType", "PerTagBKT") ==
                    ErrorCode::FailedParseValue);
        std::filesystem::remove_all(
            randomIndexDirectory);

        std::uint64_t defaultGeneration = 0;
        BOOST_REQUIRE(
            Helper::Convert::ConvertStringTo<
                std::uint64_t>(
                defaultTyped->GetOptions()
                    ->m_limitedTagGenerationFingerprint
                    .c_str(),
                defaultGeneration));
        SPANN::LimitedTagSupport defaultSupport;
        std::string defaultSupportError;
        BOOST_REQUIRE(
            defaultSupport.Load(
                indexDirectory +
                    "/limited_tag_support.bin",
                defaultHeadCount, 2, 8,
                0, 1,
                defaultGeneration,
                &defaultSupportError));
        for (SizeType head = 0;
             head < defaultHeadCount; ++head) {
            BOOST_CHECK_EQUAL(
                defaultSupport.HeadTags(head).size(),
                2);
        }

        std::ifstream defaultLoader(
            indexDirectory +
                "/indexloader.ini");
        BOOST_REQUIRE(defaultLoader.good());
        const std::string defaultConfig(
            (std::istreambuf_iterator<char>(
                 defaultLoader)),
            std::istreambuf_iterator<char>());
        BOOST_CHECK(
            defaultConfig.find(
                "LimitedTagSlotsPerHead=2") !=
            std::string::npos);
        BOOST_CHECK(
            defaultConfig.find(
                "HierarchyEnabled=true") !=
            std::string::npos);
        BOOST_CHECK(
            defaultConfig.find(
                "Ratio=0.250000") !=
            std::string::npos);
        BOOST_CHECK(
            defaultConfig.find(
                "HierarchyRouteSelectivityThreshold=") ==
            std::string::npos);
        BOOST_CHECK(
            defaultConfig.find(
                "HierarchyInitialProbeRatio=1.000000") !=
            std::string::npos);
        BOOST_CHECK(
            defaultConfig.find(
                "HierarchySignatureMinSelectivity") ==
            std::string::npos);
        BOOST_CHECK(
            defaultConfig.find(
                "HierarchySignatureMaxSelectivity") ==
            std::string::npos);
        BOOST_CHECK(
            defaultConfig.find(
                "HierarchyMaxCheck=112") !=
            std::string::npos);
        BOOST_CHECK(defaultConfig.find("HierarchyGraphSignaturePruning") == std::string::npos);
        defaultLoader.close();
        defaultIndex.reset();

        std::shared_ptr<VectorIndex>
            reloadedDefault;
        BOOST_REQUIRE(
            VectorIndex::LoadIndex(
                indexDirectory,
                reloadedDefault) ==
            ErrorCode::Success);
        auto* reloadedTyped =
            dynamic_cast<SPANN::Index<float>*>(
                reloadedDefault.get());
        BOOST_REQUIRE(reloadedTyped != nullptr);
        BOOST_CHECK_EQUAL(
            reloadedTyped->GetOptions()
                ->m_limitedTagSlotsPerHead,
            2);
        BOOST_CHECK_EQUAL(
            reloadedTyped->GetOptions()
                ->m_secondLevelMaxCheck,
            112);
        BOOST_CHECK(reloadedDefault->SetParameter(
            "SecondLevelGraphSignaturePruning", "true",
            "SearchSSDIndex") == ErrorCode::FailedParseValue);
        BOOST_CHECK_SMALL(
            reloadedTyped->GetOptions()
                    ->m_secondLevelInitialProbeRatio -
                1.0,
            1e-12);
        reloadedDefault.reset();
        std::filesystem::remove_all(
            indexDirectory);
        std::filesystem::create_directories(
            indexDirectory);
    };
    verifyDefaultTwoSlots();

    for (bool compactAtBuild : {false, true})
    {
        auto compactIndex = VectorIndex::CreateInstance(
            IndexAlgoType::SPANN, VectorValueType::Float);
        BOOST_REQUIRE(compactIndex != nullptr);
        configure(compactIndex);
        for (const auto& option : std::vector<std::pair<std::string, std::string>>{
                 {"SelectSecondLevel", "true"},
                 {"SecondLevelHierarchyLevels", "3"},
                 {"Ratio", "0.5"},
                 {"SecondLevelReplicaCount", "2"},
                 {"BuildH1Graph", "false"},
                 {"CompactHierarchyVectors", "false"}})
            BOOST_REQUIRE(compactIndex->SetParameter(option.first.c_str(),
                option.second.c_str(), "SelectHead") == ErrorCode::Success);
        auto* spann = dynamic_cast<SPANN::ISPANNIndex*>(compactIndex.get());
        BOOST_REQUIRE(spann != nullptr);
        spann->SetVectorTags(tags.data(), baseCount, 1);
        if (compactAtBuild)
        {
            BOOST_REQUIRE(compactIndex->SetParameter(
                "CompactHierarchyVectors", "true", "SelectHead") == ErrorCode::Success);
            BOOST_CHECK(compactIndex->BuildIndex(
                vectors, nullptr, true, false, false) == ErrorCode::FailedParseValue);
            BOOST_REQUIRE(compactIndex->SetParameter(
                "CompactHierarchyVectors", "false", "SelectHead") == ErrorCode::Success);
        }
        BOOST_REQUIRE(compactIndex->BuildIndex(
            vectors, nullptr, true, false, false) == ErrorCode::Success);
        BOOST_REQUIRE(spann->HasRoutingOnlyHierarchy());
        BOOST_CHECK(std::filesystem::exists(indexDirectory + "/SPTAGHeadVectors.bin"));
        BOOST_CHECK(std::filesystem::exists(indexDirectory + "/SPTAGSecondLevelHeadVectors.bin"));
        BOOST_CHECK(!std::filesystem::exists(indexDirectory + "/SPTAGHeadVectors.bin.owned"));
        BOOST_CHECK(!std::filesystem::exists(indexDirectory + "/SPTAGSecondLevelHeadVectors.bin.level2"));
        BOOST_CHECK(!std::filesystem::exists(indexDirectory + "/SecondLevelHeadIndex/head_node_meta.bin"));
        BOOST_CHECK(compactIndex->SetParameter(
            "HeadNavigationMode", "H2Only",
            "SearchSSDIndex") == ErrorCode::FailedParseValue);
        BOOST_REQUIRE(compactIndex->SetParameter(
            "InternalResultNum", "16", "SearchSSDIndex") == ErrorCode::Success);
        const auto headIndex = spann->GetMemoryIndex();
        const SizeType headCount = headIndex->GetNumSamples();
        std::vector<float> logical(static_cast<size_t>(headCount) * dimension);
        for (SizeType head = 0; head < headCount; ++head)
            std::memcpy(logical.data() + static_cast<size_t>(head) * dimension,
                headIndex->GetSample(head), static_cast<size_t>(dimension) * sizeof(float));
        const auto search = [&](const std::shared_ptr<VectorIndex>& index) {
            std::vector<std::pair<SizeType, float>> results;
            for (SizeType query : {0, 37, 127})
            {
                COMMON::QueryResultSet<float> found(
                    data + static_cast<size_t>(query) * dimension, 10);
                BOOST_REQUIRE(index->SearchIndex(found) == ErrorCode::Success);
                for (int rank = 0; rank < 10; ++rank)
                    results.emplace_back(found.GetResult(rank)->VID, found.GetResult(rank)->Dist);
            }
            return results;
        };
        const auto before = search(compactIndex);
        BOOST_REQUIRE(spann->CompactHierarchyVectors() == ErrorCode::Success);
        BOOST_REQUIRE(spann->CompactHierarchyVectors() == ErrorCode::Success);
        BOOST_CHECK(!spann->HasRoutingOnlyHierarchy());
        BOOST_REQUIRE(compactIndex->SaveIndex(indexDirectory) == ErrorCode::Success);
        const std::string ownedPath = indexDirectory + "/SPTAGHeadVectors.bin.owned";
        const std::string descriptor = indexDirectory + "/HeadIndex/head_metaonly.bin";
        BOOST_CHECK_EQUAL(std::filesystem::file_size(descriptor), 36U);
        BOOST_CHECK(!std::filesystem::exists(indexDirectory + "/SPTAGHeadVectors.bin"));
        BOOST_CHECK(!std::filesystem::exists(indexDirectory + "/SPTAGSecondLevelHeadVectors.bin"));
        BOOST_CHECK(!std::filesystem::exists(
            indexDirectory + "/SPTAGSecondLevelHeadVectors.bin.level2"));
        SizeType physical = 0;
        for (const std::string& file : {
                 ownedPath,
                 indexDirectory + "/SPTAGSecondLevelHeadVectors.bin.owned",
                 indexDirectory + "/SecondLevelHeadIndex/vectors.bin"})
        {
            std::ifstream input(file, std::ios::binary);
            SizeType count = 0;
            input.read(reinterpret_cast<char*>(&count), sizeof(count));
            BOOST_REQUIRE(input.good());
            physical += count;
        }
        BOOST_CHECK_EQUAL(physical, headCount);
        std::shared_ptr<VectorIndex> loaded;
        BOOST_REQUIRE(VectorIndex::LoadIndex(indexDirectory, loaded) == ErrorCode::Success);
        auto* loadedSPANN = dynamic_cast<SPANN::ISPANNIndex*>(loaded.get());
        BOOST_REQUIRE(loadedSPANN != nullptr);
        for (SizeType head = 0; head < headCount; ++head)
            BOOST_CHECK_EQUAL(std::memcmp(
                loadedSPANN->GetMemoryIndex()->GetSample(head),
                logical.data() + static_cast<size_t>(head) * dimension,
                static_cast<size_t>(dimension) * sizeof(float)), 0);
        const auto after = search(loaded);
        BOOST_REQUIRE_EQUAL(before.size(), after.size());
        for (size_t rank = 0; rank < before.size(); ++rank)
        {
            BOOST_CHECK_EQUAL(before[rank].first, after[rank].first);
            BOOST_CHECK_EQUAL(before[rank].second, after[rank].second);
        }
        if (!compactAtBuild)
        {
            const std::string materialized = indexDirectory + ".materialized";
            std::filesystem::remove_all(materialized);
            const auto readBytes = [](const std::string& path) {
                std::ifstream input(path, std::ios::binary);
                BOOST_REQUIRE(input.good());
                return std::vector<char>(
                    (std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
            };
            const auto originalMetadata = readBytes(indexDirectory + "/HeadIndex/head_node_meta.bin");
            BOOST_CHECK(loadedSPANN->MaterializeHierarchyVectors(indexDirectory) != ErrorCode::Success);
            BOOST_CHECK(loadedSPANN->MaterializeHierarchyVectors(indexDirectory + "/nested") !=
                ErrorCode::Success);
            BOOST_CHECK(!std::filesystem::exists(indexDirectory + "/nested"));
            BOOST_REQUIRE(loadedSPANN->MaterializeHierarchyVectors(materialized) == ErrorCode::Success);
            BOOST_CHECK(loadedSPANN->MaterializeHierarchyVectors(materialized) != ErrorCode::Success);
            BOOST_CHECK(readBytes(indexDirectory + "/HeadIndex/head_node_meta.bin") == originalMetadata);
            BOOST_CHECK(readBytes(materialized + "/HeadIndex/head_node_meta.bin") == originalMetadata);
            BOOST_CHECK(std::filesystem::exists(ownedPath));
            BOOST_CHECK(!std::filesystem::exists(indexDirectory + "/SPTAGHeadVectors.bin"));
            for (const auto& unchanged : std::vector<std::string>{
                     "SPTAGHeadVectorIDs.bin", "SPTAGSecondLevelHeadVectorIDs.bin",
                     "SPTAGSecondLevelHeadVectorIDs.bin.level2",
                     "second_level_head_postings.bin", "second_level_head_postings.bin.level2",
                     loadedSPANN->GetOptions()->m_ssdIndex})
                BOOST_CHECK(readBytes(indexDirectory + "/" + unchanged) ==
                    readBytes(materialized + "/" + unchanged));
            SizeType materializedRows = 0;
            for (const auto& file : std::vector<std::string>{
                     "SPTAGHeadVectors.bin", "SPTAGSecondLevelHeadVectors.bin",
                     "SecondLevelHeadIndex/vectors.bin"})
            {
                std::ifstream input(materialized + "/" + file, std::ios::binary);
                SizeType count = 0;
                input.read(reinterpret_cast<char*>(&count), sizeof(count));
                BOOST_REQUIRE(input.good());
                if (file == "SPTAGHeadVectors.bin") BOOST_CHECK_EQUAL(count, headCount);
                materializedRows += count;
            }
            BOOST_CHECK_GT(materializedRows, headCount);
            for (const char* absent : {"SPTAGHeadVectors.bin.owned",
                     "SPTAGSecondLevelHeadVectors.bin.owned", "SPTAGSecondLevelHeadVectors.bin.level2",
                     "SecondLevelHeadIndex/head_node_meta.bin", "SecondLevelHeadIndex/metadata.bin"})
                BOOST_CHECK(!std::filesystem::exists(materialized + "/" + absent));
            std::shared_ptr<VectorIndex> independent;
            BOOST_REQUIRE(VectorIndex::LoadIndex(materialized, independent) == ErrorCode::Success);
            auto* independentSPANN = dynamic_cast<SPANN::ISPANNIndex*>(independent.get());
            BOOST_REQUIRE(independentSPANN != nullptr);
            BOOST_REQUIRE(independentSPANN->HasRoutingOnlyHierarchy());
            BOOST_CHECK(!independentSPANN->GetOptions()->m_compactHierarchyVectors);
            for (SizeType head = 0; head < headCount; ++head)
            {
                BOOST_CHECK_EQUAL(std::memcmp(independentSPANN->GetMemoryIndex()->GetSample(head),
                    logical.data() + static_cast<size_t>(head) * dimension,
                    static_cast<size_t>(dimension) * sizeof(float)), 0);
                BOOST_CHECK_EQUAL(independentSPANN->GetMemoryIndex()->GetHeadNodeGlobalVID(head),
                    loadedSPANN->GetGlobalVID(head));
            }
            BOOST_CHECK(search(independent) == after);
            independent.reset();
            const auto originalTop = readBytes(indexDirectory + "/SecondLevelHeadIndex/vectors.bin");
            for (const char* catalog : {"SPTAGHeadVectors.bin", "SPTAGSecondLevelHeadVectors.bin",
                     "SecondLevelHeadIndex/vectors.bin"})
            {
                const std::string path = materialized + "/" + catalog;
                const auto original = readBytes(path);
                BOOST_REQUIRE_GT(original.size(), sizeof(SizeType) + sizeof(DimensionType));
                const auto publishCopy = [&](const std::vector<char>& bytes) {
                    const std::string staged = path + ".test-publish";
                    std::ofstream output(staged, std::ios::binary | std::ios::trunc);
                    output.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
                    output.close();
                    BOOST_REQUIRE(output.good());
                    BOOST_REQUIRE(Helper::AtomicReplaceFile(staged, path));
                };
                auto damaged = original;
                damaged[sizeof(SizeType) + sizeof(DimensionType)] ^= 1;
                publishCopy(damaged);
                BOOST_CHECK(VectorIndex::LoadIndex(materialized, independent) != ErrorCode::Success);
                independent.reset();
                publishCopy(original);
                BOOST_REQUIRE(VectorIndex::LoadIndex(materialized, independent) == ErrorCode::Success);
                independent.reset();
            }
            BOOST_CHECK(readBytes(indexDirectory + "/SecondLevelHeadIndex/vectors.bin") == originalTop);
            std::filesystem::resize_file(materialized + "/HeadIndex/head_metaonly.bin", 28);
            BOOST_CHECK(VectorIndex::LoadIndex(materialized, independent) != ErrorCode::Success);
            independent.reset();
            std::filesystem::remove_all(materialized);
        }
        for (const auto& option : std::vector<std::pair<std::string, std::string>>{
                 {"InternalResultNum", std::to_string(headCount)},
                 {"SecondLevelInitialProbeRatio", "0.5"}})
            BOOST_REQUIRE(loaded->SetParameter(option.first.c_str(),
                option.second.c_str(), "SearchSSDIndex") == ErrorCode::Success);
        {
            VectorIndex::ThreadLocalSearchContext context;
            context.m_queryTags = {tags.front()};
            context.m_limitedTagMembershipEligible = true;
            context.m_limitedTagQueryValues = context.m_queryTags;
            VectorIndex::ThreadLocalSearchContextGuard guard(std::move(context));
            COMMON::QueryResultSet<float> expected(data, 10), actual(data, 10);
            for (SizeType vid = 0; vid < baseCount; ++vid)
                if (tags[static_cast<size_t>(vid)] == tags.front())
                    expected.AddPoint(vid, loadedSPANN->GetMemoryIndex()->ComputeDistance(
                        data, data + static_cast<size_t>(vid) * dimension));
            expected.SortResult();
            BOOST_REQUIRE(loaded->SearchIndex(actual) == ErrorCode::Success);
            for (int rank = 0; rank < 10; ++rank)
            {
                BOOST_CHECK_EQUAL(actual.GetResult(rank)->VID, expected.GetResult(rank)->VID);
                BOOST_CHECK_EQUAL(actual.GetResult(rank)->Dist, expected.GetResult(rank)->Dist);
            }
        }
        loaded.reset();
        compactIndex.reset();
        if (!compactAtBuild)
        {
            char original = 0;
            {
                std::fstream bytes(ownedPath, std::ios::binary | std::ios::in | std::ios::out);
                bytes.seekg(sizeof(SizeType) + sizeof(DimensionType));
                bytes.read(&original, 1);
                const char corrupted = static_cast<char>(original ^ 1);
                bytes.seekp(sizeof(SizeType) + sizeof(DimensionType));
                bytes.write(&corrupted, 1);
                BOOST_REQUIRE(bytes.good());
            }
            BOOST_CHECK(VectorIndex::LoadIndex(indexDirectory, loaded) != ErrorCode::Success);
            loaded.reset();
            {
                std::fstream bytes(ownedPath, std::ios::binary | std::ios::in | std::ios::out);
                bytes.seekp(sizeof(SizeType) + sizeof(DimensionType));
                bytes.write(&original, 1);
                BOOST_REQUIRE(bytes.good());
            }
            BOOST_REQUIRE(VectorIndex::LoadIndex(indexDirectory, loaded) == ErrorCode::Success);
            loaded.reset();
            std::filesystem::resize_file(descriptor, 28);
            BOOST_CHECK(VectorIndex::LoadIndex(indexDirectory, loaded) != ErrorCode::Success);
            loaded.reset();
        }
        std::filesystem::remove_all(indexDirectory);
        std::filesystem::create_directories(indexDirectory);
    }

    std::vector<std::uint32_t> eightSlotTags(
        static_cast<size_t>(baseCount));
    for (SizeType row = 0; row < baseCount; ++row) {
        eightSlotTags[static_cast<size_t>(row)] =
            static_cast<std::uint32_t>(row) % 8;
    }
    const auto verifySlots =
        [&](int slotCount,
            const std::vector<std::uint32_t>& slotTags,
            int slotDistinctTags) {
        auto slotIndex =
            VectorIndex::CreateInstance(
                IndexAlgoType::SPANN,
                VectorValueType::Float);
        BOOST_REQUIRE(slotIndex != nullptr);
        configure(slotIndex);
        BOOST_REQUIRE(
            slotIndex->SetParameter(
                "LimitedTagSlotsPerHead",
                std::to_string(slotCount).c_str(),
                "BuildSSDIndex") ==
            ErrorCode::Success);
        auto* slotSPANN =
            dynamic_cast<SPANN::ISPANNIndex*>(
                slotIndex.get());
        auto* slotTyped =
            dynamic_cast<SPANN::Index<float>*>(
                slotIndex.get());
        BOOST_REQUIRE(slotSPANN != nullptr);
        BOOST_REQUIRE(slotTyped != nullptr);
        BOOST_REQUIRE(
            !slotTyped->GetOptions()->m_selectSecondLevel);
        slotSPANN->SetVectorTags(
            slotTags.data(), baseCount, 1);
        BOOST_REQUIRE(
            slotIndex->BuildIndex(
                vectors, nullptr, true, false,
                false) == ErrorCode::Success);
        BOOST_REQUIRE(
            slotIndex->SaveIndex(
                indexDirectory) ==
            ErrorCode::Success);

        std::uint64_t generation = 0;
        BOOST_REQUIRE(
            Helper::Convert::ConvertStringTo<
                std::uint64_t>(
                slotTyped->GetOptions()
                    ->m_limitedTagGenerationFingerprint
                    .c_str(),
                generation));
        SPANN::LimitedTagSupport support;
        std::string supportError;
        BOOST_REQUIRE(
            support.Load(
                indexDirectory +
                    "/limited_tag_support.bin",
                slotSPANN->GetMemoryIndex()
                    ->GetNumSamples(),
                slotCount, 8, 0, 1,
                generation, &supportError));
        BOOST_CHECK_EQUAL(
            support.SlotsPerHead(), slotCount);
        for (SizeType head = 0;
             head < support.HeadCount(); ++head) {
            const auto disk = slotSPANN->GetDiskIndex();
            std::string posting;
            const int full = disk->GetPostingVectorCount(head, false);
            const int pure = disk->GetPostingVectorCount(head, true);
            if (full != 0)
                BOOST_REQUIRE(disk->GetWritePosting(nullptr, head, posting, false) == ErrorCode::Success);
            const size_t stride = sizeof(SizeType) + sizeof(std::uint32_t) + dimension * sizeof(float);
            BOOST_REQUIRE_EQUAL(posting.size(), static_cast<size_t>(full) * stride);
            std::unordered_map<std::uint32_t, float> closest;
            for (int row = pure; row < full; ++row)
            {
                SizeType vid;
                std::memcpy(&vid, posting.data() + row * stride, sizeof(vid));
                BOOST_REQUIRE(vid >= 0 && vid < baseCount);
                const auto tag = slotTags[vid];
                if (tag == support.OwnTag(head)) continue;
                const float distance = slotSPANN->GetMemoryIndex()->ComputeDistance(
                    slotSPANN->GetMemoryIndex()->GetSample(head), data + vid * dimension);
                const auto found = closest.emplace(tag, distance);
                found.first->second = (std::min)(found.first->second, distance);
            }
            std::vector<std::pair<float, std::uint32_t>> ranked;
            for (const auto& candidate : closest)
                ranked.emplace_back(candidate.second, candidate.first);
            std::sort(ranked.begin(), ranked.end());
            for (int slot = 1; slot < slotCount; ++slot)
                BOOST_CHECK_EQUAL(support.TagAt(head, slot), static_cast<size_t>(slot) <= ranked.size()
                    ? ranked[slot - 1].second : SPANN::LimitedTagSupport::EmptyTag);
        }

        const auto search = [&](const std::shared_ptr<VectorIndex>& target,
                                bool limitedMembership) {
            const std::uint32_t tag = slotTags.front();
            VectorIndex::ThreadLocalSearchContext context;
            context.m_active = true;
            context.m_queryTags = {tag};
            context.m_limitedTagMembershipEligible =
                limitedMembership;
            context.m_limitedTagQueryValues = {tag};
            VectorIndex::ThreadLocalSearchContextGuard guard(
                std::move(context));
            COMMON::QueryResultSet<float> result(data, 10);
            const ErrorCode status = target->SearchIndex(result);
            BOOST_REQUIRE(status == ErrorCode::Success);
            int resultCount = 0;
            for (int rank = 0;
                 rank < result.GetResultNum(); ++rank) {
                const BasicResult* item =
                    result.GetResult(rank);
                if (item == nullptr || item->VID < 0)
                    break;
                BOOST_CHECK_EQUAL(
                    slotTags[static_cast<size_t>(
                        item->VID)],
                    tag);
                ++resultCount;
            }
            BOOST_CHECK_GT(resultCount, 0);
        };
        const auto verifySearch = [&](const std::shared_ptr<VectorIndex>& target) {
            search(target, false);
            search(target, true);
            BOOST_CHECK(target->SetParameter(
                "HeadNavigationMode", "H1Only",
                "SearchSSDIndex") == ErrorCode::FailedParseValue);
        };
        verifySearch(slotIndex);
        slotIndex.reset();

        std::shared_ptr<VectorIndex> reloaded;
        BOOST_REQUIRE(
            VectorIndex::LoadIndex(
                indexDirectory, reloaded) ==
            ErrorCode::Success);
        auto* reloadedTyped =
            dynamic_cast<SPANN::Index<float>*>(
                reloaded.get());
        BOOST_REQUIRE(reloadedTyped != nullptr);
        BOOST_REQUIRE(
            !reloadedTyped->GetOptions()->m_selectSecondLevel);
        BOOST_CHECK_EQUAL(
            reloadedTyped->GetOptions()
                ->m_limitedTagSlotsPerHead,
            slotCount);
        verifySearch(reloaded);
        reloaded.reset();
        std::filesystem::remove_all(indexDirectory);
        std::filesystem::create_directories(
            indexDirectory);
    };
    verifySlots(1, tags, distinctTags);
    verifySlots(3, eightSlotTags, 8);
    verifySlots(8, eightSlotTags, 8);

    {
        auto rejected =
            VectorIndex::CreateInstance(
                IndexAlgoType::SPANN,
                VectorValueType::Float);
        BOOST_REQUIRE(rejected != nullptr);
        configure(rejected);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "SelectSecondLevel", "true",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "Ratio", "0",
                "SelectHead") ==
            ErrorCode::Success);
        auto* rejectedSPANN =
            dynamic_cast<SPANN::ISPANNIndex*>(
                rejected.get());
        BOOST_REQUIRE(rejectedSPANN != nullptr);
        rejectedSPANN->SetVectorTags(
            tags.data(), baseCount, 1);
        BOOST_CHECK(
            rejected->BuildIndex(
                vectors, nullptr, true, false,
                false) ==
            ErrorCode::FailedParseValue);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "Ratio", "1",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_CHECK(
            rejected->BuildIndex(
                vectors, nullptr, true, false,
                false) ==
            ErrorCode::FailedParseValue);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "Ratio", "0.25",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "SecondLevelHeadIndexFolder", "..",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_CHECK(
            rejected->BuildIndex(
                vectors, nullptr, true, false,
                false) ==
            ErrorCode::FailedParseValue);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "SecondLevelHeadIndexFolder",
                "SecondLevelHeadIndex",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "SecondLevelPostingFile",
                "SPTAGFullList.bin",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_CHECK(
            rejected->BuildIndex(
                vectors, nullptr, true, false,
                false) ==
            ErrorCode::FailedParseValue);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "SSDIndex", "./aliased.bin",
                "Base") ==
            ErrorCode::Success);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "SecondLevelPostingFile",
                "aliased.bin",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_CHECK(
            rejected->BuildIndex(
                vectors, nullptr, true, false,
                false) ==
            ErrorCode::FailedParseValue);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "SSDIndex", "./aliased.bin.",
                "Base") ==
            ErrorCode::Success);
        BOOST_CHECK(
            rejected->BuildIndex(
                vectors, nullptr, true, false,
                false) ==
            ErrorCode::FailedParseValue);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "SecondLevelPostingFile",
                "head_select_state.bin",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_CHECK(
            rejected->BuildIndex(
                vectors, nullptr, true, false,
                false) ==
            ErrorCode::FailedParseValue);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "SecondLevelPostingFile",
                "second_level_head_postings.bin",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_REQUIRE(
            rejected->SetParameter(
                "SecondLevelHeadVectors",
                "head_select_state.bin.tmp",
                "SelectHead") ==
            ErrorCode::Success);
        BOOST_CHECK(
            rejected->BuildIndex(
                vectors, nullptr, true, false,
                false) ==
            ErrorCode::FailedParseValue);
    }

    const auto rejectConfiguration =
        [&](const char* key, const char* value) {
           auto rejected =
               VectorIndex::CreateInstance(
                   IndexAlgoType::SPANN,
                   VectorValueType::Float);
           BOOST_REQUIRE(rejected != nullptr);
           configure(rejected);
           BOOST_REQUIRE(
               rejected->SetParameter(
                   key, value,
                   "BuildSSDIndex") ==
               ErrorCode::Success);
           auto* rejectedSPANN =
               dynamic_cast<SPANN::ISPANNIndex*>(
                   rejected.get());
           BOOST_REQUIRE(rejectedSPANN != nullptr);
           rejectedSPANN->SetVectorTags(
               tags.data(), baseCount, 1);
           BOOST_CHECK(
               rejected->BuildIndex(
                   vectors, nullptr, true, false,
                   false) ==
               ErrorCode::FailedParseValue);
        };
    rejectConfiguration(
        "EnableHybridDistance", "true");
    rejectConfiguration(
        "LimitedTagSlotsPerHead", "0");
    rejectConfiguration(
        "LimitedTagSlotsPerHead", "-1");
    rejectConfiguration(
        "TailReplicaCount", "1");
    rejectConfiguration(
        "SecondLevelInitialProbeRatio", "0");
    rejectConfiguration(
        "SecondLevelInitialProbeRatio", "1.01");
    rejectConfiguration(
        "SecondLevelMaxCheck", "0");
    rejectConfiguration(
        "LimitedTagSupportFile",
        "SPTAGFullList.bin.");
    for (const char* reservedFile : {
             "tag_routing_stats.bin",
             "signatures_bitmask.bin"}) {
        rejectConfiguration("LimitedTagSupportFile", reservedFile);
    }

    auto index = VectorIndex::CreateInstance(
        IndexAlgoType::SPANN,
        VectorValueType::Float);
    BOOST_REQUIRE(index != nullptr);
    configure(index);
    BOOST_REQUIRE(
        index->SetParameter(
            "SelectSecondLevel", "true",
            "SelectHead") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index->SetParameter(
            "Ratio", "0.25",
            "SelectHead") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index->SetParameter(
            "LimitedTagSlotsPerHead", "4",
            "BuildSSDIndex") ==
        ErrorCode::Success);
    auto* spann =
        dynamic_cast<SPANN::ISPANNIndex*>(
            index.get());
    BOOST_REQUIRE(spann != nullptr);
    spann->SetVectorTags(
        tags.data(), baseCount, 1);
    BOOST_REQUIRE(
        index->BuildIndex(
            vectors, nullptr, true, false,
            false) == ErrorCode::Success);
    BOOST_REQUIRE(
        index->SetParameter(
            "MaxCheck", "4",
            "SearchSSDIndex") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index->SetParameter(
            "InternalResultNum", "8",
            "SearchSSDIndex") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index->SaveIndex(indexDirectory) ==
        ErrorCode::Success);
    BOOST_CHECK(
        std::filesystem::exists(
            indexDirectory +
            "/SPTAGSecondLevelHeadVectors.bin"));
    BOOST_CHECK(
        std::filesystem::exists(
            indexDirectory +
            "/SPTAGSecondLevelHeadVectorIDs.bin"));
    BOOST_CHECK(
        std::filesystem::exists(
            indexDirectory +
            "/SecondLevelHeadIndex/indexloader.ini"));
    BOOST_CHECK(
        std::filesystem::exists(
            indexDirectory +
            "/second_level_head_postings.bin"));

    auto* typed =
        dynamic_cast<SPANN::Index<float>*>(
            index.get());
    BOOST_REQUIRE(typed != nullptr);
    BOOST_REQUIRE(typed->GetDiskIndex() != nullptr);
    const int recordsPerBuildPage =
        PageSize /
        static_cast<int>(
            sizeof(SizeType) +
            sizeof(std::uint32_t) +
            sizeof(float) * dimension);
    BOOST_CHECK_GT(
        typed->GetDiskIndex()
            ->GetPostingAvgRecords(true),
        static_cast<double>(
            recordsPerBuildPage));
    const std::string staticPostingPath =
        indexDirectory + "/SPTAGFullList.bin";
    {
        std::ifstream postingInput(
            staticPostingPath, std::ios::binary);
        BOOST_REQUIRE(postingInput.good());
        std::array<std::int32_t, 11> header{};
        postingInput.read(
            reinterpret_cast<char*>(header.data()),
            static_cast<std::streamsize>(
                sizeof(header)));
        BOOST_REQUIRE(postingInput.good());
        BOOST_CHECK_EQUAL(
            static_cast<std::uint32_t>(header[0]),
            0x314D5453U);
        BOOST_CHECK_EQUAL(header[1], 3);
        BOOST_REQUIRE_EQUAL(
            header[2],
            typed->GetDiskIndex()
                ->GetPostingCount());
        const int recordBytes = header[5];
        BOOST_REQUIRE_GT(recordBytes, 0);
        double originalRecords = 0.0;
        double originalPages = 0.0;
        for (int list = 0; list < header[2];
             ++list) {
            std::int32_t pageNum = 0;
            std::uint16_t pageOffset = 0;
            std::int32_t elementCount = 0;
            std::uint16_t pageCount = 0;
            std::int32_t pureCount = 0;
            postingInput.read(
                reinterpret_cast<char*>(&pageNum),
                sizeof(pageNum));
            postingInput.read(
                reinterpret_cast<char*>(&pageOffset),
                sizeof(pageOffset));
            postingInput.read(
                reinterpret_cast<char*>(&elementCount),
                sizeof(elementCount));
            postingInput.read(
                reinterpret_cast<char*>(&pageCount),
                sizeof(pageCount));
            postingInput.read(
                reinterpret_cast<char*>(&pureCount),
                sizeof(pureCount));
            BOOST_REQUIRE(postingInput.good());
            BOOST_REQUIRE_GE(pageNum, 0);
            BOOST_REQUIRE_GE(elementCount, pureCount);
            (void)pageCount;
            const int originalCount =
                elementCount - pureCount;
            originalRecords += originalCount;
            if (originalCount > 0) {
                const std::uint64_t beginBytes =
                    static_cast<std::uint64_t>(
                        pageOffset) +
                    static_cast<std::uint64_t>(
                        pureCount) *
                        static_cast<std::uint64_t>(
                            recordBytes);
                const std::uint64_t endBytes =
                    static_cast<std::uint64_t>(
                        pageOffset) +
                    static_cast<std::uint64_t>(
                        elementCount) *
                        static_cast<std::uint64_t>(
                            recordBytes);
                originalPages +=
                    static_cast<double>(
                        (endBytes + PageSize - 1) /
                            PageSize -
                        beginBytes / PageSize);
            }
        }
        const double listCount =
            static_cast<double>(header[2]);
        BOOST_CHECK_GT(originalRecords, 0.0);
        BOOST_CHECK_SMALL(
            typed->GetDiskIndex()
                    ->GetPostingAvgRecords(false) -
                originalRecords / listCount,
            1e-9);
        BOOST_CHECK_SMALL(
            typed->GetDiskIndex()
                    ->GetPostingAvgPages(false) -
                originalPages / listCount,
            1e-9);
        BOOST_CHECK_SMALL(
            typed->GetDiskIndex()
                    ->GetPostingAvgBytes(false) -
                originalRecords *
                    static_cast<double>(recordBytes) /
                    listCount,
            1e-9);
    }
    const SizeType headCount =
        spann->GetMemoryIndex()->GetNumSamples();
    {
        const auto disk = typed->GetDiskIndex();
        const auto memory = spann->GetMemoryIndex();
        const size_t recordBytes = sizeof(SizeType) + sizeof(std::uint32_t) +
            sizeof(float) * dimension;
        for (SizeType head = 0; head < headCount; ++head)
        {
            const int pureCount = disk->GetPostingVectorCount(head, true);
            const int count = disk->GetPostingVectorCount(head, false);
            BOOST_REQUIRE_GE(pureCount, 0);
            BOOST_REQUIRE_GE(count, pureCount);
            std::string records;
            BOOST_REQUIRE(disk->GetWritePosting(nullptr, head, records, false) == ErrorCode::Success);
            BOOST_REQUIRE_EQUAL(records.size(), static_cast<size_t>(count) * recordBytes);
            Cache::PostingBitmask pure, tail;
            for (int row = 0; row < count; ++row)
            {
                std::uint32_t tag = 0;
                std::memcpy(&tag, records.data() + static_cast<size_t>(row) * recordBytes +
                    sizeof(SizeType), sizeof(tag));
                (row < pureCount ? pure : tail).Insert(tag);
            }
            for (int word = 0; word < Cache::PS_BITMASK_WORDS; ++word)
            {
                BOOST_CHECK_EQUAL(memory->GetHeadNodePS(head)->bits[word], pure.bits[word]);
                BOOST_CHECK_EQUAL(memory->GetHeadNodeTailPS(head)->bits[word], tail.bits[word]);
            }
        }
        BOOST_CHECK_EQUAL(disk->GetPostingVectorCount(-1, true), -1);
        BOOST_CHECK_EQUAL(disk->GetPostingVectorCount(headCount, false), -1);
    }
    const auto verifyRejectedVIDsStayVisited = [&]() {
        const auto disk = typed->GetDiskIndex();
        const auto memory = spann->GetMemoryIndex();
        const int postingCount = disk->GetPostingCount();
        const int bufferBytes = disk->GetPostingBufferBytes(false);
        BOOST_REQUIRE_GT(postingCount, 0);
        BOOST_REQUIRE_GT(bufferBytes, 0);
        // ExcludeHead keeps the H1 representatives out of these SSD postings.
        std::vector<bool> postingVIDs(static_cast<size_t>(baseCount), true);
        for (SizeType head = 0; head < headCount; ++head) {
            const SizeType vid = spann->GetGlobalVID(head);
            BOOST_REQUIRE_GE(vid, 0);
            BOOST_REQUIRE_LT(vid, baseCount);
            BOOST_REQUIRE(postingVIDs[static_cast<size_t>(vid)]);
            postingVIDs[static_cast<size_t>(vid)] = false;
        }

        for (bool iterative : {false, true}) {
            for (int filter = 0; filter < 5; ++filter) {
                SPANN::ExtraWorkSpace workspace;
                workspace.Initialize(
                    4096, 4, postingCount, bufferBytes, false, false);
                workspace.m_scanFullPostingForFilter = true;
                workspace.m_postingIDs.resize(
                    static_cast<size_t>(postingCount));
                std::iota(
                    workspace.m_postingIDs.begin(),
                    workspace.m_postingIDs.end(), 0);
                const std::uint32_t queryTag =
                    filter == 2 || filter == 4 ? distinctTags : 1;
                Cache::DNFPredicate predicate;
                if (filter == 1 || filter == 2) {
                    workspace.m_queryTags = &queryTag;
                    workspace.m_numQueryTags = 1;
                } else if (filter == 3 || filter == 4) {
                    Cache::DNFClause clause;
                    clause.lits.push_back(
                        {0, queryTag, Cache::DNF_EQ, 0});
                    predicate.clauses.push_back(clause);
                    workspace.m_dnf = &predicate;
                }
                int expectedMatches = 0;
                for (SizeType vid = 0; vid < baseCount; ++vid) {
                    if (postingVIDs[static_cast<size_t>(vid)] &&
                        (filter == 0 ||
                         tags[static_cast<size_t>(vid)] == queryTag)) {
                        ++expectedMatches;
                    }
                }
                COMMON::QueryResultSet<float> results(data, 10);
                if (iterative) {
                    COMMON::QueryResultSet<float> heads(data, 1);
                    workspace.m_loadPosting = true;
                    int matches = 0;
                    ErrorCode status;
                    do {
                        status = disk->SearchIterativeNext(
                            &workspace, heads, results, memory, index.get());
                        if (status == ErrorCode::Success) {
                            ++matches;
                            BOOST_REQUIRE_LE(matches, expectedMatches);
                        }
                    } while (status == ErrorCode::Success);
                    BOOST_CHECK(status == ErrorCode::VectorNotFound);
                    BOOST_CHECK_EQUAL(matches, expectedMatches);
                } else {
                    BOOST_REQUIRE(
                        disk->SearchIndex(
                            &workspace, results, memory, nullptr) ==
                        ErrorCode::Success);
                    BOOST_CHECK_EQUAL(
                        workspace.m_postingProbeStats.m_matchedVectors,
                        expectedMatches);
                    BOOST_CHECK_EQUAL(
                        workspace.m_postingProbeStats.m_scannedVectors -
                            workspace.m_postingProbeStats.m_dedupSkippedVectors,
                        baseCount - headCount);
                }
                BOOST_CHECK_EQUAL(results.GetScanned(), expectedMatches);
                BOOST_CHECK_GT(
                    workspace.m_postingProbeStats.m_dedupSkippedVectors, 0U);
                for (SizeType vid = 0; vid < baseCount; ++vid) {
                    BOOST_CHECK_EQUAL(
                        workspace.m_deduper.Contains(vid),
                        postingVIDs[static_cast<size_t>(vid)]);
                }
                results.SortResult();
                for (int rank = 0; rank < results.GetResultNum(); ++rank) {
                    const auto* result = results.GetResult(rank);
                    if (result->VID < 0) break;
                    BOOST_REQUIRE_LT(result->VID, baseCount);
                    BOOST_CHECK(
                        filter == 0 ||
                        tags[static_cast<size_t>(result->VID)] == queryTag);
                }
            }
        }
    };
    verifyRejectedVIDsStayVisited();
    std::uint64_t generation = 0;
    BOOST_REQUIRE(
        Helper::Convert::ConvertStringTo<
            std::uint64_t>(
            typed->GetOptions()
                ->m_limitedTagGenerationFingerprint
                .c_str(),
            generation));
    SPANN::LimitedTagSupport support;
    std::string supportError;
    const std::string supportPath =
        indexDirectory +
        "/limited_tag_support.bin";
    BOOST_REQUIRE(
        support.Load(
            supportPath, headCount, 4, 8,
            0, 1,
            generation, &supportError));
    {
        using Postings =
            SPANN::SecondLevelHeadPostings;
        std::ifstream postingInput(
            indexDirectory +
                "/second_level_head_postings.bin",
            std::ios::binary);
        BOOST_REQUIRE(postingInput.good());
        Postings::Header header;
        postingInput.read(
            reinterpret_cast<char*>(&header),
            sizeof(header));
        BOOST_REQUIRE(postingInput.good());
        BOOST_CHECK_EQUAL(header.m_version, 3);
        BOOST_CHECK_EQUAL(
            header.m_signatureBytes,
            sizeof(Postings::Signature));
        BOOST_CHECK_EQUAL(
            header
                .m_limitedTagSupportFingerprint,
            support.ContentFingerprint());
        BOOST_CHECK_EQUAL(
            header.m_signatureMinSelectivity,
            0.0);
        BOOST_CHECK_EQUAL(
            header.m_signatureMaxSelectivity,
            1.0);

        std::vector<std::uint64_t> offsets(
            static_cast<size_t>(
                header.m_secondLevelHeadCount) + 1);
        std::vector<Postings::Member> members(
            static_cast<size_t>(
                header.m_memberCount));
        std::vector<Postings::Signature>
            signatures(
                static_cast<size_t>(
                    header
                        .m_secondLevelHeadCount));
        postingInput.read(
            reinterpret_cast<char*>(
                offsets.data()),
            static_cast<std::streamsize>(
                offsets.size() *
                sizeof(std::uint64_t)));
        postingInput.read(
            reinterpret_cast<char*>(
                members.data()),
            static_cast<std::streamsize>(
                members.size() *
                sizeof(Postings::Member)));
        postingInput.read(
            reinterpret_cast<char*>(
                signatures.data()),
            static_cast<std::streamsize>(
                signatures.size() *
                sizeof(Postings::Signature)));
        BOOST_REQUIRE(postingInput.good());

        const auto headMetadata = typed->GetMemoryIndex();
        BOOST_REQUIRE(headMetadata != nullptr);
        BOOST_REQUIRE(headMetadata->HasHeadNodeOwnTags());
        BOOST_REQUIRE(headMetadata->HasHeadNodeTailPS());
        for (std::uint32_t second = 0;
             second <
                 header
                     .m_secondLevelHeadCount;
             ++second) {
            Postings::Signature expected;
            expected.Clear();
            const std::uint64_t begin =
                offsets[
                    static_cast<size_t>(
                        second)];
            const std::uint64_t end =
                offsets[
                    static_cast<size_t>(
                        second) + 1];
            BOOST_REQUIRE_LT(begin, end);
            BOOST_REQUIRE_LE(
                end,
                static_cast<std::uint64_t>(
                    members.size()));
            for (std::uint64_t offset = begin;
                 offset < end; ++offset) {
                const SizeType first =
                    static_cast<SizeType>(
                        members[
                            static_cast<size_t>(
                                offset)]);
                const SizeType vid = typed->GetGlobalVID(first);
                BOOST_REQUIRE_GE(vid, 0);
                BOOST_REQUIRE_LT(vid, baseCount);
                expected.Insert(tags[static_cast<size_t>(vid)]);
                const auto* pure = headMetadata->GetHeadNodePS(first);
                BOOST_REQUIRE(pure != nullptr);
                expected.MergeOR(*pure);
            }
            for (int word = 0;
                 word <
                     Cache::PS_BITMASK_WORDS;
                 ++word) {
                BOOST_CHECK_EQUAL(
                    signatures[
                        static_cast<size_t>(
                            second)]
                        .bits[word],
                    expected.bits[word]);
            }
        }
    }
    COMMON::Dataset<std::uint64_t> headVectorIDs;
    BOOST_REQUIRE(
        headVectorIDs.Load(
            indexDirectory +
                "/SPTAGHeadVectorIDs.bin",
            1048576, 2147483647) ==
        ErrorCode::Success);
    BOOST_REQUIRE_EQUAL(
        headVectorIDs.R(), headCount);
    std::unordered_map<std::uint32_t, int>
        coverage;
    for (SizeType head = 0;
         head < headCount; ++head) {
        const auto headTags =
            support.HeadTags(head);
        BOOST_REQUIRE_GE(headTags.size(), 1);
        BOOST_REQUIRE_LE(headTags.size(), 4);
        const SizeType sourceVID =
            static_cast<SizeType>(
                *headVectorIDs[head]);
        BOOST_REQUIRE(
            sourceVID >= 0 &&
            sourceVID < baseCount);
        BOOST_CHECK_EQUAL(
            headTags.front(),
            tags[static_cast<size_t>(
                sourceVID)]);
        BOOST_CHECK(
            support.Supports(
                head,
                tags[static_cast<size_t>(
                    sourceVID)]));
        for (std::uint32_t tag : headTags)
            ++coverage[tag];
    }
    for (std::uint32_t tag = 0;
         tag < distinctTags; ++tag) {
        BOOST_CHECK_GE(coverage[tag], 8);
    }
    const auto searchPostingCount =
        [&]() {
            const std::uint32_t tag = tags.front();
            VectorIndex::ThreadLocalSearchContext
                context;
            context.m_active = true;
            context.m_queryTags = {tag};
            context.m_limitedTagMembershipEligible =
                true;
            context.m_limitedTagQueryValues = {
                tag};
            VectorIndex::ThreadLocalSearchContextGuard
                guard(std::move(context));
            COMMON::QueryResultSet<float> result(
                data, 4);
            BOOST_REQUIRE(
                index->SearchIndex(result) ==
                ErrorCode::Success);
            return VectorIndex::
                GetThreadLocalPostingScanStats()
                    .m_readPostings;
        };
    const auto fullBeamPostingCount =
        searchPostingCount();
    BOOST_CHECK(
        index->SetParameter(
            "SecondLevelInitialProbeRatio", "0",
            "SearchSSDIndex") ==
        ErrorCode::FailedParseValue);
    BOOST_CHECK_SMALL(
        typed->GetOptions()
                ->m_secondLevelInitialProbeRatio -
            1.0,
        1e-12);
    BOOST_REQUIRE(
        index->SetParameter(
            "SecondLevelInitialProbeRatio", "0.125",
            "SearchSSDIndex") ==
        ErrorCode::Success);
    const auto narrowBeamPostingCount =
        searchPostingCount();
    BOOST_CHECK_EQUAL(fullBeamPostingCount, 8);
    BOOST_CHECK_EQUAL(
        fullBeamPostingCount, narrowBeamPostingCount);
    BOOST_REQUIRE(
        index->SetParameter(
            "SecondLevelInitialProbeRatio",
            "1", "SearchSSDIndex") ==
        ErrorCode::Success);
    BOOST_CHECK(
        !std::filesystem::exists(
            indexDirectory +
            "/SPTAGFullList.bin.hybrid.stats"));
    BOOST_CHECK(
        !std::filesystem::exists(
            indexDirectory +
            "/HeadIndex/head_cross_edges.bin"));

    const auto searchFiltered =
        [&](const std::shared_ptr<VectorIndex>& target,
            std::uint32_t tag,
            float ignoredSelectivity) {
            (void)ignoredSelectivity;
            const SizeType queryVID =
                static_cast<SizeType>(tag);
            VectorIndex::ThreadLocalSearchContext context;
            context.m_active = true;
            context.m_queryTags = {tag};
            context.m_limitedTagMembershipEligible = true;
            context.m_limitedTagQueryValues = {tag};
            VectorIndex::ThreadLocalSearchContextGuard guard(
                std::move(context));
            COMMON::QueryResultSet<float> result(
                data +
                    static_cast<size_t>(queryVID) *
                        dimension,
                10);
            BOOST_REQUIRE(
                target->SearchIndex(result) ==
                ErrorCode::Success);
            std::vector<SizeType> ids;
            for (int rank = 0;
                 rank < result.GetResultNum();
                 ++rank) {
                const BasicResult* item =
                    result.GetResult(rank);
                if (item == nullptr ||
                    item->VID < 0)
                    break;
                BOOST_CHECK_EQUAL(
                    tags[static_cast<size_t>(
                        item->VID)],
                    tag);
                ids.push_back(item->VID);
            }
            BOOST_CHECK(!ids.empty());
            return ids;
        };

    const auto verifySelectivityIndependentNavigation =
        [&](const std::shared_ptr<VectorIndex>& target) {
            const auto expected = searchFiltered(target, 3, 0.01f);
            for (float selectivity : {0.0f, 0.01f, 0.25f, 1.0f, -1.0f,
                                     std::numeric_limits<float>::quiet_NaN()})
            {
                const auto actual = searchFiltered(target, 3, selectivity);
                BOOST_CHECK_EQUAL_COLLECTIONS(
                    expected.begin(), expected.end(),
                    actual.begin(), actual.end());
            }
            BOOST_CHECK(target->SetParameter(
                "HeadNavigationMode", "H1Only",
                "SearchSSDIndex") == ErrorCode::FailedParseValue);
        };

    const auto verifyHeadSources =
        [&](const std::shared_ptr<VectorIndex>& target) {
           for (SizeType head = 0;
                head < headCount; ++head) {
               const SizeType sourceVID =
                   static_cast<SizeType>(
                       *headVectorIDs[head]);
               const std::uint32_t tag =
                   tags[static_cast<size_t>(
                       sourceVID)];
               VectorIndex::ThreadLocalSearchContext
                   context;
               context.m_active = true;
               context.m_queryTags = {tag};
               VectorIndex::ThreadLocalSearchContextGuard
                   guard(std::move(context));
               COMMON::QueryResultSet<float> result(
                   data +
                       static_cast<size_t>(
                           sourceVID) *
                           dimension,
                   10);
               BOOST_REQUIRE(
                   target->SearchIndex(result) ==
                   ErrorCode::Success);
               bool foundSource = false;
               for (int rank = 0;
                    rank < result.GetResultNum();
                    ++rank) {
                   const BasicResult* item =
                       result.GetResult(rank);
                   if (item == nullptr ||
                       item->VID < 0) {
                       break;
                   }
                   BOOST_CHECK_EQUAL(
                       tags[static_cast<size_t>(
                           item->VID)],
                       tag);
                   foundSource |=
                       item->VID == sourceVID;
               }
               BOOST_CHECK(foundSource);
           }
        };
    const auto searchCompound =
        [&](const std::shared_ptr<VectorIndex>& target,
           bool useDNF) {
           const std::uint32_t first =
               tags[0];
           const std::uint32_t second =
               (first + 1U) %
               distinctTags;
           VectorIndex::ThreadLocalSearchContext
               context;
           context.m_active = true;
           context.m_queryTags = {
               first, second};
           if (useDNF) {
               Cache::DNFClause firstClause;
               firstClause.lits.push_back(
                   {0, first,
                    Cache::DNF_EQ, 0});
               Cache::DNFClause secondClause;
               secondClause.lits.push_back(
                   {0, second,
                    Cache::DNF_EQ, 0});
               context.m_dnf.clauses = {
                   firstClause, secondClause};
           }
           VectorIndex::ThreadLocalSearchContextGuard
               guard(std::move(context));
           COMMON::QueryResultSet<float> result(
               data, 10);
           BOOST_REQUIRE(
               target->SearchIndex(result) ==
               ErrorCode::Success);
           int resultCount = 0;
           for (int rank = 0;
                rank < result.GetResultNum();
                ++rank) {
               const BasicResult* item =
                   result.GetResult(rank);
               if (item == nullptr ||
                   item->VID < 0) {
                   break;
               }
               const std::uint32_t resultTag =
                   tags[static_cast<size_t>(
                       item->VID)];
               BOOST_CHECK(
                   resultTag == first ||
                   resultTag == second);
               ++resultCount;
           }
           BOOST_CHECK_GT(resultCount, 0);
        };
    std::vector<SizeType> allPostingIDs;
    allPostingIDs.reserve(
        static_cast<size_t>(headCount));
    for (SizeType head = 0; head < headCount;
         ++head) {
        allPostingIDs.push_back(head);
    }
    const auto verifyDirectHeadSources =
        [&](const std::shared_ptr<VectorIndex>& target,
            bool useDNF) {
            for (SizeType head = 0;
                 head < headCount; ++head) {
                const SizeType sourceVID =
                    static_cast<SizeType>(
                        *headVectorIDs[head]);
                const std::uint32_t tag =
                    tags[static_cast<size_t>(
                        sourceVID)];
                VectorIndex::ThreadLocalSearchContext
                    context;
                context.m_active = true;
                if (useDNF) {
                    Cache::DNFClause clause;
                    clause.lits.push_back(
                        {0, tag, Cache::DNF_EQ, 0});
                    context.m_dnf.clauses = {clause};
                } else {
                    context.m_queryTags = {tag};
                }
                VectorIndex::ThreadLocalSearchContextGuard
                    guard(std::move(context));
                COMMON::QueryResultSet<float> result(
                    data +
                        static_cast<size_t>(
                            sourceVID) *
                            dimension,
                    10);
                BOOST_REQUIRE(
                    target->SearchIndex(result) ==
                    ErrorCode::Success);
                bool foundSource = false;
                for (int rank = 0;
                     rank < result.GetResultNum();
                     ++rank) {
                    const BasicResult* item =
                        result.GetResult(rank);
                    if (item == nullptr ||
                        item->VID < 0) {
                        break;
                    }
                    BOOST_CHECK_EQUAL(
                        tags[static_cast<size_t>(
                            item->VID)],
                        tag);
                    foundSource |=
                        item->VID == sourceVID;
                }
                BOOST_CHECK(foundSource);
            }
        };

    verifySelectivityIndependentNavigation(index);
    verifyHeadSources(index);
    verifyDirectHeadSources(index, false);
    verifyDirectHeadSources(index, true);
    searchCompound(index, false);
    searchCompound(index, true);
    const auto h1BeforeReload =
        searchFiltered(index, 3, 0.25f);
    const auto excludedNarrowBeforeReload =
        searchFiltered(index, 3, 0.01f);
    BOOST_CHECK_EQUAL_COLLECTIONS(
        h1BeforeReload.begin(), h1BeforeReload.end(),
        excludedNarrowBeforeReload.begin(),
        excludedNarrowBeforeReload.end());
    const auto h2BeforeReload =
        searchFiltered(index, 0, 0.01f);
    COMMON::QueryResultSet<float> unfilteredBefore(
        data, 10);
    BOOST_REQUIRE(
        index->SearchIndex(unfilteredBefore) ==
        ErrorCode::Success);
    std::array<SizeType, 10> unfilteredIDs{};
    for (int rank = 0; rank < 10; ++rank)
        unfilteredIDs[static_cast<size_t>(rank)] =
            unfilteredBefore.GetResult(rank)->VID;

    index.reset();
    std::shared_ptr<VectorIndex> reloaded;
    BOOST_REQUIRE(
        VectorIndex::LoadIndex(
            indexDirectory, reloaded) ==
        ErrorCode::Success);
    verifySelectivityIndependentNavigation(reloaded);
    verifyHeadSources(reloaded);
    verifyDirectHeadSources(reloaded, false);
    verifyDirectHeadSources(reloaded, true);
    searchCompound(reloaded, false);
    searchCompound(reloaded, true);
    const auto h1AfterReload =
        searchFiltered(reloaded, 3, 0.25f);
    const auto excludedNarrowAfterReload =
        searchFiltered(reloaded, 3, 0.01f);
    BOOST_CHECK_EQUAL_COLLECTIONS(
        h1AfterReload.begin(), h1AfterReload.end(),
        excludedNarrowAfterReload.begin(),
        excludedNarrowAfterReload.end());
    const auto h2AfterReload =
        searchFiltered(reloaded, 0, 0.01f);
    BOOST_CHECK_EQUAL_COLLECTIONS(
        h1BeforeReload.begin(), h1BeforeReload.end(),
        h1AfterReload.begin(), h1AfterReload.end());
    BOOST_CHECK_EQUAL_COLLECTIONS(
        h2BeforeReload.begin(), h2BeforeReload.end(),
        h2AfterReload.begin(), h2AfterReload.end());
    COMMON::QueryResultSet<float> unfilteredAfter(
        data, 10);
    BOOST_REQUIRE(
        reloaded->SearchIndex(unfilteredAfter) ==
        ErrorCode::Success);
    for (int rank = 0; rank < 10; ++rank) {
        BOOST_CHECK_EQUAL(
            unfilteredAfter.GetResult(rank)->VID,
            unfilteredIDs[
                static_cast<size_t>(rank)]);
    }
    reloaded.reset();

    {
        const std::string loaderPath =
            indexDirectory + "/indexloader.ini";
        std::ifstream loaderInput(loaderPath);
        BOOST_REQUIRE(loaderInput.good());
        const std::string loaderConfig(
            (std::istreambuf_iterator<char>(loaderInput)),
            std::istreambuf_iterator<char>());
        loaderInput.close();
        const std::string validProbeRatio =
            "HierarchyInitialProbeRatio=1.000000";
        const size_t runtimeProbeRatio =
            loaderConfig.rfind(validProbeRatio);
        BOOST_REQUIRE_NE(
            runtimeProbeRatio, std::string::npos);
        std::string invalidLoaderConfig = loaderConfig;
        invalidLoaderConfig.replace(
            runtimeProbeRatio, validProbeRatio.size(),
            "HierarchyInitialProbeRatio=0");
        {
            std::ofstream loaderOutput(
                loaderPath, std::ios::trunc);
            BOOST_REQUIRE(loaderOutput.good());
            loaderOutput << invalidLoaderConfig;
            BOOST_REQUIRE(loaderOutput.good());
        }
        std::shared_ptr<VectorIndex> invalidRuntimeRatio;
        BOOST_CHECK(
            VectorIndex::LoadIndex(
                indexDirectory, invalidRuntimeRatio) ==
            ErrorCode::FailedParseValue);
        invalidRuntimeRatio.reset();
        {
            std::ofstream loaderOutput(
                loaderPath, std::ios::trunc);
            BOOST_REQUIRE(loaderOutput.good());
            loaderOutput << loaderConfig;
            BOOST_REQUIRE(loaderOutput.good());
        }
    }

    {
        std::fstream posting(
            staticPostingPath,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(posting.good());
        const std::int32_t legacyVersion = 2;
        posting.seekp(sizeof(std::int32_t));
        posting.write(
            reinterpret_cast<const char*>(
                &legacyVersion),
            sizeof(legacyVersion));
        BOOST_REQUIRE(posting.good());
    }
    std::shared_ptr<VectorIndex> legacyTailReload;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory,
            legacyTailReload) !=
        ErrorCode::Success);
    {
        std::fstream posting(
            staticPostingPath,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(posting.good());
        const std::int32_t currentVersion = 3;
        posting.seekp(sizeof(std::int32_t));
        posting.write(
            reinterpret_cast<const char*>(
                &currentVersion),
            sizeof(currentVersion));
        BOOST_REQUIRE(posting.good());
    }

    const std::string staticPostingBackup =
        staticPostingPath + ".backup";
    std::filesystem::copy_file(
        staticPostingPath, staticPostingBackup,
        std::filesystem::copy_options::
            overwrite_existing);
    {
        constexpr std::streamoff headerBytes =
            11 * sizeof(std::int32_t);
        constexpr std::streamoff pageCountOffset =
            headerBytes +
            sizeof(std::int32_t) +
            sizeof(std::uint16_t) +
            sizeof(std::int32_t);
        std::fstream corrupt(
            staticPostingPath,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(corrupt.good());
        corrupt.seekp(pageCountOffset);
        const std::uint16_t impossiblePageCount =
            (std::numeric_limits<
                 std::uint16_t>::max)();
        corrupt.write(
            reinterpret_cast<const char*>(
                &impossiblePageCount),
            sizeof(impossiblePageCount));
        BOOST_REQUIRE(corrupt.good());
    }
    std::shared_ptr<VectorIndex>
        invalidStaticDirectory;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory,
            invalidStaticDirectory) !=
        ErrorCode::Success);
    invalidStaticDirectory.reset();
    std::filesystem::copy_file(
        staticPostingBackup, staticPostingPath,
        std::filesystem::copy_options::
            overwrite_existing);

    struct StaticDirectoryEntry {
        std::streamoff offset = 0;
        std::int32_t pageNum = 0;
        std::uint16_t pageOffset = 0;
        std::int32_t elementCount = 0;
        std::uint16_t pageCount = 0;
    };
    std::array<std::int32_t, 11>
        staticHeader{};
    std::vector<StaticDirectoryEntry>
        staticEntries;
    {
        std::ifstream posting(
            staticPostingPath,
            std::ios::binary);
        BOOST_REQUIRE(posting.good());
        posting.read(
            reinterpret_cast<char*>(
                staticHeader.data()),
            sizeof(staticHeader));
        BOOST_REQUIRE(posting.good());
        staticEntries.reserve(
            static_cast<size_t>(
                staticHeader[2]));
        for (std::int32_t list = 0;
             list < staticHeader[2]; ++list) {
            StaticDirectoryEntry entry;
            entry.offset =
                static_cast<std::streamoff>(
                    posting.tellg());
            std::int32_t pureCount = 0;
            posting.read(
                reinterpret_cast<char*>(
                    &entry.pageNum),
                sizeof(entry.pageNum));
            posting.read(
                reinterpret_cast<char*>(
                    &entry.pageOffset),
                sizeof(entry.pageOffset));
            posting.read(
                reinterpret_cast<char*>(
                    &entry.elementCount),
                sizeof(entry.elementCount));
            posting.read(
                reinterpret_cast<char*>(
                    &entry.pageCount),
                sizeof(entry.pageCount));
            posting.read(
                reinterpret_cast<char*>(
                    &pureCount),
                sizeof(pureCount));
            BOOST_REQUIRE(posting.good());
            staticEntries.push_back(entry);
        }
    }
    const std::uint64_t staticDataOffset =
        static_cast<std::uint64_t>(
            staticHeader[10]) *
        PageSize;
    const std::uint64_t staticFileBytes =
        std::filesystem::file_size(
            staticPostingPath);
    size_t overlapSource =
        staticEntries.size();
    size_t overlapTarget =
        staticEntries.size();
    std::uint16_t overlapPageCount = 0;
    for (size_t target = 0;
         target < staticEntries.size() &&
         overlapTarget == staticEntries.size();
         ++target) {
        const auto& targetEntry =
            staticEntries[target];
        const std::uint64_t targetEnd =
            staticDataOffset +
            static_cast<std::uint64_t>(
                targetEntry.pageNum +
                targetEntry.pageCount) *
                PageSize;
        if (targetEntry.elementCount <= 0 ||
            targetEnd == staticFileBytes) {
            continue;
        }
        for (size_t source = 0;
             source < staticEntries.size();
             ++source) {
            const auto& sourceEntry =
                staticEntries[source];
            if (source == target ||
                sourceEntry.elementCount <= 0) {
                continue;
            }
            const std::uint64_t payloadBytes =
                static_cast<std::uint64_t>(
                    targetEntry.elementCount) *
                static_cast<std::uint64_t>(
                    staticHeader[5]);
            const std::uint64_t occupiedBytes =
                sourceEntry.pageOffset +
                payloadBytes;
            const std::uint64_t pageCount =
                (occupiedBytes + PageSize - 1) /
                PageSize;
            const std::uint64_t extentEnd =
                staticDataOffset +
                (static_cast<std::uint64_t>(
                     sourceEntry.pageNum) +
                 pageCount) *
                    PageSize;
            if (pageCount == 0 ||
                pageCount >
                    (std::numeric_limits<
                         std::uint16_t>::max)() ||
                extentEnd > staticFileBytes) {
                continue;
            }
            overlapSource = source;
            overlapTarget = target;
            overlapPageCount =
                static_cast<std::uint16_t>(
                    pageCount);
            break;
        }
    }
    BOOST_REQUIRE_LT(
        overlapSource, staticEntries.size());
    BOOST_REQUIRE_LT(
        overlapTarget, staticEntries.size());
    {
        std::fstream corrupt(
            staticPostingPath,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(corrupt.good());
        const auto& source =
            staticEntries[overlapSource];
        const auto& target =
            staticEntries[overlapTarget];
        corrupt.seekp(target.offset);
        corrupt.write(
            reinterpret_cast<const char*>(
                &source.pageNum),
            sizeof(source.pageNum));
        corrupt.write(
            reinterpret_cast<const char*>(
                &source.pageOffset),
            sizeof(source.pageOffset));
        corrupt.seekp(
            target.offset +
            static_cast<std::streamoff>(
                sizeof(std::int32_t) +
                sizeof(std::uint16_t) +
                sizeof(std::int32_t)));
        corrupt.write(
            reinterpret_cast<const char*>(
                &overlapPageCount),
            sizeof(overlapPageCount));
        BOOST_REQUIRE(corrupt.good());
    }
    std::shared_ptr<VectorIndex>
        overlappingStaticPosting;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory,
            overlappingStaticPosting) !=
        ErrorCode::Success);
    overlappingStaticPosting.reset();
    std::filesystem::copy_file(
        staticPostingBackup, staticPostingPath,
        std::filesystem::copy_options::
            overwrite_existing);

    const auto validStaticBytes =
        std::filesystem::file_size(
            staticPostingPath);
    BOOST_REQUIRE_GT(
        validStaticBytes,
        static_cast<std::uintmax_t>(
            PageSize));
    std::filesystem::resize_file(
        staticPostingPath,
        validStaticBytes - PageSize);
    std::shared_ptr<VectorIndex>
        truncatedStaticPosting;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory,
            truncatedStaticPosting) !=
        ErrorCode::Success);
    truncatedStaticPosting.reset();
    std::filesystem::copy_file(
        staticPostingBackup, staticPostingPath,
        std::filesystem::copy_options::
            overwrite_existing);
    std::filesystem::remove(
        staticPostingBackup);

    const std::string loaderPath =
        indexDirectory + "/indexloader.ini";
    std::ifstream loaderInput(loaderPath);
    BOOST_REQUIRE(loaderInput.good());
    const std::string loaderConfig(
        (std::istreambuf_iterator<char>(
             loaderInput)),
        std::istreambuf_iterator<char>());
    loaderInput.close();
    {
        std::string unsafeConfig = loaderConfig;
        const std::string key =
            "HierarchyHeadIndexFolder=";
        const size_t begin =
            unsafeConfig.find(key);
        BOOST_REQUIRE(begin !=
                      std::string::npos);
        const size_t valueBegin =
            begin + key.size();
        const size_t end =
            unsafeConfig.find(
                '\n', valueBegin);
        unsafeConfig.replace(
            valueBegin,
            end == std::string::npos
                ? std::string::npos
                : end - valueBegin,
            "..");
        std::ofstream unsafeLoader(
            loaderPath, std::ios::trunc);
        BOOST_REQUIRE(unsafeLoader.good());
        unsafeLoader << unsafeConfig;
        BOOST_REQUIRE(unsafeLoader.good());
    }
    std::shared_ptr<VectorIndex> unsafeReload;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory,
            unsafeReload) !=
        ErrorCode::Success);
    {
        std::ofstream restoredLoader(
            loaderPath, std::ios::trunc);
        BOOST_REQUIRE(restoredLoader.good());
        restoredLoader << loaderConfig;
        BOOST_REQUIRE(restoredLoader.good());
    }

    std::string disabledConfig = loaderConfig;
    const std::string limitedKey =
        "EnableLimitedTagPosting=";
    const size_t limitedBegin =
        disabledConfig.find(limitedKey);
    BOOST_REQUIRE(
        limitedBegin != std::string::npos);
    const size_t limitedValueBegin =
        limitedBegin + limitedKey.size();
    const size_t limitedEnd =
        disabledConfig.find(
            '\n', limitedValueBegin);
    disabledConfig.replace(
        limitedValueBegin,
        limitedEnd == std::string::npos
            ? std::string::npos
            : limitedEnd - limitedValueBegin,
        "false");
    {
        std::ofstream disabledLoader(
            loaderPath, std::ios::trunc);
        BOOST_REQUIRE(disabledLoader.good());
        disabledLoader << disabledConfig;
        BOOST_REQUIRE(disabledLoader.good());
    }
    std::shared_ptr<VectorIndex>
        disabledLimitedMode;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory,
            disabledLimitedMode) !=
        ErrorCode::Success);
    {
        std::ofstream restoredLoader(
            loaderPath, std::ios::trunc);
        BOOST_REQUIRE(restoredLoader.good());
        restoredLoader << loaderConfig;
        BOOST_REQUIRE(restoredLoader.good());
    }

    const std::string backupPath =
        supportPath + ".backup";
    std::filesystem::copy_file(
        supportPath, backupPath,
        std::filesystem::copy_options::
            overwrite_existing);
    {
        std::fstream corrupt(
            supportPath,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(corrupt.good());
        corrupt.seekg(-1, std::ios::end);
        char byte = 0;
        corrupt.read(&byte, 1);
        corrupt.clear();
        corrupt.seekp(-1, std::ios::end);
        byte ^= 0x5a;
        corrupt.write(&byte, 1);
        BOOST_REQUIRE(corrupt.good());
    }
    std::shared_ptr<VectorIndex> rejected;
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory, rejected) ==
        ErrorCode::Fail);
    std::filesystem::copy_file(
        backupPath, supportPath,
        std::filesystem::copy_options::
            overwrite_existing);

    const std::string secondPostingPath =
        indexDirectory +
        "/second_level_head_postings.bin";
    const std::string secondPostingBackup =
        secondPostingPath + ".backup";
    std::filesystem::copy_file(
        secondPostingPath,
        secondPostingBackup,
        std::filesystem::copy_options::
            overwrite_existing);
    {
        std::fstream corrupt(
            secondPostingPath,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(corrupt.good());
        corrupt.seekg(-1, std::ios::end);
        char byte = 0;
        corrupt.read(&byte, 1);
        corrupt.clear();
        corrupt.seekp(-1, std::ios::end);
        byte ^= 0x3c;
        corrupt.write(&byte, 1);
        BOOST_REQUIRE(corrupt.good());
    }
    rejected.reset();
    BOOST_CHECK(
        VectorIndex::LoadIndex(
            indexDirectory, rejected) ==
        ErrorCode::Fail);
    std::filesystem::copy_file(
        secondPostingBackup,
        secondPostingPath,
        std::filesystem::copy_options::
            overwrite_existing);
    std::filesystem::remove_all(indexDirectory);
}

BOOST_AUTO_TEST_CASE(StaticHybridSinglePostingRetainsSelfContainedOriginalTail)
{
    const auto edge =
        [](SizeType node, float distance,
           SizeType tonode) {
            Edge value;
            value.node = node;
            value.distance = distance;
            value.tonode = tonode;
            return value;
        };

    SPANN::Selection hybrid(0, ".");
    hybrid.m_selections = {
        edge(0, 100.0f, 10),
        edge(0, 200.0f, 40),
        edge(0, 300.0f, 99),
        edge(1, 50.0f, 50),
        Edge(),
    };
    hybrid.m_start = 0;
    hybrid.m_end = hybrid.m_selections.size();
    hybrid.m_totalsize = hybrid.m_end;

    SPANN::Selection original(0, ".");
    original.m_selections = {
        edge(0, 5.0f, 20),
        edge(0, 1.0f, 10),
        edge(0, 2.0f, 30),
        edge(1, 4.0f, 60),
        edge(1, 1.0f, 70),
        edge(1, 3.0f, 50),
    };
    original.m_start = 0;
    original.m_end = original.m_selections.size();
    original.m_totalsize = original.m_end;

    std::vector<std::atomic_int> postingSizes(2);
    for (auto& size : postingSizes) size = 0;
    const std::vector<int> hybridPureSizes = {2, 1};
    const std::vector<int> originalPostingSizes = {3, 3};
    SPANN::ExtraStaticSearcher<float> searcher;
    BOOST_REQUIRE(
        searcher.MergeConstrainedPureWithOriginalPosting(
            hybrid, postingSizes,
            hybridPureSizes, original,
            originalPostingSizes));

    const std::array<SizeType, 9> expectedVIDs = {
        10, 40, 10, 30, 20,
        50, 70, 50, 60,
    };
    const std::array<SizeType, 9> expectedHeads = {
        0, 0, 0, 0, 0,
        1, 1, 1, 1,
    };
    BOOST_REQUIRE_EQUAL(
        hybrid.m_selections.size(),
        expectedVIDs.size());
    for (size_t record = 0;
         record < expectedVIDs.size(); ++record) {
        BOOST_CHECK_EQUAL(
            hybrid.m_selections[record].node,
            expectedHeads[record]);
        BOOST_CHECK_EQUAL(
            hybrid.m_selections[record].tonode,
            expectedVIDs[record]);
    }
    BOOST_CHECK_EQUAL(postingSizes[0].load(), 5);
    BOOST_CHECK_EQUAL(postingSizes[1].load(), 4);
    BOOST_CHECK_EQUAL(hybrid.m_start, 0);
    BOOST_CHECK_EQUAL(
        hybrid.m_end,
        hybrid.m_selections.size());
    BOOST_CHECK_EQUAL(
        hybrid.m_totalsize,
        hybrid.m_selections.size());
}

BOOST_AUTO_TEST_CASE(StaticGlobalTailRangeStartsAtPureBoundary)
{
    SPANN::ExtraWorkSpace::PostingReadRange
        range;
    range.SetContiguousRecordRange(
        128, 300, 600, 20);

    BOOST_CHECK_EQUAL(range.m_scanBegin, 300);
    BOOST_CHECK_EQUAL(range.m_scanEnd, 600);
    BOOST_CHECK_EQUAL(range.ScanCount(), 300);
    BOOST_CHECK_EQUAL(range.m_readStartPage, 1);
    BOOST_CHECK_EQUAL(range.m_readPageCount, 2);

    int begin = -1;
    int end = -1;
    BOOST_CHECK(range.GetScanRange(0, begin, end));
    BOOST_CHECK_EQUAL(begin, 300);
    BOOST_CHECK_EQUAL(end, 600);
    BOOST_CHECK(
        !range.GetScanRange(1, begin, end));
}

BOOST_AUTO_TEST_CASE(LimitedTagPageRangeClampsWholeRecordsAtBothBoundaries)
{
    SPANN::ExtraWorkSpace::PostingReadRange range;
    range.SetContiguousRecordRange(128, 0, 146, 140);
    range.LimitContiguousPages(3, 128, 140);
    BOOST_CHECK_EQUAL(range.m_readStartPage, 0);
    BOOST_CHECK_EQUAL(range.m_readPageCount, 3);
    BOOST_CHECK_EQUAL(range.m_scanBegin, 0);
    BOOST_CHECK_EQUAL(range.m_scanEnd, (3 * PageSize - 128) / 140);
    range.SetContiguousRecordRange(128, 146, 292, 140);
    range.LimitContiguousPages(3, 128, 140);
    BOOST_CHECK_EQUAL(range.m_readStartPage, 5);
    BOOST_CHECK_EQUAL(range.m_readPageCount, 3);
    BOOST_CHECK_EQUAL(range.m_scanBegin, 146);
    BOOST_CHECK_EQUAL(range.m_scanEnd, (8 * PageSize - 128) / 140);
    range.SetContiguousRecordRange(4090, 0, 1, 140);
    range.LimitContiguousPages(1, 4090, 140);
    BOOST_CHECK_EQUAL(range.ScanCount(), 0);
    BOOST_CHECK_EQUAL(range.m_readPageCount, 0);
    range.SetContiguousRecordRange(128, 146, 292, 140);
    range.LimitContiguousPages(0, 128, 140);
    BOOST_CHECK_EQUAL(range.ScanCount(), 146);
    // Full H (125 normal records plus rescue) does not bypass the
    // canonical 12-page search budget, nor does an O suffix after it.
    range.SetContiguousRecordRange(128, 0, 150, 524);
    range.LimitContiguousPages(12, 128, 524);
    BOOST_CHECK_EQUAL(range.m_readPageCount, 12);
    BOOST_CHECK_EQUAL(range.m_scanEnd, (12 * PageSize - 128) / 524);
    BOOST_CHECK_LT(range.m_scanEnd, 125);
    range.SetContiguousRecordRange(128, 150, 275, 524);
    const int originalStartPage = range.m_readStartPage;
    range.LimitContiguousPages(12, 128, 524);
    BOOST_CHECK_EQUAL(range.m_readPageCount, 12);
    BOOST_CHECK_EQUAL(range.m_scanBegin, 150);
    BOOST_CHECK_EQUAL(range.m_scanEnd, ((originalStartPage + 12) * PageSize - 128) / 524);
    BOOST_CHECK_LT(range.m_scanEnd, 275);
}

BOOST_AUTO_TEST_CASE(StaticHybridWorkspaceResetAndOptionDefaults)
{
    SPANN::Options opt;
    BOOST_CHECK(!opt.m_enableHybridDistance);
    BOOST_CHECK_EQUAL(opt.m_hybridVectorWeight, 1.0f);
    BOOST_CHECK(opt.m_hybridCategoricalCols.empty());
    BOOST_CHECK(opt.m_hybridCategoricalWeights.empty());
    BOOST_CHECK(opt.m_hybridNumericCols.empty());
    BOOST_CHECK(opt.m_hybridNumericWeights.empty());
    BOOST_CHECK_EQUAL(opt.m_hybridCandidateCount, 128);
    SPANN::ExtraWorkSpace workspace;
    workspace.m_postingProbeStats.m_dedupSkippedVectors = 7;
    workspace.m_postingProbeStats.Reset();
    BOOST_CHECK_EQUAL(
        workspace.m_postingProbeStats.m_dedupSkippedVectors, 0U);
    workspace.m_useHybridPure = true;
    workspace.m_scanFullPostingForFilter = true;
    workspace.m_limitedTagRegionsReadySnapshotValid = true;
    workspace.m_limitedTagRegionsReadySnapshot = true;
    workspace.Initialize(8, 4, 2, PageSize, false, false);
    BOOST_CHECK(!workspace.m_useHybridPure);
    BOOST_CHECK(!workspace.m_scanFullPostingForFilter);
    BOOST_CHECK(
        !workspace
             .m_limitedTagRegionsReadySnapshotValid);
    BOOST_CHECK(
        !workspace
             .m_limitedTagRegionsReadySnapshot);

    workspace.m_useHybridPure = true;
    workspace.m_scanFullPostingForFilter = true;
    workspace.m_limitedTagRegionsReadySnapshotValid = true;
    workspace.m_limitedTagRegionsReadySnapshot = true;
    workspace.Clear(2, PageSize, false, false);
    BOOST_CHECK(!workspace.m_useHybridPure);
    BOOST_CHECK(!workspace.m_scanFullPostingForFilter);
    BOOST_CHECK(
        !workspace
             .m_limitedTagRegionsReadySnapshotValid);
    BOOST_CHECK(
        !workspace
             .m_limitedTagRegionsReadySnapshot);

    workspace.SetAsyncContextID(17);
    workspace.Clear(4, PageSize * 2, false, false);
    BOOST_REQUIRE_EQUAL(workspace.m_diskRequests.size(), 4);
    for (const auto& request : workspace.m_diskRequests) {
        BOOST_CHECK_EQUAL(request.m_status, 17);
    }
}

BOOST_AUTO_TEST_CASE(StaticHybridCrossEdgeHeader)
{
    Helper::HeadCrossEdgesHeader header{};
    header.magic = Helper::kHeadCrossEdgesMagic;
    header.version =
        Helper::kHybridHeadCrossEdgesVersion;
    header.totalHeads = 64;
    header.maxEdgesPerHead = 16;
    header.searchTopK = 32;
    header.reserved =
        Helper::kHybridHeadCrossEdgesMarker;
    BOOST_CHECK_EQUAL(
        sizeof(header), 6 * sizeof(std::int32_t));
    BOOST_CHECK_EQUAL(
        header.maxEdgesPerHead, 16);
    BOOST_CHECK_EQUAL(
        header.reserved,
        Helper::kHybridHeadCrossEdgesMarker);
    Helper::HybridHeadCrossEdgesExtension extension{
        1234, 5678};
    BOOST_CHECK_EQUAL(
        sizeof(extension),
        2 * sizeof(std::uint64_t));
}

BOOST_AUTO_TEST_CASE(BKTHybridCollapsedSiblingAdmissionAndEdges)
{
    constexpr SizeType duplicateCount = 1024;
    constexpr SizeType vectorCount =
        duplicateCount + 1;
    constexpr DimensionType dimension = 2;
    constexpr SizeType target = duplicateCount;
    std::vector<float> vectors(
        static_cast<size_t>(vectorCount) *
            dimension,
        0.0f);
    vectors[static_cast<size_t>(target) *
                dimension] = 100.0f;
    vectors[static_cast<size_t>(target) *
                dimension +
            1] = 100.0f;

    BKT::Index<float> index;
    BOOST_REQUIRE(
        index.SetParameter(
            "DistCalcMethod", "L2") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "BKTNumber", "1") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "BKTKmeansK", "4") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "BKTLeafSize", "2") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "NeighborhoodSize", "8") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "GraphNeighborhoodScale", "1") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "TPTNumber", "1") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "TPTLeafSize", "64") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "NumTopDimensionTpTreeSplit", "2") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "CEF", "64") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "MaxCheckForRefineGraph", "64") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "RefineIterations", "1") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "NumberOfInitialDynamicPivots",
            "1") == ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "NumberOfOtherDynamicPivots",
            "1") == ErrorCode::Success);
    BOOST_REQUIRE(
        index.SetParameter(
            "NumberOfThreads", "1") ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        index.BuildIndex(
            vectors.data(), vectorCount,
            dimension, false, false) ==
        ErrorCode::Success);

    auto& graph = index.GetMutableGraph();
    const DimensionType localEdges =
        index.GetNeighborhoodSize();
    SizeType representative = -1;
    for (SizeType row = 0;
         row < duplicateCount; ++row) {
        if (graph[row][localEdges - 1] < -1) {
            representative = row;
            break;
        }
    }
    BOOST_REQUIRE_GE(representative, 0);
    SizeType sibling =
        representative == 0 ? 1 : 0;
    BOOST_REQUIRE(
        graph.SetRuntimeEdgeSuffixSize(1) ==
        ErrorCode::Success);

    std::vector<SizeType> localToGlobal(
        static_cast<size_t>(vectorCount));
    std::iota(
        localToGlobal.begin(),
        localToGlobal.end(), 0);
    BKT::Index<float>::
        CrossGraphSearchContext context;
    context.m_nodes.push_back(
        {&index, &localToGlobal});
    context.m_entryNode = 0;
    context.m_locatorLocalBits = 11;
    context.m_locatorLocalMask = 0x7ff;

    BKT::Index<float>::
        CrossGraphSearchContext originalContext =
            context;
    COMMON::QueryResultSet<float>
        legacySearch(vectors.data(), 1);
    BOOST_REQUIRE(
        index.SearchIndexWithCrossEdges(
            legacySearch, originalContext,
            128) == ErrorCode::Success);

    context.m_useHybridDistance = true;
    context.m_queryDistance =
        [representative, sibling](
            int, SizeType local, float) {
            if (local == sibling) return 1.0f;
            if (local == representative) {
                return 10.0f;
            }
            return 20.0f;
        };
    COMMON::QueryResultSet<float>
        admission(vectors.data(), 1);
    BOOST_REQUIRE(
        admission.AddPoint(1000, 5.0f));
    BOOST_REQUIRE(
        index.SearchIndexWithCrossEdges(
            admission, context, 128) ==
        ErrorCode::Success);
    BOOST_CHECK_EQUAL(
        admission.GetResult(0)->VID,
        sibling);

    context.m_queryDistance =
        [](int, SizeType local, float) {
            return local == target
                ? 0.0f
                : 10.0f;
        };
    // The native RNG need not connect a collapsed cluster to this distant point.
    const SizeType originalLocalEdge = graph[representative][0];
    graph[representative][0] = target;
    COMMON::QueryResultSet<float>
        localBridge(vectors.data(), 1);
    BOOST_REQUIRE(
        index.SearchIndexWithCrossEdges(
            localBridge, context, 128) ==
        ErrorCode::Success);
    graph[representative][0] = originalLocalEdge;
    BOOST_CHECK_EQUAL(
        localBridge.GetResult(0)->VID,
        target);

    graph.RuntimeEdgeSuffix(sibling)[0] =
        target;
    COMMON::QueryResultSet<float>
        siblingEdge(vectors.data(), 1);
    BKT::Index<float>::
        CrossGraphSearchStats stats;
    BOOST_REQUIRE(
        index.SearchIndexWithCrossEdges(
            siblingEdge, context, 128,
            &stats) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(
        siblingEdge.GetResult(0)->VID,
        target);
    BOOST_CHECK_GT(stats.m_crossEdges, 0);

    SizeType* targetEdges = graph[target];
    std::fill_n(
        targetEdges, localEdges,
        static_cast<SizeType>(-1));
    targetEdges[0] = representative;
    COMMON::QueryResultSet<float>
        filteredCollapsed(
            vectors.data() +
                static_cast<size_t>(target) *
                    dimension,
            1);
    BOOST_REQUIRE(
        index.SearchIndexWithResultFilter(
            filteredCollapsed,
            [sibling](SizeType id) {
                return id == sibling;
            },
            16) == ErrorCode::Success);
    BOOST_CHECK_EQUAL(
        filteredCollapsed.GetResult(0)->VID,
        sibling);
}

BOOST_AUTO_TEST_CASE(TaggedPureTailUpdate)
{
    constexpr SizeType baseCount = 1024;
    constexpr DimensionType dimension = 100;
    constexpr int tagCount = 5;
    constexpr int pqChunks = 2;
    const std::string indexDirectory = "tagged_pure_tail_update_index";
    const std::string checkpointDirectory = indexDirectory + "_checkpoint";
    const std::string pivotsPath = indexDirectory + "/pipepq_pivots.bin";
    const std::string codesPath = indexDirectory + "/pipepq_codes.bin";
    const std::string basePath = indexDirectory + "/base.fbin";
    std::filesystem::remove_all(indexDirectory);
    std::filesystem::remove_all(checkpointDirectory);
    std::filesystem::create_directories(indexDirectory);
    std::filesystem::create_directories(checkpointDirectory);

    auto writeOrFail = [](std::ofstream& out, const void* data, size_t bytes) {
        out.write(reinterpret_cast<const char*>(data), static_cast<std::streamsize>(bytes));
        BOOST_REQUIRE(out.good());
    };
    auto writeMatrix = [&](std::ofstream& out, std::uint32_t rows, std::uint32_t cols,
                           const void* data, size_t bytes) {
        writeOrFail(out, &rows, sizeof(rows));
        writeOrFail(out, &cols, sizeof(cols));
        if (bytes > 0) writeOrFail(out, data, bytes);
    };

    {
        std::vector<float> table(static_cast<size_t>(256) * dimension);
        for (int center = 0; center < 256; ++center) {
            for (int dim = 0; dim < dimension; ++dim) {
                table[static_cast<size_t>(center) * dimension + dim] = static_cast<float>(center);
            }
        }
        const std::vector<float> centroid(dimension, 0.0f);
        const std::uint32_t chunkOffsets[] = {0, 50, 100};
        constexpr std::uint64_t headerBytes = sizeof(std::uint32_t) * 2 + sizeof(std::uint64_t) * 5;
        const std::uint64_t tableOffset = headerBytes;
        const std::uint64_t centroidOffset =
            tableOffset + sizeof(std::uint32_t) * 2 + table.size() * sizeof(float);
        const std::uint64_t chunkOffset =
            centroidOffset + sizeof(std::uint32_t) * 2 + centroid.size() * sizeof(float);
        const std::uint32_t rootRows = 5;
        const std::uint32_t rootCols = 1;
        const std::uint64_t offsets[] = {tableOffset, centroidOffset, 0, chunkOffset, 0};
        std::ofstream out(pivotsPath, std::ios::binary | std::ios::trunc);
        writeOrFail(out, &rootRows, sizeof(rootRows));
        writeOrFail(out, &rootCols, sizeof(rootCols));
        writeOrFail(out, offsets, sizeof(offsets));
        writeMatrix(out, 256, dimension, table.data(), table.size() * sizeof(float));
        writeMatrix(out, dimension, 1, centroid.data(), centroid.size() * sizeof(float));
        writeMatrix(out, pqChunks + 1, 1, chunkOffsets, sizeof(chunkOffsets));
    }
    SPANN::PipePQTable pipePQ;
    BOOST_REQUIRE(pipePQ.Load(pivotsPath, pqChunks));
    std::array<float, dimension> encodedVector {};
    encodedVector.fill(250.0f);
    std::array<std::uint8_t, pqChunks> encodedCode {};
    pipePQ.Encode(encodedVector.data(), encodedCode.data());
    BOOST_CHECK_EQUAL(encodedCode[0], 250);
    BOOST_CHECK_EQUAL(encodedCode[1], 250);

    ByteArray baseBytes = ByteArray::Alloc(sizeof(float) * static_cast<size_t>(baseCount) * dimension);
    auto* base = reinterpret_cast<float*>(baseBytes.Data());
    for (SizeType row = 0; row < baseCount; ++row) {
        for (int dim = 0; dim < dimension; ++dim) {
            std::uint32_t value = static_cast<std::uint32_t>(row + 1) *
                                  static_cast<std::uint32_t>(dim * 97 + 41);
            value ^= value >> 13;
            value *= 0x85ebca6bU;
            value ^= value >> 16;
            base[static_cast<size_t>(row) * dimension + dim] = static_cast<float>(value & 0xffffU);
        }
    }
    {
        std::ofstream out(basePath, std::ios::binary | std::ios::trunc);
        writeMatrix(out, baseCount, dimension, base,
                    sizeof(float) * static_cast<size_t>(baseCount) * dimension);
    }
    {
        std::vector<std::uint8_t> codes(static_cast<size_t>(baseCount) * pqChunks);
        for (SizeType row = 0; row < baseCount; ++row) {
            pipePQ.Encode(base + static_cast<size_t>(row) * dimension,
                          codes.data() + static_cast<size_t>(row) * pqChunks);
        }
        std::ofstream out(codesPath, std::ios::binary | std::ios::trunc);
        writeMatrix(out, baseCount, pqChunks, codes.data(), codes.size());
    }
    auto vectors = std::make_shared<BasicVectorSet>(baseBytes, VectorValueType::Float, dimension, baseCount);
    std::vector<std::uint32_t> baseTags(static_cast<size_t>(baseCount) * tagCount);
    for (SizeType row = 0; row < baseCount; ++row) {
        std::uint32_t* tags = baseTags.data() + static_cast<size_t>(row) * tagCount;
        tags[0] = 1;
        tags[1] = 2;
        tags[2] = 3;
        tags[3] = 4;
        tags[4] = 5;
    }

    auto index = VectorIndex::CreateInstance(IndexAlgoType::SPANN, VectorValueType::Float);
    BOOST_REQUIRE(index != nullptr);
    const auto set = [&](const char* section, const char* key, const std::string& value) {
        BOOST_REQUIRE(index->SetParameter(key, value.c_str(), section) == ErrorCode::Success);
    };
    set("Base", "DistCalcMethod", "L2");
    set("Base", "IndexAlgoType", "KDT");
    set("Base", "ValueType", "Float");
    set("Base", "Dim", std::to_string(dimension));
    set("Base", "IndexDirectory", indexDirectory);
    set("SelectHead", "isExecute", "true");
    set("SelectHead", "NumberOfThreads", "2");
    set("SelectHead", "SelectHeadType", "BKT");
    set("SelectHead", "SelectThreshold", "0");
    set("SelectHead", "SplitFactor", "0");
    set("SelectHead", "SplitThreshold", "0");
    set("SelectHead", "Ratio", "0.25");
    set("BuildHead", "isExecute", "true");
    set("BuildHead", "NumberOfThreads", "2");
    set("BuildSSDIndex", "isExecute", "true");
    set("BuildSSDIndex", "BuildSsdIndex", "true");
    set("BuildSSDIndex", "InternalResultNum", "32");
    set("BuildSSDIndex", "SearchInternalResultNum", "32");
    set("BuildSSDIndex", "NumberOfThreads", "2");
    set("BuildSSDIndex", "PostingPageLimit", "1");
    set("BuildSSDIndex", "SearchPostingPageLimit", "1");
    set("BuildSSDIndex", "Storage", "FILEIO");
    set("BuildSSDIndex", "SpdkBatchSize", "16");
    set("BuildSSDIndex", "CacheSizeGB", "1");
    set("BuildSSDIndex", "PersistentBufferPath", checkpointDirectory);
    set("BuildSSDIndex", "ExcludeHead", "true");
    set("BuildSSDIndex", "ResultNum", "10");
    set("BuildSSDIndex", "SearchThreadNum", "2");
    set("BuildSSDIndex", "Update", "false");
    set("BuildSSDIndex", "BufferLength", "1");
    set("BuildSSDIndex", "StartFileSizeGB", "1");
    set("BuildSSDIndex", "ConsistencyCheck", "true");
    set("BuildSSDIndex", "ChecksumCheck", "true");
    set("BuildSSDIndex", "ChecksumInRead", "true");
    set("BuildSSDIndex", "AsyncAppendQueueSize", "0");
    set("BuildSSDIndex", "ReplicaCount", "2");
    set("BuildSSDIndex", "TailReplicaCount", "0");
    set("BuildSSDIndex", "UnfilterTailBufferLength", "1");
    set("BuildSSDIndex", "NumTagsPerVec", std::to_string(tagCount));
    set("BuildSSDIndex", "PostingQuantizer", "PipePQ");
    set("BuildSSDIndex", "PostingQuantM", std::to_string(pqChunks));
    set("BuildSSDIndex", "PostingQuantizerFile", "pipepq_codes.bin");
    set("BuildSSDIndex", "PipePQPivotsFile", "pipepq_pivots.bin");
    set("BuildSSDIndex", "FullVectorFile", basePath);
    set("BuildSSDIndex", "RerankL", "10");

    auto* spannInterface = dynamic_cast<SPANN::ISPANNIndex*>(index.get());
    BOOST_REQUIRE(spannInterface != nullptr);
    spannInterface->SetVectorTags(baseTags.data(), baseCount, tagCount);
    std::vector<std::vector<SizeType>> nodeAssignments(2);
    for (SizeType row = 0; row < baseCount; ++row) {
        nodeAssignments[static_cast<size_t>(row % 2)].push_back(row);
    }
    spannInterface->SetNodeVectorAssignments(nodeAssignments);
    spannInterface->SetPrimaryNodeVectorAssignments(nodeAssignments);
    BOOST_REQUIRE(index->BuildIndex(vectors, nullptr, true, false, false) == ErrorCode::Success);

    std::array<float, dimension> updateVector {};
    updateVector.fill(250.0f);
    const std::array<std::uint32_t, tagCount> updateTags = {17, 18, 19, 20, 21};
    auto* spann = dynamic_cast<SPANN::Index<float>*>(index.get());
    BOOST_REQUIRE(spann != nullptr);
    spann->GetOptions()->m_tailReplicaCount = 2;
    BOOST_REQUIRE(spann->AddIndexWithTags(updateVector.data(), 1, dimension, updateTags.data(),
                                          tagCount, true) == ErrorCode::Success);
    const SizeType updateVID = baseCount;

    auto inspectCopies = [&](const std::shared_ptr<VectorIndex>& inspected,
                             SizeType vid, SizeType& pureCopies, SizeType& tailCopies,
                             SizeType& firstTailPosting,
                             std::array<std::uint8_t, pqChunks>* firstPureCode,
                             SizeType* firstPurePosting) {
        auto* disk = dynamic_cast<SPANN::ExtraDynamicSearcher<float>*>(
            dynamic_cast<SPANN::ISPANNIndex*>(inspected.get())->GetDiskIndex().get());
        BOOST_REQUIRE(disk != nullptr);
        SPANN::ExtraWorkSpace workspace;
        disk->InitWorkSpace(&workspace, false);
        pureCopies = 0;
        tailCopies = 0;
        firstTailPosting = -1;
        if (firstPurePosting != nullptr) *firstPurePosting = -1;
        const SizeType postingCount = disk->GetTaggedPostingCount();
        BOOST_REQUIRE_GT(postingCount, 0);
        const size_t metadataSize = sizeof(SizeType) + sizeof(std::uint8_t) +
                                    static_cast<size_t>(tagCount) * sizeof(std::uint32_t);
        const size_t stride = metadataSize + pqChunks;
        for (SizeType posting = 0; posting < postingCount; ++posting) {
            SPANN::TaggedPostingSnapshot postingSnapshot;
            BOOST_REQUIRE(disk->GetTaggedPostingSnapshot(
                              &workspace, posting, postingSnapshot) == ErrorCode::Success);
            if (postingSnapshot.m_records.empty()) continue; // tombstoned split/merge head
            const std::string& bytes = postingSnapshot.m_records;
            const int pureCount = disk->GetPureCount(posting);
            BOOST_REQUIRE_EQUAL(bytes.size() % stride, 0U);
            for (size_t offset = 0, record = 0; offset < bytes.size(); offset += stride, ++record) {
                SizeType recordVID = -1;
                memcpy(&recordVID, bytes.data() + offset, sizeof(recordVID));
                if (recordVID != vid) continue;
                if (static_cast<int>(record) < pureCount) {
                    ++pureCopies;
                    if (firstPurePosting != nullptr &&
                        *firstPurePosting < 0) {
                        *firstPurePosting = posting;
                    }
                    if (firstPureCode != nullptr) {
                        memcpy(firstPureCode->data(), bytes.data() + offset + metadataSize,
                               firstPureCode->size());
                    }
                } else {
                    ++tailCopies;
                    if (firstTailPosting < 0) firstTailPosting = posting;
                }
            }
        }
    };

    SizeType pureCopies = 0;
    SizeType tailCopies = 0;
    SizeType purePosting = -1;
    SizeType tailPosting = -1;
    std::array<std::uint8_t, pqChunks> pureCode {};
    inspectCopies(index, updateVID, pureCopies, tailCopies, tailPosting, &pureCode,
                  &purePosting);
    BOOST_REQUIRE(pureCopies > 0);
    BOOST_REQUIRE(tailCopies > 0);
    BOOST_REQUIRE(purePosting >= 0);
    BOOST_REQUIRE(tailPosting >= 0);
    BOOST_CHECK_EQUAL(pureCode[0], 250);
    BOOST_CHECK_EQUAL(pureCode[1], 250);
    BOOST_REQUIRE(std::filesystem::exists(indexDirectory + "/update_vectors.bin"));
    BOOST_REQUIRE(index->SaveIndex(indexDirectory) == ErrorCode::Success);
    index.reset();

    std::shared_ptr<VectorIndex> reloaded;
    BOOST_REQUIRE(VectorIndex::LoadIndex(indexDirectory, reloaded) == ErrorCode::Success);
    SizeType reloadedPureCopies = 0;
    SizeType reloadedTailCopies = 0;
    SizeType reloadedPurePosting = -1;
    SizeType reloadedTailPosting = -1;
    inspectCopies(reloaded, updateVID, reloadedPureCopies, reloadedTailCopies,
                  reloadedTailPosting, nullptr, &reloadedPurePosting);
    BOOST_CHECK_EQUAL(reloadedPureCopies, pureCopies);
    BOOST_CHECK_EQUAL(reloadedTailCopies, tailCopies);
    BOOST_REQUIRE(reloadedPurePosting >= 0);

    const auto containsVID = [&](const std::vector<SizeType>& postings,
                                 const std::vector<std::uint32_t>& queryTags) {
        VectorIndex::ThreadLocalSearchContext context;
        context.m_active = true;
        context.m_queryTags = queryTags;
        context.m_limitedTagMembershipEligible =
            !queryTags.empty();
        context.m_limitedTagQueryValues =
            queryTags;
        VectorIndex::ThreadLocalSearchContextGuard guard(std::move(context));
        COMMON::QueryResultSet<float> result(updateVector.data(), 10);
        BOOST_REQUIRE(reloaded->SearchIndex(result) == ErrorCode::Success);
        for (int i = 0; i < result.GetResultNum(); ++i) {
            if (result.GetResult(i)->VID == updateVID) return true;
        }
        return false;
    };

    BOOST_CHECK(containsVID({tailPosting}, {}));
    BOOST_CHECK(!containsVID({tailPosting}, {updateTags[0]}));
    auto* reloadedSpann = dynamic_cast<SPANN::Index<float>*>(reloaded.get());
    BOOST_REQUIRE(reloadedSpann != nullptr);
    auto* reloadedDisk = dynamic_cast<SPANN::ExtraDynamicSearcher<float>*>(
        dynamic_cast<SPANN::ISPANNIndex*>(reloaded.get())->GetDiskIndex().get());
    BOOST_REQUIRE(reloadedDisk != nullptr);
    reloadedSpann->GetOptions()->m_enableLimitedTagPosting = true;
    reloadedDisk->SetLimitedTagReadRangesReady(true);
    BOOST_CHECK(containsVID({reloadedPurePosting}, {updateTags[0]}));
    BOOST_CHECK(!containsVID({reloadedPurePosting}, {}));
    BOOST_CHECK(!containsVID({reloadedTailPosting}, {updateTags[0]}));
    BOOST_CHECK(containsVID({reloadedTailPosting}, {}));
    reloadedDisk->SetLimitedTagReadRangesReady(false);
    reloadedSpann->GetOptions()->m_enableLimitedTagPosting = false;
    std::vector<SizeType> allPostings;
    const SizeType postingCount = reloadedDisk->GetTaggedPostingCount();
    for (SizeType posting = 0; posting < postingCount; ++posting) allPostings.push_back(posting);
    BOOST_CHECK(containsVID(allPostings, {updateTags[0]}));

    BOOST_REQUIRE(reloadedSpann->DeleteIndex(updateVID) == ErrorCode::Success);
    BOOST_CHECK(!containsVID(allPostings, {updateTags[0]}));
    BOOST_REQUIRE(reloaded->SaveIndex(indexDirectory) == ErrorCode::Success);
    reloaded.reset();

    std::shared_ptr<VectorIndex> afterDelete;
    BOOST_REQUIRE(VectorIndex::LoadIndex(indexDirectory, afterDelete) == ErrorCode::Success);
    reloaded = afterDelete;
    BOOST_CHECK(!containsVID(allPostings, {updateTags[0]}));
    reloadedDisk = dynamic_cast<SPANN::ExtraDynamicSearcher<float>*>(
        dynamic_cast<SPANN::ISPANNIndex*>(reloaded.get())->GetDiskIndex().get());
    BOOST_REQUIRE(reloadedDisk != nullptr);
    const auto readHeadIDCount = [](const std::string& path) {
        std::ifstream input(path, std::ios::binary);
        BOOST_REQUIRE(input.good());
        SizeType count = -1;
        DimensionType dimension = -1;
        input.read(reinterpret_cast<char*>(&count), sizeof(count));
        input.read(reinterpret_cast<char*>(&dimension), sizeof(dimension));
        BOOST_REQUIRE(input.good());
        BOOST_REQUIRE_EQUAL(dimension, 1);
        return count;
    };
    const SizeType node0HeadsBeforeSplit =
        readHeadIDCount(indexDirectory + "/HeadIndex/node_0/SPTAGHeadVectorIDs.bin");
    const SizeType node1HeadsBeforeSplit =
        readHeadIDCount(indexDirectory + "/HeadIndex/node_1/SPTAGHeadVectorIDs.bin");

    // Force the tagged path past the pure posting capacity. The scalar sweep
    // remains in one local region before the split, then supplies two
    // non-degenerate clusters for the normal SPANN split lifecycle.
    constexpr SizeType overflowCount = 400;
    const SizeType headsBeforeSplit = reloadedDisk->GetTaggedPostingCount();
    std::vector<float> overflowVectors(static_cast<size_t>(overflowCount) * dimension);
    std::vector<std::uint32_t> overflowTags(static_cast<size_t>(overflowCount) * tagCount);
    for (SizeType row = 0; row < overflowCount; ++row) {
        const float value = 250.0f + static_cast<float>(row);
        std::fill_n(overflowVectors.data() + static_cast<size_t>(row) * dimension, dimension, value);
        std::copy(updateTags.begin(), updateTags.end(),
                  overflowTags.begin() + static_cast<size_t>(row) * tagCount);
    }
    auto* overflowSpann = dynamic_cast<SPANN::Index<float>*>(reloaded.get());
    BOOST_REQUIRE(overflowSpann != nullptr);
    const ErrorCode overflowRet = overflowSpann->AddIndexWithTags(
        overflowVectors.data(), overflowCount, dimension, overflowTags.data(), tagCount, true);
    BOOST_REQUIRE_MESSAGE(overflowRet == ErrorCode::Success,
                          "forced tagged split returned " << static_cast<int>(overflowRet));
    const SizeType overflowVID = baseCount + 1;
    auto* overflowDisk = dynamic_cast<SPANN::ExtraDynamicSearcher<float>*>(
        dynamic_cast<SPANN::ISPANNIndex*>(reloaded.get())->GetDiskIndex().get());
    BOOST_REQUIRE(overflowDisk != nullptr);
    const SizeType headsAfterSplit = overflowDisk->GetTaggedPostingCount();
    SizeType maxPureCount = 0;
    for (SizeType posting = 0; posting < headsAfterSplit; ++posting) {
        maxPureCount = std::max<SizeType>(maxPureCount, overflowDisk->GetPureCount(posting));
    }
    BOOST_CHECK_LE(maxPureCount, overflowDisk->GetTaggedPureCapacity());
    BOOST_CHECK_GT(headsAfterSplit, headsBeforeSplit);
    SizeType overflowPureCopies = 0;
    SizeType overflowTailCopies = 0;
    SizeType overflowTailPosting = -1;
    inspectCopies(reloaded, overflowVID, overflowPureCopies, overflowTailCopies,
                  overflowTailPosting, nullptr, nullptr);
    BOOST_CHECK_GT(overflowPureCopies, 0);

    // Search-triggered merge maintenance must retain the original lifecycle:
    // queue a low-live posting during a tagged index search, then perform the
    // subset-local merge at the next checkpoint/save.
    auto* reloadedInterface = dynamic_cast<SPANN::ISPANNIndex*>(reloaded.get());
    BOOST_REQUIRE(reloadedInterface != nullptr);
    const std::shared_ptr<VectorIndex> reloadedHeadIndex = reloadedInterface->GetMemoryIndex();
    SPANN::ExtraWorkSpace mergeWorkspace;
    overflowDisk->InitWorkSpace(&mergeWorkspace, false);
    SizeType mergePosting = -1;
    for (SizeType posting = 0; posting < headsAfterSplit; ++posting) {
        SPANN::TaggedPostingSnapshot snapshot;
        BOOST_REQUIRE(overflowDisk->GetTaggedPostingSnapshot(
                          &mergeWorkspace, posting, snapshot) == ErrorCode::Success);
        if (reloadedHeadIndex->ContainSample(posting) &&
            snapshot.m_pureCount > 0 &&
            static_cast<int>(snapshot.m_records.size() /
                             static_cast<size_t>(overflowDisk->GetTaggedRecordSize())) <=
                overflowDisk->GetTaggedMergeThreshold()) {
            mergePosting = posting;
            break;
        }
    }
    BOOST_REQUIRE(mergePosting >= 0);
    const auto countEmptyPostings = [](SPANN::ExtraDynamicSearcher<float>* disk,
                                       SizeType postingCount) {
        SPANN::ExtraWorkSpace workspace;
        disk->InitWorkSpace(&workspace, false);
        SizeType emptyCount = 0;
        for (SizeType posting = 0; posting < postingCount; ++posting) {
            SPANN::TaggedPostingSnapshot snapshot;
            BOOST_REQUIRE(disk->GetTaggedPostingSnapshot(&workspace, posting, snapshot) == ErrorCode::Success);
            if (snapshot.m_records.empty()) ++emptyCount;
        }
        return emptyCount;
    };
    const SizeType emptyBeforeMerge = countEmptyPostings(overflowDisk, headsAfterSplit);
    {
        VectorIndex::ThreadLocalSearchContext context;
        context.m_active = true;
        VectorIndex::ThreadLocalSearchContextGuard guard(std::move(context));
        COMMON::QueryResultSet<float> result(overflowVectors.data(), 10);
        BOOST_REQUIRE(reloaded->SearchIndex(result) == ErrorCode::Success);
    }
    BOOST_REQUIRE(reloaded->SaveIndex(indexDirectory) == ErrorCode::Success);
    const SizeType node0Growth =
        readHeadIDCount(indexDirectory + "/HeadIndex/node_0/SPTAGHeadVectorIDs.bin") -
        node0HeadsBeforeSplit;
    const SizeType node1Growth =
        readHeadIDCount(indexDirectory + "/HeadIndex/node_1/SPTAGHeadVectorIDs.bin") -
        node1HeadsBeforeSplit;
    BOOST_CHECK((node0Growth > 0 && node1Growth == 0) ||
                (node1Growth > 0 && node0Growth == 0));
    reloaded.reset();
    BOOST_REQUIRE(VectorIndex::LoadIndex(indexDirectory, reloaded) == ErrorCode::Success);
    auto* afterMergeDisk = dynamic_cast<SPANN::ExtraDynamicSearcher<float>*>(
        dynamic_cast<SPANN::ISPANNIndex*>(reloaded.get())->GetDiskIndex().get());
    BOOST_REQUIRE(afterMergeDisk != nullptr);
    BOOST_CHECK_EQUAL(afterMergeDisk->GetTaggedPostingCount(), headsAfterSplit);
    BOOST_CHECK_GT(countEmptyPostings(afterMergeDisk, headsAfterSplit), emptyBeforeMerge);
    overflowPureCopies = 0;
    overflowTailCopies = 0;
    overflowTailPosting = -1;
    inspectCopies(reloaded, overflowVID, overflowPureCopies, overflowTailCopies,
                  overflowTailPosting, nullptr, nullptr);
    BOOST_CHECK_GT(overflowPureCopies, 0);
    auto* checkpointSpann = dynamic_cast<SPANN::Index<float>*>(reloaded.get());
    BOOST_REQUIRE(checkpointSpann != nullptr);
    BOOST_REQUIRE(checkpointSpann->Checkpoint() == ErrorCode::Success);
    BOOST_CHECK(std::filesystem::exists(checkpointDirectory + "/SPTAGHeadVectorIDs.bin"));
    BOOST_CHECK(std::filesystem::exists(checkpointDirectory + "/update_vectors.bin"));
    BOOST_CHECK(std::filesystem::exists(
        checkpointDirectory + "/HeadIndex/head_bundle_manifest.bin"));
    BOOST_CHECK(std::filesystem::exists(
        checkpointDirectory + "/HeadIndex/head_metaonly.bin"));
    BOOST_CHECK(!std::filesystem::exists(
        checkpointDirectory + "/HeadIndex/tag_node_index.bin"));
    BOOST_CHECK(std::filesystem::exists(
        checkpointDirectory + "/HeadIndex/head_cross_edges.dirty"));
    BOOST_CHECK(std::filesystem::exists(
        checkpointDirectory + "/HeadIndex/node_0/vectors.bin"));
    BOOST_CHECK(std::filesystem::exists(
        checkpointDirectory + "/HeadIndex/node_1/vectors.bin"));
    {
        std::ifstream checkpointHeadIDs(
            checkpointDirectory + "/SPTAGHeadVectorIDs.bin", std::ios::binary);
        SizeType checkpointHeadCount = -1;
        DimensionType checkpointHeadDimension = -1;
        checkpointHeadIDs.read(reinterpret_cast<char*>(&checkpointHeadCount),
                               sizeof(checkpointHeadCount));
        checkpointHeadIDs.read(reinterpret_cast<char*>(&checkpointHeadDimension),
                               sizeof(checkpointHeadDimension));
        BOOST_REQUIRE(checkpointHeadIDs.good());
        BOOST_CHECK_EQUAL(checkpointHeadCount, headsAfterSplit);
        BOOST_CHECK_EQUAL(checkpointHeadDimension, 1);
    }
    reloaded.reset();
    {
        const std::string loaderPath = indexDirectory + "/indexloader.ini";
        std::ifstream input(loaderPath);
        BOOST_REQUIRE(input.good());
        const std::string config((std::istreambuf_iterator<char>(input)),
                                 std::istreambuf_iterator<char>());
        const std::string disabled = "Recovery=false";
        const size_t recoveryOffset = config.find(disabled);
        BOOST_REQUIRE(recoveryOffset != std::string::npos);
        std::string recoveryConfig = config;
        recoveryConfig.replace(recoveryOffset, disabled.size(), "Recovery=true");
        std::ofstream output(loaderPath, std::ios::trunc);
        BOOST_REQUIRE(output.good());
        output << recoveryConfig;
        BOOST_REQUIRE(output.good());
    }
    std::filesystem::rename(indexDirectory + "/posting_pure_counts.bin",
                            indexDirectory + "/posting_pure_counts.source.bin");
    std::shared_ptr<VectorIndex> recovered;
    BOOST_REQUIRE(VectorIndex::LoadIndex(indexDirectory, recovered) == ErrorCode::Success);
    auto* recoveredSpann = dynamic_cast<SPANN::Index<float>*>(recovered.get());
    BOOST_REQUIRE(recoveredSpann != nullptr);
    auto* recoveredDisk = dynamic_cast<SPANN::ExtraDynamicSearcher<float>*>(
        dynamic_cast<SPANN::ISPANNIndex*>(recovered.get())->GetDiskIndex().get());
    BOOST_REQUIRE(recoveredDisk != nullptr);
    BOOST_CHECK_EQUAL(recoveredDisk->GetTaggedPostingCount(), headsAfterSplit);
    BOOST_CHECK_EQUAL(
        dynamic_cast<SPANN::ISPANNIndex*>(recovered.get())->GetMemoryIndex()->GetNumSamples(),
        headsAfterSplit);
    {
        SPANN::ExtraWorkSpace recoveryWorkspace;
        recoveredDisk->InitWorkSpace(&recoveryWorkspace, false);
        bool foundTailBoundary = false;
        for (SizeType posting = 0; posting < headsAfterSplit; ++posting) {
            SPANN::TaggedPostingSnapshot snapshot;
            BOOST_REQUIRE(recoveredDisk->GetTaggedPostingSnapshot(
                              &recoveryWorkspace, posting, snapshot) == ErrorCode::Success);
            const int recordCount = static_cast<int>(
                snapshot.m_records.size() / static_cast<size_t>(recoveredDisk->GetTaggedRecordSize()));
            if (snapshot.m_pureCount < recordCount) {
                foundTailBoundary = true;
                break;
            }
        }
        BOOST_CHECK(foundTailBoundary);
    }
    const SizeType recoveredUpdateVID = baseCount + 1 + overflowCount;
    BOOST_REQUIRE(recoveredSpann->AddIndexWithTags(
        updateVector.data(), 1, dimension, updateTags.data(), tagCount, true) == ErrorCode::Success);
    SizeType recoveredPureCopies = 0;
    SizeType recoveredTailCopies = 0;
    SizeType recoveredTailPosting = -1;
    inspectCopies(recovered, recoveredUpdateVID, recoveredPureCopies, recoveredTailCopies,
                  recoveredTailPosting, nullptr, nullptr);
    BOOST_CHECK_GT(recoveredPureCopies, 0);
    recovered.reset();
    std::filesystem::remove_all(indexDirectory);
    std::filesystem::remove_all(checkpointDirectory);
}

BOOST_AUTO_TEST_CASE(LimitedTagDualRegionInsert)
{
    constexpr SizeType baseCount = 128;
    constexpr DimensionType dimension = 8;
    constexpr std::uint32_t keyTag = 7;
    constexpr std::uint32_t alternateTag = 8;
    constexpr std::uint64_t generation = 0x12345678ULL;
    const std::string indexDirectory =
        "limited_tag_dual_insert_index";
    const std::string checkpointDirectory =
        indexDirectory + "_checkpoint";
    const std::string supportFile =
        "limited_tag_support.bin";
    std::filesystem::remove_all(indexDirectory);
    std::filesystem::remove_all(checkpointDirectory);
    std::filesystem::create_directories(indexDirectory);
    std::filesystem::create_directories(
        checkpointDirectory);

    ByteArray baseBytes = ByteArray::Alloc(
        sizeof(float) *
        static_cast<size_t>(baseCount) *
        dimension);
    auto* base =
        reinterpret_cast<float*>(baseBytes.Data());
    for (SizeType row = 0; row < baseCount; ++row) {
        for (DimensionType dim = 0;
             dim < dimension; ++dim) {
            base[static_cast<size_t>(row) *
                     dimension +
                 dim] =
                static_cast<float>(
                    row * 17 + dim * 3);
        }
    }
    auto vectors = std::make_shared<BasicVectorSet>(
        baseBytes, VectorValueType::Float,
        dimension, baseCount);
    std::vector<std::uint32_t> baseTags(
        static_cast<size_t>(baseCount), alternateTag);
    baseTags.front() = keyTag;

    auto index = VectorIndex::CreateInstance(
        IndexAlgoType::SPANN,
        VectorValueType::Float);
    BOOST_REQUIRE(index != nullptr);
    const auto set =
        [&](const char* section, const char* key,
            const std::string& value) {
            BOOST_REQUIRE(
                index->SetParameter(
                    key, value.c_str(), section) ==
                ErrorCode::Success);
        };
    set("Base", "DistCalcMethod", "L2");
    set("Base", "IndexAlgoType", "BKT");
    set("Base", "ValueType", "Float");
    set("Base", "Dim", std::to_string(dimension));
    set("Base", "IndexDirectory", indexDirectory);
    set("SelectHead", "isExecute", "true");
    set("SelectHead", "NumberOfThreads", "2");
    set("SelectHead", "SelectHeadType", "BKT");
    set("SelectHead", "SelectThreshold", "0");
    set("SelectHead", "SplitFactor", "0");
    set("SelectHead", "SplitThreshold", "0");
    set("SelectHead", "Ratio", "0.25");
    set("BuildHead", "isExecute", "true");
    set("BuildHead", "NumberOfThreads", "2");
    set("BuildSSDIndex", "isExecute", "true");
    set("BuildSSDIndex", "BuildSsdIndex", "true");
    set("BuildSSDIndex", "InternalResultNum", "16");
    set("BuildSSDIndex", "SearchInternalResultNum", "16");
    set("BuildSSDIndex", "NumberOfThreads", "2");
    set("BuildSSDIndex", "PostingPageLimit", "4");
    set("BuildSSDIndex", "SearchPostingPageLimit", "1");
    set("BuildSSDIndex", "Storage", "FILEIO");
    set("BuildSSDIndex", "SpdkBatchSize", "16");
    set("BuildSSDIndex", "CacheSizeGB", "1");
    set("BuildSSDIndex", "PersistentBufferPath",
        checkpointDirectory);
    set("BuildSSDIndex", "ExcludeHead", "true");
    set("BuildSSDIndex", "ResultNum", "10");
    set("BuildSSDIndex", "SearchThreadNum", "2");
    set("BuildSSDIndex", "Update", "false");
    set("BuildSSDIndex", "BufferLength", "1");
    set("BuildSSDIndex", "StartFileSizeGB", "1");
    set("BuildSSDIndex", "ConsistencyCheck", "true");
    set("BuildSSDIndex", "ChecksumCheck", "true");
    set("BuildSSDIndex", "ChecksumInRead", "true");
    set("BuildSSDIndex", "AsyncAppendQueueSize", "0");
    set("BuildSSDIndex", "ReplicaCount", "1");
    set("BuildSSDIndex", "TailReplicaCount", "0");
    set("BuildSSDIndex", "UnfilterTailBufferLength", "0");
    set("BuildSSDIndex", "NumTagsPerVec", "1");

    auto* spannInterface =
        dynamic_cast<SPANN::ISPANNIndex*>(
            index.get());
    BOOST_REQUIRE(spannInterface != nullptr);
    spannInterface->SetVectorTags(
        baseTags.data(), baseCount, 1);
    BOOST_REQUIRE(
        index->BuildIndex(
            vectors, nullptr, true, false, false) ==
        ErrorCode::Success);

    auto* disk =
        dynamic_cast<SPANN::ExtraDynamicSearcher<float>*>(
            spannInterface->GetDiskIndex().get());
    BOOST_REQUIRE(disk != nullptr);
    const SizeType headCount =
        disk->GetTaggedPostingCount();
    BOOST_REQUIRE_GT(headCount, 1);
    disk->InitializePureCountsFromTotals(headCount);
    const SizeType hTargetPosting = 0;
    const SizeType oTargetPosting = headCount - 1;
    auto* builtSpann =
        dynamic_cast<SPANN::Index<float>*>(
            index.get());
    BOOST_REQUIRE(builtSpann != nullptr);
    const auto headIndex =
        builtSpann->GetMemoryIndex();
    BOOST_REQUIRE(headIndex != nullptr);
    const auto* oHeadVector =
        reinterpret_cast<const float*>(
            headIndex->GetSample(oTargetPosting));
    BOOST_REQUIRE(oHeadVector != nullptr);
    std::array<float, dimension> updateVector;
    std::copy_n(
        oHeadVector, dimension,
        updateVector.begin());
    const std::array<std::uint32_t, 1>
        updateTags = {keyTag};

    SPANN::LimitedTagSupport support;
    BOOST_REQUIRE(
        support.Initialize(
            headCount, 1, 1, 0, 1,
            generation));
    for (SizeType head = 0;
         head < headCount; ++head) {
        const std::uint32_t headTag =
            head == hTargetPosting
                ? keyTag
                : alternateTag;
        const std::uint32_t attributes[] = {
            headTag};
        BOOST_REQUIRE(
            support.SetHeadTags(
                head, {headTag}));
        BOOST_REQUIRE(
            support.SetHeadAttributes(
                head, attributes, 1));
    }
    BOOST_REQUIRE(
        support.SetTagVectorCounts(
            baseCount,
            {{keyTag, 1},
             {alternateTag, baseCount - 1}}));
    std::string supportError;
    BOOST_REQUIRE_MESSAGE(
        support.Save(
            indexDirectory + "/" + supportFile,
            &supportError),
        supportError);

    set("BuildSSDIndex",
        "EnableLimitedTagPosting", "true");
    set("BuildSSDIndex",
        "LimitedTagGenerationFingerprint",
        std::to_string(generation));
    set("BuildSSDIndex",
        "LimitedTagSupportFile", supportFile);
    set("BuildSSDIndex",
        "LimitedTagColumn", "0");
    set("BuildSSDIndex",
        "LimitedTagSlotsPerHead", "1");
    set("BuildSSDIndex",
        "LimitedTagMinHeadCount", "1");
    {
        SPANN::ExtraWorkSpace workspace;
        disk->InitWorkSpace(&workspace, false);
        const int stride =
            disk->GetTaggedRecordSize();
        BOOST_REQUIRE_GT(stride, 0);
        std::vector<SPANN::TaggedPostingSnapshot>
            rewrites;
        rewrites.reserve(
            static_cast<size_t>(headCount));
        for (SizeType head = 0;
             head < headCount; ++head) {
            SPANN::TaggedPostingSnapshot original;
            BOOST_REQUIRE(
                disk->GetTaggedPostingSnapshot(
                    &workspace, head, original) ==
                ErrorCode::Success);
            BOOST_REQUIRE_EQUAL(
                original.m_records.size() %
                    static_cast<size_t>(stride),
                0U);
            SPANN::TaggedPostingSnapshot migrated;
            migrated.m_headID = head;
            const int recordCount =
                static_cast<int>(
                    original.m_records.size() /
                    static_cast<size_t>(stride));
            for (int record = 0;
                 record < recordCount; ++record) {
                const char* bytes =
                    original.m_records.data() +
                    static_cast<size_t>(record) *
                        static_cast<size_t>(stride);
                std::uint32_t recordTag = 0;
                std::memcpy(
                    &recordTag,
                    bytes + sizeof(SizeType) +
                        sizeof(std::uint8_t),
                    sizeof(recordTag));
                if (support.Supports(head, recordTag)) {
                    migrated.m_records.append(
                        bytes,
                        static_cast<size_t>(stride));
                    ++migrated.m_pureCount;
                }
            }
            migrated.m_records.append(
                original.m_records);
            rewrites.emplace_back(
                std::move(migrated));
        }
        BOOST_REQUIRE(
            disk->RewriteTaggedPostings(
                &workspace, rewrites) ==
            ErrorCode::Success);
    }
    disk->SetLimitedTagReadRangesReady(true);
    BOOST_REQUIRE(
        index->SaveIndex(indexDirectory) ==
        ErrorCode::Success);
    BOOST_CHECK(
        std::filesystem::exists(
            indexDirectory +
            "/limited_tag_ho_ready.bin"));
    index.reset();

    const std::string readyMarkerPath =
        indexDirectory +
        "/limited_tag_ho_ready.bin";
    std::ifstream readyMarkerInput(
        readyMarkerPath, std::ios::binary);
    BOOST_REQUIRE(readyMarkerInput.good());
    const std::string readyMarkerBytes(
        (std::istreambuf_iterator<char>(
            readyMarkerInput)),
        std::istreambuf_iterator<char>());
    BOOST_REQUIRE(!readyMarkerBytes.empty());
    const auto writeReadyMarker =
        [&](const std::string& bytes) {
            std::ofstream output(
                readyMarkerPath,
                std::ios::binary |
                    std::ios::trunc);
            BOOST_REQUIRE(output.good());
            output.write(
                bytes.data(),
                static_cast<std::streamsize>(
                    bytes.size()));
            BOOST_REQUIRE(output.good());
        };
    {
        BOOST_REQUIRE(
            std::filesystem::remove(
                readyMarkerPath));
        std::shared_ptr<VectorIndex> unready;
        BOOST_REQUIRE(
            VectorIndex::LoadIndex(
                indexDirectory, unready) ==
            ErrorCode::Success);
        auto* unreadySpann =
            dynamic_cast<SPANN::Index<float>*>(
                unready.get());
        auto* unreadyDisk =
            dynamic_cast<
                SPANN::ExtraDynamicSearcher<float>*>(
                dynamic_cast<SPANN::ISPANNIndex*>(
                    unready.get())
                    ->GetDiskIndex()
                    .get());
        BOOST_REQUIRE(unreadySpann != nullptr);
        BOOST_REQUIRE(unreadyDisk != nullptr);
        BOOST_CHECK(
            !unreadyDisk
                 ->LimitedTagPostingRegionsReady());
        const SizeType before =
            unreadySpann->GetNumSamples();
        BOOST_CHECK(
            unreadySpann->AddIndexWithTags(
                updateVector.data(), 1, dimension,
                updateTags.data(), 1, true) ==
            ErrorCode::Fail);
        BOOST_CHECK_EQUAL(
            unreadySpann->GetNumSamples(), before);
        unready.reset();
        writeReadyMarker(readyMarkerBytes);
    }
    {
        std::string staleMarker =
            readyMarkerBytes;
        staleMarker.back() =
            static_cast<char>(
                staleMarker.back() ^ 0x1);
        writeReadyMarker(staleMarker);
        std::shared_ptr<VectorIndex> stale;
        BOOST_REQUIRE(
            VectorIndex::LoadIndex(
                indexDirectory, stale) ==
            ErrorCode::Success);
        auto* staleDisk =
            dynamic_cast<
                SPANN::ExtraDynamicSearcher<float>*>(
                dynamic_cast<SPANN::ISPANNIndex*>(
                    stale.get())
                    ->GetDiskIndex()
                    .get());
        BOOST_REQUIRE(staleDisk != nullptr);
        BOOST_CHECK(
            !staleDisk
                 ->LimitedTagPostingRegionsReady());
        stale.reset();
        writeReadyMarker(readyMarkerBytes);
    }
    {
        const std::string incompletePath =
            indexDirectory +
            "/limited_tag_ho_checkpoint.incomplete";
        std::ofstream incomplete(
            incompletePath,
            std::ios::binary |
                std::ios::trunc);
        BOOST_REQUIRE(incomplete.good());
        incomplete.put('\0');
        incomplete.close();
        BOOST_REQUIRE(incomplete.good());
        std::shared_ptr<VectorIndex> rejected;
        BOOST_CHECK(
            VectorIndex::LoadIndex(
                indexDirectory, rejected) !=
            ErrorCode::Success);
        rejected.reset();
        BOOST_REQUIRE(
            std::filesystem::remove(
                incompletePath));
    }

    BOOST_REQUIRE(
        VectorIndex::LoadIndex(
            indexDirectory, index) ==
        ErrorCode::Success);
    auto* spann =
        dynamic_cast<SPANN::Index<float>*>(
            index.get());
    BOOST_REQUIRE(spann != nullptr);
    spannInterface =
        dynamic_cast<SPANN::ISPANNIndex*>(
            index.get());
    BOOST_REQUIRE(spannInterface != nullptr);
    disk =
        dynamic_cast<SPANN::ExtraDynamicSearcher<float>*>(
            spannInterface->GetDiskIndex().get());
    BOOST_REQUIRE(disk != nullptr);
    BOOST_CHECK(
        disk->LimitedTagPostingRegionsReady());
    auto* mutableOptions = spann->GetOptions();
    BOOST_REQUIRE(mutableOptions != nullptr);
    mutableOptions->m_enableLimitedTagPosting =
        false;
    BOOST_CHECK(
        spannInterface->HasLimitedTagLayout());
    const std::string tamperedSingleFile =
        indexDirectory + "/tampered_unsupported.bin";
    BOOST_CHECK(
        index->SaveIndexToFile(
            tamperedSingleFile) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        !std::filesystem::exists(
            tamperedSingleFile));
    std::string tamperedConfig;
    const std::vector<ByteArray> tamperedBlobs;
    BOOST_CHECK(
        index->SaveIndex(
            tamperedConfig, tamperedBlobs) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        index->SaveIndex(indexDirectory) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        std::filesystem::exists(
            readyMarkerPath));
    BOOST_CHECK(
        !std::filesystem::exists(
            indexDirectory +
            "/limited_tag_ho_checkpoint.incomplete"));
    BOOST_REQUIRE(
        disk->BeginLimitedTagCheckpoint(
            indexDirectory) ==
        ErrorCode::Success);
    BOOST_CHECK(
        !std::filesystem::exists(
            readyMarkerPath));
    BOOST_CHECK(
        std::filesystem::exists(
            indexDirectory +
            "/limited_tag_ho_checkpoint.incomplete"));
    mutableOptions->m_enableLimitedTagPosting =
        true;
    BOOST_REQUIRE(
        index->SaveIndex(indexDirectory) ==
        ErrorCode::Success);
    {
        SPANN::ExtraWorkSpace invalidRewriteWorkspace;
        disk->InitWorkSpace(
            &invalidRewriteWorkspace, false);
        SPANN::TaggedPostingSnapshot invalidRewrite;
        invalidRewrite.m_headID =
            disk->GetTaggedPostingCount();
        BOOST_CHECK(
            disk->RewriteTaggedPostings(
                &invalidRewriteWorkspace,
                {invalidRewrite}) ==
            ErrorCode::Posting_SizeError);
        BOOST_CHECK(
            std::filesystem::exists(
                readyMarkerPath));
        BOOST_CHECK(
            !std::filesystem::exists(
                indexDirectory +
                "/limited_tag_ho_checkpoint.incomplete"));
    }
    const std::string unsupportedSingleFile =
        indexDirectory + "/unsupported.bin";
    BOOST_CHECK(
        index->SaveIndexToFile(
            unsupportedSingleFile) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        !std::filesystem::exists(
            unsupportedSingleFile));
    std::string unsupportedConfig;
    const std::vector<ByteArray>
        unsupportedBlobs;
    BOOST_CHECK(
        index->SaveIndex(
            unsupportedConfig,
            unsupportedBlobs) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        disk->LimitedTagPostingRegionsReady());
    BOOST_CHECK(
        !std::filesystem::exists(
            indexDirectory +
            "/limited_tag_ho_checkpoint.incomplete"));

    const size_t regionCapacity =
        static_cast<size_t>(
            disk->GetTaggedPureCapacity());
    const SizeType samplesBeforeHOverflow =
        spann->GetNumSamples();
    const SizeType headsBeforeHOverflow =
        disk->GetTaggedPostingCount();
    const SizeType hOverflowCount =
        static_cast<SizeType>(
            regionCapacity + 1);
    std::vector<float> hOverflowVectors(
        static_cast<size_t>(hOverflowCount) *
        dimension);
    std::vector<std::uint32_t> hOverflowTags(
        static_cast<size_t>(hOverflowCount),
        keyTag);
    for (SizeType row = 0;
         row < hOverflowCount; ++row) {
        const SizeType target =
            1 +
            row %
                (headsBeforeHOverflow - 1);
        const auto* center =
            reinterpret_cast<const float*>(
                spann->GetMemoryIndex()
                    ->GetSample(target));
        BOOST_REQUIRE(center != nullptr);
        for (DimensionType dim = 0;
             dim < dimension; ++dim) {
            hOverflowVectors[
                static_cast<size_t>(row) *
                    dimension +
                dim] =
                center[dim] +
                static_cast<float>(row + 1) *
                    1.0e-7f;
        }
    }
    BOOST_CHECK(
        spann->AddIndexWithTags(
            hOverflowVectors.data(),
            hOverflowCount, dimension,
            hOverflowTags.data(), 1, true) ==
        ErrorCode::Posting_OverFlow);
    BOOST_CHECK_EQUAL(
        spann->GetNumSamples(),
        samplesBeforeHOverflow);
    BOOST_CHECK_EQUAL(
        disk->GetTaggedPostingCount(),
        headsBeforeHOverflow);
    BOOST_CHECK(
        std::filesystem::exists(
            readyMarkerPath));
    BOOST_CHECK(
        !std::filesystem::exists(
            indexDirectory +
            "/limited_tag_ho_checkpoint.incomplete"));

    const SizeType vectorCountBeforeLegacyInsert =
        spann->GetNumSamples();
    SizeType legacyVID = -1;
    BOOST_CHECK(
        spann->AddIndexSPFresh(
            updateVector.data(), 1, dimension,
            &legacyVID) ==
        ErrorCode::Fail);
    BOOST_CHECK_EQUAL(
        spann->GetNumSamples(),
        vectorCountBeforeLegacyInsert);
    BOOST_REQUIRE(
        spann->AddIndexWithTags(
            updateVector.data(), 1, dimension,
            updateTags.data(), 1, true) ==
        ErrorCode::Success);
    const SizeType updateVID = baseCount;
    const std::string incompleteMarkerPath =
        indexDirectory +
        "/limited_tag_ho_checkpoint.incomplete";
    BOOST_CHECK(
        std::filesystem::exists(
            incompleteMarkerPath));
    BOOST_CHECK(
        !std::filesystem::exists(
            readyMarkerPath));

    const SizeType fillerCount =
        static_cast<SizeType>(
            PageSize /
                disk->GetTaggedRecordSize()) +
        16;
    std::vector<float> fillerVectors(
        static_cast<size_t>(fillerCount) *
        dimension);
    std::vector<std::uint32_t> fillerTags(
        static_cast<size_t>(fillerCount),
        alternateTag);
    for (SizeType row = 0;
         row < fillerCount; ++row) {
        for (DimensionType dim = 0;
             dim < dimension; ++dim) {
            fillerVectors[
                static_cast<size_t>(row) *
                    dimension +
                dim] =
                updateVector[
                    static_cast<size_t>(dim)] +
                static_cast<float>(row + 1) *
                    0.001f;
        }
    }
    BOOST_REQUIRE(
        spann->AddIndexWithTags(
            fillerVectors.data(), fillerCount,
            dimension, fillerTags.data(), 1,
            true) ==
        ErrorCode::Success);

    auto countRegions =
        [&](const std::shared_ptr<VectorIndex>& inspected,
            SizeType vid, SizeType& hPosting,
            SizeType& oPosting) {
            auto* inspectedDisk =
                dynamic_cast<
                    SPANN::ExtraDynamicSearcher<float>*>(
                    dynamic_cast<SPANN::ISPANNIndex*>(
                        inspected.get())
                        ->GetDiskIndex()
                        .get());
            BOOST_REQUIRE(inspectedDisk != nullptr);
            SPANN::ExtraWorkSpace workspace;
            inspectedDisk->InitWorkSpace(
                &workspace, false);
            const int stride =
                inspectedDisk->GetTaggedRecordSize();
            BOOST_REQUIRE_GT(stride, 0);
            int hCopies = 0;
            int oCopies = 0;
            hPosting = -1;
            oPosting = -1;
            for (SizeType head = 0;
                 head <
                     inspectedDisk
                         ->GetTaggedPostingCount();
                 ++head) {
                SPANN::TaggedPostingSnapshot snapshot;
                BOOST_REQUIRE(
                    inspectedDisk
                        ->GetTaggedPostingSnapshot(
                            &workspace, head,
                            snapshot) ==
                    ErrorCode::Success);
                BOOST_REQUIRE_EQUAL(
                    snapshot.m_records.size() %
                        static_cast<size_t>(stride),
                    0U);
                const int count = static_cast<int>(
                    snapshot.m_records.size() /
                    static_cast<size_t>(stride));
                for (int record = 0;
                     record < count; ++record) {
                    SizeType recordVID = -1;
                    std::memcpy(
                        &recordVID,
                        snapshot.m_records.data() +
                            static_cast<size_t>(
                                record) *
                                static_cast<size_t>(
                                    stride),
                        sizeof(recordVID));
                    if (recordVID != vid) continue;
                    if (record <
                        snapshot.m_pureCount) {
                        ++hCopies;
                        hPosting = head;
                    } else {
                        ++oCopies;
                        oPosting = head;
                    }
                }
            }
            return std::make_pair(
                hCopies, oCopies);
        };

    SizeType hPosting = -1;
    SizeType oPosting = -1;
    auto copies = countRegions(
        index, updateVID, hPosting, oPosting);
    BOOST_CHECK_EQUAL(copies.first, 1);
    BOOST_CHECK_EQUAL(copies.second, 1);
    BOOST_REQUIRE_EQUAL(
        hPosting, hTargetPosting);
    BOOST_REQUIRE_EQUAL(
        oPosting, oTargetPosting);
    BOOST_REQUIRE_NE(hPosting, oPosting);
    const std::string saveAsDirectory =
        indexDirectory + "_save_as";
    std::filesystem::remove_all(saveAsDirectory);
    BOOST_CHECK(
        index->SaveIndex(saveAsDirectory) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        !std::filesystem::exists(
            saveAsDirectory));

    const std::string blockedSyncDirectory =
        indexDirectory + "/blocked_sync";
    std::filesystem::create_directory(
        blockedSyncDirectory);
    std::filesystem::permissions(
        blockedSyncDirectory,
        std::filesystem::perms::none);
    const ErrorCode failedSave =
        index->SaveIndex(indexDirectory);
    std::filesystem::permissions(
        blockedSyncDirectory,
        std::filesystem::perms::owner_all);
    std::filesystem::remove(
        blockedSyncDirectory);
    BOOST_CHECK(
        failedSave == ErrorCode::DiskIOFail);
    BOOST_CHECK(
        std::filesystem::exists(
            incompleteMarkerPath));
    BOOST_CHECK(
        !std::filesystem::exists(
            readyMarkerPath));

    BOOST_REQUIRE(
        index->SaveIndex(indexDirectory) ==
        ErrorCode::Success);
    BOOST_CHECK(
        !std::filesystem::exists(
            incompleteMarkerPath));
    BOOST_CHECK(
        std::filesystem::exists(
            readyMarkerPath));
    index.reset();

    BOOST_REQUIRE(
        VectorIndex::LoadIndex(
            indexDirectory, index) ==
        ErrorCode::Success);
    spann =
        dynamic_cast<SPANN::Index<float>*>(
            index.get());
    BOOST_REQUIRE(spann != nullptr);
    disk =
        dynamic_cast<SPANN::ExtraDynamicSearcher<float>*>(
            dynamic_cast<SPANN::ISPANNIndex*>(
                index.get())
                ->GetDiskIndex()
                .get());
    BOOST_REQUIRE(disk != nullptr);
    BOOST_CHECK(
        disk->LimitedTagPostingRegionsReady());
    SizeType reloadedHPosting = -1;
    SizeType reloadedOPosting = -1;
    copies = countRegions(
        index, updateVID, reloadedHPosting,
        reloadedOPosting);
    BOOST_CHECK_EQUAL(copies.first, 1);
    BOOST_CHECK_EQUAL(copies.second, 1);
    BOOST_CHECK_EQUAL(
        reloadedHPosting, hPosting);
    BOOST_CHECK_EQUAL(
        reloadedOPosting, oPosting);

    {
        SPANN::ExtraWorkSpace workspace;
        disk->InitWorkSpace(
            &workspace, false);
        SPANN::TaggedPostingSnapshot snapshot;
        BOOST_REQUIRE(
            disk->GetTaggedPostingSnapshot(
                &workspace, reloadedOPosting,
                snapshot) ==
            ErrorCode::Success);
        const int stride =
            disk->GetTaggedRecordSize();
        BOOST_REQUIRE_GE(
            static_cast<size_t>(
                snapshot.m_pureCount) *
                static_cast<size_t>(stride),
            static_cast<size_t>(PageSize));
        int updateRecord = -1;
        const int recordCount =
            static_cast<int>(
                    snapshot.m_records.size() /
                    static_cast<size_t>(stride));
        for (int record =
                     snapshot.m_pureCount;
             record < recordCount; ++record) {
            SizeType vid = -1;
            std::memcpy(
                    &vid,
                    snapshot.m_records.data() +
                        static_cast<size_t>(record) *
                            static_cast<size_t>(stride),
                    sizeof(vid));
            if (vid == updateVID) {
                    updateRecord = record;
                    break;
            }
        }
        BOOST_REQUIRE_GE(updateRecord, 0);
        BOOST_CHECK_GE(
            static_cast<size_t>(updateRecord) *
                    static_cast<size_t>(stride),
            static_cast<size_t>(PageSize));
    }

    const auto containsVID =
        [&](const std::vector<SizeType>& postings,
            const std::vector<std::uint32_t>& queryTags,
            bool limitedMembershipEligible = true) {
            SPANN::ExtraWorkSpace workspace;
            disk->InitWorkSpace(&workspace, false);
            workspace.m_postingIDs = postings;
            workspace.m_queryTags = queryTags.empty() ? nullptr : queryTags.data();
            workspace.m_numQueryTags = static_cast<int>(queryTags.size());
            workspace.m_useHybridPure = limitedMembershipEligible && !queryTags.empty();
            COMMON::QueryResultSet<float> result(
                updateVector.data(), 10);
            BOOST_REQUIRE(
                disk->SearchIndex(&workspace, result, spann->GetMemoryIndex(),
                    nullptr, nullptr, nullptr) ==
                ErrorCode::Success);
            for (int i = 0;
                 i < result.GetResultNum(); ++i) {
                if (result.GetResult(i)->VID ==
                    updateVID) {
                    return true;
                }
            }
            return false;
        };

    disk->SetLimitedTagReadRangesReady(false);
    // Without validated H/O boundaries the fixed prefix cannot reach this O row.
    BOOST_CHECK(
        !containsVID(
            {reloadedOPosting}, {}));
    disk->SetLimitedTagReadRangesReady(true);
    BOOST_CHECK(
        containsVID(
            {reloadedHPosting}, {keyTag}));
    BOOST_CHECK(
        containsVID(
            {reloadedOPosting}, {}));
    BOOST_CHECK(
        !containsVID(
            {reloadedOPosting}, {keyTag}));
    BOOST_CHECK(
        !containsVID(
            {reloadedHPosting}, {}));
    BOOST_CHECK(
        containsVID(
            {reloadedOPosting}, {keyTag},
            false));
    auto* options = spann->GetOptions();
    BOOST_REQUIRE(options != nullptr);
    for (const char* key : {"EnableUnfilterTail", "AblateTail"})
        BOOST_CHECK(options->SetParameter("SearchSSDIndex", key, "0") == ErrorCode::FailedParseValue);
    BOOST_CHECK(
        containsVID(
            {reloadedOPosting}, {}));

    {
        SPANN::ExtraWorkSpace workspace;
        disk->InitWorkSpace(
            &workspace, false);
        workspace
            .m_limitedTagRegionsReadySnapshotValid =
            true;
        workspace
            .m_limitedTagRegionsReadySnapshot =
            false;
        workspace.m_queryTags =
            updateTags.data();
        workspace.m_numQueryTags = 1;
        workspace.m_postingIDs = {
            static_cast<int>(
                reloadedOPosting)};
        COMMON::QueryResultSet<float> result(
            updateVector.data(), 10);
        BOOST_REQUIRE(
            disk->SearchIndex(
                &workspace, result,
                spann->GetMemoryIndex(),
                nullptr, nullptr, nullptr) ==
            ErrorCode::Success);
        bool found = false;
        for (int i = 0;
             i < result.GetResultNum(); ++i) {
            found =
                found ||
                result.GetResult(i)->VID ==
                    updateVID;
        }
        BOOST_CHECK(!found);
    }

    std::vector<std::shared_ptr<
        Helper::DiskIO>> refineStreams;
    std::vector<SizeType> refineMapping;
    BOOST_CHECK(
        spann->RefineIndex(
            refineStreams, nullptr,
            &refineMapping) ==
        ErrorCode::Undefined);
    BOOST_REQUIRE(
        spann->DeleteIndex(updateVID) ==
        ErrorCode::Success);
    BOOST_CHECK(
        !containsVID(
            {reloadedHPosting}, {keyTag}));
    BOOST_CHECK(
        !containsVID(
            {reloadedOPosting}, {}));

    SPANN::ExtraWorkSpace gcWorkspace;
    disk->InitWorkSpace(
        &gcWorkspace, false);
    SPANN::TaggedPostingSnapshot hBeforeGC;
    SPANN::TaggedPostingSnapshot oBeforeGC;
    BOOST_REQUIRE(
        disk->GetTaggedPostingSnapshot(
            &gcWorkspace, reloadedHPosting,
            hBeforeGC) ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        disk->GetTaggedPostingSnapshot(
            &gcWorkspace, reloadedOPosting,
            oBeforeGC) ==
        ErrorCode::Success);
    spann->ForceGC();
    SPANN::TaggedPostingSnapshot hAfterGC;
    SPANN::TaggedPostingSnapshot oAfterGC;
    BOOST_REQUIRE(
        disk->GetTaggedPostingSnapshot(
            &gcWorkspace, reloadedHPosting,
            hAfterGC) ==
        ErrorCode::Success);
    BOOST_REQUIRE(
        disk->GetTaggedPostingSnapshot(
            &gcWorkspace, reloadedOPosting,
            oAfterGC) ==
        ErrorCode::Success);
    BOOST_CHECK_EQUAL(
        hAfterGC.m_pureCount,
        hBeforeGC.m_pureCount);
    BOOST_CHECK_EQUAL(
        oAfterGC.m_pureCount,
        oBeforeGC.m_pureCount);
    BOOST_CHECK(
        hAfterGC.m_records ==
        hBeforeGC.m_records);
    BOOST_CHECK(
        oAfterGC.m_records ==
        oBeforeGC.m_records);

    SPANN::TaggedPostingSnapshot
        dualRegionBefore;
    BOOST_REQUIRE(
        disk->GetTaggedPostingSnapshot(
            &gcWorkspace, hTargetPosting,
            dualRegionBefore) ==
        ErrorCode::Success);
    const size_t dualRegionTotalBefore =
        dualRegionBefore.m_records.size() /
        static_cast<size_t>(
            disk->GetTaggedRecordSize());
    BOOST_REQUIRE_LE(
        static_cast<size_t>(
            dualRegionBefore.m_pureCount),
        dualRegionTotalBefore);
    const size_t dualRegionHBefore =
        static_cast<size_t>(
            dualRegionBefore.m_pureCount);
    const size_t dualRegionOBefore =
        dualRegionTotalBefore -
        dualRegionHBefore;
    BOOST_REQUIRE_LT(
        dualRegionHBefore, regionCapacity);
    BOOST_REQUIRE_LT(
        dualRegionOBefore, regionCapacity);
    const size_t dualRegionNeeded =
        dualRegionHBefore +
                    dualRegionOBefore <
                regionCapacity
            ? (regionCapacity -
                   dualRegionHBefore -
                   dualRegionOBefore) /
                      2 +
                  1
            : 1;
    const size_t dualRegionAvailable =
        (std::min)(
            regionCapacity -
                dualRegionHBefore,
            regionCapacity -
                dualRegionOBefore);
    BOOST_REQUIRE_LE(
        dualRegionNeeded,
        dualRegionAvailable);
    const SizeType dualRegionInsertCount =
        static_cast<SizeType>(
            dualRegionNeeded);
    const SizeType dualRegionFirstVID =
        spann->GetNumSamples();
    const SizeType headsBeforeDualRegionInsert =
        disk->GetTaggedPostingCount();
    const auto* dualRegionCenter =
        reinterpret_cast<const float*>(
            spann->GetMemoryIndex()->GetSample(
                hTargetPosting));
    BOOST_REQUIRE(dualRegionCenter != nullptr);
    std::vector<float> dualRegionVectors(
        static_cast<size_t>(
            dualRegionInsertCount) *
        dimension);
    std::vector<std::uint32_t>
        dualRegionTags(
            static_cast<size_t>(
                dualRegionInsertCount),
            keyTag);
    for (SizeType row = 0;
         row < dualRegionInsertCount; ++row) {
        for (DimensionType dim = 0;
             dim < dimension; ++dim) {
            dualRegionVectors[
                static_cast<size_t>(row) *
                    dimension +
                dim] =
                dualRegionCenter[dim] +
                static_cast<float>(row + 1) *
                    1.0e-6f;
        }
    }
    BOOST_REQUIRE(
        spann->AddIndexWithTags(
            dualRegionVectors.data(),
            dualRegionInsertCount, dimension,
            dualRegionTags.data(), 1, true) ==
        ErrorCode::Success);
    BOOST_CHECK_EQUAL(
        disk->GetTaggedPostingCount(),
        headsBeforeDualRegionInsert);
    SizeType dualRegionH = -1;
    SizeType dualRegionO = -1;
    const auto dualRegionCopies =
        countRegions(
            index, dualRegionFirstVID,
            dualRegionH, dualRegionO);
    BOOST_CHECK_EQUAL(
        dualRegionCopies.first, 1);
    BOOST_CHECK_EQUAL(
        dualRegionCopies.second, 1);
    BOOST_CHECK_EQUAL(
        dualRegionH, hTargetPosting);
    BOOST_CHECK_EQUAL(
        dualRegionO, hTargetPosting);

    const SizeType headsBeforeLimitedSplit =
        disk->GetTaggedPostingCount();
    const SizeType splitProbeVID =
        spann->GetNumSamples();
    const SizeType splitInsertCount =
        static_cast<SizeType>(
            regionCapacity + 32);
    std::vector<float> splitVectors(
        static_cast<size_t>(splitInsertCount) *
        dimension);
    std::vector<std::uint32_t> splitTags(
        static_cast<size_t>(splitInsertCount),
        alternateTag);
    const auto* splitCenter =
        reinterpret_cast<const float*>(
            spann->GetMemoryIndex()->GetSample(
                oTargetPosting));
    BOOST_REQUIRE(splitCenter != nullptr);
    for (SizeType row = 0;
         row < splitInsertCount; ++row) {
        const float clusterOffset =
            row % 2 == 0 ? -0.25f : 0.25f;
        for (DimensionType dim = 0;
             dim < dimension; ++dim) {
            splitVectors[
                static_cast<size_t>(row) *
                    dimension +
                dim] =
                splitCenter[dim] +
                clusterOffset +
                static_cast<float>(row) *
                    1.0e-5f;
        }
    }
    constexpr SizeType splitBatchSize = 24;
    for (SizeType begin = 0;
         begin < splitInsertCount;
         begin += splitBatchSize) {
        const SizeType count =
            (std::min)(
                splitBatchSize,
                splitInsertCount - begin);
        BOOST_REQUIRE(
            spann->AddIndexWithTags(
                splitVectors.data() +
                    static_cast<size_t>(begin) *
                        dimension,
                count, dimension,
                splitTags.data() + begin, 1,
                true) ==
            ErrorCode::Success);
    }
    const SizeType headsAfterLimitedSplit =
        disk->GetTaggedPostingCount();
    BOOST_REQUIRE_GT(
        headsAfterLimitedSplit,
        headsBeforeLimitedSplit);
    for (SizeType head = 0;
         head < headsAfterLimitedSplit; ++head) {
        SPANN::TaggedPostingSnapshot snapshot;
        BOOST_REQUIRE(
            disk->GetTaggedPostingSnapshot(
                &gcWorkspace, head, snapshot) ==
            ErrorCode::Success);
        BOOST_CHECK_LE(
            snapshot.m_pureCount,
            static_cast<size_t>(
                disk->GetTaggedPureCapacity()));
        const size_t recordCount =
            snapshot.m_records.size() /
            static_cast<size_t>(
                disk->GetTaggedRecordSize());
        BOOST_REQUIRE_LE(
            static_cast<size_t>(
                snapshot.m_pureCount),
            recordCount);
        BOOST_CHECK_LE(
            recordCount -
                static_cast<size_t>(
                    snapshot.m_pureCount),
            static_cast<size_t>(
                disk->GetTaggedPureCapacity()));
    }
    SizeType splitProbeH = -1;
    SizeType splitProbeO = -1;
    copies = countRegions(
        index,
        splitProbeVID +
            splitInsertCount - 1,
        splitProbeH, splitProbeO);
    BOOST_CHECK_EQUAL(copies.first, 1);
    BOOST_CHECK_EQUAL(copies.second, 1);

    BOOST_REQUIRE(
        index->SaveIndex(indexDirectory) ==
        ErrorCode::Success);
    SPANN::LimitedTagSupport splitSupport;
    supportError.clear();
    BOOST_REQUIRE_MESSAGE(
        splitSupport.Load(
            indexDirectory + "/" + supportFile,
            headsAfterLimitedSplit, 1, 1,
            0, 1, generation, &supportError),
        supportError);
    BOOST_CHECK_EQUAL(
        splitSupport.HeadCount(),
        headsAfterLimitedSplit);

    SizeType mergeCandidate = -1;
    std::vector<SizeType> mergeCandidateO;
    for (SizeType head =
             headsBeforeLimitedSplit;
         head < headsAfterLimitedSplit;
         ++head) {
        if (!spann->GetMemoryIndex()
                 ->ContainSample(head)) {
            continue;
        }
        SPANN::TaggedPostingSnapshot snapshot;
        BOOST_REQUIRE(
            disk->GetTaggedPostingSnapshot(
                &gcWorkspace, head, snapshot) ==
            ErrorCode::Success);
        const int stride =
            disk->GetTaggedRecordSize();
        const int count = static_cast<int>(
            snapshot.m_records.size() /
            static_cast<size_t>(stride));
        std::vector<SizeType> oVIDs;
        for (int record =
                 snapshot.m_pureCount;
             record < count; ++record) {
            SizeType vid = -1;
            std::memcpy(
                &vid,
                snapshot.m_records.data() +
                    static_cast<size_t>(record) *
                        static_cast<size_t>(
                            stride),
                sizeof(vid));
            if (vid >= 0)
                oVIDs.push_back(vid);
        }
        if (oVIDs.size() >
            static_cast<size_t>(
                disk->GetTaggedMergeThreshold() +
                2)) {
            mergeCandidate = head;
            mergeCandidateO =
                std::move(oVIDs);
            break;
        }
    }
    BOOST_REQUIRE(mergeCandidate >= 0);
    const auto readOVIDs =
        [&](SizeType head) {
            SPANN::TaggedPostingSnapshot snapshot;
            BOOST_REQUIRE(
                disk->GetTaggedPostingSnapshot(
                    &gcWorkspace, head, snapshot) ==
                ErrorCode::Success);
            const int stride =
                disk->GetTaggedRecordSize();
            const int count = static_cast<int>(
                snapshot.m_records.size() /
                static_cast<size_t>(stride));
            std::vector<SizeType> vids;
            for (int record =
                     snapshot.m_pureCount;
                 record < count; ++record) {
                SizeType vid = -1;
                std::memcpy(
                    &vid,
                    snapshot.m_records.data() +
                        static_cast<size_t>(record) *
                            static_cast<size_t>(
                                stride),
                    sizeof(vid));
                if (vid >= 0)
                    vids.push_back(vid);
            }
            return vids;
        };
    const auto memoryIndex =
        spann->GetMemoryIndex();
    BOOST_REQUIRE(memoryIndex != nullptr);
    COMMON::QueryResultSet<float> nearby(
        const_cast<float*>(
            reinterpret_cast<const float*>(
                memoryIndex->GetSample(
                    mergeCandidate))),
        (std::max)(
            2,
            spann->GetOptions()
                ->m_internalResultNum));
    BOOST_REQUIRE(
        memoryIndex->SearchIndex(nearby) ==
        ErrorCode::Success);
    SizeType mergeNeighbor = -1;
    std::vector<SizeType> mergeNeighborO;
    for (int result = 0;
         result < nearby.GetResultNum(); ++result) {
        const BasicResult* candidate =
            nearby.GetResult(result);
        if (candidate == nullptr ||
            candidate->VID < 0 ||
            candidate->VID == mergeCandidate ||
            !splitSupport.IsActiveHead(
                candidate->VID)) {
            continue;
        }
        auto candidateO =
            readOVIDs(candidate->VID);
        if (candidateO.size() < 2)
            continue;
        std::unordered_set<SizeType> combined(
            mergeCandidateO.begin(),
            mergeCandidateO.end());
        combined.insert(
            candidateO.begin(), candidateO.end());
        if (combined.size() >
            static_cast<size_t>(
                disk->GetTaggedPureCapacity())) {
            continue;
        }
        mergeNeighbor = candidate->VID;
        mergeNeighborO =
            std::move(candidateO);
        break;
    }
    BOOST_REQUIRE(mergeNeighbor >= 0);
    const auto retainLastTwo =
        [&](std::vector<SizeType>& vids) {
            BOOST_REQUIRE_GE(vids.size(), 2U);
            const size_t retainedBegin =
                vids.size() - 2;
            for (size_t i = 0;
                 i < retainedBegin; ++i) {
                const ErrorCode deleteRet =
                    spann->DeleteIndex(vids[i]);
                BOOST_REQUIRE(
                    deleteRet ==
                        ErrorCode::Success ||
                    deleteRet ==
                        ErrorCode::VectorNotFound);
            }
            vids.erase(
                vids.begin(),
                vids.begin() +
                    static_cast<std::ptrdiff_t>(
                        retainedBegin));
        };
    retainLastTwo(mergeCandidateO);
    retainLastTwo(mergeNeighborO);
    const auto countActiveHeads =
        [&]() {
            SizeType active = 0;
            const auto headIndex =
                spann->GetMemoryIndex();
            for (SizeType head = 0;
                 head <
                     headIndex->GetNumSamples();
                 ++head) {
                if (headIndex->ContainSample(head))
                    ++active;
            }
            return active;
        };
    const SizeType activeBeforeMerge =
        countActiveHeads();
    spann->GetOptions()
        ->m_searchPostingPageLimit = 16;
    spann->GetOptions()->m_replicaCount = 2;
    spann->GetOptions()->m_rngFactor = 100.0f;
    const auto queueMergeCandidate =
        [&](SizeType head) {
        SPANN::ExtraWorkSpace workspace;
        disk->InitWorkSpace(&workspace, false);
        workspace.m_postingIDs = {head};
        COMMON::QueryResultSet<float> result(
            splitVectors.data(), 10);
        BOOST_REQUIRE(
            disk->SearchIndex(&workspace, result, spann->GetMemoryIndex(),
                nullptr, nullptr, nullptr) ==
            ErrorCode::Success);
        };
    BOOST_CHECK(
        disk->LimitedTagPostingRegionsReady());
    BOOST_REQUIRE(
        std::filesystem::exists(
            incompleteMarkerPath));
    BOOST_REQUIRE(
        !std::filesystem::exists(
            readyMarkerPath));
    queueMergeCandidate(mergeCandidate);
    BOOST_REQUIRE(
        index->SaveIndex(indexDirectory) ==
        ErrorCode::Success);
    const SizeType activeAfterMerge =
        countActiveHeads();
    BOOST_CHECK_EQUAL(
        activeAfterMerge + 1,
        activeBeforeMerge);

    SPANN::LimitedTagSupport mergedSupport;
    supportError.clear();
    BOOST_REQUIRE_MESSAGE(
        mergedSupport.Load(
            indexDirectory + "/" + supportFile,
            headsAfterLimitedSplit, 1, 1,
            0, 1, generation, &supportError),
        supportError);
    SizeType activeSupportHeads = 0;
    for (SizeType head = 0;
         head < mergedSupport.HeadCount();
         ++head) {
        if (mergedSupport.IsActiveHead(head))
            ++activeSupportHeads;
    }
    BOOST_CHECK_EQUAL(
        activeSupportHeads,
        activeAfterMerge);
    const bool candidateActive =
        mergedSupport.IsActiveHead(
            mergeCandidate);
    const bool neighborActive =
        mergedSupport.IsActiveHead(
            mergeNeighbor);
    BOOST_REQUIRE_NE(
        candidateActive, neighborActive);
    const SizeType loserHead =
        candidateActive
            ? mergeNeighbor
            : mergeCandidate;
    const SizeType survivorHead =
        candidateActive
            ? mergeCandidate
            : mergeNeighbor;
    const auto survivorTagsBefore =
        splitSupport.HeadTags(survivorHead);
    const auto survivorTagsAfter =
        mergedSupport.HeadTags(survivorHead);
    BOOST_CHECK_EQUAL_COLLECTIONS(
        survivorTagsBefore.begin(),
        survivorTagsBefore.end(),
        survivorTagsAfter.begin(),
        survivorTagsAfter.end());
    const SizeType loserAnchor =
        spann->GetGlobalVID(loserHead);
    BOOST_REQUIRE_GE(loserAnchor, 0);
    std::vector<SizeType> reassignedOVIDs =
        loserHead == mergeCandidate
            ? std::vector<SizeType>{
                  mergeCandidateO[0],
                  mergeCandidateO[1],
                  loserAnchor}
            : std::vector<SizeType>{
                  mergeNeighborO[0],
                  mergeNeighborO[1],
                  loserAnchor};
    std::sort(
        reassignedOVIDs.begin(),
        reassignedOVIDs.end());
    reassignedOVIDs.erase(
        std::unique(
            reassignedOVIDs.begin(),
            reassignedOVIDs.end()),
        reassignedOVIDs.end());
    for (SizeType vid : reassignedOVIDs) {
        SizeType replannedH = -1;
        SizeType replannedO = -1;
        const auto replannedCopies =
            countRegions(
                index, vid,
                replannedH, replannedO);
        BOOST_CHECK_GE(
            replannedCopies.first, 1);
        BOOST_CHECK_GE(
            replannedCopies.second, 1);
        BOOST_CHECK_LE(
            replannedCopies.second, 2);
    }
    const auto& survivorOVIDs =
        survivorHead == mergeCandidate
            ? mergeCandidateO
            : mergeNeighborO;
    for (size_t i = 0; i < 2; ++i) {
        SizeType retainedH = -1;
        SizeType retainedO = -1;
        const auto retainedCopies =
            countRegions(
                index, survivorOVIDs[i],
                retainedH, retainedO);
        BOOST_CHECK_GE(
            retainedCopies.first, 1);
        BOOST_CHECK_EQUAL(
            retainedCopies.second, 1);
    }

    SPANN::TaggedPostingSnapshot zeroOSnapshot;
    BOOST_REQUIRE(
        disk->GetTaggedPostingSnapshot(
            &gcWorkspace, survivorHead,
            zeroOSnapshot) ==
        ErrorCode::Success);
    const int zeroOStride =
        disk->GetTaggedRecordSize();
    BOOST_REQUIRE_GT(zeroOStride, 0);
    BOOST_REQUIRE_EQUAL(
        zeroOSnapshot.m_records.size() %
            static_cast<size_t>(zeroOStride),
        0U);
    BOOST_REQUIRE_GT(
        zeroOSnapshot.m_records.size() /
                static_cast<size_t>(zeroOStride),
        static_cast<size_t>(
            zeroOSnapshot.m_pureCount));
    std::vector<SizeType> zeroORecordVIDs;
    for (size_t offset = 0;
         offset < zeroOSnapshot.m_records.size();
         offset +=
             static_cast<size_t>(zeroOStride)) {
        SizeType vid = -1;
        std::memcpy(
            &vid,
            zeroOSnapshot.m_records.data() +
                offset,
            sizeof(vid));
        if (vid >= 0)
            zeroORecordVIDs.push_back(vid);
    }
    std::sort(
        zeroORecordVIDs.begin(),
        zeroORecordVIDs.end());
    zeroORecordVIDs.erase(
        std::unique(
            zeroORecordVIDs.begin(),
            zeroORecordVIDs.end()),
        zeroORecordVIDs.end());
    BOOST_REQUIRE(
        !zeroORecordVIDs.empty());
    for (SizeType vid : zeroORecordVIDs) {
        if (!index->ContainSample(vid))
            continue;
        BOOST_REQUIRE(
            spann->DeleteIndex(vid) ==
            ErrorCode::Success);
    }
    const SizeType activeBeforeZeroOMerge =
        countActiveHeads();
    queueMergeCandidate(survivorHead);
    std::vector<SizeType> queuedZeroOHeads;
    disk->DrainTaggedMergeCandidates(
        queuedZeroOHeads);
    BOOST_REQUIRE(
        std::find(
            queuedZeroOHeads.begin(),
            queuedZeroOHeads.end(),
            survivorHead) !=
        queuedZeroOHeads.end());
    queueMergeCandidate(survivorHead);
    BOOST_REQUIRE(
        index->SaveIndex(indexDirectory) ==
        ErrorCode::Success);
    BOOST_CHECK_EQUAL(
        countActiveHeads() + 1,
        activeBeforeZeroOMerge);

    const SizeType checkpointVectorCount =
        spann->GetNumSamples();
    SPANN::ExtraWorkSpace
        checkpointWorkspace;
    disk->InitWorkSpace(
        &checkpointWorkspace, false);
    const SizeType checkpointPostingCount =
        disk->GetTaggedPostingCount();
    std::vector<SPANN::TaggedPostingSnapshot>
        checkpointPostings(
            static_cast<size_t>(
                checkpointPostingCount));
    for (SizeType head = 0;
         head < checkpointPostingCount;
         ++head) {
        BOOST_REQUIRE(
            disk->GetTaggedPostingSnapshot(
                &checkpointWorkspace, head,
                checkpointPostings[
                    static_cast<size_t>(
                        head)]) ==
            ErrorCode::Success);
    }
    BOOST_REQUIRE(
        spann->Checkpoint() ==
        ErrorCode::Success);
    BOOST_CHECK(
        std::filesystem::exists(
            checkpointDirectory + "/" +
            supportFile));
    std::array<float, dimension>
        postCheckpointVector = updateVector;
    postCheckpointVector[0] += 0.25f;
    BOOST_REQUIRE(
        spann->AddIndexWithTags(
            postCheckpointVector.data(), 1,
            dimension, updateTags.data(), 1,
            true) == ErrorCode::Success);
    BOOST_REQUIRE(
        index->SaveIndex(indexDirectory) ==
        ErrorCode::Success);
    BOOST_CHECK(
        std::filesystem::exists(
            checkpointDirectory +
            "/limited_tag_ho_ready.bin"));
    const std::string liveMappingPath =
        indexDirectory + "/ssdmapping";
    const std::string livePostingPath =
        indexDirectory + "/ssdmapping_postings";
    const std::string liveBlockPoolPath =
        indexDirectory +
        "/ssdmapping_postings_blockpool";
    BOOST_REQUIRE(
        std::filesystem::exists(liveMappingPath));
    BOOST_REQUIRE(
        std::filesystem::exists(livePostingPath));
    BOOST_REQUIRE(
        std::filesystem::exists(
            liveBlockPoolPath));
    const auto cleanMappingWriteTime =
        std::filesystem::last_write_time(
            liveMappingPath);
    const auto cleanPostingWriteTime =
        std::filesystem::last_write_time(
            livePostingPath);
    const auto cleanBlockPoolWriteTime =
        std::filesystem::last_write_time(
            liveBlockPoolPath);
    index.reset();
    BOOST_CHECK(
        std::filesystem::last_write_time(
            liveMappingPath) ==
        cleanMappingWriteTime);
    BOOST_CHECK(
        std::filesystem::last_write_time(
            livePostingPath) ==
        cleanPostingWriteTime);
    BOOST_CHECK(
        std::filesystem::last_write_time(
            liveBlockPoolPath) ==
        cleanBlockPoolWriteTime);
    BOOST_REQUIRE(
        VectorIndex::LoadIndex(
            indexDirectory, index) ==
        ErrorCode::Success);
    auto* liveReloadedSpann =
        dynamic_cast<SPANN::Index<float>*>(
            index.get());
    BOOST_REQUIRE(liveReloadedSpann != nullptr);
    postCheckpointVector[0] += 0.25f;
    BOOST_REQUIRE(
        liveReloadedSpann->AddIndexWithTags(
            postCheckpointVector.data(), 1,
            dimension, updateTags.data(), 1,
            true) == ErrorCode::Success);

    index.reset();
    {
        const std::string loaderPath =
            indexDirectory +
            "/indexloader.ini";
        std::ifstream input(loaderPath);
        BOOST_REQUIRE(input.good());
        const std::string config(
            (std::istreambuf_iterator<char>(
                input)),
            std::istreambuf_iterator<char>());
        const std::string disabled =
            "Recovery=false";
        const size_t recoveryOffset =
            config.find(disabled);
        BOOST_REQUIRE(
            recoveryOffset !=
            std::string::npos);
        std::string recoveryConfig = config;
        recoveryConfig.replace(
            recoveryOffset, disabled.size(),
            "Recovery=true");
        std::ofstream output(
            loaderPath, std::ios::trunc);
        BOOST_REQUIRE(output.good());
        output << recoveryConfig;
        BOOST_REQUIRE(output.good());
    }
    BOOST_REQUIRE(
        VectorIndex::LoadIndex(
            indexDirectory, index) ==
        ErrorCode::Success);
    auto* recoveredSpann =
        dynamic_cast<SPANN::Index<float>*>(
            index.get());
    BOOST_REQUIRE(recoveredSpann != nullptr);
    auto* recoveredDisk =
        dynamic_cast<
            SPANN::ExtraDynamicSearcher<float>*>(
            dynamic_cast<SPANN::ISPANNIndex*>(
                index.get())
                ->GetDiskIndex()
                .get());
    BOOST_REQUIRE(recoveredDisk != nullptr);
    BOOST_CHECK(
        recoveredDisk
            ->LimitedTagPostingRegionsReady());
    BOOST_CHECK_EQUAL(
        index->GetNumSamples(),
        checkpointVectorCount);
    BOOST_REQUIRE_EQUAL(
        recoveredDisk
            ->GetTaggedPostingCount(),
        checkpointPostingCount);
    SPANN::ExtraWorkSpace
        recoveredCheckpointWorkspace;
    recoveredDisk->InitWorkSpace(
        &recoveredCheckpointWorkspace,
        false);
    for (SizeType head = 0;
         head < checkpointPostingCount;
         ++head) {
        SPANN::TaggedPostingSnapshot
            recoveredPosting;
        BOOST_REQUIRE(
            recoveredDisk
                ->GetTaggedPostingSnapshot(
                    &recoveredCheckpointWorkspace,
                    head, recoveredPosting) ==
            ErrorCode::Success);
        const auto& expected =
            checkpointPostings[
                static_cast<size_t>(head)];
        BOOST_CHECK_EQUAL(
            recoveredPosting.m_pureCount,
            expected.m_pureCount);
        BOOST_CHECK_EQUAL(
            recoveredPosting.m_records,
            expected.m_records);
    }
    const std::string checkpointMappingPath =
        checkpointDirectory + "/ssdmapping";
    const std::string checkpointBlockPoolPath =
        checkpointDirectory +
        "/ssdmapping_postings_blockpool";
    const std::string checkpointReadyPath =
        checkpointDirectory +
        "/limited_tag_ho_ready.bin";
    const std::string liveIncompletePath =
        indexDirectory +
        "/limited_tag_ho_checkpoint.incomplete";
    const std::string liveReadyPath =
        indexDirectory +
        "/limited_tag_ho_ready.bin";
    BOOST_REQUIRE(
        std::filesystem::exists(
            checkpointMappingPath));
    BOOST_REQUIRE(
        std::filesystem::exists(
            checkpointBlockPoolPath));
    BOOST_REQUIRE(
        std::filesystem::exists(
            checkpointReadyPath));
    BOOST_REQUIRE(
        std::filesystem::exists(
            liveIncompletePath));
    BOOST_CHECK(
        !std::filesystem::exists(
            liveReadyPath));
    const auto recoveryLiveMappingWriteTime =
        std::filesystem::last_write_time(
            liveMappingPath);
    const auto recoveryLivePostingWriteTime =
        std::filesystem::last_write_time(
            livePostingPath);
    const auto recoveryLiveBlockPoolWriteTime =
        std::filesystem::last_write_time(
            liveBlockPoolPath);
    const auto recoveryCheckpointMappingWriteTime =
        std::filesystem::last_write_time(
            checkpointMappingPath);
    const auto recoveryCheckpointBlockPoolWriteTime =
        std::filesystem::last_write_time(
            checkpointBlockPoolPath);
    const auto recoveryReadyWriteTime =
        std::filesystem::last_write_time(
            checkpointReadyPath);
    const auto recoveryIncompleteWriteTime =
        std::filesystem::last_write_time(
            liveIncompletePath);
    const SizeType recoveredVectorCount =
        index->GetNumSamples();
    BOOST_CHECK(
        recoveredDisk
            ->SupportsLimitedTagUpdates());
    recoveredSpann->GetOptions()->m_recovery =
        false;
    recoveredSpann->GetOptions()
        ->m_enableLimitedTagPosting = false;
    const std::string blockedRecoveryFile =
        indexDirectory +
        "/recovery_unsupported.bin";
    BOOST_CHECK(
        index->SaveIndexToFile(
            blockedRecoveryFile) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        !std::filesystem::exists(
            blockedRecoveryFile));
    std::string blockedRecoveryConfig;
    const std::vector<ByteArray>
        blockedRecoveryBlobs;
    BOOST_CHECK(
        index->SaveIndex(
            blockedRecoveryConfig,
            blockedRecoveryBlobs) ==
        ErrorCode::Undefined);
    SizeType blockedLegacyVID = -1;
    BOOST_CHECK(
        recoveredSpann->AddIndexSPFresh(
            postCheckpointVector.data(), 1,
            dimension, &blockedLegacyVID) ==
        ErrorCode::Undefined);
    const std::vector<
        std::shared_ptr<Helper::DiskIO>>
        noRefineStreams;
    BOOST_CHECK(
        recoveredSpann->RefineIndex(
            noRefineStreams, nullptr, nullptr) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        recoveredDisk
            ->BeginLimitedTagCheckpoint(
                checkpointDirectory) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        recoveredDisk
            ->InvalidateLimitedTagReadiness(
                checkpointDirectory) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        recoveredDisk
            ->CommitLimitedTagReadiness(
                checkpointDirectory) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        recoveredDisk->Checkpoint(
            checkpointDirectory) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        recoveredDisk->DeleteIndex(0) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        recoveredDisk->ReserveTaggedPosting(
            checkpointPostingCount) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        recoveredDisk
            ->RollbackReservedTaggedPosting(
                checkpointPostingCount) ==
        ErrorCode::Undefined);
    std::vector<SPANN::TaggedPostingSnapshot>
        blockedRewrite = {
            checkpointPostings.front()};
    BOOST_CHECK(
        recoveredDisk->RewriteTaggedPostings(
            &recoveredCheckpointWorkspace,
            blockedRewrite) ==
        ErrorCode::Undefined);
    std::string blockedPosting =
        checkpointPostings.front().m_records;
    BOOST_CHECK(
        recoveredDisk->GetWritePosting(
            &recoveredCheckpointWorkspace, 0,
            blockedPosting, true) ==
        ErrorCode::Undefined);
    recoveredDisk->ForceCompaction();
    const auto recoveredStore =
        recoveredDisk->GetKVStore();
    BOOST_REQUIRE(recoveredStore != nullptr);
    const std::chrono::microseconds
        blockedTimeout(1000000);
    BOOST_CHECK(
        recoveredStore->Put(
            0, blockedPosting, blockedTimeout,
            &recoveredCheckpointWorkspace
                 .m_diskRequests) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        recoveredStore->Merge(
            0, blockedPosting, blockedTimeout,
            &recoveredCheckpointWorkspace
                 .m_diskRequests,
            [](const void*, int) {
                return true;
            }) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        recoveredStore->Delete(0) ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        recoveredStore->Checkpoint(
            checkpointDirectory) ==
        ErrorCode::Undefined);
    recoveredStore->ForceCompaction();
    BOOST_CHECK(
        recoveredSpann->AddIndexWithTags(
            postCheckpointVector.data(), 1,
            dimension, updateTags.data(), 1,
            true) == ErrorCode::Undefined);
    BOOST_CHECK(
        recoveredSpann->DeleteIndex(0) ==
        ErrorCode::Undefined);
    recoveredSpann->ForceGC();
    BOOST_CHECK(
        recoveredSpann->Checkpoint() ==
        ErrorCode::Undefined);
    BOOST_CHECK(
        index->SaveIndex(indexDirectory) ==
        ErrorCode::Undefined);
    BOOST_CHECK_EQUAL(
        index->GetNumSamples(),
        recoveredVectorCount);
    for (SizeType head = 0;
         head < checkpointPostingCount;
         ++head) {
        SPANN::TaggedPostingSnapshot
            recoveredPosting;
        BOOST_REQUIRE(
            recoveredDisk
                ->GetTaggedPostingSnapshot(
                    &recoveredCheckpointWorkspace,
                    head, recoveredPosting) ==
            ErrorCode::Success);
        const auto& expected =
            checkpointPostings[
                static_cast<size_t>(head)];
        BOOST_CHECK_EQUAL(
            recoveredPosting.m_pureCount,
            expected.m_pureCount);
        BOOST_CHECK_EQUAL(
            recoveredPosting.m_records,
            expected.m_records);
    }
    index.reset();
    BOOST_CHECK(
        std::filesystem::last_write_time(
            liveMappingPath) ==
        recoveryLiveMappingWriteTime);
    BOOST_CHECK(
        std::filesystem::last_write_time(
            livePostingPath) ==
        recoveryLivePostingWriteTime);
    BOOST_CHECK(
        std::filesystem::last_write_time(
            liveBlockPoolPath) ==
        recoveryLiveBlockPoolWriteTime);
    BOOST_CHECK(
        std::filesystem::last_write_time(
            checkpointMappingPath) ==
        recoveryCheckpointMappingWriteTime);
    BOOST_CHECK(
        std::filesystem::last_write_time(
            checkpointBlockPoolPath) ==
        recoveryCheckpointBlockPoolWriteTime);
    BOOST_CHECK(
        std::filesystem::last_write_time(
            checkpointReadyPath) ==
        recoveryReadyWriteTime);
    BOOST_CHECK(
        std::filesystem::last_write_time(
            liveIncompletePath) ==
        recoveryIncompleteWriteTime);
    BOOST_CHECK(
        !std::filesystem::exists(
            liveReadyPath));
    std::filesystem::remove_all(indexDirectory);
    std::filesystem::remove_all(checkpointDirectory);
}

BOOST_AUTO_TEST_CASE(LimitedTagDynamicLoadRejectsWAL)
{
    const auto rejects =
        [](SPANN::Options& options) {
            COMMON::VersionLabel versionMap;
            COMMON::Dataset<std::uint64_t>
                vectorTranslateMap;
            SPANN::ExtraDynamicSearcher<float>
                searcher(options);
            return !searcher.LoadIndex(
                options, versionMap,
                vectorTranslateMap, nullptr);
        };

    SPANN::Options walOptions;
    walOptions.m_enableLimitedTagPosting = true;
    walOptions.m_enableWAL = true;
    BOOST_CHECK(rejects(walOptions));

    SPANN::Options quantizedOptions;
    quantizedOptions.m_enableLimitedTagPosting =
        true;
    quantizedOptions.m_postingQuantizer = "OPQ";
    quantizedOptions.m_postingQuantM = 4;
    BOOST_CHECK(rejects(quantizedOptions));
}

BOOST_AUTO_TEST_CASE(TestClone)
{
    using namespace SPFreshTest;

    // Prepare test data using TestDataGenerator
    std::shared_ptr<VectorSet> vecset, queryset, truth, addvecset, addtruth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.Run(vecset, metaset, queryset, truth, addvecset, addmetaset, addtruth);

    auto originalIndex = BuildIndex<int8_t>("original_index", vecset, metaset);
    BOOST_REQUIRE(originalIndex != nullptr);
    BOOST_REQUIRE(originalIndex->SaveIndex("original_index") == ErrorCode::Success);

    auto clonedIndex = originalIndex->Clone("cloned_index");
    BOOST_REQUIRE(clonedIndex != nullptr);
    BOOST_REQUIRE(clonedIndex->SaveIndex("cloned_index") == ErrorCode::Success);
    originalIndex.reset();
    clonedIndex = nullptr;

    std::unordered_set<std::string> exceptions = {"indexloader.ini"};

    // Compare files in both directories
    BOOST_REQUIRE_MESSAGE(CompareDirectoriesWithLogging("original_index", "cloned_index", exceptions),
                          "Saved index does not match loaded-then-saved index");

    std::filesystem::remove_all("original_index");
    std::filesystem::remove_all("cloned_index");
}

BOOST_AUTO_TEST_CASE(TestCloneRecall)
{
    using namespace SPFreshTest;

    // Prepare test data using TestDataGenerator
    std::shared_ptr<VectorSet> vecset, queryset, truth, addvecset, addtruth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.Run(vecset, metaset, queryset, truth, addvecset, addmetaset, addtruth);

    auto originalIndex = BuildIndex<int8_t>("original_index", vecset, metaset);
    BOOST_REQUIRE(originalIndex != nullptr);
    BOOST_REQUIRE(originalIndex->SaveIndex("original_index") == ErrorCode::Success);
    float originalRecall = Search<int8_t>(originalIndex, queryset, vecset, addvecset, K, truth, N);
    
    auto clonedIndex = originalIndex->Clone("cloned_index");
    BOOST_REQUIRE(clonedIndex != nullptr);
    originalIndex.reset();
    clonedIndex = nullptr;

    std::shared_ptr<VectorIndex> loadedClonedIndex;
    BOOST_REQUIRE(VectorIndex::LoadIndex("cloned_index", loadedClonedIndex) == ErrorCode::Success);
    BOOST_REQUIRE(loadedClonedIndex != nullptr);
    float clonedRecall = Search<int8_t>(loadedClonedIndex, queryset, vecset, addvecset, K, truth, N);
    loadedClonedIndex = nullptr;

    BOOST_REQUIRE_MESSAGE(std::fabs(originalRecall - clonedRecall) < 0.02,
                          "Recall mismatch between original and cloned index: "
                              << "original=" << originalRecall << ", cloned=" << clonedRecall);

    std::filesystem::remove_all("original_index");
    std::filesystem::remove_all("cloned_index");
}

BOOST_AUTO_TEST_CASE(IndexPersistenceAndInsertSanity)
{
    using namespace SPFreshTest;

    // Prepare test data
    std::shared_ptr<VectorSet> vecset, queryset, truth, addvecset, addtruth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.Run(vecset, metaset, queryset, truth, addvecset, addmetaset, addtruth);

    // Build and save base index
    auto baseIndex = BuildIndex<int8_t>("insert_test_index", vecset, metaset);
    BOOST_REQUIRE(baseIndex != nullptr);
    BOOST_REQUIRE(baseIndex->SaveIndex("insert_test_index") == ErrorCode::Success);
    baseIndex = nullptr;

    // Load the saved index
    std::shared_ptr<VectorIndex> loadedOnce;
    BOOST_REQUIRE(VectorIndex::LoadIndex("insert_test_index", loadedOnce) == ErrorCode::Success);
    BOOST_REQUIRE(loadedOnce != nullptr);

    // Search sanity check
    SearchOnly<int8_t>(loadedOnce, queryset, K);

    // Clone the loaded index
    auto clonedIndex = loadedOnce->Clone("insert_cloned_index");
    BOOST_REQUIRE(clonedIndex != nullptr);

    // Save and reload the cloned index
    BOOST_REQUIRE(clonedIndex->SaveIndex("insert_cloned_index") == ErrorCode::Success);
    loadedOnce.reset();
    clonedIndex = nullptr;

    std::shared_ptr<VectorIndex> loadedClone;
    BOOST_REQUIRE(VectorIndex::LoadIndex("insert_cloned_index", loadedClone) == ErrorCode::Success);
    BOOST_REQUIRE(loadedClone != nullptr);

    // Insert new vectors
    InsertVectors<int8_t>(static_cast<SPANN::Index<int8_t> *>(loadedClone.get()), 1,
                          static_cast<int>(addvecset->Count()), addvecset, addmetaset);

    // Final save and reload after insert
    BOOST_REQUIRE(loadedClone->SaveIndex("insert_final_index") == ErrorCode::Success);
    loadedClone = nullptr;

    std::shared_ptr<VectorIndex> reloadedFinal;
    BOOST_REQUIRE(VectorIndex::LoadIndex("insert_final_index", reloadedFinal) == ErrorCode::Success);

    // Final search sanity
    SearchOnly<int8_t>(reloadedFinal, queryset, K);
    reloadedFinal = nullptr;

    // Cleanup
    std::filesystem::remove_all("insert_test_index");
    std::filesystem::remove_all("insert_cloned_index");
    std::filesystem::remove_all("insert_final_index");
}

BOOST_AUTO_TEST_CASE(IndexPersistenceAndInsertMultipleThreads)
{
    using namespace SPFreshTest;

    // Prepare test data
    std::shared_ptr<VectorSet> vecset, queryset, truth, addvecset, addtruth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.Run(vecset, metaset, queryset, truth, addvecset, addmetaset, addtruth);

    // Build and save base index
    auto baseIndex = BuildIndex<int8_t>("insert_test_index_multi", vecset, metaset);
    BOOST_REQUIRE(baseIndex != nullptr);
    BOOST_REQUIRE(baseIndex->SaveIndex("insert_test_index_multi") == ErrorCode::Success);
    baseIndex = nullptr;

    // Load the saved index
    std::shared_ptr<VectorIndex> loadedOnce;
    BOOST_REQUIRE(VectorIndex::LoadIndex("insert_test_index_multi", loadedOnce) == ErrorCode::Success);
    BOOST_REQUIRE(loadedOnce != nullptr);

    // Search sanity check
    SearchOnly<int8_t>(loadedOnce, queryset, K);

    // Clone the loaded index
    auto clonedIndex = loadedOnce->Clone("insert_cloned_index_multi");
    BOOST_REQUIRE(clonedIndex != nullptr);

    // Save and reload the cloned index
    BOOST_REQUIRE(clonedIndex->SaveIndex("insert_cloned_index_multi") == ErrorCode::Success);
    loadedOnce.reset();
    clonedIndex = nullptr;

    std::shared_ptr<VectorIndex> loadedClone;
    BOOST_REQUIRE(VectorIndex::LoadIndex("insert_cloned_index_multi", loadedClone) == ErrorCode::Success);
    BOOST_REQUIRE(loadedClone != nullptr);

    // Insert new vectors
    InsertVectors<int8_t>(static_cast<SPANN::Index<int8_t> *>(loadedClone.get()), 2,
                          static_cast<int>(addvecset->Count()), addvecset, addmetaset);

    // Final save and reload after insert
    BOOST_REQUIRE(loadedClone->SaveIndex("insert_final_index_multi") == ErrorCode::Success);
    loadedClone = nullptr;

    std::shared_ptr<VectorIndex> reloadedFinal;
    BOOST_REQUIRE(VectorIndex::LoadIndex("insert_final_index_multi", reloadedFinal) == ErrorCode::Success);
    BOOST_REQUIRE(reloadedFinal != nullptr);
    // Final search sanity
    SearchOnly<int8_t>(reloadedFinal, queryset, K);
    reloadedFinal = nullptr;

    // Cleanup
    std::filesystem::remove_all("insert_test_index_multi");
    std::filesystem::remove_all("insert_cloned_index_multi");
}

BOOST_AUTO_TEST_CASE(IndexSaveDuringQuery)
{
    using namespace SPFreshTest;

    std::shared_ptr<VectorSet> vecset, queryset, truth, addvecset, addtruth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.Run(vecset, metaset, queryset, truth, addvecset, addmetaset, addtruth);

    auto index = BuildIndex<int8_t>("save_during_query_index", vecset, metaset);
    BOOST_REQUIRE(index != nullptr);

    std::atomic<bool> keepQuerying(true);
    std::thread queryThread([&]() {
        while (keepQuerying)
        {
            for (int q = 0; q < queryset->Count(); ++q)
            {
                QueryResult result(queryset->GetVector(q), K, true);
                index->SearchIndex(result);
            }
        }
    });

    // Wait a bit before saving
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    ErrorCode saveStatus = index->SaveIndex("save_during_query_index");
    BOOST_REQUIRE(saveStatus == ErrorCode::Success);

    keepQuerying = false;
    queryThread.join();

    index = nullptr;

    std::shared_ptr<VectorIndex> reloaded;
    BOOST_REQUIRE(VectorIndex::LoadIndex("save_during_query_index", reloaded) == ErrorCode::Success);
    BOOST_REQUIRE(reloaded != nullptr);

    SearchOnly<int8_t>(reloaded, queryset, K);
    reloaded = nullptr;

    std::filesystem::remove_all("save_during_query_index");
}

BOOST_AUTO_TEST_CASE(IndexMultiThreadedQuerySanity)
{
    using namespace SPFreshTest;

    // Generate test data
    std::shared_ptr<VectorSet> vecset, queryset, truth, addvecset, addtruth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.Run(vecset, metaset, queryset, truth, addvecset, addmetaset, addtruth);

    // Build and save index
    auto index = BuildIndex<int8_t>("multi_query_index", vecset, metaset);
    BOOST_REQUIRE(index != nullptr);
    BOOST_REQUIRE(index->SaveIndex("multi_query_index") == ErrorCode::Success);
    index = nullptr;

    // Reload the index
    std::shared_ptr<VectorIndex> loaded;
    BOOST_REQUIRE(VectorIndex::LoadIndex("multi_query_index", loaded) == ErrorCode::Success);
    BOOST_REQUIRE(loaded != nullptr);

    // Insert additional vectors
    InsertVectors<int8_t>(static_cast<SPANN::Index<int8_t> *>(loaded.get()), 2,
                          static_cast<int>(addvecset->Count()), addvecset, addmetaset);

    // Perform multithreaded query
    const int threadCount = 4;
    std::vector<std::thread> threads;
    std::atomic<int> nextQuery(0);
    std::atomic<int> completedQueries(0);

    for (int t = 0; t < threadCount; ++t)
    {
        threads.emplace_back([&, t]() {
            QueryResult result(nullptr, K, true);
            while (true)
            {
                int i = nextQuery.fetch_add(1);
                if (i >= queryset->Count())
                    break;

                result.SetTarget(queryset->GetVector(static_cast<SizeType>(i)));
                loaded->SearchIndex(result);

                ++completedQueries;
            }
        });
    }

    for (auto &thread : threads)
    {
        thread.join();
    }

    SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Multithreaded query completed: %d queries\n", completedQueries.load());
    loaded = nullptr;

    // Cleanup
    std::filesystem::remove_all("multi_query_index");
}

BOOST_AUTO_TEST_CASE(IndexShadowCloneLifecycleKeepLast)
{
    using namespace SPFreshTest;

    constexpr int iterations = 5;
    constexpr int insertBatchSize = 100;

    std::shared_ptr<VectorSet> vecset, queryset, truth, addvecset, addtruth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.Run(vecset, metaset, queryset, truth, addvecset, addmetaset, addtruth);

    const std::string baseIndexName = "base_index";
    BOOST_REQUIRE(BuildIndex<int8_t>(baseIndexName, vecset, metaset)->SaveIndex(baseIndexName) == ErrorCode::Success);

    std::string previousIndexName = baseIndexName;

    for (int iter = 0; iter < iterations; ++iter)
    {
        std::string shadowIndexName = "shadow_index_" + std::to_string(iter);
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "[%d] Loading index: %s\n", iter, previousIndexName.c_str());

        // Load previous index
        std::shared_ptr<VectorIndex> loaded;
        BOOST_REQUIRE(VectorIndex::LoadIndex(previousIndexName, loaded) == ErrorCode::Success);
        BOOST_REQUIRE(loaded != nullptr);

        // Query check
        for (int i = 0; i < std::min<SizeType>(queryset->Count(), 5); ++i)
        {
            QueryResult result(queryset->GetVector(i), K, true);
            loaded->SearchIndex(result);
        }

        // Cleanup previous base index after first iteration
        if (iter == 1)
        {
            std::filesystem::remove_all(baseIndexName);
        }

        // Clone to shadow
        BOOST_REQUIRE(loaded->Clone(shadowIndexName) != nullptr);
        loaded.reset();

        std::shared_ptr<VectorIndex> shadowLoaded;
        BOOST_REQUIRE(VectorIndex::LoadIndex(shadowIndexName, shadowLoaded) == ErrorCode::Success);
        BOOST_REQUIRE(shadowLoaded != nullptr);
        auto *shadowIndex = static_cast<SPANN::Index<int8_t> *>(shadowLoaded.get());

        // Prepare insert batch
        const int insertOffset = (iter * insertBatchSize) % static_cast<int>(addvecset->Count());
        const int insertCount = min(insertBatchSize, static_cast<int>(addvecset->Count()) - insertOffset);

        std::vector<std::uint8_t> metaBytes;
        std::vector<std::uint64_t> offsetTable(insertCount + 1);
        std::uint64_t offset = 0;
        for (int i = 0; i < insertCount; ++i)
        {
            ByteArray meta = addmetaset->GetMetadata(insertOffset + i);
            offsetTable[i] = offset;
            metaBytes.insert(metaBytes.end(), meta.Data(), meta.Data() + meta.Length());
            offset += meta.Length();
        }
        offsetTable[insertCount] = offset;

        ByteArray metaBuf(new std::uint8_t[metaBytes.size()], metaBytes.size(), true);
        std::memcpy(metaBuf.Data(), metaBytes.data(), metaBytes.size());

        ByteArray offsetBuf(new std::uint8_t[offsetTable.size() * sizeof(std::uint64_t)],
                            offsetTable.size() * sizeof(std::uint64_t), true);
        std::memcpy(offsetBuf.Data(), offsetTable.data(), offsetTable.size() * sizeof(std::uint64_t));

        auto batchMeta = std::make_shared<MemMetadataSet>(metaBuf, offsetBuf, insertCount);
        const void *vectorStart = addvecset->GetVector(insertOffset);

        shadowIndex->AddIndex(vectorStart, insertCount, shadowIndex->GetOptions()->m_dim, batchMeta, true);

        while (!shadowIndex->AllFinished())
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }

        BOOST_REQUIRE(shadowLoaded->SaveIndex(shadowIndexName) == ErrorCode::Success);
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "[%d] Created new shadow index: %s\n", iter, shadowIndexName.c_str());
        shadowLoaded = nullptr;

        previousIndexName = shadowIndexName;
    }

    // Keep the final shadow index directory for debugging/inspection
    SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Kept final index: %s\n", previousIndexName.c_str());

    // Cleanup all created indexes after test
    std::filesystem::remove_all(baseIndexName);
    for (int iter = 0; iter < iterations; ++iter)
    {
        std::string shadow = "shadow_index_" + std::to_string(iter);
        std::filesystem::remove_all(shadow);
    }
}

BOOST_AUTO_TEST_CASE(IterativeSearch)
{
    using namespace SPFreshTest;

    constexpr int insertIterations = 5;
    constexpr int insertBatchSize = 1000;
    constexpr int dimension = 1024;
    std::shared_ptr<VectorSet> vecset = get_embeddings<float>(0, insertBatchSize, dimension, -1);
    std::shared_ptr<MetadataSet> metaset =
        TestUtils::TestDataGenerator<float>::GenerateMetadataSet(insertBatchSize, 0);

    auto originalIndex = BuildIndex<float>("original_index", vecset, metaset);
    BOOST_REQUIRE(originalIndex != nullptr);
    BOOST_REQUIRE(originalIndex->SaveIndex("original_index") == ErrorCode::Success);
    originalIndex = nullptr;

    std::string prevPath = "original_index";
    for (int iter = 0; iter < insertIterations; iter++)
    {
        std::string clone_path = "clone_index_" + std::to_string(iter);
        std::shared_ptr<VectorIndex> prevIndex;
        BOOST_REQUIRE(VectorIndex::LoadIndex(prevPath, prevIndex) == ErrorCode::Success);
        BOOST_REQUIRE(prevIndex != nullptr);

        auto cloneIndex = prevIndex->Clone(clone_path);
        auto *cloneIndexPtr = static_cast<SPANN::Index<float> *>(cloneIndex.get());
        std::shared_ptr<VectorSet> tmpvecs =
            get_embeddings<float>((iter + 1) * insertBatchSize, (iter + 2) * insertBatchSize, dimension, -1);
        std::shared_ptr<MetadataSet> tmpmetas =
            TestUtils::TestDataGenerator<float>::GenerateMetadataSet(insertBatchSize, (iter + 1) * insertBatchSize);
        InsertVectors<float>(cloneIndexPtr, 1, insertBatchSize, tmpvecs, tmpmetas);

        BOOST_REQUIRE(cloneIndex->SaveIndex(clone_path) == ErrorCode::Success);
        cloneIndex = nullptr;

        std::shared_ptr<VectorIndex> loadedIndex;
        BOOST_REQUIRE(VectorIndex::LoadIndex(clone_path, loadedIndex) == ErrorCode::Success);
        BOOST_REQUIRE(loadedIndex != nullptr);

        std::shared_ptr<VectorSet> embedding =
            get_embeddings<float>((1000 * iter) + 500, ((1000 * iter) + 501), dimension, -1);
        std::shared_ptr<ResultIterator> resultIterator = loadedIndex->GetIterator(embedding->GetData(), false);
        int batch = 100;
        int ri = 0;
        float current = INT_MAX, previous = INT_MAX;
        bool relaxMono = false;
        while (!relaxMono)
        {
            auto results = resultIterator->Next(batch);
            int resultCount = results->GetResultNum();
            if (resultCount <= 0)
                break;

            previous = current;
            current = 0;
            for (int j = 0; j < resultCount; j++)
            {
                std::cout << "Result[" << ri << "] VID:" << results->GetResult(j)->VID
                          << " Dist:" << results->GetResult(j)->Dist
                          << " RelaxedMono:" << results->GetResult(j)->RelaxedMono << " current:" << current
                          << " previous:" << previous << std::endl;
                relaxMono = results->GetResult(j)->RelaxedMono;
                current += results->GetResult(j)->Dist;
                ri++;
            }
            current /= resultCount;
        }
        resultIterator->Close();
        loadedIndex = nullptr;
    }

    for (int iter = 0; iter < insertIterations; iter++)
    {
        std::filesystem::remove_all("clone_index_" + std::to_string(iter));
    }
    std::filesystem::remove_all("original_index");
}

BOOST_AUTO_TEST_CASE(RefineIndex)
{
    using namespace SPFreshTest;

    int iterations = 5;
    int insertBatchSize = N / iterations;
    int deleteBatchSize = N / iterations;

    // Generate test data
    std::shared_ptr<VectorSet> vecset, addvecset, queryset, truth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.RunBatches(vecset, metaset, addvecset, addmetaset, queryset, N, insertBatchSize, deleteBatchSize,
                         iterations, truth);

    // Build and save index
    auto originalIndex = BuildIndex<int8_t>("original_index", vecset, metaset);
    BOOST_REQUIRE(originalIndex != nullptr);
    BOOST_REQUIRE(originalIndex->SaveIndex("original_index") == ErrorCode::Success);

    float recall = Search<int8_t>(originalIndex, queryset, vecset, addvecset, K, truth, N);
    std::cout << "original: recall@" << K << "= " << recall << std::endl;

    for (int iter = 0; iter < iterations; iter++)
    {

        InsertVectors<int8_t>(static_cast<SPANN::Index<int8_t> *>(originalIndex.get()), 1, insertBatchSize, addvecset,
                              metaset, iter * insertBatchSize);
        for (int i = 0; i < deleteBatchSize; i++)
            originalIndex->DeleteIndex(iter * deleteBatchSize + i);

        recall = Search<int8_t>(originalIndex, queryset, vecset, addvecset, K, truth, N, iter + 1);
        std::cout << "iter " << iter << ": recall@" << K << "=" << recall << std::endl;
    }
    std::cout << "Before Refine:" << " recall@" << K << "=" << recall << std::endl;
    static_cast<SPANN::Index<int8_t> *>(originalIndex.get())->GetDBStat();
    BOOST_REQUIRE(originalIndex->SaveIndex("original_index") == ErrorCode::Success);
    originalIndex = nullptr;

    BOOST_REQUIRE(VectorIndex::LoadIndex("original_index", originalIndex) == ErrorCode::Success);
    BOOST_REQUIRE(originalIndex != nullptr);
    BOOST_REQUIRE(originalIndex->Check() == ErrorCode::Success);

    recall = Search<int8_t>(originalIndex, queryset, vecset, addvecset, K, truth, N, iterations);
    std::cout << "After Refine:" << " recall@" << K << "=" << recall << std::endl;
    static_cast<SPANN::Index<int8_t> *>(originalIndex.get())->GetDBStat();
    BOOST_REQUIRE(originalIndex->SaveIndex("original_index") == ErrorCode::Success);
    originalIndex = nullptr;

    std::filesystem::remove_all("original_index");
}

BOOST_AUTO_TEST_CASE(CacheTest)
{
    using namespace SPFreshTest;

    int iterations = 5;
    int insertBatchSize = N / iterations;
    int deleteBatchSize = N / iterations;

    // Generate test data
    std::shared_ptr<VectorSet> vecset, addvecset, queryset, truth;
    std::shared_ptr<MetadataSet> metaset, addmetaset;

    TestUtils::TestDataGenerator<int8_t> generator(N, queries, M, K, "L2");
    generator.RunBatches(vecset, metaset, addvecset, addmetaset, queryset, N, insertBatchSize, deleteBatchSize,
                         iterations, truth);

    // Build and save index
    std::shared_ptr<VectorIndex> originalIndex, finalIndex;
    
    std::filesystem::remove_all("original_index");

    originalIndex = BuildIndex<int8_t>("original_index", vecset, metaset);
    BOOST_REQUIRE(originalIndex != nullptr);
    BOOST_REQUIRE(originalIndex->SaveIndex("original_index") == ErrorCode::Success);
    originalIndex = nullptr;

   
    for (int iter = 0; iter < iterations; iter++)
    {
        if (direxists(("clone_index_" + std::to_string(iter)).c_str()))
        {
            std::filesystem::remove_all("clone_index_" + std::to_string(iter));
        }
    }
    
    std::string prevPath = "original_index";
    float recall = 0.0;
    
    std::cout << "=================No Cache===================" << std::endl;
    
    for (int iter = 0; iter < iterations; iter++)
    {
        std::string clone_path = "clone_index_" + std::to_string(iter);
        std::shared_ptr<VectorIndex> prevIndex;
        BOOST_REQUIRE(VectorIndex::LoadIndex(prevPath, prevIndex) == ErrorCode::Success);
        BOOST_REQUIRE(prevIndex != nullptr);
        auto t0 = std::chrono::high_resolution_clock::now();
        BOOST_REQUIRE(prevIndex->Check() == ErrorCode::Success);
        std::cout << "[INFO] Check time for iteration " << iter << ": "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - t0).count()
                  << " ms" << std::endl;
        

        auto cloneIndex = prevIndex->Clone(clone_path);
        prevIndex = nullptr;
        BOOST_REQUIRE(cloneIndex->Check() == ErrorCode::Success);
        
        recall = Search<int8_t>(cloneIndex, queryset, vecset, addvecset, K, truth, N, iter);
        std::cout << "[INFO] After Save, Clone and Load:" << " recall@" << K << "=" << recall << std::endl;
        static_cast<SPANN::Index<int8_t> *>(cloneIndex.get())->GetDBStat();

        auto t1 = std::chrono::high_resolution_clock::now();
        InsertVectors<int8_t>(static_cast<SPANN::Index<int8_t> *>(cloneIndex.get()), 1, insertBatchSize, addvecset,
                              metaset, iter * insertBatchSize);
        std::cout << "[INFO] Insert time for iteration " << iter << ": "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - t1).count()
                  << " ms" << std::endl;
        
        for (int i = 0; i < deleteBatchSize; i++)
            cloneIndex->DeleteIndex(iter * deleteBatchSize + i);

        recall = Search<int8_t>(cloneIndex, queryset, vecset, addvecset, K, truth, N, iter + 1);
        std::cout << "[INFO] After iter " << iter << ": recall@" << K << "=" << recall << std::endl;
        static_cast<SPANN::Index<int8_t> *>(cloneIndex.get())->GetDBStat();

        BOOST_REQUIRE(cloneIndex->SaveIndex(clone_path) == ErrorCode::Success);
        cloneIndex = nullptr;
        prevPath = clone_path;
    }
 
    BOOST_REQUIRE(VectorIndex::LoadIndex(prevPath, finalIndex) == ErrorCode::Success);
    BOOST_REQUIRE(finalIndex != nullptr);
    auto t = std::chrono::high_resolution_clock::now();
    BOOST_REQUIRE(finalIndex->Check() == ErrorCode::Success);
    std::cout << "[INFO] Check time for iteration " << iterations << ": "
                << std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - t).count()
                << " ms" << std::endl;
    
    recall = Search<int8_t>(finalIndex, queryset, vecset, addvecset, K, truth, N, iterations);
    std::cout << "[INFO] After Save and Load:" << " recall@" << K << "=" << recall << std::endl;
    static_cast<SPANN::Index<int8_t> *>(finalIndex.get())->GetDBStat();
    finalIndex = nullptr;
    for (int iter = 0; iter < iterations; iter++)
    {
        std::filesystem::remove_all("clone_index_" + std::to_string(iter));
    }
    
    std::cout << "=================Enable Cache===================" << std::endl;
    prevPath = "original_index";
    for (int iter = 0; iter < iterations; iter++)
    {
        std::string clone_path = "clone_index_" + std::to_string(iter);
        std::shared_ptr<VectorIndex> prevIndex;
        BOOST_REQUIRE(VectorIndex::LoadIndex(prevPath, prevIndex) == ErrorCode::Success);
        BOOST_REQUIRE(prevIndex != nullptr);
        auto t0 = std::chrono::high_resolution_clock::now();
        BOOST_REQUIRE(prevIndex->Check() == ErrorCode::Success);
        std::cout << "[INFO] Check time for iteration " << iter << ": "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - t0).count()
                  << " ms" << std::endl;
        

        prevIndex->SetParameter("CacheSizeGB", "4", "BuildSSDIndex");
        prevIndex->SetParameter("CacheShards", "2", "BuildSSDIndex");
        
        BOOST_REQUIRE(prevIndex->SaveIndex(prevPath) == ErrorCode::Success);
        auto cloneIndex = prevIndex->Clone(clone_path);

        recall = Search<int8_t>(cloneIndex, queryset, vecset, addvecset, K, truth, N, iter);
        std::cout << "[INFO] After Save, Clone and Load:" << " recall@" << K << "=" << recall << std::endl;
        static_cast<SPANN::Index<int8_t> *>(cloneIndex.get())->GetDBStat();

        auto t1 = std::chrono::high_resolution_clock::now();
        InsertVectors<int8_t>(static_cast<SPANN::Index<int8_t> *>(cloneIndex.get()), 1, insertBatchSize, addvecset,
                              metaset, iter * insertBatchSize);
        std::cout << "[INFO] Insert time for iteration " << iter << ": "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - t1).count()
                  << " ms" << std::endl;

        for (int i = 0; i < deleteBatchSize; i++)
            cloneIndex->DeleteIndex(iter * deleteBatchSize + i);

        recall = Search<int8_t>(cloneIndex, queryset, vecset, addvecset, K, truth, N, iter + 1);
        std::cout << "[INFO] After iter " << iter << ": recall@" << K << "=" << recall << std::endl;
        static_cast<SPANN::Index<int8_t> *>(cloneIndex.get())->GetDBStat();

        BOOST_REQUIRE(cloneIndex->SaveIndex(clone_path) == ErrorCode::Success);
        cloneIndex = nullptr;
        prevPath = clone_path;
    }
    BOOST_REQUIRE(VectorIndex::LoadIndex(prevPath, finalIndex) == ErrorCode::Success);
    BOOST_REQUIRE(finalIndex != nullptr);
    auto tt = std::chrono::high_resolution_clock::now();
    BOOST_REQUIRE(finalIndex->Check() == ErrorCode::Success);
    std::cout << "[INFO] Check time for iteration " << iterations << ": "
                << std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - tt).count()
                << " ms" << std::endl;
    
    recall = Search<int8_t>(finalIndex, queryset, vecset, addvecset, K, truth, N, iterations);
    std::cout << "[INFO] After Save and Load:" << " recall@" << K << "=" << recall << std::endl;
    static_cast<SPANN::Index<int8_t> *>(finalIndex.get())->GetDBStat();
    finalIndex = nullptr;

    for (int iter = 0; iter < iterations; iter++)
    {
        std::filesystem::remove_all("clone_index_" + std::to_string(iter));
    }
    std::filesystem::remove_all("original_index");
}

BOOST_AUTO_TEST_CASE(IterativeSearchPerf)
{
    using namespace SPFreshTest;

    constexpr int insertIterations = 5;
    constexpr int insertBatchSize = 60000;
    constexpr int appendBatchSize = 40000;
    constexpr int dimension = 100;
    std::shared_ptr<VectorSet> vecset = get_embeddings<float>(0, insertBatchSize, dimension, -1);
    std::shared_ptr<MetadataSet> metaset = TestUtils::TestDataGenerator<float>::GenerateMetadataSet(insertBatchSize, 0);

    auto originalIndex = BuildIndex<float>("original_index", vecset, metaset);
    BOOST_REQUIRE(originalIndex != nullptr);
    BOOST_REQUIRE(originalIndex->SaveIndex("original_index") == ErrorCode::Success);
    originalIndex = nullptr;

    std::string prevPath = "original_index";
    for (int iter = 0; iter < insertIterations; iter++)
    {
        std::string clone_path = "clone_index_" + std::to_string(iter);
        std::shared_ptr<VectorIndex> prevIndex;
        BOOST_REQUIRE(VectorIndex::LoadIndex(prevPath, prevIndex) == ErrorCode::Success);
        BOOST_REQUIRE(prevIndex != nullptr);
        auto t0 = std::chrono::high_resolution_clock::now();
        BOOST_REQUIRE(prevIndex->Check() == ErrorCode::Success);
        std::cout << "Check time for iteration " << iter << ": "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() - t0).count()
                  << " ms" << std::endl;

        auto cloneIndex = prevIndex->Clone(clone_path);
        auto *cloneIndexPtr = static_cast<SPANN::Index<float> *>(cloneIndex.get());
        std::shared_ptr<VectorSet> tmpvecs = get_embeddings<float>(
            insertBatchSize + iter * appendBatchSize, insertBatchSize + (iter + 1) * appendBatchSize, dimension, -1);
        std::shared_ptr<MetadataSet> tmpmetas = TestUtils::TestDataGenerator<float>::GenerateMetadataSet(
            appendBatchSize, insertBatchSize + (iter)*appendBatchSize);
        auto t1 = std::chrono::high_resolution_clock::now();
        InsertVectors<float>(cloneIndexPtr, 1, appendBatchSize, tmpvecs, tmpmetas);
        std::cout << "Insert time for iteration " << iter << ": "
                  << std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::high_resolution_clock::now() -
                                                                           t1)
                         .count()
                  << " ms" << std::endl;

        BOOST_REQUIRE(cloneIndex->SaveIndex(clone_path) == ErrorCode::Success);
        cloneIndex = nullptr;
    }

    for (int iter = 0; iter < insertIterations; iter++)
    {
        std::filesystem::remove_all("clone_index_" + std::to_string(iter));
    }
    std::filesystem::remove_all("original_index");
}

BOOST_AUTO_TEST_CASE(BenchmarkFromConfig)
{
    using namespace SPFreshTest;

    // Check if benchmark config is provided via environment variable
    const char *configPath = std::getenv("BENCHMARK_CONFIG");
    if (configPath == nullptr)
    {
        BOOST_TEST_MESSAGE("Skipping benchmark test - BENCHMARK_CONFIG environment variable not set");
        return;
    }

    BOOST_TEST_MESSAGE("Running benchmark with config: " << configPath);

    // Read benchmark configuration
    Helper::IniReader iniReader;
    if (ErrorCode::Success != iniReader.LoadIniFile(configPath))
    {
        BOOST_FAIL("Failed to load benchmark config file: " << configPath);
        return;
    }

    // Parse config parameters
    std::string vectorPath = iniReader.GetParameter("Benchmark", "VectorPath", std::string(""));
    std::string queryPath = iniReader.GetParameter("Benchmark", "QueryPath", std::string(""));
    std::string truthPath = iniReader.GetParameter("Benchmark", "TruthPath", std::string(""));
    std::string indexPath = iniReader.GetParameter("Benchmark", "IndexPath", std::string("benchmark_index"));
    std::string quantizerFilePath = iniReader.GetParameter("Benchmark", "QuantizerFilePath", std::string(""));
    int quantizedDim = iniReader.GetParameter("Benchmark", "QuantizedDim", 0);

    VectorValueType valueType = VectorValueType::Float;
    std::string valueTypeStr = iniReader.GetParameter("Benchmark", "ValueType", std::string("Float"));
    if (valueTypeStr == "Float")
        valueType = VectorValueType::Float;
    else if (valueTypeStr == "Int8")
        valueType = VectorValueType::Int8;
    else if (valueTypeStr == "UInt8")
        valueType = VectorValueType::UInt8;

    int dimension = iniReader.GetParameter("Benchmark", "Dimension", 128);
    int baseVectorCount = iniReader.GetParameter("Benchmark", "BaseVectorCount", 8000);
    int insertVectorCount = iniReader.GetParameter("Benchmark", "InsertVectorCount", 2000);
    int deleteVectorCount = iniReader.GetParameter("Benchmark", "DeleteVectorCount", 2000);
    int batchNum = iniReader.GetParameter("Benchmark", "BatchNum", 100);
    int topK = iniReader.GetParameter("Benchmark", "TopK", 10);
    int numThreads = iniReader.GetParameter("Benchmark", "NumThreads", 32);
    int numQueries = iniReader.GetParameter("Benchmark", "NumQueries", 1000);
    DistCalcMethod distMethod = iniReader.GetParameter("Benchmark", "DistMethod", DistCalcMethod::L2);
    bool rebuild = iniReader.GetParameter("Benchmark", "Rebuild", true);
    int resume = iniReader.GetParameter("Benchmark", "Resume", -1);

    BOOST_TEST_MESSAGE("=== Benchmark Configuration ===");
    BOOST_TEST_MESSAGE("Vector Path: " << vectorPath);
    BOOST_TEST_MESSAGE("Query Path: " << queryPath);
    BOOST_TEST_MESSAGE("Base Vectors: " << baseVectorCount);
    BOOST_TEST_MESSAGE("Insert Vectors: " << insertVectorCount);
    BOOST_TEST_MESSAGE("Dimension: " << dimension);
    BOOST_TEST_MESSAGE("Batch Number: " << batchNum);
    BOOST_TEST_MESSAGE("Top-K: " << topK);
    BOOST_TEST_MESSAGE("Threads: " << numThreads);
    BOOST_TEST_MESSAGE("Queries: " << numQueries);
    BOOST_TEST_MESSAGE("DistMethod: " << Helper::Convert::ConvertToString(distMethod));
    if (!quantizerFilePath.empty())
    {
        BOOST_TEST_MESSAGE("QuantizerFilePath: " << quantizerFilePath);
        BOOST_TEST_MESSAGE("QuantizedDim: " << quantizedDim);
    }

    // Get output file path from environment variable or use default
    const char *outputPath = std::getenv("BENCHMARK_OUTPUT");
    std::string outputFile = outputPath ? std::string(outputPath) : "output.json";
    BOOST_TEST_MESSAGE("Output File: " << outputFile);

    // Dispatch to appropriate type
    if (valueType == VectorValueType::Float)
    {
        RunBenchmark<float>(vectorPath, queryPath, truthPath, distMethod, indexPath, dimension, baseVectorCount,
                    insertVectorCount, deleteVectorCount, batchNum, topK, numThreads, numQueries, outputFile, 
                    rebuild, resume, quantizerFilePath, quantizedDim);
    }
    else if (valueType == VectorValueType::Int8)
    {
        RunBenchmark<std::int8_t>(vectorPath, queryPath, truthPath, distMethod, indexPath, dimension, baseVectorCount,
                      insertVectorCount, deleteVectorCount, batchNum, topK, numThreads, numQueries,
                      outputFile, rebuild, resume, quantizerFilePath, quantizedDim);
    }
    else if (valueType == VectorValueType::UInt8)
    {
        RunBenchmark<std::uint8_t>(vectorPath, queryPath, truthPath, distMethod, indexPath, dimension, baseVectorCount,
                       insertVectorCount, deleteVectorCount, batchNum, topK, numThreads, numQueries,
                       outputFile, rebuild, resume, quantizerFilePath, quantizedDim);
    }

    //std::filesystem::remove_all(indexPath);
}
BOOST_AUTO_TEST_SUITE_END()
