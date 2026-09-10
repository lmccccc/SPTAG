// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.

#ifndef _SPTAG_SPANN_OPTIONS_H_
#define _SPTAG_SPANN_OPTIONS_H_

#include "inc/Core/Common.h"
#include "inc/Core/TagSchema.h"
#include "inc/Helper/StringConvert.h"
#include "inc/Helper/CommonHelper.h"
#include "inc/Helper/KeyValueIO.h"
#include <memory>
#include <string>
#include <cmath>

namespace SPTAG {
    namespace SPANN {

        class Options
        {
        public:
            VectorValueType m_valueType;
            DistCalcMethod m_distCalcMethod;
            IndexAlgoType m_indexAlgoType;
            DimensionType m_dim;
            std::string m_vectorPath;
            VectorFileType m_vectorType;
            SizeType m_vectorSize; //Optional on condition
            std::string m_vectorDelimiter; //Optional on condition
            std::string m_queryPath;
            VectorFileType m_queryType;
            SizeType m_querySize; //Optional on condition
            std::string m_queryDelimiter; //Optional on condition
            std::string m_warmupPath;
            VectorFileType m_warmupType;
            SizeType m_warmupSize; //Optional on condition
            std::string m_warmupDelimiter; //Optional on condition
            std::string m_truthPath;
            TruthFileType m_truthType;
            bool m_generateTruth;
            std::string m_indexDirectory;
            std::string m_headIDFile;
            std::string m_headVectorFile;
            std::string m_headIndexFolder;
            std::string m_deleteIDFile;
            std::string m_ssdIndex;
            bool m_deleteHeadVectors;
            int m_ssdIndexFileNum;
            std::string m_quantizerFilePath;
            int m_datasetRowsInBlock;
            int m_datasetCapacity;

            // Section 2: for selecting head
            bool m_selectHead;
            int m_iTreeNumber;
            int m_iBKTKmeansK;
            int m_iBKTLeafSize;
            int m_iSamples;
            float m_fBalanceFactor;
            int m_iSelectHeadNumberOfThreads;
            bool m_saveBKT;
            // analyze
            bool m_analyzeOnly;
            bool m_calcStd;
            bool m_selectDynamically;
            bool m_noOutput;
            // selection factors
            int m_selectThreshold;
            int m_splitFactor;
            int m_splitThreshold;
            int m_maxRandomTryCount;
            double m_ratio;
            int m_headVectorCount;
            bool m_recursiveCheckSmallCluster;
            bool m_printSizeCount;
            std::string m_selectType;
            int m_minHeadsPerTag;
            bool m_dualPoolAugment;
            double m_dualPoolExtraRatio;
            std::string m_uExtraIDFile;
            bool m_parallelBKTBuild;
            bool m_selectSecondLevel;
            int m_secondLevelHierarchyLevels;
            // Read-only legacy constraint, never used for selection or serialized.
            std::string m_legacyHierarchyRatio;
            std::string m_secondLevelHeadVectorFile;
            std::string m_secondLevelHeadIDFile;
            int m_secondLevelReplicaCount;
            std::string m_secondLevelHeadIndexFolder;
            std::string m_secondLevelPostingFile;
            std::string m_secondLevelGenerationFingerprint;
            double m_secondLevelInitialProbeRatio;
            int m_secondLevelMaxCheck;
            bool m_secondLevelGraphSignaturePruning;
            std::string m_secondLevelPrefetchMode;
            std::string m_headNavigationMode;

            // Section 3: for build head
            bool m_buildHead;
            bool m_buildH1Graph;
            bool m_compactHierarchyVectors; // Legacy V2 option: accepted on load, rejected by fresh builds.

            // Section 4: for build ssd and search ssd
            bool m_enableSSD;
            bool m_buildSsdIndex;
            int m_iSSDNumberOfThreads;
            bool m_enableDeltaEncoding;
            bool m_enablePostingListRearrange;
            bool m_enableOrderedPageStart;
            std::string m_orderedPageStartAttrs;
            bool m_enableHybridDistance;
            std::string m_hybridGenerationFingerprint;
            bool m_enableLimitedTagPosting;
            std::string m_limitedTagGenerationFingerprint;
            std::string m_limitedTagSupportFile;
            int m_limitedTagColumn;
            int m_limitedTagSlotsPerHead;
            int m_limitedTagMinHeadCount;
            bool m_enableLimitedTagSupportExpansion;
            float m_hybridVectorWeight;
            std::string m_hybridCategoricalCols;
            std::string m_hybridCategoricalWeights;
            std::string m_hybridNumericCols;
            std::string m_hybridNumericWeights;
            int m_hybridCandidateCount;
            int m_hybridRouteSampleCount;
            float m_hybridRouteSelectivityThreshold;
            float m_hybridRouteDeformationThreshold;
            bool m_logHybridRoute;
            bool m_enableDataCompression;
            bool m_enableDictTraining;
            int m_minDictTraingBufferSize;
            int m_dictBufferCapacity;
            int m_zstdCompressLevel;

