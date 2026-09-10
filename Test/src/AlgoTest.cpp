// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/Core/Common/CommonUtils.h"
#include "inc/Core/VectorIndex.h"
#include "inc/Core/BKT/Index.h"
#include "inc/Helper/SimpleIniReader.h"
#include "inc/Test.h"

#include <chrono>
#include <unordered_set>

template <typename T>
void Build(SPTAG::IndexAlgoType algo, std::string distCalcMethod, std::shared_ptr<SPTAG::VectorSet> &vec,
           std::shared_ptr<SPTAG::MetadataSet> &meta, const std::string out)
{

    std::shared_ptr<SPTAG::VectorIndex> vecIndex =
        SPTAG::VectorIndex::CreateInstance(algo, SPTAG::GetEnumValueType<T>());
    BOOST_CHECK(nullptr != vecIndex);

    if (algo != SPTAG::IndexAlgoType::SPANN)
    {
        vecIndex->SetParameter("DistCalcMethod", distCalcMethod);
        vecIndex->SetParameter("NumberOfThreads", "16");
    }
    else
    {
        vecIndex->SetParameter("IndexAlgoType", "BKT", "Base");
        vecIndex->SetParameter("DistCalcMethod", distCalcMethod, "Base");

        vecIndex->SetParameter("isExecute", "true", "SelectHead");
        vecIndex->SetParameter("NumberOfThreads", "4", "SelectHead");
        vecIndex->SetParameter("Ratio", "0.2", "SelectHead"); // vecIndex->SetParameter("Count", "200", "SelectHead");

        vecIndex->SetParameter("isExecute", "true", "BuildHead");
        vecIndex->SetParameter("RefineIterations", "3", "BuildHead");
        vecIndex->SetParameter("NumberOfThreads", "4", "BuildHead");

        vecIndex->SetParameter("isExecute", "true", "BuildSSDIndex");
        vecIndex->SetParameter("BuildSsdIndex", "true", "BuildSSDIndex");
        vecIndex->SetParameter("NumberOfThreads", "4", "BuildSSDIndex");
        vecIndex->SetParameter("PostingPageLimit", "12", "BuildSSDIndex");
        vecIndex->SetParameter("SearchPostingPageLimit", "12", "BuildSSDIndex");
        vecIndex->SetParameter("InternalResultNum", "64", "BuildSSDIndex");
        vecIndex->SetParameter("SearchInternalResultNum", "64", "BuildSSDIndex");
    }

    BOOST_CHECK(SPTAG::ErrorCode::Success == vecIndex->BuildIndex(vec, meta));
    BOOST_CHECK(SPTAG::ErrorCode::Success == vecIndex->SaveIndex(out));
}

template <typename T>
void BuildWithMetaMapping(SPTAG::IndexAlgoType algo, std::string distCalcMethod, std::shared_ptr<SPTAG::VectorSet> &vec,
                          std::shared_ptr<SPTAG::MetadataSet> &meta, const std::string out)
{

    std::shared_ptr<SPTAG::VectorIndex> vecIndex =
        SPTAG::VectorIndex::CreateInstance(algo, SPTAG::GetEnumValueType<T>());
    BOOST_CHECK(nullptr != vecIndex);

    if (algo != SPTAG::IndexAlgoType::SPANN)
    {
        vecIndex->SetParameter("DistCalcMethod", distCalcMethod);
        vecIndex->SetParameter("NumberOfThreads", "16");
    }
    else
    {
        vecIndex->SetParameter("IndexAlgoType", "BKT", "Base");
        vecIndex->SetParameter("DistCalcMethod", distCalcMethod, "Base");

        vecIndex->SetParameter("isExecute", "true", "SelectHead");
        vecIndex->SetParameter("NumberOfThreads", "4", "SelectHead");
        vecIndex->SetParameter("Ratio", "0.2", "SelectHead"); // vecIndex->SetParameter("Count", "200", "SelectHead");

        vecIndex->SetParameter("isExecute", "true", "BuildHead");
        vecIndex->SetParameter("RefineIterations", "3", "BuildHead");
        vecIndex->SetParameter("NumberOfThreads", "4", "BuildHead");

        vecIndex->SetParameter("isExecute", "true", "BuildSSDIndex");
        vecIndex->SetParameter("BuildSsdIndex", "true", "BuildSSDIndex");
        vecIndex->SetParameter("NumberOfThreads", "4", "BuildSSDIndex");
        vecIndex->SetParameter("PostingPageLimit", "12", "BuildSSDIndex");
        vecIndex->SetParameter("SearchPostingPageLimit", "12", "BuildSSDIndex");
        vecIndex->SetParameter("InternalResultNum", "64", "BuildSSDIndex");
        vecIndex->SetParameter("SearchInternalResultNum", "64", "BuildSSDIndex");
    }

    BOOST_CHECK(SPTAG::ErrorCode::Success == vecIndex->BuildIndex(vec, meta, true));
    BOOST_CHECK(SPTAG::ErrorCode::Success == vecIndex->SaveIndex(out));
}

