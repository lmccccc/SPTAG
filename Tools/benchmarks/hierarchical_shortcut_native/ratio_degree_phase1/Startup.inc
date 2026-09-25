    if (auto* c = H1Supplier::Active()) {
        auto& e = *c->engine;
        e.collectingEntry = false;
        e.stats.entryCalls = e.stats.Used();
        e.stats.entryBefore = p_space.m_NGQueue.size();
        if (e.enabled) {
            ++e.stats.startupChecks;
            const int possibleGoal = e.Required(m_pGraph.m_iNeighborhoodSize);
            if (e.stats.entryBefore < static_cast<unsigned>(possibleGoal)) {
                const int anchor = e.EntryAnchor();
                const auto checked = p_space.m_iNumberOfCheckedLeaves;
                const auto calls = e.stats.Used(), qualified = e.stats.qualified;
                const auto rows = e.stats.h2Rows;
                e.stats.startupBefore = e.stats.entryBefore;
                if (anchor < 0) {
                    ++e.stats.startupBlocked;
                } else {
                    H1Supplier::Connectivity connectivity(e, 1, anchor,
                        m_pGraph[anchor], m_pGraph.m_iNeighborhoodSize,
                        &p_space.m_iNumberOfCheckedLeaves);
                    if (!e.Needs(connectivity.audit.physical, connectivity.audit.before)) {
                        ++e.stats.startupConnected;
                    } else if (p_space.m_iNumberOfCheckedLeaves >= p_space.m_iMaxCheck) {
                        ++e.stats.startupBlocked;
                    } else {
                        int fresh = 0, effective = 0;
                        const auto& adjacent = connectivity.Ordinary();
                        expandEdges(anchor, 0, this, fresh, effective, adjacent.data(),
                            0, static_cast<DimensionType>(adjacent.size()), false);
                        if (p_space.m_iNumberOfCheckedLeaves < p_space.m_iMaxCheck) {
                            ++e.stats.startupCalls;
                            e.Supply(anchor, fresh, connectivity.audit.before, connectivity.audit.physical, [&](int id) {
                                const bool connection = connectivity.AddSupplied(id);
                                const int rawBefore = fresh, qualifiedBefore = effective;
                                expandEdges(anchor, 0, this, fresh, effective, &id, 0, 1, false);
                                return (fresh != rawBefore ? 1 : 0) |
                                    (effective != qualifiedBefore ? 2 : 0) | (connection ? 4 : 0);
                            }, [&] { return p_space.m_iNumberOfCheckedLeaves < p_space.m_iMaxCheck; });
                        } else ++e.stats.startupBlocked;
                    }
                }
                e.stats.startupAfter = p_space.m_NGQueue.size();
                e.stats.startupQualified = e.stats.qualified - qualified;
                if (e.capture) e.startupAudits.push_back({
                    anchor, static_cast<std::int64_t>(e.stats.entryBefore),
                    static_cast<std::int64_t>(e.stats.startupAfter), checked,
                    p_space.m_iNumberOfCheckedLeaves,
                    static_cast<std::int64_t>(e.stats.Used() - calls),
                    static_cast<std::int64_t>(e.stats.startupQualified),
                    static_cast<std::int64_t>(e.stats.h2Rows - rows)});
            }
        }
    }
