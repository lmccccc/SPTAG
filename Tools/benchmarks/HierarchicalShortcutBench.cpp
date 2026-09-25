// Reuse the existing native readers, fixtures and distance/INI helpers, not its H2 algorithm.
#define main H2SweepUnusedMain
#include "H2SweepBench.cpp"
#undef main
#include "hierarchical_shortcut_native/ShortcutHooks.h"

namespace {
struct ShortcutConfig {
    Config io{};
    std::string h2Vectors, h3Vectors, h2Map, h3Map, lowerCSR, upperCSR;
    std::vector<int> budgets, nativeChecks;
    int maxCheck;
};
ShortcutConfig ShortcutINI(const std::string& file) {
    IniReader ini;
    OK(ini.LoadIniFile(file), "Cannot load INI");
    const std::map<std::string, std::set<std::string>> allowed{
        {"input", {"headvectors", "queries", "querycount", "dim", "flatindexdirectory",
                   "h2vectors", "h3vectors", "h2map", "h3map", "lowercsr", "uppercsr"}},
        {"search", {"resultnum", "threads", "warmup", "repeats", "distancebudgets",
                    "nativemaxchecks", "maxcheck", "shortcutdegree", "parentcount"}},
        {"output", {"directory"}}};
    std::ifstream in(file);
    std::set<std::string> sections;
    std::set<std::pair<std::string, std::string>> seen;
    std::string section;
    for (std::string line; std::getline(in,line);) {
        line = Trim(line);
        if (line.empty() || line.front() == ';') continue;
        std::transform(line.begin(),line.end(),line.begin(),[](unsigned char c){return std::tolower(c);});
        if (line.front() == '[') {
            Check(line.back() == ']', "Invalid section");
            section = line.substr(1,line.size()-2);
            Check(allowed.count(section) && sections.insert(section).second, "Unknown/duplicate section");
        } else {
            auto equal = line.find('=');
            auto key = Trim(line.substr(0,equal));
            Check(equal != std::string::npos && allowed.count(section) && allowed.at(section).count(key) &&
                  seen.emplace(section,key).second, "Unknown/duplicate key: " + key);
        }
    }
    auto str = [&](const char* s, const char* k) {
        auto value = Trim(ini.GetParameter<std::string>(s,k,""));
        Check(!value.empty(), std::string("Missing ") + s + "." + k); return value;
    };
    auto number = [&](const char* k) { return Int(str("search",k)); };
    ShortcutConfig c;
    c.io.heads = str("input","headvectors"); c.io.queries = str("input","queries");
    c.io.flat = str("input","flatindexdirectory"); c.h2Vectors = str("input","h2vectors");
    c.h3Vectors = str("input","h3vectors"); c.h2Map = str("input","h2map");
    c.h3Map = str("input","h3map"); c.lowerCSR = str("input","lowercsr");
    c.upperCSR = str("input","uppercsr"); c.io.output = str("output","directory");
    for (const auto* p : {&c.io.heads,&c.io.queries,&c.io.flat,&c.h2Vectors,&c.h3Vectors,
                         &c.h2Map,&c.h3Map,&c.lowerCSR,&c.upperCSR,&c.io.output})
        Check(fs::path(*p).is_absolute() && p->find(',') == std::string::npos, "Require absolute single paths");
    c.io.nq = Int(str("input","querycount")); c.io.dim = Int(str("input","dim"));
    c.io.k = number("resultnum"); c.io.threads = number("threads");
    c.io.warmup = number("warmup"); c.io.repeats = number("repeats");
    c.budgets = List(str("search","distancebudgets")); c.nativeChecks = List(str("search","nativemaxchecks"));
    c.maxCheck = number("maxcheck");
    Check(c.io.nq > 0 && c.io.nq <= 1000 && c.io.dim == 128 && c.io.k == 24 &&
          c.io.threads == 1 && c.io.warmup >= 0 && c.io.warmup <= 1000 &&
          c.io.repeats > 0 && c.io.repeats <= 5 && c.maxCheck > 0 && c.maxCheck <= 8192 &&
          number("shortcutdegree") == 8 && number("parentcount") == 2, "Invalid/bounded controls");
    Check(c.budgets.size() <= 4 && c.nativeChecks.size() <= 3, "Grid too large");
    for (int n : c.budgets) Check(n >= 64 && n <= 8192,"Invalid distance budget");
    for (int n : c.nativeChecks) Check(n > 0 && n <= 8192,"Invalid native MaxCheck");
    return c;
}

struct HierarchyCSR {
    std::vector<std::vector<int>> children, parents;
    int replicas;
    HierarchyCSR(const std::string& path, int lo, int up) {
        std::ifstream in(path,std::ios::binary);
        auto read = [&](auto& value) {
            in.read(reinterpret_cast<char*>(&value),sizeof(value));
            Check(bool(in),"Truncated CSR");
        };
        uint64_t magic, entries;
        uint32_t version, header, lower, upper, rep, memberBytes, signatureBytes, reserved;
        double minimum, maximum;
        read(magic); read(version); read(header); read(lower); read(upper); read(rep);
        read(memberBytes); read(signatureBytes); read(reserved); read(minimum); read(maximum); read(entries);
        Check(magic == 0x325253324E4E4153ULL && version == 3 && header == 104 &&
              lower == unsigned(lo) && upper == unsigned(up) && rep > 0 && rep <= upper &&
              memberBytes == 4 && signatureBytes == 32 && entries == uint64_t(lo)*rep &&
              fs::file_size(path) == 104 + 8*(uint64_t(up)+1) + 4*entries + uint64_t(up)*32,
              "Invalid V3 CSR header/size");
        replicas = rep;
        in.seekg(header);
        std::vector<uint64_t> offsets(up+1);
        for (auto& offset : offsets) read(offset);
        Check(offsets.front() == 0 && offsets.back() == entries &&
              std::is_sorted(offsets.begin(),offsets.end()),"Invalid CSR offsets");
        children.resize(up); parents.resize(lo);
        for (int p=0;p<up;++p) {
            for (auto i=offsets[p];i<offsets[p+1];++i) {
                uint32_t child; read(child);
                Check(child < unsigned(lo) && (children[p].empty() || children[p].back() < int(child)),
                      "Invalid/duplicate/unsorted CSR member");
                children[p].push_back(child); parents[child].push_back(p);
            }
        }
        for (auto& row : parents) Check(row.size() == rep,"Missing/duplicate reverse ownership");
    }
};

std::vector<int> RepresentativeMap(const std::string& path, const SPTAG::VectorSet& lower,
                                   const SPTAG::VectorSet& upper) {
    const int n = upper.Count();
    std::ifstream in(path,std::ios::binary);
    const auto bytes = fs::file_size(path);
    if (bytes == uint64_t(n)*8+8) {
        int32_t shape[2]; in.read(reinterpret_cast<char*>(shape),8);
        uint64_t count; std::memcpy(&count,shape,8);
        Check((shape[0] == n && shape[1] == 1) || count == uint64_t(n),"Invalid map shape");
    } else if (bytes == uint64_t(n)*8+4) {
        int32_t count; in.read(reinterpret_cast<char*>(&count),4); Check(count == n,"Invalid map count");
    } else Check(bytes == uint64_t(n)*8,"Invalid map size");
    std::vector<int> map(n);
    std::set<int> seen;
    for (int i=0;i<n;++i) {
        uint64_t id; in.read(reinterpret_cast<char*>(&id),8);
        Check(in.good() && id < uint64_t(lower.Count()) && seen.insert(id).second,"Invalid map ID");
        Check(std::memcmp(lower.GetVector(id),upper.GetVector(i),lower.Dimension()*4) == 0,
              "Representative map vector mismatch");
        map[i] = id;
    }
    return map;
}

struct Edges {
    std::vector<std::vector<int>> rows;
    uint64_t h3Routes = 0, descentCandidates = 0, rejected = 0;
};
Edges BuildEdges(const SPTAG::VectorSet& h1, const SPTAG::VectorSet& h2,
                 const SPTAG::VectorSet& h3, const HierarchyCSR& lower,
                 const HierarchyCSR& upper, const std::vector<int>& representatives,
                 SPTAG::BKT::Index<float>& graph) {
    Edges out; out.rows.resize(h1.Count());
    auto sample = [](const SPTAG::VectorSet& set, int i) {return static_cast<const float*>(set.GetVector(i));};
    for (int x=0;x<h1.Count();++x) {
        auto q = sample(h1,x);
        auto nearest = [&](const SPTAG::VectorSet& set, const std::vector<int>& ids,
                           const std::function<bool(int)>& accept) {
            Pair best{SPTAG::MaxDist,-1};
            for (int id : ids) if (accept(id)) best = std::min(best,Pair{Distance(q,sample(set,id),128),id});
            return best.second;
        };
        Results owners;
        for (int p : lower.parents[x]) owners.emplace_back(Distance(q,sample(h2,p),128),p);
        std::sort(owners.begin(),owners.end());
        auto add = [&](int id) {
            if (id < 0) return;
            auto& row = out.rows[x];
            auto original = graph.BenchmarkGraphRow(x);
            bool exists = id == x || std::find(row.begin(),row.end(),id) != row.end();
            for (int j=0;j<graph.BenchmarkGraphWidth() && original[j]>=0;++j) exists |= original[j] == id;
            if (exists) ++out.rejected;
            else row.push_back(id);
        };
        for (int i=0;i<std::min(2,int(owners.size()));++i) {
            int p = owners[i].second;
            add(representatives[p]);
            add(nearest(h1,lower.children[p],[&](int id){return id != x;}));
            ++out.descentCandidates;
            int g = nearest(h3,upper.parents[p],[](int){return true;});
            Check(g >= 0,"Parent has no H3 owner");
            int sibling = nearest(h2,upper.children[g],[&](int id) {
                return !std::binary_search(lower.parents[x].begin(),lower.parents[x].end(),id);
            });
            if (sibling >= 0) {
                ++out.h3Routes;
                add(representatives[sibling]);
                add(nearest(h1,lower.children[sibling],[&](int id){return id != x;}));
                ++out.descentCandidates;
            }
        }
        Check(out.rows[x].size() <= 8,"Shortcut degree exceeded");
    }
    return out;
}

struct Observation {
    Results result;
    uint64_t calls = 0, adjacency = 0, shortcutCalls = 0;
    bool exhausted = false;
};
Observation ShortcutSearch(Index& index, const float* query, int k,
                           const Edges* edges, bool rewire, int budget, bool profile) {
    auto* bkt = dynamic_cast<SPTAG::BKT::Index<float>*>(&index);
    Check(bkt != nullptr,"BKT required");
    ShortcutBench::State state;
    state.edges = edges ? &edges->rows : nullptr; state.rewire = rewire;
    state.remaining = budget; state.profile = profile;
    Observation observation;
    std::function<float(const float*,const float*,SPTAG::DimensionType)> counterOriginal, budgetOriginal;
    if (profile) counterOriginal = bkt->BenchmarkWrapDistance(&observation.calls);
    if (budget) budgetOriginal = bkt->BenchmarkBudgetDistance();
    ShortcutBench::active = &state;
    try { observation.result = Search(index,query,k); }
    catch (...) {
        ShortcutBench::active = nullptr;
        if (budget) bkt->BenchmarkRestoreDistance(budgetOriginal);
        if (profile) bkt->BenchmarkRestoreDistance(counterOriginal);
        throw;
    }
    ShortcutBench::active = nullptr;
    if (budget) bkt->BenchmarkRestoreDistance(budgetOriginal);
    if (profile) bkt->BenchmarkRestoreDistance(counterOriginal);
    if (profile && budget) Check(observation.calls == state.used && state.used <= uint64_t(budget),"Distance budget accounting");
    observation.adjacency = state.adjacency; observation.shortcutCalls = state.shortcutDistances;
    observation.exhausted = state.exhausted;
    return observation;
}

void SaveEdges(const Edges& edges, const fs::path& output) {
    std::ofstream file(output/"shortcuts.bin",std::ios::binary);
    file.write("H13EDGE1",8);
    Write(file,int32_t(edges.rows.size())); Write(file,int32_t(8));
    uint64_t offset = 0;
    Write(file,offset);
    for (auto& row : edges.rows) { offset += row.size(); Write(file,offset); }
    for (auto& row : edges.rows) for (auto id : row) Write(file,int32_t(id));
    Check(file.good(),"Cannot write edges");
}

std::pair<int,int> Reachability(SPTAG::BKT::Index<float>& graph, const Edges* edges, bool rewire) {
    const int n = graph.GetNumSamples();
    std::vector<std::vector<int>> forward(n), reverse(n);
    ShortcutBench::State state;
    state.edges = edges ? &edges->rows : nullptr; state.rewire = rewire;
    ShortcutBench::active = &state;
    for (int x=0;x<n;++x) {
        int row[64];
        int count = ShortcutBench::Neighbors(graph.BenchmarkGraphRow(x),graph.BenchmarkGraphWidth(),x,row);
        forward[x].assign(row,row+count);
        for (int y : forward[x]) reverse[y].push_back(x);
    }
    ShortcutBench::active = nullptr;
    auto reach = [&](const std::vector<std::vector<int>>& adjacency) {
        std::vector<bool> seen(n,false);
        std::vector<int> queue{0}; seen[0] = true;
        for (size_t i=0;i<queue.size();++i)
            for (int y : adjacency[queue[i]]) if (!seen[y]) { seen[y] = true; queue.push_back(y); }
        return int(queue.size());
    };
    return {reach(forward),reach(reverse)};
}

void ShortcutRun(const ShortcutConfig& c, const std::string& ini) {
    auto h1 = ReadVectors(c.io.heads,true,c.io);
    auto h2 = ReadVectors(c.h2Vectors,true,c.io), h3 = ReadVectors(c.h3Vectors,true,c.io);
    auto queries = ReadVectors(c.io.queries,false,c.io);
    auto flat = Load(c.io.flat,128);
    Check(flat->GetNumSamples() == h1->Count(),"H1 graph count mismatch");
    auto* graph = dynamic_cast<SPTAG::BKT::Index<float>*>(flat.get());
    Check(graph->BenchmarkGraphWidth() <= 32,"Graph width exceeds bounded prototype");
    for (int i=0;i<h1->Count();++i) {
        Check(std::memcmp(h1->GetVector(i),flat->GetSample(i),512) == 0,"H1 graph vectors not byte-identical");
        auto row = graph->BenchmarkGraphRow(i);
        for (int j=0;j<graph->BenchmarkGraphWidth();++j)
            Check(row[j] < h1->Count(),"Out-of-bounds original graph edge");
    }
    HierarchyCSR lower(c.lowerCSR,h1->Count(),h2->Count()), upper(c.upperCSR,h2->Count(),h3->Count());
    auto h2Map = RepresentativeMap(c.h2Map,*h1,*h2), h3Map = RepresentativeMap(c.h3Map,*h2,*h3);
    for (int p=0;p<h2->Count();++p)
        Check(std::binary_search(lower.children[p].begin(),lower.children[p].end(),h2Map[p]),"H2 representative not owned");
    for (int g=0;g<h3->Count();++g)
        Check(std::binary_search(upper.children[g].begin(),upper.children[g].end(),h3Map[g]),"H3 representative not owned");
    Check(!fs::exists(c.io.output) && fs::create_directory(c.io.output),"Output must be new");
    const fs::path output(c.io.output);
    fs::copy_file(ini,output/"input.ini");
    auto constructionStart = Clock::now();
    auto edges = BuildEdges(*h1,*h2,*h3,lower,upper,h2Map,*graph);
    double constructionSeconds = std::chrono::duration<double>(Clock::now()-constructionStart).count();
    SaveEdges(edges,output);
    uint64_t edgeCount = 0, heapBytes = edges.rows.capacity()*sizeof(std::vector<int>);
    for (auto& row : edges.rows) { edgeCount += row.size(); heapBytes += row.capacity()*sizeof(int); }
    auto plainReach = Reachability(*graph,nullptr,false);
    auto addReach = Reachability(*graph,&edges,false), rewireReach = Reachability(*graph,&edges,true);
    std::ofstream structure(output/"structure.json");
    structure << std::setprecision(12) << "{\"h1\":" << h1->Count() << ",\"h2\":" << h2->Count()
              << ",\"h3\":" << h3->Count() << ",\"lower_replicas\":" << lower.replicas
              << ",\"upper_replicas\":" << upper.replicas << ",\"edges\":" << edgeCount
              << ",\"h3_route_candidates\":" << edges.h3Routes << ",\"descent_candidates\":" << edges.descentCandidates
              << ",\"duplicates_self_or_original_rejected\":" << edges.rejected
              << ",\"serialized_edge_bytes\":" << fs::file_size(output/"shortcuts.bin")
              << ",\"runtime_edge_capacity_bytes\":" << heapBytes
              << ",\"plain_reachable_from_zero\":" << plainReach.first
              << ",\"plain_can_reach_zero\":" << plainReach.second
              << ",\"add_reachable_from_zero\":" << addReach.first
              << ",\"add_can_reach_zero\":" << addReach.second
              << ",\"rewire_reachable_from_zero\":" << rewireReach.first
              << ",\"rewire_can_reach_zero\":" << rewireReach.second
              << ",\"construction_seconds\":" << constructionSeconds << "}\n";
    // Hierarchy data is construction-only; no query traverses CSR rows.
    lower.children.clear(); lower.parents.clear(); upper.children.clear(); upper.parents.clear();
    h2.reset(); h3.reset(); h2Map.clear(); h3Map.clear();
    std::vector<Results> oracle(c.io.nq);
    std::ofstream truth(output/"oracle_h1.jsonl");
    for (int q=0;q<c.io.nq;++q) {
        oracle[q] = Exact(static_cast<const float*>(queries->GetVector(q)),h1->Count(),
                         [&](int i){return static_cast<const float*>(h1->GetVector(i));},128,c.io.k);
        truth << "{\"query\":" << q << ",\"ids\":"; IDs(truth,oracle[q]); truth << "}\n";
    }
    h1.reset();
    struct Point { std::string name; int check, budget; bool hybrid, rewire; };
    std::vector<Point> points;
    for (int n : c.nativeChecks) points.push_back({"native_"+std::to_string(n),n,0,false,false});
    for (int budget : c.budgets)
        for (int mode=0;mode<3;++mode)
            points.push_back({std::string(mode==0?"plain_":mode==1?"add8_":"rewire8_")+std::to_string(budget),
                              c.maxCheck,budget,mode!=0,mode==2});
    std::ofstream rows(output/"queries.jsonl"), summary(output/"summary.jsonl");
    rows << std::setprecision(12); summary << std::setprecision(12);
    for (const auto& point : points) {
        MaxCheck(*flat,point.check);
        auto run = [&](int q, bool profile) {
            return ShortcutSearch(*flat,static_cast<const float*>(queries->GetVector(q)),c.io.k,
                                  point.hybrid?&edges:nullptr,point.rewire,point.budget,profile);
        };
        std::vector<Observation> counted(c.io.nq);
        for (int q=0;q<c.io.nq;++q) {
            counted[q] = run(q,true);
            Check(counted[q].result == run(q,false).result,"Counted/ordinary result parity failed");
            std::set<int> unique;
            for (auto pair : counted[q].result)
                Check(unique.insert(pair.second).second,"Duplicate result");
        }
        for (int w=0;w<c.io.warmup;++w) run(w%c.io.nq,false);
        std::vector<double> times(c.io.nq,0), calls, recall, adjacency, shortcutCalls;
        int exhausted = 0;
        for (int r=0;r<c.io.repeats;++r) for (int q=0;q<c.io.nq;++q) {
            auto start = Clock::now();
            auto ordinary = run(q,false);
            auto stop = Clock::now();
            times[q] += std::chrono::duration<double,std::micro>(stop-start).count()/c.io.repeats;
            Check(ordinary.result == counted[q].result,"Timed/ordinary parity failed");
        }
        for (int q=0;q<c.io.nq;++q) {
            auto& observation = counted[q];
            calls.push_back(observation.calls); adjacency.push_back(observation.adjacency);
            shortcutCalls.push_back(observation.shortcutCalls);
            recall.push_back(Recall(observation.result,oracle[q],c.io.k));
            exhausted += observation.exhausted;
            rows << "{\"case\":\"" << point.name << "\",\"query\":" << q
                 << ",\"distance_calls\":" << observation.calls << ",\"adjacency_entries\":" << observation.adjacency
                 << ",\"shortcut_distance_calls\":" << observation.shortcutCalls
                 << ",\"exhausted\":" << (observation.exhausted?"true":"false")
                 << ",\"exact_h1_recall\":" << recall.back() << ",\"ordinary_us\":" << times[q] << ",\"ids\":";
            IDs(rows,observation.result);
            rows << ",\"distances\":[";
            for (size_t j=0;j<observation.result.size();++j) {
                if(j) rows << ','; rows << observation.result[j].first;
            }
            rows << "]}\n";
        }
        summary << "{\"case\":\"" << point.name << "\",\"maxcheck\":" << point.check
                << ",\"distance_budget\":" << point.budget << ",\"queries\":" << c.io.nq
                << ",\"resultnum\":" << c.io.k << ",\"exhausted_queries\":" << exhausted;
        Stats(summary,"distance_calls",calls); Stats(summary,"ordinary_us",times);
        Stats(summary,"exact_h1_recall",recall); Stats(summary,"adjacency_entries",adjacency);
        Stats(summary,"shortcut_distance_calls",shortcutCalls);
        summary << "}\n"; summary.flush(); rows.flush();
        std::cout << "SHORTCUT_POINT " << point.name << " recall=" << Mean(recall)
                  << " calls=" << Mean(calls) << " us=" << Mean(times) << std::endl;
    }
    Check(rows.good() && summary.good() && truth.good() && structure.good(),"Output failure");
    std::ofstream complete(output/"COMPLETE");
    complete << "Native navigation only; no final SSD/data recall or full-query QPS claim.\n";
    complete.flush(); Check(complete.good(),"Completion failure");
}
}

int main(int argc, char** argv) {
    try {
        if (argc==3 && std::string(argv[1])=="--make-fixture") {
            Fixture(fs::absolute(argv[2])); return 0;
        }
        Check(argc==3 && std::string(argv[1])=="--config","Usage: shortcutbench --config absolute.ini");
        auto ini = fs::absolute(argv[2]).string();
        ShortcutRun(ShortcutINI(ini),ini);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "SHORTCUT_ERROR " << error.what() << '\n'; return 1;
    }
}
