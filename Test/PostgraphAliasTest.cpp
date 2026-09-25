#include "inc/Core/BKT/Index.h"
#include "inc/Core/Common/PostingNavigation.h"
#include "inc/Core/SPANN/PostingNavigation.h"
#include <iostream>
#include <vector>
#include <fstream>
#include <filesystem>
#include <chrono>
#include <map>
using namespace SPTAG;
#define CHECK(x) do {if(!(x)) throw std::runtime_error("Postgraph alias line "+std::to_string(__LINE__));} while(false)
struct AliasRow : COMMON::PostingNavigation {
    std::uint32_t representative=0;
    int calls=0;
    std::uint64_t distances=0;
    RowResult row;
    void Expand(const std::vector<int>&,const Consumer& consume) override {
        ++calls;
        const std::uint32_t ids[]={representative,representative};
        const auto* stats=COMMON::g_graphAccessStats;
        const auto before=stats?stats->m_distanceCalls:0;
        row=consume(ids,2);
        distances=stats?stats->m_distanceCalls-before:0;
    }
};
struct SparseRows : COMMON::PostingNavigation {
    std::vector<RowResult> rows;
    std::vector<std::uint64_t> distances;
    void Expand(const std::vector<int>&,const Consumer& consume) override {
        const std::vector<std::vector<std::uint32_t>> members={{202,202,203,204,203},{230,230}};
        for(const auto& ids:members) {
            const auto* stats=COMMON::g_graphAccessStats;
            CHECK(stats);
            const auto before=stats->m_distanceCalls;
            rows.push_back(consume(ids.data(),static_cast<int>(ids.size())));
            distances.push_back(stats->m_distanceCalls-before);
            if(!rows.back().canContinue) break;
        }
    }
};
struct RefinementOwners {
    const int ids[3]={0,1,2};
    std::size_t Levels() const {return 1;}
    int Count(std::size_t) const {return 3;}
    SPANN::PostingOwners::Range Parents(std::size_t,int) const {return {ids,ids+3};}
};
struct RefinementRows {
    const std::uint32_t ids[3][2]={{250,250},{150,150},{254,254}};
    const std::uint32_t* Begin(int id) const {return ids[id];}
    const std::uint32_t* End(int id) const {return ids[id]+2;}
};
int main(int argc,char** argv) {
    try {
        BKT::Index<float> index;
        for(auto p:std::vector<std::pair<const char*,const char*>>{
            {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"MaxCheck","2"},
            {"BKTKmeansK","8"},{"BKTLeafSize","1"},{"NeighborhoodSize","32"},{"TPTNumber","1"},
            {"NumberOfInitialDynamicPivots","1"},{"NumberOfOtherDynamicPivots","0"}})
            index.SetParameter(p.first,p.second);
        std::vector<float> data(256*128);
        for(int i=0;i<256;++i) std::fill_n(data.data()+i*128,128,float(i>=200 && i<232?200:i));
        std::fill_n(data.data()+128,128,240.0f);data[255]=239;
        CHECK(index.BuildIndex(data.data(),256,128)==ErrorCode::Success);
        const int representative=200;
        for(int i=0;i<256;++i) std::fill_n(index.GetMutableGraph()[i],32,-1);
        index.GetMutableGraph()[0][0]=1;
        index.GetMutableGraph()[representative][31]=-5;
        CHECK(index.DeleteIndex(201)==ErrorCode::Success);
        const auto parent=argc>1?std::filesystem::path(argv[1]):std::filesystem::current_path();
        const auto folder=parent/("postgraph_alias_"+std::to_string(
            std::chrono::steady_clock::now().time_since_epoch().count()));
        CHECK(!std::filesystem::exists(folder));
        CHECK(index.SaveIndex(folder.string())==ErrorCode::Success);
        std::vector<COMMON::BKTNode> nodes;
        for(int id:{0,1,2,200}) nodes.emplace_back(id);
        nodes[0].childStart=1;nodes[0].childEnd=3;
        nodes[3].childStart=-4;nodes[3].childEnd=35;
        for(int id=201;id<232;++id) nodes.emplace_back(id);
        nodes.emplace_back(-1);
        {
            const std::int32_t header[]={1,0,36};
            std::ofstream tree(folder/"tree.bin",std::ios::binary);
            tree.write(reinterpret_cast<const char*>(header),sizeof(header));
            tree.write(reinterpret_cast<const char*>(nodes.data()),nodes.size()*sizeof(nodes[0]));
            CHECK(bool(tree));
        }
        std::shared_ptr<VectorIndex> restored;
        CHECK(VectorIndex::LoadIndex(folder.string(),restored)==ErrorCode::Success);
        auto* typed=dynamic_cast<BKT::Index<float>*>(restored.get());
        CHECK(typed);
        auto& loaded=*typed;
        const auto predicate=[&](int id){return id==1 || (id>=200 && id<232 && id!=representative);};
        COMMON::QueryResultSet<float> baseline(data.data(),4), result(data.data(),4);
        CHECK(loaded.SearchIndexWithResultFilter(baseline,predicate,2)==ErrorCode::Success);
        int before=0;for(int i=0;i<4;++i) before+=baseline.GetResult(i)->VID>=0;
        CHECK(before<4);
        AliasRow posting;posting.representative=representative;
        COMMON::GraphAccessStats aliasStats;
        {
            COMMON::ScopedGraphAccessStats scope(&aliasStats);
            CHECK(loaded.SearchIndexWithPostingNavigation(result,predicate,&posting,2,false,8,1)==ErrorCode::Success);
        }
        CHECK(posting.calls==1 && posting.row.degree==2 && posting.row.newCandidates==0 && posting.row.targetFilled);
        CHECK(posting.distances==1 && !posting.row.canContinue);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        CHECK(aliasStats.m_auxiliaryFirstVisits==1 && aliasStats.m_auxiliaryNegativeFirstVisits==1);
        CHECK(aliasStats.m_auxiliaryUnvisitedNegativeSkips==0 && aliasStats.m_auxiliaryVisitedSkips==1);
#endif
        for(int i=0;i<4;++i) CHECK(result.GetResult(i)->VID>=0 && result.GetResult(i)->VID!=201 &&
                                  predicate(result.GetResult(i)->VID));
        for(int i=0;i<before;++i) {
            bool found=false;
            for(int j=0;j<4;++j) found|=baseline.GetResult(i)->VID==result.GetResult(j)->VID &&
                baseline.GetResult(i)->Dist==result.GetResult(j)->Dist;
            CHECK(found);
        }
        std::cout<<"PASS false representative admits valid aliases; zero fresh matches still fill real slots; base heads preserved\n";
        for(bool deletedOnly:{false,true}) {
            AliasRow rejected;rejected.representative=representative;
            COMMON::QueryResultSet<float> empty(data.data(),4);
            COMMON::GraphAccessStats stats;
            std::map<int,int> calls;
            {
                COMMON::ScopedGraphAccessStats scope(&stats);
                CHECK(loaded.SearchIndexWithPostingNavigation(empty,[&](int id) {
                    ++calls[id];return deletedOnly && id==201;
                },&rejected,2,false,8,1)==ErrorCode::Success);
            }
            CHECK(rejected.calls==1 && rejected.row.degree==2 && rejected.row.newCandidates==0);
            CHECK(!rejected.row.targetFilled && rejected.row.canContinue && rejected.distances==0);
            for(int i=0;i<4;++i) CHECK(empty.GetResult(i)->VID<0);
            for(int id=200;id<232;++id) CHECK(calls[id]==1);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            CHECK(stats.m_auxiliaryFirstVisits==0 && stats.m_auxiliaryPrefetchLines==0);
            CHECK(stats.m_auxiliaryUnvisitedNegativeSkips==1 && stats.m_auxiliaryVisitedSkips==1);
#endif
        }
        SparseRows sparse;
        COMMON::QueryResultSet<float> sparseResult(data.data(),1);
        COMMON::GraphAccessStats sparseStats;
        std::map<int,int> calls;
        {
            COMMON::ScopedGraphAccessStats scope(&sparseStats);
            CHECK(loaded.SearchIndexWithPostingNavigation(sparseResult,[&](int id) {
                ++calls[id];return id==230;
            },&sparse,2,false,8,1)==ErrorCode::Success);
        }
        CHECK(sparse.rows.size()==2 && sparse.distances==std::vector<std::uint64_t>({0,1}));
        CHECK(sparse.rows[0].degree==5 && sparse.rows[0].eligible==0 && sparse.rows[0].canContinue);
        CHECK(sparse.rows[1].degree==2 && sparse.rows[1].newCandidates==1 && sparse.rows[1].targetFilled);
        CHECK(!sparse.rows[1].canContinue && sparseResult.GetResult(0)->VID==230);
        for(int id:{202,203,204,230}) CHECK(calls[id]==1);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        CHECK(sparseStats.m_auxiliaryFirstVisits==1 && sparseStats.m_auxiliaryNegativeFirstVisits==0);
        CHECK(sparseStats.m_auxiliaryUnvisitedNegativeSkips==3 && sparseStats.m_auxiliaryVisitedSkips==3);
        CHECK(sparseStats.m_supplementLeaves==1 && sparseStats.m_supplementDistances==1);
#endif
        std::cout<<"PASS rejected/deleted-only alias groups use zero distances; negative rows preserve budget for later matches; duplicate predicates cached\n";
        const auto refinementPredicate=[](int id) {return id==2 || id==150 || id==250;};
        COMMON::QueryResultSet<float> refinementBase(data.data(),2);
        CHECK(loaded.SearchIndexWithResultFilter(refinementBase,refinementPredicate,2)==ErrorCode::Success);
        CHECK(refinementBase.GetResult(0)->VID==2 && refinementBase.GetResult(1)->VID<0);
        for(int extra:{1,2,16}) {
            RefinementOwners owners;
            const std::vector<RefinementRows> layers(1);
            const auto signature=[](std::size_t,int) {return true;};
            const auto distance=[](std::size_t,int id) {return id<2?float(id+1):100.0f;};
            SPANN::HierarchyPostingQuery<decltype(layers),decltype(signature),decltype(distance),RefinementOwners>
                hierarchy(owners,layers,signature,distance);
            COMMON::QueryResultSet<float> refined(data.data(),2);
            COMMON::GraphAccessStats stats;
            std::map<int,int> checked;
            {
                COMMON::ScopedGraphAccessStats scope(&stats);
                CHECK(loaded.SearchIndexWithPostingNavigation(refined,[&](int id) {
                    ++checked[id];return refinementPredicate(id);
                },&hierarchy,2,false,8,extra)==ErrorCode::Success);
            }
            CHECK(refined.GetResult(0)->VID==refinementBase.GetResult(0)->VID &&
                  refined.GetResult(0)->Dist==refinementBase.GetResult(0)->Dist);
            CHECK(refined.GetResult(1)->VID==(extra==1?250:150));
            CHECK(checked[250]==1 && checked[150]==(extra==1?0:1));
            CHECK(!checked.count(254) && hierarchy.Converged()==(extra==16));
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
            CHECK(stats.m_headBefore==1 && stats.m_headAfter==2 && stats.m_preservedHeads==1);
            CHECK(stats.m_supplementLeaves==static_cast<unsigned>(std::min(extra,2)));
            CHECK(stats.m_supplementDistances==static_cast<unsigned>(std::min(extra,2)));
            CHECK(stats.m_h2Postings.completedRows==static_cast<unsigned>(std::min(extra,2)));
            CHECK(stats.m_postingTargetMet==1 && stats.m_supplementReason==(extra==16?7:6));
#endif
        }
        std::cout<<"PASS real hierarchy refines after first fill, stops adaptively before extra16, and preserves the original H1 head and exact checked-work ceiling\n";
        std::filesystem::remove_all(folder);
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