template <typename T> void Search(const std::string folder, T *vec, SPTAG::SizeType n, int k, std::string *truthmeta)
{
    std::shared_ptr<SPTAG::VectorIndex> vecIndex;
    BOOST_CHECK(SPTAG::ErrorCode::Success == SPTAG::VectorIndex::LoadIndex(folder, vecIndex));
    BOOST_CHECK(nullptr != vecIndex);

    for (SPTAG::SizeType i = 0; i < n; i++)
    {
        SPTAG::QueryResult res(vec, k, true);
        vecIndex->SearchIndex(res);
        std::unordered_set<std::string> resmeta;
        for (int j = 0; j < k; j++)
        {
            resmeta.insert(std::string((char *)res.GetMetadata(j).Data(), res.GetMetadata(j).Length()));
            std::cout << res.GetResult(j)->Dist << "@(" << res.GetResult(j)->VID << ","
                      << std::string((char *)res.GetMetadata(j).Data(), res.GetMetadata(j).Length()) << ") ";
        }
        std::cout << std::endl;
        for (int j = 0; j < k; j++)
        {
            BOOST_CHECK(resmeta.find(truthmeta[i * k + j]) != resmeta.end());
        }
        vec += vecIndex->GetFeatureDim();
    }
    vecIndex.reset();
}

template <typename T>
void Add(const std::string folder, std::shared_ptr<SPTAG::VectorSet> &vec, std::shared_ptr<SPTAG::MetadataSet> &meta,
         const std::string out)
{
    std::shared_ptr<SPTAG::VectorIndex> vecIndex;
    BOOST_CHECK(SPTAG::ErrorCode::Success == SPTAG::VectorIndex::LoadIndex(folder, vecIndex));
    BOOST_CHECK(nullptr != vecIndex);

    BOOST_CHECK(SPTAG::ErrorCode::Success == vecIndex->AddIndex(vec, meta));
    BOOST_CHECK(SPTAG::ErrorCode::Success == vecIndex->SaveIndex(out));
    vecIndex.reset();
}

template <typename T>
void AddOneByOne(SPTAG::IndexAlgoType algo, std::string distCalcMethod, std::shared_ptr<SPTAG::VectorSet> &vec,
                 std::shared_ptr<SPTAG::MetadataSet> &meta, const std::string out)
{
    std::shared_ptr<SPTAG::VectorIndex> vecIndex =
        SPTAG::VectorIndex::CreateInstance(algo, SPTAG::GetEnumValueType<T>());
    BOOST_CHECK(nullptr != vecIndex);

    vecIndex->SetParameter("DistCalcMethod", distCalcMethod);
    vecIndex->SetParameter("NumberOfThreads", "16");

    auto t1 = std::chrono::high_resolution_clock::now();
    for (SPTAG::SizeType i = 0; i < vec->Count(); i++)
    {
        SPTAG::ByteArray metaarr = meta->GetMetadata(i);
        std::uint64_t offset[2] = {0, metaarr.Length()};
        std::shared_ptr<SPTAG::MetadataSet> metaset(new SPTAG::MemMetadataSet(
            metaarr, SPTAG::ByteArray((std::uint8_t *)offset, 2 * sizeof(std::uint64_t), false), 1));
        SPTAG::ErrorCode ret = vecIndex->AddIndex(vec->GetVector(i), 1, vec->Dimension(), metaset, true);
        if (SPTAG::ErrorCode::Success != ret)
            std::cerr << "Error AddIndex(" << (int)(ret) << ") for vector " << i << std::endl;
    }
    auto t2 = std::chrono::high_resolution_clock::now();
    std::cout << "AddIndex time: "
              << (std::chrono::duration_cast<std::chrono::microseconds>(t2 - t1).count() / (float)(vec->Count()))
              << "us" << std::endl;

    Sleep(10000);

    BOOST_CHECK(SPTAG::ErrorCode::Success == vecIndex->SaveIndex(out));
}

