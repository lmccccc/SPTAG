#include "benchmark.h"

#include <climits>

#include "inc/CoreInterface.h"
#include "inc/Helper/AsyncFileReader.h"
#include "inc/Helper/SimpleIniReader.h"

#ifndef THREEWAY_SPANN_CORE
#error Build with build_spann_client.py against the frozen measured native core
#endif

namespace
{
constexpr int native_aio_contexts = 4;
constexpr int native_aio_events_per_context = 1024;

int native_int(uint64_t value, const std::string &name)
{
    threeway::require(value > 0 && value <= static_cast<uint64_t>(INT_MAX),
                      name + " must fit a positive native int");
    return static_cast<int>(value);
}

void validate_overlay(const threeway::Options &options)
{
    const auto &ini = options.ini;
    ini.only("SPANN", {"indexdirectory", "benchmarkbinary", "nprobe", "protocol"});
    const bool explicit_ini = ini.has("SPANN", "Protocol");
    const bool hierarchy_ablation = explicit_ini &&
        ini.require("SPANN", "Protocol") == "hierarchy-ablation-v1";
    const bool layout_ablation = explicit_ini &&
        ini.require("SPANN", "Protocol") == "posting-layout-ablation-v1";
#ifndef THREEWAY_SPANN_POSTING_LAYOUT_ABLATION
    threeway::require(!layout_ablation, "Posting-layout ablation requires its matching isolated native core");
#endif
    if (explicit_ini)
        threeway::require(ini.require("SPANN", "Protocol") == "explicit-ini-v1" ||
                              hierarchy_ablation || layout_ablation,
                          "Unknown SPANN.Protocol");
    std::set<std::string> search_keys{
        "isexecute", "buildssdindex", "internalresultnum", "numberofthreads", "hashtableexponent",
        "resultnum", "maxcheck", "maxdistratio", "searchpostingpagelimit", "disablecrossedges",
        "logphasetime", "logpathstats", "dumpheads", "enablehybriddistance", "enablepostingnavigation",
        "postinganchorcount", "postingadditionalmaxcheck", "enablepackedheadlayout"};
    if (ini.has("SearchSSDIndex", "EnablePackedHeadLayout"))
    {
        const bool packed = ini.bool_value("SearchSSDIndex", "EnablePackedHeadLayout");
#ifdef THREEWAY_SPANN_PACKED_HEAD_LAYOUT
        (void)packed;
#else
        threeway::require(!packed, "Packed layout requires a client linked to its matching packed-head core");
#endif
    }
    if (explicit_ini)
        search_keys.insert({"postingnavigationwidth", "postingmatchratepercent", "postingmatchwindow",
                            "collectpostingcontributionstats"});
    if (layout_ablation)
        search_keys.insert("globalpostingnomemoryfilter");
    ini.only("SearchSSDIndex", search_keys);
    if (layout_ablation)
    {
        ini.bool_value("SearchSSDIndex", "GlobalPostingNoMemoryFilter");
        threeway::require(!ini.bool_value("SearchSSDIndex", "EnablePostingNavigation"),
                          "Both posting-layout variants must disable hierarchy");
    }
    for (const auto &item : std::vector<std::pair<std::string, uint64_t>>{
             {"NumberOfThreads", 1}, {"HashTableExponent", 4}, {"ResultNum", options.top_k},
             {"MaxCheck", 2048}, {"SearchPostingPageLimit", 3}, {"DumpHeads", 0},
             {"PostingAnchorCount", 8}, {"PostingAdditionalMaxCheck", 2048}})
        if (!explicit_ini || (item.first != "MaxCheck" && item.first != "PostingAnchorCount"))
            threeway::require(ini.uint_value("SearchSSDIndex", item.first) == item.second,
                              "SPANN protocol differs at SearchSSDIndex." + item.first);
    if (explicit_ini)
    {
        native_int(ini.uint_value("SearchSSDIndex", "MaxCheck"), "SearchSSDIndex.MaxCheck");
        threeway::require(ini.uint_value("SearchSSDIndex", "PostingAnchorCount") == 0 &&
                          ini.uint_value("SearchSSDIndex", "PostingNavigationWidth") == 128 &&
                          ini.double_value("SearchSSDIndex", "PostingMatchRatePercent") == 12.5 &&
                          ini.uint_value("SearchSSDIndex", "PostingMatchWindow") == 1024,
                          "Explicit protocol requires the approved local-match-union-v2 policy");
    }
    for (const auto &item : std::vector<std::pair<std::string, bool>>{
             {"isExecute", true}, {"BuildSsdIndex", false}, {"DisableCrossEdges", true},
             {"LogPhaseTime", false}, {"LogPathStats", false}, {"EnableHybridDistance", false},
             {"EnablePostingNavigation", true}})
    {
        const bool value = ini.bool_value("SearchSSDIndex", item.first);
        threeway::require(value == item.second ||
                              ((hierarchy_ablation || layout_ablation) && item.first == "EnablePostingNavigation"),
                          "Frozen SPANN protocol differs at SearchSSDIndex." + item.first);
    }
    threeway::require(ini.double_value("SearchSSDIndex", "MaxDistRatio") == 8,
                      "Frozen SPANN protocol requires SearchSSDIndex.MaxDistRatio=8");
    const auto initial = ini.uint_value("SearchSSDIndex", "InternalResultNum");
    native_int(initial, "SearchSSDIndex.InternalResultNum");
    threeway::require(initial >= options.top_k, "Native InternalResultNum must be at least TopK");
    for (uint32_t probe : options.controls)
    {
        native_int(probe, "SPANN.NProbe");
        threeway::require(probe >= options.top_k, "Every SPANN.NProbe must be at least TopK");
    }
    for (char **entry = environ; *entry; ++entry)
    {
        const std::string setting(*entry);
        const std::string variable = setting.substr(0, setting.find('='));
        threeway::require(variable.rfind("SPTAG_", 0) != 0 && variable.rfind("SPANN_", 0) != 0 &&
                              variable.rfind("OMP_", 0) != 0 && variable.rfind("GOMP_", 0) != 0 &&
                              variable != "LD_PRELOAD",
                          "SPANN search environment override is forbidden: " + variable);
    }
}

void validate_saved_index(const threeway::Options &options, const threeway::fs::path &directory)
{
    SPTAG::Helper::IniReader saved;
    threeway::require(saved.LoadIniFile((directory / "tenant_0/indexloader.ini").string()) ==
                          SPTAG::ErrorCode::Success,
                      "Cannot read saved SPANN tenant_0 capability header");
    for (const auto &item : std::vector<std::tuple<std::string, std::string, std::string>>{
             {"Index", "IndexAlgoType", "SPANN"}, {"Index", "ValueType", "UInt8"},
             {"Base", "IndexAlgoType", "BKT"}, {"Base", "ValueType", "UInt8"},
             {"Base", "DistCalcMethod", "L2"}, {"BuildSSDIndex", "Storage", "STATIC"}})
        threeway::require(saved.GetParameter<std::string>(std::get<0>(item), std::get<1>(item), "") ==
                              std::get<2>(item),
                          "SPANN requires an immutable native STATIC UInt8/L2 SPANN/BKT index: " +
                              std::get<0>(item) + "." + std::get<1>(item));
    threeway::require(saved.GetParameter<int>("Base", "Dim", 0) == native_int(options.dimension, "Dimension"),
                      "Saved SPANN dimension differs from Dataset.Dimension");
    const int width = saved.GetParameter<int>("BuildSSDIndex", "NumTagsPerVec", 0);
    threeway::require(width == native_int(options.attribute_columns, "AttributeColumns"),
                      "Saved SPANN attribute width differs from the original dataset schema");
    const auto schema = SPTAG::TagSchema::Parse(
        saved.GetParameter<std::string>("BuildSSDIndex", "ColumnTypes", ""), width,
        saved.GetParameter<int>("BuildSSDIndex", "StaticACLTagCols", 0));
    threeway::require(schema.IsCategorical(static_cast<int>(options.categorical_column)) &&
                          schema.NumericLane(static_cast<int>(options.numeric_column)) >= 0,
                      "SPANN predicate columns must retain their original categorical/numeric schema");
}

uint64_t aio_counter(const char *path)
{
    std::ifstream input(path);
    std::string text, extra;
    threeway::require(static_cast<bool>(input >> text) && !(input >> extra),
                      std::string("Cannot read current Linux AIO allowance: ") + path);
    return threeway::parse_uint(text, path);
}

void check_aio(const threeway::Options &options)
{
    const uint64_t maximum = aio_counter("/proc/sys/fs/aio-max-nr");
    const uint64_t used = aio_counter("/proc/sys/fs/aio-nr");
    const uint64_t requested = native_aio_contexts * native_aio_events_per_context;
    threeway::require(used <= maximum && options.aio_reserve <= maximum - used &&
                          requested <= maximum - used - options.aio_reserve,
                      "Insufficient CURRENT HOST SPANN AIO headroom: used=" + std::to_string(used) +
                          " max=" + std::to_string(maximum) + " requested=" + std::to_string(requested) +
                          " reserve=" + std::to_string(options.aio_reserve) +
                          "; re-plan explicitly, no hidden thread reduction or sysctl change");
    std::cout << "THREEWAY_AIO {\"used\":" << used << ",\"maximum\":" << maximum
              << ",\"requested\":" << requested << ",\"reserve\":" << options.aio_reserve
              << ",\"policy\":\"native-shared-pool\"}" << std::endl;
}

std::vector<uint32_t> encode_predicate(const threeway::Options &options, const threeway::Scenario &scenario)
{
    if (scenario.kind == threeway::ScenarioKind::unfilter)
        return {};
    // DNF3 literals are (kind, ORIGINAL column, operation, uint32 value).
    if (scenario.kind == threeway::ScenarioKind::categorical)
        return {0x444e4633U, 1, 1, 0, options.categorical_column, SPTAG::Cache::DNF_EQ, scenario.tag};
    if (scenario.kind == threeway::ScenarioKind::categorical_or)
    {
        std::vector<uint32_t> predicate{0x444e4633U, static_cast<uint32_t>(scenario.tags.size())};
        for (const auto tag : scenario.tags)
            predicate.insert(predicate.end(), {1, 0, options.categorical_column, SPTAG::Cache::DNF_EQ, tag});
        return predicate;
    }
    threeway::require(scenario.kind == threeway::ScenarioKind::mixed_dnf, "Unsupported SPANN predicate kind");
    return {0x444e4633U, 2,
            1, 0, options.categorical_column, SPTAG::Cache::DNF_EQ, scenario.rare_tag,
            2, 0, options.categorical_column, SPTAG::Cache::DNF_EQ, scenario.regular_tag,
               1, options.numeric_column, SPTAG::Cache::DNF_LE, scenario.upper_inclusive};
}

class SPANNBackend final : public threeway::Backend
{
    const threeway::Options &options_;
    const uint32_t max_threads_;
    const bool collect_posting_stats_;
    std::unique_ptr<TenantIndexManager> manager_;
    std::vector<uint32_t> predicate_;

