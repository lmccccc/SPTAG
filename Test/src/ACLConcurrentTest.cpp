// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/CoreInterface.h"
#include "inc/Helper/AtomicFile.h"
#include "inc/Helper/HeadCrossEdges.h"
#include "inc/Test.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#include <direct.h>
#include <process.h>
#include <sys/stat.h>
#define STAT_STRUCT struct _stat
#define STAT_CALL _stat
#else
#include <sys/stat.h>
#include <unistd.h>
#define STAT_STRUCT struct stat
#define STAT_CALL stat
#endif

namespace {

std::string MakeTempDir()
{
#ifdef _WIN32
    char basePath[MAX_PATH];
    DWORD baseLen = GetTempPathA(MAX_PATH, basePath);
    BOOST_REQUIRE(baseLen > 0 && baseLen < MAX_PATH);

    std::string dir = std::string(basePath) + "sptag_acl_concurrent_" +
                      std::to_string(_getpid()) + "_" +
                      std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
    int mkret = _mkdir(dir.c_str());
    BOOST_REQUIRE(mkret == 0);
    return dir;
#else
    char dirTemplate[] = "/tmp/sptag_acl_concurrent_XXXXXX";
    char* dir = mkdtemp(dirTemplate);
    BOOST_REQUIRE(dir != nullptr);
    return std::string(dir);
#endif
}

bool PathExists(const std::string& path)
{
    STAT_STRUCT st;
    return STAT_CALL(path.c_str(), &st) == 0;
}

void RemoveTree(const std::string& path)
{
    if (path.empty()) {
        return;
    }

#ifdef _WIN32
    std::string cmd = "rmdir /s /q \"" + path + "\"";
#else
    std::string cmd = "rm -rf \"" + path + "\"";
#endif
    std::ignore = std::system(cmd.c_str());
}

struct ScopedTempDir {
    explicit ScopedTempDir(std::string p_path)
        : path(std::move(p_path))
    {
    }

    ~ScopedTempDir()
    {
        RemoveTree(path);
    }

    std::string path;
};

std::vector<int> ExtractValidIds(const std::shared_ptr<QueryResult>& result)
{
    std::vector<int> ids;
    if (result == nullptr) {
        return ids;
    }

    for (int i = 0; i < result->GetResultNum(); ++i)
    {
        auto* entry = result->GetResult(i);
        if (entry != nullptr && entry->VID >= 0)
        {
            ids.push_back(static_cast<int>(entry->VID));
        }
    }
    return ids;
}

void FillNormalizedVectors(std::vector<float>& vectors, int numVectors, int dimension)
{
    std::mt19937 rng(42);
    std::normal_distribution<float> dist(0.0f, 1.0f);

    for (int vectorId = 0; vectorId < numVectors; ++vectorId)
    {
        float normSq = 0.0f;
        for (int dim = 0; dim < dimension; ++dim)
        {
            float value = dist(rng);
            vectors[static_cast<size_t>(vectorId) * static_cast<size_t>(dimension) + static_cast<size_t>(dim)] = value;
            normSq += value * value;
        }

        float invNorm = 1.0f / std::sqrt(std::max(normSq, 1e-6f));
        for (int dim = 0; dim < dimension; ++dim)
        {
            vectors[static_cast<size_t>(vectorId) * static_cast<size_t>(dimension) + static_cast<size_t>(dim)] *= invNorm;
        }
    }
}

} // namespace

BOOST_AUTO_TEST_SUITE(ACLConcurrentTest)

BOOST_AUTO_TEST_CASE(SearchWithACLSameTenantThreadLocalState)
{
    constexpr int kDim = 16;
    constexpr int kNumVectors = 256;
    constexpr int kNumTagsPerVec = 4;
    constexpr int kResultNum = 10;
    constexpr int kIterationsPerThread = 20;

    std::vector<float> vectors(static_cast<size_t>(kNumVectors) * static_cast<size_t>(kDim));
    FillNormalizedVectors(vectors, kNumVectors, kDim);

    std::vector<uint32_t> tags(static_cast<size_t>(kNumVectors) * static_cast<size_t>(kNumTagsPerVec));
    for (int i = 0; i < kNumVectors; ++i)
    {
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 0] = static_cast<uint32_t>(i % 2);
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 1] = static_cast<uint32_t>((i / 2) % 4);
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 2] = static_cast<uint32_t>((i / 4) % 8);
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 3] = static_cast<uint32_t>(i % 32);
    }

    std::string metadata;
    metadata.reserve(static_cast<size_t>(kNumVectors) * 8);
    for (int i = 0; i < kNumVectors; ++i)
    {
        metadata += "tenant0\n";
    }

    TenantIndexManager builder(kDim, "SPANN", "Float");
    BOOST_REQUIRE(builder.BuildFromDataWithTags(
        ByteArray(reinterpret_cast<std::uint8_t*>(vectors.data()), vectors.size() * sizeof(float), false),
        ByteArray(reinterpret_cast<std::uint8_t*>(metadata.data()), metadata.size(), false),
        kNumVectors,
        ByteArray(reinterpret_cast<std::uint8_t*>(tags.data()), tags.size() * sizeof(uint32_t), false),
        kNumTagsPerVec,
        false,
        true));

    ScopedTempDir saveDir(MakeTempDir());
    BOOST_REQUIRE(builder.SaveAll(saveDir.path.c_str()));

    TenantIndexManager loaded(kDim, "SPANN", "Float");
    BOOST_REQUIRE(loaded.LoadAll(saveDir.path.c_str()));

    const int tenantId = loaded.GetInternalTenantId("tenant0");
    BOOST_REQUIRE_EQUAL(tenantId, 0);

    BOOST_CHECK(PathExists(saveDir.path + "/tenant_" + std::to_string(tenantId) + "/HeadIndex/head_bundle_manifest.bin"));

    const float* queryA = vectors.data();
    const float* queryB = vectors.data() + static_cast<size_t>(127) * static_cast<size_t>(kDim);
    std::vector<uint32_t> queryTagsA = {tags[0], tags[2]};
    std::vector<uint32_t> queryTagsB = {
        tags[static_cast<size_t>(127) * static_cast<size_t>(kNumTagsPerVec) + 1],
        tags[static_cast<size_t>(127) * static_cast<size_t>(kNumTagsPerVec) + 3]
    };

    auto baselineA = loaded.SearchWithACL(
        ByteArray(reinterpret_cast<std::uint8_t*>(const_cast<float*>(queryA)), kDim * sizeof(float), false),
        tenantId,
        kResultNum,
        ByteArray(reinterpret_cast<std::uint8_t*>(queryTagsA.data()), queryTagsA.size() * sizeof(uint32_t), false),
        static_cast<int>(queryTagsA.size()));
    auto baselineB = loaded.SearchWithACL(
        ByteArray(reinterpret_cast<std::uint8_t*>(const_cast<float*>(queryB)), kDim * sizeof(float), false),
        tenantId,
        kResultNum,
        ByteArray(reinterpret_cast<std::uint8_t*>(queryTagsB.data()), queryTagsB.size() * sizeof(uint32_t), false),
        static_cast<int>(queryTagsB.size()));

    std::vector<int> expectedA = ExtractValidIds(baselineA);
    std::vector<int> expectedB = ExtractValidIds(baselineB);
    BOOST_REQUIRE(!expectedA.empty());
    BOOST_REQUIRE(!expectedB.empty());
    BOOST_REQUIRE_NE(expectedA.front(), expectedB.front());

    std::mutex startMutex;
    std::condition_variable startCv;
    int readyThreads = 0;
    bool startSearch = false;

    std::mutex errorMutex;
    std::vector<std::string> failures;

    auto worker = [&](const char* name,
                      const float* query,
                      const std::vector<uint32_t>& queryTags,
                      int expectedTop1) {
        {
            std::unique_lock<std::mutex> lock(startMutex);
            ++readyThreads;
            startCv.notify_all();
            startCv.wait(lock, [&] { return startSearch; });
        }

        for (int iter = 0; iter < kIterationsPerThread; ++iter)
        {
            auto result = loaded.SearchWithACL(
                ByteArray(reinterpret_cast<std::uint8_t*>(const_cast<float*>(query)), kDim * sizeof(float), false),
                tenantId,
                kResultNum,
                ByteArray(reinterpret_cast<std::uint8_t*>(const_cast<uint32_t*>(queryTags.data())), queryTags.size() * sizeof(uint32_t), false),
                static_cast<int>(queryTags.size()));

            std::vector<int> ids = ExtractValidIds(result);
            if (ids.empty())
            {
                std::lock_guard<std::mutex> guard(errorMutex);
                failures.emplace_back(std::string(name) + ": empty result at iteration " + std::to_string(iter));
                return;
            }

            if (ids.front() != expectedTop1)
            {
                std::lock_guard<std::mutex> guard(errorMutex);
                failures.emplace_back(std::string(name) + ": top1=" + std::to_string(ids.front()) +
                                      " expected=" + std::to_string(expectedTop1) +
                                      " at iteration " + std::to_string(iter));
                return;
            }
        }
    };

    std::thread threadA(worker, "A", queryA, std::cref(queryTagsA), expectedA.front());
    std::thread threadB(worker, "B", queryB, std::cref(queryTagsB), expectedB.front());

    {
        std::unique_lock<std::mutex> lock(startMutex);
        startCv.wait(lock, [&] { return readyThreads == 2; });
        startSearch = true;
        startCv.notify_all();
    }

    threadA.join();
    threadB.join();

    if (!failures.empty())
    {
        BOOST_FAIL(failures.front());
    }

    BOOST_CHECK(failures.empty());
}

