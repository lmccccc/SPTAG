// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#include "inc/CoreInterface.h"
#include "inc/Core/SPANN/Index.h"
#include "inc/Helper/AtomicFile.h"
#include "inc/Helper/HeadCrossEdges.h"
#include "inc/Test.h"
#include "inc/ScopedEnvironmentVariable.h"

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
#include <iterator>
#include <limits>
#include <map>
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

bool LoadHeadNodeMetaFile(
    const std::string& workDir,
    const std::shared_ptr<SPTAG::VectorIndex>& index);
bool SaveHeadNodeMetaFile(
    const std::string& workDir,
    const std::shared_ptr<SPTAG::VectorIndex>& headIndex,
    std::uint64_t generationFingerprint);

namespace {

std::string MakeTempDir()
{
#ifdef _WIN32
    const int process = _getpid();
#else
    const int process = getpid();
#endif
    const auto dir = std::filesystem::current_path() /
        ("sptag_acl_concurrent_" + std::to_string(process) + "_" +
         std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    BOOST_REQUIRE(std::filesystem::create_directory(dir));
    return dir.string();
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

using FileSnapshot = std::map<std::string, std::vector<char>>;

FileSnapshot SnapshotFiles(const std::string& directory)
{
    FileSnapshot files;
    for (const auto& entry : std::filesystem::recursive_directory_iterator(directory))
    {
        if (entry.is_directory()) continue;
        BOOST_REQUIRE(entry.is_regular_file());
        std::ifstream input(entry.path(), std::ios::binary);
        BOOST_REQUIRE(input.good());
        auto& bytes = files[entry.path().lexically_relative(directory).generic_string()];
        bytes.assign(std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>());
        BOOST_REQUIRE(!input.bad());
        BOOST_REQUIRE_EQUAL(bytes.size(), entry.file_size());
    }
    return files;
}

void CheckSavedFiles(const FileSnapshot& expected, const FileSnapshot& actual,
                     bool includeConfig = true)
{
    BOOST_REQUIRE_EQUAL(expected.size(), actual.size());
    for (const auto& file : expected)
    {
        BOOST_TEST_CONTEXT("saved artifact: " << file.first)
        {
            const auto found = actual.find(file.first);
            BOOST_REQUIRE(found != actual.end());
            if (includeConfig || file.first != "indexloader.ini")
                BOOST_CHECK(file.second == found->second);
        }
    }
}

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

BOOST_AUTO_TEST_CASE(RemovedAttributeOrganizationRejectedBeforeBuild)
{
    const float vector[2] = {1.0f, 2.0f};
    ByteArray vectors(reinterpret_cast<std::uint8_t*>(const_cast<float*>(vector)),
                      sizeof(vector), false);
    for (const char* name : {"ACLCols", "HierLevelWidths", "PivotForceNodeCount",
                             "DisablePivotEstimator", "RoutingCols", "PerVectorTagsFile",
                             "LimitedTagVoteHeadCount", "TagOffset", "BKTSeed", "TPTSeed",
                             "HierarchySignatureMinSelectivity", "HierarchySignatureMaxSelectivity"}) {
        TenantIndexManager builder(2, "SPANN", "Float");
        builder.SetBuildParam(name, "0", "MultiTenant");
        BOOST_CHECK(!builder.BuildFromData(vectors, ByteArray(), 1, false, true));
        TenantIndexManager ssdBuilder(2, "SPANN", "Float");
        ssdBuilder.SetSSDBuildParam(name, "0");
        BOOST_CHECK(!ssdBuilder.BuildFromData(vectors, ByteArray(), 1, false, true));
    }
    TenantIndexManager builder(2, "SPANN", "Float");
    builder.SetBuildParam("SelectHeadType", "PerTagBKT", "SelectHead");
    BOOST_CHECK(!builder.BuildFromData(vectors, ByteArray(), 1, false, true));
}

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

BOOST_AUTO_TEST_CASE(FlatACLSignaturesDoNotInferCategoricalColumns)
{
    constexpr int dimension = 8;
    constexpr int count = 256;
    constexpr int resultCount = 10;
    std::vector<float> vectors(count * dimension);
    FillNormalizedVectors(vectors, count, dimension);
    std::string metadata;
    for (int row = 0; row < count; ++row) metadata += "tenant0\n";
    ByteArray vectorBytes(reinterpret_cast<std::uint8_t*>(vectors.data()),
        vectors.size() * sizeof(float), false);
    ByteArray metadataBytes(reinterpret_cast<std::uint8_t*>(metadata.data()),
        metadata.size(), false);
    ByteArray query(vectorBytes.Data(), dimension * sizeof(float), false);

    for (const auto& scenario : {
             std::pair<bool, bool>{false, false}, {false, true},
             {true, false}, {true, true}}) {
        const bool limitedTag = scenario.first;
        const bool overlapping = scenario.second;
        BOOST_TEST_CONTEXT("limited-tag=" << limitedTag << ", overlapping=" << overlapping) {
            std::vector<std::uint32_t> tags(count * 2);
            for (int row = 0; row < count; ++row) {
                tags[row * 2] = overlapping ? 10U + row % 2 : 10U;
                tags[row * 2 + 1] = overlapping && row % 3 != 0 ? 10U : 0U;
            }
            ByteArray tagBytes(reinterpret_cast<std::uint8_t*>(tags.data()),
                tags.size() * sizeof(std::uint32_t), false);
            TenantIndexManager builder(dimension, "SPANN", "Float");
            builder.SetStorageBackend("STATIC");
            builder.SetBuildParam("DistCalcMethod", "L2", "Base");
            builder.SetBuildParam("IndexAlgoType", "BKT", "Base");
            builder.SetBuildParam("SelectHeadType", "BKT", "SelectHead");
            builder.SetBuildParam("Ratio", "0.25", "SelectHead");
            builder.SetBuildParam("NumberOfThreads", "1", "SelectHead");
            builder.SetBuildParam("NumberOfThreads", "1", "BuildHead");
            builder.SetSSDBuildParam("NumberOfThreads", "1");
            builder.SetSSDBuildParam("InternalResultNum", "64");
            builder.SetSSDBuildParam("SearchInternalResultNum", "256");
            builder.SetSSDBuildParam("MaxCheck", "4096");
            builder.SetSSDBuildParam("ReplicaCount", "2");
            builder.SetSSDBuildParam("PostingPageLimit", "2");
            builder.SetSSDBuildParam("StaticACLTagCols", "2");
            builder.SetSSDBuildParam("EnableLimitedTagPosting", limitedTag ? "true" : "false");
            if (limitedTag)
                builder.SetSSDBuildParam("LimitedTagSlotsPerHead", overlapping ? "2" : "1");
            builder.SetSSDBuildParam("LimitedTagMinHeadCount", "1");
            builder.SetSSDBuildParam("EnableHybridDistance", "false");
            builder.SetSSDBuildParam("ForceDenseTagSearch", "true");
            builder.SetSSDBuildParam("ExcludeHead", "true");
            builder.SetSSDBuildParam("EnableUnfilterTail", "true");
            builder.SetSSDBuildParam("TailReplicaCount", "0");
            builder.SetSSDBuildParam("UnfilterTailBufferLength", "0");
            BOOST_REQUIRE(builder.BuildFromDataWithTags(vectorBytes, metadataBytes,
                count, tagBytes, 2, false, true));
            BOOST_REQUIRE(builder.BuildSignatures(0, tagBytes, count, 2));
            ScopedTempDir saved(MakeTempDir());
            BOOST_REQUIRE(builder.SaveAll(saved.path.c_str()));
            if (limitedTag) {
                // Two categorical columns make flat ACL ineligible for key-only H:
                // these searches must retain the complete original/O route.
                std::shared_ptr<SPTAG::VectorIndex> native;
                const std::string tenantDirectory = saved.path + "/tenant_0";
                BOOST_REQUIRE(SPTAG::VectorIndex::LoadIndex(tenantDirectory, native) ==
                    SPTAG::ErrorCode::Success);
                auto* spann = dynamic_cast<SPTAG::SPANN::Index<float>*>(native.get());
                BOOST_REQUIRE(spann != nullptr);
                BOOST_REQUIRE(LoadHeadNodeMetaFile(tenantDirectory, native));
                BOOST_REQUIRE(spann->GetDiskIndex()->LimitedTagPostingRegionsReady());
                BOOST_REQUIRE(spann->GetMemoryIndex()->HasHeadNodeTailPS());
            }
            TenantIndexManager loaded(dimension, "SPANN", "Float");
            BOOST_REQUIRE(loaded.LoadAll(saved.path.c_str()));
            for (int pass = 0; pass < 2; ++pass) {
                for (std::uint32_t value : {10U, 0U, overlapping ? 11U : 10U}) {
                    auto result = loaded.SearchWithACL(query, 0, resultCount,
                        ByteArray(reinterpret_cast<std::uint8_t*>(&value), sizeof(value), false), 1);
                    const auto ids = ExtractValidIds(result);
                    BOOST_CHECK_EQUAL(ids.size(), resultCount);
                    for (const int id : ids) {
                        BOOST_REQUIRE_LT(id, count);
                        BOOST_CHECK(tags[id * 2] == value || tags[id * 2 + 1] == value);
                    }
                }
                for (std::uint32_t column : {0U, 1U}) {
                    const std::uint32_t value = column == 0 || overlapping ? 10U : 0U;
                    std::vector<std::uint32_t> words{0x444E4633U, 1U, 1U, 0U, column, 0U, value};
                    auto result = loaded.SearchWithPredicate(query, 0, resultCount,
                        ByteArray(reinterpret_cast<std::uint8_t*>(words.data()),
                            words.size() * sizeof(std::uint32_t), false), -1);
                    const auto ids = ExtractValidIds(result);
                    BOOST_CHECK_EQUAL(ids.size(), resultCount);
                    for (const int id : ids) {
                        BOOST_REQUIRE_LT(id, count);
                        BOOST_CHECK_EQUAL(tags[id * 2 + column], value);
                    }
                }
                std::uint32_t absent = 9999U;
                BOOST_CHECK(ExtractValidIds(loaded.SearchWithACL(query, 0, resultCount,
                    ByteArray(reinterpret_cast<std::uint8_t*>(&absent), sizeof(absent), false), 1)).empty());
                BOOST_REQUIRE(loaded.UnloadTenant(0));
            }
        }
    }
}

BOOST_AUTO_TEST_CASE(BuildSignaturesRejectsMismatchedSchemaWidth)
{
    constexpr int dimension = 8;
    constexpr int count = 256;
    std::vector<float> vectors(count * dimension);
    FillNormalizedVectors(vectors, count, dimension);
    std::string metadata;
    for (int row = 0; row < count; ++row) metadata += "tenant0\n";
    ByteArray vectorBytes(reinterpret_cast<std::uint8_t*>(vectors.data()),
        vectors.size() * sizeof(float), false);
    ByteArray metadataBytes(reinterpret_cast<std::uint8_t*>(metadata.data()),
        metadata.size(), false);

    for (const char* types : {
             "numeric,categorical", "numeric,categorical,numeric,categorical"}) {
        BOOST_TEST_CONTEXT("ColumnTypes=" << types) {
            const auto schema = SPTAG::TagSchema::Parse(types);
            const int width = schema.Width();
            std::vector<std::uint32_t> tags(count * width);
            for (int row = 0; row < count; ++row) {
                for (int column = 0; column < width; ++column) {
                    tags[row * width + column] = schema.IsCategorical(column)
                        ? 10U + row % 2 : 1000U * (column + 1) + row;
                }
            }
            ByteArray tagBytes(reinterpret_cast<std::uint8_t*>(tags.data()),
                tags.size() * sizeof(std::uint32_t), false);
            ScopedTempDir saved(MakeTempDir());
            ScopedEnvironmentVariable inPlace("SPTAG_SPANN_INPLACE_DIR", saved.path.c_str());
            TenantIndexManager builder(dimension, "SPANN", "Float");
            builder.SetStorageBackend("STATIC");
            builder.SetBuildParam("DistCalcMethod", "L2", "Base");
            builder.SetBuildParam("IndexAlgoType", "BKT", "Base");
            builder.SetBuildParam("SelectHeadType", "BKT", "SelectHead");
            builder.SetBuildParam("Ratio", "0.25", "SelectHead");
            builder.SetBuildParam("BKTLambdaFactor", "1", "SelectHead");
            builder.SetBuildParam("NumberOfThreads", "1", "SelectHead");
            builder.SetBuildParam("NumberOfThreads", "1", "BuildHead");
            builder.SetBuildParam("RefineIterations", "1", "BuildHead");
            builder.SetSSDBuildParam("NumberOfThreads", "1");
            builder.SetSSDBuildParam("InternalResultNum", "64");
            builder.SetSSDBuildParam("ReplicaCount", "2");
            builder.SetSSDBuildParam("PostingPageLimit", "2");
            builder.SetSSDBuildParam("ColumnTypes", types);
            builder.SetSSDBuildParam("LimitedTagColumn", std::to_string(width - 1).c_str());
            builder.SetSSDBuildParam("EnableLimitedTagPosting", "true");
            builder.SetSSDBuildParam("LimitedTagSlotsPerHead", "2");
            builder.SetSSDBuildParam("LimitedTagMinHeadCount", "1");
            builder.SetSSDBuildParam("EnableHybridDistance", "false");
            builder.SetSSDBuildParam("ExcludeHead", "true");
            builder.SetSSDBuildParam("EnableUnfilterTail", "true");
            builder.SetSSDBuildParam("TailReplicaCount", "0");
            builder.SetSSDBuildParam("UnfilterTailBufferLength", "0");
            builder.SetSSDBuildParam("CrossEdges", "0");
            BOOST_REQUIRE(builder.BuildFromDataWithTags(vectorBytes, metadataBytes,
                count, tagBytes, width, false, true));

            const auto checkRejectedWidths = [&](TenantIndexManager& manager) {
                const auto before = SnapshotFiles(saved.path);
                BOOST_REQUIRE(!before.empty());
                for (int callerWidth : {width + 1, width - 1}) {
                    BOOST_TEST_CONTEXT("caller width=" << callerWidth) {
                        // Each buffer matches the caller's dimensions, not the saved schema.
                        std::vector<std::uint32_t> mismatched(count * callerWidth, 10U);
                        ByteArray bytes(reinterpret_cast<std::uint8_t*>(mismatched.data()),
                            mismatched.size() * sizeof(std::uint32_t), false);
                        BOOST_CHECK(!manager.BuildSignatures(0, bytes, count, callerWidth));
                        CheckSavedFiles(before, SnapshotFiles(saved.path));
                    }
                }
            };
            checkRejectedWidths(builder);
            BOOST_REQUIRE(builder.BuildSignatures(0, tagBytes, count, width));
            BOOST_REQUIRE(builder.SaveAll(saved.path.c_str()));
            BOOST_REQUIRE(PathExists(saved.path + "/tenant_0/HeadIndex/head_node_meta.bin"));
            checkRejectedWidths(builder);
            BOOST_REQUIRE(builder.BuildSignatures(0, tagBytes, count, width));

            TenantIndexManager loaded(dimension, "SPANN", "Float");
            BOOST_REQUIRE(loaded.LoadAll(saved.path.c_str()));
            checkRejectedWidths(loaded);
            BOOST_REQUIRE(loaded.BuildSignatures(0, tagBytes, count, width));
        }
    }
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

BOOST_AUTO_TEST_CASE(LimitedTagRescueSignaturesRepairAndExport)
{
    constexpr int dimension = 128, count = 512, width = 4;
    constexpr int stride = sizeof(SPTAG::SizeType) + width * sizeof(uint32_t) + dimension * sizeof(float);
    std::vector<float> vectors(count * dimension);
    FillNormalizedVectors(vectors, count, dimension);
    std::vector<uint32_t> tags(count * width);
    std::string metadata;
    for (int vid = 0; vid < count; ++vid) {
        tags[vid * width] = vid;
        tags[vid * width + 1] = 7;
        tags[vid * width + 2] = count - vid;
        tags[vid * width + 3] = 1000 + vid;
        metadata += "tenant0\n";
    }
    TenantIndexManager builder(dimension, "SPANN", "Float");
    builder.SetStorageBackend("STATIC");
    builder.SetBuildParam("DistCalcMethod", "L2", "Base");
    builder.SetBuildParam("IndexAlgoType", "BKT", "Base");
    builder.SetBuildParam("SelectHeadType", "Random", "SelectHead");
    builder.SetBuildParam("Ratio", "0.015625", "SelectHead");
    builder.SetBuildParam("NumberOfThreads", "1", "SelectHead");
    builder.SetBuildParam("NumberOfThreads", "1", "BuildHead");
    builder.SetBuildParam("BKTLambdaFactor", "1", "BuildHead");
    builder.SetBuildParam("RefineIterations", "1", "BuildHead");
    builder.SetSSDBuildParam("NumberOfThreads", "1");
    builder.SetSSDBuildParam("InternalResultNum", "16");
    builder.SetSSDBuildParam("ReplicaCount", "8");
    builder.SetSSDBuildParam("RNGFactor", "100");
    builder.SetSSDBuildParam("PostingPageLimit", "1");
    builder.SetSSDBuildParam("PostingVectorLimit", "0");
    builder.SetSSDBuildParam("SearchPostingPageLimit", "12");
    builder.SetSSDBuildParam("ExcludeHead", "true");
    builder.SetSSDBuildParam("ColumnTypes", "numeric,categorical,numeric,categorical");
    builder.SetSSDBuildParam("StaticACLTagCols", "2");
    builder.SetSSDBuildParam("EnableLimitedTagPosting", "true");
    builder.SetSSDBuildParam("EnableLimitedTagSupportExpansion", "true");
    builder.SetSSDBuildParam("LimitedTagColumn", "1");
    builder.SetSSDBuildParam("LimitedTagSlotsPerHead", "1");
    builder.SetSSDBuildParam("LimitedTagMinHeadCount", "1");
    ByteArray vectorBytes(reinterpret_cast<uint8_t*>(vectors.data()), vectors.size() * sizeof(float), false);
    ByteArray tagBytes(reinterpret_cast<uint8_t*>(tags.data()), tags.size() * sizeof(uint32_t), false);
    ByteArray metadataBytes(reinterpret_cast<uint8_t*>(metadata.data()), metadata.size(), false);
    BOOST_REQUIRE(builder.BuildFromDataWithTags(
        vectorBytes, metadataBytes, count, tagBytes, width, false, true));
    BOOST_REQUIRE(builder.BuildSignatures(0, tagBytes, count, width));
    ScopedTempDir directory(MakeTempDir());
    BOOST_REQUIRE(builder.SaveAll(directory.path.c_str()));
    const auto tenant = directory.path + "/tenant_0";
    std::shared_ptr<SPTAG::VectorIndex> index;
    BOOST_REQUIRE(SPTAG::VectorIndex::LoadIndex(tenant, index) == SPTAG::ErrorCode::Success);
    BOOST_REQUIRE(LoadHeadNodeMetaFile(tenant, index));
    auto* spann = dynamic_cast<SPTAG::SPANN::Index<float>*>(index.get());
    BOOST_REQUIRE(spann != nullptr);
    const auto headIndex = spann->GetMemoryIndex();
    const auto disk = spann->GetDiskIndex();
    BOOST_REQUIRE_EQUAL(headIndex->GetNumSamples(), 8);
    BOOST_CHECK_EQUAL(spann->GetOptions()->Schema().text, "numeric,categorical,numeric,categorical");
    BOOST_CHECK_EQUAL(spann->GetOptions()->m_searchPostingPageLimit, 12);
    unsigned beyondCut = 0;
    std::vector<unsigned> copies(count, 0);
    for (SPTAG::SizeType head = 0; head < headIndex->GetNumSamples(); ++head) {
        const auto own = spann->GetGlobalVID(head);
        BOOST_CHECK_EQUAL(own, headIndex->GetHeadNodeGlobalVID(head));
        BOOST_CHECK_EQUAL(headIndex->GetHeadNodeHierMask(head)->tag[1], 7U);
        BOOST_CHECK_EQUAL(headIndex->GetHeadNodeHierMask(head)->tag[3], 1000U + own);
        const int pure = disk->GetPostingVectorCount(head, true);
        const int full = disk->GetPostingVectorCount(head, false);
        beyondCut += (std::max)(0, pure - 7);
        BOOST_CHECK_LE(full - pure, 7);
        std::string records;
        BOOST_REQUIRE(disk->GetWritePosting(nullptr, head, records, false) == SPTAG::ErrorCode::Success);
        BOOST_REQUIRE_EQUAL(records.size(), static_cast<size_t>(full) * stride);
        SPTAG::Cache::PostingBitmask expectedH, expectedO;
        for (int row = 0; row < full; ++row) {
            SPTAG::SizeType vid;
            const char* record = records.data() + static_cast<size_t>(row) * stride;
            std::memcpy(&vid, record, sizeof(vid));
            BOOST_REQUIRE_GE(vid, 0);
            BOOST_REQUIRE_LT(vid, count);
            BOOST_CHECK_EQUAL(std::memcmp(record + sizeof(vid), tags.data() + vid * width,
                width * sizeof(uint32_t)), 0);
            BOOST_CHECK_EQUAL(std::memcmp(record + sizeof(vid) + width * sizeof(uint32_t),
                vectors.data() + vid * dimension, dimension * sizeof(float)), 0);
            auto& signature = row < pure ? expectedH : expectedO;
            signature.Insert(7);
            signature.Insert(1000 + vid);
            if (row < pure) ++copies[vid];
        }
        BOOST_REQUIRE(headIndex->GetHeadNodePS(head) != nullptr);
        BOOST_REQUIRE(headIndex->GetHeadNodeTailPS(head) != nullptr);
        for (int word = 0; word < SPTAG::Cache::PS_BITMASK_WORDS; ++word) {
            BOOST_CHECK_EQUAL(headIndex->GetHeadNodePS(head)->bits[word], expectedH.bits[word]);
            BOOST_CHECK_EQUAL(headIndex->GetHeadNodeTailPS(head)->bits[word], expectedO.bits[word]);
        }
    }
    BOOST_CHECK_GT(beyondCut, 0U);
    for (SPTAG::SizeType head = 0; head < headIndex->GetNumSamples(); ++head)
        copies[spann->GetGlobalVID(head)] = 1;
    BOOST_CHECK(std::all_of(copies.begin(), copies.end(), [](unsigned n) { return n > 0; }));
    const auto snapshot = SnapshotFiles(tenant);
    BOOST_REQUIRE(std::filesystem::remove(tenant + "/numeric_meta.bin"));
    TenantIndexManager repaired(dimension, "SPANN", "Float");
    BOOST_REQUIRE(repaired.LoadAll(directory.path.c_str()));
    BOOST_REQUIRE(repaired.BuildSignatures(0, tagBytes, count, width));
    CheckSavedFiles(snapshot, SnapshotFiles(tenant));
    ScopedTempDir exported(MakeTempDir());
    BOOST_REQUIRE(repaired.SaveAll(exported.path.c_str()));
    CheckSavedFiles(snapshot, SnapshotFiles(exported.path + "/tenant_0"), false);
    TenantIndexManager loaded(dimension, "SPANN", "Float");
    BOOST_REQUIRE(loaded.LoadAll(exported.path.c_str()));
}

BOOST_AUTO_TEST_CASE(GraphlessHeadMetadataUsesCanonicalVIDs)
{
    constexpr int kDim = 16;
    constexpr int kNumVectors = 256;
    std::vector<float> vectors(static_cast<size_t>(kNumVectors) * kDim);
    FillNormalizedVectors(vectors, kNumVectors, kDim);
    std::vector<std::uint32_t> tags(kNumVectors);
    std::string metadata;
    for (int row = 0; row < kNumVectors; ++row)
    {
        tags[static_cast<size_t>(row)] = 1U + static_cast<std::uint32_t>(row % 4);
        metadata += "tenant0\n";
    }
    ByteArray vectorBytes(
        reinterpret_cast<std::uint8_t*>(vectors.data()),
        vectors.size() * sizeof(float), false);
    ByteArray tagBytes(
        reinterpret_cast<std::uint8_t*>(tags.data()),
        tags.size() * sizeof(std::uint32_t), false);
    ByteArray metadataBytes(
        reinterpret_cast<std::uint8_t*>(metadata.data()), metadata.size(), false);

    for (bool graphless : {false, true})
    {
        ScopedTempDir saved(MakeTempDir());
        ScopedEnvironmentVariable inPlace(
            "SPTAG_SPANN_INPLACE_DIR", saved.path.c_str());
        TenantIndexManager builder(kDim, "SPANN", "Float");
        builder.SetStorageBackend("STATIC");
        builder.SetBuildParam("DistCalcMethod", "L2", "Base");
        builder.SetBuildParam("IndexAlgoType", "BKT", "Base");
        builder.SetBuildParam("SelectHeadType", "BKT", "SelectHead");
        builder.SetBuildParam("Ratio", "0.25", "SelectHead");
        builder.SetBuildParam("SelectThreshold", "4", "SelectHead");
        builder.SetBuildParam("SplitFactor", "2", "SelectHead");
        builder.SetBuildParam("SplitThreshold", "8", "SelectHead");
        builder.SetBuildParam("BKTLambdaFactor", "-1", "SelectHead");
        builder.SetBuildParam("NumberOfThreads", "1", "SelectHead");
        builder.SetBuildParam("HierarchyEnabled", "true", "SelectHead");
        builder.SetBuildParam("HierarchyLevels", "3", "SelectHead");
        builder.SetBuildParam("HierarchyReplicaCount", "2", "SelectHead");
        builder.SetBuildParam("BuildH1Graph", graphless ? "false" : "true", "SelectHead");
        builder.SetBuildParam("CompactHierarchyVectors", "false", "SelectHead");
        builder.SetBuildParam("NumberOfThreads", "1", "BuildHead");
        builder.SetBuildParam("NeighborhoodSize", "16", "BuildHead");
        builder.SetBuildParam("RefineIterations", "1", "BuildHead");
        builder.SetSSDBuildParam("InternalResultNum", "32");
        builder.SetSSDBuildParam("SearchInternalResultNum", "16");
        builder.SetSSDBuildParam("NumberOfThreads", "1");
        builder.SetSSDBuildParam("PostingPageLimit", "2");
        builder.SetSSDBuildParam("SearchPostingPageLimit", "2");
        builder.SetSSDBuildParam("ReplicaCount", "2");
        builder.SetSSDBuildParam("TailReplicaCount", "0");
        builder.SetSSDBuildParam("UnfilterTailBufferLength", "0");
        builder.SetSSDBuildParam("EnableUnfilterTail", "true");
        builder.SetSSDBuildParam("CrossEdges", "0");
        builder.SetSSDBuildParam("ExcludeHead", "true");
        builder.SetSSDBuildParam("StaticACLTagCols", "1");
        builder.SetSSDBuildParam("EnableLimitedTagPosting", "true");
        builder.SetSSDBuildParam("LimitedTagSlotsPerHead", "2");
        builder.SetSSDBuildParam("LimitedTagMinHeadCount", "1");
            BOOST_REQUIRE(builder.BuildFromDataWithTags(
            vectorBytes, metadataBytes, kNumVectors, tagBytes, 1, false, true));
        BOOST_REQUIRE(builder.BuildSignatures(
            0, tagBytes, kNumVectors, 1));

        const std::string tenantDirectory = saved.path + "/tenant_0";
        const std::string routingStatsPath = tenantDirectory + "/tag_routing_stats.bin";
        BOOST_REQUIRE(PathExists(routingStatsPath));
        BOOST_REQUIRE(std::filesystem::remove(routingStatsPath));
        BOOST_REQUIRE(builder.BuildSignatures(
            0, tagBytes, kNumVectors, 1));
        BOOST_REQUIRE(PathExists(routingStatsPath));
        std::shared_ptr<SPTAG::VectorIndex> index;
        BOOST_REQUIRE(SPTAG::VectorIndex::LoadIndex(tenantDirectory, index) ==
                      SPTAG::ErrorCode::Success);
        auto* spann = dynamic_cast<SPTAG::SPANN::Index<float>*>(index.get());
        BOOST_REQUIRE(spann != nullptr);
        BOOST_CHECK_EQUAL(spann->GetOptions()->m_buildH1Graph, !graphless);
        BOOST_CHECK_EQUAL(spann->HasRoutingOnlyHierarchy(), graphless);
        BOOST_REQUIRE(LoadHeadNodeMetaFile(tenantDirectory, index));
        auto head = spann->GetMemoryIndex();
        BOOST_REQUIRE(head != nullptr);
        BOOST_REQUIRE(head->HasHeadNodeOwnTags());
        const SPTAG::SizeType headCount = head->GetNumSamples();
        BOOST_REQUIRE_EQUAL(head->GetHeadNodeMetaSampleCount(), headCount);
        for (SPTAG::SizeType local = 0; local < headCount; ++local)
        {
            const SPTAG::SizeType vid = head->GetHeadNodeGlobalVID(local);
            BOOST_REQUIRE(vid >= 0 && vid < kNumVectors);
            BOOST_CHECK_EQUAL(vid, spann->GetGlobalVID(local));
            const auto* ownTags = head->GetHeadNodeHierMask(local);
            BOOST_REQUIRE(ownTags != nullptr);
            BOOST_CHECK_EQUAL(ownTags->tag[0], tags[static_cast<size_t>(vid)]);
        }

        ScopedTempDir exported(MakeTempDir());
        const std::string exportedTenant = exported.path + "/tenant_0";
        const auto sourceFiles = SnapshotFiles(tenantDirectory);
        BOOST_REQUIRE(builder.SaveAll(exported.path.c_str()));
        CheckSavedFiles(sourceFiles, SnapshotFiles(tenantDirectory), graphless);
        CheckSavedFiles(sourceFiles, SnapshotFiles(exportedTenant), false);
        for (const auto& file : sourceFiles)
        {
            BOOST_CHECK(!std::filesystem::equivalent(
                std::filesystem::path(tenantDirectory) / file.first,
                std::filesystem::path(exportedTenant) / file.first));
        }
        {
            SPTAG::Helper::IniReader config;
            BOOST_REQUIRE(config.LoadIniFile(exportedTenant + "/indexloader.ini") ==
                          SPTAG::ErrorCode::Success);
            BOOST_CHECK_EQUAL(config.GetParameter("Base", "IndexDirectory", std::string()),
                              exportedTenant);
            BOOST_CHECK_EQUAL(config.GetParameter("SelectHead", "BuildH1Graph", graphless),
                              !graphless);
            BOOST_CHECK(!config.GetParameter("SelectHead", "CompactHierarchyVectors", true));
            BOOST_CHECK_EQUAL(config.GetParameter("SelectHead", "HierarchyLevels", 0), 3);
            BOOST_CHECK_EQUAL(config.GetParameter("BuildSSDIndex", "ReplicaCount", 0), 2);
            BOOST_CHECK(config.GetParameter("BuildSSDIndex", "EnableLimitedTagPosting", false));
            BOOST_CHECK_EQUAL(config.GetParameter("BuildSSDIndex", "UnfilterTailBufferLength", -1), 0);

            std::shared_ptr<SPTAG::VectorIndex> restored;
            BOOST_REQUIRE(SPTAG::VectorIndex::LoadIndex(exportedTenant, restored) ==
                          SPTAG::ErrorCode::Success);
            auto* restoredSPANN = dynamic_cast<SPTAG::SPANN::Index<float>*>(restored.get());
            BOOST_REQUIRE(restoredSPANN != nullptr);
            BOOST_REQUIRE(LoadHeadNodeMetaFile(exportedTenant, restored));
            const auto restoredHead = restoredSPANN->GetMemoryIndex();
            BOOST_REQUIRE(restoredHead != nullptr);
            BOOST_REQUIRE_EQUAL(restoredHead->GetNumSamples(), headCount);
            BOOST_REQUIRE_EQUAL(restoredHead->GetHeadNodeMetaSampleCount(), headCount);
            BOOST_REQUIRE(restoredSPANN->GetDiskIndex()->LimitedTagPostingRegionsReady());
            BOOST_REQUIRE_EQUAL(restoredSPANN->GetDiskIndex()->GetPostingCount(), headCount);
            for (SPTAG::SizeType local = 0; local < headCount; ++local)
            {
                const auto vid = restoredHead->GetHeadNodeGlobalVID(local);
                BOOST_REQUIRE_EQUAL(vid, head->GetHeadNodeGlobalVID(local));
                BOOST_CHECK_EQUAL(vid, restoredSPANN->GetGlobalVID(local));
                BOOST_REQUIRE(restoredHead->GetSample(local) != nullptr);
                BOOST_CHECK_EQUAL(std::memcmp(
                    restoredHead->GetSample(local),
                    vectors.data() + static_cast<size_t>(vid) * kDim,
                    kDim * sizeof(float)), 0);
                BOOST_REQUIRE(restoredHead->GetHeadNodeHierMask(local) != nullptr);
                BOOST_CHECK_EQUAL(restoredHead->GetHeadNodeHierMask(local)->tag[0],
                                  tags[static_cast<size_t>(vid)]);
            }
            BOOST_CHECK(PathExists(exportedTenant + "/SecondLevelHeadIndex/graph.bin"));
            BOOST_CHECK(!PathExists(exportedTenant + "/SecondLevelHeadIndex.level1.build"));
            if (graphless)
            {
                BOOST_CHECK(PathExists(exportedTenant + "/HeadIndex/head_metaonly.bin"));
                BOOST_CHECK(PathExists(exportedTenant + "/SPTAGHeadVectors.bin"));
                BOOST_CHECK(PathExists(exportedTenant + "/SPTAGSecondLevelHeadVectors.bin"));
                BOOST_CHECK(!PathExists(exportedTenant + "/SPTAGHeadVectors.bin.owned"));
                BOOST_CHECK(!PathExists(exportedTenant + "/SPTAGSecondLevelHeadVectors.bin.owned"));
                BOOST_CHECK(!PathExists(exportedTenant + "/SPTAGSecondLevelHeadVectors.bin.level2"));
                BOOST_CHECK(!PathExists(exportedTenant + "/SecondLevelHeadIndex/head_node_meta.bin"));
                BOOST_CHECK(!PathExists(exportedTenant + "/SecondLevelHeadIndex/metadata.bin"));
            }
            TenantIndexManager loaded(kDim, "SPANN", "Float");
            BOOST_REQUIRE(loaded.LoadAll(exported.path.c_str()));
            BOOST_REQUIRE_EQUAL(loaded.GetInternalTenantId("tenant0"), 0);
            auto result = loaded.SearchWithACL(
                ByteArray(reinterpret_cast<std::uint8_t*>(vectors.data()),
                          kDim * sizeof(float), false),
                0, 8,
                ByteArray(reinterpret_cast<std::uint8_t*>(tags.data()),
                          sizeof(std::uint32_t), false), 1);
            const auto ids = ExtractValidIds(result);
            BOOST_REQUIRE(!ids.empty());
            for (int vid : ids)
            {
                BOOST_REQUIRE(vid >= 0 && vid < kNumVectors);
                BOOST_CHECK_EQUAL(tags[static_cast<size_t>(vid)], tags[0]);
            }
            if (graphless)
            {
                TenantIndexManager unloaded(kDim, "SPANN", "Float");
                BOOST_REQUIRE(unloaded.LoadAll(exported.path.c_str()));
                ScopedTempDir coldExport(MakeTempDir());
                BOOST_REQUIRE(unloaded.SaveAll(coldExport.path.c_str()));
                std::shared_ptr<SPTAG::VectorIndex> coldReload;
                BOOST_REQUIRE(SPTAG::VectorIndex::LoadIndex(
                    coldExport.path + "/tenant_0", coldReload) == SPTAG::ErrorCode::Success);
                auto* coldSPANN = dynamic_cast<SPTAG::SPANN::ISPANNIndex*>(coldReload.get());
                BOOST_REQUIRE(coldSPANN != nullptr);
                BOOST_CHECK(coldSPANN->HasRoutingOnlyHierarchy());
                BOOST_REQUIRE(LoadHeadNodeMetaFile(coldExport.path + "/tenant_0", coldReload));
                BOOST_CHECK_EQUAL(coldSPANN->GetMemoryIndex()->GetHeadNodeMetaSampleCount(), headCount);
            }
        }

        if (graphless)
        {
            BOOST_REQUIRE(index->SaveIndex(tenantDirectory + "/.") == SPTAG::ErrorCode::Success);
            CheckSavedFiles(sourceFiles, SnapshotFiles(tenantDirectory), false);
            const auto savedInPlace = SnapshotFiles(tenantDirectory);
            BOOST_REQUIRE(index->SaveIndex(tenantDirectory) == SPTAG::ErrorCode::Success);
            CheckSavedFiles(savedInPlace, SnapshotFiles(tenantDirectory));
            BOOST_REQUIRE(index->SaveIndex(exportedTenant) == SPTAG::ErrorCode::Success);
            CheckSavedFiles(savedInPlace, SnapshotFiles(tenantDirectory));
            const auto publishedFiles = SnapshotFiles(exportedTenant);

            ScopedTempDir failures(MakeTempDir());
            const std::string missingExport = failures.path + "/missing";
            const std::string requiredRelative = "SPTAGHeadVectors.bin";
            const std::string required = tenantDirectory + "/" + requiredRelative;
            const std::vector<std::string> requiredArtifacts = {
                requiredRelative, "HeadIndex/deletes.bin",
                "SPTAGHeadVectorIDs.bin", "SecondLevelHeadIndex/graph.bin",
                "SPTAGSecondLevelHeadVectors.bin", "HeadIndex/head_node_meta.bin",
                spann->GetOptions()->m_secondLevelPostingFile};
            for (const auto& artifact : requiredArtifacts)
            {
                BOOST_TEST_CONTEXT("missing export artifact: " << artifact)
                {
                    const std::string path = tenantDirectory + "/" + artifact;
                    const std::string withheld = path + ".withheld";
                    std::filesystem::rename(path, withheld);
                    const auto missingFiles = SnapshotFiles(tenantDirectory);
                    BOOST_CHECK(index->SaveIndex(missingExport) != SPTAG::ErrorCode::Success);
                    BOOST_CHECK(!PathExists(missingExport));
                    BOOST_CHECK(index->SaveIndex(tenantDirectory) != SPTAG::ErrorCode::Success);
                    CheckSavedFiles(missingFiles, SnapshotFiles(tenantDirectory));
                    std::filesystem::rename(withheld, path);
                    CheckSavedFiles(savedInPlace, SnapshotFiles(tenantDirectory));
                }
            }

            const auto original = savedInPlace.at(requiredRelative);
            BOOST_REQUIRE(!original.empty());
            auto corrupt = original;
            corrupt[0] ^= 1;
            const auto writeArtifact = [&](const std::vector<char>& bytes)
            {
                std::ofstream output(required, std::ios::binary | std::ios::trunc);
                BOOST_REQUIRE(output.good());
                output.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
                output.close();
                BOOST_REQUIRE(output.good());
            };
            writeArtifact(corrupt);
            const auto corruptFiles = SnapshotFiles(tenantDirectory);
            BOOST_CHECK(index->SaveIndex(exportedTenant) != SPTAG::ErrorCode::Success);
            BOOST_CHECK(index->SaveIndex(tenantDirectory + "/.") != SPTAG::ErrorCode::Success);
            BOOST_CHECK(!builder.SaveAll(failures.path.c_str()));
            BOOST_CHECK(!PathExists(failures.path + "/manifest.txt"));
            BOOST_CHECK(!PathExists(failures.path + "/tenant_0/indexloader.ini"));
            BOOST_REQUIRE(std::filesystem::is_empty(failures.path + "/tenant_0"));
            BOOST_REQUIRE(std::filesystem::remove(failures.path + "/tenant_0"));
            CheckSavedFiles(corruptFiles, SnapshotFiles(tenantDirectory));
            CheckSavedFiles(publishedFiles, SnapshotFiles(exportedTenant));
            BOOST_CHECK_EQUAL(index->GetParameter("IndexDirectory", "Base"), tenantDirectory);
            writeArtifact(original);
            CheckSavedFiles(savedInPlace, SnapshotFiles(tenantDirectory));

            const std::string nestedExport = tenantDirectory + "/nested_export";
            BOOST_CHECK(index->SaveIndex(nestedExport) != SPTAG::ErrorCode::Success);
            BOOST_CHECK(!PathExists(nestedExport));
            CheckSavedFiles(savedInPlace, SnapshotFiles(tenantDirectory));
            const std::string upperMetadata = tenantDirectory + "/SecondLevelHeadIndex/head_node_meta.bin";
            {
                std::ofstream output(upperMetadata, std::ios::binary);
                output << "obsolete upper result metadata";
                BOOST_REQUIRE(output.good());
            }
            const auto withUpperMetadata = SnapshotFiles(tenantDirectory);
            const std::string metadataFreeExport = failures.path + "/metadata_free";
            BOOST_REQUIRE(index->SaveIndex(metadataFreeExport) == SPTAG::ErrorCode::Success);
            BOOST_CHECK(!PathExists(metadataFreeExport + "/SecondLevelHeadIndex/head_node_meta.bin"));
            CheckSavedFiles(withUpperMetadata, SnapshotFiles(tenantDirectory));
            std::filesystem::remove(upperMetadata);
            std::filesystem::remove_all(metadataFreeExport);
            CheckSavedFiles(savedInPlace, SnapshotFiles(tenantDirectory));
            BOOST_CHECK(std::filesystem::is_empty(failures.path));
        }

        std::uint64_t generation = 0;
        BOOST_REQUIRE(SPTAG::Helper::Convert::ConvertStringTo<std::uint64_t>(
            spann->GetOptions()->m_limitedTagGenerationFingerprint.c_str(), generation));
        if (graphless)
        {
            head->InitializeHeadNodeMeta(headCount + 1);
            BOOST_CHECK(!spann->PopulateHeadNodeGlobalVIDsFromBundles());
            BOOST_CHECK(!SaveHeadNodeMetaFile(tenantDirectory, head, generation));
            BOOST_REQUIRE(LoadHeadNodeMetaFile(tenantDirectory, index));
        }
        head->SetHeadNodeGlobalVID(0, SPTAG::MaxSize);
        BOOST_CHECK(!SaveHeadNodeMetaFile(tenantDirectory, head, generation));
        BOOST_REQUIRE(LoadHeadNodeMetaFile(tenantDirectory, index));
        BOOST_CHECK_EQUAL(head->GetHeadNodeGlobalVID(0), spann->GetGlobalVID(0));
    }
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
    builder.SetSSDBuildParam("HybridCandidateCount", "32");

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

BOOST_AUTO_TEST_CASE(SingleLabelIntegerCosineUsesNativeScale)
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
    const auto originalVectors = vectors;
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
        "TailReplicaCount", "0");
    builder.SetSSDBuildParam(
        "EnableUnfilterTail", "true");
    builder.SetSSDBuildParam(
        "UnfilterTailBufferLength", "0");
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
        "LimitedTagMinHeadCount", "1");

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
    BOOST_CHECK_EQUAL_COLLECTIONS(
        vectors.begin(), vectors.end(),
        originalVectors.begin(), originalVectors.end());
    BOOST_REQUIRE(
        builder.BuildSignatures(
            0, tagBytes, kNumVectors, 1));

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