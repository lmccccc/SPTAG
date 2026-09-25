#define main UnusedOrderBenchmarkMain
#include "MatchedBench.cpp"
#undef main
#include <OrderTrace.h>
#include "inc/Core/Common/QueryResultSet.h"
#include "inc/Core/BKT/Index.h"
#include "inc/Core/SPANN/LimitedTagSupport.h"

int main(int argc,char** argv) {
    try {
        if(argc!=3) throw std::runtime_error("Expected native INI and graph/match");
        const auto options=ReadProtocol(argv[1]);
        const std::string mode=argv[2];
        if(mode!="graph" && mode!="match") throw std::runtime_error("Invalid trace mode");
        if(options.maxQueries!=32 && options.maxQueries!=1000)
            throw std::runtime_error("Only fixed32 or1000 untimed replay allowed");
        Helper::IniReader ini;
        if(ini.LoadIniFile(argv[1])!=ErrorCode::Success) throw std::runtime_error("INI load failed");
        std::shared_ptr<VectorIndex> index;
        if(VectorIndex::LoadIndex(options.indexDir+"/tenant_0/HeadIndex",index)!=ErrorCode::Success)
            throw std::runtime_error("H1 load failed");
        for(const char* key:{"MaxCheck","NumberOfThreads","HashTableExponent"})
            index->SetParameter(key,ini.GetParameter<std::string>("SearchSSDIndex",key,"").c_str());
        index->UpdateIndex();
        std::vector<float> queries;std::size_t rows=0,dim=0;
        if(!ReadNpyMatrix(options.queryFile,"<f4",queries,rows,dim))
            throw std::runtime_error("Query load failed");
        std::vector<std::uint32_t> tags;std::size_t tagRows=0,tagCols=0;
        if(!ReadNpyMatrix(options.queryTagsFile,"<u4",tags,tagRows,tagCols) || tagRows!=rows)
            throw std::runtime_error("Predicate load failed");
        SPANN::LimitedTagSupport support;
        const auto path=options.indexDir+"/tenant_0/limited_tag_support.bin";
        std::ifstream file(path,std::ios::binary);SPANN::LimitedTagSupport::Header header;
        if(!file.read(reinterpret_cast<char*>(&header),sizeof(header)))
            throw std::runtime_error("Support header read failed");
        std::string error;
        if(!support.Load(path,header.m_headCount,header.m_slotsPerHead,header.m_minHeadCount,
            header.m_keyColumn,header.m_attributeCount,header.m_generationFingerprint,&error))
            throw std::runtime_error(error);
        auto* bkt=dynamic_cast<BKT::Index<float>*>(index.get());
        if(!bkt) throw std::runtime_error("Expected native BKT");
        std::vector<OrderTrace::Event> events;
        std::vector<int> heads;std::vector<float> distances;
        std::ofstream trace("order.i32",std::ios::binary);
        for(int q=0;q<options.maxQueries;++q) {
            events.clear();
            OrderTrace::events=&events;OrderTrace::query=q;
            COMMON::QueryResultSet<float> result(queries.data()+q*dim,24);
            const auto predicate=[&](int head) {
                const bool match=support.Supports(head,tags[q*tagCols]);
                OrderTrace::Emit('E',head,match,OrderTrace::initializing);
                return match;
            };
#ifdef ORDER_CURRENT
            if(mode!=ShortcutFull::mode) throw std::runtime_error("Trace mode differs from native INI");
            COMMON::NativeNeighborHooks hooks;
            hooks.matchEnabled=mode=="match";
            hooks.immutableSnapshot=true;
            const auto status=bkt->SearchIndexWithNativeHooks(result,hooks,predicate,{});
#else
            if(mode!="graph") throw std::runtime_error("Original has no match bit");
            const auto status=bkt->SearchIndexWithResultFilter(result,predicate);
#endif
            OrderTrace::events=nullptr;
            if(status!=ErrorCode::Success) throw std::runtime_error("Native trace query failed");
            trace.write(reinterpret_cast<const char*>(events.data()),events.size()*sizeof(events[0]));
            heads.push_back(24);
            for(int i=0;i<24;++i) {
                heads.push_back(result.GetResult(i)->VID);
                distances.push_back(result.GetResult(i)->Dist);
            }
        }
        std::ofstream ids("head.i32",std::ios::binary),dists("head.f32",std::ios::binary);
        ids.write(reinterpret_cast<const char*>(heads.data()),heads.size()*sizeof(int));
        dists.write(reinterpret_cast<const char*>(distances.data()),distances.size()*sizeof(float));
        if(!trace || !ids || !dists) throw std::runtime_error("Trace write failed");
        std::cout<<"PASS untimed native predicate order replay "<<options.maxQueries<<"\n";
        return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