BOOST_AUTO_TEST_CASE(HeadMetadataV8WithoutTailRoundTrips)
{
    constexpr int kDim = 8;
    constexpr int kNumVectors = 128;
    constexpr int kNumTagsPerVec = 4;

    std::vector<float> vectors(
        static_cast<size_t>(kNumVectors) *
        static_cast<size_t>(kDim));
    FillNormalizedVectors(vectors, kNumVectors, kDim);

    std::vector<uint32_t> tags(
        static_cast<size_t>(kNumVectors) *
        static_cast<size_t>(kNumTagsPerVec));
    for (int i = 0; i < kNumVectors; ++i)
    {
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 0] =
            static_cast<uint32_t>(i % 2);
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 1] =
            static_cast<uint32_t>((i / 2) % 4);
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 2] =
            static_cast<uint32_t>((i / 4) % 8);
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 3] =
            static_cast<uint32_t>(i % 32);
    }

    std::string metadata;
    for (int i = 0; i < kNumVectors; ++i)
    {
        metadata += "tenant0\n";
    }

    TenantIndexManager builder(kDim, "SPANN", "Float");
    builder.SetStorageBackend("STATIC");
    builder.SetBuildParam("DistCalcMethod", "L2", "Base");
    builder.SetBuildParam("IndexAlgoType", "BKT", "Base");
    builder.SetBuildParam("SelectHeadType", "BKT", "SelectHead");
    builder.SetBuildParam("Ratio", "0.25", "SelectHead");
    builder.SetBuildParam("BKTLambdaFactor", "-1", "SelectHead");
    builder.SetBuildParam("NumberOfThreads", "1", "SelectHead");
    builder.SetBuildParam("NumberOfThreads", "1", "BuildHead");
    builder.SetBuildParam("NeighborhoodSize", "32", "BuildHead");
    builder.SetBuildParam("RefineIterations", "3", "BuildHead");
    builder.SetBuildParam("BKTLambdaFactor", "-1", "BuildHead");
    builder.SetSSDBuildParam("InternalResultNum", "16");
    builder.SetSSDBuildParam("SearchInternalResultNum", "16");
    builder.SetSSDBuildParam("NumberOfThreads", "1");
    builder.SetSSDBuildParam("PostingPageLimit", "2");
    builder.SetSSDBuildParam("SearchPostingPageLimit", "2");
    builder.SetSSDBuildParam("ReplicaCount", "2");
    builder.SetSSDBuildParam("ExcludeHead", "true");
    builder.SetSSDBuildParam("StaticACLTagCols", "4");
    builder.SetSSDBuildParam("EnableHierPostingFilter", "true");

    ByteArray vectorBytes(
        reinterpret_cast<std::uint8_t*>(vectors.data()),
        vectors.size() * sizeof(float), false);
    ByteArray metadataBytes(
        reinterpret_cast<std::uint8_t*>(metadata.data()),
        metadata.size(), false);
    ByteArray tagBytes(
        reinterpret_cast<std::uint8_t*>(tags.data()),
        tags.size() * sizeof(uint32_t), false);
    BOOST_REQUIRE(builder.BuildFromDataWithTags(
        vectorBytes, metadataBytes, kNumVectors,
        tagBytes, kNumTagsPerVec, false, true));
    BOOST_REQUIRE(builder.BuildSignaturesWithVectors(
        0, tagBytes, kNumVectors, kNumTagsPerVec,
        vectorBytes));

    ScopedTempDir saveDir(MakeTempDir());
    BOOST_REQUIRE(builder.SaveAll(saveDir.path.c_str()));
    const std::string headMetadataPath =
        saveDir.path +
        "/tenant_0/HeadIndex/head_node_meta.bin";
    {
        std::ifstream input(headMetadataPath, std::ios::binary);
        BOOST_REQUIRE(input.good());
        std::array<std::int32_t, 4> header{};
        input.read(
            reinterpret_cast<char*>(header.data()),
            static_cast<std::streamsize>(sizeof(header)));
        BOOST_REQUIRE(input.good());
        BOOST_CHECK_EQUAL(header[0], 8);
        input.seekg(
            static_cast<std::streamoff>(
                5 * sizeof(std::int32_t)),
            std::ios::cur);
        std::uint32_t flags = 0;
        input.read(
            reinterpret_cast<char*>(&flags),
            static_cast<std::streamsize>(sizeof(flags)));
        BOOST_REQUIRE(input.good());
        BOOST_CHECK_EQUAL(flags, 0x3U);
    }

    TenantIndexManager loaded(kDim, "SPANN", "Float");
    BOOST_REQUIRE(loaded.LoadAll(saveDir.path.c_str()));
    BOOST_REQUIRE_EQUAL(
        loaded.GetInternalTenantId("tenant0"), 0);
}

