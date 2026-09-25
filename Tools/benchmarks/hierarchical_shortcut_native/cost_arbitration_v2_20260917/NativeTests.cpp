#include <NativeSupplier.h>
#include <iostream>
#include <set>
#include <map>

void Check(bool value,int line) {
    if (!value) throw std::runtime_error("Native arbitration fixture failed at "+std::to_string(line));
}
#define CHECK(v) Check((v),__LINE__)
using namespace SPTAG;
using Hooks=COMMON::NativeNeighborHooks;
using Result=COMMON::QueryResultSet<float>;

int main() {
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
    auto clearGraph=[&] {
        for (int id=0;id<count;++id)
            std::fill(index.GetMutableGraph()[id],index.GetMutableGraph()[id]+32,-1);
    };
    clearGraph();
    std::set<int> seeds;
    Hooks seedHooks; seedHooks.ownPoint=[&](int id,float){seeds.insert(id);};
    Result seedResult(data.data(),1);
    CHECK(index.SearchIndexWithNativeHooks(seedResult,seedHooks,[](int){return false;},{},1)==ErrorCode::Success);
    CHECK(seeds.count(0));
    int b=1; while(seeds.count(b)) ++b;
    int c=b+1; while(seeds.count(c)) ++c;
    int d=c+1; while(seeds.count(d)) ++d;
    CHECK(d<count);
    index.GetMutableGraph()[0][0]=b;
    index.GetMutableGraph()[b][0]=c;
    auto matchesC=[&](int id){return id==c;};
    Hooks bridge; bridge.capture=bridge.diagnostics=bridge.auditVisited=true;
    Result result(data.data(),1);
    CHECK(index.SearchIndexWithNativeHooks(result,bridge,matchesC,{},32)==ErrorCode::Success);
    CHECK(result.GetResult(0)->VID==c);
    CHECK(std::find(bridge.visitedEdges.begin(),bridge.visitedEdges.end(),std::make_pair(b,false))!=bridge.visitedEdges.end());
    CHECK(std::find(bridge.visitedEdges.begin(),bridge.visitedEdges.end(),std::make_pair(c,false))!=bridge.visitedEdges.end());
    Result nativePost(data.data(),1);
    CHECK(index.SearchIndexWithResultFilter(nativePost,matchesC,32)==ErrorCode::Success);
    CHECK(nativePost.GetResult(0)->VID==c);

    clearGraph(); index.GetMutableGraph()[b][0]=c;
    Hooks auxiliary; auxiliary.capture=auxiliary.diagnostics=auxiliary.auditVisited=true;
    bool offered=false;
    auto offer=[&](int head,const CostArbitration::Work&,int,const COMMON::NativeEdgeConsumer& native) {
        if (head==0 && !offered) {
            offered=true;
            const std::uint32_t row[]={static_cast<std::uint32_t>(b)};
            const auto work=native.consumeRow(row,1);
            CHECK(work.members==1 && work.fresh==1 && work.predicatePasses==0);
        }
    };
    auxiliary.afterGraph=offer;
    Result auxResult(data.data(),1);
    CHECK(index.SearchIndexWithNativeHooks(auxResult,auxiliary,matchesC,{},32)==ErrorCode::Success);
    CHECK(offered && auxResult.GetResult(0)->VID==c);
    CHECK(std::find(auxiliary.visitedEdges.begin(),auxiliary.visitedEdges.end(),std::make_pair(c,false))!=auxiliary.visitedEdges.end());

    std::array<std::vector<std::vector<std::uint32_t>>,2> rows;
    rows[0].resize(129); rows[1].resize(8);
    for (int p=0;p<8;++p) for (int id=0;id<count;++id) rows[0][p].push_back(id);
    for (auto& row:rows[1]) for (int id=0;id<129;++id) row.push_back(id);
    int accesses=0;
    NativeReuse::PostingModel model;
    model.counts={count,129,8};
    model.children=[&](int level,int id)->NativeReuse::PostingModel::Row {
        ++accesses;
        const auto& row=rows[level-1][id];
        static const std::uint32_t empty=0;
        const auto* begin=row.empty()?&empty:row.data();
        return {begin,begin+row.size()};
    };
    model.BuildOwners();
    auto run=[&](const std::string& mode,CostArbitration::Config config,bool allowed,bool capture,
                 Hooks& h,int budget,bool allowResults=true) {
        clearGraph(); index.GetMutableGraph()[0][0]=d;
        accesses=0;
        h.diagnostics=h.auditVisited=true; h.capture=capture;
        auto signature=[&](int,int){return allowed;};
        auto parentDistance=[&](int,int id){return index.ComputeDistance(data.data(),index.GetSample(id));};
        config.Validate();
        NativeReuse::PostingSupplier supplier(model,h,signature,parentDistance,config);
        auto after=[&](int head,const CostArbitration::Work& work,int width,const COMMON::NativeEdgeConsumer& native) {
            supplier.AfterGraph(head,work,width,native);
        };
        ShortcutFull::mode=mode; h.afterGraph=after;
        Result q(data.data(),2);
        CHECK(index.SearchIndexWithNativeHooks(q,h,[&](int id){return allowResults && (id==d || id==c);},{},budget)==ErrorCode::Success);
        h.afterGraph={};
        return std::make_pair(q.GetResult(0)->VID,q.GetResult(0)->Dist);
    };
    CostArbitration::Config graphCost; graphCost.postingSetup=1e6;
    Hooks graph;
    run("auto",graphCost,true,true,graph,32);
    CHECK(graph.graphDecisions>0 && graph.members==0 && graph.parentDistances==0 && accesses==0);
    CostArbitration::Config postingCost; postingCost.graphSetup=1e6;
    Hooks rejected;
    run("auto",postingCost,false,true,rejected,32);
    CHECK(rejected.signatureRejects>0 && rejected.parentDistances==0 && rejected.members==0 && accesses==0);
    Hooks noEvidence;
    run("auto",postingCost,true,true,noEvidence,32,false);
    CHECK(noEvidence.postingDecisions>0 && noEvidence.members>0 && noEvidence.parentDistances>0);
    CHECK(noEvidence.decisions.front().previousGraph.useful==0 &&
          noEvidence.decisions.front().chosen!=0 && noEvidence.decisions.front().posting.gain>0);
    Hooks wide;
    const auto wideResult=run("auto",postingCost,true,true,wide,8);
    CHECK(wide.h2Rows>0 && wide.parentDistances>=1 && wide.checked>8);
    CHECK(wide.childDistances>0 && wide.queueOffers>0);
    for (const auto& visited:wide.visitedEdges) CHECK(visited.first<count);
    for (const auto& row:wide.rows) CHECK(row.size==row.consumed);
    Hooks noCapture;
    const auto noCaptureResult=run("auto",postingCost,true,false,noCapture,8);
    CHECK(wideResult==noCaptureResult && wide.evaluated.size()>0 && wide.checked==noCapture.checked &&
          wide.members==noCapture.members && wide.parentDistances==noCapture.parentDistances &&
          wide.visitedEdges==noCapture.visitedEdges);

    // H2 128 is a descriptor, never an H1 frontier identifier.
    rows[0][128]=std::move(rows[0][7]);
    model.BuildOwners();
    std::vector<float> representatives(129*8);
    for (int id=0;id<129;++id) for (int j=0;j<8;++j)
        representatives[id*8+j]=(id==128?0.0f:id==0?127.0f:64.0f)*(j+1);
    auto spatialRun=[&](const float* query,bool delayed) {
        clearGraph();
        if (delayed) {
            for (int i=0;i<31;++i) index.GetMutableGraph()[0][i]=i+1;
            for (int i=0;i<31;++i) index.GetMutableGraph()[1][i]=i+32;
        }
        Hooks h; h.capture=h.diagnostics=h.auditVisited=true;
        std::set<std::pair<int,int>> signatures,scored;
        auto signature=[&](int level,int id) {
            CHECK(signatures.insert({level,id}).second);
            return level==1 ? !(delayed && id==7) : delayed && id==0;
        };
        auto spatial=[&](int level,int id) {
            CHECK(signatures.count({level,id}));
            CHECK(scored.insert({level,id}).second);
            return index.ComputeDistance(query,representatives.data()+8*(level==2?0:id));
        };
        CostArbitration::Config cost;
        if (!delayed) cost.graphSetup=1e6;
        cost.Validate();
        NativeReuse::PostingSupplier supplier(model,h,signature,spatial,cost);
        auto after=[&](int head,const CostArbitration::Work& w,int width,const COMMON::NativeEdgeConsumer& native) {
            supplier.AfterGraph(head,w,width,native);
        };
        ShortcutFull::mode="auto"; h.afterGraph=after;
        Result result(query,1);
        CHECK(index.SearchIndexWithNativeHooks(result,h,[](int id){return id==100;},{},2048)==ErrorCode::Success);
        CHECK(result.GetResult(0)->VID==100);
        h.afterGraph={};
        for (const auto& edge:h.visitedEdges) CHECK(edge.first<count);
        for (const auto& row:h.rows) CHECK(row.size==row.consumed);
        return h;
    };
    const auto near=spatialRun(data.data(),false);
    const auto far=spatialRun(data.data()+8*127,false);
    CHECK(near.decisions.front().row==128 && far.decisions.front().row==0);
    CHECK(near.decisions.front().postingSetup==.25 && far.decisions.front().postingSetup==.25);
    rows[0][128].erase(rows[0][128].begin(),rows[0][128].begin()+2);
    rows[0][7]={0,1};
    model.BuildOwners();
    const auto delayed=spatialRun(data.data(),true);
    bool discoveredNow=false,usedLater=false;
    for (const auto& decision:delayed.decisions) {
        if (decision.chosen==2 && decision.realized.members==0 && decision.retainedCount>0)
            discoveredNow=true;
        if (decision.chosen==1 && decision.retainedChosen && decision.row==128) {
            CHECK(discoveredNow && decision.previousGraph.members>0 && decision.source==2);
            const auto& direct=model.owners[0][decision.head];
            CHECK(std::find(direct.begin(),direct.end(),128)==direct.end());
            usedLater=true;
        }
    }
    CHECK(discoveredNow && usedLater);
    std::cout<<"V2 native cold-start posting, near/far nearest-owner 128/0, "
        "H3 discovery -> native graph -> retained H2 128, signature/representative cache passed\n";

    rows[0][7].clear();
    for (int id=0;id<count;++id) rows[0][7].push_back(id);
    rows[0][128].clear();
    model.BuildOwners();
    CHECK(index.DeleteIndex(c)==ErrorCode::Success);
    Hooks deleted;
    const auto deletedResult=run("auto",postingCost,true,true,deleted,32);
    CHECK(deletedResult.first!=c);

    BKT::Index<float> collapsed;
    collapsed.SetParameter("NumberOfThreads","1"); collapsed.SetParameter("BKTLambdaFactor","0");
    collapsed.SetParameter("NeighborhoodSize","32");
    std::vector<float> same(1024*8,1);
    CHECK(collapsed.BuildIndex(same.data(),1024,8)==ErrorCode::Success);
    int collapsedRows=0;
    for(int i=0;i<1024;++i) collapsedRows+=collapsed.GetGraph()[i][31]<-1;
    CHECK(collapsedRows>0);
    for (int budget:{1,2,8,32,2048}) {
        Hooks own; std::set<int> ownIDs;
        own.ownPoint=[&](int id,float){if(id%7==0 && collapsed.ContainSample(id)) ownIDs.insert(id);};
        Result ownResult(same.data(),4);
        CHECK(collapsed.SearchIndexWithNativeHooks(ownResult,own,[](int){return false;},{},budget)==ErrorCode::Success);
        CHECK(ownResult.GetResult(0)->VID<0 && ownIDs.count(0));
        Result aliases(same.data(),4);
        CHECK(collapsed.SearchIndexWithResultFilter(aliases,[](int id){return id%4==1;},budget)==ErrorCode::Success);
        for(int i=0;i<4;++i) CHECK(aliases.GetResult(i)->VID<0 || aliases.GetResult(i)->VID%4==1);
    }
    CostArbitration::Config zero; zero.predicatePrior=zero.competitivePrior=0;
    CHECK(CostArbitration::Predict(zero,{},.5,32,1,1).gain==0);
    for (double invalid:{0.0,-1.0,std::numeric_limits<double>::infinity(),
                         std::numeric_limits<double>::quiet_NaN()}) {
        CostArbitration::Config invalidCost; invalidCost.distance=invalid;
        bool caught=false; try {invalidCost.Validate();} catch(const std::runtime_error&){caught=true;}
        CHECK(caught);
    }
    std::map<std::string,std::string> old{{"shortcutretainedratio","0.5"}};
    bool rejectedOld=false;
    try {ShortcutFull::Configure(old);} catch(const std::runtime_error&){rejectedOld=true;}
    CHECK(rejectedOld);
    ShortcutFull::mode="ordinary"; ShortcutFull::capture=false;
    Result original(data.data(),2),unchangedDefault(data.data(),2);
    CHECK(index.SearchIndex(original)==ErrorCode::Success);
    std::vector<std::shared_ptr<VectorSet>> noCatalogs;
    std::vector<int> noPostings;
    CHECK(NativeReuse::Search(&index,&unchangedDefault,nullptr,noCatalogs,noPostings,
        [](int){CHECK(false);return false;},[](int,float){CHECK(false);return false;},
        [](int,int){CHECK(false);return false;},false)==ErrorCode::Success);
    for(int i=0;i<2;++i)
        CHECK(original.GetResult(i)->VID==unchangedDefault.GetResult(i)->VID &&
              original.GetResult(i)->Dist==unchangedDefault.GetResult(i)->Dist);
    std::cout<<"Native bridge A->B->C, auxiliary result-rejection non-poisoning, graph-only/signed rejection, "
        "cost-selected complete-row same-frontier budget, diagnostics parity, aliases/ties/deletes/own-only, "
        "opposing costs, zero utility and rejected old/invalid costs passed\n";
}
