// Included inside the offline analyzer namespace; no production query path.
#pragma once

struct AuditCSR {
    int lower = 0, upper = 0, replicas = 0;
    std::vector<std::vector<int>> children, parents;

    AuditCSR(const std::string& path, int expectedLower, int expectedUpper)
    {
        std::ifstream file(path, std::ios::binary);
        Require(bool(file), "Cannot open audit CSR: " + path);
        auto read = [&](auto& value) {
            file.read(reinterpret_cast<char*>(&value), sizeof(value));
            Require(bool(file), "Truncated audit CSR: " + path);
        };
        std::uint64_t magic, members;
        std::uint32_t version, header, lo, up, rep, memberBytes, signatureBytes, reserved;
        double minimum, maximum;
        read(magic); read(version); read(header); read(lo); read(up); read(rep);
        read(memberBytes); read(signatureBytes); read(reserved); read(minimum); read(maximum);
        read(members);
        Require(magic == 0x325253324E4E4153ULL && version == 3 && header == 104 &&
                lo == static_cast<unsigned>(expectedLower) && up == static_cast<unsigned>(expectedUpper) &&
                rep > 0 && rep <= up && memberBytes == 4 && signatureBytes == 32 &&
                members == std::uint64_t(lo) * rep &&
                fs::file_size(path) == header + (std::uint64_t(up) + 1) * 8 + members * 4 + up * 32ULL,
                "Unsupported or inconsistent audit CSR: " + path);
        lower = lo; upper = up; replicas = rep;
        file.seekg(header);
        std::vector<std::uint64_t> offsets(up + 1);
        for (auto& offset : offsets) read(offset);
        Require(offsets.front() == 0 && offsets.back() == members &&
                std::is_sorted(offsets.begin(), offsets.end()), "Invalid CSR offsets");
        children.resize(up); parents.resize(lo);
        for (int p = 0; p < upper; ++p) {
            for (auto i = offsets[p]; i < offsets[p + 1]; ++i) {
                std::uint32_t child; read(child);
                Require(child < lo && (children[p].empty() || children[p].back() < int(child)),
                        "Invalid or duplicate CSR child");
                children[p].push_back(child); parents[child].push_back(p);
            }
        }
        for (const auto& row : parents)
            Require(row.size() == rep, "CSR does not retain exactly the declared replicas");
    }
};

class HeadRoutingAudit {
    const Config& c;
    std::shared_ptr<SPTAG::VectorSet> h1, h2, h3;
    AuditCSR lower, upper;
    decltype(SPTAG::COMMON::DistanceCalcSelector<float>(SPTAG::DistCalcMethod::L2)) distance;
    std::vector<std::vector<int>> nearestH2, nearestH3;
    std::map<std::string, std::uint64_t> totals;

    std::vector<float> Distances(const float* query, const std::shared_ptr<SPTAG::VectorSet>& set)
    {
        std::vector<float> result(set->Count());
        for (int i = 0; i < set->Count(); ++i) {
            result[i] = distance(query, static_cast<const float*>(set->GetVector(i)), c.dim);
            Require(std::isfinite(result[i]) && result[i] >= 0, "Invalid audit distance");
        }
        return result;
    }
    static std::vector<int> Nearest(const std::vector<float>& d, int count, std::vector<int> ids = {})
    {
        if (ids.empty()) { ids.resize(d.size()); std::iota(ids.begin(), ids.end(), 0); }
        Require(ids.size() >= static_cast<unsigned>(count), "Underfilled oracle audit frontier");
        std::partial_sort(ids.begin(), ids.begin() + count, ids.end(), [&](int a, int b) {
            return d[a] < d[b] || (d[a] == d[b] && a < b);
        });
        ids.resize(count);
        return ids;
    }
    static std::vector<int> Expand(const AuditCSR& csr, const std::vector<int>& parents)
    {
        std::set<int> unique;
        for (int parent : parents)
            unique.insert(csr.children[parent].begin(), csr.children[parent].end());
        Require(!unique.empty(), "Empty oracle audit frontier");
        return {unique.begin(), unique.end()};
    }
    static bool Intersects(const std::vector<int>& parents, const std::set<int>& selected)
    {
        return std::any_of(parents.begin(), parents.end(), [&](int p) { return selected.count(p); });
    }
    const std::vector<int>& KNN(int id, bool bottom)
    {
        auto& cache = bottom ? nearestH2 : nearestH3;
        if (cache[id].empty()) {
            auto source = bottom ? h1 : h2;
            auto target = bottom ? h2 : h3;
            cache[id] = Nearest(Distances(static_cast<const float*>(source->GetVector(id)), target),
                                bottom ? lower.replicas : upper.replicas);
        }
        return cache[id];
    }
public:
    HeadRoutingAudit(const Config& config, std::shared_ptr<SPTAG::VectorSet> heads)
        : c(config), h1(std::move(heads)), h2(ReadVectors(c.catalogs[0], true, c)),
          h3(ReadVectors(c.catalogs[1], true, c)),
          lower(c.postings[0], h1->Count(), h2->Count()),
          upper(c.postings[1], h2->Count(), h3->Count()),
          distance(SPTAG::COMMON::DistanceCalcSelector<float>(SPTAG::DistCalcMethod::L2)),
          nearestH2(h1->Count()), nearestH3(h2->Count()) {}

