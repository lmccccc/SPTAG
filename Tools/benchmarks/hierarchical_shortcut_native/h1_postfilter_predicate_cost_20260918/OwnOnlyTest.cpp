#define main UnusedBenchmarkMain
#include "MatchedBench.cpp"
#undef main
#include "inc/Core/SPANN/LimitedTagSupport.h"

int main(int argc,char** argv) {
    try {
        if(argc!=2) throw std::runtime_error("Expected native INI");
        auto options=ReadProtocol(argv[1]);
        const auto root=options.indexDir+"/tenant_0/";
        std::ifstream directory(root+"SPTAGFullList.bin",std::ios::binary);
        std::uint32_t header[11]{};
        if(!directory.read(reinterpret_cast<char*>(header),sizeof(header)) ||
           header[0]!=0x314d5453U || header[1]!=3 || header[4]!=128)
            throw std::runtime_error("Own-only fixture requires native STM1 v3 Float128");
        int ownHead=-1;
        for(std::uint32_t id=0;id<header[2];++id) {
            std::int32_t page,count,pure;std::uint16_t offset,pages;
            directory.read(reinterpret_cast<char*>(&page),4);
            directory.read(reinterpret_cast<char*>(&offset),2);
            directory.read(reinterpret_cast<char*>(&count),4);
            directory.read(reinterpret_cast<char*>(&pages),2);
            directory.read(reinterpret_cast<char*>(&pure),4);
            if(!directory) throw std::runtime_error("Truncated native posting directory");
            if(count==0) {ownHead=static_cast<int>(id);break;}
        }
        if(ownHead<0) throw std::runtime_error("No native empty posting available; fixture cannot pass vacuously");
        std::ifstream attributes(root+"limited_tag_support.bin",std::ios::binary);
        SPANN::LimitedTagSupport::Header h;
        if(!attributes.read(reinterpret_cast<char*>(&h),sizeof(h))) throw std::runtime_error("Missing support");
        SPANN::LimitedTagSupport support;std::string error;
        if(!support.Load(root+"limited_tag_support.bin",h.m_headCount,h.m_slotsPerHead,h.m_minHeadCount,
            h.m_keyColumn,h.m_attributeCount,h.m_generationFingerprint,&error)) throw std::runtime_error(error);
        const auto tag=support.OwnTag(ownHead);
        if(!support.Supports(ownHead,tag)) throw std::runtime_error("Own tag absent");
        std::vector<float> query(header[4]);
        std::ifstream vectors(root+"HeadIndex/vectors.bin",std::ios::binary);
        vectors.seekg(8+std::uint64_t(ownHead)*header[4]*sizeof(float));
        if(!vectors.read(reinterpret_cast<char*>(query.data()),query.size()*sizeof(float))) throw std::runtime_error("Missing head vector");
        std::uint64_t vid=0;
        std::ifstream ids(root+"SPTAGHeadVectorIDs.bin",std::ios::binary);
        ids.seekg(8+std::uint64_t(ownHead)*sizeof(vid));
        if(!ids.read(reinterpret_cast<char*>(&vid),sizeof(vid)) || vid>=1000000) throw std::runtime_error("Invalid canonical VID");
        TenantIndexManager manager(header[4],"SPANN","Float");
        if(!ApplySearchIni(manager,options.searchIni) || !manager.LoadAll(options.indexDir.c_str()))
            throw std::runtime_error("Cannot load native own-only fixture");
        const ByteArray bytes(reinterpret_cast<std::uint8_t*>(query.data()),query.size()*sizeof(float),false);
        std::uint32_t predicate[]={0x444E4633U,1,1,0,h.m_keyColumn,Cache::DNF_EQ,tag};
        const ByteArray filter(reinterpret_cast<std::uint8_t*>(predicate),sizeof(predicate),false);
        for(const char* mode:{"graph","match","posting"}) {
            ShortcutFull::mode=mode;ShortcutFull::capture=true;
            auto result=manager.SearchWithPredicate(bytes,0,10,filter,-1);
            const auto& observation=ShortcutFull::Last();
            const bool selected=std::find(observation.heads.begin(),observation.heads.end(),ownHead)!=observation.heads.end();
            const bool nativeOwn=std::find(observation.nativeSelectedHeadRecords.begin(),
                observation.nativeSelectedHeadRecords.end(),static_cast<int>(vid))!=observation.nativeSelectedHeadRecords.end();
            bool finalOwn=false;
            if(result) for(int k=0;k<10;++k) {
                const auto* r=result->GetResult(k);
                if(r->VID==static_cast<int>(vid) && r->Dist==0) finalOwn=true;
            }
            if(!selected || !nativeOwn || !finalOwn || !observation.ownIDs.empty())
                throw std::runtime_error("Empty-posting native selected own result lost");
            std::cout<<"PASS own-only mode="<<mode<<" head="<<ownHead<<" VID="<<vid
                     <<" physical_posting_count=0 selected_head=1 native_preSSD_own=1 final_exact_own=1 supplemental_heap=0\n";
        }
        ShortcutFull::capture=false;
        return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