template <typename T> void Delete(const std::string folder, T *vec, SPTAG::SizeType n, const std::string out)
{
    std::shared_ptr<SPTAG::VectorIndex> vecIndex;
    BOOST_CHECK(SPTAG::ErrorCode::Success == SPTAG::VectorIndex::LoadIndex(folder, vecIndex));
    BOOST_CHECK(nullptr != vecIndex);

    BOOST_CHECK(SPTAG::ErrorCode::Success == vecIndex->DeleteIndex((const void *)vec, n));
    BOOST_CHECK(SPTAG::ErrorCode::Success == vecIndex->SaveIndex(out));
    vecIndex.reset();
}

template <typename T> void Test(SPTAG::IndexAlgoType algo, std::string distCalcMethod)
{
    SPTAG::SizeType n = 2000, q = 3;
    SPTAG::DimensionType m = 10;
    int k = 3;
    std::vector<T> vec;
    for (SPTAG::SizeType i = 0; i < n; i++)
    {
        for (SPTAG::DimensionType j = 0; j < m; j++)
        {
            vec.push_back((T)i);
        }
    }

    std::vector<T> query;
    for (SPTAG::SizeType i = 0; i < q; i++)
    {
        for (SPTAG::DimensionType j = 0; j < m; j++)
        {
            query.push_back((T)i * 2);
        }
    }

    std::vector<char> meta;
    std::vector<std::uint64_t> metaoffset;
    for (SPTAG::SizeType i = 0; i < n; i++)
    {
        metaoffset.push_back((std::uint64_t)meta.size());
        std::string a = std::to_string(i);
        for (size_t j = 0; j < a.length(); j++)
            meta.push_back(a[j]);
    }
    metaoffset.push_back((std::uint64_t)meta.size());

    std::shared_ptr<SPTAG::VectorSet> vecset(new SPTAG::BasicVectorSet(
        SPTAG::ByteArray((std::uint8_t *)vec.data(), sizeof(T) * n * m, false), SPTAG::GetEnumValueType<T>(), m, n));

    std::shared_ptr<SPTAG::MetadataSet> metaset(new SPTAG::MemMetadataSet(
        SPTAG::ByteArray((std::uint8_t *)meta.data(), meta.size() * sizeof(char), false),
        SPTAG::ByteArray((std::uint8_t *)metaoffset.data(), metaoffset.size() * sizeof(std::uint64_t), false), n));

    Build<T>(algo, distCalcMethod, vecset, metaset, "testindices");
    std::string truthmeta1[] = {"0", "1", "2", "2", "1", "3", "4", "3", "5"};
    Search<T>("testindices", query.data(), q, k, truthmeta1);

    if (algo != SPTAG::IndexAlgoType::SPANN)
    {
        Add<T>("testindices", vecset, metaset, "testindices");
        std::string truthmeta2[] = {"0", "0", "1", "2", "2", "1", "4", "4", "3"};
        Search<T>("testindices", query.data(), q, k, truthmeta2);

        Delete<T>("testindices", query.data(), q, "testindices");
        std::string truthmeta3[] = {"1", "1", "3", "1", "3", "1", "3", "5", "3"};
        Search<T>("testindices", query.data(), q, k, truthmeta3);
    }

    BuildWithMetaMapping<T>(algo, distCalcMethod, vecset, metaset, "testindices");
    std::string truthmeta4[] = {"0", "1", "2", "2", "1", "3", "4", "3", "5"};
    Search<T>("testindices", query.data(), q, k, truthmeta4);

    if (algo != SPTAG::IndexAlgoType::SPANN)
    {
        Add<T>("testindices", vecset, metaset, "testindices");
        std::string truthmeta5[] = {"0", "1", "2", "2", "1", "3", "4", "3", "5"};
        Search<T>("testindices", query.data(), q, k, truthmeta5);

        AddOneByOne<T>(algo, distCalcMethod, vecset, metaset, "testindices");
        std::string truthmeta6[] = {"0", "1", "2", "2", "1", "3", "4", "3", "5"};
        Search<T>("testindices", query.data(), q, k, truthmeta6);
    }
}

