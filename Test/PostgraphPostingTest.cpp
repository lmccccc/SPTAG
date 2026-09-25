#include "inc/Core/BKT/Index.h"
#include "inc/Core/SPANN/PostingNavigation.h"
#include "inc/Core/SPANN/Options.h"
#include <iostream>
#include <map>
#include <set>
using namespace SPTAG;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Postgraph test line "+std::to_string(__LINE__)); } while(false)
struct Rows : COMMON::PostingNavigation {
    std::vector<std::uint32_t> members;
    std::vector<int> anchors;
    unsigned calls=0;
    std::uint64_t distances=0;
    RowResult result;
    void Expand(const std::vector<int>& source,const Consumer& consume) override {
        ++calls; anchors=source;
        const auto* stats=COMMON::g_graphAccessStats;
        const auto before=stats?stats->m_distanceCalls:0;
        result=consume(members.data(),static_cast<int>(members.size()));
        distances=stats?stats->m_distanceCalls-before:0;
    }
};
template<class T> void CheckPhases() {
    BKT::Index<T> index;
    for(auto p:std::vector<std::pair<const char*,const char*>>{
        {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"MaxCheck","8"},
        {"BKTKmeansK","8"},{"BKTLeafSize","1"},{"NeighborhoodSize","32"},{"TPTNumber","1"},
        {"NumberOfInitialDynamicPivots","1"},{"NumberOfOtherDynamicPivots","0"}})
        index.SetParameter(p.first,p.second);
    std::vector<T> data(256*128);
    for(int i=0;i<256;++i) std::fill_n(data.data()+i*128,128,static_cast<T>(i));
    std::fill_n(data.data()+128,128,static_cast<T>(240));
    data[255]=static_cast<T>(239);
    CHECK(index.BuildIndex(data.data(),256,128)==ErrorCode::Success);
    for(int i=0;i<256;++i) std::fill_n(index.GetMutableGraph()[i],32,-1);
    index.GetMutableGraph()[0][0]=1;
    const auto selected=[](int id){return id==1 || id>=200;};
    COMMON::QueryResultSet<T> base(data.data(),4), shared(data.data(),4), extra(data.data(),4);
    CHECK(index.SearchIndexWithResultFilter(base,selected,8)==ErrorCode::Success);
    Rows noBudget; noBudget.members={200,201,202};
    CHECK(index.SearchIndexWithPostingNavigation(shared,selected,&noBudget,8,false,8,0)==ErrorCode::Success);
    CHECK(noBudget.calls==0);
    for(int i=0;i<4;++i) CHECK(base.GetResult(i)->VID==shared.GetResult(i)->VID &&
                             base.GetResult(i)->Dist==shared.GetResult(i)->Dist);
    Rows rows;
    for(unsigned i=200;i<232;++i) rows.members.push_back(i);
    rows.members.push_back(200);
    std::map<int,int> checked;
    CHECK(index.SearchIndexWithPostingNavigation(extra,[&](int id){++checked[id];return selected(id);},
        &rows,8,false,8,1)==ErrorCode::Success);
    CHECK(rows.calls==1 && !rows.anchors.empty() && rows.anchors.size()<=8);
    CHECK(rows.result.degree==33 && rows.result.targetFilled && !rows.result.canContinue);
    CHECK(checked[231]==1 && checked[200]==1);
    for(int i=0;i<4;++i) {
        if(base.GetResult(i)->VID<0) continue;
        bool found=false;
        for(int j=0;j<4;++j) found |= base.GetResult(i)->VID==extra.GetResult(j)->VID &&
                                       base.GetResult(i)->Dist==extra.GetResult(j)->Dist;
        CHECK(found);
    }
    Rows enough; enough.members=rows.members;
    COMMON::QueryResultSet<T> full(data.data(),4);
    CHECK(index.SearchIndexWithPostingNavigation(full,[](int){return true;},&enough,64,false,8,64)==ErrorCode::Success);
    CHECK(enough.calls==0);
    Rows negative; negative.members={200,200,201,202};
    COMMON::QueryResultSet<T> none(data.data(),4);
    std::map<int,int> negatives;
    COMMON::GraphAccessStats negativeStats;
    {
        COMMON::ScopedGraphAccessStats scope(&negativeStats);
        CHECK(index.SearchIndexWithPostingNavigation(none,[&](int id){++negatives[id];return false;},
            &negative,8,false,8,1)==ErrorCode::Success);
    }
    CHECK(negative.calls==1 && negative.result.degree==4 && negative.result.newCandidates==0);
    CHECK(!negative.result.targetFilled && !negative.anchors.empty() && negatives[202]==1 && negatives[200]==1);
    CHECK(negative.result.canContinue && negative.distances==0);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    CHECK(negativeStats.m_auxiliaryFirstVisits==0 && negativeStats.m_auxiliaryNegativeFirstVisits==0);
    CHECK(negativeStats.m_auxiliaryPrefetchLines==0 && negativeStats.m_auxiliaryUnvisitedNegativeSkips>0);
    CHECK(negativeStats.m_auxiliaryUnvisitedNegativeSkips+negativeStats.m_auxiliaryVisitedSkips==4);
#endif
    for(int i=0;i<4;++i) CHECK(none.GetResult(i)->VID<0);
    for(int id=200;id<232;++id) CHECK(index.DeleteIndex(id)==ErrorCode::Success);
    Rows deleted; deleted.members=rows.members;
    COMMON::QueryResultSet<T> rejected(data.data(),4);
    CHECK(index.SearchIndexWithPostingNavigation(rejected,selected,&deleted,8,false,8,64)==ErrorCode::Success);
    CHECK(deleted.calls==1 && deleted.result.newCandidates>0 && !deleted.result.targetFilled);
    for(int i=0;i<4;++i) CHECK(rejected.GetResult(i)->VID<0 || rejected.GetResult(i)->VID==1);
    CHECK(index.SearchIndexWithPostingNavigation(none,selected,&negative,8,false,0,0)==ErrorCode::FailedParseValue);
    CHECK(index.SearchIndexWithPostingNavigation(none,selected,&negative,8,false,8,-1)==ErrorCode::FailedParseValue);
    std::cout<<"PASS graph parity, protected base, one postgraph phase, negative anchors, complete rows, genuine slots and explicit budgets; type bytes="<<sizeof(T)<<'\n';
}
int main() {
    try {
        SPANN::Options options;
        CHECK(options.m_postingAnchorCount==8 && options.m_postingAdditionalMaxCheck==0);
        CHECK(options.SetParameter("BuildSSDIndex","PostingMinCandidates","10")==ErrorCode::FailedParseValue);
        CHECK(options.SetParameter("BuildSSDIndex","PostingAnchorCount","0")==ErrorCode::FailedParseValue);
        CHECK(options.SetParameter("BuildSSDIndex","PostingAdditionalMaxCheck","-1")==ErrorCode::FailedParseValue);
        CHECK(options.SetParameter("BuildSSDIndex","PostingAdditionalMaxCheck","2048")==ErrorCode::Success);
        CHECK(options.SetParameter("BuildSSDIndex","PostingAnchorCount","17")==ErrorCode::Success);
        CHECK(options.SetParameter("BuildSSDIndex","MaxCheck","2147483647")==ErrorCode::FailedParseValue);
        CheckPhases<float>(); CheckPhases<std::uint8_t>();
    }
    catch(const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
