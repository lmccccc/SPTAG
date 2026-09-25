#define main UnusedBenchmarkMain
#include "Bench.cpp"
#undef main
#include "inc/Core/BKT/Index.h"
#include "inc/Core/SPANN/LimitedTagSupport.h"

int OwnOnlyMain(int argc,char** argv) {
    try {
        Require(argc==2,"Expected native INI");Config cfg(argv[1]);
        Require(cfg.valueType=="Float","Own-only fixture requires Float");
        const auto root=cfg.index+"/tenant_0/";
        std::ifstream directory(root+"SPTAGFullList.bin",std::ios::binary);
        std::uint32_t header[11]{};
        Require(bool(directory.read(reinterpret_cast<char*>(header),sizeof(header))) &&
            header[0]==0x314d5453U && header[1]==3 && header[4]==128,"STM1 v3 Float128 fixture");
        int ownHead=-1;
        for(std::uint32_t id=0;id<header[2];++id) {
            std::int32_t page,count,pure;std::uint16_t offset,pages;
            directory.read(reinterpret_cast<char*>(&page),4);directory.read(reinterpret_cast<char*>(&offset),2);
            directory.read(reinterpret_cast<char*>(&count),4);directory.read(reinterpret_cast<char*>(&pages),2);
            directory.read(reinterpret_cast<char*>(&pure),4);
            Require(bool(directory),"Posting directory read");
            if(count==0) {ownHead=static_cast<int>(id);break;}
        }
        Require(ownHead>=0,"Nonvacuous empty-posting fixture");
        const auto path=root+"limited_tag_support.bin";
        std::ifstream attributes(path,std::ios::binary);SPANN::LimitedTagSupport::Header h;
        Require(bool(attributes.read(reinterpret_cast<char*>(&h),sizeof(h))),"Support header");
        SPANN::LimitedTagSupport support;std::string error;
        Require(support.Load(path,h.m_headCount,h.m_slotsPerHead,h.m_minHeadCount,
            h.m_keyColumn,h.m_attributeCount,h.m_generationFingerprint,&error),error);
        const auto tag=support.OwnTag(ownHead);Require(support.Supports(ownHead,tag),"Own support");
        std::vector<float> query(header[4]);
        std::ifstream vectors(root+"HeadIndex/vectors.bin",std::ios::binary);
        vectors.seekg(8+std::uint64_t(ownHead)*header[4]*sizeof(float));
        Require(bool(vectors.read(reinterpret_cast<char*>(query.data()),query.size()*sizeof(float))),"Own vector");
        std::uint64_t vid=0;
        std::ifstream ids(root+"SPTAGHeadVectorIDs.bin",std::ios::binary);
        ids.seekg(8+std::uint64_t(ownHead)*sizeof(vid));
        Require(bool(ids.read(reinterpret_cast<char*>(&vid),sizeof(vid))) && vid<1000000,"Canonical VID");
        TenantIndexManager manager(128,"SPANN","Float");cfg.Apply(manager);
        Require(manager.LoadAll(cfg.index.c_str()),"Native index load");
        const ByteArray bytes(reinterpret_cast<std::uint8_t*>(query.data()),query.size()*sizeof(float),false);
        std::uint32_t predicate[]={0x444e4633U,1,1,0,h.m_keyColumn,Cache::DNF_EQ,tag};
        const ByteArray filter(reinterpret_cast<std::uint8_t*>(predicate),sizeof(predicate),false);
        const auto result=manager.SearchWithPredicate(bytes,0,10,filter,-1);
        bool found=false;
        if(result) for(int k=0;k<10;++k)
            found|=result->GetResult(k)->VID==static_cast<int>(vid) && result->GetResult(k)->Dist==0;
        Require(found,"Native empty-posting selected own record lost");
        std::cout<<"PASS native empty-posting own result mode="<<cfg.mode<<" head="<<ownHead
                 <<" VID="<<vid<<" zero_distance=1 supplementary_heap=0\n";
        return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
#ifndef NATIVE_EMBED_OWN
int main(int argc,char** argv) {return OwnOnlyMain(argc,argv);}
#endif