BOOST_AUTO_TEST_SUITE(AlgoTest)

BOOST_AUTO_TEST_CASE(KDTTest)
{
    Test<float>(SPTAG::IndexAlgoType::KDT, "L2");
}

BOOST_AUTO_TEST_CASE(BKTTest)
{
    Test<float>(SPTAG::IndexAlgoType::BKT, "L2");
}

BOOST_AUTO_TEST_CASE(NativeResultAdmissionFilter)
{
    constexpr SPTAG::SizeType kVectorCount = 256;
    constexpr SPTAG::DimensionType kDimension = 4;
    constexpr int k = 8;
    std::vector<float> vectors(
        static_cast<size_t>(kVectorCount) * kDimension);
    for (SPTAG::SizeType row = 0; row < kVectorCount; ++row) {
        for (SPTAG::DimensionType col = 0; col < kDimension; ++col) {
            vectors[static_cast<size_t>(row) * kDimension + col] =
                static_cast<float>(
                    (row * (col + 5) + col * 29) % 263) /
                263.0f;
        }
    }

    float target[kDimension];
    for (SPTAG::DimensionType col = 0; col < kDimension; ++col) {
        target[col] =
            static_cast<float>(
                (257 * (col + 5) + col * 29) % 263) /
            263.0f;
    }
    for (auto algorithm : {SPTAG::IndexAlgoType::BKT, SPTAG::IndexAlgoType::KDT})
    {
        auto index = SPTAG::VectorIndex::CreateInstance(
            algorithm, SPTAG::VectorValueType::Float);
        BOOST_REQUIRE(index != nullptr);
        BOOST_REQUIRE(index->SetParameter("DistCalcMethod", "L2") == SPTAG::ErrorCode::Success);
        BOOST_REQUIRE(index->SetParameter("NumberOfThreads", "1") == SPTAG::ErrorCode::Success);
        BOOST_REQUIRE(index->BuildIndex(vectors.data(), kVectorCount, kDimension) ==
            SPTAG::ErrorCode::Success);
        for (int bfs : {0, 1})
        {
            if (algorithm == SPTAG::IndexAlgoType::KDT && bfs != 0) continue;
            if (algorithm == SPTAG::IndexAlgoType::BKT)
                BOOST_REQUIRE(index->SetParameter("EnableBfs", bfs ? "1" : "0") ==
                    SPTAG::ErrorCode::Success);
            for (int maxCheck : {1, 32, 128, 4096})
            {
                BOOST_TEST_CONTEXT("algorithm=" << static_cast<int>(algorithm) <<
                    " bfs=" << bfs << " maxCheck=" << maxCheck)
                {
                    SPTAG::QueryResult native(target, k, false), admitted(target, k, false);
                    SPTAG::QueryResult sparse(target, k, false), empty(target, k, false);
                    SPTAG::QueryResult replay(target, k, false);
                    std::vector<SPTAG::SizeType> allVisits, sparseVisits, emptyVisits;
                    BOOST_REQUIRE(index->SearchIndexWithMaxCheck(native, maxCheck) ==
                        SPTAG::ErrorCode::Success);
                    BOOST_REQUIRE(index->SearchIndexWithResultFilter(admitted,
                        [&](SPTAG::SizeType id) { allVisits.push_back(id); return true; },
                        maxCheck) == SPTAG::ErrorCode::Success);
                    BOOST_REQUIRE(index->SearchIndexWithResultFilter(sparse,
                        [&](SPTAG::SizeType id) { sparseVisits.push_back(id); return id % 32 == 0; },
                        maxCheck) == SPTAG::ErrorCode::Success);
                    BOOST_REQUIRE(index->SearchIndexWithResultFilter(empty,
                        [&](SPTAG::SizeType id) { emptyVisits.push_back(id); return false; },
                        maxCheck) == SPTAG::ErrorCode::Success);
                    BOOST_REQUIRE(index->SearchIndexWithMaxCheck(replay, maxCheck) ==
                        SPTAG::ErrorCode::Success);
                    BOOST_CHECK_LE(native.GetScanned(), maxCheck);
                    BOOST_CHECK_EQUAL(native.GetScanned(), admitted.GetScanned());
                    BOOST_CHECK_EQUAL(native.GetScanned(), sparse.GetScanned());
                    BOOST_CHECK_EQUAL(native.GetScanned(), empty.GetScanned());
                    BOOST_CHECK_EQUAL(native.GetScanned(), replay.GetScanned());
                    BOOST_CHECK_EQUAL_COLLECTIONS(allVisits.begin(), allVisits.end(),
                        sparseVisits.begin(), sparseVisits.end());
                    BOOST_CHECK_EQUAL_COLLECTIONS(allVisits.begin(), allVisits.end(),
                        emptyVisits.begin(), emptyVisits.end());
                    std::vector<int> visits(kVectorCount, 0);
                    std::vector<std::pair<float, SPTAG::SizeType>> eligible;
                    for (const auto id : allVisits)
                    {
                        BOOST_REQUIRE(id >= 0 && id < kVectorCount);
                        BOOST_CHECK_EQUAL(++visits[static_cast<size_t>(id)], 1);
                        if (id % 32 == 0)
                            eligible.emplace_back(index->ComputeDistance(target,
                                vectors.data() + static_cast<size_t>(id) * kDimension), id);
                    }
                    std::sort(eligible.begin(), eligible.end());
                    for (int rank = 0; rank < k; ++rank)
                    {
                        BOOST_CHECK_EQUAL(admitted.GetResult(rank)->VID, native.GetResult(rank)->VID);
                        BOOST_CHECK_EQUAL(admitted.GetResult(rank)->Dist, native.GetResult(rank)->Dist);
                        BOOST_CHECK_EQUAL(replay.GetResult(rank)->VID, native.GetResult(rank)->VID);
                        BOOST_CHECK_EQUAL(replay.GetResult(rank)->Dist, native.GetResult(rank)->Dist);
                        BOOST_CHECK_LT(empty.GetResult(rank)->VID, 0);
                        if (static_cast<size_t>(rank) < eligible.size())
                        {
                            BOOST_CHECK_EQUAL(sparse.GetResult(rank)->VID, eligible[rank].second);
                            BOOST_CHECK_EQUAL(sparse.GetResult(rank)->Dist, eligible[rank].first);
                        }
                        else
                            BOOST_CHECK_LT(sparse.GetResult(rank)->VID, 0);
                    }
                }
            }
        }

        if (algorithm == SPTAG::IndexAlgoType::BKT)
            BOOST_REQUIRE(index->SetParameter("EnableBfs", "0") == SPTAG::ErrorCode::Success);
        BOOST_REQUIRE(index->SetParameter("NumberOfInitialDynamicPivots", "1") == SPTAG::ErrorCode::Success);
        BOOST_REQUIRE(index->SetParameter("NumberOfOtherDynamicPivots", "1") == SPTAG::ErrorCode::Success);
        SPTAG::QueryResult native(target, 1, false), bridgePreserved(target, 1, false);
        std::vector<SPTAG::SizeType> visited;
        BOOST_REQUIRE(index->SearchIndexWithResultFilter(native,
            [&](SPTAG::SizeType id) { visited.push_back(id); return true; },
            128) == SPTAG::ErrorCode::Success);
        SPTAG::SizeType reachable = -1;
        for (const auto id : visited)
            if (id != native.GetResult(0)->VID) reachable = id;
        BOOST_REQUIRE_GE(reachable, 0);
        BOOST_REQUIRE(index->SearchIndexWithResultFilter(bridgePreserved,
            [reachable](SPTAG::SizeType id) { return id == reachable; },
            128) == SPTAG::ErrorCode::Success);
        BOOST_CHECK_EQUAL(bridgePreserved.GetScanned(), native.GetScanned());
        BOOST_CHECK_EQUAL(bridgePreserved.GetResult(0)->VID, reachable);
    }
}

