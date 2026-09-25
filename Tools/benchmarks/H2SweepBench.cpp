// Standalone, bounded navigation diagnostic. No SSD or production hierarchy code.
#include "inc/Core/BKT/Index.h"
#include "inc/Helper/VectorSetReader.h"
#include <algorithm>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <numeric>
#include <set>
#include <sstream>
#include <thread>
#include <unistd.h>

namespace fs = std::filesystem;
using Index = SPTAG::VectorIndex;
using Pair = std::pair<float, int>;
using Results = std::vector<Pair>;
using Clock = std::chrono::steady_clock;
using SPTAG::Helper::IniReader;

namespace {
void Check(bool ok, const std::string& message) { if (!ok) throw std::runtime_error(message); }
void OK(SPTAG::ErrorCode code, const std::string& what) { Check(code == SPTAG::ErrorCode::Success, what); }
std::string Trim(std::string s) {
    auto first = s.find_first_not_of(" \r\n\t");
    return first == std::string::npos ? "" : s.substr(first, s.find_last_not_of(" \r\n\t") - first + 1);
}
int Int(const std::string& s) {
    int n;
    auto p = std::from_chars(s.data(), s.data() + s.size(), n);
    Check(!s.empty() && p.ec == std::errc() && p.ptr == s.data() + s.size(), "Invalid integer: " + s);
    return n;
}
std::vector<int> List(const std::string& s) {
    std::vector<int> out;
    std::stringstream stream(s);
    for (std::string item; std::getline(stream, item, ',');) out.push_back(Int(Trim(item)));
    Check(!out.empty() && s.back() != ',', "Empty list item");
    std::set<int> unique(out.begin(), out.end());
    Check(unique.size() == out.size(), "Duplicate list item");
    return out;
}
struct Config {
    std::string heads, queries, h2, ids, flat, output;
    int nq, dim, k, candidates, assignCheck, searchCheck, flatCheck, threads, buildThreads, warmup, repeats;
    std::vector<int> replicas, beams;
};
Config ReadConfig(const std::string& file) {
    IniReader ini;
    OK(ini.LoadIniFile(file), "Cannot load INI");
    std::map<std::string, std::set<std::string>> allowed = {
        {"input", {"headvectors","queries","querycount","dim","headindexdirectory","headids","flatindexdirectory"}},
        {"sweep", {"replicas","beams","resultnum","assignmentcandidates","assignmentmaxcheck","searchmaxcheck",
                   "flatmaxcheck","rngfactor","threads","buildthreads","warmup","repeats"}},
        {"output", {"directory"}}};
    std::ifstream in(file);
    for (std::string line; std::getline(in, line);) {
        line = Trim(line);
        if (!line.empty() && line[0] == '[') {
            std::transform(line.begin(), line.end(), line.begin(), [](unsigned char c) { return std::tolower(c); });
            Check(line.back() == ']' && allowed.count(line.substr(1, line.size()-2)), "Unknown section " + line);
        }
    }
    for (const auto& section : allowed)
        for (const auto& key : ini.GetParameters(section.first))
            Check(section.second.count(key.first), "Unknown key " + key.first);
    auto str = [&](const char* section, const char* key, const char* fallback = "") {
        auto s = Trim(ini.GetParameter<std::string>(section, key, fallback));
        Check(!s.empty(), std::string("Missing ") + section + "." + key);
        return s;
    };
    auto number = [&](const char* key, const char* fallback) { return Int(str("sweep", key, fallback)); };
    Config c;
    c.heads = str("input","headvectors"); c.queries = str("input","queries");
    c.h2 = str("input","headindexdirectory"); c.ids = str("input","headids");
    c.flat = str("input","flatindexdirectory"); c.output = str("output","directory");
    for (auto* p : {&c.heads,&c.queries,&c.h2,&c.ids,&c.flat,&c.output})
        Check(fs::path(*p).is_absolute() && p->find(',') == std::string::npos, "Require single absolute paths");
    c.nq = Int(str("input","querycount","1000")); c.dim = Int(str("input","dim","128"));
    c.k = number("resultnum","24"); c.candidates = number("assignmentcandidates","64");
    c.assignCheck = number("assignmentmaxcheck","8192"); c.searchCheck = number("searchmaxcheck","512");
    c.flatCheck = number("flatmaxcheck","2048"); c.threads = number("threads","1");
    c.buildThreads = number("buildthreads","24"); c.warmup = number("warmup","1000");
    c.repeats = number("repeats","3"); c.replicas = List(str("sweep","replicas","2,4,8,16"));
    c.beams = List(str("sweep","beams","4,8,12,16,24,32,48,64,96,128"));
    Check(str("sweep","rngfactor","1") == "1", "Only frozen RNGFactor=1 supported");
    Check(c.nq > 0 && c.nq <= 1000 && c.dim == 128 && c.k == 24 && c.candidates == 64 &&
          c.threads == 1 && c.buildThreads > 0 && c.buildThreads <= 64 &&
          c.warmup >= 0 && c.warmup <= 1000 && c.repeats > 0 && c.repeats <= 10 &&
          c.assignCheck > 0 && c.assignCheck <= 65536 && c.searchCheck > 0 && c.searchCheck <= 65536 &&
          c.flatCheck > 0 && c.flatCheck <= 65536, "Invalid/unbounded parameters");
    for (int r : c.replicas)
        Check(r == 2 || r == 4 || r == 8 || r == 16 || r == 32, "Replicas must be 2,4,8,16,32");
    for (int b : c.beams) Check(b > 0 && b <= 128, "Beam outside [1,128]");
    return c;
}
struct ReaderDirectory {
    fs::path original = fs::current_path(), owned = original / ("h2-reader-" + std::to_string(getpid()));
    ReaderDirectory() { Check(fs::create_directory(owned), "Reader directory exists"); fs::current_path(owned); }
    ~ReaderDirectory() {
        std::error_code e; fs::current_path(original,e); fs::remove(owned/"tempfolder",e); fs::remove(owned,e);
    }
};
std::shared_ptr<SPTAG::VectorSet> ReadVectors(const std::string& path, bool heads, const Config& c) {
    auto bytes = fs::file_size(path);
    auto header = heads ? 8u : 0u;
    auto stride = 4u * (c.dim + (heads ? 0 : 1));
    Check(bytes > header && (bytes-header)%stride == 0, "Bad vector size");
    auto count = (bytes-header)/stride;
    Check(count <= 200000 && count >= static_cast<unsigned>(heads ? c.k : c.nq), "Input exceeds bounded head/query size");
    ReaderDirectory dir;
    auto options = std::make_shared<SPTAG::Helper::ReaderOptions>(SPTAG::VectorValueType::Float,c.dim,
        heads ? SPTAG::VectorFileType::DEFAULT : SPTAG::VectorFileType::XVEC,"|",1,false);
    auto reader = SPTAG::Helper::VectorSetReader::CreateInstance(options);
    Check(reader != nullptr, "Reader creation failed"); OK(reader->LoadFile(path), "Load vectors");
    auto shape = reader->GetVectorSet(0,0);
    Check(shape && shape->Dimension() == c.dim, "Dimension mismatch");
    auto data = reader->GetVectorSet(0,heads ? static_cast<int>(count+1) : c.nq);
    Check(data && data->Available() && data->Count() == static_cast<int>(heads ? count : c.nq) &&
          data->Dimension() == c.dim && data->GetValueType() == SPTAG::VectorValueType::Float, "Shape mismatch");
    auto values = static_cast<const float*>(data->GetData());
    for (size_t i=0; i<static_cast<size_t>(data->Count())*c.dim; ++i) Check(std::isfinite(values[i]),"Nonfinite input");
    return data;
}
std::shared_ptr<Index> Load(const std::string& path, int dim) {
    std::shared_ptr<Index> index;
    OK(Index::LoadIndex(path,index),"Load native index " + path);
    Check(index && index->GetIndexAlgoType() == SPTAG::IndexAlgoType::BKT &&
          index->GetVectorValueType() == SPTAG::VectorValueType::Float &&
          index->GetDistCalcMethod() == SPTAG::DistCalcMethod::L2 &&
          index->GetFeatureDim() == dim && index->GetNumDeleted() == 0 && index->GetQuantizer() == nullptr &&
          index->GetNumSamples() > 0, "Require unquantized Float128 L2 BKT");
    OK(index->SetParameter("NumberOfThreads","1"),"Set threads");
#ifdef H2_CALLBACK_COUNTER
    auto* bkt = dynamic_cast<SPTAG::BKT::Index<float>*>(index.get());
    Check(bkt != nullptr,"Counter requires BKT Float");
    uint64_t probe=0;
    auto original=bkt->BenchmarkWrapDistance(&probe);
    const auto* sample=index->GetSample(0);
    for(int i=0;i<3;++i) Check(index->ComputeDistance(sample,sample) == 0,"Counter changed native self distance");
    bkt->BenchmarkRestoreDistance(original);
    Check(probe == 3,"Known-call distance counter test failed");
    index->ComputeDistance(sample,sample);
    Check(probe == 3,"Counter restoration failed");
#endif
    return index;
}
void MaxCheck(Index& index, int n) {
    OK(index.SetParameter("MaxCheck",std::to_string(n).c_str()),"Set MaxCheck");
    OK(index.UpdateIndex(),"Update search workspace");
}
Results Search(Index& index, const float* q, int k) {
    SPTAG::COMMON::QueryResultSet<float> result(q,k);
    OK(index.SearchIndex(result),"SearchIndex failed");
    Results out;
    for (int i=0;i<k;++i) {
        const auto* r = result.GetResult(i);
        if (r->VID >= 0) {
            Check(r->VID < index.GetNumSamples() && std::isfinite(r->Dist), "Invalid search result");
            out.emplace_back(r->Dist,r->VID);
        }
    }
    return out;
}
Results CountedSearch(Index& index, const float* q, int k, uint64_t& count) {
    count = 0;
#ifdef H2_CALLBACK_COUNTER
    auto* bkt = dynamic_cast<SPTAG::BKT::Index<float>*>(&index);
    Check(bkt != nullptr,"Counter requires BKT Float");
    auto original = bkt->BenchmarkWrapDistance(&count);
    Results result;
    try { result = Search(index,q,k); }
    catch (...) { bkt->BenchmarkRestoreDistance(original); throw; }
    bkt->BenchmarkRestoreDistance(original);
    Check(count > 0,"Counter did not intercept SearchIndex");
    Check(result == Search(index,q,k),"Instrumented/ordinary IDs or distances differ");
    return result;
#else
    return Search(index,q,k);
#endif
}
auto Distance = SPTAG::COMMON::DistanceCalcSelector<float>(SPTAG::DistCalcMethod::L2);
Results Exact(const float* q, int count, const std::function<const float*(int)>& sample, int dim, int k) {
    Results result; result.reserve(count);
    for (int i=0;i<count;++i) result.emplace_back(Distance(q,sample(i),dim),i);
    std::partial_sort(result.begin(),result.begin()+k,result.end());
    result.resize(k); return result;
}
double Recall(const Results& result, const Results& truth, int k) {
    int hit=0;
    for (auto x : result) for (auto y : truth) if (x.second == y.second) { ++hit; break; }
    return static_cast<double>(hit)/k;
}
template<class T> void Write(std::ofstream& out, const T& value) {
    out.write(reinterpret_cast<const char*>(&value),sizeof(value));
}
template<class T> void WriteArray(std::ofstream& out, const std::vector<T>& values) {
    out.write(reinterpret_cast<const char*>(values.data()),values.size()*sizeof(T));
}
std::vector<int> Mapping(const Config& c, Index& upper, SPTAG::VectorSet& heads) {
    const int n = upper.GetNumSamples();
    auto bytes = fs::file_size(c.ids);
    std::ifstream in(c.ids,std::ios::binary);
    if (bytes == uint64_t(n)*8+4) {
        int32_t count; in.read(reinterpret_cast<char*>(&count),4); Check(count == n,"HeadIDs count mismatch");
    } else if (bytes == uint64_t(n)*8+8) {
        int32_t shape[2]; in.read(reinterpret_cast<char*>(shape),8);
        uint64_t count; std::memcpy(&count,shape,8);
        Check((shape[0] == n && shape[1] == 1) || count == uint64_t(n),
              "HeadIDs require native Dataset shape (count,1) or uint64 count");
    } else Check(bytes == uint64_t(n)*8,"HeadIDs require headerless uint64 or count-prefixed uint64");
    std::vector<int> self(heads.Count(),-1);
    for (int h=0;h<n;++h) {
        uint64_t lower; in.read(reinterpret_cast<char*>(&lower),8);
        Check(in.good() && lower < self.size() && self[lower] == -1,"Invalid/duplicate H2 map");
        Check(std::memcmp(heads.GetVector(lower),upper.GetSample(h),c.dim*sizeof(float)) == 0,"H2/H1 map vector mismatch");
        self[lower] = h;
    }
    return self;
}
struct CSR {
    int replicas;
    std::vector<uint64_t> offsets;
    std::vector<int32_t> members, owners;
};
std::vector<int> Select(const Results& candidates, int self, int replicas, Index& upper) {
    std::vector<int> selected;
    if (self >= 0) selected.push_back(self);
    for (auto candidate : candidates) {
        if (selected.size() == static_cast<size_t>(replicas)) break;
        if (std::find(selected.begin(),selected.end(),candidate.second) != selected.end()) continue;
        bool accepted = true;
        for (int chosen : selected)
            if (upper.ComputeDistance(upper.GetSample(candidate.second),upper.GetSample(chosen)) < candidate.first) {
                accepted=false; break;
            }
        if (accepted) selected.push_back(candidate.second);
    }
    for (auto candidate : candidates) {
        if (selected.size() == static_cast<size_t>(replicas)) break;
        if (std::find(selected.begin(),selected.end(),candidate.second) == selected.end()) selected.push_back(candidate.second);
    }
    Check(selected.size() == static_cast<size_t>(replicas),"Insufficient assignment candidates");
    return selected;
}
std::vector<CSR> Assign(const Config& c, Index& upper, SPTAG::VectorSet& heads, const std::vector<int>& self) {
    MaxCheck(upper,c.assignCheck);
    const int lowerCount=heads.Count(), upperCount=upper.GetNumSamples();
    std::vector<CSR> csrs;
    for (int r : c.replicas) {
        CSR csr; csr.replicas=r; csr.owners.resize(static_cast<size_t>(lowerCount)*r); csrs.push_back(std::move(csr));
    }
    std::vector<int32_t> candidateIDs(static_cast<size_t>(lowerCount)*c.candidates,-1);
    std::vector<float> candidateDistances(candidateIDs.size(),SPTAG::MaxDist);
    std::atomic<int> next(0); std::atomic<bool> failed(false);
    std::exception_ptr error; std::mutex errorLock;
    auto worker = [&] {
        try {
            for (int lower;(lower=next.fetch_add(1)) < lowerCount && !failed.load();) {
                auto results=Search(upper,static_cast<const float*>(heads.GetVector(lower)),c.candidates);
                for (size_t i=0;i<results.size();++i) {
                    candidateIDs[static_cast<size_t>(lower)*c.candidates+i]=results[i].second;
                    candidateDistances[static_cast<size_t>(lower)*c.candidates+i]=results[i].first;
                }
                for (auto& csr : csrs) {
                    auto selected=Select(results,self[lower],csr.replicas,upper);
                    std::copy(selected.begin(),selected.end(),csr.owners.begin()+static_cast<size_t>(lower)*csr.replicas);
                }
            }
        } catch (...) { std::lock_guard<std::mutex> lock(errorLock); error=std::current_exception(); failed=true; }
    };
    std::vector<std::thread> workers;
    for (int i=0;i<c.buildThreads;++i) workers.emplace_back(worker);
    for (auto& t : workers) t.join();
    if (error) std::rethrow_exception(error);
    std::ofstream candidates(fs::path(c.output)/"assignment_candidates.bin",std::ios::binary);
    Write(candidates,int32_t(lowerCount)); Write(candidates,int32_t(c.candidates));
    WriteArray(candidates,candidateIDs); WriteArray(candidates,candidateDistances);
    Check(candidates.good(),"Cannot persist candidates");
    std::ofstream provenance(fs::path(c.output)/"assignments.jsonl");
    for (auto& csr : csrs) {
        csr.offsets.assign(upperCount+1,0);
        for (int parent : csr.owners) ++csr.offsets[parent+1];
        std::partial_sum(csr.offsets.begin(),csr.offsets.end(),csr.offsets.begin());
        auto cursor=csr.offsets; csr.members.resize(csr.owners.size());
        for (int lower=0;lower<lowerCount;++lower)
            for (int r=0;r<csr.replicas;++r)
                csr.members[cursor[csr.owners[static_cast<size_t>(lower)*csr.replicas+r]]++]=lower;
        uint64_t minimum=UINT64_MAX,maximum=0;
        for (int h=0;h<upperCount;++h) {
            auto size=csr.offsets[h+1]-csr.offsets[h]; minimum=std::min(minimum,size); maximum=std::max(maximum,size);
        }
        std::ofstream out(fs::path(c.output)/("replicas_"+std::to_string(csr.replicas)+".csr"),std::ios::binary);
        out.write("H2SWCSR1",8); Write(out,int32_t(lowerCount)); Write(out,int32_t(upperCount)); Write(out,int32_t(csr.replicas));
        Write(out,uint64_t(csr.members.size())); WriteArray(out,csr.offsets); WriteArray(out,csr.members); WriteArray(out,csr.owners);
        Check(out.good(),"Cannot persist CSR");
        provenance << "{\"replicas\":" << csr.replicas << ",\"assignments\":" << csr.members.size()
                   << ",\"row_min\":" << minimum << ",\"row_max\":" << maximum
                   << ",\"self_pinned\":" << upperCount << ",\"same_candidates_across_replicas\":true}\n";
    }
    Check(provenance.good(),"Cannot persist provenance");
    return csrs;
}
struct Expansion { Results result; uint64_t unique=0,entries=0; };
Expansion Expand(const Results& parents, int beam, const CSR& csr, const float* q,
                 SPTAG::VectorSet& heads, const Config& c, std::vector<uint32_t>& visited, uint32_t& epoch) {
    if (++epoch == 0) { std::fill(visited.begin(),visited.end(),0); ++epoch; }
    Expansion out;
    Results scored;
    for (int p=0;p<std::min(beam,static_cast<int>(parents.size()));++p) {
        int parent=parents[p].second;
        for (uint64_t i=csr.offsets[parent];i<csr.offsets[parent+1];++i) {
            ++out.entries;
            int id=csr.members[i];
            if (visited[id] == epoch) continue;
            visited[id]=epoch; ++out.unique;
            scored.emplace_back(Distance(q,static_cast<const float*>(heads.GetVector(id)),c.dim),id);
        }
    }
    auto keep=std::min(static_cast<size_t>(c.k),scored.size());
    std::partial_sort(scored.begin(),scored.begin()+keep,scored.end()); scored.resize(keep);
    out.result=std::move(scored); return out;
}
double Micros(Clock::time_point start) { return std::chrono::duration<double,std::micro>(Clock::now()-start).count(); }
double Mean(const std::vector<double>& v) { return std::accumulate(v.begin(),v.end(),0.0)/v.size(); }
double P95(std::vector<double> v) { std::sort(v.begin(),v.end()); return v[static_cast<size_t>(std::ceil(.95*v.size()))-1]; }
void Stats(std::ostream& out, const std::string& name, const std::vector<double>& v) {
    out << ",\"" << name << "_mean\":" << Mean(v) << ",\"" << name << "_p95\":" << P95(v);
}
void IDs(std::ostream& out, const Results& result) {
    out << "[";
    for (size_t i=0;i<result.size();++i) { if(i) out << ','; out << result[i].second; }
    out << "]";
}
void Run(const Config& c, const std::string& configFile) {
    Check(!fs::exists(c.output),"Refusing to reuse output directory");
    Check(fs::create_directory(c.output),"Output parent must exist");
    fs::copy_file(configFile,fs::path(c.output)/"input.ini");
    auto heads=ReadVectors(c.heads,true,c), queries=ReadVectors(c.queries,false,c);
    auto h2=Load(c.h2,c.dim), flat=Load(c.flat,c.dim);
    Check(h2->GetNumSamples() >= std::max(c.candidates,*std::max_element(c.beams.begin(),c.beams.end())) &&
          h2->GetNumSamples() <= heads->Count() && flat->GetNumSamples() == heads->Count(),"Index sizes incompatible");
    for (int i=0;i<heads->Count();++i)
        Check(std::memcmp(heads->GetVector(i),flat->GetSample(i),c.dim*sizeof(float)) == 0,"Flat/H1 identity mismatch");
    auto self=Mapping(c,*h2,*heads);
    std::cout << "H2_SWEEP_INPUT lower=" << heads->Count() << " upper=" << h2->GetNumSamples() << std::endl;
    auto csrs=Assign(c,*h2,*heads,self);
    MaxCheck(*h2,c.searchCheck); MaxCheck(*flat,c.flatCheck);
    std::vector<Results> truth(c.nq), exactParents(c.nq);
    std::ofstream oracle(fs::path(c.output)/"oracle_h1.jsonl");
    for (int q=0;q<c.nq;++q) {
        auto query=static_cast<const float*>(queries->GetVector(q));
        truth[q]=Exact(query,heads->Count(),[&](int i){return static_cast<const float*>(heads->GetVector(i));},c.dim,c.k);
        exactParents[q]=Exact(query,h2->GetNumSamples(),[&](int i){return static_cast<const float*>(h2->GetSample(i));},
                             c.dim,h2->GetNumSamples());
        oracle << "{\"query\":" << q << ",\"ids\":"; IDs(oracle,truth[q]); oracle << "}\n";
    }
    std::ofstream rows(fs::path(c.output)/"queries.jsonl"), summaries(fs::path(c.output)/"summary.jsonl");
    rows << std::setprecision(12); summaries << std::setprecision(12);
#ifdef H2_CALLBACK_COUNTER
    constexpr bool counted=true;
#else
    constexpr bool counted=false;
#endif
    std::vector<double> flatCost,flatRecall,flatTime;
    for (int w=0;w<c.warmup;++w) Search(*flat,static_cast<const float*>(queries->GetVector(w%c.nq)),c.k);
    for (int q=0;q<c.nq;++q) {
        auto query=static_cast<const float*>(queries->GetVector(q)); uint64_t count;
        auto result=CountedSearch(*flat,query,c.k,count);
        double us=0;
        for (int rep=0;rep<c.repeats;++rep) { auto start=Clock::now(); auto r=Search(*flat,query,c.k); us+=Micros(start); Check(r==result,"Flat repeat drift"); }
        flatCost.push_back(count); flatRecall.push_back(Recall(result,truth[q],c.k)); flatTime.push_back(us/c.repeats);
        rows << "{\"mode\":\"flat\",\"query\":" << q << ",\"graph_distances\":";
        if(counted) rows << count; else rows << "null";
        rows << ",\"recall\":" << flatRecall.back() << ",\"ordinary_us\":" << flatTime.back() << ",\"ids\":";
        IDs(rows,result); rows << "}\n";
    }
    summaries << "{\"mode\":\"flat\",\"counted\":" << (counted?"true":"false") << ",\"queries\":" << c.nq;
    if(counted) Stats(summaries,"total_distances",flatCost);
    Stats(summaries,"recall",flatRecall); Stats(summaries,"ordinary_us",flatTime); summaries << "}\n";
    std::ofstream boundary(fs::path(c.output)/"boundary.jsonl"), histogram(fs::path(c.output)/"boundary_histogram.jsonl");
    std::ofstream boundarySummary(fs::path(c.output)/"boundary_summary.jsonl");
    boundary << std::setprecision(12);
    boundarySummary << std::setprecision(12);
    std::vector<uint32_t> visited(heads->Count(),0); uint32_t epoch=0;
    for (const auto& csr : csrs) {
        std::map<int,uint64_t> ranksHistogram;
        for (int q=0;q<c.nq;++q) {
            std::vector<int> ranks(h2->GetNumSamples()); std::vector<float> distances(h2->GetNumSamples());
            for (size_t i=0;i<exactParents[q].size();++i) { ranks[exactParents[q][i].second]=i+1; distances[exactParents[q][i].second]=exactParents[q][i].first; }
            for (auto target : truth[q]) {
                int nearest=h2->GetNumSamples()+1;
                boundary << "{\"replicas\":" << csr.replicas << ",\"query\":" << q << ",\"target\":" << target.second
                         << ",\"target_distance\":" << target.first << ",\"owners\":[";
                for (int r=0;r<csr.replicas;++r) {
                    int owner=csr.owners[static_cast<size_t>(target.second)*csr.replicas+r]; nearest=std::min(nearest,ranks[owner]);
                    if(r) boundary << ',';
                    boundary << "{\"id\":" << owner << ",\"rank\":" << ranks[owner] << ",\"distance\":" << distances[owner] << "}";
                }
                boundary << "],\"nearest_owner_rank\":" << nearest << "}\n"; ++ranksHistogram[nearest];
            }
        }
        for(auto item:ranksHistogram) histogram << "{\"replicas\":" << csr.replicas << ",\"rank\":" << item.first << ",\"targets\":" << item.second << "}\n";
        const uint64_t targetCount=static_cast<uint64_t>(c.nq)*c.k;
        auto rankQuantile = [&](double quantile) {
            auto threshold=static_cast<uint64_t>(std::ceil(quantile*targetCount));
            uint64_t cumulative=0;
            for(const auto& item:ranksHistogram) {
                cumulative+=item.second;
                if(cumulative>=threshold) return item.first;
            }
            throw std::runtime_error("Incomplete boundary histogram");
        };
        boundarySummary << "{\"replicas\":" << csr.replicas << ",\"targets\":" << targetCount
                        << ",\"nearest_owner_rank_median\":" << rankQuantile(.5)
                        << ",\"nearest_owner_rank_p90\":" << rankQuantile(.9)
                        << ",\"nearest_owner_rank_p95\":" << rankQuantile(.95)
                        << ",\"nearest_owner_rank_p99\":" << rankQuantile(.99)
                        << ",\"nearest_owner_rank_max\":" << ranksHistogram.rbegin()->first;
        for(int threshold:{16,24,32,64,128}) {
            uint64_t cumulative=0;
            for(const auto& item:ranksHistogram) if(item.first<=threshold) cumulative+=item.second;
            boundarySummary << ",\"fraction_rank_le_" << threshold << "\":" << double(cumulative)/targetCount;
        }
        boundarySummary << "}\n";
        for (int beam : c.beams) {
            // Separate top-beam SearchIndex calls: result queue size affects native graph work.
            for(int w=0;w<c.warmup;++w) {
                auto query=static_cast<const float*>(queries->GetVector(w%c.nq)); auto parents=Search(*h2,query,beam);
                Expand(parents,beam,csr,query,*heads,c,visited,epoch);
            }
            std::vector<double> costs,graphCosts,unique,entries,recalls,times,oracleRecalls,oracleUnique;
            for (int q=0;q<c.nq;++q) {
                auto query=static_cast<const float*>(queries->GetVector(q)); uint64_t count;
                auto parents=CountedSearch(*h2,query,beam,count);
                auto expansion=Expand(parents,beam,csr,query,*heads,c,visited,epoch);
                double us=0;
                for (int rep=0;rep<c.repeats;++rep) {
                    auto start=Clock::now(); auto p=Search(*h2,query,beam);
                    auto e=Expand(p,beam,csr,query,*heads,c,visited,epoch); us+=Micros(start);
                    Check(p==parents && e.result==expansion.result,"Ordinary repeat drift");
                }
                auto diagnostic=Expand(exactParents[q],beam,csr,query,*heads,c,visited,epoch);
                costs.push_back(count+expansion.unique); graphCosts.push_back(count); unique.push_back(expansion.unique);
                entries.push_back(expansion.entries); recalls.push_back(Recall(expansion.result,truth[q],c.k)); times.push_back(us/c.repeats);
                oracleRecalls.push_back(Recall(diagnostic.result,truth[q],c.k)); oracleUnique.push_back(diagnostic.unique);
                rows << "{\"mode\":\"h2\",\"replicas\":" << csr.replicas << ",\"beam\":" << beam << ",\"query\":" << q
                     << ",\"graph_distances\":";
                if(counted) rows << count; else rows << "null";
                rows << ",\"h1_unique_distances\":" << expansion.unique << ",\"assignment_entries\":" << expansion.entries
                     << ",\"total_distances\":";
                if(counted) rows << count+expansion.unique; else rows << "null";
                rows << ",\"recall\":" << recalls.back() << ",\"ordinary_us\":" << times.back()
                     << ",\"oracle_nonruntime_recall\":" << oracleRecalls.back() << ",\"oracle_nonruntime_unique\":" << diagnostic.unique << ",\"ids\":";
                IDs(rows,expansion.result); rows << "}\n";
            }
            summaries << "{\"mode\":\"h2\",\"replicas\":" << csr.replicas << ",\"beam\":" << beam
                      << ",\"h2_nodes\":" << h2->GetNumSamples() << ",\"actual_ratio\":" << double(h2->GetNumSamples())/heads->Count()
                      << ",\"counted\":" << (counted?"true":"false");
            if(counted) { Stats(summaries,"total_distances",costs); Stats(summaries,"graph_distances",graphCosts);
                summaries << ",\"work_ratio_vs_flat_mean\":" << Mean(costs)/Mean(flatCost); }
            Stats(summaries,"h1_unique_distances",unique); Stats(summaries,"assignment_entries",entries);
            Stats(summaries,"recall",recalls); Stats(summaries,"ordinary_us",times);
            Stats(summaries,"oracle_nonruntime_recall",oracleRecalls); Stats(summaries,"oracle_nonruntime_unique",oracleUnique);
            summaries << "}\n"; summaries.flush(); rows.flush();
            std::cout << "H2_SWEEP_POINT replicas=" << csr.replicas << " beam=" << beam << " recall=" << Mean(recalls) << std::endl;
        }
    }
    rows.flush(); summaries.flush(); boundary.flush(); histogram.flush(); boundarySummary.flush(); oracle.flush();
    Check(rows.good() && summaries.good() && boundary.good() && histogram.good() &&
          boundarySummary.good() && oracle.good(),"Output write failure");
    std::ofstream complete(fs::path(c.output)/"COMPLETE");
    complete << "Navigation only; no SSD/data recall. Exact oracle excluded from runtime.\n";
    complete.flush();
    Check(complete.good(),"Completion write failure");
}

void Fixture(const fs::path& root) {
    Check(!fs::exists(root) && fs::create_directory(root),"Fixture output exists");
    constexpr int n=256,d=128;
    std::vector<float> vectors(n*d);
    uint32_t state=7;
    for (auto& x:vectors) { state=1664525*state+1013904223; x=float((state>>16)%1000)/100; }
    std::ofstream heads(root/"heads.bin",std::ios::binary);
    Write(heads,int32_t(n)); Write(heads,int32_t(d)); WriteArray(heads,vectors); heads.close();
    std::ofstream queries(root/"queries.fvecs",std::ios::binary);
    for(int q=0;q<4;++q) { Write(queries,int32_t(d)); queries.write(reinterpret_cast<char*>(vectors.data()+q*d),d*4); } queries.close();
    std::vector<float> upper(vectors.begin(),vectors.begin()+128*d);
    std::ofstream ids(root/"ids.bin",std::ios::binary);
    for(uint64_t i=0;i<128;++i) Write(ids,i); ids.close();
    for(auto name:{"flat","h2"}) {
        auto index=Index::CreateInstance(SPTAG::IndexAlgoType::BKT,SPTAG::VectorValueType::Float);
        for(auto parameter:std::vector<std::pair<const char*,const char*>>{
            {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"TPTNumber","1"},{"TPTLeafSize","32"},
            {"NeighborhoodSize","16"},{"CEF","64"},{"MaxCheckForRefineGraph","128"},{"RefineIterations","1"},
            {"BKTKmeansK","8"},{"Samples","128"},{"BKTLambdaFactor","1"}})
            OK(index->SetParameter(parameter.first,parameter.second),"Fixture parameter");
        bool flat=std::string(name)=="flat";
        OK(index->BuildIndex(flat?vectors.data():upper.data(),flat?n:128,d,false,false),"Fixture build");
        OK(index->SaveIndex((root/name).string()),"Fixture save");
    }
}
}
int main(int argc,char** argv) {
    try {
        if(argc==3 && std::string(argv[1])=="--make-fixture") { Fixture(fs::absolute(argv[2])); return 0; }
        Check(argc==3 && std::string(argv[1])=="--config","Usage: h2sweepbench --config absolute.ini");
        auto file=fs::absolute(argv[2]).string(); auto config=ReadConfig(file); Run(config,file); return 0;
    } catch(const std::exception& error) {
        std::cerr << "H2_SWEEP_ERROR " << error.what() << '\n'; return 1;
    }
}