            // Building
            int m_replicaCount;
            int m_tailReplicaCount;
            int m_postingPageLimit;
            int m_internalResultNum;
            bool m_outputEmptyReplicaID;
            int m_batches;
            std::string m_tmpdir;
            float m_rngFactor;
            int m_samples;
            bool m_excludehead;
            int m_postingVectorLimit;
            std::string m_fullDeletedIDFile;
            Storage m_storage;
            std::string m_KVFile;
            std::string m_ssdMappingFile;
            std::string m_ssdInfoFile;
            std::string m_checksumFile;
            std::string m_postingPureCountsFile;
            bool m_useDirectIO;
            bool m_preReassign;
            float m_preReassignRatio;
            bool m_enableWAL;
            bool m_disableCheckpoint;
            std::string m_headRoleFile;

            // Per-vector tags embedded in posting metadata
            int m_numTagsPerVec;
            std::string m_columnTypes;
            int m_tagSchemaVersion;
            std::string m_tagSchemaFingerprint;
            const TagSchema& Schema() const {
                if (m_cachedSchemaText != m_columnTypes || m_cachedSchemaWidth != m_numTagsPerVec ||
                    m_cachedLegacyColumns != m_staticACLTagCols) {
                    m_cachedSchema = TagSchema::Parse(m_columnTypes, m_numTagsPerVec, m_staticACLTagCols);
                    m_cachedSchemaText = m_columnTypes;
                    m_cachedSchemaWidth = m_numTagsPerVec;
                    m_cachedLegacyColumns = m_staticACLTagCols;
                }
                return m_cachedSchema;
            }
            mutable TagSchema m_cachedSchema;
            mutable std::string m_cachedSchemaText;
            mutable int m_cachedSchemaWidth = -1;
            mutable int m_cachedLegacyColumns = -1;
            bool ValidateTagSchema(bool loading = false) {
                try {
                    const auto schema = Schema();
                    if (!m_columnTypes.empty()) {
                        if (loading && m_tagSchemaVersion != 1)
                            throw std::invalid_argument("Unsupported or missing stored tag schema version");
                        if (loading && m_tagSchemaFingerprint != schema.Fingerprint())
                            throw std::invalid_argument("Stored tag schema fingerprint mismatch");
                        m_tagSchemaVersion = 1;
                        m_columnTypes = schema.text;
                        m_tagSchemaFingerprint = schema.Fingerprint();
                    } else if (!m_tagSchemaFingerprint.empty() || m_tagSchemaVersion != 0) {
                        throw std::invalid_argument("Stored tag schema is missing ColumnTypes");
                    }
                    if (m_enableLimitedTagPosting && !schema.IsCategorical(m_limitedTagColumn))
                        throw std::invalid_argument("LimitedTagColumn must identify a categorical original column");
                    (void)Schema();
                    return true;
                } catch (const std::exception& error) {
                    SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Invalid tag schema: %s\n", error.what());
                    return false;
                }
            }
            // Number of leading tag columns used by static ACL filtering.
            // Zero preserves the legacy behavior of using every tag column.
            int m_staticACLTagCols;
            // Build-time cross-subgraph sidecar used by STATIC unfilter-tail
            // construction and retained for runtime unified traversal.
            bool m_buildCrossEdges;
            int m_crossExtraEdges;

            // GPU building
            int m_gpuSSDNumTrees;
            int m_gpuSSDLeafSize;
            int m_numGPUs;

