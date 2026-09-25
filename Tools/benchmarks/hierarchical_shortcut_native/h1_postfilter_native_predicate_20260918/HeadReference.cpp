#define main UnusedFullReferenceMain
#include "MatchedBench.cpp"
#undef main
#include "inc/Core/Common/QueryResultSet.h"
#include "inc/Core/BKT/Index.h"
#include "inc/Core/SPANN/LimitedTagSupport.h"

int main(int argc,char** argv) {
    try {
        if(argc!=2) throw std::runtime_error("Expected native reference INI");
        auto options=ReadProtocol(argv[1]);
        if(options.maxQueries!=32) throw std::runtime_error("Head reference is bounded to 32 queries");
        Helper::IniReader ini;
        if(ini.LoadIniFile(argv[1])!=ErrorCode::Success) throw std::runtime_error("Cannot load reference INI");
        std::shared_ptr<VectorIndex> index;
        if(VectorIndex::LoadIndex(options.indexDir+"/tenant_0/HeadIndex",index)!=ErrorCode::Success)
            throw std::runtime_error("Cannot load native H1 reference");
        for(const char* key:{"MaxCheck","NumberOfThreads","HashTableExponent"})
            index->SetParameter(key,ini.GetParameter<std::string>("SearchSSDIndex",key,"").c_str());
        index->UpdateIndex();
        std::vector<float> queries;std::size_t rows=0,dim=0;
        if(!ReadNpyMatrix(options.queryFile,"<f4",queries,rows,dim))
            throw std::runtime_error("Cannot read native query input");
        std::vector<std::uint32_t> tags;std::size_t tagRows=0,tagCols=0;
        SPANN::LimitedTagSupport support;
        if(!options.queryTagsFile.empty()) {
            if(!ReadNpyMatrix(options.queryTagsFile,"<u4",tags,tagRows,tagCols) || tagRows!=rows)
                throw std::runtime_error("Cannot read reference predicates");
            const auto path=options.indexDir+"/tenant_0/limited_tag_support.bin";
            std::ifstream file(path,std::ios::binary);SPANN::LimitedTagSupport::Header header;
            if(!file.read(reinterpret_cast<char*>(&header),sizeof(header)))
                throw std::runtime_error("Cannot read native support header");
            std::string error;
            if(!support.Load(path,header.m_headCount,header.m_slotsPerHead,header.m_minHeadCount,
                header.m_keyColumn,header.m_attributeCount,header.m_generationFingerprint,&error))
                throw std::runtime_error(error);
        }
        auto* bkt=dynamic_cast<BKT::Index<float>*>(index.get());
        if(!bkt) throw std::runtime_error("Expected authentic native H1 BKT");
        std::vector<int> heads;std::vector<float> distances;
        for(std::size_t q=0;q<32;++q) {
            COMMON::QueryResultSet<float> result(queries.data()+q*dim,24);
            const auto status=tags.empty() ? index->SearchIndex(result) :
                bkt->SearchIndexWithResultFilter(result,[&](int head) {
                    return support.Supports(head,tags[q*tagCols]);
                });
            if(status!=ErrorCode::Success) throw std::runtime_error("Reference query failed");
            heads.push_back(24);
            for(int i=0;i<24;++i) {
                heads.push_back(result.GetResult(i)->VID);
                distances.push_back(result.GetResult(i)->Dist);
            }
        }
        std::ofstream ids("head.i32",std::ios::binary),dists("head.f32",std::ios::binary);
        ids.write(reinterpret_cast<const char*>(heads.data()),heads.size()*sizeof(int));
        dists.write(reinterpret_cast<const char*>(distances.data()),distances.size()*sizeof(float));
        if(!ids || !dists) throw std::runtime_error("Cannot save head reference");
        std::cout<<"PASS authentic SearchIndexWithResultFilter support-only H1 reference 32 queries\n";
        return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
