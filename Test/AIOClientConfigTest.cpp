#define THREEWAY_SPANN_AIO_ISOLATION
#define THREEWAY_SPANN_CORE "aio-config-fixture"
#define main AIOClientMain
#include "../Tools/benchmarks/threeway_native/spann_bench.cpp"
#undef main

int main()
{
    const auto root = std::filesystem::current_path() / ("aio-client-config-" + std::to_string(getpid()));
    try {
        threeway::require(std::filesystem::create_directory(root), "Fixture already exists");
        std::filesystem::create_directories(root / "index/tenant_0");
        std::filesystem::create_directories(root / "run/plans");
        std::ofstream(root / "run/plans/SPTAG_adaptive.throughput.tsv")
            << "scenario\tcontrol_index\tthread_index\trepeat\nall\t0\t0\t1\n";
        std::ofstream(root / "index/tenant_0/indexloader.ini")
            << "[Index]\nIndexAlgoType=SPANN\nValueType=UInt8\n"
               "[Base]\nIndexAlgoType=BKT\nValueType=UInt8\nDistCalcMethod=L2\nDim=128\n"
               "[BuildSSDIndex]\nStorage=STATIC\nNumTagsPerVec=2\nColumnTypes=categorical,numeric\n";
        std::ofstream(root / "index/tenant_0/SPTAGFullList.bin") << "fixture";
        const auto profile = [&](const char* contexts, const char* shared, const char* threads, const char* maxCheck) {
            const auto path = root / "profile.ini";
            std::ofstream file(path);
            file << "[Dataset]\nVectors=base.u8bin\nQueries=query.u8bin\nAttributes=tags.u32\n"
                    "VectorCount=10000\nDimension=128\nAttributeColumns=2\nCategoricalColumn=0\nNumericColumn=1\n"
                    "[Run]\nOutputDirectory=run\nPreparedDirectory=prepared\n"
                    "[Benchmark]\nQueryCount=1000\nWarmupQueries=1000\nTopK=100\nSingleRepeats=1\nScenarios=all\n"
                    "[Single]\nCPUNodes=3\nMemoryNodes=3\n"
                    "[Throughput]\nThreads=48\nRepeats=1\nMinimumSeconds=1\nRecallTargets=0.9\n"
                    "AIOReserve=0\nCPUNodes=2,3\nMemoryNodes=2,3\n"
                    "[SPANN]\nIndexDirectory=index\nBenchmarkBinary=spannAioBench\nNProbe=320\n"
                    "[Scenario.all]\nKind=unfilter\nTruth=truth.u32bin\nCandidateCount=10000\n"
                    "[SearchSSDIndex]\nisExecute=true\nBuildSsdIndex=false\nInternalResultNum=320\n"
                    "HashTableExponent=4\nResultNum=100\nSearchPostingPageLimit=0\nMaxDistRatio=8\n"
                    "DisableCrossEdges=true\nLogPhaseTime=false\nLogPathStats=false\nDumpHeads=0\n"
                    "EnableHybridDistance=false\nEnablePostingNavigation=true\nPostingAnchorCount=0\n"
                    "PostingAdditionalMaxCheck=2048\nEnablePackedHeadLayout=true\n";
            file << "UseSharedAIOPool=" << shared << "\nAIOContextCount=" << contexts
                 << "\nNumberOfThreads=" << threads << "\nMaxCheck=" << maxCheck << '\n';
            file.close();
            threeway::require(bool(file), "Cannot write test profile");
            return threeway::Options(path, "throughput", "SPTAG_adaptive");
        };
        const auto valid = profile("48", "false", "48", "1024");
        validate_overlay(valid);
        validate_saved_index(valid, root / "index");
        threeway::require(private_aio_reservation(valid) == 48 * 320, "Incorrect single-file AIO reservation");
        std::ofstream(root / "index/tenant_0/SPTAGFullList.bin_1") << "fixture";
        threeway::require(private_aio_reservation(valid) == 2 * 48 * 320, "Missing additional posting-file reservation");
        for (const auto& values : std::vector<std::array<const char*,4>>{
                {"4","false","48","1024"}, {"0","false","48","1024"},
                {"65536","false","48","1024"}, {"48","true","48","1024"},
                {"48","false","1","1024"}, {"48","false","48","0"}}) {
            bool rejected = false;
            try { validate_overlay(profile(values[0], values[1], values[2], values[3])); }
            catch (const std::runtime_error&) { rejected = true; }
            threeway::require(rejected, "Invalid isolated benchmark configuration accepted");
        }
        std::filesystem::remove_all(root);
        std::cout << "PASS private benchmark admission, Top100/MC1024/page0 and per-file AIO accounting\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << "\nFixture retained at " << root << '\n';
        return 1;
    }
}