            // Searching
            std::string m_searchResult;
            std::string m_logFile;
            int m_qpsLimit;
            int m_resultNum;
            int m_truthResultNum;
            int m_queryCountLimit;
            int m_maxCheck;
            int m_hashExp;
            float m_maxDistRatio;
            int m_ioThreads;
            int m_searchPostingPageLimit;
            int m_searchInternalResultNum;
            bool m_collectPostingContributionStats;
            bool m_forceDenseTagSearch;
            int m_directSparseMaxPostings;
            float m_filteredSearchNprobeSafety;
            float m_filteredSearchTargetRecall;
            float m_filteredSearchCoverageExponent;
            bool m_enableAdaptiveFilteredNprobe;
            bool m_logAdaptiveNprobe;
            bool m_logPhaseTime;
            bool m_disableCrossEdges;
            bool m_logCrossStats;
            bool m_logPathStats;
            int m_dumpHeads;
            bool m_filterKeepUExtra;
            bool m_enableUnfilterTail;
            bool m_ablateUExtra;
            bool m_ablateTail;
            bool m_unfilterPurePages;
            int m_unfilterExtraTailPages;
            int m_unfilterPureDistanceScanPercent;
            int m_rerank;
            bool m_recall_analysis;
            int m_debugBuildInternalResultNum;
            bool m_enableADC;
            int m_iotimeout;

            int m_searchThreadNum;

            // Calculating
            std::string m_truthFilePrefix;
            bool m_calTruth;
            bool m_calAllTruth;
            int m_searchTimes;
            int m_minInternalResultNum;
            int m_stepInternalResultNum;
            int m_maxInternalResultNum;
            bool m_onlySearchFinalBatch;

            // Updating
            bool m_disableReassign;
            int m_postingOffset;
            bool m_searchDuringUpdate;
            int m_reassignK;
            bool m_recovery;

            // Updating(SPFresh Update Test)
            bool m_update;
            bool m_inPlace;
            bool m_outOfPlace;
            float m_latencyLimit;
            int m_step;
            int m_insertThreadNum;
            int m_endVectorNum;
            std::string m_persistentBufferPath;
            int m_appendThreadNum;
            int m_reassignThreadNum;
            int m_batch;
            std::string m_fullVectorPath;
            std::string m_updateVectorFile;

            // Steady State Update
            std::string m_updateFilePrefix;
            std::string m_updateMappingPrefix;
            int m_days;
            int m_deleteQPS;
            int m_sampling;
            bool m_showUpdateProgress;
            int m_mergeThreshold;
            bool m_loadAllVectors;
            bool m_steadyState;
            int m_spdkBatchSize;
            bool m_stressTest;
            int m_bufferLength;
            int m_unfilterTailBufferLength;
            int m_maxFileSize;
            int m_startFileSize;
            int m_growthFileSize;
            float m_growThreshold;
            float m_fDeletePercentageForRefine;
            bool m_oneClusterCutMax;
            bool m_consistencyCheck;
            bool m_checksumCheck;
            bool m_checksumInRead;
            int m_cacheSize;
            int m_cacheShards;
            bool m_asyncMergeInSearch;
            bool m_centeringToZero;

            // Iterative
            int m_headBatch;
            int m_asyncAppendQueueSize;
            bool m_allowZeroReplica;

            // ShareDB: when true, ExtraDynamicSearcher will use the externally-provided
            // m_externalDB (typically a Helper::TenantPrefixedKeyValueIO wrapping a
            // shared RocksDB instance) instead of allocating its own per-tenant
            // RocksDBIO. The flag is exposed via the parameter system; m_externalDB
            // is a runtime-only handle set programmatically (e.g., by
            // SPANN::Index::SetSharedDB or by TenantIndexManager).
            bool m_shareDB;
            std::shared_ptr<Helper::KeyValueIO> m_externalDB;

            // Primary-head CSR bypass for sparse categorical filters.
            bool m_buildPrimaryHeadCSR;
            std::string m_primaryHeadCSRFile;
            bool m_enablePrimaryHeadBypass;
            int m_primaryHeadBypassRerankL;

            // In-posting quantization (unified config interface). See ParameterDefinitionList.h.
            std::string m_postingQuantizer;   // None|RaBitQ|OPQ|PipePQ
            int m_postingQuantM;              // OPQ/PipePQ code bytes per vector
            bool m_requantizeFromPipePQ;      // one-time same-stride PipePQ->OPQ posting rewrite
            bool m_quantizeHead;              // quantize the head index too
            std::string m_postingQuantFile;   // code sidecar path
            std::string m_pipePQPivotsFile;   // PipeANN PQ pivot sidecar path
            std::string m_fullVectorFile;     // full-precision base for cold rerank
            int m_rerankL;                    // exact-rerank depth (0 = default)
            bool m_quantADCOnly;              // skip rerank, return ADC/estimate order

