#include <NativeSupplier.h>
#include <Qualification.h>
#include <iostream>
#include <map>
#include <set>

using namespace SPTAG;
using Hooks=COMMON::NativeNeighborHooks;
using Result=COMMON::QueryResultSet<float>;
void Check(bool value,int line) {
    if (!value) throw std::runtime_error("Native NaviX fixture failed at "+std::to_string(line));
}
#define CHECK(v) Check((v),__LINE__)

int main() {
    {
        NativeReuse::QualificationLease disabled(false,160091,true);
        CHECK(disabled.Get()==nullptr);
        NativeReuse::QualificationLease lease(true,160091,true);
        auto& q=*lease.Get();
        int postingCalls=0,exactCalls=0,liveCalls=0;
        bool deleted=false;
        auto posting=[&] {++postingCalls;return false;};
        auto exact=[&] {++exactCalls;return true;};
        auto live=[&] {++liveCalls;return !deleted;};
        CHECK(q.Eligible(160090,posting,exact,live));
        CHECK(q.Eligible(160090,posting,exact,live));
        CHECK(postingCalls==1 && exactCalls==1 && liveCalls==2);
        CHECK(!q.Posting(160090,[] {CHECK(false);return true;}));
        CHECK(q.Exact(160090,[] {CHECK(false);return false;}));
        deleted=true;
        CHECK(!q.Eligible(160090,posting,exact,live) && liveCalls==3);
        deleted=false;
        CHECK(q.Eligible(160090,posting,exact,live) && exactCalls==1);
        CHECK(q.Posting(12,[] {return true;}));
        CHECK(!q.Exact(12,[] {return false;}));
        CHECK(q.Eligible(12,posting,exact,[] {CHECK(false);return false;}));
        NativeReuse::QualificationLease nested(true,160091,true);
        CHECK(nested.Get()!=&q && !nested.Get()->Exact(160090,[] {return false;}));
        q.Begin(160091,true);
        CHECK(q.counts[5]==0 && !q.Exact(160090,[] {return false;}));
        CHECK(!q.Eligible(160090,posting,exact,live));
        q.Begin(2,false);
        CHECK(q.Eligible(1,[] {return true;},exact,live));
        CHECK(q.Eligible(-1,[] {return true;},exact,live));
        CHECK((q.counts==std::array<std::uint64_t,6>{}));
        std::cout<<"PASS qualification byte: posting/own separation, positive/negative reuse, "
            "uncached mutable deletion, per-query reset, >65536 IDs, nested lease, empty zero-work\n";
    }
    BKT::Index<float> index;
    for (auto p:std::vector<std::pair<const char*,const char*>>{
        {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"MaxCheck","2048"},{"BKTKmeansK","8"},
        {"BKTLeafSize","1"},{"NeighborhoodSize","32"},{"TPTNumber","1"},
        {"NumberOfInitialDynamicPivots","1"},{"NumberOfOtherDynamicPivots","0"}})
        index.SetParameter(p.first,p.second);
    constexpr int count=128;
    std::vector<float> data(count*8);
    for (int i=0;i<count;++i) for (int j=0;j<8;++j) data[8*i+j]=float(i*(j+1));
    CHECK(index.BuildIndex(data.data(),count,8)==ErrorCode::Success);
    const auto clear=[&] {
        for (int i=0;i<count;++i) std::fill(index.GetMutableGraph()[i],index.GetMutableGraph()[i]+32,-1);
    };
    clear();
    std::set<int> seeds;
    Hooks entry; entry.ownPoint=[&](int id,float){seeds.insert(id);};
    Result entryResult(data.data(),1);
    CHECK(index.SearchIndexWithNativeHooks(entryResult,entry,[](int){return false;},{},1)==ErrorCode::Success);
    CHECK(seeds.count(0));
    std::vector<int> fresh;
    for (int i=1;i<count;++i) if (!seeds.count(i)) fresh.push_back(i);
    CHECK(fresh.size()>80);
    const auto writeRow=[&](int id,const std::vector<int>& row) {
        CHECK(row.size()<=31);
        std::copy(row.begin(),row.end(),index.GetMutableGraph()[id]);
    };
    const auto run=[&](Hooks& hooks,const std::set<int>& matches,int budget=2048) {
        hooks.navix=true;
        Result result(data.data(),1);
        CHECK(index.SearchIndexWithNativeHooks(result,hooks,
            [&](int id){return matches.count(id)!=0;},{},budget)==ErrorCode::Success);
        return std::make_pair(result.GetResult(0)->VID,result.GetResult(0)->Dist);
    };
    const auto observed=[] {
        Hooks h; h.capture=h.diagnostics=h.auditVisited=true; return h;
    };
    const auto decision=[](const Hooks& h,int id)->Navix::Decision {
        for (const auto& d:h.decisions) if (d.head==id) return d;
        throw std::runtime_error("Missing real native expansion");
    };
    const auto seen=[](const Hooks& h,int id) {
        return std::find(h.visitedEdges.begin(),h.visitedEdges.end(),std::make_pair(id,false))!=h.visitedEdges.end();
    };

    for (int e:{0,1,5,10}) {
        clear();
        std::vector<int> row(fresh.begin(),fresh.begin()+20);
        row[0]=0;
        writeRow(0,row);
        std::set<int> matches;
        for (int i=0;i<e;++i) matches.insert(row[i]);
        Hooks h=observed(); h.postingThreshold=.05;
        int callbacks=0;
        auto reject=[&](int,const COMMON::NativeEdgeConsumer&){++callbacks;return false;};
        h.posting=reject;
        run(h,matches,64);
        const auto d=decision(h,0);
        CHECK(d.d==20 && d.e==e);
        CHECK(d.route==(e==0?Navix::Posting:e==10?Navix::OneHop:e==5?Navix::Directed:Navix::FullTwoHop));
        if (e==0) CHECK(callbacks>0 && d.fallback==Navix::FullTwoHop);
        else CHECK(callbacks==0 && d.signatures==0 && d.representatives==0 && d.members==0);
        CHECK(h.parentDistances==0 && h.signatureChecks==0);
    }
    clear();
    Hooks zero=observed(); zero.postingThreshold=.05;
    auto forbidden=[](int,const COMMON::NativeEdgeConsumer&)->bool { CHECK(false);return false; };
    zero.posting=forbidden;
    run(zero,{0},32);
    CHECK(decision(zero,0).route==Navix::NoNeighbors && zero.qualificationChecks==0);

    clear();
    const int bridge=fresh[0],target=fresh[60];
    writeRow(0,{bridge}); writeRow(bridge,{target});
    Hooks two=observed();
    CHECK(run(two,{target}).first==target && seen(two,target));
    CHECK(decision(two,0).route==Navix::FullTwoHop && two.intermediates.front()==bridge);

    clear();
    writeRow(0,{0,target});
    Hooks visited=observed();
    run(visited,{target},64);
    // s=.5 would choose one-hop; add a nonmatch to force full-two-hop.
    clear(); writeRow(0,{0,target,bridge});
    visited=observed(); run(visited,{target},64);
    CHECK(decision(visited,0).route==Navix::FullTwoHop);
    CHECK(std::find(visited.intermediates.begin(),visited.intermediates.end(),0)!=visited.intermediates.end());
    CHECK(seen(visited,target));

    clear();
    std::vector<int> directRow;
    for (int i=14;i>=0;--i) directRow.push_back(fresh[i]);
    std::set<int> directMatches{0,target};
    directRow.push_back(0);
    for (int i=30;i<34;++i) {directRow.push_back(fresh[i]);directMatches.insert(fresh[i]);}
    writeRow(0,directRow);
    std::vector<int> complete(31,0); complete.back()=target;
    writeRow(bridge,complete);
    Hooks directed=observed(); run(directed,directMatches,64);
    CHECK(decision(directed,0).route==Navix::Directed);
    CHECK(directed.intermediates.front()==bridge && seen(directed,target));
    CHECK(directed.intermediateDistances>=15);
    CHECK(directed.secondRows==1);

    clear();writeRow(0,directRow);
    Hooks ordered=observed();run(ordered,directMatches,64);
    CHECK(ordered.intermediates.size()>=15);
    for(int i=0;i<15;++i) CHECK(ordered.intermediates[i]==fresh[i]);

    std::array<std::vector<std::vector<std::uint32_t>>,2> csr;
    csr[0].resize(8);csr[1].resize(8);
    for (auto& row:csr[0]) for (int i=0;i<count;++i) row.push_back(i);
    for (auto& row:csr[1]) for (int i=0;i<8;++i) row.push_back(i);
    int accesses=0;
    NativeReuse::PostingModel model;model.counts={count,8,8};
    std::set<std::pair<int,int>> signatures;
    bool requireSignature=false;
    model.children=[&](int level,int id)->NativeReuse::PostingModel::Row {
        ++accesses;
        if (requireSignature) CHECK(signatures.count({level,id}));
        const auto& row=csr[level-1][id];return {row.data(),row.data()+row.size()};
    };
    model.BuildOwners(); requireSignature=true;
    const auto postingRun=[&](bool allow,bool diagnostics,bool capture,int budget,Hooks& h) {
        clear(); writeRow(0,{bridge});writeRow(bridge,{target});
        accesses=0;signatures.clear();
        h.navix=true;h.diagnostics=diagnostics;h.capture=capture;h.auditVisited=true;h.postingThreshold=.05;
        auto sig=[&](int level,int id) {CHECK(signatures.insert({level,id}).second);return allow;};
        auto distance=[&](int level,int id) {
            CHECK(signatures.count({level,id}));
            return index.ComputeDistance(data.data(),index.GetSample(id));
        };
        NativeReuse::PostingSupplier supplier(model,h,sig,distance);
        auto posting=[&](int id,const COMMON::NativeEdgeConsumer& n){return supplier.Expand(id,n);};
        h.posting=posting;
        auto result=run(h,{target},budget);
        h.posting={};return result;
    };
    Hooks rejected=observed();
    CHECK(postingRun(false,true,true,64,rejected).first==target);
    CHECK(rejected.signatureRejects>0 && rejected.parentDistances==0 && accesses==0);
    CHECK(rejected.fallbacks>0 && seen(rejected,target));
    Hooks combined=observed();
    const auto expected=postingRun(true,true,true,8,combined);
    CHECK(expected.first==target && combined.h2Rows>0 && combined.childDistances>0 && combined.members>=128);
    CHECK(combined.rows.front().id==0);
    CHECK(combined.branches[Navix::Posting]>0);
    for (const auto& row:combined.rows) CHECK(row.size==row.consumed);
    CHECK(!seen(combined,bridge));
    Hooks off;
    CHECK(postingRun(true,false,false,8,off)==expected);
    CHECK(off.checked==combined.checked && off.visitedEdges==combined.visitedEdges);
    CHECK(off.headDistances==0 && off.childDistances==0 && off.decisions.empty());

    // A rejected CSR member must remain available to directed intermediate expansion.
    clear();writeRow(0,directRow);writeRow(bridge,complete);
    Hooks poison=observed();poison.postingThreshold=.3;
    auto failed=[&](int head,const COMMON::NativeEdgeConsumer& n) {
        if(head!=0) return false;
        const std::uint32_t row[]={static_cast<std::uint32_t>(bridge)};
        const auto w=n.consumeRow(row,1);
        CHECK(w.members==1 && w.fresh==0);
        return false;
    };
    poison.posting=failed;
    run(poison,directMatches,64);
    CHECK(decision(poison,0).fallback==Navix::Directed && poison.intermediates.front()==bridge && seen(poison,target));

    requireSignature=false;
    csr[0].resize(9);
    csr[0][7]={0};
    for(int i=1;i<count;++i) csr[0][8].push_back(i);
    for(auto& row:csr[1]) row.push_back(8);
    model.counts[1]=9;model.BuildOwners();
    requireSignature=true;signatures.clear();accesses=0;
    clear();writeRow(0,{bridge});
    Hooks upper=observed();upper.postingThreshold=.05;
    auto upperSignature=[&](int level,int id) {
        CHECK(signatures.insert({level,id}).second);
        return level==2 ? id==0 : id==8;
    };
    auto upperDistance=[&](int level,int id) {
        CHECK(signatures.count({level,id}));
        return index.ComputeDistance(data.data(),index.GetSample(id));
    };
    NativeReuse::PostingSupplier upperSupplier(model,upper,upperSignature,upperDistance);
    auto upperPosting=[&](int id,const COMMON::NativeEdgeConsumer& n){return upperSupplier.Expand(id,n);};
    upper.posting=upperPosting;
    CHECK(run(upper,{target},64).first==target);
    CHECK(upper.h3Rows==1 && upper.h2Rows==1 && upper.rows[0].level==2 && upper.rows[1].id==8);
    for(const auto& row:upper.rows) CHECK(row.size==row.consumed);
    upper.posting={};

    clear();writeRow(0,{bridge});writeRow(bridge,{target});
    Hooks ownOnly=observed();
    std::set<int> ownOnlyIDs;
    auto ownEligible=[&](int id){return id==target;};
    ownOnly.eligible=ownEligible;
    ownOnly.ownPoint=[&](int id,float){if(id==target) ownOnlyIDs.insert(id);};
    CHECK(run(ownOnly,{},64).first<0 && ownOnlyIDs.count(target));

    NativeReuse::Qualification nativeQualification;
    nativeQualification.Begin(count,true);
    int ownCalls=0;
    const auto cachedRun=[&](bool diagnostics) {
        Hooks h;h.diagnostics=h.capture=diagnostics;
        auto eligible=[&](int id) {
            return nativeQualification.Eligible(id,[&] {return id==target;},
                [] {return false;},[] {return true;});
        };
        auto own=[&](int,float) {++ownCalls;return false;};
        h.eligible=eligible;h.ownGain=own;
        return run(h,{target},64);
    };
    CHECK(cachedRun(true).first==target);
    const auto previousOwnCalls=ownCalls;
    CHECK(cachedRun(false).first==target && ownCalls>previousOwnCalls);
    CHECK(index.DeleteIndex(target)==ErrorCode::Success);
    CHECK(cachedRun(true).first!=target);
    Hooks deleted=observed();
    CHECK(postingRun(true,true,true,64,deleted).first!=target);
    BKT::Index<float> collapsed;
    collapsed.SetParameter("NumberOfThreads","1");collapsed.SetParameter("BKTLambdaFactor","0");
    collapsed.SetParameter("NeighborhoodSize","32");
    std::vector<float> same(1024*8,1);
    CHECK(collapsed.BuildIndex(same.data(),1024,8)==ErrorCode::Success);
    int collapsedRows=0;
    for(int i=0;i<1024;++i) collapsedRows+=collapsed.GetGraph()[i][31]<-1;
    CHECK(collapsedRows>0);
    for(int budget:{1,2,8,32,2048}) {
        Hooks own=observed();own.navix=true;
        std::set<int> ownIDs;
        own.ownPoint=[&](int id,float){if(id%7==0 && collapsed.ContainSample(id)) ownIDs.insert(id);};
        Result ownResult(same.data(),4);
        CHECK(collapsed.SearchIndexWithNativeHooks(ownResult,own,[](int){return false;},{},budget)==ErrorCode::Success);
        CHECK(ownIDs.count(0) && ownResult.GetResult(0)->VID<0);
        Hooks alias=observed();alias.navix=true;
        Result aliases(same.data(),4);
        CHECK(collapsed.SearchIndexWithNativeHooks(aliases,alias,[](int id){return id%4==1;},{},budget)==ErrorCode::Success);
        CHECK(aliases.GetResult(0)->VID>=0);
        for(int i=0;i<4;++i) CHECK(aliases.GetResult(i)->VID<0 || aliases.GetResult(i)->VID%4==1);
    }
    const std::map<std::string,std::string> validParams{
        {"navixmode","posting"},{"navixcapture","false"},{"navixpostingthreshold","0.05"},
        {"headnavigationmode","H1Only"},{"numberofthreads","1"},
        {"enablehybriddistance","false"},{"enableadaptivefilterednprobe","false"}};
    ShortcutFull::Configure(validParams);
    for (const char* key:{"arbitrationmode","shortcutretainedratio","navixunknown"}) {
        auto params=validParams;params[key]="0";
        bool caught=false;try{ShortcutFull::Configure(params);}catch(const std::exception&){caught=true;}
        CHECK(caught);
    }
    for (const char* value:{"nan","inf","-0.1","0.6","0.05junk"}) {
        auto params=validParams;params["navixpostingthreshold"]=value;
        bool caught=false;try{ShortcutFull::Configure(params);}catch(const std::exception&){caught=true;}
        CHECK(caught);
    }
    ShortcutFull::mode="posting";ShortcutFull::capture=false;
    Result native(data.data(),2),empty(data.data(),2);
    CHECK(index.SearchIndex(native)==ErrorCode::Success);
    std::vector<std::shared_ptr<VectorSet>> noCatalogs;std::vector<int> noPostings;
    CHECK(NativeReuse::Search(&index,&empty,nullptr,noCatalogs,noPostings,
        [](int){CHECK(false);return false;},[](int,float){CHECK(false);return false;},
        [](int,int){CHECK(false);return false;},true,[](int){CHECK(false);return false;})==ErrorCode::Success);
    for(int i=0;i<2;++i) CHECK(native.GetResult(i)->VID==empty.GetResult(i)->VID &&
                              native.GetResult(i)->Dist==empty.GetResult(i)->Dist);
    std::cout<<"PASS real BKT: threshold equality, d0, valid visited ratio, all graph branches, bridge, "
        "ascending directed intermediates, complete selected row, valid encounters before visited, "
        "full visited intermediate, graph zero upper callbacks, actual CSR posting, signature order, "
        "full CSR rows, rejected auxiliary non-poisoning, deleted/own/aliases/ties, diagnostics parity, empty bypass\n";
}