    void Query(int q, const float* query, const std::vector<int>& h1order,
               const std::vector<float>& h1dist, const std::vector<Rows>& logs)
    {
        const auto d2 = Distances(query, h2), d3 = Distances(query, h3);
        const auto exact2 = Nearest(d2, c.routingBeam), exact3 = Nearest(d3, c.routingBeam);
        const std::set<int> s2(exact2.begin(), exact2.end()), s3(exact3.begin(), exact3.end());
        const auto reached2 = Expand(upper, exact3);
        const auto selected2 = Nearest(d2, c.routingBeam, reached2);
        const std::set<int> routed2(selected2.begin(), selected2.end());
        const auto reached1 = Expand(lower, selected2);
        const auto predicted = Nearest(h1dist, c.nprobe, reached1);
        const std::set<int> oracle(h1order.begin(), h1order.begin() + c.nprobe);
        std::map<std::string, int> row;
        for (int h : oracle) {
            const bool saved = Intersects(lower.parents[h], s2);
            const bool knn = Intersects(KNN(h, true), s2);
            row["h1_exact_h2_saved_hits"] += saved;
            row["h1_exact_h2_knn_hits"] += knn;
            row["h1_knn_rescues"] += !saved && knn;
            row["h1_knn_regressions"] += saved && !knn;
            row["h1_exact_h3_saved_hits"] += Intersects(lower.parents[h], routed2);
        }
        for (int h : exact2) {
            const bool saved = Intersects(upper.parents[h], s3);
            const bool knn = Intersects(KNN(h, false), s3);
            row["h2_exact_h3_saved_hits"] += saved;
            row["h2_exact_h3_knn_hits"] += knn;
            row["h2_knn_rescues"] += !saved && knn;
            row["h2_knn_regressions"] += saved && !knn;
        }
        const std::set<int> predictedSet(predicted.begin(), predicted.end());
        for (size_t i = 0; i < logs.size(); ++i) {
            std::set<int> actual;
            for (auto h : logs[i][q]) actual.insert(h.id);
            row["exact_top_replay_matches_" + c.names[i]] = actual == predictedSet;
            int shared = 0;
            for (int h : actual) shared += predictedSet.count(h);
            row["exact_top_replay_overlap_" + c.names[i]] = shared;
        }
        row["h1_candidates"] = reached1.size();
        row["h2_candidates"] = reached2.size();
        std::cout << "HEAD_ROUTING_QUERY {\"queryid\":" << q;
        for (auto item : row) {
            totals[item.first] += item.second;
            std::cout << ",\"" << item.first << "\":" << item.second;
        }
        std::cout << "}\n";
    }
    void Summary()
    {
        std::cout << "HEAD_ROUTING_SUMMARY {\"query_count\":" << c.queryCount
                  << ",\"nprobe\":" << c.nprobe << ",\"routing_beam\":" << c.routingBeam
                  << ",\"h1_replicas\":" << lower.replicas << ",\"h2_replicas\":" << upper.replicas
                  << ",\"scope\":\"offline exact-parent coverage; KNN also removes construction ANN error\"";
        for (auto item : totals) std::cout << ",\"" << item.first << "\":" << item.second;
        std::cout << "}\n";
    }
};