            Options() {
#define DefineBasicParameter(VarName, VarType, DefaultValue, RepresentStr) \
                VarName = DefaultValue; \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineBasicParameter

#define DefineSelectHeadParameter(VarName, VarType, DefaultValue, RepresentStr) \
                VarName = DefaultValue; \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineSelectHeadParameter

#define DefineBuildHeadParameter(VarName, VarType, DefaultValue, RepresentStr) \
                VarName = DefaultValue; \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineBuildHeadParameter

#define DefineSSDParameter(VarName, VarType, DefaultValue, RepresentStr) \
                VarName = DefaultValue; \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineSSDParameter
            }

            ~Options() {}

            static const char* CanonicalParameter(const char* section, const char* name)
            {
                if (section == nullptr || name == nullptr) return name;
                if (Helper::StrUtils::StrEqualIgnoreCase(section, "SearchSSDIndex") &&
                    Helper::StrUtils::StrEqualIgnoreCase(name, "PostingPageLimit"))
                    return "SearchPostingPageLimit";
                const bool select = Helper::StrUtils::StrEqualIgnoreCase(section, "SelectHead");
                const bool ssd = Helper::StrUtils::StrEqualIgnoreCase(section, "BuildSSDIndex") ||
                    Helper::StrUtils::StrEqualIgnoreCase(section, "SearchSSDIndex");
                struct Alias { const char* oldName; const char* name; bool select; };
                static const Alias aliases[] = {
                    {"SelectSecondLevel", "HierarchyEnabled", true},
                    {"SecondLevelHierarchyLevels", "HierarchyLevels", true},
                    {"SecondLevelHeadVectors", "HierarchyHeadVectors", true},
                    {"SecondLevelHeadVectorIDs", "HierarchyHeadVectorIDs", true},
                    {"SecondLevelReplicaCount", "HierarchyReplicaCount", true},
                    {"SecondLevelHeadIndexFolder", "HierarchyHeadIndexFolder", true},
                    {"SecondLevelPostingFile", "HierarchyPostingFile", true},
                    {"SecondLevelGenerationFingerprint", "HierarchyGenerationFingerprint", true},
                    {"SecondLevelInitialProbeRatio", "HierarchyInitialProbeRatio", false},
                    {"SecondLevelMaxCheck", "HierarchyMaxCheck", false},
                    {"SecondLevelGraphSignaturePruning", "HierarchyGraphSignaturePruning", false},
                    {"SecondLevelPrefetchMode", "HierarchyPrefetchMode", false},
                };
                for (const auto& alias : aliases)
                    if ((alias.select ? select : ssd) &&
                        Helper::StrUtils::StrEqualIgnoreCase(name, alias.oldName))
                        return alias.name;
                return name;
            }

            bool ValidateHierarchyRatio() const
            {
                if (!m_selectSecondLevel) return true;
                double legacy = 0.0;
                if (!m_legacyHierarchyRatio.empty() &&
                    (!Helper::Convert::ConvertStringTo<double>(m_legacyHierarchyRatio.c_str(), legacy) ||
                     !std::isfinite(legacy) || std::abs(legacy - m_ratio) > 1e-12))
                {
                    SPTAGLIB_LOG(Helper::LogLevel::LL_Error,
                        "Conflicting legacy SecondLevelRatio: hierarchy selection now uses Ratio at every level. "
                        "An index built with unequal ratios requires the legacy reader; do not rewrite its config. "
                        "For a new build remove SecondLevelRatio and set Ratio explicitly.\n");
                    return false;
                }
                if (!std::isfinite(m_ratio) || m_ratio <= 0.0 || m_ratio >= 1.0)
                {
                    SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Hierarchy selection requires 0 < Ratio < 1.\n");
                    return false;
                }
                return true;
            }