BOOST_AUTO_TEST_CASE(BKTMetadataAdmissionWorkspaceReuse)
{
    constexpr SPTAG::SizeType count = 64;
    constexpr SPTAG::DimensionType dimension = 4;
    constexpr int k = 8;
    std::vector<float> vectors(static_cast<size_t>(count) * dimension);
    std::vector<std::uint8_t> metadata(count);
    std::vector<std::uint64_t> offsets(count + 1);
    for (SPTAG::SizeType id = 0; id < count; ++id)
    {
        for (SPTAG::DimensionType col = 0; col < dimension; ++col)
            vectors[static_cast<size_t>(id) * dimension + col] = id * (col + 1.0f);
        metadata[id] = static_cast<std::uint8_t>(id);
        offsets[id] = id;
    }
    offsets.back() = count;
    std::shared_ptr<SPTAG::VectorSet> vectorSet = std::make_shared<SPTAG::BasicVectorSet>(
        SPTAG::ByteArray(reinterpret_cast<std::uint8_t*>(vectors.data()),
            vectors.size() * sizeof(float), false), SPTAG::VectorValueType::Float, dimension, count);
    std::shared_ptr<SPTAG::MetadataSet> metadataSet = std::make_shared<SPTAG::MemMetadataSet>(
        SPTAG::ByteArray(metadata.data(), metadata.size(), false),
        SPTAG::ByteArray(reinterpret_cast<std::uint8_t*>(offsets.data()),
            offsets.size() * sizeof(std::uint64_t), false), count);
    std::shared_ptr<SPTAG::VectorIndex> index = std::make_shared<SPTAG::BKT::Index<float>>();
    BOOST_REQUIRE(index->SetParameter("DistCalcMethod", "L2") == SPTAG::ErrorCode::Success);
    BOOST_REQUIRE(index->SetParameter("NumberOfThreads", "1") == SPTAG::ErrorCode::Success);
    BOOST_REQUIRE(index->BuildIndex(vectorSet, metadataSet) == SPTAG::ErrorCode::Success);
    const float* target = vectors.data() + 17 * dimension;
    SPTAG::QueryResult native(target, k, false), idFiltered(target, k, false);
    BOOST_REQUIRE(index->SearchIndexWithMaxCheck(native, 32) == SPTAG::ErrorCode::Success);
    BOOST_REQUIRE(index->SearchIndexWithResultFilter(idFiltered,
        [](SPTAG::SizeType id) { return id % 2 == 0; }, 32) == SPTAG::ErrorCode::Success);
    for (int repeat = 0; repeat < 3; ++repeat)
    {
        SPTAG::QueryResult all(target, k, true), sparse(target, k, true), empty(target, k, false);
        int admissionCalls = 0;
        BOOST_REQUIRE(index->SearchIndexWithFilter(all,
            [&](const SPTAG::ByteArray&) { ++admissionCalls; return true; },
            32) == SPTAG::ErrorCode::Success);
        BOOST_CHECK_GT(admissionCalls, 0);
        BOOST_REQUIRE(index->SearchIndexWithFilter(sparse,
            [](const SPTAG::ByteArray& value) { return value.Data()[0] % 2 == 0; },
            32) == SPTAG::ErrorCode::Success);
        BOOST_REQUIRE(index->SearchIndexWithFilter(empty,
            [](const SPTAG::ByteArray&) { return false; }, 32) == SPTAG::ErrorCode::Success);
        BOOST_CHECK_EQUAL(native.GetScanned(), all.GetScanned());
        BOOST_CHECK_EQUAL(native.GetScanned(), sparse.GetScanned());
        BOOST_CHECK_EQUAL(native.GetScanned(), empty.GetScanned());
        for (int rank = 0; rank < k; ++rank)
        {
            BOOST_CHECK_EQUAL(all.GetResult(rank)->VID, native.GetResult(rank)->VID);
            BOOST_CHECK_EQUAL(all.GetResult(rank)->Dist, native.GetResult(rank)->Dist);
            BOOST_CHECK_EQUAL(sparse.GetResult(rank)->VID, idFiltered.GetResult(rank)->VID);
            BOOST_CHECK_EQUAL(sparse.GetResult(rank)->Dist, idFiltered.GetResult(rank)->Dist);
            BOOST_CHECK_LT(empty.GetResult(rank)->VID, 0);
            if (all.GetResult(rank)->VID >= 0)
            {
                BOOST_REQUIRE_EQUAL(all.GetMetadata(rank).Length(), 1U);
                BOOST_CHECK_EQUAL(all.GetMetadata(rank).Data()[0], all.GetResult(rank)->VID);
            }
        }
    }
}

