class Connectivity {
    Engine& engine;
    int* checked;
    std::unordered_set<int> identities;
    bool identitiesReady;
    std::uint64_t callsBefore, freshBefore, qualifiedBefore, visitedBefore;
public:
    ConnectivityAudit audit;
    const std::vector<int>& Ordinary() const {
        return engine.optimized ? engine.ordinaryScratch : audit.ordinary;
    }
    Connectivity(Engine& e, int phase, int current, const int* row, int width, int* nativeChecked)
        : engine(e), checked(nativeChecked), identitiesReady(!e.optimized), callsBefore(e.stats.calls),
          freshBefore(e.stats.rawBefore), qualifiedBefore(e.stats.effectiveBefore),
          visitedBefore(e.stats.ordinaryVisited) {
        PhaseScope clock(e.profiling, e.stats.degreeNs);
        if (current < 0 || current >= e.model->counts[0] || !row || width <= 0 || !checked)
            throw std::runtime_error("Invalid native connectivity row");
        audit.phase = phase; audit.node = current; audit.width = width;
        audit.checkedBefore = *checked; audit.stopSlot = width;
        if (e.optimized) e.ordinaryScratch.clear();
        for (int slot = 0; slot < width; ++slot) {
            const int id = row[slot];
            if (id < 0) { audit.stopSlot = slot; audit.terminator = id; break; }
            if (id >= e.model->counts[0] || id == current) continue;
            if (e.optimized) {
                auto& ids = e.ordinaryScratch;
                if (std::find(ids.begin(), ids.end(), id) != ids.end()) continue;
                ids.push_back(id);
            } else if (!identities.insert(id).second) continue;
            const int flag = e.Qualify(id);
            if (e.capture || !e.optimized) {
                audit.ordinary.push_back(id);
                audit.flags.push_back(flag);
            }
            ++audit.physical;
            audit.before += flag != 0;
        }
        audit.required = e.Required(audit.physical);
        audit.after = audit.before;
        ++e.stats.connChecks; e.stats.connBefore += audit.before;
        e.stats.connLow += e.Needs(audit.physical, audit.before);
        e.stats.shortRows += audit.physical < e.minBaseDegree;
        e.Event(80, current); e.Event(81, audit.before);
    }
    Connectivity(const Connectivity&) = delete;
    Connectivity& operator=(const Connectivity&) = delete;
    bool AddSupplied(int id) {
        if (id < 0 || id >= engine.model->counts[0])
            throw std::runtime_error("Invalid supplied connectivity ID");
        if (!identitiesReady) {
            identities.insert(Ordinary().begin(), Ordinary().end());
            identitiesReady = true;
        }
        if (id == audit.node || !identities.insert(id).second || !engine.Qualify(id)) return false;
        ++audit.after;
        if (engine.capture) audit.supplied.push_back(id);
        return true;
    }
    ~Connectivity() {
        audit.calls = engine.stats.calls - callsBefore;
        audit.fresh = engine.stats.rawBefore - freshBefore;
        audit.freshQualified = engine.stats.effectiveBefore - qualifiedBefore;
        audit.visited = engine.stats.ordinaryVisited - visitedBefore;
        audit.checkedAfter = *checked;
        engine.stats.connAfter += audit.after;
        if (engine.capture) engine.connectivityAudits.push_back(std::move(audit));
    }
};