  public:
    SPANNBackend(const threeway::Options &options, uint32_t max_threads)
        : options_(options), max_threads_(max_threads),
          collect_posting_stats_(options.ini.has("SearchSSDIndex", "CollectPostingContributionStats") &&
                                options.ini.bool_value("SearchSSDIndex", "CollectPostingContributionStats"))
    {
        validate_overlay(options);
        native_int(options.top_k, "TopK");
        native_int(options.vector_count, "VectorCount");
        const auto directory = options.resolve("SPANN", "IndexDirectory");
        validate_saved_index(options, directory);
        check_aio(options);
        threeway::require(!SPTAG::Helper::SharedAIOPool::Instance().IsInitialized(),
                          "SPANN requires its original manager-owned AIO pool");
        manager_ = std::make_unique<TenantIndexManager>(native_int(options.dimension, "Dimension"), "SPANN", "UInt8");
        // Keep the measured client's native four-context pool. The batch reader
        // leases workspace_id % 4 under a per-context lock through completion;
        // query workspaces remain thread-local, not one index per IO channel.
        const auto &pool = SPTAG::Helper::SharedAIOPool::Instance();
        threeway::require(pool.IsUsable() && pool.NumContexts() == native_aio_contexts,
                          "Original SPANN shared AIO pool initialization failed");
        manager_->SetHeadIndexCacheLimit(0);
        for (const auto &parameter : options.ini.section("SearchSSDIndex"))
        {
            if (parameter.first == "enablepackedheadlayout") continue;
            const std::string value = parameter.first == "numberofthreads" ? std::to_string(max_threads) :
                                      parameter.first == "internalresultnum" ? std::to_string(options.jobs.front().L) :
                                                                             parameter.second;
            manager_->SetSearchParam(parameter.first.c_str(), value.c_str(), "SearchSSDIndex");
        }
#ifdef THREEWAY_SPANN_PACKED_HEAD_LAYOUT
        threeway::require(manager_->SetSearchParamChecked("EnablePackedHeadLayout",
            options.ini.has("SearchSSDIndex", "EnablePackedHeadLayout") &&
                options.ini.bool_value("SearchSSDIndex", "EnablePackedHeadLayout") ? "true" : "false",
            "SearchSSDIndex"), "Cannot apply packed head layout");
#endif
        threeway::require(manager_->LoadAll(directory.c_str()), "Cannot load immutable SPANN manager metadata");
        threeway::require(manager_->GetTenantCount() == 1 &&
                              manager_->GetTenantVectorCount(0) == static_cast<int>(options.vector_count),
                          "SPANN requires exactly one tenant_0 with the original global VID domain");

        // LoadAll is metadata-only. A real first query materializes tenant_0
        // once before the harness's load timer ends; all workers share it.
        std::ifstream queries(options.prepared_directory / "query.u8bin", std::ios::binary);
        std::vector<uint8_t> first_query(options.dimension);
        queries.seekg(8);
        queries.read(reinterpret_cast<char *>(first_query.data()), first_query.size());
        threeway::require(static_cast<bool>(queries), "Cannot read first query for native SPANN resident load");
        std::vector<uint32_t> ids(options.top_k);
        std::vector<float> distances(options.top_k);
        configure(options.jobs.front(), options.scenarios[options.jobs.front().scenario_index]);
        search(first_query.data(), 0, 0, ids.data(), distances.data());
        std::cout << "THREEWAY_SPANN_INDEX {\"index_load_count\":1,\"max_threads\":" << max_threads
                  << ",\"enable_posting_navigation\":"
                  << (options.ini.bool_value("SearchSSDIndex", "EnablePostingNavigation") ? "true" : "false")
                  << ",\"packed_head_layout\":"
                  << (options.ini.has("SearchSSDIndex", "EnablePackedHeadLayout") &&
                      options.ini.bool_value("SearchSSDIndex", "EnablePackedHeadLayout") ? "true" : "false")
                  << ",\"global_posting_no_memory_filter\":"
                  << (options.ini.has("SearchSSDIndex", "GlobalPostingNoMemoryFilter") &&
                      options.ini.bool_value("SearchSSDIndex", "GlobalPostingNoMemoryFilter") ? "true" : "false")
                  << ",\"aio_contexts\":" << pool.NumContexts()
                  << ",\"aio_events_per_context\":" << native_aio_events_per_context
                  << ",\"core\":" << threeway::json_string(THREEWAY_SPANN_CORE)
                  << ",\"index\":" << threeway::json_string(directory.string()) << "}" << std::endl;
    }

