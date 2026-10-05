#define main PostingBenchmarkMain
#include "../Tools/benchmarks/hierarchical_shortcut_native/native_postfilter/Bench.cpp"
#undef main
#include <unistd.h>

int main()
{
    const auto directory = std::filesystem::current_path() /
        ("posting-config-test-" + std::to_string(getpid()));
    try {
        Require(std::filesystem::create_directory(directory), "Fixture directory already exists");
        std::filesystem::create_directories(directory / "index/tenant_0");
        std::ofstream(directory / "index/tenant_0/indexloader.ini") <<
            "[Index]\nValueType=Float\nIndexAlgoType=SPANN\n[Base]\nValueType=Float\n"
            "IndexAlgoType=BKT\n[BuildSSDIndex]\nStorage=STATIC\n";
        std::ofstream(directory / "queries.npy") << "Not loaded by configuration validation\n";
        const auto config = [&](const char* name, const char* width, bool missing,
                                const char* anchors="8", const char* maxCheck="2048",
                                const char* topk="10", const char* probe="96", const char* sweep=nullptr) {
            std::ofstream file(directory / name);
            file << "[SearchSSDIndex]\nisExecute=true\nBuildSsdIndex=false\nInternalResultNum=" << probe <<
                "\nNumberOfThreads=1\nHashTableExponent=4\nResultNum=" << topk << "\nMaxCheck=" << maxCheck << "\nMaxDistRatio=8\n"
                "SearchPostingPageLimit=3\nDisableCrossEdges=true\nLogPhaseTime=false\nLogPathStats=false\n"
                "DumpHeads=0\nEnableHybridDistance=false\nEnablePostingNavigation=true\nPostingAnchorCount="
                << anchors << '\n';
            if (!missing) file << "PostingAdditionalMaxCheck=2048\n";
            if (width) file << "PostingNavigationWidth=" << width << '\n';
            if (sweep) file << "[SearchSweep]\nNProbe=" << sweep << '\n';
            file << "[Benchmark]\nIndex=" << (directory / "index").string() << "\nQueries="
                << (directory / "queries.npy").string()
                << "\nValueType=Float\nPredicate=empty\nMaxQueries=32\nWarmup=32\n";
        };
        config("on.ini", "8", false);
        config("off.ini", nullptr, false);
        config("invalid.ini", "-1", false);
        config("missing.ini", "8", true);
        config("nprobe.ini", "8", false, "0");
        config("invalid-anchors.ini", "8", false, "-1");
        for (const char* budget : {"1","512","1024","4096"})
            config(("budget"+std::string(budget)+".ini").c_str(), "8", false, "0", budget);
        config("zero-budget.ini", "8", false, "0", "0");
        config("negative-budget.ini", "8", false, "0", "-1");
        config("malformed-budget.ini", "8", false, "0", "512x");
        config("overflow-budget.ini", "8", false, "0", "2147481600");
        config("top100.ini", "8", false, "0", "2048", "100", "192", "[100,192,384]");
        config("short-probe.ini", "8", false, "0", "2048", "100", "96");
        config("short-sweep.ini", "8", false, "0", "2048", "100", "192", "[96,192]");
        for (const char* topk : {"0", "-1", "100x", "2147483648"})
            config(("invalid-topk"+std::string(topk)+".ini").c_str(), "8", false, "0", "2048", topk);
        const auto batch = [&](const std::vector<std::string>& names,const char* warmupPolicy=nullptr) {
            const auto path = directory / "batch.ini";
            std::ofstream file(path);
            file << "[Batch]\nCaseCount=" << names.size() << '\n';
            if(warmupPolicy) file << "WarmupPolicy=" << warmupPolicy << '\n';
            for (std::size_t i=0;i<names.size();++i)
                file << "[Case" << i+1 << "]\nConfig=" << (directory/names[i]).string()
                    << "\nOutputDirectory=" << (directory/("result"+std::to_string(i))).string() << '\n';
            file.close();
            Helper::IniReader reader;
            Require(reader.LoadIniFile(path.string())==ErrorCode::Success, "Cannot read test batch");
            return ReadBatch(path.c_str(), reader);
        };
        const auto cases = batch({"on.ini", "off.ini", "on.ini"});
        Require(cases.size()==3 && cases[0]->cfg.navigationWidth==8 &&
            cases[1]->cfg.navigationWidth==0 && cases[2]->cfg.navigationWidth==8,
            "Optional native cutoff or per-case default changed");
        const auto anchors = batch({"nprobe.ini","on.ini","nprobe.ini"});
        Require(anchors.size()==3 && anchors[0]->cfg.anchorCount==0 &&
            anchors[1]->cfg.anchorCount==8 && anchors[2]->cfg.anchorCount==0,
            "Native automatic/fixed/automatic anchor mode changed");
        const auto budgets = batch({"budget512.ini","budget1024.ini","on.ini","budget4096.ini","budget1.ini"});
        const std::array<int,5> expectedBudgets{512,1024,2048,4096,1};
        Require(budgets.size()==expectedBudgets.size(), "Incomplete native graph budget cases");
        for (std::size_t i=0;i<budgets.size();++i)
            Require(budgets[i]->cfg.maxCheck==expectedBudgets[i],
                "Native graph budget was replaced by a benchmark constant");
        const auto results = batch({"on.ini", "top100.ini", "on.ini"});
        Require(results[0]->cfg.topk==10 && results[1]->cfg.topk==100 &&
            results[2]->cfg.topk==10 && results[1]->cfg.probes==std::vector<int>({100,192,384}),
            "Native result capacity or top100 sweep was replaced by a benchmark constant");
        Require(batch({"on.ini","top100.ini"},"once").size()==2 &&
            batch({"on.ini"},"per_point").size()==1,"Explicit batch warmup policy rejected");
        for(const char* policy:{"per_case","true",""}) {
            bool rejected=false;
            try {batch({"on.ini"},policy);} catch(const std::runtime_error&) {rejected=true;}
            Require(rejected,"Invalid batch warmup policy accepted");
        }
        AnnIndex wrapped("SPANN","Float",128);
        wrapped.SetBuildParam("PostingAnchorCount","8","BuildSSDIndex");
        Require(wrapped.GetInternalIndex()!=nullptr, "Cannot initialize wrapper fixture");
        for (const char* section : {"BuildSSDIndex","SearchSSDIndex"}) {
            wrapped.SetSearchParam("PostingAnchorCount","8",section);
            Require(wrapped.GetInternalIndex()->GetParameter("PostingAnchorCount",section)=="8",
                "Cannot set fixed wrapper anchors");
            wrapped.SetSearchParam("PostingAnchorCount","0",section);
            Require(wrapped.GetInternalIndex()->GetParameter("PostingAnchorCount",section)=="0",
                "Wrapper rejected automatic nprobe anchors");
            wrapped.SetSearchParam("PostingAnchorCount","-1",section);
            Require(wrapped.GetInternalIndex()->GetParameter("PostingAnchorCount",section)=="0",
                "Wrapper accepted invalid anchors");
        }
        for (const auto& name : {"invalid.ini", "missing.ini", "invalid-anchors.ini",
                                "zero-budget.ini", "negative-budget.ini", "malformed-budget.ini", "overflow-budget.ini",
                                "short-probe.ini", "short-sweep.ini", "invalid-topk0.ini", "invalid-topk-1.ini",
                                "invalid-topk100x.ini", "invalid-topk2147483648.ini"}) {
            bool rejected=false;
            try { batch({name}); }
            catch (const std::runtime_error&) { rejected=true; }
            catch (const std::invalid_argument&) { rejected=true; }
            Require(rejected, "Invalid or incomplete batch was accepted");
        }
        std::filesystem::remove_all(directory);
        std::cout << "PASS batch cutoff, native anchors/budgets, top10/top100/top10 result capacity and strict required settings\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\nFixture retained at " << directory << '\n';
        return 1;
    }
}
