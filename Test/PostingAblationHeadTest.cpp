#define main UnusedBenchmarkMain
#include "../Tools/benchmarks/hierarchical_shortcut_native/native_postfilter/Bench.cpp"
#undef main

int main(int argc, char** argv)
{
    try {
        Require(argc == 4, "Expected empty, scalar and union global fixture INIs");
        Config first(argv[1]);
        Helper::IniReader saved;
        Require(saved.LoadIniFile(first.index + "/tenant_0/indexloader.ini") == ErrorCode::Success &&
            saved.GetParameter<int>("Base", "VectorSize", 0) > 0 &&
            saved.GetParameter<int>("Base", "VectorSize", 0) <= 1000000,
            "Only bounded existing fixtures are allowed");
        auto queries = ReadNpy<std::uint8_t>(first.queries, "|u1");
        TenantIndexManager manager(128, "SPANN", "UInt8");
        first.Apply(manager);
        Require(manager.LoadAll(first.index.c_str()), "Cannot load fixture");
        for (int i = 1; i < argc; ++i) {
            Config cfg(argv[i]);
            Require(cfg.globalPostingSearch && cfg.valueType == "UInt8" && cfg.count == 32 &&
                cfg.index == first.index && cfg.queries == first.queries, "Invalid global fixture");
            auto predicates = PrepareCase(cfg, queries);
            cfg.Apply(manager);
            manager.SetSearchParam("InternalResultNum", "24", "SearchSSDIndex");
            manager.SetSearchParam("DumpHeads", "96", "SearchSSDIndex");
            for (int q = 0; q < 32; ++q)
                Require(bool(SearchOne(cfg, queries, predicates, manager, q)), "Fixture search failed");
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