    void configure(const threeway::Job &job, const threeway::Scenario &scenario) override
    {
        threeway::require(job.threads > 0 && job.threads <= max_threads_, "SPANN job exceeds its resident worker capacity");
        predicate_ = encode_predicate(options_, scenario);
        if (job.maxcheck)
            manager_->SetSearchParam("MaxCheck", std::to_string(job.maxcheck).c_str(), "SearchSSDIndex");
        manager_->SetSearchParam("InternalResultNum", std::to_string(job.L).c_str(), "SearchSSDIndex");
    }

    threeway::SearchStats search(const uint8_t *query, uint32_t, uint32_t worker_id,
                                 uint32_t *ids, float *distances) override
    {
        threeway::require(worker_id < max_threads_, "SPANN worker outside its planned capacity");
        const ByteArray vector(const_cast<uint8_t *>(query), options_.dimension, false);
        const ByteArray predicate(reinterpret_cast<uint8_t *>(predicate_.data()), predicate_.size() * sizeof(uint32_t), false);
        // The frozen wrapper owns each result and installs a thread-local
        // predicate context; the native core loans one workspace per thread.
        const auto result = manager_->SearchWithPredicate(vector, 0, static_cast<int>(options_.top_k),
                                                          predicate, predicate_.empty() ? 0 : -1);
        threeway::require(static_cast<bool>(result) && result->GetResultNum() == static_cast<int>(options_.top_k),
                          "Native SPANN SearchWithPredicate failed");
        size_t count = 0;
        for (uint32_t k = 0; k < options_.top_k; ++k)
        {
            const auto *entry = result->GetResult(static_cast<int>(k));
            ids[k] = entry->VID >= 0 ? static_cast<uint32_t>(entry->VID) : UINT32_MAX;
            distances[k] = entry->Dist;
            count += entry->VID >= 0;
        }
        const auto native = SPTAG::VectorIndex::GetThreadLocalPostingScanStats();
        // Native read requests, not page counts or physical-device operations.
        threeway::SearchStats stats{count, static_cast<double>(native.m_readPostings + native.m_rerankReadRequests), {}};
        if (collect_posting_stats_)
        {
            stats.posting_counters = {
                {"read_postings", native.m_readPostings},
                {"matched_postings", native.m_matchedPostings},
                {"unique_matched_postings", native.m_uniqueMatchedPostings},
                {"scanned_vectors", native.m_scannedVectors},
                {"matched_vectors", native.m_matchedVectors},
                {"dedup_skipped_vectors", native.m_dedupSkippedVectors},
                {"unique_matched_vectors", native.m_uniqueMatchedVectors},
                {"posting_page_reads", native.m_postingPageReads},
                {"posting_logical_bytes", native.m_postingLogicalBytes},
                {"posting_physical_bytes", native.m_postingPhysicalBytes},
                {"distance_computations", static_cast<uint64_t>((std::max)(result->GetScanned(), 0))}
            };
        }
        return stats;
    }
};
} // namespace

int main(int argc, char **argv)
{
    return threeway::run_main(argc, argv, "SPTAG_adaptive", [](const threeway::Options &options, uint32_t threads) {
        return std::make_unique<SPANNBackend>(options, threads);
    });
}
