#include "inc/CoreInterface.h"
#include "inc/Core/VectorIndex.h"
#include "inc/Core/SPANN/Options.h"
#include "inc/Helper/SimpleIniReader.h"
#include "inc/Core/Common/GraphAccessStats.h"
#include "../../NativeNProbeSweep.h"
#include <array>
#include <charconv>
#include <cctype>
#include <chrono>
#include <cmath>
#include <fstream>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <limits>
#include <regex>
#include <set>
#include <sstream>
using namespace SPTAG;
extern char** environ;
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
void* operator new(std::size_t size) {
    if (auto* stats = COMMON::g_graphAccessStats) {
        ++stats->m_cppAllocations;
        stats->m_cppAllocationBytes += size;
    }
    if (void* pointer = std::malloc(size ? size : 1)) return pointer;
    throw std::bad_alloc();
}
void operator delete(void* pointer) noexcept { std::free(pointer); }
void operator delete(void* pointer, std::size_t) noexcept { std::free(pointer); }
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete[](void* pointer) noexcept { std::free(pointer); }
void operator delete[](void* pointer, std::size_t) noexcept { std::free(pointer); }
#endif

void Require(bool value,const std::string& message) {
    if(!value) throw std::runtime_error(message);
}
template<class T> struct Matrix { std::vector<T> data;std::size_t rows=0,cols=0,rank=0; };
template<class T> Matrix<T> ReadNpy(const std::string& path,const std::string& dtype) {
    std::ifstream stream(path,std::ios::binary|std::ios::ate);
    Require(bool(stream),"Cannot open NPY "+path);
    const auto fileSize=stream.tellg();
    Require(fileSize>=10,"Truncated NPY header "+path);
    stream.seekg(0);
    unsigned char prefix[8]{};
    Require(bool(stream.read(reinterpret_cast<char*>(prefix),8)) &&
        std::string(reinterpret_cast<char*>(prefix),6)==std::string("\x93NUMPY",6),"Invalid NPY "+path);
    Require((prefix[6]==1 || prefix[6]==2) && prefix[7]==0,"Unsupported NPY version");
    const unsigned lengthBytes=prefix[6]==1?2:4;
    unsigned char encodedLength[4]{};
    Require(bool(stream.read(reinterpret_cast<char*>(encodedLength),lengthBytes)),
        "Truncated NPY header length "+path);
    std::uint32_t length=0;
    for(unsigned i=0;i<lengthBytes;++i) length|=std::uint32_t(encodedLength[i])<<(8*i);
    Require(length>0 && length<=65536 &&
        std::uint64_t(fileSize)>=8+lengthBytes+std::uint64_t(length),"Invalid NPY header length "+path);
    std::string header(length,'\0');
    Require(bool(stream.read(header.data(),length)) && header.back()=='\n',"Invalid NPY header "+path);
    const auto field=[&](const std::string& expression) {
        const std::regex pattern(expression);
        auto at=std::sregex_iterator(header.begin(),header.end(),pattern);
        Require(at!=std::sregex_iterator(),"Missing NPY header field "+path);
        const std::string value=(*at)[1];
        Require(++at==std::sregex_iterator(),"Duplicate NPY header field "+path);
        return value;
    };
    Require(field(R"(['"]descr['"]\s*:\s*['"]([^'"]+)['"])")==dtype &&
        field(R"(['"]fortran_order['"]\s*:\s*(True|False))")=="False",
        "NPY type/order mismatch "+path);
    const auto shape=field(R"(['"]shape['"]\s*:\s*\(([^)]*)\))");
    std::smatch dimensions;
    Require(std::regex_match(shape,dimensions,std::regex(R"(\s*([0-9]+)\s*,\s*(?:([0-9]+)\s*,?\s*)?)")),
        "NPY requires a one- or two-dimensional shape "+path);
    Matrix<T> result;
    const auto dimension=[&](const std::string& text) {
        std::size_t value=0;
        const auto parsed=std::from_chars(text.data(),text.data()+text.size(),value);
        Require(parsed.ec==std::errc() && parsed.ptr==text.data()+text.size(),"Invalid NPY dimension "+path);
        return value;
    };
    result.rows=dimension(dimensions[1]);
    result.rank=dimensions[2].matched?2:1;
    result.cols=result.rank==2?dimension(dimensions[2]):1;
    Require(result.rows && result.cols && result.rows<=10000000 && result.cols<=4096,"Invalid NPY shape");
    Require(result.rows<=(std::numeric_limits<std::size_t>::max)()/result.cols/sizeof(T),
        "NPY byte extent overflow "+path);
    const auto bytes=result.rows*result.cols*sizeof(T);
    Require(bytes<=static_cast<std::size_t>((std::numeric_limits<std::streamsize>::max)()) &&
        std::uint64_t(fileSize)==8+lengthBytes+std::uint64_t(length)+bytes,
        "NPY payload size mismatch "+path);
    result.data.resize(result.rows*result.cols);
    Require(bool(stream.read(reinterpret_cast<char*>(result.data.data()),bytes)),
        "Truncated NPY "+path);
    return result;
}
template<class T> void Write(const std::string& name,const std::vector<T>& values) {
    std::ofstream stream(name,std::ios::binary);
    Require(bool(stream.write(reinterpret_cast<const char*>(values.data()),values.size()*sizeof(T))),"Output write failed");
    stream.close();
    Require(bool(stream),"Output close failed");
}
struct Config {
    Helper::IniReader ini;
    std::string index,queries,predicateFile,predicate,mode,valueType;
    bool phaseTiming=false;
    int count,warmup,maxCheck,anchorCount,additionalMaxCheck,postingPageLimit;
    std::vector<int> probes;
    explicit Config(const char* path) {
        Require(ini.LoadIniFile(path)==ErrorCode::Success,"Cannot read native INI");
        index=Get("Benchmark","Index");queries=Get("Benchmark","Queries");
        predicate=Get("Benchmark","Predicate");
        valueType=ini.GetParameter<std::string>("Benchmark","ValueType","Float");
        Require(valueType=="Float" || valueType=="UInt8","Benchmark.ValueType must be Float or UInt8");
        const auto phase=ini.GetParameter<std::string>("Benchmark","PhaseTiming","false");
        Require(phase=="true" || phase=="false","Benchmark.PhaseTiming must be true or false");
        phaseTiming=phase=="true";
        Require(Get("SearchSSDIndex","LogPhaseTime")==phase,
            "LogPhaseTime must match explicit Benchmark.PhaseTiming (default false)");
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        Require(!phaseTiming,"PhaseTiming requires the normal core, not work-counter instrumentation");
#endif
        mode=ini.GetParameter<bool>("SearchSSDIndex","EnablePostingNavigation",false) ? "posting" : "graph";
        Require(!ini.DoesParameterExist("SearchSSDIndex","PostingMinCandidates") &&
            !ini.DoesParameterExist("BuildSSDIndex","PostingMinCandidates"),
            "PostingMinCandidates was removed even when OFF; migrate to PostingAnchorCount and PostingAdditionalMaxCheck");
        SPANN::Options checked;
        for (const char* key : {"MaxCheck", "PostingAnchorCount", "PostingAdditionalMaxCheck"})
            Require(checked.SetParameter("BuildSSDIndex",key,Get("SearchSSDIndex",key).c_str())==ErrorCode::Success,
                std::string("Invalid post-graph budget parameter ")+key);
        Require(checked.m_maxCheck==2048 || checked.m_maxCheck==4096,"MaxCheck must be 2048 or 4096");
        maxCheck=checked.m_maxCheck;
        anchorCount=checked.m_postingAnchorCount;
        additionalMaxCheck=checked.m_postingAdditionalMaxCheck;
        predicateFile=ini.GetParameter<std::string>("Benchmark","PredicateFile","");
        const auto queryCount=Get("Benchmark","MaxQueries"),warmupCount=Get("Benchmark","Warmup");
        Require((queryCount=="32" || queryCount=="1000" || queryCount=="all") &&
            queryCount==warmupCount,"Use equal full-query warmup and measured windows");
        count=queryCount=="all"?0:std::stoi(queryCount);warmup=count;
        Require(predicate=="empty" || predicate=="categorical" || predicate=="dnf","Unsupported predicate encoding");
        Helper::IniReader saved;
        Require(saved.LoadIniFile((index+"/tenant_0/indexloader.ini").c_str())==ErrorCode::Success,"Saved native capability header");
        Require(saved.GetParameter<std::string>("Index","ValueType","")==valueType &&
            saved.GetParameter<std::string>("Base","ValueType","")==valueType,
            "Benchmark.ValueType does not match saved index ValueType");
        Require(saved.GetParameter<std::string>("Index","IndexAlgoType","")=="SPANN" &&
            saved.GetParameter<std::string>("Base","IndexAlgoType","")=="BKT" &&
            saved.GetParameter<std::string>("BuildSSDIndex","Storage","")=="STATIC",
            "Benchmark supports a loaded, immutable STATIC SPANN/BKT snapshot only");
        for(auto p:std::vector<std::pair<std::string,std::string>>{
            {"NumberOfThreads","1"},{"ResultNum","10"},
            {"EnableHybridDistance","false"},{"DumpHeads","0"},
            {"LogPathStats","false"}})
            Require(Get("SearchSSDIndex",p.first)==p.second,"Fixed native protocol violated: "+p.first);
        const auto pageLimit=Get("SearchSSDIndex","SearchPostingPageLimit");
        const auto parsedPageLimit=std::from_chars(pageLimit.data(),pageLimit.data()+pageLimit.size(),postingPageLimit);
        Require(parsedPageLimit.ec==std::errc() && parsedPageLimit.ptr==pageLimit.data()+pageLimit.size() &&
            postingPageLimit>0,"SearchPostingPageLimit must be a positive integer");
        Require(ini.GetParameter<int>("SearchSSDIndex","InternalResultNum",0)>=10,
            "Native nprobe must be at least topk");
        if(ini.DoesSectionExist("SearchSweep")) {
            Require(ini.GetParameters("SearchSweep").size()==1,"Only native SearchSweep.NProbe is supported");
            probes=NativeNProbeSweep::Parse(Get("SearchSweep","NProbe"),10);
        } else {
            probes={ini.GetParameter<int>("SearchSSDIndex","InternalResultNum",0)};
        }
        const std::set<std::string> keys={"isexecute","buildssdindex","internalresultnum","resultnum",
            "numberofthreads","hashtableexponent","maxcheck",
            "maxdistratio","searchpostingpagelimit","disablecrossedges",
            "logphasetime","logpathstats","dumpheads","enablehybriddistance","enablepostingnavigation",
            "postinganchorcount","postingadditionalmaxcheck"};
        for(const auto& p:ini.GetParameters("SearchSSDIndex"))
            Require(keys.count(p.first),"Unsupported search parameter "+p.first);
        for(char** entry=environ;*entry;++entry) {
            std::string key=*entry;key=key.substr(0,key.find('='));
            Require(key.rfind("SPTAG_",0)!=0 && key.rfind("SPANN_",0)!=0 &&
                key!="LD_PRELOAD" && key.rfind("OMP_",0)!=0,"Search environment override "+key);
        }
    }
    std::string Get(const std::string& section,const std::string& key) {
        Require(ini.DoesParameterExist(section.c_str(),key.c_str()),"Missing INI "+section+"."+key);
        return ini.GetParameter<std::string>(section.c_str(),key.c_str(),"");
    }
    void Apply(TenantIndexManager& manager,const Config* previous=nullptr) {
        // Lower the declared extra first when a higher base would transiently overflow.
        if(previous && maxCheck>previous->maxCheck && additionalMaxCheck<previous->additionalMaxCheck)
            manager.SetSearchParam("PostingAdditionalMaxCheck",
                std::to_string(additionalMaxCheck).c_str(),"SearchSSDIndex");
        for(const auto& p:ini.GetParameters("SearchSSDIndex"))
            manager.SetSearchParam(p.first.c_str(),p.second.c_str(),"SearchSSDIndex");
    }
};
using Work=std::array<std::uint64_t,8>;
Work GetWork(int scanned) {
    const auto s=VectorIndex::GetThreadLocalPostingScanStats();
    return {s.m_readPostings,s.m_scannedVectors,s.m_matchedVectors,s.m_dedupSkippedVectors,
        static_cast<std::uint64_t>((std::max)(0,scanned)),s.m_postingPageReads,
        s.m_postingLogicalBytes,s.m_postingPhysicalBytes};
}
struct CaseIdentity {
    std::string id,config,output;
};
std::string JsonString(const std::string& value) {
    std::string result="\"";
    constexpr char hex[]="0123456789abcdef";
    for(unsigned char c:value) {
        if(c=='"' || c=='\\') {result+='\\';result+=c;}
        else if(c<32) {result+="\\u00";result+=hex[c>>4];result+=hex[c&15];}
        else result+=c;
    }
    return result+'"';
}
void WriteIdentity(const CaseIdentity& identity) {
    std::cout<<",\"case_id\":"<<JsonString(identity.id)
        <<",\"config\":"<<JsonString(identity.config)
        <<",\"output_directory\":"<<JsonString(identity.output);
}
void FlushProgress() {
    std::cout.flush();
    Require(bool(std::cout),"Progress write failed");
}
template<class T> Matrix<std::uint32_t> PrepareCase(Config& cfg,const Matrix<T>& queries) {
        if(cfg.count==0) cfg.count=cfg.warmup=static_cast<int>(queries.rows);
        Matrix<std::uint32_t> predicates;
        if(cfg.predicate!="empty") predicates=ReadNpy<std::uint32_t>(cfg.predicateFile,"<u4");
        Require(queries.rank==2 && queries.cols==128 && queries.rows>=static_cast<std::size_t>(cfg.count) &&
            (cfg.predicate=="empty" || predicates.rows==queries.rows),"Query/predicate shape mismatch");
        if(cfg.predicate=="dnf")
            for(int i=0;i<cfg.count;++i)
                Require(predicates.data[std::size_t(i)*predicates.cols]<predicates.cols,"Invalid DNF row");
        return predicates;
}
template<class T> int RunCase(Config& cfg,Matrix<T>& queries,Matrix<std::uint32_t>& predicates,
    TenantIndexManager& manager,const CaseIdentity* identity=nullptr) {
        const auto search=[&](int i) {
            const ByteArray query(reinterpret_cast<std::uint8_t*>(queries.data.data()+i*queries.cols),
                queries.cols*sizeof(T),false);
            std::uint32_t categorical[]={0x444e4633U,1,1,0,0,Cache::DNF_EQ,
                cfg.predicate=="categorical"?predicates.data[i*predicates.cols]:0};
            std::uint32_t* words=nullptr;std::size_t length=0;
            if(cfg.predicate=="categorical") {words=categorical;length=7;}
            if(cfg.predicate=="dnf") {
                auto* row=predicates.data.data()+i*predicates.cols;
                Require(row[0]<predicates.cols,"Invalid DNF row");
                words=row+1;length=row[0];
            }
            const ByteArray predicate(reinterpret_cast<std::uint8_t*>(words),length*sizeof(std::uint32_t),false);
            return manager.SearchWithPredicate(query,0,10,predicate,cfg.predicate=="empty"?0:-1);
        };
        for(int probe:cfg.probes) {
        manager.SetSearchParam("InternalResultNum",std::to_string(probe).c_str(),"SearchSSDIndex");
        const std::string output=identity?identity->output+"/nprobe_"+std::to_string(probe):
            (cfg.ini.DoesSectionExist("SearchSweep")?"nprobe_"+std::to_string(probe):".");
        if(output!=".") Require(std::filesystem::create_directory(output),"Probe output already exists");
        for(int i=0;i<cfg.warmup;++i) Require(bool(search(i)),"Warmup query failed");
        std::vector<std::int32_t> ids(cfg.count*10,-1);std::vector<float> distances(cfg.count*10,MaxDist);
        std::vector<Work> work(cfg.count);
        std::vector<double> latency(cfg.count);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        std::vector<std::array<std::uint64_t,57>> navigation(cfg.count);
        std::vector<std::int32_t> graphIds(std::size_t(cfg.count)*probe,-1);
        std::vector<float> graphDistances(std::size_t(cfg.count)*probe,MaxDist);
#endif
        const auto start=std::chrono::steady_clock::now();
        for(int i=0;i<cfg.count;++i) {
            const auto queryStart=std::chrono::steady_clock::now();
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            COMMON::GraphAccessStats stats;
            COMMON::ScopedGraphAccessStats scope(&stats);
#endif
            const auto result=search(i);Require(bool(result),"Native query failed");
            work[i]=GetWork(result->GetScanned());
            for(int k=0;k<10;++k) {ids[i*10+k]=result->GetResult(k)->VID;distances[i*10+k]=result->GetResult(k)->Dist;}
            latency[i]=std::chrono::duration<double,std::micro>(
                std::chrono::steady_clock::now()-queryStart).count();
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            navigation[i]={stats.m_distanceCalls,stats.m_graphRows,stats.m_visitedChecks,stats.m_treeNodeVisits,
                stats.m_upperDistances,stats.m_signatureChecks,stats.m_ownerReferences,stats.m_upperMembers,
                stats.m_auxiliaryMembers,stats.m_predicateCalls,stats.m_postingStates,stats.m_stateInitializedBytes,
                stats.m_selectedPostingRows,stats.m_discoveredPops,stats.m_cppAllocations,stats.m_cppAllocationBytes,
                stats.m_nativeHashClearedBytes,stats.m_postingActivations,stats.m_postingNewCandidates,
                stats.m_postingTargetMet,stats.m_postingUnderfilled,stats.m_postingBudgetUnderfilled,
                stats.m_auxiliaryFirstVisits,stats.m_auxiliaryNegativeFirstVisits,stats.m_auxiliaryVisitedSkips,
                stats.m_auxiliaryUnvisitedNegativeSkips,stats.m_selectedH2ZeroEligibleRows,
                stats.m_selectedH2ZeroFreshRows,stats.m_auxiliaryPrefetchLines,
                stats.m_h2Postings.ownerReferences,stats.m_h2Postings.memberReferences,
                stats.m_h2Postings.candidateConsiderations,stats.m_h2Postings.uniqueStates,
                stats.m_h2Postings.expandAttempts,stats.m_h2Postings.expandedSkips,
                stats.m_h2Postings.completedRows,stats.m_h2Postings.representativeDistances,
                stats.m_upperPostings.ownerReferences,stats.m_upperPostings.memberReferences,
                stats.m_upperPostings.candidateConsiderations,stats.m_upperPostings.uniqueStates,
                stats.m_upperPostings.expandAttempts,stats.m_upperPostings.expandedSkips,
                stats.m_upperPostings.completedRows,stats.m_upperPostings.representativeDistances,
                stats.m_headBefore,stats.m_headAfter,stats.m_headTarget,stats.m_graphUnique,stats.m_graphMatches,
                stats.m_anchorCount,stats.m_supplementReason,stats.m_graphLeaves,stats.m_supplementLeaves,
                stats.m_graphDistances,stats.m_supplementDistances,stats.m_preservedHeads};
            std::copy(stats.m_graphHeadIds.begin(),stats.m_graphHeadIds.end(),graphIds.begin()+std::size_t(i)*probe);
            std::copy(stats.m_graphHeadDistances.begin(),stats.m_graphHeadDistances.end(),graphDistances.begin()+std::size_t(i)*probe);
#endif
        }
        const double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
        for(int i=0;i<cfg.count;++i) {
            const auto result=search(i);Require(bool(result) && work[i]==GetWork(result->GetScanned()),"Replay work differs");
            for(int k=0;k<10;++k) Require(ids[i*10+k]==result->GetResult(k)->VID &&
                distances[i*10+k]==result->GetResult(k)->Dist,"Replay result differs");
        }
        Write(output+"/ids.i32",ids);Write(output+"/dist.f32",distances);
        Write(output+"/work.u64",work);Write(output+"/latency_us.f64",latency);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        Write(output+"/navigation.u64",navigation);
        Write(output+"/graph_ids.i32",graphIds);
        Write(output+"/graph_dist.f32",graphDistances);
        constexpr bool diagnostic=true;
#else
        constexpr bool diagnostic=false;
#endif
        std::sort(latency.begin(),latency.end());
        const auto percentile=[&](double quantile) {
            return latency[static_cast<std::size_t>(std::ceil(quantile*latency.size()))-1];
        };
        std::cout<<std::setprecision(12)<<"{\"mode\":\""<<cfg.mode<<"\",\"queries\":"<<cfg.count
                 <<",\"value_type\":\""<<cfg.valueType<<"\""
                 <<",\"search_posting_page_limit\":"<<cfg.postingPageLimit
                 <<",\"nprobe\":"<<probe
                 <<",\"max_check\":"<<cfg.maxCheck
                 <<",\"posting_anchor_count\":"<<cfg.anchorCount
                 <<",\"posting_additional_max_check\":"<<cfg.additionalMaxCheck
                 <<",\"diagnostic\":"<<(diagnostic?"true":"false")
                 <<",\"phase_timing\":"<<(cfg.phaseTiming?"true":"false")
                 <<",\"navigation_schema_version\":6,\"navigation_columns\":57"
                 <<",\"mean_ms\":"<<elapsed*1000/cfg.count<<",\"qps\":"<<cfg.count/elapsed
                 <<",\"p50_us\":"<<percentile(.50)<<",\"p95_us\":"<<percentile(.95)
                 <<",\"p99_us\":"<<percentile(.99);
        if(identity) {
            std::cout<<",\"event\":\"point\",\"warmup_queries\":"<<cfg.warmup
                <<",\"measured_queries\":"<<cfg.count<<",\"replay_queries\":"<<cfg.count;
            WriteIdentity(*identity);
        }
        std::cout<<"}\n";
        FlushProgress();
        }
        return 0;
}
template<class T> int RunBenchmark(Config& cfg,const std::string& dtype) {
    auto queries=ReadNpy<T>(cfg.queries,dtype);
    auto predicates=PrepareCase(cfg,queries);
    TenantIndexManager manager(128,"SPANN",cfg.valueType.c_str());
    cfg.Apply(manager);
    Require(manager.LoadAll(cfg.index.c_str()),"Cannot load immutable native index");
    return RunCase(cfg,queries,predicates,manager);
}
struct BatchCase {
    CaseIdentity identity;
    Config cfg;
    Matrix<std::uint32_t> predicates;
    BatchCase(CaseIdentity value):identity(std::move(value)),cfg(identity.config.c_str()) {}
};
bool Within(const std::filesystem::path& child,const std::filesystem::path& parent) {
    auto c=child.begin();
    for(auto p=parent.begin();p!=parent.end();++p,++c)
        if(c==child.end() || *c!=*p) return false;
    return true;
}
std::string AbsoluteInput(const std::string& value) {
    Require(std::filesystem::path(value).is_absolute(),"Batch input paths must be absolute: "+value);
    return std::filesystem::canonical(value).string();
}
std::vector<std::unique_ptr<BatchCase>> ReadBatch(const char* path,Helper::IniReader& ini) {
    Require(ini.GetParameters("Batch").size()==1 && ini.DoesParameterExist("Batch","CaseCount"),
        "Batch requires only CaseCount");
    const auto text=ini.GetParameter<std::string>("Batch","CaseCount","");
    int count=0;
    const auto parsed=std::from_chars(text.data(),text.data()+text.size(),count);
    Require(parsed.ec==std::errc() && parsed.ptr==text.data()+text.size() && count>0 && count<=10000,
        "Batch.CaseCount must be in [1,10000]");
    std::set<std::string> sections={"batch"};
    for(int i=1;i<=count;++i) sections.insert("case"+std::to_string(i));
    std::ifstream manifest(path);
    std::string line;
    while(std::getline(manifest,line)) {
        const auto begin=line.find_first_not_of(" \t\r");
        if(begin==std::string::npos || line[begin]!='[') continue;
        const auto end=line.find(']',begin);
        Require(end!=std::string::npos,"Invalid batch section");
        auto section=line.substr(begin+1,end-begin-1);
        std::transform(section.begin(),section.end(),section.begin(),
            [](unsigned char c) {return static_cast<char>(std::tolower(c));});
        Require(sections.erase(section)==1,"Unexpected or duplicate batch section: "+section);
    }
    Require(manifest.eof() && sections.empty(),"Incomplete batch registration");
    std::vector<std::unique_ptr<BatchCase>> cases;
    const std::set<std::string> completeKeys={"isexecute","buildssdindex","internalresultnum","resultnum",
        "numberofthreads","hashtableexponent","maxcheck","maxdistratio","searchpostingpagelimit",
        "disablecrossedges","logphasetime","logpathstats","dumpheads","enablehybriddistance",
        "enablepostingnavigation","postinganchorcount","postingadditionalmaxcheck"};
    for(int i=1;i<=count;++i) {
        const auto id="Case"+std::to_string(i);
        const auto& parameters=ini.GetParameters(id);
        Require(parameters.size()==2 && parameters.count("config") && parameters.count("outputdirectory"),
            id+" requires only Config and OutputDirectory");
        const auto config=AbsoluteInput(parameters.at("config"));
        const auto rawOutput=std::filesystem::path(parameters.at("outputdirectory"));
        Require(rawOutput.is_absolute(),"Batch output paths must be absolute");
        const auto output=std::filesystem::weakly_canonical(rawOutput);
        Require(!std::filesystem::exists(output) && !std::filesystem::is_symlink(output),
            "Batch output already exists: "+output.string());
        auto item=std::make_unique<BatchCase>(CaseIdentity{id,config,output.string()});
        auto& cfg=item->cfg;
        Require(!cfg.ini.DoesSectionExist("Batch"),"Nested batch INIs are not supported");
        cfg.index=AbsoluteInput(cfg.index);cfg.queries=AbsoluteInput(cfg.queries);
        if(cfg.predicate!="empty") cfg.predicateFile=AbsoluteInput(cfg.predicateFile);
        Require(!Within(output,cfg.index),"Batch output must not be inside the immutable index");
        const auto& search=cfg.ini.GetParameters("SearchSSDIndex");
        Require(search.size()==completeKeys.size(),"Batch cases require complete SearchSSDIndex settings");
        auto validated=VectorIndex::CreateInstance(IndexAlgoType::SPANN,
            cfg.valueType=="UInt8"?VectorValueType::UInt8:VectorValueType::Float);
        Require(bool(validated),"Cannot create native search-parameter validator");
        for(const auto& key:completeKeys) {
            Require(search.count(key),"Missing batch search setting "+key);
            Require(validated->SetParameter(key.c_str(),search.at(key).c_str(),"SearchSSDIndex")==ErrorCode::Success,
                "Invalid batch search setting "+key);
        }
        Require(search.at("isexecute")=="true" && search.at("buildssdindex")=="false",
            "Batch requires isExecute=true and BuildSsdIndex=false");
        if(!cases.empty()) {
            const auto& first=cases.front()->cfg;
            Require(cfg.phaseTiming==first.phaseTiming,"Mixed batch PhaseTiming modes");
            Require(cfg.index==first.index && cfg.valueType==first.valueType && cfg.queries==first.queries &&
                cfg.count==first.count && cfg.warmup==first.warmup,"Mixed batch index, value type or query cohort");
            // Workspace sizing and graph connectivity are not mutable batch dimensions.
            for(const char* key:{"hashtableexponent","disablecrossedges"})
                Require(search.at(key)==first.ini.GetParameters("SearchSSDIndex").at(key),
                    std::string("Batch invariant differs: ")+key);
        }
        for(const auto& previous:cases)
            Require(!Within(output,previous->identity.output) && !Within(previous->identity.output,output),
                "Batch output directories overlap");
        cases.push_back(std::move(item));
    }
    return cases;
}
template<class T> int RunBatch(std::vector<std::unique_ptr<BatchCase>>& cases,const std::string& dtype) {
    auto& first=cases.front()->cfg;
    auto queries=ReadNpy<T>(first.queries,dtype);
    for(auto& item:cases) item->predicates=PrepareCase(item->cfg,queries);
    std::cout<<"{\"event\":\"batch_begin\",\"cases\":"<<cases.size()
        <<",\"phase_timing\":"<<(first.phaseTiming?"true":"false")
        <<",\"value_type\":"<<JsonString(first.valueType)<<",\"index\":"<<JsonString(first.index)
        <<",\"queries\":"<<JsonString(first.queries)<<"}\n";
    FlushProgress();
    TenantIndexManager manager(128,"SPANN",first.valueType.c_str());
    first.Apply(manager);
    Require(manager.LoadAll(first.index.c_str()),"Cannot load immutable native index");
    std::cout<<"{\"event\":\"batch_loaded\",\"index_load_count\":1,\"query_corpus_load_count\":1}\n";
    FlushProgress();
    for(std::size_t i=0;i<cases.size();++i) {
        auto& item=*cases[i];
        if(i!=0) item.cfg.Apply(manager,&cases[i-1]->cfg);
        Require(std::filesystem::weakly_canonical(item.identity.output)==item.identity.output &&
            std::filesystem::create_directories(item.identity.output),"Batch output collision at case start");
        std::cout<<"{\"event\":\"case_begin\"";
        WriteIdentity(item.identity);
        std::cout<<",\"predicate\":"<<JsonString(item.cfg.predicate)
            <<",\"phase_timing\":"<<(item.cfg.phaseTiming?"true":"false")
            <<",\"predicate_file\":"<<JsonString(item.cfg.predicateFile)
            <<",\"warmup_queries\":"<<item.cfg.warmup<<",\"measured_queries\":"<<item.cfg.count
            <<",\"replay_queries\":"<<item.cfg.count<<",\"search_settings\":{";
        bool comma=false;
        for(const auto& setting:item.cfg.ini.GetParameters("SearchSSDIndex")) {
            if(comma) std::cout<<',';
            std::cout<<JsonString(setting.first)<<':'<<JsonString(setting.second);
            comma=true;
        }
        std::cout<<"}}\n";
        FlushProgress();
        RunCase(item.cfg,queries,item.predicates,manager,&item.identity);
        std::cout<<"{\"event\":\"case_end\",\"completed_points\":"<<item.cfg.probes.size();
        WriteIdentity(item.identity);
        std::cout<<"}\n";
        FlushProgress();
    }
    std::cout<<"{\"event\":\"batch_end\",\"completed_cases\":"<<cases.size()<<"}\n";
    FlushProgress();
    return 0;
}
int main(int argc,char** argv) {
    try {
        Require(argc==2,"Expected one native INI");
        Helper::IniReader entry;
        Require(entry.LoadIniFile(argv[1])==ErrorCode::Success,"Cannot read native INI");
        if(entry.DoesSectionExist("Batch")) {
            auto cases=ReadBatch(argv[1],entry);
            return cases.front()->cfg.valueType=="UInt8"?RunBatch<std::uint8_t>(cases,"|u1"):
                RunBatch<float>(cases,"<f4");
        }
        Config cfg(argv[1]);
        return cfg.valueType=="UInt8" ? RunBenchmark<std::uint8_t>(cfg,"|u1")
                                     : RunBenchmark<float>(cfg,"<f4");
    } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