BOOST_AUTO_TEST_CASE(HybridTagRoutingStatsPersistRepairAndReload)
{
    constexpr int kDim = 8;
    constexpr int kNumVectors = 256;
    constexpr int kNumTagsPerVec = 5;
    constexpr int kResultNum = 5;

    std::vector<float> vectors(
        static_cast<size_t>(kNumVectors) *
        static_cast<size_t>(kDim));
    FillNormalizedVectors(vectors, kNumVectors, kDim);

    std::vector<uint32_t> tags(
        static_cast<size_t>(kNumVectors) *
        static_cast<size_t>(kNumTagsPerVec));
    for (int i = 0; i < kNumVectors; ++i)
    {
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 0] =
            1000U + static_cast<uint32_t>(i / 128);
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 1] =
            2000U + static_cast<uint32_t>(i / 32);
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 2] =
            3000U + static_cast<uint32_t>(i / 8);
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 3] =
            i == 8
                ? 3001U
                : (i < 2
                       ? 4000U
                       : 3000U +
                             static_cast<uint32_t>(i / 2));
        tags[static_cast<size_t>(i) * kNumTagsPerVec + 4] =
            0x80000000U +
            static_cast<uint32_t>(i);
    }

    std::string metadata;
    for (int i = 0; i < kNumVectors; ++i)
    {
        metadata += "tenant0\n";
    }

    TenantIndexManager builder(kDim, "SPANN", "Float");
    builder.SetStorageBackend("STATIC");
    builder.SetBuildParam("DistCalcMethod", "L2", "Base");
    builder.SetBuildParam("IndexAlgoType", "BKT", "Base");
    builder.SetBuildParam("SelectHeadType", "BKT", "SelectHead");
    builder.SetBuildParam("Ratio", "0.25", "SelectHead");
    builder.SetBuildParam("BKTLambdaFactor", "-1", "SelectHead");
    builder.SetBuildParam("NumberOfThreads", "1", "SelectHead");
    builder.SetBuildParam("NumberOfThreads", "1", "BuildHead");
    builder.SetBuildParam("NeighborhoodSize", "32", "BuildHead");
    builder.SetBuildParam("RefineIterations", "3", "BuildHead");
    builder.SetBuildParam("BKTLambdaFactor", "-1", "BuildHead");
    builder.SetSSDBuildParam("InternalResultNum", "16");
    builder.SetSSDBuildParam("SearchInternalResultNum", "128");
    builder.SetSSDBuildParam("NumberOfThreads", "2");
    builder.SetSSDBuildParam("PostingPageLimit", "2");
    builder.SetSSDBuildParam("SearchPostingPageLimit", "2");
    builder.SetSSDBuildParam("ReplicaCount", "3");
    builder.SetSSDBuildParam("TailReplicaCount", "2");
    builder.SetSSDBuildParam("EnableUnfilterTail", "true");
    builder.SetSSDBuildParam("UnfilterTailBufferLength", "-1");
    builder.SetSSDBuildParam("CrossEdges", "0");
    builder.SetSSDBuildParam("CrossExtraEdges", "4");
    builder.SetSSDBuildParam("ExcludeHead", "true");
    builder.SetSSDBuildParam("StaticACLTagCols", "4");
    builder.SetSSDBuildParam("EnableHybridDistance", "true");
    builder.SetSSDBuildParam("HybridVectorWeight", "1");
    builder.SetSSDBuildParam("HybridCategoricalCols", "0,1,2,3");
    builder.SetSSDBuildParam(
        "HybridCategoricalWeights", "8,16,32,64");
    builder.SetSSDBuildParam("HybridNumericCols", "4");
    builder.SetSSDBuildParam("HybridNumericWeights", "0.01");
    builder.SetSSDBuildParam("HybridGraphDegree", "16");
    builder.SetSSDBuildParam("HybridCandidateCount", "32");
    builder.SetSSDBuildParam(
        "EnableHierPostingFilter", "true");

    ByteArray vectorBytes(
        reinterpret_cast<std::uint8_t*>(vectors.data()),
        vectors.size() * sizeof(float), false);
    ByteArray metadataBytes(
        reinterpret_cast<std::uint8_t*>(metadata.data()),
        metadata.size(), false);
    ByteArray tagBytes(
        reinterpret_cast<std::uint8_t*>(tags.data()),
        tags.size() * sizeof(uint32_t), false);
    BOOST_REQUIRE(builder.BuildFromDataWithTags(
        vectorBytes, metadataBytes, kNumVectors,
        tagBytes, kNumTagsPerVec, false, true));
    BOOST_REQUIRE(builder.BuildSignatures(
        0, tagBytes, kNumVectors, kNumTagsPerVec));

    ScopedTempDir saveDir(MakeTempDir());
    BOOST_REQUIRE(builder.SaveAll(saveDir.path.c_str()));
    const std::string tenantDir =
        saveDir.path + "/tenant_0";
    const std::string routeStats =
        tenantDir + "/tag_routing_stats.bin";
    BOOST_REQUIRE(PathExists(routeStats));
    BOOST_REQUIRE(PathExists(
        tenantDir +
        "/SPTAGFullList.bin.hybrid.stats"));
    BOOST_CHECK(!PathExists(
        tenantDir + "/SPTAGHybridList.bin"));
    const std::string crossEdges =
        tenantDir + "/HeadIndex/" +
        SPTAG::Helper::kHeadCrossEdgesFileName;
    BOOST_REQUIRE(PathExists(crossEdges));
    {
        std::ifstream input(crossEdges, std::ios::binary);
        BOOST_REQUIRE(input.good());
        SPTAG::Helper::HeadCrossEdgesHeader header{};
        input.read(
            reinterpret_cast<char*>(&header),
            sizeof(header));
        BOOST_REQUIRE(input.good());
        BOOST_CHECK_EQUAL(
            header.version,
            SPTAG::Helper::
                kHybridHeadCrossEdgesVersion);
        BOOST_CHECK_EQUAL(
            header.maxEdgesPerHead, 16);
        BOOST_CHECK_EQUAL(
            header.reserved,
            SPTAG::Helper::kHybridHeadCrossEdgesMarker);
        SPTAG::Helper::
            HybridHeadCrossEdgesExtension extension{};
        input.read(
            reinterpret_cast<char*>(&extension),
            sizeof(extension));
        BOOST_REQUIRE(input.good());
        BOOST_CHECK_NE(
            extension.generationFingerprint, 0);
        BOOST_CHECK_NE(
            extension.contentFingerprint, 0);
    }
    {
        struct RouteRecord {
            std::uint32_t column;
            std::uint32_t tag;
            std::int32_t vectorCount;
            std::int32_t postingCount;
        };
        const ByteArray routeBlob =
            builder.GetColumnAwareTagRoutingStatsBlob(0);
        BOOST_REQUIRE_EQUAL(
            routeBlob.Length() %
                sizeof(RouteRecord),
            0);
        BOOST_CHECK_EQUAL(
            routeBlob.Length() /
                sizeof(RouteRecord),
            170);
        bool foundColumn2 = false;
        bool foundColumn3 = false;
        const auto* records =
            reinterpret_cast<const RouteRecord*>(
                routeBlob.Data());
        for (size_t index = 0;
             index <
             routeBlob.Length() /
                 sizeof(RouteRecord);
             ++index) {
            BOOST_CHECK_LT(records[index].column, 4);
            if (records[index].tag == 3001U &&
                records[index].column == 2) {
                foundColumn2 = true;
                BOOST_CHECK_EQUAL(
                    records[index].vectorCount, 8);
            }
            if (records[index].tag == 3001U &&
                records[index].column == 3) {
                foundColumn3 = true;
                BOOST_CHECK_EQUAL(
                    records[index].vectorCount, 3);
            }
        }
        BOOST_CHECK(foundColumn2);
        BOOST_CHECK(foundColumn3);

        struct LegacyRouteRecord {
            std::uint32_t tag;
            std::int32_t vectorCount;
            std::int32_t postingCount;
        };
        const ByteArray legacyBlob =
            builder.GetTagRoutingStatsBlob(0);
        BOOST_REQUIRE_EQUAL(
            legacyBlob.Length() %
                sizeof(LegacyRouteRecord),
            0);
        const auto* legacyRecords =
            reinterpret_cast<const LegacyRouteRecord*>(
                legacyBlob.Data());
        bool foundLegacy3001 = false;
        for (size_t index = 0;
             index <
             legacyBlob.Length() /
                 sizeof(LegacyRouteRecord);
             ++index) {
            if (legacyRecords[index].tag == 3001U) {
                foundLegacy3001 = true;
                BOOST_CHECK_EQUAL(
                    legacyRecords[index].vectorCount,
                    10);
            }
        }
        BOOST_CHECK(foundLegacy3001);
    }

    const auto filteredSearch =
        [&](TenantIndexManager& manager) {
            const std::uint32_t queryTag = tags[3];
            return manager.SearchWithACL(
                ByteArray(
                    reinterpret_cast<std::uint8_t*>(
                        vectors.data()),
                    kDim * sizeof(float), false),
                0, kResultNum,
                ByteArray(
                    reinterpret_cast<std::uint8_t*>(
                        const_cast<std::uint32_t*>(
                            &queryTag)),
                    sizeof(queryTag), false),
                1);
        };

    TenantIndexManager loaded(kDim, "SPANN", "Float");
    BOOST_REQUIRE(loaded.LoadAll(saveDir.path.c_str()));
    BOOST_CHECK_GT(
        loaded.GetColumnAwareTagRoutingStatsBlob(0).Length(), 0);
    BOOST_REQUIRE(filteredSearch(loaded) != nullptr);

    const std::vector<std::uint32_t> mixedOrDNF = {
        0x444E4633U, 2,
        1, 0, 3, SPTAG::Cache::DNF_EQ, 4000U,
        1, 1, 4, SPTAG::Cache::DNF_GE,
        tags[static_cast<size_t>(200) *
             kNumTagsPerVec + 4]};
    auto mixedResult = loaded.SearchWithACL(
        ByteArray(
            reinterpret_cast<std::uint8_t*>(
                vectors.data() +
                static_cast<size_t>(200) *
                    kDim),
            kDim * sizeof(float), false),
        0, kResultNum,
        ByteArray(
            reinterpret_cast<std::uint8_t*>(
                const_cast<std::uint32_t*>(
                    mixedOrDNF.data())),
            mixedOrDNF.size() *
                sizeof(std::uint32_t),
            false),
        -1);
    BOOST_REQUIRE(mixedResult != nullptr);
    const auto mixedIds =
        ExtractValidIds(mixedResult);
    BOOST_CHECK(
        std::find(
            mixedIds.begin(), mixedIds.end(),
            200) != mixedIds.end());
    for (int vectorId : mixedIds) {
        const bool categoricalMatch =
            tags[static_cast<size_t>(vectorId) *
                     kNumTagsPerVec +
                 3] == 4000U;
        const bool numericMatch =
            tags[static_cast<size_t>(vectorId) *
                     kNumTagsPerVec +
                 4] >=
            tags[static_cast<size_t>(200) *
                     kNumTagsPerVec +
                 4];
        BOOST_CHECK(
            categoricalMatch ||
            numericMatch);
    }

    const std::vector<std::uint32_t> columnDNF = {
        0x444E4633U, 1,
        1, 0, 3, SPTAG::Cache::DNF_EQ, 3001U};
    auto columnResult = loaded.SearchWithACL(
        ByteArray(
            reinterpret_cast<std::uint8_t*>(
                vectors.data() +
                static_cast<size_t>(2) * kDim),
            kDim * sizeof(float), false),
        0, kResultNum,
        ByteArray(
            reinterpret_cast<std::uint8_t*>(
                const_cast<std::uint32_t*>(
                    columnDNF.data())),
            columnDNF.size() *
                sizeof(std::uint32_t),
            false),
        -1);
    BOOST_REQUIRE(columnResult != nullptr);
    const auto columnIds =
        ExtractValidIds(columnResult);
    BOOST_REQUIRE(
        std::find(
            columnIds.begin(), columnIds.end(),
            2) != columnIds.end());
    for (int vectorId : columnIds) {
        BOOST_CHECK_EQUAL(
            tags[static_cast<size_t>(vectorId) *
                     kNumTagsPerVec +
                 3],
            3001U);
    }

    const auto malformedDNFSearch =
        [&](const std::vector<std::uint32_t>& blob) {
            return loaded.SearchWithACL(
                ByteArray(
                    reinterpret_cast<std::uint8_t*>(
                        vectors.data()),
                    kDim * sizeof(float), false),
                0, kResultNum,
                ByteArray(
                    reinterpret_cast<std::uint8_t*>(
                        const_cast<std::uint32_t*>(
                            blob.data())),
                    blob.size() *
                        sizeof(std::uint32_t),
                    false),
                -1);
        };
    BOOST_CHECK(malformedDNFSearch({
        0x444E4633U, 1,
        1, 0, 3, SPTAG::Cache::DNF_EQ}) == nullptr);
    BOOST_CHECK(malformedDNFSearch({
        0x444E4633U, 1,
        1, 0, 3, SPTAG::Cache::DNF_EQ, 3001U,
        99U}) == nullptr);
    BOOST_CHECK(malformedDNFSearch({
        0x444E4633U, 1,
        1, 2, 3, SPTAG::Cache::DNF_EQ, 3001U}) ==
        nullptr);
    BOOST_CHECK(malformedDNFSearch({
        0x444E4633U, 1,
        1, 0, 3, 99U, 3001U}) == nullptr);
    BOOST_CHECK(malformedDNFSearch({
        0x444E4633U, 1,
        1, 0, 5, SPTAG::Cache::DNF_EQ, 3001U}) ==
        nullptr);
    BOOST_CHECK(malformedDNFSearch({
        0x444E4633U, 1,
        1, 1, 3, SPTAG::Cache::DNF_EQ, 3001U}) ==
        nullptr);
    BOOST_CHECK(malformedDNFSearch({
        0x444E4633U, 1, 0}) == nullptr);
    const std::vector<std::uint32_t> v2NumericDNF = {
        0x444E4632U, 1,
        1, 4, SPTAG::Cache::DNF_GE,
        tags[4]};
    BOOST_REQUIRE(
        malformedDNFSearch(v2NumericDNF) != nullptr);
    const std::vector<std::uint8_t> shortFlatTag(
        sizeof(std::uint32_t) - 1, 0);
    BOOST_CHECK(
        loaded.SearchWithACL(
            ByteArray(
                reinterpret_cast<std::uint8_t*>(
                    vectors.data()),
                kDim * sizeof(float), false),
            0, kResultNum,
            ByteArray(
                const_cast<std::uint8_t*>(
                    shortFlatTag.data()),
                shortFlatTag.size(), false),
            1) == nullptr);

    const std::string headMetadata =
        tenantDir +
        "/HeadIndex/head_node_meta.bin";
    BOOST_REQUIRE(
        std::filesystem::exists(headMetadata));
    std::vector<std::uint8_t> validHeadMetadata;
    {
        std::ifstream input(
            headMetadata, std::ios::binary);
        BOOST_REQUIRE(input.good());
        input.seekg(0, std::ios::end);
        const std::streamoff bytes = input.tellg();
        BOOST_REQUIRE_GT(bytes, 64);
        validHeadMetadata.resize(
            static_cast<size_t>(bytes));
        input.seekg(0, std::ios::beg);
        input.read(
            reinterpret_cast<char*>(
                validHeadMetadata.data()),
            bytes);
        BOOST_REQUIRE(input.good());
        std::int32_t version = 0;
        std::memcpy(
            &version,
            validHeadMetadata.data(),
            sizeof(version));
        BOOST_CHECK_EQUAL(version, 8);
        std::uint32_t flags = 0;
        std::memcpy(
            &flags,
            validHeadMetadata.data() + 36,
            sizeof(flags));
        BOOST_CHECK_EQUAL(flags, 7U);
        std::uint64_t generation = 0;
        std::uint64_t contentFingerprint = 0;
        std::memcpy(
            &generation,
            validHeadMetadata.data() + 48,
            sizeof(generation));
        std::memcpy(
            &contentFingerprint,
            validHeadMetadata.data() + 56,
            sizeof(contentFingerprint));
        BOOST_CHECK_NE(generation, 0U);
        BOOST_CHECK_NE(contentFingerprint, 0U);
    }
    const auto writeHeadMetadata =
        [&](const std::vector<std::uint8_t>& bytes) {
            std::ofstream output(
                headMetadata,
                std::ios::binary |
                    std::ios::trunc);
            BOOST_REQUIRE(output.good());
            output.write(
                reinterpret_cast<const char*>(
                    bytes.data()),
                static_cast<std::streamsize>(
                    bytes.size()));
            BOOST_REQUIRE(output.good());
        };
    const auto requireFilteredResults =
        [&](TenantIndexManager& manager) {
            auto result = filteredSearch(manager);
            BOOST_REQUIRE(result != nullptr);
            BOOST_CHECK(
                !ExtractValidIds(result).empty());
        };

    auto corruptedHeadBody = validHeadMetadata;
    corruptedHeadBody.back() ^= 0x5aU;
    writeHeadMetadata(corruptedHeadBody);
    TenantIndexManager bodyFallback(
        kDim, "SPANN", "Float");
    BOOST_REQUIRE(
        bodyFallback.LoadAll(
            saveDir.path.c_str()));
    requireFilteredResults(bodyFallback);
    writeHeadMetadata(validHeadMetadata);

    auto wrongHeadGeneration = validHeadMetadata;
    constexpr size_t kHeadMetaGenerationOffset = 48;
    constexpr size_t kHeadMetaContentFingerprintOffset = 56;
    constexpr size_t kHeadMetaBlobOffset = 64;
    BOOST_REQUIRE_GE(
        wrongHeadGeneration.size(),
        kHeadMetaBlobOffset);
    wrongHeadGeneration[
        kHeadMetaGenerationOffset] ^= 1U;
    std::uint64_t replacementFingerprint =
        1469598103934665603ULL;
    const auto updateFingerprint =
        [&replacementFingerprint](
            const std::uint8_t* bytes,
            size_t count) {
            constexpr std::uint64_t prime =
                1099511628211ULL;
            for (size_t index = 0;
                 index < count; ++index) {
                replacementFingerprint ^=
                    bytes[index];
                replacementFingerprint *= prime;
            }
        };
    updateFingerprint(
        wrongHeadGeneration.data(),
        kHeadMetaContentFingerprintOffset);
    updateFingerprint(
        wrongHeadGeneration.data() +
            kHeadMetaBlobOffset,
        wrongHeadGeneration.size() -
            kHeadMetaBlobOffset);
    std::memcpy(
        wrongHeadGeneration.data() +
            kHeadMetaContentFingerprintOffset,
        &replacementFingerprint,
        sizeof(replacementFingerprint));
    writeHeadMetadata(wrongHeadGeneration);
    TenantIndexManager generationFallback(
        kDim, "SPANN", "Float");
    BOOST_REQUIRE(
        generationFallback.LoadAll(
            saveDir.path.c_str()));
    requireFilteredResults(
        generationFallback);
    writeHeadMetadata(validHeadMetadata);

    {
        FILE* file = std::fopen(routeStats.c_str(), "r+b");
        BOOST_REQUIRE(file != nullptr);
        BOOST_REQUIRE(std::fseek(file, 16, SEEK_SET) == 0);
        const std::uint64_t staleGeneration =
            0x123456789abcdef0ULL;
        BOOST_REQUIRE(
            std::fwrite(
                &staleGeneration,
                sizeof(staleGeneration), 1, file) == 1);
        BOOST_REQUIRE(std::fclose(file) == 0);
    }
    TenantIndexManager stale(kDim, "SPANN", "Float");
    BOOST_REQUIRE(stale.LoadAll(saveDir.path.c_str()));
    BOOST_CHECK(filteredSearch(stale) == nullptr);
    BOOST_REQUIRE(stale.BuildSignatures(
        0, tagBytes, kNumVectors, kNumTagsPerVec));
    BOOST_REQUIRE(filteredSearch(stale) != nullptr);

    BOOST_REQUIRE(std::remove(routeStats.c_str()) == 0);
    TenantIndexManager missing(kDim, "SPANN", "Float");
    BOOST_REQUIRE(missing.LoadAll(saveDir.path.c_str()));
    BOOST_CHECK(filteredSearch(missing) == nullptr);
    BOOST_REQUIRE(missing.BuildSignatures(
        0, tagBytes, kNumVectors, kNumTagsPerVec));
    BOOST_REQUIRE(filteredSearch(missing) != nullptr);

    {
        FILE* file = std::fopen(
            routeStats.c_str(), "r+b");
        BOOST_REQUIRE(file != nullptr);
        BOOST_REQUIRE(
            std::fseek(file, 12, SEEK_SET) == 0);
        const std::int32_t impossibleRecordCount =
            (std::numeric_limits<
                 std::int32_t>::max)();
        BOOST_REQUIRE(
            std::fwrite(
                &impossibleRecordCount,
                sizeof(impossibleRecordCount), 1,
                file) == 1);
        BOOST_REQUIRE(std::fclose(file) == 0);
    }
    TenantIndexManager corrupt(kDim, "SPANN", "Float");
    BOOST_REQUIRE(
        corrupt.LoadAll(saveDir.path.c_str()));
    BOOST_CHECK(filteredSearch(corrupt) == nullptr);
    BOOST_REQUIRE(corrupt.BuildSignatures(
        0, tagBytes, kNumVectors,
        kNumTagsPerVec));
    BOOST_REQUIRE(filteredSearch(corrupt) != nullptr);

    {
        FILE* file = std::fopen(
            headMetadata.c_str(), "r+b");
        BOOST_REQUIRE(file != nullptr);
        BOOST_REQUIRE(
            std::fseek(
                file,
                static_cast<long>(
                    sizeof(std::int32_t) * 2),
                SEEK_SET) == 0);
        const std::int32_t impossibleNumericColumns =
            (std::numeric_limits<
                 std::int32_t>::max)();
        BOOST_REQUIRE(
            std::fwrite(
                &impossibleNumericColumns,
                sizeof(impossibleNumericColumns),
                1, file) == 1);
        BOOST_REQUIRE(std::fclose(file) == 0);
    }
    TenantIndexManager forgedMetadata(
        kDim, "SPANN", "Float");
    BOOST_REQUIRE(
        forgedMetadata.LoadAll(
            saveDir.path.c_str()));
    BOOST_REQUIRE(
        forgedMetadata.BuildSignatures(
            0, tagBytes, kNumVectors,
            kNumTagsPerVec));
    BOOST_REQUIRE(
        filteredSearch(forgedMetadata) !=
        nullptr);

    std::filesystem::resize_file(
        headMetadata, 4);
    BOOST_REQUIRE(corrupt.BuildSignatures(
        0, tagBytes, kNumVectors,
        kNumTagsPerVec));
    BOOST_CHECK_GT(
        std::filesystem::file_size(
            headMetadata),
        4);
    TenantIndexManager repaired(
        kDim, "SPANN", "Float");
    BOOST_REQUIRE(
        repaired.LoadAll(saveDir.path.c_str()));
    BOOST_REQUIRE(
        filteredSearch(repaired) != nullptr);
}

