// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
//
// Native C++ build entry for attribute-aware SPANN indexes.
//
// This is the C++ analog of the Python demo build_yfcc_facetA.py /
// build_5col.py: it drives the EXACT same attribute pipeline
// (TenantIndexManager::BuildFromDataWithTagsSingleTenant -> tag-view posting
// embedding + global spatial head selection + exact categorical/numeric
// signatures) but without the Python/SWIG layer. Native readers own vector IO;
// DEFAULT input can be mapped read-only without a duplicate corpus.
//
// Attribute/routing configuration comes from the native sectioned INI. Standard
// SPANN sections are staged directly into the core parameter system; a small
// set of wrapper-only routing extensions retains an internal INI-to-wrapper
// bridge until those extensions gain native option fields.
//
// Usage:
//   spannbuilder --vectors <file> [--vector-size <prefix>] [--dim <D>]
//     --value-type Int8|UInt8|Float --vector-type DEFAULT|TXT|XVEC
//     --tags <headerless-u32-file> --num-tags-per-vec <K>
//     --index-dir <out> [--storage-backend FILEIO|ROCKSDBIO]
//     [--build-signatures] [--normalized true|false]

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <algorithm>

#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/stat.h>
#ifdef __linux__
#include <sys/syscall.h>
#endif

#include "inc/CoreInterface.h"
#include "inc/Core/CommonDataStructure.h"
#include "inc/Core/VectorIndex.h"
#include "inc/Core/SPANN/Index.h"
#include "inc/Core/Common/IQuantizer.h"
#include "inc/Core/SPANN/PipePQ.h"
#include "inc/Helper/SimpleIniReader.h"
#include "inc/Helper/VectorSetReader.h"
#include "inc/Helper/NativeAttributeReader.h"
#include "inc/Helper/NpyAttributeReader.h"
#include <limits>

using namespace SPTAG;

namespace {

size_t ValueTypeSize(const std::string& vt) {
    VectorValueType type;
    return Helper::Convert::ConvertStringTo(vt.c_str(), type) ? GetValueTypeSize(type) : 0;
}

const char* ArgVal(int argc, char** argv, const char* key, const char* def) {
    for (int i = 1; i + 1 < argc; ++i) if (std::strcmp(argv[i], key) == 0) return argv[i + 1];
    return def;
}
bool ArgFlag(int argc, char** argv, const char* key) {
    for (int i = 1; i < argc; ++i) if (std::strcmp(argv[i], key) == 0) return true;
    return false;
}
bool HasArgument(int argc, char** argv, const char* key) {
    const size_t length = std::strlen(key);
    for (int i = 1; i < argc; ++i)
        if (std::strncmp(argv[i], key, length) == 0 &&
            (argv[i][length] == '\0' || argv[i][length] == '=')) return true;
    return false;
}

// --- Native .ini config resolution (mirrors classic IndexBuilder semantics) ---
// Precedence: an explicit CLI flag overrides the ini value, which overrides the
// built-in default. This keeps the config file the single source of truth while
// still allowing a one-off CLI override (exactly like the classic builder's
// `Section.Param=Value` overrides).
std::string Resolve(int argc, char** argv, const char* cliKey,
                    const Helper::IniReader* ini, const char* section, const char* key,
                    const char* def) {
    if (cliKey != nullptr)
        if (const char* c = ArgVal(argc, argv, cliKey, nullptr)) return std::string(c);
    if (ini && ini->DoesParameterExist(section, key))
        return ini->GetParameter<std::string>(section, key, std::string(def ? def : ""));
    return std::string(def ? def : "");
}
bool ResolveFlag(int argc, char** argv, const char* cliKey,
                 const Helper::IniReader* ini, const char* section, const char* key) {
    if (cliKey != nullptr && ArgFlag(argc, argv, cliKey)) return true;
    if (ini && ini->DoesParameterExist(section, key)) {
        std::string v = ini->GetParameter<std::string>(section, key, std::string("false"));
        return v == "1" || v == "true" || v == "True" || v == "yes" || v == "on";
    }
    return false;
}

int NativeInteger(const std::string& text, const char* key, int minimum)
{
    char* end = nullptr;
    errno = 0;
    const long long value = std::strtoll(text.c_str(), &end, 10);
    if (errno || end == text.c_str() || *end || value < minimum || value > MaxSize)
        throw std::runtime_error(std::string("Invalid ") + key + ": " + text);
    return static_cast<int>(value);
}

struct NativeVectorInput {
    std::shared_ptr<VectorSet> vectors;
    bool normalized;
    SizeType sourceRows;
};

NativeVectorInput ReadVectors(int argc, char** argv, const Helper::IniReader* ini,
                              const char* path, const std::string& valueType)
{
    VectorValueType element;
    VectorFileType container;
    const auto fileType = Resolve(argc, argv, "--vector-type", ini, "Base", "VectorType", "DEFAULT");
    if (!Helper::Convert::ConvertStringTo(valueType.c_str(), element))
        throw std::runtime_error("Invalid ValueType; expected Int8, UInt8, Int16 or Float");
    if (!Helper::Convert::ConvertStringTo(fileType.c_str(), container))
        throw std::runtime_error("Invalid VectorType; expected DEFAULT, TXT or XVEC. Legacy raw inputs must be migrated explicitly");
    const int dimension = NativeInteger(Resolve(argc, argv, "--dim", ini, "Base", "Dim", "0"), "Dim", 0);
    if (HasArgument(argc, argv, "--vector-size") &&
        ArgVal(argc, argv, "--vector-size", nullptr) == nullptr)
        throw std::runtime_error("Native --vector-size requires an integer value");
    const int limit = NativeInteger(Resolve(argc, argv, "--vector-size", ini, "Base", "VectorSize", "-1"), "VectorSize", -1);
    if (limit == 0 || (container != VectorFileType::DEFAULT && dimension == 0))
        throw std::runtime_error("VectorSize must be -1 or positive; TXT/XVEC require Dim");
    auto options = std::make_shared<Helper::ReaderOptions>(element, dimension, container,
        Resolve(argc, argv, "--vector-delimiter", ini, "Base", "VectorDelimiter", "|"));
    const char* normalizedArgument = ArgVal(argc, argv, "--normalized", nullptr);
    if (normalizedArgument == nullptr) normalizedArgument = ArgVal(argc, argv, "-norm", nullptr);
    if ((HasArgument(argc, argv, "--normalized") || HasArgument(argc, argv, "-norm")) &&
        normalizedArgument == nullptr)
        throw std::runtime_error("Native --normalized/-norm requires a boolean value");
    const std::string normalized = normalizedArgument != nullptr ? normalizedArgument :
        Resolve(argc, argv, nullptr, ini, "Base", "Normalized", "false");
    if (!Helper::Convert::ConvertStringTo(normalized.c_str(), options->m_normalized))
        throw std::runtime_error("Invalid native reader Normalized boolean");
    options->m_readOnlyMapped = true;
    auto reader = Helper::VectorSetReader::CreateInstance(options);
    if (!reader || reader->LoadFile(path) != ErrorCode::Success)
        throw std::runtime_error("Failed loading native vector input");
    auto vectors = reader->GetVectorSet(0, limit);
    if (!vectors || vectors->Count() <= 0)
        throw std::runtime_error("Native vector input is empty");
    return {vectors, reader->IsNormalized(), reader->SourceCount()};
}
} // namespace

