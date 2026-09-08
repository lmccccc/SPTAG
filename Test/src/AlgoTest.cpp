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

BOOST_AUTO_TEST_CASE(BKTResultAdmissionFilter)
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

    auto index = SPTAG::VectorIndex::CreateInstance(
        SPTAG::IndexAlgoType::BKT, SPTAG::VectorValueType::Float);
    BOOST_REQUIRE(index != nullptr);
    index->SetParameter("NumberOfThreads", "1");
    index->SetParameter("MaxCheck", "4096");
    BOOST_REQUIRE(
        index->BuildIndex(
            vectors.data(), kVectorCount, kDimension) ==
        SPTAG::ErrorCode::Success);

    float target[kDimension];
    for (SPTAG::DimensionType col = 0; col < kDimension; ++col) {
        target[col] =
            static_cast<float>(
                (257 * (col + 5) + col * 29) % 263) /
            263.0f;
    }
    SPTAG::QueryResult native(target, k, false);
    SPTAG::QueryResult allAdmitted(target, k, false);
    BOOST_REQUIRE(
        index->SearchIndex(native) == SPTAG::ErrorCode::Success);
    BOOST_REQUIRE(
        index->SearchIndexWithResultFilter(
            allAdmitted,
            [](SPTAG::SizeType) { return true; },
            4096) == SPTAG::ErrorCode::Success);
    BOOST_CHECK_EQUAL(native.GetScanned(), allAdmitted.GetScanned());
    for (int rank = 0; rank < k; ++rank) {
        BOOST_REQUIRE(native.GetResult(rank) != nullptr);
        BOOST_REQUIRE(allAdmitted.GetResult(rank) != nullptr);
        BOOST_CHECK_EQUAL(
            native.GetResult(rank)->VID,
            allAdmitted.GetResult(rank)->VID);
        BOOST_CHECK_EQUAL(
            native.GetResult(rank)->Dist,
            allAdmitted.GetResult(rank)->Dist);
    }

    SPTAG::QueryResult sparse(target, k, false);
    BOOST_REQUIRE(
        index->SearchIndexWithResultFilter(
            sparse,
            [](SPTAG::SizeType id) { return id % 2 == 0; },
            4096) == SPTAG::ErrorCode::Success);
    bool reachedBeyondUnfilteredTopK = false;
    for (int rank = 0; rank < k; ++rank) {
        BOOST_REQUIRE(sparse.GetResult(rank) != nullptr);
        BOOST_CHECK_EQUAL(sparse.GetResult(rank)->VID % 2, 0);
        reachedBeyondUnfilteredTopK |= sparse.GetResult(rank)->VID >= 8;
    }
    BOOST_CHECK(reachedBeyondUnfilteredTopK);

    SPTAG::QueryResult rare(target, k, false);
    BOOST_REQUIRE(
        index->SearchIndexWithResultFilter(
            rare,
            [](SPTAG::SizeType id) {
                return id % 32 == 0;
            },
            256) == SPTAG::ErrorCode::Success);
    for (int rank = 0; rank < k; ++rank) {
        BOOST_REQUIRE(
            rare.GetResult(rank) != nullptr);
        BOOST_CHECK_GE(
            rare.GetResult(rank)->VID, 0);
        BOOST_CHECK_EQUAL(
            rare.GetResult(rank)->VID % 32, 0);
    }

    SPTAG::QueryResult noneAdmitted(target, k, false);
    BOOST_REQUIRE(
        index->SearchIndexWithResultFilter(
            noneAdmitted,
            [](SPTAG::SizeType) {
                return false;
            },
            32) == SPTAG::ErrorCode::Success);
    BOOST_CHECK_LT(
        noneAdmitted.GetScanned(), kVectorCount);
    for (int rank = 0; rank < k; ++rank) {
        BOOST_REQUIRE(
            noneAdmitted.GetResult(rank) !=
            nullptr);
        BOOST_CHECK_LT(
            noneAdmitted.GetResult(rank)->VID,
            0);
    }

    for (const char* bfs : {"0", "1"})
    {
        BOOST_REQUIRE(index->SetParameter("EnableBfs", bfs) == SPTAG::ErrorCode::Success);
        SPTAG::QueryResult plain(target, k, false), admitted(target, k, false);
        SPTAG::QueryResult empty(target, k, false), replay(target, k, false);
        std::vector<int> visits(kVectorCount, 0);
        BOOST_REQUIRE(index->SearchIndexWithMaxCheck(plain, 128) == SPTAG::ErrorCode::Success);
        BOOST_REQUIRE(index->SearchIndexWithTraversalFilter(admitted,
            [&](SPTAG::SizeType id) {
                BOOST_REQUIRE(id >= 0 && id < kVectorCount);
                ++visits[static_cast<size_t>(id)];
                return true;
            }, 128) == SPTAG::ErrorCode::Success);
        BOOST_CHECK_EQUAL(plain.GetScanned(), admitted.GetScanned());
        for (int visitsForID : visits) BOOST_CHECK_LE(visitsForID, 1);
        BOOST_REQUIRE(index->SearchIndexWithTraversalFilter(empty,
            [](SPTAG::SizeType) { return false; }, 128) == SPTAG::ErrorCode::Success);
        BOOST_REQUIRE(index->SearchIndexWithMaxCheck(replay, 128) == SPTAG::ErrorCode::Success);
        for (int rank = 0; rank < k; ++rank)
        {
            BOOST_CHECK_EQUAL(admitted.GetResult(rank)->VID, plain.GetResult(rank)->VID);
            BOOST_CHECK_EQUAL(admitted.GetResult(rank)->Dist, plain.GetResult(rank)->Dist);
            BOOST_CHECK_LT(empty.GetResult(rank)->VID, 0);
            BOOST_CHECK_EQUAL(replay.GetResult(rank)->VID, plain.GetResult(rank)->VID);
            BOOST_CHECK_EQUAL(replay.GetResult(rank)->Dist, plain.GetResult(rank)->Dist);
        }
    }

    BOOST_REQUIRE(index->SetParameter("EnableBfs", "0") == SPTAG::ErrorCode::Success);
    BOOST_REQUIRE(index->SetParameter("NumberOfInitialDynamicPivots", "1") == SPTAG::ErrorCode::Success);
    BOOST_REQUIRE(index->SetParameter("NumberOfOtherDynamicPivots", "1") == SPTAG::ErrorCode::Success);
    std::unordered_set<SPTAG::SizeType> initialSeeds;
    SPTAG::QueryResult rejectedSeeds(target, 1, false);
    BOOST_REQUIRE(index->SearchIndexWithTraversalFilter(rejectedSeeds,
        [&](SPTAG::SizeType id) { initialSeeds.insert(id); return false; },
        4096) == SPTAG::ErrorCode::Success);
    SPTAG::SizeType reachable = kVectorCount - 1;
    while (reachable >= 0 && initialSeeds.count(reachable) != 0) --reachable;
    BOOST_REQUIRE_GE(reachable, 0);
    const auto onlyReachable = [reachable](SPTAG::SizeType id) { return id == reachable; };
    SPTAG::QueryResult bridgePreserved(target, 1, false), bridgePruned(target, 1, false);
    BOOST_REQUIRE(index->SearchIndexWithResultFilter(bridgePreserved,
        onlyReachable, 4096) == SPTAG::ErrorCode::Success);
    BOOST_REQUIRE(index->SearchIndexWithTraversalFilter(bridgePruned,
        onlyReachable, 4096) == SPTAG::ErrorCode::Success);
    BOOST_CHECK_EQUAL(bridgePreserved.GetResult(0)->VID, reachable);
    BOOST_CHECK_LT(bridgePruned.GetResult(0)->VID, 0);

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
    BOOST_REQUIRE(duplicates->SearchIndexWithTraversalFilter(duplicateAdmitted,
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
    BOOST_REQUIRE(duplicates->SearchIndexWithTraversalFilter(alias,
        [center, survivingAlias](SPTAG::SizeType id) {
            return id == center || id == survivingAlias;
        }, 64) == SPTAG::ErrorCode::Success);
    BOOST_CHECK_EQUAL(alias.GetResult(0)->VID, survivingAlias);
}

BOOST_AUTO_TEST_CASE(SPANNTest)
{
    Test<float>(SPTAG::IndexAlgoType::SPANN, "L2");
}

BOOST_AUTO_TEST_SUITE_END()