BOOST_AUTO_TEST_CASE(ExtremeSparseTagRouteMergeAndReload)
{
    constexpr int kDim = 8;
    constexpr int kNumVectors = 256;
    constexpr int kNumAttributes = 2;
    constexpr int kResultNum = 5;
    constexpr int kSparseCount = 8;
    constexpr std::uint32_t kCommonTag = 10U;
    constexpr std::uint32_t kSparseTag = 99U;

    std::vector<float> vectors(
        static_cast<size_t>(kNumVectors) * kDim);
    FillNormalizedVectors(
        vectors, kNumVectors, kDim);
    std::vector<std::uint32_t> attributes(
        static_cast<size_t>(kNumVectors) *
        kNumAttributes);
    for (int vector = 0;
         vector < kNumVectors; ++vector)
    {
        attributes[
            static_cast<size_t>(vector) *
                kNumAttributes] =
            vector >= kNumVectors - kSparseCount
            ? kSparseTag
            : kCommonTag +
                static_cast<std::uint32_t>(
                    vector % 4);
        attributes[
            static_cast<size_t>(vector) *
                kNumAttributes +
            1] = static_cast<std::uint32_t>(vector);
    }
    attributes[
        static_cast<size_t>(7) *
            kNumAttributes] = kCommonTag;
    std::string metadata;
    for (int vector = 0;
         vector < kNumVectors; ++vector)
    {
        metadata += "tenant0\n";
    }

    TenantIndexManager builder(
        kDim, "SPANN", "Float");
    builder.SetStorageBackend("STATIC");
    builder.SetBuildParam(
        "DistCalcMethod", "L2", "Base");
    builder.SetBuildParam(
        "IndexAlgoType", "BKT", "Base");
    builder.SetBuildParam(
        "SSDIndex", "custom_static_postings.bin",
        "Base");
    builder.SetBuildParam(
        "SelectHeadType", "BKT", "SelectHead");
    builder.SetBuildParam(
        "Ratio", "0.25", "SelectHead");
    builder.SetBuildParam(
        "BKTLambdaFactor", "-1", "SelectHead");
    builder.SetBuildParam(
        "NumberOfThreads", "1", "SelectHead");
    builder.SetBuildParam(
        "NumberOfThreads", "1", "BuildHead");
    builder.SetBuildParam(
        "NeighborhoodSize", "32", "BuildHead");
    builder.SetBuildParam(
        "RefineIterations", "3", "BuildHead");
    builder.SetBuildParam(
        "BKTLambdaFactor", "-1", "BuildHead");
    builder.SetSSDBuildParam(
        "InternalResultNum", "32");
    builder.SetSSDBuildParam(
        "SearchInternalResultNum", "16");
    builder.SetSSDBuildParam(
        "NumberOfThreads", "2");
    builder.SetSSDBuildParam(
        "PostingPageLimit", "4");
    builder.SetSSDBuildParam(
        "SearchPostingPageLimit", "4");
    builder.SetSSDBuildParam(
        "ReplicaCount", "3");
    builder.SetSSDBuildParam(
        "TailReplicaCount", "2");
    builder.SetSSDBuildParam(
        "EnableUnfilterTail", "true");
    builder.SetSSDBuildParam(
        "UnfilterTailBufferLength", "-1");
    builder.SetSSDBuildParam("CrossEdges", "0");
    builder.SetSSDBuildParam(
        "ExcludeHead", "true");
    builder.SetSSDBuildParam(
        "StaticACLTagCols", "1");
    builder.SetSSDBuildParam(
        "EnableLimitedTagPosting", "true");
    builder.SetSSDBuildParam(
        "LimitedTagColumn", "0");
    builder.SetSSDBuildParam(
        "LimitedTagSlotsPerHead", "2");
    builder.SetSSDBuildParam(
        "LimitedTagVoteHeadCount", "2");
    builder.SetSSDBuildParam(
        "LimitedTagMinHeadCount", "1");
    builder.SetSSDBuildParam(
        "EnableExtremeSparseTag", "true");
    builder.SetSSDBuildParam(
        "ExtremeSparseTagMaxSelectivity", "0.04");
    builder.SetSSDBuildParam(
        "ExtremeSparseTagFile",
        "extreme_sparse_tags.bin");
    builder.SetSSDBuildParam(
        "LogExtremeSparseTagRoute", "true");
    builder.SetSSDBuildParam(
        "EnableHierPostingFilter", "false");

    ByteArray vectorBytes(
        reinterpret_cast<std::uint8_t*>(
            vectors.data()),
        vectors.size() * sizeof(float), false);
    ByteArray metadataBytes(
        reinterpret_cast<std::uint8_t*>(
            metadata.data()),
        metadata.size(), false);
    ByteArray attributeBytes(
        reinterpret_cast<std::uint8_t*>(
            attributes.data()),
        attributes.size() *
            sizeof(std::uint32_t),
        false);
    BOOST_REQUIRE(builder.BuildFromDataWithTags(
        vectorBytes, metadataBytes, kNumVectors,
        attributeBytes, kNumAttributes,
        false, true));
    BOOST_REQUIRE(builder.BuildSignaturesWithVectors(
        0, attributeBytes, kNumVectors,
        kNumAttributes, vectorBytes));
    auto staleAttributes = attributes;
    staleAttributes.back() = 10000U;
    ByteArray staleAttributeBytes(
        reinterpret_cast<std::uint8_t*>(
            staleAttributes.data()),
        staleAttributes.size() *
            sizeof(std::uint32_t),
        false);
    BOOST_REQUIRE(
        builder.BuildSignaturesWithVectors(
            0, staleAttributeBytes, kNumVectors,
            kNumAttributes, vectorBytes));
    BOOST_REQUIRE(
        builder.BuildSignaturesWithVectors(
            0, attributeBytes, kNumVectors,
            kNumAttributes, vectorBytes));

    ScopedTempDir saveDir(MakeTempDir());
    BOOST_REQUIRE(
        builder.SaveAll(saveDir.path.c_str()));
    BOOST_CHECK(
        std::filesystem::exists(
            saveDir.path +
            "/tenant_0/custom_static_postings.bin"));
    BOOST_CHECK(
        !std::filesystem::exists(
            saveDir.path +
            "/tenant_0/SPTAGFullList.bin"));
    const std::string headMetadataPath =
        saveDir.path +
        "/tenant_0/HeadIndex/head_node_meta.bin";
    {
        std::ifstream headMetadata(
            headMetadataPath,
            std::ios::binary);
        BOOST_REQUIRE(headMetadata.good());
        std::array<std::int32_t, 4> header{};
        headMetadata.read(
            reinterpret_cast<char*>(header.data()),
            static_cast<std::streamsize>(
                sizeof(header)));
        BOOST_REQUIRE(headMetadata.good());
        BOOST_CHECK_EQUAL(header[0], 8);
        BOOST_CHECK_GT(header[1], 0);
        BOOST_CHECK_EQUAL(header[2], 1);
    }
    const std::string sparsePath =
        saveDir.path +
        "/tenant_0/extreme_sparse_tags.bin";
    BOOST_REQUIRE(PathExists(sparsePath));
    const std::string numericMetadataPath =
        saveDir.path +
        "/tenant_0/numeric_meta.bin";
    std::vector<std::uint8_t>
        validNumericMetadata;
    {
        std::ifstream input(
            numericMetadataPath,
            std::ios::binary);
        BOOST_REQUIRE(input.good());
        input.seekg(0, std::ios::end);
        const std::streamoff bytes = input.tellg();
        BOOST_REQUIRE_GE(bytes, 48);
        validNumericMetadata.resize(
            static_cast<size_t>(bytes));
        input.seekg(0, std::ios::beg);
        input.read(
            reinterpret_cast<char*>(
                validNumericMetadata.data()),
            bytes);
        BOOST_REQUIRE(input.good());
        std::uint32_t magic = 0;
        std::uint32_t version = 0;
        std::int32_t base = 0;
        std::int32_t count = 0;
        std::int32_t vectorsInMetadata = 0;
        std::int32_t attributesInMetadata = 0;
        std::uint64_t generation = 0;
        std::uint32_t domainHigh = 0;
        std::memcpy(
            &magic,
            validNumericMetadata.data(),
            sizeof(magic));
        std::memcpy(
            &version,
            validNumericMetadata.data() + 4,
            sizeof(version));
        std::memcpy(
            &base,
            validNumericMetadata.data() + 8,
            sizeof(base));
        std::memcpy(
            &count,
            validNumericMetadata.data() + 12,
            sizeof(count));
        std::memcpy(
            &vectorsInMetadata,
            validNumericMetadata.data() + 16,
            sizeof(vectorsInMetadata));
        std::memcpy(
            &attributesInMetadata,
            validNumericMetadata.data() + 20,
            sizeof(attributesInMetadata));
        std::memcpy(
            &generation,
            validNumericMetadata.data() + 24,
            sizeof(generation));
        std::memcpy(
            &domainHigh,
            validNumericMetadata.data() + 44,
            sizeof(domainHigh));
        BOOST_CHECK_EQUAL(magic, 0x324D554EU);
        BOOST_CHECK_EQUAL(version, 2U);
        BOOST_CHECK_EQUAL(base, 1);
        BOOST_CHECK_EQUAL(count, 1);
        BOOST_CHECK_EQUAL(
            vectorsInMetadata, kNumVectors);
        BOOST_CHECK_EQUAL(
            attributesInMetadata,
            kNumAttributes);
        BOOST_CHECK_NE(generation, 0U);
        BOOST_CHECK_EQUAL(
            domainHigh,
            static_cast<std::uint32_t>(
                kNumVectors - 1));
    }

    TenantIndexManager loaded(
        kDim, "SPANN", "Float");
    BOOST_REQUIRE(
        loaded.LoadAll(saveDir.path.c_str()));
    const auto query =
        [&](int vector) {
            return ByteArray(
                reinterpret_cast<std::uint8_t*>(
                    vectors.data() +
                    static_cast<size_t>(vector) *
                        kDim),
                kDim * sizeof(float), false);
        };
    std::uint32_t sparseTag = kSparseTag;
    auto sparseOnly = loaded.SearchWithACL(
        query(kNumVectors - 1), 0, kResultNum,
        ByteArray(
            reinterpret_cast<std::uint8_t*>(
                &sparseTag),
            sizeof(sparseTag), false),
        1);
    BOOST_REQUIRE(sparseOnly != nullptr);
    const auto sparseOnlyIDs =
        ExtractValidIds(sparseOnly);
    BOOST_REQUIRE_EQUAL(
        sparseOnlyIDs.size(), kResultNum);
    BOOST_CHECK(
        std::find(
            sparseOnlyIDs.begin(),
            sparseOnlyIDs.end(),
            kNumVectors - 1) !=
        sparseOnlyIDs.end());
    for (int vector : sparseOnlyIDs)
    {
        BOOST_CHECK_GE(
            vector, kNumVectors - kSparseCount);
    }
    BOOST_CHECK_EQUAL(
        sparseOnly->GetScanned(), kSparseCount);

    const std::vector<std::uint32_t> mixedDNF = {
        0x444E4633U, 2,
        1, 0, 0, SPTAG::Cache::DNF_EQ,
        kSparseTag,
        2,
        0, 0, SPTAG::Cache::DNF_EQ,
        kCommonTag,
        1, 1, SPTAG::Cache::DNF_EQ, 7U};
    auto mixed = loaded.SearchWithACL(
        query(7), 0, kResultNum,
        ByteArray(
            reinterpret_cast<std::uint8_t*>(
                const_cast<std::uint32_t*>(
                    mixedDNF.data())),
            mixedDNF.size() *
                sizeof(std::uint32_t),
            false),
        -1);
    BOOST_REQUIRE(mixed != nullptr);
    const auto mixedIDs = ExtractValidIds(mixed);
    BOOST_CHECK(
        std::find(
            mixedIDs.begin(), mixedIDs.end(), 7) !=
        mixedIDs.end());
    bool foundSparse = false;
    for (int vector : mixedIDs)
    {
        foundSparse =
            foundSparse ||
            vector >=
                kNumVectors - kSparseCount;
        BOOST_CHECK(
            vector >=
                kNumVectors - kSparseCount ||
            vector == 7);
    }
    BOOST_CHECK(foundSparse);

    const std::vector<std::uint32_t> numericOnlyDNF = {
        0x444E4633U, 1,
        1, 1, 1, SPTAG::Cache::DNF_EQ, 42U};
    auto numericOnly = loaded.SearchWithACL(
        query(42), 0, kResultNum,
        ByteArray(
            reinterpret_cast<std::uint8_t*>(
                const_cast<std::uint32_t*>(
                    numericOnlyDNF.data())),
            numericOnlyDNF.size() *
                sizeof(std::uint32_t),
            false),
        -1);
    BOOST_REQUIRE(numericOnly != nullptr);
    const auto numericIDs =
        ExtractValidIds(numericOnly);
    BOOST_REQUIRE(
        std::find(
            numericIDs.begin(), numericIDs.end(),
            42) != numericIDs.end());
    for (int vector : numericIDs)
    {
        BOOST_CHECK_EQUAL(vector, 42);
    }

    const auto writeNumericMetadata =
        [&](const std::vector<std::uint8_t>& bytes) {
            std::ofstream output(
                numericMetadataPath,
                std::ios::binary |
                    std::ios::trunc);
            BOOST_REQUIRE(output.good());
            if (!bytes.empty()) {
                output.write(
                    reinterpret_cast<const char*>(
                        bytes.data()),
                    static_cast<std::streamsize>(
                        bytes.size()));
            }
            BOOST_REQUIRE(output.good());
        };
    const auto checkNumericFailOpen = [&]() {
        TenantIndexManager fallback(
            kDim, "SPANN", "Float");
        BOOST_REQUIRE(
            fallback.LoadAll(
                saveDir.path.c_str()));
        auto result = fallback.SearchWithACL(
            query(42), 0, kResultNum,
            ByteArray(
                reinterpret_cast<std::uint8_t*>(
                    const_cast<std::uint32_t*>(
                        numericOnlyDNF.data())),
                numericOnlyDNF.size() *
                    sizeof(std::uint32_t),
                false),
            -1);
        BOOST_REQUIRE(result != nullptr);
        const auto ids = ExtractValidIds(result);
        BOOST_REQUIRE(
            std::find(
                ids.begin(), ids.end(), 42) !=
            ids.end());
        for (int vector : ids) {
            BOOST_CHECK_EQUAL(vector, 42);
        }
    };
    const auto refreshNumericFingerprint =
        [](std::vector<std::uint8_t>& bytes) {
            constexpr std::uint64_t kOffset =
                1469598103934665603ULL;
            constexpr std::uint64_t kPrime =
                1099511628211ULL;
            std::uint64_t fingerprint = kOffset;
            const auto append =
                [&](size_t offset, size_t count) {
                    for (size_t i = 0; i < count;
                         ++i) {
                        fingerprint ^=
                            bytes[offset + i];
                        fingerprint *= kPrime;
                    }
                };
            append(8, 4);
            append(12, 4);
            append(16, 4);
            append(20, 4);
            append(24, 8);
            append(40, 8);
            std::memcpy(
                bytes.data() + 32,
                &fingerprint,
                sizeof(fingerprint));
        };

    std::filesystem::remove(
        numericMetadataPath);
    checkNumericFailOpen();
    writeNumericMetadata(validNumericMetadata);

    auto truncatedNumericMetadata =
        validNumericMetadata;
    truncatedNumericMetadata.pop_back();
    writeNumericMetadata(
        truncatedNumericMetadata);
    checkNumericFailOpen();

    auto wrongBaseNumericMetadata =
        validNumericMetadata;
    wrongBaseNumericMetadata[8] ^= 1U;
    refreshNumericFingerprint(
        wrongBaseNumericMetadata);
    writeNumericMetadata(
        wrongBaseNumericMetadata);
    checkNumericFailOpen();

    auto wrongCountNumericMetadata =
        validNumericMetadata;
    wrongCountNumericMetadata[12] ^= 1U;
    writeNumericMetadata(
        wrongCountNumericMetadata);
    checkNumericFailOpen();

    auto wrongDomainNumericMetadata =
        validNumericMetadata;
    wrongDomainNumericMetadata[44] ^= 1U;
    refreshNumericFingerprint(
        wrongDomainNumericMetadata);
    writeNumericMetadata(
        wrongDomainNumericMetadata);
    checkNumericFailOpen();

    auto wrongGenerationNumericMetadata =
        validNumericMetadata;
    wrongGenerationNumericMetadata[24] ^= 1U;
    refreshNumericFingerprint(
        wrongGenerationNumericMetadata);
    writeNumericMetadata(
        wrongGenerationNumericMetadata);
    checkNumericFailOpen();
    writeNumericMetadata(validNumericMetadata);

    {
        std::fstream output(
            sparsePath,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(output.good());
        output.seekg(-1, std::ios::end);
        char byte = 0;
        output.read(&byte, 1);
        BOOST_REQUIRE(output.good());
        output.clear();
        output.seekp(-1, std::ios::end);
        byte ^= 1;
        output.write(&byte, 1);
        BOOST_REQUIRE(output.good());
    }
    TenantIndexManager corrupted(
        kDim, "SPANN", "Float");
    BOOST_CHECK(
        !corrupted.LoadAll(saveDir.path.c_str()));
    TenantIndexManager repair(
        kDim, "SPANN", "Float");
    BOOST_REQUIRE(
        repair.LoadAllForSignatureRepair(
            saveDir.path.c_str()));
    BOOST_REQUIRE(
        repair.BuildSignaturesWithVectors(
            0, attributeBytes, kNumVectors,
            kNumAttributes, vectorBytes));
    TenantIndexManager repaired(
        kDim, "SPANN", "Float");
    BOOST_REQUIRE(
        repaired.LoadAll(saveDir.path.c_str()));
    auto repairedSparse = repaired.SearchWithACL(
        query(kNumVectors - 1), 0, kResultNum,
        ByteArray(
            reinterpret_cast<std::uint8_t*>(
                &sparseTag),
            sizeof(sparseTag), false),
        1);
    BOOST_REQUIRE(repairedSparse != nullptr);
    const auto repairedIDs =
        ExtractValidIds(repairedSparse);
    BOOST_REQUIRE_EQUAL(
        repairedIDs.size(), kResultNum);
    BOOST_CHECK(
        std::find(
            repairedIDs.begin(), repairedIDs.end(),
            kNumVectors - 1) !=
        repairedIDs.end());

    TenantIndexManager saveFailure(
        kDim, "SPANN", "Float");
    BOOST_REQUIRE(
        saveFailure.LoadAll(
            saveDir.path.c_str()));
    const std::uintmax_t publishedHeadBytes =
        std::filesystem::file_size(
            headMetadataPath);
    const std::string blockedTemporary =
        headMetadataPath + ".tmp";
    BOOST_REQUIRE(
        std::filesystem::create_directory(
            blockedTemporary));
    BOOST_CHECK(
        !saveFailure.BuildSignaturesWithVectors(
            0, staleAttributeBytes, kNumVectors,
            kNumAttributes, vectorBytes));
    BOOST_CHECK_EQUAL(
        std::filesystem::file_size(
            headMetadataPath),
        publishedHeadBytes);
    BOOST_REQUIRE(
        std::filesystem::remove(
            blockedTemporary));
}

BOOST_AUTO_TEST_CASE(ExtremeSparseIntegerCosineMergeUsesNativeScale)
{
    constexpr int kDim = 128;
    constexpr int kNumVectors = 256;
    constexpr std::uint32_t kDenseTag = 10U;
    constexpr std::uint32_t kSparseTag = 99U;
    constexpr int kSparseVector = kNumVectors - 1;

    std::vector<std::uint8_t> vectors(
        static_cast<size_t>(kNumVectors) * kDim, 0);
    std::vector<std::uint32_t> tags(
        static_cast<size_t>(kNumVectors));
    for (int vector = 0;
         vector < kSparseVector; ++vector)
    {
        auto* row = vectors.data() +
            static_cast<size_t>(vector) * kDim;
        const int group = vector % 4;
        row[group] = 230;
        row[(group + 1) % 4] =
            static_cast<std::uint8_t>(
                20 + vector % 21);
        for (int dimension = 4;
             dimension < kDim; ++dimension)
        {
            row[dimension] =
                static_cast<std::uint8_t>(
                    (vector * (dimension + 3) +
                     dimension * 11) %
                    16);
        }
        tags[static_cast<size_t>(vector)] =
            kDenseTag +
            static_cast<std::uint32_t>(group);
    }
    {
        auto* row = vectors.data() +
            static_cast<size_t>(kSparseVector) *
                kDim;
        row[0] = 128;
        row[1] = 221;
        tags[static_cast<size_t>(kSparseVector)] =
            kSparseTag;
    }
    TenantIndexManager builder(
        kDim, "SPANN", "UInt8");
    builder.SetStorageBackend("STATIC");
    builder.SetBuildParam(
        "DistCalcMethod", "Cosine", "Base");
    builder.SetBuildParam(
        "IndexAlgoType", "BKT", "Base");
    builder.SetBuildParam(
        "SelectHeadType", "BKT", "SelectHead");
    builder.SetBuildParam(
        "Ratio", "0.25", "SelectHead");
    builder.SetBuildParam(
        "BKTLambdaFactor", "-1", "SelectHead");
    builder.SetBuildParam(
        "NumberOfThreads", "1", "SelectHead");
    builder.SetBuildParam(
        "NumberOfThreads", "1", "BuildHead");
    builder.SetBuildParam(
        "NeighborhoodSize", "16", "BuildHead");
    builder.SetBuildParam(
        "RefineIterations", "1", "BuildHead");
    builder.SetBuildParam(
        "BKTLambdaFactor", "-1", "BuildHead");
    builder.SetSSDBuildParam(
        "InternalResultNum", "16");
    builder.SetSSDBuildParam(
        "SearchInternalResultNum", "8");
    builder.SetSSDBuildParam(
        "NumberOfThreads", "1");
    builder.SetSSDBuildParam(
        "PostingPageLimit", "2");
    builder.SetSSDBuildParam(
        "SearchPostingPageLimit", "2");
    builder.SetSSDBuildParam(
        "ReplicaCount", "2");
    builder.SetSSDBuildParam(
        "TailReplicaCount", "1");
    builder.SetSSDBuildParam(
        "EnableUnfilterTail", "true");
    builder.SetSSDBuildParam(
        "UnfilterTailBufferLength", "-1");
    builder.SetSSDBuildParam("CrossEdges", "0");
    builder.SetSSDBuildParam(
        "ExcludeHead", "true");
    builder.SetSSDBuildParam(
        "StaticACLTagCols", "1");
    builder.SetSSDBuildParam(
        "EnableLimitedTagPosting", "true");
    builder.SetSSDBuildParam(
        "LimitedTagColumn", "0");
    builder.SetSSDBuildParam(
        "LimitedTagSlotsPerHead", "2");
    builder.SetSSDBuildParam(
        "LimitedTagVoteHeadCount", "2");
    builder.SetSSDBuildParam(
        "LimitedTagMinHeadCount", "1");
    builder.SetSSDBuildParam(
        "EnableExtremeSparseTag", "true");
    builder.SetSSDBuildParam(
        "ExtremeSparseTagMaxSelectivity",
        "0.004");
    builder.SetSSDBuildParam(
        "EnableHierPostingFilter", "false");

    ByteArray vectorBytes(
        vectors.data(), vectors.size(), false);
    ByteArray tagBytes(
        reinterpret_cast<std::uint8_t*>(
            tags.data()),
        tags.size() * sizeof(std::uint32_t),
        false);
    BOOST_REQUIRE(
        builder.BuildFromDataWithTagsSingleTenant(
            vectorBytes, 0, kNumVectors, tagBytes, 1,
            false, false));
    BOOST_REQUIRE(
        builder.BuildSignaturesWithVectors(
            0, tagBytes, kNumVectors, 1,
            vectorBytes));

    std::array<std::uint8_t, kDim> query = {
        255, 0, 0, 0, 0, 0, 0, 0};
    const std::vector<std::uint32_t> mixedDNF = {
        0x444E4633U, 2,
        1, 0, 0, SPTAG::Cache::DNF_EQ,
        kSparseTag,
        1, 0, 0, SPTAG::Cache::DNF_EQ,
        kDenseTag};
    auto result = builder.SearchWithACL(
        ByteArray(
            query.data(), query.size(), false),
        0, 1,
        ByteArray(
            reinterpret_cast<std::uint8_t*>(
                const_cast<std::uint32_t*>(
                    mixedDNF.data())),
            mixedDNF.size() *
                sizeof(std::uint32_t),
            false),
        -1);
    BOOST_REQUIRE(result != nullptr);
    const auto ids = ExtractValidIds(result);
    BOOST_REQUIRE_EQUAL(ids.size(), 1);
    BOOST_CHECK_NE(ids[0], kSparseVector);
    BOOST_CHECK_EQUAL(
        tags[static_cast<size_t>(ids[0])],
        kDenseTag);
}

BOOST_AUTO_TEST_CASE(HeadMetadataWidthsRemainIndexLocal)
{
    int narrowBits[SPTAG::Cache::HIER_LEVELS] = {
        64, 64, 64, 64, 64};
    int wideBits[SPTAG::Cache::HIER_LEVELS] = {
        256, 128, 128, 256, 64};
    SPTAG::Cache::HierWidthTable narrow;
    SPTAG::Cache::HierWidthTable wide;
    narrow.Set(
        narrowBits,
        SPTAG::Cache::HIER_LEVELS);
    wide.Set(
        wideBits,
        SPTAG::Cache::HIER_LEVELS);

    auto first = SPTAG::VectorIndex::CreateInstance(
        SPTAG::IndexAlgoType::BKT,
        SPTAG::VectorValueType::Float);
    auto second = SPTAG::VectorIndex::CreateInstance(
        SPTAG::IndexAlgoType::BKT,
        SPTAG::VectorValueType::Float);
    BOOST_REQUIRE(first != nullptr);
    BOOST_REQUIRE(second != nullptr);

    first->InitializeHeadNodeMeta(
        1, 0, narrow);
    SPTAG::Cache::HierarchicalPostingMask firstMask;
    firstMask.Clear();
    firstMask.Insert(4, 12345U, narrow);
    first->SetHeadNodePostingHierMask(
        0, firstMask);

    second->InitializeHeadNodeMeta(
        1, 0, wide);
    SPTAG::Cache::HierarchicalPostingMask secondMask;
    secondMask.Clear();
    secondMask.Insert(1, 98765U, wide);
    second->SetHeadNodePostingHierMask(
        0, secondMask);

    const auto savedGlobalWidths =
        SPTAG::Cache::HierWidths();
    SPTAG::Cache::SetHierWidths(
        wideBits,
        SPTAG::Cache::HIER_LEVELS);

    BOOST_CHECK_NE(
        first->GetHeadNodeMetaStride(),
        second->GetHeadNodeMetaStride());
    BOOST_CHECK_EQUAL(
        first->GetHeadNodeHierWidths().bits[4],
        narrow.bits[4]);
    BOOST_CHECK_EQUAL(
        second->GetHeadNodeHierWidths().bits[1],
        wide.bits[1]);

    SPTAG::Cache::HierarchicalPostingMask firstQuery;
    firstQuery.Clear();
    firstQuery.Insert(4, 12345U, narrow);
    BOOST_CHECK(
        first->HeadPostingHierMaskMayIntersect(
            0, firstQuery));

    SPTAG::Cache::HierarchicalPostingMask secondQuery;
    secondQuery.Clear();
    secondQuery.Insert(1, 98765U, wide);
    BOOST_CHECK(
        second->HeadPostingHierMaskMayIntersect(
            0, secondQuery));
    BOOST_CHECK(
        !first->HeadPostingHierMaskMayIntersect(
            0, secondQuery));
    SPTAG::Cache::SetHierWidths(
        savedGlobalWidths.bits,
        SPTAG::Cache::HIER_LEVELS);
}

BOOST_AUTO_TEST_CASE(GlobalTailSignaturesRemainIndependent)
{
    const std::vector<std::vector<uint32_t>>
        pureTags = {{11U, 12U}, {21U}};
    const std::vector<std::vector<uint32_t>>
        tailTags = {{31U}, {41U, 42U}};
    SPTAG::Cache::TenantBitmaskPS signatures;
    signatures.Build(
        2, pureTags, tailTags);

    SPTAG::Cache::PostingBitmask query;
    query.Clear();
    query.Insert(31U);
    BOOST_CHECK(
        !signatures.ShouldReadPosting(0, query));
    BOOST_CHECK(
        signatures.ShouldReadTailPosting(
            0, query));

    ScopedTempDir directory(MakeTempDir());
    const std::string path =
        directory.path +
        "/signatures_bitmask.bin";
    constexpr std::uint64_t generation =
        0x123456789ABCDEF0ULL;
    BOOST_REQUIRE(
        signatures.Save(path, generation));
    SPTAG::Cache::TenantBitmaskPS loaded;
    BOOST_REQUIRE(
        loaded.Load(path, generation));
    BOOST_CHECK(loaded.has_tail_signatures);
    BOOST_CHECK(
        !loaded.ShouldReadPosting(0, query));
    BOOST_CHECK(
        loaded.ShouldReadTailPosting(0, query));

    auto index =
        SPTAG::VectorIndex::CreateInstance(
            SPTAG::IndexAlgoType::BKT,
            SPTAG::VectorValueType::Float);
    BOOST_REQUIRE(index != nullptr);
    SPTAG::Cache::HierWidthTable widths;
    index->InitializeHeadNodeMeta(
        1, 1, widths, true);
    BOOST_REQUIRE(index->HasHeadNodeTailPS());
    index->SetHeadNodePS(
        0, loaded.ps[0]);
    index->SetHeadNodeTailPS(
        0, loaded.tail_ps[0]);
    BOOST_CHECK(
        !index->HeadNodePSMayIntersect(
            0, query));
    BOOST_CHECK(
        index->HeadNodeTailPSMayIntersect(
            0, query));

    const SPTAG::Cache::NumQuantParam
        numericDomain{0U, 1000U};
    auto* pureNumeric =
        index->GetHeadNodeNumQuantMutable(0);
    auto* tailNumeric =
        index->GetHeadNodeTailNumQuantMutable(0);
    BOOST_REQUIRE(pureNumeric != nullptr);
    BOOST_REQUIRE(tailNumeric != nullptr);
    SPTAG::Cache::NumQuantInsert(
        pureNumeric, 0,
        SPTAG::Cache::NumQuantBucket(
            numericDomain, 100U));
    SPTAG::Cache::NumQuantInsert(
        tailNumeric, 0,
        SPTAG::Cache::NumQuantBucket(
            numericDomain, 900U));

    SPTAG::Cache::DNFPredicate numericDNF;
    numericDNF.clauses.push_back(
        SPTAG::Cache::DNFClause{{
            SPTAG::Cache::DNFLiteral{
                1U, 800U,
                SPTAG::Cache::DNF_GE, 1U}}});
    BOOST_CHECK(
        !numericDNF.MayMatchCoarseQuant(
            *index->GetHeadNodeTailPS(0),
            pureNumeric, 1,
            &numericDomain, 1, 1));
    BOOST_CHECK(
        numericDNF.MayMatchCoarseQuant(
            *index->GetHeadNodeTailPS(0),
            tailNumeric, 1,
            &numericDomain, 1, 1));
    BOOST_CHECK(loaded.generation_bound);
    BOOST_CHECK_EQUAL(
        loaded.generation_fingerprint,
        generation);
    SPTAG::Cache::TenantBitmaskPS wrongGeneration;
    BOOST_CHECK(
        !wrongGeneration.Load(
            path, generation + 1));
    const std::string truncatedPath =
        directory.path +
        "/posting_signatures_truncated.bin";
    {
        std::ifstream input(path, std::ios::binary);
        std::ofstream output(
            truncatedPath, std::ios::binary);
        BOOST_REQUIRE(input.good());
        BOOST_REQUIRE(output.good());
        std::uint32_t magic = 0;
        input.read(
            reinterpret_cast<char*>(&magic),
            sizeof(magic));
        BOOST_REQUIRE(input.good());
        output.write(
            reinterpret_cast<const char*>(&magic),
            sizeof(magic));
        BOOST_REQUIRE(output.good());
    }
    SPTAG::Cache::TenantBitmaskPS truncated;
    BOOST_CHECK(
        !truncated.Load(
            truncatedPath, generation));
    {
        std::fstream output(
            path,
            std::ios::binary |
                std::ios::in |
                std::ios::out);
        BOOST_REQUIRE(output.good());
        output.seekg(-1, std::ios::end);
        char byte = 0;
        output.read(&byte, 1);
        BOOST_REQUIRE(output.good());
        output.clear();
        output.seekp(-1, std::ios::end);
        byte ^= 1;
        output.write(&byte, 1);
        BOOST_REQUIRE(output.good());
    }
    SPTAG::Cache::TenantBitmaskPS corrupt;
    BOOST_CHECK(
        !corrupt.Load(path, generation));
}

BOOST_AUTO_TEST_CASE(AtomicReplacementPreservesPublishedFileOnFailure)
{
    ScopedTempDir dir(MakeTempDir());
    const std::string destination =
        dir.path + "/published.bin";
    const std::string temporary =
        destination + ".tmp";
    {
        std::ofstream output(
            destination,
            std::ios::binary);
        BOOST_REQUIRE(output.good());
        output << "old";
    }

    BOOST_CHECK(
        !SPTAG::Helper::AtomicReplaceFile(
            temporary, destination));
    {
        std::ifstream input(
            destination,
            std::ios::binary);
        std::string contents;
        input >> contents;
        BOOST_CHECK_EQUAL(contents, "old");
    }

    {
        std::ofstream output(
            temporary,
            std::ios::binary);
        BOOST_REQUIRE(output.good());
        output << "new";
    }
    BOOST_REQUIRE(
        SPTAG::Helper::AtomicReplaceFile(
            temporary, destination));
    {
        std::ifstream input(
            destination,
            std::ios::binary);
        std::string contents;
        input >> contents;
        BOOST_CHECK_EQUAL(contents, "new");
    }
}

BOOST_AUTO_TEST_SUITE_END()