int Run(int argc, char** argv) {
    if (!SPANN::Options::ValidateNativeEnvironment()) return 2;
    const bool tenantMaintenance =
        ArgFlag(argc, argv, "--compact-hierarchy") || ArgFlag(argc, argv, "--materialize-hierarchy") ||
        ArgFlag(argc, argv, "--inpost-opq-requantize") || ArgFlag(argc, argv, "--inpost-rbq-transform");
    if (!tenantMaintenance && HasArgument(argc, argv, "--tenant"))
        throw std::runtime_error("--tenant was removed from bulk input; the bulk model has native tenant 0");
    for (const char* removed : {"--routing-only", "--out-group", "--group-col", "--posting-quant-bits",
                                "--vec-offset", "--vector-offset", "--vector-count",
                                "--tag-offset", "--tags-offset", "--limited-tag-max-extra-supports",
                                "--sparse-fallback-max-heads", "--sparse-fallback-max-posting-pages",
                                "--static-acl-tag-cols", "--bkt-seed", "--tpt-seed",
                                "--hierarchy-signature-min-selectivity", "--hierarchy-signature-max-selectivity",
                                "--second-level-signature-min-selectivity", "--second-level-signature-max-selectivity",
                                "--with-meta-index", "--share-build-ownership"}) {
        if (HasArgument(argc, argv, removed)) {
            fprintf(stderr, "[spannbuilder] %s was removed; use canonical native options.\n", removed);
            return 2;
        }
        if (HasArgument(argc, argv, "--n") && !ArgFlag(argc, argv, "--merge-tags5")) {
            fprintf(stderr, "[spannbuilder] --n was removed for vectors; use native --vector-size prefix (-1 = all).\n");
            return 2;
        }
    }
    // --- Same-stride PipePQ -> OPQ posting rewrite for a cloned index ---
    // The clone's native indexloader.ini must set PostingQuantizer=OPQ,
    // PostingQuantM to the source PipePQ width, RequantizeFromPipePQ=true, and
    // point PostingQuantizerFile at an M-byte-per-vector OPQ code sidecar.
    if (ArgFlag(argc, argv, "--inpost-opq-requantize")) {
        const char* indexDir = ArgVal(argc, argv, "--index-dir", nullptr);
        const int dim = (int)std::strtol(ArgVal(argc, argv, "--dim", "0"), nullptr, 10);
        const std::string valueType = ArgVal(argc, argv, "--value-type", "UInt8");
        const int tenant = (int)std::strtol(ArgVal(argc, argv, "--tenant", "0"), nullptr, 10);
        if (!indexDir || dim <= 0) {
            fprintf(stderr, "usage: spannbuilder --inpost-opq-requantize --index-dir <d> "
                            "--dim <D> [--value-type UInt8] [--tenant 0]\n");
            return 2;
        }
        const size_t valSize = ValueTypeSize(valueType);
        if (valSize == 0) { fprintf(stderr, "[spannbuilder] bad value-type\n"); return 2; }
        fprintf(stderr, "[spannbuilder] inpost-opq-requantize: load %s ...\n", indexDir);
        {
            TenantIndexManager mgr(dim, "SPANN", valueType.c_str());
            if (!mgr.LoadAll(indexDir)) {
                fprintf(stderr, "[spannbuilder] LoadAll FAILED\n");
                return 1;
            }
            // Lazy tenant construction executes the native-INI requantization before
            // this query reaches the normal search path.
            std::vector<std::uint8_t> q((size_t)dim * valSize, 0);
            ByteArray query(q.data(), q.size(), false);
            ByteArray noTags(nullptr, 0, false);
            if (!mgr.SearchWithPredicate(query, tenant, 10, noTags, 0)) {
                fprintf(stderr, "[spannbuilder] requantization query FAILED\n");
                return 1;
            }
        }

        const std::string markerPath = std::string(indexDir) + "/tenant_" +
            std::to_string(tenant) + "/inpost_opq.bin";
        int marker[2] = { 0, 0 };
        std::ifstream markerIn(markerPath, std::ios::binary);
        markerIn.read(reinterpret_cast<char*>(marker), sizeof(marker));
        if (!markerIn || marker[0] <= 0 || marker[1] <= 0) {
            fprintf(stderr, "[spannbuilder] requantization marker missing or invalid: %s\n",
                    markerPath.c_str());
            return 1;
        }

        // Reload after the first manager is destroyed so success proves that the
        // checkpointed mapping, posting store, checksums, and OPQ marker are coherent.
        {
            TenantIndexManager verifier(dim, "SPANN", valueType.c_str());
            if (!verifier.LoadAll(indexDir)) {
                fprintf(stderr, "[spannbuilder] post-requantization reload FAILED\n");
                return 1;
            }
            std::vector<std::uint8_t> q((size_t)dim * valSize, 0);
            ByteArray query(q.data(), q.size(), false);
            ByteArray noTags(nullptr, 0, false);
            if (!verifier.SearchWithPredicate(query, tenant, 10, noTags, 0)) {
                fprintf(stderr, "[spannbuilder] post-requantization query FAILED\n");
                return 1;
            }
        }
        fprintf(stderr, "[spannbuilder] inpost-opq-requantize complete.\n");
        return 0;
    }

    // --- Post-build slim transform mode (C++ analog of build_inpost_rbq2_contig.py) ---
    // Rewrites an existing index's full postings to slim [meta | RaBitQ2-code] stored
    // contiguously, driven by the same SPTAG_INPOST_RBQ* env the Python demo sets, then
    // exits. Requires the RBQ2 code sidecar (from rabitq2_encode_stream) inside the
    // index's tenant_0/ dir. Query-time uses SPTAG_INPOST_RBQ + SPTAG_INPOST_BASE.
    if (ArgFlag(argc, argv, "--inpost-rbq-transform")) {
        const char* indexDir = ArgVal(argc, argv, "--index-dir", nullptr);
        const char* rbqFile = ArgVal(argc, argv, "--rbq-file", nullptr);
        const int dim = (int)std::strtol(ArgVal(argc, argv, "--dim", "0"), nullptr, 10);
        const std::string valueType = ArgVal(argc, argv, "--value-type", "Int8");
        const int tenant = (int)std::strtol(ArgVal(argc, argv, "--tenant", "0"), nullptr, 10);
        if (!indexDir || !rbqFile || dim <= 0) {
            fprintf(stderr, "usage: spannbuilder --inpost-rbq-transform --index-dir <d> "
                            "--rbq-file <rabitq2.bin> --dim <D> [--value-type Int8] [--tenant 0]\n");
            return 2;
        }
        const size_t valSize = ValueTypeSize(valueType);
        if (valSize == 0) { fprintf(stderr, "[spannbuilder] bad value-type\n"); return 2; }
        // The transform runs lazily inside the ExtraDynamicSearcher ctor on first query.
        setenv("SPTAG_INPOST_RBQ", "1", 1);
        setenv("SPTAG_INPOST_RBQ_FILE", rbqFile, 1);
        setenv("SPTAG_INPOST_RBQ_BUILD", "1", 1);
        setenv("SPTAG_INPOST_RBQ_CONTIG", "1", 1);
        fprintf(stderr, "[spannbuilder] inpost-rbq-transform: load %s (rbq=%s) ...\n", indexDir, rbqFile);
        TenantIndexManager mgr(dim, "SPANN", valueType.c_str());
        if (!mgr.LoadAll(indexDir)) { fprintf(stderr, "[spannbuilder] LoadAll FAILED\n"); return 1; }
        // Dummy query forces lazy per-tenant searcher construction -> TransformInPostingsRbqContig.
        std::vector<std::uint8_t> q((size_t)dim * valSize, 0);
        ByteArray query(q.data(), q.size(), false);
        ByteArray noTags(nullptr, 0, false);
        mgr.SearchWithPredicate(query, tenant, 10, noTags, 0);
        fprintf(stderr, "[spannbuilder] inpost-rbq-transform complete.\n");
        return 0;
    }

    // --- OPQ code generation mode (C++ analog of AnnService/src/Quantizer/main.cpp) ---
    // Encodes native vector input into the in-posting OPQ code sidecar
    // (opq_codes_m<M>.bin) that the SPANN build consumes (config
    // [BuildSSDIndex] PostingQuantizerFile). Mimics the original Quantizer main
    // (IQuantizer::LoadIQuantizer + per-vector QuantizeVector) but follows the
    // in-posting convention that ExtraDynamicSearcher uses when it self-encodes
    // (ExtraDynamicSearcher.h ~5165): widen the vector to float WITHOUT
    // normalization and call QuantizeVector(vf, code, /*ADC=*/false), writing the
    // raw, header-less N*M uint8 codes vid-indexed. NOTE: the generic
    // Release/quantizer tool normalizes and emits an (n,d) header, so it is NOT a
    // drop-in for this sidecar -- use this mode instead.
    if (ArgFlag(argc, argv, "--gen-opq-codes")) {
        const char* vectors  = ArgVal(argc, argv, "--vectors", nullptr);
        const char* quantF   = ArgVal(argc, argv, "--quantizer", nullptr);
        const char* outF     = ArgVal(argc, argv, "--out", nullptr);
        const int   threads  = (int)std::strtol(ArgVal(argc, argv, "--threads", "1"), nullptr, 10);
        const std::string valueType = ArgVal(argc, argv, "--value-type", "Int8");
        if (!vectors || !quantF || !outF || threads <= 0) {
            fprintf(stderr, "usage: spannbuilder --gen-opq-codes --vectors <base.i8bin> "
                            "--quantizer <opq_quantizer.bin> --out <opq_codes_m<M>.bin> "
                            "[--dim <D>] [--vector-type DEFAULT|TXT|XVEC] [--vector-size <count>] [--threads 1] "
                            "[--value-type Int8]\n");
            return 2;
        }
        const size_t valSize = ValueTypeSize(valueType);
        if (valSize == 0) { fprintf(stderr, "[spannbuilder] bad value-type\n"); return 2; }
        auto vectorSet = ReadVectors(argc, argv, nullptr, vectors, valueType).vectors;
        const int dim = vectorSet->Dimension();

        // Load the OPQ quantizer exactly like Quantizer/main.cpp.
        auto fp = SPTAG::f_createIO();
        if (fp == nullptr || !fp->Initialize(quantF, std::ios::binary | std::ios::in)) {
            fprintf(stderr, "[spannbuilder] cannot open quantizer: %s\n", quantF); return 1;
        }
        auto quantizer = SPTAG::COMMON::IQuantizer::LoadIQuantizer(fp);
        if (!quantizer) { fprintf(stderr, "[spannbuilder] failed to load quantizer\n"); return 1; }
        quantizer->SetEnableADC(false);
        const int M = quantizer->GetNumSubvectors();
        if (quantizer->ReconstructDim() != dim) {
            fprintf(stderr, "[spannbuilder] OPQ quantizer dimension %d does not match requested dimension %d\n",
                    quantizer->ReconstructDim(), dim);
            return 1;
        }
        fprintf(stderr, "[spannbuilder][gen-opq-codes] M=%d dim=%d valueType=%s\n",
                M, dim, valueType.c_str());
        const size_t recBytes = (size_t)dim * valSize;
        const long N = vectorSet->Count();
        const char* basePtr = static_cast<const char*>(vectorSet->GetData());

        const size_t outputBytes = static_cast<size_t>(N) * M;
        const int outFd = open(outF, O_RDWR | O_CREAT | O_TRUNC, 0644);
        if (outFd < 0) { fprintf(stderr, "[spannbuilder] cannot open out: %s\n", outF); return 1; }
        if (ftruncate(outFd, static_cast<off_t>(outputBytes)) != 0) {
            fprintf(stderr, "[spannbuilder] cannot size out: %s\n", outF);
            close(outFd);
            return 1;
        }
        void* outputMap = mmap(nullptr, outputBytes, PROT_READ | PROT_WRITE, MAP_SHARED, outFd, 0);
        if (outputMap == MAP_FAILED) {
            fprintf(stderr, "[spannbuilder] cannot mmap out: %s\n", outF);
            close(outFd);
            return 1;
        }
        auto* codes = reinterpret_cast<std::uint8_t*>(outputMap);
        fprintf(stderr,
                "[spannbuilder][gen-opq-codes] encoding %ld vectors -> %s (%zu bytes) with %d threads\n",
                N, outF, outputBytes, threads);

        const long chunkSize = 1L << 14;
        std::atomic<long> nextVector(0);
        auto encodeChunk = [&]() {
            std::vector<float> vf(static_cast<size_t>(dim));
            while (true) {
                const long start = nextVector.fetch_add(chunkSize);
                if (start >= N) break;
                const long end = std::min<long>(start + chunkSize, N);
                for (long i = start; i < end; ++i) {
                    const char* rec = basePtr + static_cast<size_t>(i) * recBytes;
                    // Widen raw values without normalization; this is the same
                    // convention used by the in-posting OPQ build path.
                    if (vectorSet->GetValueType() == VectorValueType::Float) {
                        const auto* v = reinterpret_cast<const float*>(rec);
                        for (int d = 0; d < dim; ++d) vf[d] = v[d];
                    } else if (valSize == 2) {
                        const auto* v = reinterpret_cast<const std::int16_t*>(rec);
                        for (int d = 0; d < dim; ++d) vf[d] = static_cast<float>(v[d]);
                    } else if (vectorSet->GetValueType() == VectorValueType::UInt8) {
                        const auto* v = reinterpret_cast<const std::uint8_t*>(rec);
                        for (int d = 0; d < dim; ++d) vf[d] = static_cast<float>(v[d]);
                    } else {
                        const auto* v = reinterpret_cast<const std::int8_t*>(rec);
                        for (int d = 0; d < dim; ++d) vf[d] = static_cast<float>(v[d]);
                    }
                    quantizer->QuantizeVector(vf.data(), codes + static_cast<size_t>(i) * M,
                                               /*ADC=*/false);
                }
            }
        };
        std::vector<std::thread> workers;
        workers.reserve(static_cast<size_t>(threads));
        for (int worker = 0; worker < threads; ++worker) workers.emplace_back(encodeChunk);
        for (auto& worker : workers) worker.join();
        if (msync(outputMap, outputBytes, MS_SYNC) != 0) {
            fprintf(stderr, "[spannbuilder] failed to flush out: %s\n", outF);
            munmap(outputMap, outputBytes);
            close(outFd);
            return 1;
        }
        munmap(outputMap, outputBytes);
        close(outFd);
        fprintf(stderr, "\n[spannbuilder][gen-opq-codes] done.\n");
        return 0;
    }

    // --- PipeANN fixed-chunk PQ code generation mode ---
    // Uses the PipeANN pq_pivots.bin format and writes a raw, header-less N*M
    // uint8 sidecar consumed by PostingQuantizer=PipePQ. Existing PipeANN
    // compressed.bin files ([uint32 N][uint32 M] + codes) are accepted directly
    // by the SPANN build/search path and are the authoritative choice for
    // byte-identical PipeANN code assignment; this helper is for same-algorithm
    // experiments where rare BLAS rounding ties need not match byte-for-byte.
    if (ArgFlag(argc, argv, "--gen-pipepq-codes")) {
        const char* vectors = ArgVal(argc, argv, "--vectors", nullptr);
        const char* pivots = ArgVal(argc, argv, "--pivots", nullptr);
        const char* outF = ArgVal(argc, argv, "--out", nullptr);
        const int M = (int)std::strtol(ArgVal(argc, argv, "--posting-quant-m", "0"), nullptr, 10);
        const std::string valueType = ArgVal(argc, argv, "--value-type", "Int8");
        if (!vectors || !pivots || !outF || M <= 0) {
            fprintf(stderr, "usage: spannbuilder --gen-pipepq-codes --vectors <base.i8bin> "
                            "--pivots <pipeann_pq_pivots.bin> --out <pipepq_codes_m<M>.bin> "
                            "[--dim <D>] --posting-quant-m <M> [--vector-type DEFAULT|TXT|XVEC] [--vector-size <count>] "
                            "[--value-type Int8]\n");
            return 2;
        }
        const size_t valSize = ValueTypeSize(valueType);
        if (valSize == 0) { fprintf(stderr, "[spannbuilder] bad value-type\n"); return 2; }
        auto vectorSet = ReadVectors(argc, argv, nullptr, vectors, valueType).vectors;
        const int dim = vectorSet->Dimension();

        SPTAG::SPANN::PipePQTable table;
        if (!table.Load(pivots, M) || table.Dim() != dim) {
            fprintf(stderr, "[spannbuilder][gen-pipepq-codes] failed to load pivots=%s dim=%d M=%d\n",
                    pivots, dim, M);
            return 1;
        }

        const size_t recBytes = (size_t)dim * valSize;
        const long N = vectorSet->Count();
        const char* basePtr = static_cast<const char*>(vectorSet->GetData());

        FILE* out = std::fopen(outF, "wb");
        if (!out) { fprintf(stderr, "[spannbuilder] cannot open out: %s\n", outF); return 1; }
        fprintf(stderr, "[spannbuilder][gen-pipepq-codes] encoding %ld vectors -> %s (%ld bytes), M=%d\n",
                N, outF, (long)N * M, M);

        const size_t CHUNK = 1u << 16;
        std::vector<std::uint8_t> codes(CHUNK * (size_t)M);
        std::vector<float> vf((size_t)dim);
        for (long s = 0; s < N; s += (long)CHUNK) {
            const long e = std::min<long>(s + (long)CHUNK, N);
            for (long i = s; i < e; ++i) {
                const char* rec = basePtr + (size_t)i * recBytes;
                if (vectorSet->GetValueType() == VectorValueType::Float) {
                    const float* v = reinterpret_cast<const float*>(rec);
                    for (int d = 0; d < dim; ++d) vf[d] = v[d];
                } else if (valSize == 2) {
                    const std::int16_t* v = reinterpret_cast<const std::int16_t*>(rec);
                    for (int d = 0; d < dim; ++d) vf[d] = (float)v[d];
                } else if (vectorSet->GetValueType() == VectorValueType::UInt8) {
                    const std::uint8_t* v = reinterpret_cast<const std::uint8_t*>(rec);
                    for (int d = 0; d < dim; ++d) vf[d] = (float)v[d];
                } else {
                    const std::int8_t* v = reinterpret_cast<const std::int8_t*>(rec);
                    for (int d = 0; d < dim; ++d) vf[d] = (float)v[d];
                }
                table.Encode(vf.data(), &codes[(size_t)(i - s) * M]);
            }
            std::fwrite(codes.data(), 1, (size_t)(e - s) * M, out);
            fprintf(stderr, "\r[spannbuilder][gen-pipepq-codes] %ld/%ld", e, N);
        }
        std::fclose(out);
        fprintf(stderr, "\n[spannbuilder][gen-pipepq-codes] done.\n");
        return 0;
    }

    // --- Tag-merge mode: build the builder's 5-col tag sidecar from the dataset's
    //     .npy attribute arrays (C++, no Python). Reads tags.npy [N,acl] uint32 +
    //     num_attr.npy [N] int32 and writes:
    //       --out-tags5  : raw uint32 [N, acl+1] = [acl cols | numeric], row-major
    //     (.npy v1.0: magic 6B + ver 2B + hlen(u16) + header; data at 10+hlen.) ---
    if (ArgFlag(argc, argv, "--merge-tags5")) {
        const char* tagsNpy = ArgVal(argc, argv, "--tags-npy", nullptr);
        const char* numNpy  = ArgVal(argc, argv, "--num-npy", nullptr);
        const char* outT    = ArgVal(argc, argv, "--out-tags5", nullptr);
        for (const char* key : {"--acl-cols", "--n"})
            if (HasArgument(argc, argv, key) && !ArgVal(argc, argv, key, nullptr))
                throw std::runtime_error(std::string(key) + " requires an integer value");
        const int aclCols = NativeInteger(ArgVal(argc, argv, "--acl-cols", "4"), "acl-cols", 1);
        const long nArg = NativeInteger(ArgVal(argc, argv, "--n", "-1"), "n", -1);
        if (nArg == 0 || aclCols == MaxSize)
            throw std::runtime_error("Invalid NPY merge count/column width");
        if (!tagsNpy || !numNpy || !outT || aclCols <= 0) {
            fprintf(stderr, "usage: spannbuilder --merge-tags5 --tags-npy <tags.npy> "
                            "--num-npy <num_attr.npy> --out-tags5 <tags5.u32> "
                            "[--acl-cols 4] [--n <count>]\n");
            return 2;
        }
        const auto mt = Helper::ReadNpyAttributes(tagsNpy, aclCols, false);
        const auto mn = Helper::ReadNpyAttributes(numNpy, 1, true);
        if (mt.rows != mn.rows || nArg > mt.rows)
            throw std::runtime_error("NPY row counts disagree or requested prefix exceeds source");
        const long N = nArg < 0 ? mt.rows : nArg;
        Helper::NativeAttributeBytes(N, static_cast<std::uint64_t>(aclCols) + 1);
        const auto* tags = reinterpret_cast<const std::uint32_t*>(mt.data.Data());
        const auto* nums = reinterpret_cast<const std::int32_t*>(mn.data.Data());
        for (long i = 0; i < N; ++i)
            if (nums[i] < 0) throw std::runtime_error("Numeric NPY values must fit native uint32 without signed wraparound");
        if (std::filesystem::exists(outT) &&
            (std::filesystem::equivalent(outT, tagsNpy) || std::filesystem::equivalent(outT, numNpy)))
            throw std::runtime_error("NPY merge output must not overwrite its inputs");
        fprintf(stderr, "[spannbuilder][merge-tags5] N=%ld categoricalCols=%d -> recordCols=%d\n",
                N, aclCols, aclCols + 1);

        FILE* fT = std::fopen(outT, "wb");
        if (!fT) { fprintf(stderr, "[spannbuilder] cannot open output\n"); return 1; }
        const int W = aclCols + 1;
        const size_t CHUNK = std::max<size_t>(1, (1u << 20) / W);
        std::vector<std::uint32_t> rowbuf(CHUNK * (size_t)W);
        for (long s = 0; s < N; s += (long)CHUNK) {
            const long e = std::min<long>(s + (long)CHUNK, N);
            for (long i = s; i < e; ++i) {
                std::uint32_t* dst = &rowbuf[(size_t)(i - s) * W];
                const std::uint32_t* src = &tags[(size_t)i * aclCols];
                for (int c = 0; c < aclCols; ++c) dst[c] = src[c];
                dst[aclCols] = (std::uint32_t)nums[i];
            }
            const size_t count = (size_t)(e - s) * W;
            if (std::fwrite(rowbuf.data(), sizeof(std::uint32_t), count, fT) != count) {
                std::fclose(fT);
                throw std::runtime_error("Failed writing native tag merge output");
            }
            fprintf(stderr, "\r[spannbuilder][merge-tags5] %ld/%ld", e, N);
        }
        if (std::fclose(fT) != 0) throw std::runtime_error("Failed closing native tag merge output");
        fprintf(stderr, "\n[spannbuilder][merge-tags5] done.\n");
        return 0;
    }

    // --- Native SPANN .ini config (single source of truth, classic-builder style) ---
    // -c/--config <file.ini> loads all build parameters from a sectioned ini via
    // the same Helper::IniReader the classic IndexBuilder uses. Native
    // [SelectHead], [BuildHead], and [BuildSSDIndex] values are staged directly
    // into the index options. Wrapper-only routing extensions stay in
    // [MultiTenant]. Explicit CLI flags still override the INI where supported.
    Helper::IniReader iniStore;
    const Helper::IniReader* ini = nullptr;
    {
        const char* cfg = ArgVal(argc, argv, "--config", nullptr);
        if (!cfg) cfg = ArgVal(argc, argv, "-c", nullptr);
        if (cfg) {
            if (iniStore.LoadIniFile(cfg) != ErrorCode::Success) {
                fprintf(stderr, "[spannbuilder] cannot open config file: %s\n", cfg);
                return 2;
            }
            ini = &iniStore;
            fprintf(stderr, "[spannbuilder] config = %s\n", cfg);
            for (const char* section : {"MultiTenant", "SelectHead", "BuildHead",
                                        "BuildSSDIndex", "SearchSSDIndex", "Base", "Tags", "Build"}) {
                for (const auto& kv : ini->GetParameters(section)) {
                    if (Helper::StrUtils::StrEqualIgnoreCase(kv.first.c_str(), "StaticACLTagCols")) {
                        fprintf(stderr, "[spannbuilder] Use [Tags] ColumnTypes to specify every original attribute column.\n");
                        return 2;
                    }
                    if (Helper::StrUtils::StrEqualIgnoreCase(kv.first.c_str(), "ColumnTypes") &&
                        !Helper::StrUtils::StrEqualIgnoreCase(section, "Tags"))
                        throw std::runtime_error("Specify ColumnTypes only in [Tags]");
                    if (Helper::StrUtils::StrEqualIgnoreCase(kv.first.c_str(), "VectorOffset") ||
                        Helper::StrUtils::StrEqualIgnoreCase(kv.first.c_str(), "VectorCount") ||
                        Helper::StrUtils::StrEqualIgnoreCase(kv.first.c_str(), "WithMetaIndex") ||
                        Helper::StrUtils::StrEqualIgnoreCase(kv.first.c_str(), "ShareBuildOwnership") ||
                        (!tenantMaintenance && Helper::StrUtils::StrEqualIgnoreCase(section, "Tags") &&
                         Helper::StrUtils::StrEqualIgnoreCase(kv.first.c_str(), "Tenant")) ||
                        (!tenantMaintenance &&
                         (Helper::StrUtils::StrEqualIgnoreCase(section, "BuildSSDIndex") ||
                          Helper::StrUtils::StrEqualIgnoreCase(section, "SearchSSDIndex")) &&
                         Helper::StrUtils::StrEqualIgnoreCase(kv.first.c_str(), "NumTagsPerVec")) ||
                        SPANN::Options::IsRemovedParameter(kv.first.c_str()) ||
                        SPANN::Options::IsRemovedSectionAlias(section, kv.first.c_str()) ||
                        ((Helper::StrUtils::StrEqualIgnoreCase(kv.first.c_str(), "SelectHeadType") ||
                          Helper::StrUtils::StrEqualIgnoreCase(kv.first.c_str(), "SelectType")) &&
                         Helper::StrUtils::StrEqualIgnoreCase(kv.second.c_str(), "PerTagBKT"))) {
                        fprintf(stderr, "[spannbuilder] [%s] %s was removed; use canonical native options.\n",
                                section, kv.first.c_str());
                        return 2;
                    }
                }
            }
            SPANN::Options hierarchyOptions;
            for (const auto& kv : ini->GetParameters("SelectHead")) {
                const char* canonical = SPANN::Options::CanonicalParameter("SelectHead", kv.first.c_str());
                if (!Helper::StrUtils::StrEqualIgnoreCase(canonical, kv.first.c_str()) &&
                    ini->DoesParameterExist("SelectHead", canonical)) continue;
                if (hierarchyOptions.SetParameter("SelectHead", canonical, kv.second.c_str()) != ErrorCode::Success)
                    return 2;
            }
            if (!hierarchyOptions.ValidateHierarchyRatio()) return 2;
            if (hierarchyOptions.m_selectSecondLevel &&
                (hierarchyOptions.m_secondLevelHierarchyLevels < 2 || hierarchyOptions.m_headVectorCount != 0)) {
                fprintf(stderr, "[spannbuilder] HierarchyLevels must be >=2 and Count must be 0; use Ratio.\n");
                return 2;
            }

        }
    }

    std::string sVecPath    = Resolve(argc, argv, "--vectors",          ini, "Base", "VectorPath",      nullptr);
    std::string sTagPath    = Resolve(argc, argv, "--tags",             ini, "Tags", "TagFile",         nullptr);
    std::string sIndexDir   = Resolve(argc, argv, "--index-dir",        ini, "Base", "IndexDirectory",  nullptr);
    const char* vecPath  = sVecPath.empty()  ? nullptr : sVecPath.c_str();
    const char* tagPath  = sTagPath.empty()  ? nullptr : sTagPath.c_str();
    const char* indexDir = sIndexDir.empty() ? nullptr : sIndexDir.c_str();
    if (ArgFlag(argc, argv, "--compact-hierarchy")) {
        fprintf(stderr, "[spannbuilder] --compact-hierarchy is retired; use --materialize-hierarchy with a NEW output root.\n");
        return 2;
    }
    const bool materializeHierarchy = ArgFlag(argc, argv, "--materialize-hierarchy");
    if (materializeHierarchy) {
        const char* outputRoot = ArgVal(argc, argv, "--output-index-dir", nullptr);
        if (!indexDir || ArgFlag(argc, argv, "--build-signatures-only") ||
            ArgFlag(argc, argv, "--backfill-primary-head-csr") ||
            outputRoot == nullptr || *outputRoot == '\0') {
            fprintf(stderr, "[spannbuilder] Hierarchy maintenance requires IndexDirectory "
                "and exactly one maintenance command. --materialize-hierarchy also requires "
                "--output-index-dir pointing to a NEW output root.\n");
            return 2;
        }
        const std::string tenantText = Resolve(
            argc, argv, "--tenant", ini, "Tags", "Tenant", "0");
        char* end = nullptr;
        const long tenantID = std::strtol(tenantText.c_str(), &end, 10);
        if (end == tenantText.c_str() || *end != '\0' ||
            tenantID < 0 || tenantID >= SPTAG::MaxSize) {
            fprintf(stderr, "[spannbuilder] Invalid hierarchy maintenance tenant.\n");
            return 2;
        }
        const std::string tenantDirectory =
            sIndexDir + "/tenant_" + std::to_string(tenantID);
        std::shared_ptr<SPTAG::VectorIndex> index;
        if (SPTAG::VectorIndex::LoadIndex(tenantDirectory, index) !=
                SPTAG::ErrorCode::Success || index == nullptr) {
            fprintf(stderr, "[spannbuilder] Hierarchy maintenance LoadIndex FAILED: %s\n",
                tenantDirectory.c_str());
            return 1;
        }
        auto* spann = dynamic_cast<SPTAG::SPANN::ISPANNIndex*>(index.get());
        if (spann == nullptr) return 1;
        namespace fs = std::filesystem;
        struct StagedOutput {
            fs::path path;
            ~StagedOutput() {
                if (!path.empty()) {
                    std::error_code ignored;
                    fs::remove_all(path, ignored);
                }
            }
        } staged;
        try {
            const fs::path source = fs::canonical(sIndexDir);
            const fs::path destination = fs::weakly_canonical(fs::absolute(outputRoot));
            const auto isWithin = [](const fs::path& child, const fs::path& parent) {
                auto part = child.begin();
                for (auto ancestor = parent.begin(); ancestor != parent.end(); ++ancestor, ++part)
                    if (part == child.end() || *part != *ancestor) return false;
                return true;
            };
            if (fs::exists(destination) || isWithin(source, destination) || isWithin(destination, source)) {
                fprintf(stderr, "[spannbuilder] Output root must be new and non-overlapping.\n");
                return 2;
            }
            std::ifstream sourceManifest(source / "manifest.txt");
            if (!sourceManifest) {
                fprintf(stderr, "[spannbuilder] Cannot read source tenant manifest.\n");
                return 1;
            }
            bool foundTenant = false;
            std::string tenantMapping;
            std::string line;
            while (std::getline(sourceManifest, line)) {
                std::istringstream fields(line);
                std::string key;
                long mappedTenant = -1;
                if (!(fields >> key >> mappedTenant) || mappedTenant != tenantID) continue;
                if (key == "tenant") {
                    std::int64_t vectorCount = -1, globalOffset = -1, headCount = -1, type = -1;
                    if (foundTenant || !(fields >> vectorCount >> globalOffset >> headCount >> type) ||
                        vectorCount != index->GetNumSamples() ||
                        headCount != spann->GetMemoryIndex()->GetNumSamples()) {
                        fprintf(stderr, "[spannbuilder] Source tenant manifest disagrees with native index.\n");
                        return 1;
                    }
                    foundTenant = true;
                } else if (key == "tenant_mapping") {
                    std::string externalID, extra;
                    if (!tenantMapping.empty() || !(fields >> externalID) || fields >> extra) {
                        fprintf(stderr, "[spannbuilder] Invalid source tenant identity mapping.\n");
                        return 1;
                    }
                    tenantMapping = "tenant_mapping " + std::to_string(tenantID) + " " + externalID;
                }
            }
            if (!sourceManifest.eof() || !foundTenant || tenantMapping.empty()) {
                fprintf(stderr, "[spannbuilder] Source manifest must identify the selected tenant "
                    "and its external mapping; use the native per-tenant API for a standalone index.\n");
                return 1;
            }
            fs::create_directories(destination.parent_path());
            for (int attempt = 0; attempt < 32 && staged.path.empty(); ++attempt) {
                fs::path candidate = destination;
                candidate += ".materializing-root." +
                    std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) +
                    "." + std::to_string(attempt);
                if (fs::create_directory(candidate)) staged.path = candidate;
            }
            if (staged.path.empty()) return 1;
            const std::string tenantName = "tenant_" + std::to_string(tenantID);
            if (spann->MaterializeHierarchyVectors((staged.path / tenantName).string()) != ErrorCode::Success) {
                fprintf(stderr, "[spannbuilder] MATERIALIZE-HIERARCHY FAILED; source unchanged.\n");
                return 1;
            }
            const std::string stagedTenant = (staged.path / tenantName).string();
            const std::string configPath = stagedTenant + "/indexloader.ini";
            const std::string temporaryConfig = configPath + ".root-publish";
            Helper::IniReader tenantConfig;
            std::shared_ptr<VectorIndex> outputIndex;
            if (tenantConfig.LoadIniFile(configPath) != ErrorCode::Success ||
                VectorIndex::LoadIndex(stagedTenant, outputIndex) != ErrorCode::Success ||
                outputIndex == nullptr ||
                outputIndex->SetParameter("IndexDirectory",
                    (destination / tenantName).string().c_str(), "Base") != ErrorCode::Success) {
                fprintf(stderr, "[spannbuilder] Cannot prepare the published native tenant configuration.\n");
                return 1;
            }
            auto configOutput = f_createIO();
            if (configOutput == nullptr || !configOutput->Initialize(temporaryConfig.c_str(), std::ios::out)) {
                fprintf(stderr, "[spannbuilder] Cannot stage the published native tenant configuration.\n");
                return 1;
            }
            std::string prefix;
            for (const char* section : {"MetaData", "Index"}) {
                if (!tenantConfig.DoesSectionExist(section)) continue;
                prefix += std::string("[") + section + "]\n";
                for (const auto& parameter : tenantConfig.GetParameters(section))
                    prefix += parameter.first + "=" + parameter.second + "\n";
                prefix += "\n";
            }
            const bool configWritten =
                configOutput->WriteString(prefix.c_str()) == prefix.size() &&
                outputIndex->SaveConfig(configOutput) == ErrorCode::Success;
            const bool configClosed = configOutput->ShutDownAndCheck();
            outputIndex.reset();
            if (!configWritten || !configClosed || !Helper::AtomicReplaceFile(temporaryConfig, configPath)) {
                fprintf(stderr, "[spannbuilder] Cannot persist the published native tenant configuration.\n");
                return 1;
            }
            const fs::path manifestPath = staged.path / "manifest.txt";
            const fs::path stagedManifest = staged.path / "manifest.txt.tmp";
            std::ofstream manifest(stagedManifest);
            manifest << "dimension " << index->GetFeatureDim()
                     << "\nalgorithm SPANN\nunified_storage 1\ntotal_postings "
                     << spann->GetMemoryIndex()->GetNumSamples()
                     << "\ntenant " << tenantID << ' ' << index->GetNumSamples() << " 0 "
                     << spann->GetMemoryIndex()->GetNumSamples() << " 0\n"
                     << tenantMapping << '\n';
            manifest.close();
            if (!manifest ||
                !Helper::AtomicReplaceFile(stagedManifest.string(), manifestPath.string()) ||
                !Helper::SyncDirectoryTree(staged.path.string())) return 1;
#if defined(__linux__) && defined(SYS_renameat2)
            constexpr unsigned int renameNoReplace = 1U;
            if (syscall(SYS_renameat2, AT_FDCWD, staged.path.c_str(), AT_FDCWD,
                    destination.c_str(), renameNoReplace) != 0)
                throw fs::filesystem_error("Cannot publish hierarchy output root",
                    staged.path, destination, std::error_code(errno, std::generic_category()));
#else
            if (!fs::create_directory(destination)) return 1;
            std::error_code publishError;
            fs::rename(staged.path, destination, publishError);
            if (publishError) {
                std::error_code cleanupError;
                fs::remove(destination, cleanupError);
                return 1;
            }
#endif
            staged.path.clear();
            if (!Helper::SyncParentDirectory(destination.string())) return 1;
            fprintf(stderr, "[spannbuilder] MATERIALIZE-HIERARCHY done: %s/%s "
                "(source/H1 V8/SSD/sample IDs preserved; CSR signatures conservatively repaired).\n",
                destination.string().c_str(), tenantName.c_str());
            return 0;
        } catch (const fs::filesystem_error& error) {
            fprintf(stderr, "[spannbuilder] MATERIALIZE-HIERARCHY failed: %s\n", error.what());
            return 1;
        }
    }
    if (!vecPath || !indexDir) {
        fprintf(stderr,
            "usage: spannbuilder -c <config.ini>   (native SPANN ini, single source of truth)\n"
            "   or: spannbuilder --vectors <f> --vector-type DEFAULT|TXT|XVEC [--vector-size <N>] [--dim <D>] "
            "--value-type Int8|UInt8|Float [--tags <headerless-u32-file> "
            "--column-types categorical,numeric] --index-dir <out> "
            "[--storage-backend FILEIO|ROCKSDBIO] [--build-signatures] "
            "[--normalized true|false] "
            "[--posting-quantizer None|RaBitQ|OPQ|PipePQ] [--posting-quant-m <B>] "
            "[--posting-quant-bits <b>] [--posting-quant-file <f>] "
            "[--full-vector-file <f>] [--rerank-l <L>] [--quantize-head] [--quant-adc-only] "
            "[--ssd-start-file-gb <GB>] [--ssd-max-file-gb <GB>] [--ssd-growth-file-gb <GB>] "
            "[--backfill-primary-head-csr] [--compact-hierarchy] "
            "[--materialize-hierarchy --output-index-dir <new-root>]\n");
        return 2;
    }

    if (HasArgument(argc, argv, "--num-tags-per-vec") &&
        !ArgVal(argc, argv, "--num-tags-per-vec", nullptr))
        throw std::runtime_error("--num-tags-per-vec requires an integer value");
    const std::string columnTypes = Resolve(argc, argv, "--column-types",
        ini, "Tags", "ColumnTypes", "");
    if (tagPath && columnTypes.empty())
        throw std::runtime_error("TagFile requires [Tags] ColumnTypes for every original column");
    const auto tagSchema = TagSchema::Parse(columnTypes, columnTypes.empty() ? 0 : -1);
    const int numTagsPerVec = NativeInteger(Resolve(argc, argv, "--num-tags-per-vec",
        ini, "Tags", "NumTagsPerVec", std::to_string(tagSchema.Width()).c_str()), "NumTagsPerVec", 0);
    if (numTagsPerVec != tagSchema.Width())
        throw std::runtime_error("NumTagsPerVec must equal the ColumnTypes width");
    for (const char* section : {"BuildSSDIndex", "SearchSSDIndex"}) {
        if (tagSchema.Width() > 0 &&
            ((ini && ini->DoesParameterExist(section, "LimitedTagColumn")) ||
             (Helper::StrUtils::StrEqualIgnoreCase(section, "BuildSSDIndex") &&
              ResolveFlag(argc, argv, nullptr, ini, "BuildSSDIndex", "EnableLimitedTagPosting")))) {
            const int key = NativeInteger(Resolve(argc, argv, nullptr, ini, section,
                "LimitedTagColumn", "0"), "LimitedTagColumn", 0);
            if (!tagSchema.IsCategorical(key))
                throw std::runtime_error("LimitedTagColumn must identify a categorical original column");
            if (Helper::StrUtils::StrEqualIgnoreCase(section, "SearchSSDIndex") &&
                key != NativeInteger(Resolve(argc, argv, nullptr, ini, "BuildSSDIndex",
                    "LimitedTagColumn", "0"), "LimitedTagColumn", 0))
                throw std::runtime_error("Search overlay cannot change the constructed tag key column");
        }
    }
    constexpr int tenant = 0;
    const std::string valueType     = Resolve(argc, argv, "--value-type",      ini, "Base", "ValueType", "Float");
    const std::string storageBackend= Resolve(argc, argv, "--storage-backend", ini, "BuildSSDIndex", "Storage", "FILEIO");
    const bool buildSignatures = ResolveFlag(argc, argv, "--build-signatures", ini, "Build", "BuildSignatures");
    const std::string distCalcMethod =
        Resolve(argc, argv, "--dist-calc-method", ini, "Base", "DistCalcMethod", "Cosine");
    DistCalcMethod metric;
    if (!Helper::Convert::ConvertStringTo(distCalcMethod.c_str(), metric))
        throw std::runtime_error("Invalid DistCalcMethod");
    const bool hasTags = tagPath != nullptr || numTagsPerVec > 0;
    if (!hasTags && (buildSignatures || ArgFlag(argc, argv, "--build-signatures-only")))
        throw std::runtime_error("BuildSignatures requires tags; no-tag/unfiltered builds can omit it");

    const size_t valSize = ValueTypeSize(valueType);
    if (valSize == 0 || (hasTags && (tagPath == nullptr || numTagsPerVec <= 0))) {
        fprintf(stderr, "[spannbuilder] invalid value-type/dim/tag configuration\n");
        return 2;
    }
    if (std::getenv("SPTAG_BUILD_SHARE_OWNERSHIP"))
        throw std::runtime_error("SPTAG_BUILD_SHARE_OWNERSHIP was removed from bulk input; ownership is derived automatically");
    auto input = ReadVectors(argc, argv, ini, vecPath, valueType);
    auto vectorSet = input.vectors;
    const bool normalized = input.normalized;
    const int dim = vectorSet->Dimension();
    const long long n = vectorSet->Count();
    const size_t vecBytes = (size_t)n * dim * valSize;
    ByteArray tags = hasTags ? Helper::ReadNativeAttributes(
        tagPath, vectorSet->Count(), input.sourceRows, numTagsPerVec) : ByteArray();

    fprintf(stderr,
        "[spannbuilder] N=%lld dim=%d valueType=%s tagsPerVec=%d tenant=%d backend=%s\n"
        "               vectors=%s (native reader, %.2f GB)  tags=%s (headerless uint32)\n"
        "               buildSignatures=%d index-dir=%s\n",
        n, dim, valueType.c_str(), numTagsPerVec, tenant, storageBackend.c_str(),
        vecPath, vecBytes / 1e9, hasTags ? tagPath : "<none>",
        (int)buildSignatures, indexDir);

    // vectorSet owns the native reader's mapping/allocation through build and save.
    std::uint8_t* vecPtr = static_cast<std::uint8_t*>(vectorSet->GetData());
    ByteArray vectors(vecPtr, vecBytes, false);

    if (ArgFlag(argc, argv, "--build-signatures-only") ||
        ArgFlag(argc, argv, "--backfill-primary-head-csr")) {
        Helper::IniReader saved;
        const std::string savedPath = sIndexDir + "/tenant_" + std::to_string(tenant) + "/indexloader.ini";
        if (saved.LoadIniFile(savedPath) != ErrorCode::Success ||
            saved.GetParameter<int>("Base", "Dim", 0) != dim ||
            saved.GetParameter<int>("BuildSSDIndex", "NumTagsPerVec", 0) != numTagsPerVec ||
            saved.GetParameter<int>("BuildSSDIndex", "LimitedTagColumn", 0) !=
                NativeInteger(Resolve(argc, argv, nullptr, ini, "BuildSSDIndex",
                    "LimitedTagColumn", "0"), "LimitedTagColumn", 0) ||
            TagSchema::Parse(saved.GetParameter<std::string>("BuildSSDIndex", "ColumnTypes", ""),
                numTagsPerVec, saved.GetParameter<int>("BuildSSDIndex", "StaticACLTagCols", 0)).text != tagSchema.text ||
            !Helper::StrUtils::StrEqualIgnoreCase(
                saved.GetParameter<std::string>("Base", "ValueType", "").c_str(), valueType.c_str()))
            throw std::runtime_error("Native input dimension/ValueType/attribute schema disagrees with saved index");
    }

    TenantIndexManager mgr(dim, "SPANN", valueType.c_str());
    mgr.SetSSDBuildParam("ColumnTypes", tagSchema.text.c_str());
    if (storageBackend != "FILEIO") mgr.SetStorageBackend(storageBackend.c_str());

    // Native build sections are staged before BuildFromDataWithTags creates the
    // tenant index. TenantIndexManager applies them after its automatic defaults,
    // so an explicit INI value always wins over a size-based heuristic.
    if (ini) {
        for (const char* name : {
                 "DistCalcMethod", "IndexAlgoType",
                 "SSDIndex"}) {
            if (!ini->DoesParameterExist("Base", name)) continue;
            const std::string value = ini->GetParameter<std::string>(
                "Base", name, std::string());
            mgr.SetBuildParam(name, value.c_str(), "Base");
            fprintf(stderr, "[spannbuilder][cfg] [Base] %s = %s\n", name, value.c_str());
        }

        const bool hasNativeSelectType =
            ini->DoesParameterExist("SelectHead", "SelectHeadType");
        for (const auto& kv : ini->GetParameters("SelectHead")) {
            std::string name = SPANN::Options::CanonicalParameter("SelectHead", kv.first.c_str());
            if (!Helper::StrUtils::StrEqualIgnoreCase(name.c_str(), kv.first.c_str()) &&
                ini->DoesParameterExist("SelectHead", name.c_str())) continue;
            if (SPTAG::Helper::StrUtils::StrEqualIgnoreCase(name.c_str(), "SelectType")) {
                if (hasNativeSelectType) {
                    fprintf(stderr,
                            "[spannbuilder][cfg] ignoring deprecated [SelectHead] SelectType; "
                            "SelectHeadType is also set\n");
                    continue;
                }
                name = "SelectHeadType";
            }
            mgr.SetBuildParam(name.c_str(), kv.second.c_str(), "SelectHead");
            fprintf(stderr, "[spannbuilder][cfg] [SelectHead] %s = %s\n",
                    name.c_str(), kv.second.c_str());
        }
        for (const auto& kv : ini->GetParameters("BuildHead")) {
            mgr.SetBuildParam(kv.first.c_str(), kv.second.c_str(), "BuildHead");
            fprintf(stderr, "[spannbuilder][cfg] [BuildHead] %s = %s\n",
                    kv.first.c_str(), kv.second.c_str());
        }

    }

    // The native metric also determines whether the bulk path needs a mutable copy.
    mgr.SetBuildParam("DistCalcMethod", distCalcMethod.c_str(), "Base");

    // Native [BuildSSDIndex] section: apply every param through the SPANN parameter
    // system (SetSSDBuildParam -> staged m_extraSSDBuildParams -> SetBuildParam at
    // build, CoreInterface.cpp). This is the same mechanism the classic IndexBuilder
    // uses, so ReplicaCount / PostingPageLimit / StartFileSizeGB / MaxFileSizeGB /
    // GrowthFileSizeGB / PostingQuantizer / PostingQuantM /
    // PostingQuantizerFile / FullVectorFile / RerankL / QuantizeHead / QuantADCOnly
    // all flow from the ini. ("Storage" is handled above via SetStorageBackend.)
    if (ini) {
        for (const auto& kv : ini->GetParameters("BuildSSDIndex")) {
            if (kv.first == "storage") continue;
            const char* canonical = SPANN::Options::CanonicalParameter("BuildSSDIndex", kv.first.c_str());
            if (!Helper::StrUtils::StrEqualIgnoreCase(canonical, kv.first.c_str()) &&
                ini->DoesParameterExist("BuildSSDIndex", canonical)) continue;
            mgr.SetSSDBuildParam(canonical, kv.second.c_str());
            fprintf(stderr, "[spannbuilder][cfg] [BuildSSDIndex] %s = %s\n",
                    kv.first.c_str(), kv.second.c_str());
        }

        // Queue native runtime parameters before tenant construction. The manager
        // applies them only after each SPANN tenant finishes building and before
        // its first Save, so they cannot change construction behavior.
        for (const auto& kv : ini->GetParameters("SearchSSDIndex")) {
            const char* canonical = SPANN::Options::CanonicalParameter("SearchSSDIndex", kv.first.c_str());
            if (!Helper::StrUtils::StrEqualIgnoreCase(canonical, kv.first.c_str()) &&
                ini->DoesParameterExist("SearchSSDIndex", canonical)) continue;
            mgr.SetSearchParam(canonical, kv.second.c_str(), "SearchSSDIndex");
            fprintf(stderr, "[spannbuilder][cfg] queued [SearchSSDIndex] %s = %s\n",
                    kv.first.c_str(), kv.second.c_str());
        }
    }

    // In-posting quantization config (unified, env-free). Explicit CLI flags override
    // the ini (pushed after the [BuildSSDIndex] loop -> applied later -> win).
    {
        const char* pq = ArgVal(argc, argv, "--posting-quantizer", nullptr);   // None|RaBitQ|OPQ|PipePQ
        if (pq) mgr.SetSSDBuildParam("PostingQuantizer", pq);
        const char* pqm = ArgVal(argc, argv, "--posting-quant-m", nullptr);     // OPQ code bytes
        if (pqm) mgr.SetSSDBuildParam("PostingQuantM", pqm);
        const char* pqf = ArgVal(argc, argv, "--posting-quant-file", nullptr);  // code sidecar
        if (pqf) mgr.SetSSDBuildParam("PostingQuantizerFile", pqf);
        const char* fvf = ArgVal(argc, argv, "--full-vector-file", nullptr);    // cold-rerank base
        if (fvf) mgr.SetSSDBuildParam("FullVectorFile", fvf);
        const char* rl = ArgVal(argc, argv, "--rerank-l", nullptr);             // rerank depth
        if (rl) mgr.SetSSDBuildParam("RerankL", rl);
        if (ArgFlag(argc, argv, "--quantize-head")) mgr.SetSSDBuildParam("QuantizeHead", "true");
        if (ArgFlag(argc, argv, "--quant-adc-only")) mgr.SetSSDBuildParam("QuantADCOnly", "true");
        // Explicit SSD block-pool sizing (GB). When set, these pin the pre-alloc /
        // growth ceiling exactly and bypass the auto estimate (which is sized for
        // billion-scale slim postings but kept conservative). Keeping the disk
        // budget in the build script makes it visible and reproducible.
        const char* sfs = ArgVal(argc, argv, "--ssd-start-file-gb", nullptr);
        if (sfs) mgr.SetSSDBuildParam("StartFileSizeGB", sfs);
        const char* mfs = ArgVal(argc, argv, "--ssd-max-file-gb", nullptr);
        if (mfs) mgr.SetSSDBuildParam("MaxFileSizeGB", mfs);
        const char* gfs = ArgVal(argc, argv, "--ssd-growth-file-gb", nullptr);
        if (gfs) mgr.SetSSDBuildParam("GrowthFileSizeGB", gfs);
    }

    fprintf(stderr, "[spannbuilder] %s ...\n",
            hasTags ? "BuildFromDataWithTagsSingleTenant"
                    : "BuildFromDataSingleTenant");
    if (ArgFlag(argc, argv, "--build-signatures-only")) {
        if (!hasTags) {
            fprintf(stderr, "[spannbuilder] BUILD-SIGNATURES-ONLY requires tags\n");
            return 2;
        }
        if (!mgr.LoadAll(indexDir) || mgr.GetTenantVectorCount(tenant) != n) {
            fprintf(stderr, "[spannbuilder] BUILD-SIGNATURES-ONLY LoadAll FAILED\n");
            return 1;
        }
        if (!mgr.BuildSignatures(
                tenant, tags, static_cast<int>(n),
                numTagsPerVec)) {
            fprintf(stderr, "[spannbuilder] BUILD-SIGNATURES-ONLY BuildSignatures FAILED\n");
            return 1;
        }
        fprintf(stderr, "[spannbuilder] BUILD-SIGNATURES-ONLY done.\n");
        return 0;
    }
    bool ok = hasTags
        ? mgr.BuildFromDataWithTagsSingleTenant(
              vectors, tenant, static_cast<SizeType>(n), tags,
              numTagsPerVec, false, normalized)
        : mgr.BuildFromDataSingleTenant(
              vectors, tenant, static_cast<SizeType>(n),
              false, normalized);
    if (!ok) {
        fprintf(stderr, "[spannbuilder] %s FAILED\n",
                hasTags ? "BuildFromDataWithTagsSingleTenant"
                        : "BuildFromDataSingleTenant");
        return 1;
    }

    if (buildSignatures) {
        if (!hasTags) {
            fprintf(stderr, "[spannbuilder] BuildSignatures requires tags\n");
            return 2;
        }
        fprintf(stderr, "[spannbuilder] BuildSignatures (numeric quant) ...\n");
        if (!mgr.BuildSignatures(
                tenant, tags, (SizeType)n,
                numTagsPerVec)) {
            fprintf(stderr, "[spannbuilder] BuildSignatures FAILED\n");
            return 1;
        }
    }

    fprintf(stderr, "[spannbuilder] SaveAll -> %s\n", indexDir);
    if (!mgr.SaveAll(indexDir)) {
        fprintf(stderr, "[spannbuilder] SaveAll FAILED\n");
        return 1;
    }
    fprintf(stderr, "[spannbuilder] done.\n");
    return 0;
}

int main(int argc, char** argv)
{
    try {
        return Run(argc, argv);
    } catch (const std::exception& error) {
        fprintf(stderr, "[spannbuilder] %s\n", error.what());
        return 2;
    }
}
