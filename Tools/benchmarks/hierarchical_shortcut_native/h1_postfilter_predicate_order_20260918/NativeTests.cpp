#include <NativeSupplier.h>
#include "inc/Core/SPANN/HeadNodeMetadata.h"
#include <iostream>
#include <map>
#include <set>

using namespace SPTAG;
using Hooks=COMMON::NativeNeighborHooks;
using Result=COMMON::QueryResultSet<float>;
#define CHECK(v) do { if (!(v)) throw std::runtime_error("Native restoration fixture line "+std::to_string(__LINE__)); } while (false)

int main() {
    try {
        ShortcutFull::mode="graph";
        BKT::Index<float> index;
        for (auto p:std::vector<std::pair<const char*,const char*>>{
            {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"MaxCheck","2048"},
            {"BKTKmeansK","8"},{"BKTLeafSize","1"},{"NeighborhoodSize","128"},
            {"TPTNumber","1"},{"NumberOfInitialDynamicPivots","1"},{"NumberOfOtherDynamicPivots","0"}})
            index.SetParameter(p.first,p.second);
        constexpr int count=256;
        std::vector<float> data(count*8);
        for (int i=0;i<count;++i) for (int j=0;j<8;++j) data[8*i+j]=float(i*(j+1));
        std::copy_n(data.data()+8*254,8,data.data()+8*255);
        CHECK(index.BuildIndex(data.data(),count,8)==ErrorCode::Success);
        SPANN::LimitedTagSupport support;
        CHECK(support.Initialize(count,2,1,0,2,1));
        index.InitializeHeadNodeMeta(count,0,index.GetHeadNodeHierWidths(),true);
        index.SetHeadNodeOwnTagsAvailable(true);
        for(int id=0;id<count;++id) {
            const std::uint32_t attributes[]={static_cast<std::uint32_t>(100+id),static_cast<std::uint32_t>(id)};
            CHECK(support.SetHeadAttributes(id,attributes,2));
            CHECK(support.SetHeadTags(id,{attributes[0]}));
            CHECK(support.Supports(id,attributes[0]));
            Cache::PostingBitmask empty;empty.Clear();index.SetHeadNodePS(id,empty);
        }
        std::vector<Cache::PostingBitmask> signatures;
        CHECK(SPANN::CollectHierarchyHeadSignatures(index,support,signatures));
        for(int id=0;id<count;++id) {
            Cache::PostingBitmask own;own.Clear();own.Insert(support.OwnTag(id));
            for(std::size_t w=0;w<sizeof(own.bits)/sizeof(own.bits[0]);++w)
                CHECK((signatures[id].bits[w]&own.bits[w])==own.bits[w]);
        }
        std::cout<<"PASS native CollectHierarchyHeadSignatures includes own tags with empty posting PS; support own-only route requires no result eligibility scan\n";
        const auto observed=[](bool match=true) {
            Hooks h;h.capture=h.diagnostics=h.auditVisited=true;
            h.defaultAdmission=false;h.matchEnabled=h.immutableSnapshot=match;return h;
        };
        const auto values=[](Result& r) {
            std::vector<std::pair<int,float>> out;
            for (int i=0;i<r.GetResultNum();++i) out.emplace_back(r.GetResult(i)->VID,r.GetResult(i)->Dist);
            return out;
        };
        const auto run=[&](Hooks& h,const std::function<bool(int)>& predicate,int budget=2048,int k=256) {
            Result r(data.data(),k);
            CHECK(index.SearchIndexWithNativeHooks(r,h,predicate,{},budget)==ErrorCode::Success);
            return values(r);
        };
        const auto parity=[](const Hooks& a,const Hooks& b) {
            CHECK(a.checked==b.checked && a.evaluated==b.evaluated);
            CHECK(a.visitedEdges==b.visitedEdges && a.allVisited==b.allVisited);
            CHECK(a.queueOffers==b.queueOffers && a.queueAccepted==b.queueAccepted &&
                  a.queueRejected==b.queueRejected && a.outerExpansions==b.outerExpansions);
            CHECK(a.nativePredicateCalls==b.nativePredicateCalls);
            CHECK(b.nativePredicateCalls==b.nativePredicateEvaluations+b.nativePredicateBitReads);
        };
        auto noPosting=[](int,const COMMON::NativeEdgeConsumer&)->bool {CHECK(false);return false;};
        // Native collapsed aliases and distance/termination are not qualification work.
        for (int budget:{1,8,64,2048}) for (int k:{1,24,count}) {
            auto graph=observed(false),bit=observed();
            bit.posting=noPosting;bit.activationRatio=0;
            Result native(data.data(),k);
            CHECK(index.SearchIndexWithResultFilter(native,[](int){return false;},budget)==ErrorCode::Success);
            const auto expected=run(graph,[](int){return false;},budget,k);
            CHECK(values(native)==expected && run(bit,[](int){return false;},budget,k)==expected);
            parity(graph,bit);
        }
        CHECK(index.DeleteIndex(254)==ErrorCode::Success);
        auto deleted=observed();
        const auto deletedResults=run(deleted,[](int){return true;});
        for (auto r:deletedResults) CHECK(r.first!=254);
        CHECK(deleted.matchComponentCalls==deleted.firstMatchEvaluations);
        std::cout<<"PASS native alias/tie/delete admission and graph/match IDs/distances/queues/visited/termination; no match alias/liveness/own qualification\n";

        const auto clear=[&] {
            for (int i=0;i<count;++i) std::fill_n(index.GetMutableGraph()[i],128,-1);
        };
        const auto row=[&](int id,const std::vector<int>& edges) {
            CHECK(edges.size()<128);std::copy(edges.begin(),edges.end(),index.GetMutableGraph()[id]);
        };
        clear();auto seeds=observed(false);run(seeds,[](int){return false;},1);
        std::set<int> inserted;
        for (auto v:seeds.allVisited) if (!v.second) inserted.insert(v.first);
        CHECK(inserted.count(0));
        std::vector<int> fresh;
        for (int id=1;id<254;++id) if (!inserted.count(id)) fresh.push_back(id);
        CHECK(fresh.size()>127);
        const int bridge=fresh[0],target=fresh[60];
        const auto decision=[](const Hooks& h,int head) {
            for (auto d:h.decisions) if (d.head==head) return d;
            throw std::runtime_error("Missing ordinary row");
        };
        const auto seen=[](const Hooks& h,int id) {
            return std::find(h.allVisited.begin(),h.allVisited.end(),std::make_pair(id,false))!=h.allVisited.end();
        };
        clear();row(0,{0,0,0,0,0,bridge});row(bridge,{target});
        auto visited=observed();visited.posting=noPosting;
        run(visited,[&](int id){return id!=bridge;});
        CHECK(decision(visited,0).degree==6 && decision(visited,0).eligible==5 &&
              decision(visited,0).eligibleVisited==5 && seen(visited,target));
        for (int wanted:{bridge,target}) {
            auto once=observed();once.diagnostics=once.capture=false;
            std::map<int,int> calls;
            run(once,[&](int id){++calls[id];return id==wanted;});
            CHECK(calls.at(bridge)==1 && calls.at(target)==1);
            CHECK(calls.count(bridge) && calls.count(target));
        }
        clear();row(0,std::vector<int>(fresh.begin(),fresh.begin()+127));
        auto nativeOrder=observed(false);
        std::map<int,int> admissionCalls;
        const auto nearestOne=run(nativeOrder,[&](int id){++admissionCalls[id];return true;},2048,1);
        CHECK(nearestOne[0].first==0 && nearestOne[0].second==0);
        CHECK(nativeOrder.ordinaryPredicateCalls==nativeOrder.queueOffers &&
              nativeOrder.ordinaryPredicateCalls>=127);
        for(int i=0;i<127;++i) CHECK(admissionCalls.at(fresh[i])==1);
        std::cout<<"PASS original predicate order: all 127 fresh ordinary neighbors evaluated despite distance above output worstDist\n";
        const std::set<int> farther(fresh.begin()+64,fresh.begin()+88);
        auto h1Predicate=[&](int id){return farther.count(id)!=0;};
        Result nearest(data.data(),24),postfiltered(data.data(),24);
        CHECK(index.SearchIndexWithMaxCheck(nearest,2048)==ErrorCode::Success);
        for(auto r:values(nearest)) CHECK(!farther.count(r.first));
        CHECK(index.SearchIndexWithResultFilter(postfiltered,h1Predicate,2048)==ErrorCode::Success);
        auto clean=observed(false),bits=observed();
        const auto expected=run(clean,h1Predicate,2048,24);
        CHECK(values(postfiltered)==expected && run(bits,h1Predicate,2048,24)==expected);
        for(auto r:expected) CHECK(farther.count(r.first));
        parity(clean,bits);
        CHECK(bits.nativePredicateBitReads>0 &&
              bits.nativePredicateEvaluations<clean.nativePredicateEvaluations);
        std::cout<<"PASS over-restoration trap: closest24 unmatched, 24 farther matching H1 heads returned through nonmatching graph; native result-filter reference and bit parity\n";
        clear();row(0,{bridge});
        auto ownOnly=observed();
        const auto ownResult=run(ownOnly,[&](int id){return support.Supports(id,support.OwnTag(0));},64,1);
        CHECK(ownResult[0].first==0 && ownResult[0].second==0);
        CHECK(!ownOnly.ownPoint && !ownOnly.ownGain && index.GetHeadNodePS(0)->Popcount()==0);
        std::cout<<"PASS selected own-only H1 with empty posting PS admitted by support; no supplementary own collector\n";
        for (int length:{100,101}) for (int pass:{0,1,2}) {
            clear();row(0,std::vector<int>(fresh.begin(),fresh.begin()+length));
            auto h=observed();int activated=0;
            auto action=[&](int head,const COMMON::NativeEdgeConsumer&) {CHECK(head==0);++activated;return false;};
            h.posting=action;
            std::set<int> matches(fresh.begin(),fresh.begin()+pass);
            run(h,[&](int id){return matches.count(id)!=0;});
            const auto d=decision(h,0);
            CHECK(d.degree==length && d.eligible==pass && d.oracleEligible==pass &&
                  d.oracleDegree==length && activated==int(pass<.01*length));
        }
        clear();row(0,std::vector<int>(fresh.begin(),fresh.begin()+127));
        auto partial=observed();partial.posting=noPosting;
        run(partial,[](int){return false;},8);
        CHECK(!decision(partial,0).complete && partial.checked==8);
        clear();auto empty=observed();empty.posting=noPosting;
        run(empty,[](int){return false;});
        CHECK(decision(empty,0).degree==0);
        std::cout<<"PASS single first evaluation across query reuse; real physical degree including visited, equality/zero/partial rows; false bridge navigated\n";

        std::array<std::vector<std::vector<std::uint32_t>>,2> csr;
        csr[0].resize(8);csr[1].resize(8);
        for (auto& r:csr[0]) for (int i=0;i<count;++i) r.push_back(i);
        for (auto& r:csr[1]) for (int i=0;i<8;++i) r.push_back(i);
        NativeReuse::PostingModel model;model.counts={count,8,8};
        bool audit=false;std::set<std::pair<int,int>> allowed;int accesses=0;
        model.children=[&](int level,int id)->NativeReuse::PostingModel::Row {
            if(audit) {CHECK(allowed.count({level,id}));++accesses;}
            const auto& r=csr[level-1][id];return {r.data(),r.data()+r.size()};
        };
        model.BuildOwners();audit=true;
        for(bool accept:{false,true}) {
            clear();row(0,{bridge});row(bridge,{target});
            auto h=observed();allowed.clear();accesses=0;
            auto signature=[&](int level,int id){if(accept) allowed.insert({level,id});return accept;};
            auto distance=[&](int level,int id){CHECK(allowed.count({level,id}));return float(id);};
            NativeReuse::PostingSupplier supplier(model,h,signature,distance);
            auto action=[&](int head,const COMMON::NativeEdgeConsumer& n){return supplier.Expand(head,n);};
            h.posting=action;
            run(h,[&](int id){return id!=0 && id!=bridge;},8);
            if(accept) {
                CHECK(h.h2Rows==1 && h.checked>8);
                for(auto r:h.rows) CHECK(r.size==r.consumed);
            } else CHECK(h.signatureRejects>0 && h.parentDistances==0 && accesses==0);
        }
        clear();row(0,{bridge});row(bridge,{fresh[1]});row(fresh[1],{target});
        auto poison=observed();
        auto reject=[&](int head,const COMMON::NativeEdgeConsumer& n) {
            if(head==0) {const std::uint32_t ids[]={static_cast<std::uint32_t>(fresh[1])};
                CHECK(n.consumeRow(ids,1).fresh==0 && n.consumeRow(ids,1).fresh==0);}
            return false;
        };
        poison.posting=reject;
        run(poison,[&](int id){return id==target;});
        CHECK(seen(poison,fresh[1]) && seen(poison,target) && poison.rejectedAuxiliaryEvaluations>=2);
        audit=false;csr[0].resize(9);csr[0][7]={0};
        for(int id=1;id<count;++id) csr[0][8].push_back(id);
        for(auto& r:csr[1]) r.push_back(8);
        model.counts[1]=9;model.BuildOwners();audit=true;allowed.clear();
        clear();row(0,{bridge});
        auto upper=observed();
        auto upperSignature=[&](int level,int id) {
            const bool accept=level==2?id==0:id==8;
            if(accept) allowed.insert({level,id});
            return accept;
        };
        auto upperDistance=[&](int level,int id){CHECK(allowed.count({level,id}));return float(id);};
        NativeReuse::PostingSupplier upperSupplier(model,upper,upperSignature,upperDistance);
        auto upperAction=[&](int head,const COMMON::NativeEdgeConsumer& n){return upperSupplier.Expand(head,n);};
        upper.posting=upperAction;run(upper,[&](int id){return id==target;});
        CHECK(upper.h3Rows==1 && upper.h2Rows==1 && upper.rows[0].level==2 && upper.rows[1].id==8);
        std::cout<<"PASS signature before representative/CSR, whole selected row crosses budget, rejected auxiliary does not poison ordinary bridge\n";

        using Storage=COMMON::OptHashPosVector;
        clear();row(0,{bridge});row(bridge,{target});
        for(int i=0;i<4;++i) {auto h=observed(i!=0);run(h,[](int){return true;});}
        Storage::StorageDiagnostics stats;Storage::Diagnostics()=&stats;
        for(int q=0;q<128;++q) {
            if(q%4==0) {Result r(data.data(),count);CHECK(index.SearchIndex(r)==ErrorCode::Success);}
            else {auto h=observed();h.capture=h.diagnostics=false;run(h,[&](int id){return id%2==q%2;});}
        }
        Storage::Diagnostics()=nullptr;
        CHECK(stats.allocations==0 && stats.conversions==0);
        auto captured=observed(),off=observed();off.capture=off.diagnostics=false;
        CHECK(run(captured,[](int){return true;})==run(off,[](int){return true;}));
        CHECK(captured.allVisited==off.allVisited && captured.checked==off.checked);
        std::cout<<"PASS mixed pool native bypass/match 128 queries zero backend allocations/conversions; capture parity\n";
        return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
