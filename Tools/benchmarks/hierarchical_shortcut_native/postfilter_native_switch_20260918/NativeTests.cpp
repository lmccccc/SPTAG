#include <NativeSupplier.h>
#include <iostream>
#include <map>
#include <set>

using namespace SPTAG;
using Hooks=COMMON::NativeNeighborHooks;
using Result=COMMON::QueryResultSet<float>;
void Check(bool value,int line) {
    if (!value) throw std::runtime_error("Native postfilter fixture failed at "+std::to_string(line));
}
#define CHECK(v) Check((v),__LINE__)

int main() {
    const auto rejects=[](auto operation) {
        bool caught=false;
        try { operation(); } catch (const std::exception&) { caught=true; }
        CHECK(caught);
    };
    const std::map<std::string,std::string> parameters{
        {"nativepostfiltermode","posting"},{"postingfilterhitratio","0.01"},
        {"headnavigationmode","H1Only"},{"numberofthreads","1"},
        {"enablehybriddistance","false"},{"enableadaptivefilterednprobe","false"}};
    for (const char* mode:{"graph","posting"}) for (const char* ratio:{"0","0.01"}) {
        auto p=parameters;p["nativepostfiltermode"]=mode;p["postingfilterhitratio"]=ratio;
        ShortcutFull::Configure(p);
    }
    for (const char* key:{"navixmode","navixpostingthreshold","navixtwohopthreshold",
                         "twohopthreshold","arbitrationmode","shortcutretainedratio","minbasedegree",
                         "observe","postfilterpostingmode","postingactivationratio"}) {
        auto p=parameters;p[key]="0";rejects([&]{ShortcutFull::Configure(p);});
    }
    for (const char* value:{"nan","inf","-1","0.05","1","0.01junk",""}) {
        auto p=parameters;p["postingfilterhitratio"]=value;rejects([&]{ShortcutFull::Configure(p);});
    }
    auto missing=parameters;missing.erase("postingfilterhitratio");
    rejects([&]{ShortcutFull::Configure(missing);});
    auto obsolete=parameters;obsolete["nativepostfiltermode"]="observe";
    rejects([&]{ShortcutFull::Configure(obsolete);});
    BKT::Index<float> index;
    for (auto p:std::vector<std::pair<const char*,const char*>>{
        {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"MaxCheck","2048"},{"BKTKmeansK","8"},
        {"BKTLeafSize","1"},{"NeighborhoodSize","128"},{"TPTNumber","1"},
        {"NumberOfInitialDynamicPivots","1"},{"NumberOfOtherDynamicPivots","0"}})
        index.SetParameter(p.first,p.second);
    constexpr int count=256;
    std::vector<float> data(count*8);
    for (int i=0;i<count;++i) for (int j=0;j<8;++j) data[8*i+j]=float(i*(j+1));
    CHECK(index.BuildIndex(data.data(),count,8)==ErrorCode::Success);
    const auto clear=[&] {
        for (int i=0;i<count;++i) std::fill(index.GetMutableGraph()[i],index.GetMutableGraph()[i]+128,-1);
    };
    clear();std::set<int> seeds;
    Hooks entry;entry.ownPoint=[&](int id,float){seeds.insert(id);};
    Result entryResult(data.data(),1);
    CHECK(index.SearchIndexWithNativeHooks(entryResult,entry,[](int){return false;},{},1)==ErrorCode::Success);
    CHECK(seeds.count(0));
    std::vector<int> fresh;
    for (int i=1;i<count;++i) if (!seeds.count(i)) fresh.push_back(i);
    CHECK(fresh.size()>127);
    const int bridge=fresh[0],target=fresh[60];
    const auto writeRow=[&](int id,const std::vector<int>& row) {
        CHECK(row.size()<=127);std::copy(row.begin(),row.end(),index.GetMutableGraph()[id]);
    };
    const auto observed=[](bool =true) {
        Hooks h;h.capture=h.diagnostics=h.auditVisited=true;return h;
    };
    const auto run=[&](Hooks& hooks,const std::set<int>& matches,int budget=2048) {
        Result result(data.data(),1);
        CHECK(index.SearchIndexWithNativeHooks(result,hooks,
            [&](int id){return matches.count(id)!=0;},{},budget)==ErrorCode::Success);
        return std::make_pair(result.GetResult(0)->VID,result.GetResult(0)->Dist);
    };
    const auto decision=[](const Hooks& h,int id)->Postfilter::Decision {
        for (const auto& d:h.decisions) if (d.head==id) return d;
        throw std::runtime_error("Missing real native expansion");
    };
    const auto seen=[](const Hooks& h,int id) {
        return std::find(h.visitedEdges.begin(),h.visitedEdges.end(),std::make_pair(id,false))!=h.visitedEdges.end();
    };
    const auto parity=[](const Hooks& a,const Hooks& b) {
        CHECK(a.checked==b.checked && a.evaluated==b.evaluated);
        CHECK(a.visitedEdges==b.visitedEdges && a.allVisited==b.allVisited);
        CHECK(a.queueOffers==b.queueOffers && a.queueAccepted==b.queueAccepted && a.queueRejected==b.queueRejected);
        CHECK(a.outerExpansions==b.outerExpansions && a.ordinaryNeighborEntries==b.ordinaryNeighborEntries);
    };
    auto forbidden=[](int,const COMMON::NativeEdgeConsumer&)->bool {CHECK(false);return false;};
    clear();writeRow(0,{0,bridge});writeRow(bridge,{target});
    for (int budget:{1,2,8,64,2048}) {
        auto graph=observed(false),disabled=observed(),posting=observed();
        disabled.activationRatio=posting.activationRatio=0;
        disabled.posting=posting.posting=forbidden;
        const auto expected=run(graph,{target},budget);
        Result original(data.data(),1);
        CHECK(index.SearchIndexWithResultFilter(original,[&](int id){return id==target;},budget)==ErrorCode::Success);
        CHECK(original.GetResult(0)->VID==expected.first && original.GetResult(0)->Dist==expected.second);
        CHECK(run(disabled,{target},budget)==expected && run(posting,{target},budget)==expected);
        parity(graph,disabled);parity(graph,posting);
        CHECK(graph.ordinaryPredicateCalls==posting.ordinaryPredicateCalls);
        CHECK(posting.calls==0 && posting.signatureChecks==0);
        if (budget>=8) {
            CHECK(expected.first==target && seen(posting,bridge));
            CHECK(decision(posting,0).checks==1 && decision(posting,0).passes==0);
        }
    }
    std::cout<<"PASS exact native graph/disabled posting IDs distances CheckAndSet/queue/checked; visited unjudged is not a failure\n";
    clear();writeRow(0,std::vector<int>(fresh.begin(),fresh.begin()+127));
    auto partial=observed();partial.posting=forbidden;
    run(partial,{},2);
    CHECK(partial.checked<=2 && !decision(partial,0).complete && decision(partial,0).checks<127);
    auto partialEight=observed();partialEight.posting=forbidden;
    run(partialEight,{},8);
    CHECK(partialEight.checked==8 && !decision(partialEight,0).complete);
    CHECK(partialEight.ordinaryNeighborEntries<127 && partialEight.selectedActions==0);
    clear();auto zero=observed();zero.posting=forbidden;run(zero,{},64);
    CHECK(decision(zero,0).checks==0 && zero.ordinaryPredicateCalls==0);
    clear();writeRow(0,{bridge});writeRow(bridge,{target});
    auto disabled=observed();disabled.activationRatio=0;disabled.posting=forbidden;
    CHECK(run(disabled,{target},64).first==target);

    for (int length:{100,101}) for (int passes:{0,1,2}) {
        clear();writeRow(0,std::vector<int>(fresh.begin(),fresh.begin()+length));
        std::set<int> matches(fresh.begin()+length-passes,fresh.begin()+length);
        std::vector<std::pair<char,int>> graphEvents,postingEvents;
        const auto exercise=[&](Hooks& h,std::vector<std::pair<char,int>>& events) {
            auto own=[&](int id,float) {
                events.emplace_back('o',id);
                return true; // Deliberately not the head predicate.
            };
            h.ownGain=own;
            Result r(data.data(),count);
            auto predicate=[&](int id) {
                events.emplace_back('p',id);
                events.emplace_back('s',id);
                if (!matches.count(id)) return false;
                events.emplace_back('v',id);
                events.emplace_back('c',id);
                return true;
            };
            CHECK(index.SearchIndexWithNativeHooks(r,h,predicate,{},2048)==ErrorCode::Success);
            h.ownGain={};
            std::vector<std::pair<int,float>> results;
            for(int i=0;i<count;++i) results.emplace_back(r.GetResult(i)->VID,r.GetResult(i)->Dist);
            return results;
        };
        auto graph=observed(),posting=observed();
        int activations=0;
        auto action=[&](int head,const COMMON::NativeEdgeConsumer&) {
            CHECK(head==0);++activations;return false;
        };
        posting.posting=action;
        CHECK(exercise(graph,graphEvents)==exercise(posting,postingEvents));
        CHECK(graphEvents==postingEvents);parity(graph,posting);
        const auto d=decision(posting,0);
        CHECK(d.checks==length && d.passes==passes);
        CHECK(activations==int(passes<.01*length));
        CHECK(posting.ordinaryPredicateCalls==graph.ordinaryPredicateCalls);
        CHECK(posting.auxiliaryPredicateCalls==0);
    }
    clear();writeRow(0,{0});
    auto unjudged=observed();unjudged.posting=forbidden;
    run(unjudged,{},64);
    CHECK(decision(unjudged,0).checks==0 && unjudged.ordinaryNeighborEntries>0);
    std::cout<<"PASS real native callback sequences p_resultFilter/support/validposting/candidate/own exact order; mixed/equality .01, allfail, ownGain not predicate, unjudged unknown\n";

    std::array<std::vector<std::vector<std::uint32_t>>,2> csr;
    csr[0].resize(8);csr[1].resize(8);
    for (auto& row:csr[0]) for (int i=0;i<count;++i) row.push_back(i);
    for (auto& row:csr[1]) for (int i=0;i<8;++i) row.push_back(i);
    NativeReuse::PostingModel model;model.counts={count,8,8};
    std::set<std::pair<int,int>> signatures;bool requireSignature=false;int accesses=0;
    model.children=[&](int level,int id)->NativeReuse::PostingModel::Row {
        ++accesses;if(requireSignature) CHECK(signatures.count({level,id}));
        const auto& row=csr[level-1][id];return {row.data(),row.data()+row.size()};
    };
    model.BuildOwners();requireSignature=true;
    const auto postingRun=[&](bool allow,bool diagnostics,int budget,bool allEligible,Hooks& h) {
        clear();writeRow(0,{bridge});writeRow(bridge,{target});
        accesses=0;signatures.clear();
        h=observed();h.diagnostics=h.capture=diagnostics;
        auto sig=[&](int level,int id){CHECK(signatures.insert({level,id}).second);return allow;};
        auto distance=[&](int level,int id){CHECK(signatures.count({level,id}));return float(id);};
        NativeReuse::PostingSupplier supplier(model,h,sig,distance);
        auto posting=[&](int id,const COMMON::NativeEdgeConsumer& n) {
            CHECK(seen(h,bridge));return supplier.Expand(id,n);
        };
        auto match=[&](int id){return (allEligible && id!=bridge && id!=0) || id==target;};
        h.auxiliaryEligible=match;h.posting=posting;
        const auto result=run(h,{target},budget);
        h.posting={};h.auxiliaryEligible={};return result;
    };
    Hooks reject;
    CHECK(postingRun(false,true,64,false,reject).first==target);
    CHECK(reject.signatureRejects>0 && reject.parentDistances==0 && accesses==0);
    CHECK(seen(reject,bridge) && reject.selectedActions==0);
    Hooks combined;
    const auto expected=postingRun(true,true,2048,false,combined);
    CHECK(expected.first==target && combined.h2Rows>0 && combined.childDistances>0);
    CHECK(combined.sparseActivations>0 && combined.rows.front().id==0);
    CHECK(combined.rows.front().size==count && combined.rows.front().consumed==count);
    CHECK(combined.ordinaryPredicateCalls==1 && combined.ordinaryPredicatePasses==0);
    CHECK(combined.auxiliaryPredicateCalls>0 && combined.sparseActivations==1);
    Hooks off;CHECK(postingRun(true,false,2048,false,off)==expected);
    CHECK(off.checked==combined.checked && off.visitedEdges==combined.visitedEdges);
    CHECK(off.headDistances==0 && off.childDistances==0 && off.decisions.empty());
    Hooks completed;
    const auto budgetResult=postingRun(true,true,8,true,completed);
    CHECK(completed.checked>8 && completed.h2Rows==1);
    for (const auto& row:completed.rows) CHECK(row.consumed==row.size);
    Hooks budgetOff;
    CHECK(postingRun(true,false,8,true,budgetOff)==budgetResult);
    CHECK(budgetOff.checked==completed.checked && budgetOff.visitedEdges==completed.visitedEdges);
    clear();writeRow(0,{bridge});writeRow(bridge,{target});
    auto poison=observed();
    auto rejectMember=[&](int head,const COMMON::NativeEdgeConsumer& n) {
        if(head==0) {
            const std::uint32_t row[]={static_cast<std::uint32_t>(fresh[1])};
            CHECK(n.consumeRow(row,1).fresh==0);
        }
        return false;
    };
    writeRow(bridge,{fresh[1]});writeRow(fresh[1],{target});
    poison.posting=rejectMember;
    CHECK(run(poison,{target},64).first==target && seen(poison,fresh[1]));
    requireSignature=false;csr[0].resize(9);csr[0][7]={0};
    for(int i=1;i<count;++i) csr[0][8].push_back(i);
    for(auto& row:csr[1]) row.push_back(8);
    model.counts[1]=9;model.BuildOwners();
    requireSignature=true;signatures.clear();accesses=0;
    clear();writeRow(0,{bridge});
    auto upper=observed();
    auto upperSig=[&](int level,int id) {
        CHECK(signatures.insert({level,id}).second);return level==2?id==0:id==8;
    };
    auto upperDistance=[&](int level,int id){CHECK(signatures.count({level,id}));return float(id);};
    NativeReuse::PostingSupplier upperSupplier(model,upper,upperSig,upperDistance);
    auto upperPosting=[&](int id,const COMMON::NativeEdgeConsumer& n){return upperSupplier.Expand(id,n);};
    upper.posting=upperPosting;
    CHECK(run(upper,{target},64).first==target && upper.h3Rows==1 && upper.h2Rows==1);
    CHECK(upper.rows[0].level==2 && upper.rows[1].id==8);
    std::cout<<"PASS zero-pass actual H2/H3: signature before representative/CSR, complete CSR crossing budget8 and no next action, ordinary per-edge truncation, rejected auxiliary does not poison bridge\n";

    clear();writeRow(0,{bridge});writeRow(bridge,{target});
    for(bool gather:{false,true}) {
        auto own=observed(gather);std::set<int> ids;
        own.ownPoint=[&](int id,float){if(id==target) ids.insert(id);};
        CHECK(run(own,{},64).first<0 && ids.count(target));
    }
    CHECK(index.DeleteIndex(target)==ErrorCode::Success);
    for(bool gather:{false,true}) {auto h=observed(gather);CHECK(run(h,{target},64).first!=target);}
    BKT::Index<float> collapsed;
    collapsed.SetParameter("NumberOfThreads","1");collapsed.SetParameter("BKTLambdaFactor","0");
    collapsed.SetParameter("NeighborhoodSize","32");
    std::vector<float> same(1024*8,1);
    CHECK(collapsed.BuildIndex(same.data(),1024,8)==ErrorCode::Success);
    int collapsedRows=0;
    for(int i=0;i<1024;++i) collapsedRows+=collapsed.GetGraph()[i][31]<-1;
    CHECK(collapsedRows>0);
    for(int budget:{1,2,8,32,2048}) {
        Hooks graph=observed(false),observe=observed();
        observe.posting=forbidden;observe.activationRatio=0;
        Result a(same.data(),4),b(same.data(),4);
        std::vector<int> predicatesA,predicatesB,ownA,ownB;
        graph.ownPoint=[&](int id,float){ownA.push_back(id);};
        observe.ownPoint=[&](int id,float){ownB.push_back(id);};
        CHECK(collapsed.SearchIndexWithNativeHooks(a,graph,[&](int id){
            predicatesA.push_back(id);return id%4==1;},{},budget)==ErrorCode::Success);
        CHECK(collapsed.SearchIndexWithNativeHooks(b,observe,[&](int id){
            predicatesB.push_back(id);return id%4==1;},{},budget)==ErrorCode::Success);
        CHECK(predicatesA==predicatesB && ownA==ownB);
        CHECK(std::set<int>(predicatesA.begin(),predicatesA.end()).size()==predicatesA.size());
        parity(graph,observe);
        for(int i=0;i<4;++i) CHECK(a.GetResult(i)->VID==b.GetResult(i)->VID && a.GetResult(i)->Dist==b.GetResult(i)->Dist);
        auto own=observed();std::set<int> ids;
        own.ownPoint=[&](int id,float){if(id%7==0 && collapsed.ContainSample(id)) ids.insert(id);};
        Result ownResult(same.data(),4);
        CHECK(collapsed.SearchIndexWithNativeHooks(ownResult,own,[](int){return false;},{},budget)==ErrorCode::Success);
        CHECK(ids.count(0) && ownResult.GetResult(0)->VID<0);
    }
    std::vector<std::shared_ptr<VectorSet>> noCatalogs;std::vector<int> noPostings;
    for(const char* mode:{"graph","posting"}) for(bool capture:{false,true}) {
        ShortcutFull::mode=mode;ShortcutFull::capture=capture;ShortcutFull::Last()={};
        Result native(data.data(),2),empty(data.data(),2);
        CHECK(index.SearchIndex(native)==ErrorCode::Success);
        CHECK(NativeReuse::Search(&index,&empty,nullptr,noCatalogs,noPostings,
            [](int){CHECK(false);return false;},[](int,float){CHECK(false);return false;},
            [](int,int){CHECK(false);return false;},true,[](int){CHECK(false);return false;})==ErrorCode::Success);
        CHECK(!ShortcutFull::Last().invoked);
        for(int i=0;i<2;++i) CHECK(native.GetResult(i)->VID==empty.GetResult(i)->VID && native.GetResult(i)->Dist==empty.GetResult(i)->Dist);
        if(std::string(mode)!="posting") {
            Result result(data.data(),2);
            CHECK(NativeReuse::Search(&index,&result,nullptr,noCatalogs,noPostings,
                [](int){return false;},[](int,float){return false;},[](int,int){CHECK(false);return false;},
                false,[](int){return false;})==ErrorCode::Success);
            const auto& h=ShortcutFull::Last().native;
            CHECK(!h.ownPoint && !h.ownGain && !h.auxiliaryEligible && !h.posting && h.calls==0);
        }
    }
    std::cout<<"PASS own/posting separation, mutable deletes, aliases/ties, graph without hierarchy, unfilter direct native zero hooks, retired controls rejected\n";
}