            static bool IsRemovedParameter(const char* name)
            {
                for (const char* removed : {"HierarchyRouteSelectivityThreshold", "SecondLevelRouteSelectivityThreshold",
                                            "ACLCols", "HierLevelWidths", "PivotForceNodeCount",
                                            "DisablePivotEstimator", "RoutingCols", "PerVectorTagsFile",
                                            "NumericCols", "EnableExtremeSparseTag", "ExtremeSparseTagMinCount",
                                            "ExtremeSparseTagFile", "LogExtremeSparseTagRoute", "FilterKeepCross",
                                            "DisableCrossSubgraph", "UnifiedNprobeBudget", "MultiNodeBudgetKeepRatio",
                                            "LogUExtra", "PostingQuantBits", "HybridGraphDegree",
                                            "EnableHierPostingFilter", "LimitedTagVoteHeadCount",
                                            "LimitedTagMaxExpandedPostingPages", "LimitedTagMaxExtraSupports",
                                            "TagOffset", "BKTSeed", "TPTSeed",
                                            "SparseFallbackMaxHeads", "SparseFallbackMaxPostingPages",
                                            "HierarchySignatureMinSelectivity", "HierarchySignatureMaxSelectivity",
                                            "SecondLevelSignatureMinSelectivity", "SecondLevelSignatureMaxSelectivity"}) {
                    if (Helper::StrUtils::StrEqualIgnoreCase(name, removed)) return true;
                }
                return false;
            }

            static bool IsRemovedSectionAlias(const char* section, const char* name)
            {
                if (!Helper::StrUtils::StrEqualIgnoreCase(section, "MultiTenant")) return false;
                for (const char* native : {"CrossEdges", "CrossExtraEdges", "DualPoolAugment",
                                          "DualPoolExtraRatio", "UExtraIDFile"}) {
                    if (Helper::StrUtils::StrEqualIgnoreCase(name, native)) return true;
                }
                return false;
            }

            static bool ValidateNativeEnvironment()
            {
                for (const char* removed : {"SPTAG_ACL_COLS", "SPTAG_HIER_LEVEL_WIDTHS",
                                            "SPTAG_PIVOT_FORCE_NODE_COUNT", "SPTAG_DISABLE_PIVOT_ESTIMATOR",
                                            "SPTAG_ROUTING_COLS", "SPTAG_ROUTING_ONLY",
                                            "SPTAG_PER_VECTOR_TAGS_FILE", "SPTAG_PERTAG_HEAD_RATIO",
                                            "SPTAG_SELECT_TYPE_OVERRIDE", "SPTAG_NUMERIC_COLS",
                                            "SPTAG_TAG_OFFSET", "SPTAG_TAGS_OFFSET"}) {
                    if (std::getenv(removed) != nullptr) {
                        SPTAGLIB_LOG(Helper::LogLevel::LL_Error,
                            "%s was removed: use the native record schema and spatial configuration.\n", removed);
                        return false;
                    }
                }
                return true;
            }

