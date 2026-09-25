// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#pragma once

#include "inc/Core/Cache/PostingSignature.h"
#include "inc/Helper/AtomicFile.h"
#include <array>
#include <cstdio>
#include <fstream>
#include <limits>
#include <string>
#include <vector>

namespace SPTAG { namespace SPANN {
constexpr std::uint32_t kLegacyNumericMetaMagic =
    0x54454D4EU; // NMET
constexpr std::uint32_t kNumericMetaMagic =
    0x324D554EU; // NUM2
constexpr std::uint32_t kNumericMetaVersion = 2;

struct NumericMetaFileHeader {
    std::uint32_t magic = kNumericMetaMagic;
    std::uint32_t version = kNumericMetaVersion;
    std::int32_t numBaseColumns = 0;
    std::int32_t numNumericColumns = 0;
    std::int32_t vectorCount = 0;
    std::int32_t tagColumnCount = 0;
    std::uint64_t generationFingerprint = 0;
    std::uint64_t contentFingerprint = 0;
};

static_assert(sizeof(NumericMetaFileHeader) == 40,
              "Unexpected NumericMetaFileHeader layout");

struct NumericMetaDiskData {
    int numBaseColumns = 0;
    int vectorCount = 0;
    int tagColumnCount = 0;
    std::uint64_t generationFingerprint = 0;
    std::uint64_t contentFingerprint = 0;
    bool generationBound = false;
    std::vector<SPTAG::Cache::NumQuantParam> params;
};

inline std::uint64_t NumericMetaFingerprint(
    const NumericMetaFileHeader& header,
    const std::vector<SPTAG::Cache::NumQuantParam>& params)
{
    constexpr std::uint64_t offset =
        1469598103934665603ULL;
    constexpr std::uint64_t prime =
        1099511628211ULL;
    std::uint64_t hash = offset;
    const auto append =
        [&](const void* data, size_t bytes) {
            const auto* begin =
                static_cast<const std::uint8_t*>(data);
            for (size_t i = 0; i < bytes; ++i) {
                hash ^= begin[i];
                hash *= prime;
            }
        };
    append(&header.numBaseColumns,
           sizeof(header.numBaseColumns));
    append(&header.numNumericColumns,
           sizeof(header.numNumericColumns));
    append(&header.vectorCount,
           sizeof(header.vectorCount));
    append(&header.tagColumnCount,
           sizeof(header.tagColumnCount));
    append(&header.generationFingerprint,
           sizeof(header.generationFingerprint));
    if (!params.empty()) {
        append(params.data(),
               params.size() * sizeof(params[0]));
    }
    return hash;
}

inline std::uint64_t ComputeNumericMetaContentFingerprint(
    int numBaseColumns,
    int vectorCount,
    int tagColumnCount,
    std::uint64_t generationFingerprint,
    const std::vector<SPTAG::Cache::NumQuantParam>&
        params)
{
    NumericMetaFileHeader header;
    header.numBaseColumns = numBaseColumns;
    header.numNumericColumns =
        static_cast<std::int32_t>(params.size());
    header.vectorCount = vectorCount;
    header.tagColumnCount = tagColumnCount;
    header.generationFingerprint =
        generationFingerprint;
    return NumericMetaFingerprint(header, params);
}

inline bool SaveNumericMetaFile(
    const std::string& path,
    int numBaseColumns,
    int vectorCount,
    int tagColumnCount,
    std::uint64_t generationFingerprint,
    const std::vector<SPTAG::Cache::NumQuantParam>&
        params,
    std::uint64_t* contentFingerprint = nullptr)
{
    const std::int64_t totalColumns =
        static_cast<std::int64_t>(numBaseColumns) +
        static_cast<std::int64_t>(params.size());
    if (numBaseColumns < 0 ||
        params.empty() ||
        params.size() >
            static_cast<size_t>(
                (std::numeric_limits<std::int32_t>::max)()) ||
        vectorCount <= 0 ||
        tagColumnCount <= numBaseColumns ||
        totalColumns !=
            tagColumnCount) {
        return false;
    }
    for (const auto& param : params) {
        if (param.lo > param.hi) return false;
    }

    NumericMetaFileHeader header;
    header.numBaseColumns = numBaseColumns;
    header.numNumericColumns =
        static_cast<std::int32_t>(params.size());
    header.vectorCount = vectorCount;
    header.tagColumnCount = tagColumnCount;
    header.generationFingerprint =
        generationFingerprint;
    header.contentFingerprint =
        ComputeNumericMetaContentFingerprint(
            numBaseColumns, vectorCount,
            tagColumnCount, generationFingerprint,
            params);
    if (contentFingerprint != nullptr) {
        *contentFingerprint =
            header.contentFingerprint;
    }

    const std::string temporary = path + ".tmp";
    FILE* file = std::fopen(temporary.c_str(), "wb");
    if (file == nullptr) return false;
    bool ok =
        std::fwrite(&header, sizeof(header), 1, file) ==
            1 &&
        std::fwrite(
            params.data(), sizeof(params[0]),
            params.size(), file) == params.size();
    if (std::fclose(file) != 0) ok = false;
    if (!ok ||
        !SPTAG::Helper::AtomicReplaceFile(
            temporary, path)) {
        std::remove(temporary.c_str());
        return false;
    }
    return true;
}

inline bool LoadNumericMetaFile(
    const std::string& path,
    NumericMetaDiskData& metadata)
{
    metadata = NumericMetaDiskData();
    std::ifstream input(path, std::ios::binary);
    if (!input.good()) return false;
    input.seekg(0, std::ios::end);
    const std::streamoff fileBytes = input.tellg();
    input.seekg(0, std::ios::beg);
    std::uint32_t magic = 0;
    input.read(
        reinterpret_cast<char*>(&magic), sizeof(magic));
    if (!input.good()) return false;
    input.seekg(0, std::ios::beg);

    if (magic == kNumericMetaMagic) {
        NumericMetaFileHeader header;
        input.read(
            reinterpret_cast<char*>(&header),
            sizeof(header));
        const std::int64_t totalColumns =
            static_cast<std::int64_t>(
                header.numBaseColumns) +
            static_cast<std::int64_t>(
                header.numNumericColumns);
        if (!input.good() ||
            header.version != kNumericMetaVersion ||
            header.numBaseColumns < 0 ||
            header.numNumericColumns <= 0 ||
            header.vectorCount <= 0 ||
            header.tagColumnCount <=
                header.numBaseColumns ||
            totalColumns !=
                header.tagColumnCount) {
            return false;
        }
        const std::uint64_t expectedBytes =
            sizeof(header) +
            static_cast<std::uint64_t>(
                header.numNumericColumns) *
                sizeof(
                    SPTAG::Cache::NumQuantParam);
        if (fileBytes < 0 ||
            static_cast<std::uint64_t>(fileBytes) !=
                expectedBytes) {
            return false;
        }
        metadata.params.resize(
            static_cast<size_t>(
                header.numNumericColumns));
        input.read(
            reinterpret_cast<char*>(
                metadata.params.data()),
            static_cast<std::streamsize>(
                metadata.params.size() *
                sizeof(metadata.params[0])));
        if (!input.good() ||
            header.contentFingerprint !=
                NumericMetaFingerprint(
                    header, metadata.params)) {
            return false;
        }
        for (const auto& param : metadata.params) {
            if (param.lo > param.hi) return false;
        }
        metadata.numBaseColumns =
            header.numBaseColumns;
        metadata.vectorCount = header.vectorCount;
        metadata.tagColumnCount =
            header.tagColumnCount;
        metadata.generationFingerprint =
            header.generationFingerprint;
        metadata.contentFingerprint =
            header.contentFingerprint;
        metadata.generationBound = true;
        return true;
    }

    if (magic != kLegacyNumericMetaMagic) {
        return false;
    }
    std::array<std::int32_t, 3> legacyHeader{};
    input.read(
        reinterpret_cast<char*>(legacyHeader.data()),
        static_cast<std::streamsize>(
            sizeof(legacyHeader)));
    if (!input.good() ||
        legacyHeader[1] < 0 ||
        legacyHeader[2] <= 0) {
        return false;
    }
    const std::uint64_t expectedBytes =
        sizeof(legacyHeader) +
        static_cast<std::uint64_t>(legacyHeader[2]) *
            sizeof(SPTAG::Cache::NumQuantParam);
    if (fileBytes < 0 ||
        static_cast<std::uint64_t>(fileBytes) !=
            expectedBytes) {
        return false;
    }
    const std::int64_t legacyTagColumns =
        static_cast<std::int64_t>(
            legacyHeader[1]) +
        static_cast<std::int64_t>(
            legacyHeader[2]);
    if (legacyTagColumns >
        (std::numeric_limits<int>::max)()) {
        return false;
    }
    metadata.params.resize(
        static_cast<size_t>(legacyHeader[2]));
    input.read(
        reinterpret_cast<char*>(
            metadata.params.data()),
        static_cast<std::streamsize>(
            metadata.params.size() *
            sizeof(metadata.params[0])));
    if (!input.good()) return false;
    for (const auto& param : metadata.params) {
        if (param.lo > param.hi) return false;
    }
    metadata.numBaseColumns = legacyHeader[1];
    metadata.tagColumnCount =
        static_cast<int>(legacyTagColumns);
    return true;
}
}}
