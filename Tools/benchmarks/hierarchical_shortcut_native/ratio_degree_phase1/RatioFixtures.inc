void ConnectivityFixtures() {
    auto p = Postings(64);
    H1Supplier::Model m;
    m.counts = {64, 16, 8};
    for (int i = 0; i < 16; ++i) m.canonical[0].push_back(i < 8 ? i : 32 + i - 8);
    for (int i = 0; i < 8; ++i) m.canonical[1].push_back(i);
    m.children = [&](int l, int id) -> H1Supplier::Model::Row {
        return {p[l - 1].Begin(id), p[l - 1].End(id)};
    };
    m.BuildOwners();
    for (bool optimized : {false, true}) {
        H1Supplier::Engine e;
        e.optimized = optimized;
        int checked = 0, eligible = 32;
        bool signatures = false;
        std::array<bool, 64> flags{};
        const auto reset = [&] {
            checked = 0;
            e.Reset(m, true, true, true, [](int id) { return float(id * id); },
                    [](int, float) {}, [&](int id) { return flags[id] ? 3 : 0; },
                    [&](int, int) { return signatures; });
        };
        for (auto test : std::vector<std::array<int, 3>>{
                {32,32,0}, {32,16,0}, {32,15,1}, {20,10,0}, {20,9,1},
                {16,8,0}, {16,7,1}, {15,15,0}, {15,1,0}, {0,0,0},
                {17,8,1}, {17,9,0}, {31,15,1}, {31,16,0}}) {
            const int d = test[0], valid = test[1];
            for (int visitedCount : {0, 20, 32}) {
                flags.fill(false);
                for (int id = 1; id <= valid; ++id) flags[id] = true;
                reset();
                std::vector<int> row;
                for (int id = 1; id <= d; ++id) row.push_back(id);
                row.push_back(-1);
                {
                    H1Supplier::Connectivity c(e, 0, 0, row.data(), row.size(), &checked);
                    e.stats.ordinaryVisited = (std::min)(d, visitedCount);
                    Check(c.audit.physical == d && c.audit.before == valid);
                    Check(c.audit.required == (d < 16 ? 0 : (d + 1) / 2));
                    e.Supply(0, 0, c.audit.before, c.audit.physical,
                             [](int) { throw std::runtime_error("Signature-rejected scope scanned"); return 0; },
                             [] { return true; });
                    Check(e.stats.calls == static_cast<unsigned>(test[2]));
                    Check(e.stats.Used() == 0 && e.stats.members == 0);
                }
                Check(e.connectivityAudits.back().before == valid);
            }
        }
        flags.fill(true); reset();
        int malformed[] = {0, 1, 1, 64, 2, -2, 3};
        {
            H1Supplier::Connectivity c(e, 0, 0, malformed, 7, &checked);
            Check(c.audit.physical == 2 && c.audit.before == 2 && c.audit.required == 0);
            Check(c.audit.ordinary == std::vector<int>({1,2}) && c.audit.stopSlot == 5 &&
                  c.audit.terminator == -2);
            e.Supply(0, 0, c.audit.before, c.audit.physical, [](int) { return 0; }, [] { return true; });
            Check(e.stats.calls == 0);
        }
        signatures = true; flags.fill(true);
        for (int id = 8; id <= 16; ++id) flags[id] = false;
        reset();
        std::vector<int> row(16);
        std::iota(row.begin(), row.end(), 1);
        {
            H1Supplier::Connectivity c(e, 1, 0, row.data(), row.size(), &checked);
            Check(c.audit.physical == 16 && c.audit.before == 7 && c.audit.required == 8);
            e.Supply(0, 0, c.audit.before, c.audit.physical, [&](int id) {
                return c.AddSupplied(id) ? 4 : 0;
            }, [] { return true; });
            Check(e.stats.calls == 1 && e.stats.qualified == 0 && e.stats.child == 0);
            Check(e.stats.h2Completed == 1 && e.stats.h3Rows == 0 && e.stats.members == 32);
            Check(c.audit.after == 22 && !c.AddSupplied(17) && !c.AddSupplied(1) && !c.AddSupplied(0));
            Check(!e.RowAllowed(1, static_cast<int>(e.rowAudits[0][1])));
        }
        flags.fill(false); signatures = false; reset(); checked = 2048;
        {
            H1Supplier::Connectivity c(e, 1, 0, row.data(), row.size(), &checked);
            Check(c.audit.physical == 16 && c.audit.before == 0 && c.audit.checkedBefore == 2048);
            e.Supply(0, 0, c.audit.before, c.audit.physical, [](int) { return 0; }, [] { return false; });
            Check(e.stats.calls == 0);
        }
    }
}