            ErrorCode SetParameter(const char* p_section, const char* p_param, const char* p_value)
            {
                if (nullptr == p_section || nullptr == p_param || nullptr == p_value) return ErrorCode::Fail;
                if (IsRemovedParameter(p_param) || IsRemovedSectionAlias(p_section, p_param) ||
                    ((Helper::StrUtils::StrEqualIgnoreCase(p_param, "SelectHeadType") ||
                      Helper::StrUtils::StrEqualIgnoreCase(p_param, "SelectType")) &&
                     Helper::StrUtils::StrEqualIgnoreCase(p_value, "PerTagBKT"))) {
                    SPTAGLIB_LOG(Helper::LogLevel::LL_Error,
                        "[%s] %s=%s was removed: use the canonical native configuration.\n",
                        p_section, p_param, p_value);
                    return ErrorCode::FailedParseValue;
                }
                p_param = CanonicalParameter(p_section, p_param);
                if (Helper::StrUtils::StrEqualIgnoreCase(p_param, "ColumnTypes") &&
                    Helper::StrUtils::StrEqualIgnoreCase(p_section, "BuildSSDIndex")) {
                    try {
                        m_columnTypes = *p_value ? TagSchema::Parse(p_value).text : "";
                        return ErrorCode::Success;
                    } catch (const std::exception& error) {
                        SPTAGLIB_LOG(Helper::LogLevel::LL_Error, "Invalid tag schema: %s\n", error.what());
                        return ErrorCode::FailedParseValue;
                    }
                }
                if (Helper::StrUtils::StrEqualIgnoreCase(p_section, "SelectHead") &&
                    Helper::StrUtils::StrEqualIgnoreCase(p_param, "SecondLevelRatio"))
                {
                    m_legacyHierarchyRatio = p_value;
                    return ErrorCode::Success;
                }

                if (Helper::StrUtils::StrEqualIgnoreCase(p_section, "Base")) {
#define DefineBasicParameter(VarName, VarType, DefaultValue, RepresentStr) \
    if (Helper::StrUtils::StrEqualIgnoreCase(p_param, RepresentStr)) \
    { \
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Setting %s with value %s\n", RepresentStr, p_value); \
        VarType tmp; \
        if (Helper::Convert::ConvertStringTo<VarType>(p_value, tmp)) \
        { \
            VarName = tmp; \
        } \
    } else \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineBasicParameter

                    ;
                }
                else if (Helper::StrUtils::StrEqualIgnoreCase(p_section, "SelectHead")) {
#define DefineSelectHeadParameter(VarName, VarType, DefaultValue, RepresentStr) \
    if (Helper::StrUtils::StrEqualIgnoreCase(p_param, RepresentStr)) \
    { \
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Setting %s with value %s\n", RepresentStr, p_value); \
        VarType tmp; \
        if (Helper::Convert::ConvertStringTo<VarType>(p_value, tmp)) \
        { \
            VarName = tmp; \
        } \
        else return ErrorCode::FailedParseValue; \
    } else \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineSelectHeadParameter

                    ;
                }
                else if (Helper::StrUtils::StrEqualIgnoreCase(p_section, "BuildHead")) {
#define DefineBuildHeadParameter(VarName, VarType, DefaultValue, RepresentStr) \
    if (Helper::StrUtils::StrEqualIgnoreCase(p_param, RepresentStr)) \
    { \
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Setting %s with value %s\n", RepresentStr, p_value); \
        VarType tmp; \
        if (Helper::Convert::ConvertStringTo<VarType>(p_value, tmp)) \
        { \
            VarName = tmp; \
        } \
    } else \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineBuildHeadParameter

                    ;
                }
                else if (Helper::StrUtils::StrEqualIgnoreCase(p_section, "BuildSSDIndex")) {
#define DefineSSDParameter(VarName, VarType, DefaultValue, RepresentStr) \
    if (Helper::StrUtils::StrEqualIgnoreCase(p_param, RepresentStr)) \
    { \
        SPTAGLIB_LOG(Helper::LogLevel::LL_Info, "Setting %s with value %s\n", RepresentStr, p_value); \
        VarType tmp; \
        if (Helper::Convert::ConvertStringTo<VarType>(p_value, tmp)) \
        { \
            VarName = tmp; \
        } \
    } else \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineSSDParameter

                    ;
                }
                return ErrorCode::Success;
            }
            
            std::string GetParameter(const char* p_section, const char* p_param) const
            {
                if (nullptr == p_section || nullptr == p_param) return std::string();
                p_param = CanonicalParameter(p_section, p_param);

                if (Helper::StrUtils::StrEqualIgnoreCase(p_section, "Base")) {
#define DefineBasicParameter(VarName, VarType, DefaultValue, RepresentStr) \
        if (Helper::StrUtils::StrEqualIgnoreCase(p_param, RepresentStr)) \
        { \
            return SPTAG::Helper::Convert::ConvertToString(VarName); \
        } else \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineBasicParameter

                    ;
                }
                else if (Helper::StrUtils::StrEqualIgnoreCase(p_section, "SelectHead")) {
#define DefineSelectHeadParameter(VarName, VarType, DefaultValue, RepresentStr) \
        if (Helper::StrUtils::StrEqualIgnoreCase(p_param, RepresentStr)) \
        { \
            return SPTAG::Helper::Convert::ConvertToString(VarName); \
        } else \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineSelectHeadParameter

                    ;
                }
                else if (Helper::StrUtils::StrEqualIgnoreCase(p_section, "BuildHead")) {
#define DefineBuildHeadParameter(VarName, VarType, DefaultValue, RepresentStr) \
        if (Helper::StrUtils::StrEqualIgnoreCase(p_param, RepresentStr)) \
        { \
            return SPTAG::Helper::Convert::ConvertToString(VarName); \
        } else \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineBuildHeadParameter

                    ;
                }
                else if (Helper::StrUtils::StrEqualIgnoreCase(p_section, "BuildSSDIndex")) {
#define DefineSSDParameter(VarName, VarType, DefaultValue, RepresentStr) \
        if (Helper::StrUtils::StrEqualIgnoreCase(p_param, RepresentStr)) \
        { \
            return SPTAG::Helper::Convert::ConvertToString(VarName); \
        } else \

#include "inc/Core/SPANN/ParameterDefinitionList.h"
#undef DefineSSDParameter

                    ;
                }
                return std::string();
            }
        };
    }
}

#endif // _SPTAG_SPANN_OPTIONS_H_