BOOST_AUTO_TEST_CASE(BKTResultAdmissionCollapsedAliases)
{
    constexpr SPTAG::DimensionType kDimension = 4;
    constexpr SPTAG::SizeType duplicateCount = 1024;
    std::vector<float> duplicateVectors((duplicateCount + 1) * kDimension, 0.0f);
    for (int col = 0; col < kDimension; ++col)
        duplicateVectors[duplicateCount * kDimension + col] = 100.0f;
    auto duplicates = std::make_shared<SPTAG::BKT::Index<float>>();
    const std::pair<const char*, const char*> duplicateParameters[] = {
        {"DistCalcMethod", "L2"}, {"NumberOfThreads", "1"},
        {"BKTKmeansK", "4"}, {"BKTLeafSize", "2"}, {"NeighborhoodSize", "8"},
        {"GraphNeighborhoodScale", "1"}, {"TPTNumber", "1"}, {"TPTLeafSize", "64"},
        {"NumTopDimensionTpTreeSplit", "4"},
        {"CEF", "64"}, {"MaxCheckForRefineGraph", "64"}, {"RefineIterations", "1"},
    };
    for (const auto& parameter : duplicateParameters)
        BOOST_REQUIRE(duplicates->SetParameter(parameter.first, parameter.second) == SPTAG::ErrorCode::Success);
    BOOST_REQUIRE(duplicates->BuildIndex(duplicateVectors.data(), duplicateCount + 1, kDimension, true, false) ==
        SPTAG::ErrorCode::Success);
    SPTAG::QueryResult duplicatePlain(duplicateVectors.data(), 16, false);
    SPTAG::QueryResult duplicateAdmitted(duplicateVectors.data(), 16, false);
    BOOST_REQUIRE(duplicates->SearchIndexWithMaxCheck(duplicatePlain, 64) == SPTAG::ErrorCode::Success);
    BOOST_REQUIRE(duplicates->SearchIndexWithResultFilter(duplicateAdmitted,
        [](SPTAG::SizeType) { return true; }, 64) == SPTAG::ErrorCode::Success);
    BOOST_CHECK_EQUAL(duplicatePlain.GetScanned(), duplicateAdmitted.GetScanned());
    for (int rank = 0; rank < 16; ++rank)
        BOOST_CHECK_EQUAL(duplicatePlain.GetResult(rank)->VID, duplicateAdmitted.GetResult(rank)->VID);
    SPTAG::SizeType center = -1;
    const auto& duplicateGraph = duplicates->GetGraph();
    for (SPTAG::SizeType id = 0; id < duplicateCount; ++id)
        if (duplicateGraph[id][duplicates->GetNeighborhoodSize() - 1] < -1)
            center = id;
    BOOST_REQUIRE_GE(center, 0);
    const SPTAG::SizeType survivingAlias = center == duplicateCount - 1
        ? duplicateCount - 2 : duplicateCount - 1;
    BOOST_REQUIRE(duplicates->DeleteIndex(center) == SPTAG::ErrorCode::Success);
    SPTAG::QueryResult alias(duplicateVectors.data(), 1, false);
    SPTAG::QueryResult aliasPlain(duplicateVectors.data(), 1, false);
    BOOST_REQUIRE(duplicates->SearchIndexWithMaxCheck(aliasPlain, 64) == SPTAG::ErrorCode::Success);
    BOOST_REQUIRE(duplicates->SearchIndexWithResultFilter(alias,
        [center, survivingAlias](SPTAG::SizeType id) {
            return id == center || id == survivingAlias;
        }, 64) == SPTAG::ErrorCode::Success);
    BOOST_CHECK_EQUAL(alias.GetResult(0)->VID, survivingAlias);
    BOOST_CHECK_EQUAL(alias.GetScanned(), aliasPlain.GetScanned());
}

BOOST_AUTO_TEST_CASE(SPANNTest)
{
    Test<float>(SPTAG::IndexAlgoType::SPANN, "L2");
}

BOOST_AUTO_TEST_SUITE_END()
