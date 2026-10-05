// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#include "inc/Core/SPANN/PostingNavigation.h"
#include <iostream>
#include <numeric>
#include <set>

using namespace SPTAG;
using RowResult = COMMON::PostingNavigation::RowResult;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("Collection test line " + std::to_string(__LINE__)); } while (false)

struct Layer {
    std::vector<std::vector<std::uint32_t>> rows;
    std::vector<bool> allowed;
    std::vector<float> distances;
    mutable std::vector<int> reads;
    const std::uint32_t* Begin(int id) const {
        CHECK(allowed.at(id)); ++reads.at(id); return rows.at(id).data();
    }
    const std::uint32_t* End(int id) const {
        CHECK(allowed.at(id)); const auto& row = rows.at(id);
        return row.empty() ? row.data() : row.data() + row.size();
    }
};
struct Owners {
    std::vector<int> counts;
    std::vector<std::vector<std::vector<int>>> parents;
    std::size_t Levels() const { return counts.size(); }
    int Count(std::size_t level) const { return counts.at(level); }
    SPANN::PostingOwners::Range Parents(std::size_t level, int head) const {
        const auto& ids = parents.at(level).at(head);
        return {ids.data(), ids.data() + ids.size()};
    }
};
struct Fixture {
    std::vector<Layer> layers;
    Owners owners;
    std::set<std::uint32_t> visited, rejected;
    int fresh = 0, members = 0, consumed = 0;
    bool budget = true;
    explicit Fixture(std::vector<int> counts) {
        owners.counts = counts;
        for (std::size_t level = 0; level < counts.size(); ++level) {
            Layer layer;
            layer.rows.resize(counts[level]); layer.allowed.resize(counts[level], true);
            layer.distances.resize(counts[level]); layer.reads.resize(counts[level]);
            std::iota(layer.distances.begin(),layer.distances.end(),0);
            layers.push_back(std::move(layer));
            owners.parents.emplace_back(level == 0 ? 4096 : counts[level-1], std::vector<int>{0});
        }
    }
    void Row(std::size_t level, int id, int start, int size) {
        auto& row = layers[level].rows[id]; row.resize(size);
        std::iota(row.begin(),row.end(),start);
    }
    auto Signature() { return [this](std::size_t level,int id) { return layers[level].allowed[id]; }; }
    auto Distance() {
        return [this](std::size_t level,int id) {
            CHECK(layers[level].allowed[id]); return layers[level].distances[id];
        };
    }
    auto Consumer(unsigned workLimit=10) {
        return [this,limit=fresh+workLimit](const std::uint32_t* ids,int count) {
            RowResult row; ++consumed;
            for (int i=0;i<count;++i) {
                ++row.degree; ++members;
                if (rejected.count(ids[i])) continue;
                ++row.eligible;
                if (visited.insert(ids[i]).second) { ++row.newCandidates; ++fresh; }
            }
            row.canContinue=budget && fresh<limit;
            row.targetFilled=fresh>=limit;
            return row;
        };
    }
    void Run(unsigned target=10) {
        auto signature=Signature(); auto distance=Distance();
        SPANN::HierarchyPostingQuery<decltype(layers),decltype(signature),decltype(distance),Owners>
            query(owners,layers,signature,distance);
        query.SetSearchCapacity(64);
        query.Expand(0,Consumer(target));
    }
};

void BoundaryAndOrdering() {
    CHECK((RowResult{200,1,true,0}.Sparse()));
    CHECK((!RowResult{100,1,true,0}.Sparse()));
    for (int count : {9,10}) {
        Fixture f({3,1});
        f.Row(0,0,0,count); f.Row(0,1,100,4); f.Row(0,2,20,12);
        f.layers[1].rows[0]={1,2};
        f.layers[0].distances[1]=100;
        f.layers[0].distances[2]=1;
        f.Run();
        CHECK(f.layers[1].reads[0]==(count==9 ? 1 : 0));
        CHECK(f.layers[0].reads[1]==0);
        CHECK(f.fresh==(count==9 ? 21 : 10));
        CHECK(f.members==(count==9 ? 21 : 10));
    }
    Fixture lowFraction({2,1});
    lowFraction.Row(0,0,0,2000);
    for (unsigned id=10;id<2000;++id) lowFraction.rejected.insert(id);
    lowFraction.Run();
    CHECK(lowFraction.fresh==10 && lowFraction.members==2000 && lowFraction.layers[1].reads[0]==0);
    Fixture custom({2,1});
    custom.Row(0,0,0,4); custom.Row(0,1,4,1); custom.layers[1].rows[0]={1};
    custom.Run(5); CHECK(custom.fresh==5);
    std::cout<<"PASS explicit consumer work limit, full-row overshoot and representative order independent of CSR order\n";
}
void UniqueH1CountsAndDepth() {
    Fixture f({4,1});
    f.layers[0].rows={{0,1,2,3},{2,3,4,5,6},{6,7,8},{8,9}};
    f.layers[1].rows[0]={1,2,3}; f.Run();
    CHECK(f.fresh==10 && f.consumed==4 && f.members==14);
    Fixture deep({12,2,1});
    deep.visited.insert(0);
    for(int id=0;id<11;++id) deep.layers[0].rows[id]={0};
    deep.Row(0,11,1,10);
    deep.Row(1,0,1,10); deep.layers[1].rows[1]={11};
    deep.layers[2].rows[0]={0,1};
    deep.owners.parents[1][11]={1};
    deep.Run();
    CHECK(deep.layers[2].reads[0]==1 && deep.layers[1].reads[1]==1);
    CHECK(deep.fresh==10 && deep.consumed==12);
    std::cout<<"PASS duplicate/visited matches excluded; ten H2 representatives do not fake ten fresh H1; arbitrary-depth collection\n";
}
void BudgetCacheAndPruning() {
    Fixture f({3,1});
    f.Row(0,0,0,9); f.Row(0,1,9,1); f.Row(0,2,10,9);
    f.layers[1].rows[0]={1,2}; f.budget=false;
    auto signature=f.Signature(); auto distance=f.Distance();
    SPANN::HierarchyPostingQuery<decltype(f.layers),decltype(signature),decltype(distance),Owners>
        query(f.owners,f.layers,signature,distance);
    query.SetSearchCapacity(64);
    query.Expand(0,f.Consumer());
    CHECK(f.members==9 && f.fresh==9 && f.layers[1].reads[0]==0);
    f.budget=true; query.Expand(0,f.Consumer());
    CHECK(f.fresh==19 && f.layers[0].reads[2]==1);
    query.Expand(0,f.Consumer());
    CHECK(f.fresh==19 && f.consumed==3);
    Fixture pruned({2,2,1});
    pruned.layers[0].allowed[0]=false; pruned.layers[1].allowed[0]=false;
    pruned.layers[2].rows[0]={0,1}; pruned.layers[1].rows[1]={1};
    pruned.Row(0,1,0,3); pruned.Run();
    CHECK(pruned.fresh==3 && pruned.layers[0].reads[0]==0 && pruned.layers[1].reads[0]==0);
    std::cout<<"PASS full-row budget underfill; per-activation reset; cached rows add zero; rejected rows never accessed; exhaustion underfills\n";
}
void PendingChildrenAfterCompleteUpperRow() {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    COMMON::GraphAccessStats stats;
    COMMON::ScopedGraphAccessStats scope(&stats);
#endif
    Fixture f({4,1});
    f.Row(0,0,0,4); f.Row(0,1,100,11); f.Row(0,2,20,12);
    f.layers[1].rows[0]={1,3,2};
    f.layers[0].allowed[3]=false;
    f.layers[0].distances[1]=100;
    f.layers[0].distances[2]=1;
    std::vector<int> checked, scored;
    auto signature=[&](std::size_t level,int id) {
        if (level==0) checked.push_back(id);
        return f.layers[level].allowed[id];
    };
    auto distance=[&](std::size_t level,int id) {
        CHECK(f.layers[level].allowed[id]);
        if (level==0) scored.push_back(id);
        return f.layers[level].distances[id];
    };
    SPANN::HierarchyPostingQuery<decltype(f.layers),decltype(signature),decltype(distance),Owners>
        query(f.owners,f.layers,signature,distance);
    query.SetSearchCapacity(64);
    auto consume=f.Consumer();
    query.Expand(0,[&](const std::uint32_t* ids,int count) {
        if (f.consumed==1) {
            CHECK((checked==std::vector<int>{0,1,3,2}));
            CHECK((scored==std::vector<int>{0,1,2}));
            CHECK(ids[0]==20);
        }
        return consume(ids,count);
    });
    CHECK(f.fresh==16 && f.members==16 && f.visited.count(31)==1);
    CHECK(f.layers[0].reads[1]==0 && f.layers[0].reads[2]==1);
    CHECK(f.layers[0].reads[3]==0 && f.layers[1].reads[0]==1);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    CHECK(stats.m_h2Postings.candidateConsiderations==4 && stats.m_h2Postings.uniqueStates==4);
    CHECK(stats.m_h2Postings.completedRows==2 && stats.m_upperPostings.completedRows==1);
#endif
    query.Expand(0,consume);
    CHECK(f.fresh==27 && f.members==27 && f.visited.count(110)==1);
    CHECK(f.layers[0].reads[1]==1 && f.layers[0].reads[2]==1);
    CHECK(f.layers[1].reads[0]==1);
    query.Expand(0,consume);
    CHECK(f.fresh==27 && f.consumed==3 && f.layers[1].reads[0]==1);
    query.Expand(1,consume);
    CHECK(f.fresh==27 && f.consumed==3 && f.layers[1].reads[0]==1);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    CHECK(stats.m_postingActivations==4);
    CHECK(stats.m_h2Postings.ownerReferences==4 && stats.m_h2Postings.memberReferences==3);
    CHECK(stats.m_h2Postings.candidateConsiderations==7 && stats.m_h2Postings.uniqueStates==4);
    CHECK(stats.m_upperPostings.ownerReferences==3 && stats.m_upperPostings.memberReferences==0);
    CHECK(stats.m_upperPostings.candidateConsiderations==3 && stats.m_upperPostings.uniqueStates==1);
    CHECK(stats.m_h2Postings.expandAttempts==3 && stats.m_h2Postings.expandedSkips==0);
    CHECK(stats.m_upperPostings.expandAttempts==1 && stats.m_upperPostings.expandedSkips==0);
    CHECK(stats.m_h2Postings.completedRows==3 && stats.m_upperPostings.completedRows==1);
    CHECK(stats.m_h2Postings.representativeDistances==3 && stats.m_upperPostings.representativeDistances==1);
    CHECK(stats.m_postingStates==5 && stats.m_upperDistances==4);
#endif
    std::cout<<"PASS complete H3 enumeration before descent; last member nearest; whole-child overshoot; pending children survive parent expansion across activations\n";
}
void RejectedReferenceCounts() {
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    COMMON::GraphAccessStats stats;
    COMMON::ScopedGraphAccessStats scope(&stats);
    Fixture f({2,2,1});
    f.layers[0].allowed[0]=false; f.layers[1].allowed[0]=false;
    f.layers[2].rows[0]={0,1}; f.layers[1].rows[1]={1};
    f.Row(0,1,0,3); f.Run();
    CHECK(stats.m_h2Postings.candidateConsiderations==2 && stats.m_h2Postings.uniqueStates==2);
    CHECK(stats.m_upperPostings.candidateConsiderations==6 && stats.m_upperPostings.uniqueStates==3);
    CHECK(stats.m_h2Postings.completedRows==1 && stats.m_upperPostings.completedRows==2);
    CHECK(stats.m_h2Postings.expandAttempts==1 && stats.m_upperPostings.expandAttempts==2);
    CHECK(stats.m_h2Postings.memberReferences==1 && stats.m_upperPostings.memberReferences==2);
    CHECK(stats.m_h2Postings.representativeDistances==1 && stats.m_upperPostings.representativeDistances==2);
    CHECK(f.layers[0].reads[0]==0 && f.layers[1].reads[0]==0);
    CHECK(stats.m_selectedPostingRows==1 && stats.m_postingStates==5);
    CHECK(stats.m_ownerReferences==stats.m_h2Postings.ownerReferences+stats.m_upperPostings.ownerReferences);
    std::cout<<"PASS rejected/unexpanded candidates counted; H3+ includes H4; unique states and completed rows agree with fixture reads\n";
#endif
}
void RejectedDescentDoesNotFanOut() {
    constexpr int rejectedChildren=256, replicas=8;
    Fixture f({rejectedChildren+2,1+rejectedChildren*(replicas-1)});
    for(int id=0;id<=rejectedChildren;++id) f.layers[0].allowed[id]=false;
    f.Row(1,0,0,rejectedChildren+2);
    f.layers[0].rows.back()={42};
    for(int id=1;id<=rejectedChildren;++id) {
        auto& owners=f.owners.parents[1][id];
        for(int replica=1;replica<replicas;++replica) {
            const int parent=(id-1)*(replicas-1)+replica;
            owners.push_back(parent);
            f.layers[1].rows[parent]={static_cast<unsigned>(id)};
            f.layers[1].distances[parent]=100000.0f;
        }
    }
    std::vector<int> signatures(2),distances(2);
    auto signature=[&](std::size_t level,int id) {
        ++signatures[level];return f.layers[level].allowed[id];
    };
    auto distance=[&](std::size_t level,int id) {
        CHECK(f.layers[level].allowed[id]);
        ++distances[level];return f.layers[level].distances[id];
    };
    SPANN::HierarchyPostingQuery<decltype(f.layers),decltype(signature),decltype(distance),Owners>
        query(f.owners,f.layers,signature,distance);
    query.SetSearchCapacity(64);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    COMMON::GraphAccessStats stats;COMMON::ScopedGraphAccessStats scope(&stats);
#endif
    query.Expand(0,f.Consumer(1));
    CHECK(f.fresh==1 && f.visited.count(42)==1 && f.layers[1].reads[0]==1);
    CHECK((signatures==std::vector<int>{rejectedChildren+2,1}));
    CHECK((distances==std::vector<int>{1,1}));
    for(int id=0;id<=rejectedChildren;++id) CHECK(f.layers[0].reads[id]==0);
    for(std::size_t id=1;id<f.layers[1].rows.size();++id) CHECK(f.layers[1].reads[id]==0);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    CHECK(stats.m_ownerReferences==3 && stats.m_postingStates==rejectedChildren+3);
    CHECK(stats.m_upperPostings.uniqueStates==1 && stats.m_upperPostings.representativeDistances==1);
#endif
    std::cout<<"PASS rejected anchor ascends; a complete upper row rejects 256 children without discovering their 1792 other owners\n";
}
void CachedRejectionCanAscend() {
    Fixture f({3,2});
    f.owners.parents[0][1]={1};
    f.owners.parents[1][1]={0,1};f.owners.parents[1][2]={1};
    f.layers[0].allowed[1]=false;
    f.layers[1].rows={{0,1},{1,2}};
    f.layers[0].rows[2]={42};
    int rejectedChecks=0;
    auto signature=[&](std::size_t level,int id) {
        rejectedChecks+=level==0 && id==1;return f.layers[level].allowed[id];
    };
    auto distance=f.Distance();
    SPANN::HierarchyPostingQuery<decltype(f.layers),decltype(signature),decltype(distance),Owners>
        query(f.owners,f.layers,signature,distance);
    query.SetSearchCapacity(64);
    query.Expand(0,f.Consumer());
    CHECK(f.fresh==0 && f.layers[1].reads[1]==0 && rejectedChecks==1);
    query.Expand(1,f.Consumer());
    CHECK(f.fresh==1 && f.visited.count(42)==1 && rejectedChecks==1);
    CHECK(f.layers[0].reads[1]==0 && f.layers[1].reads[1]==1);
    query.Expand(1,f.Consumer());
    CHECK(f.fresh==1 && rejectedChecks==1 && f.layers[1].reads[1]==1);

    Fixture promoted({3,3,2});
    promoted.owners.parents[0][0]={0,1};
    promoted.owners.parents[1]={{0},{1},{2}};
    promoted.owners.parents[2]={{0},{0,1},{1}};
    promoted.layers[0].distances={1,5,8};
    promoted.layers[1].distances={2,4,7};
    promoted.layers[2].distances={3,6};
    promoted.layers[1].allowed[1]=false;
    promoted.layers[0].rows[2]={84};
    promoted.layers[1].rows={{0},{1},{2}};
    promoted.layers[2].rows={{0,1},{1,2}};
    int promotedChecks=0;
    auto promotedSignature=[&](std::size_t level,int id) {
        promotedChecks+=level==1 && id==1;return promoted.layers[level].allowed[id];
    };
    auto promotedDistance=promoted.Distance();
    SPANN::HierarchyPostingQuery<decltype(promoted.layers),decltype(promotedSignature),decltype(promotedDistance),Owners>
        promotedQuery(promoted.owners,promoted.layers,promotedSignature,promotedDistance);
    promotedQuery.SetSearchCapacity(64);
    promotedQuery.Expand(0,promoted.Consumer(1));
    CHECK(promoted.fresh==1 && promoted.visited.count(84)==1 && promotedChecks==1);
    CHECK(promoted.layers[1].reads[1]==0 && promoted.layers[2].reads[1]==1);
    std::cout<<"PASS a cached rejected child still ascends once when later selected as an entry or explicitly promoted owner\n";
}
void SharedAnchorOwners() {
    Fixture f({2});
    f.owners.parents[0][0]={0,1};
    f.owners.parents[0][1]={1,0};
    f.Row(0,0,0,10); f.Row(0,1,20,10);
    f.layers[0].distances={100,1};
    auto signature=f.Signature(); auto distance=f.Distance();
    SPANN::HierarchyPostingQuery<decltype(f.layers),decltype(signature),decltype(distance),Owners>
        query(f.owners,f.layers,signature,distance);
    query.SetSearchCapacity(64);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    COMMON::GraphAccessStats stats; COMMON::ScopedGraphAccessStats scope(&stats);
#endif
    query.Expand(std::vector<int>{0,1},f.Consumer());
    CHECK(f.layers[0].reads[0]==0 && f.layers[0].reads[1]==1);
    CHECK(f.fresh==10 && f.visited.count(29));
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    CHECK(stats.m_postingActivations==1 && stats.m_h2Postings.ownerReferences==4);
    CHECK(stats.m_h2Postings.candidateConsiderations==2 && stats.m_h2Postings.uniqueStates==2);
#endif
    std::cout<<"PASS one phase merges shared anchor owners; query-representative ranking; full selected row\n";
}
void NProbeAnchorOwners() {
    for(int probe : {48,96}) {
        Fixture f({probe+1});
        std::vector<int> anchors(probe), checks(probe+1), scores(probe+1);
        std::iota(anchors.begin(),anchors.end(),0);
        for(int head=0;head<probe;++head) f.owners.parents[0][head]={head,probe};
        for(int id=0;id<=probe;++id) {
            f.layers[0].rows[id]={static_cast<std::uint32_t>(id)};
            f.layers[0].distances[id]=id==probe ? 1000.0f : static_cast<float>(probe-id);
        }
        auto signature=[&](std::size_t,int id) { ++checks[id]; return true; };
        auto distance=[&](std::size_t,int id) { ++scores[id]; return f.layers[0].distances[id]; };
        SPANN::HierarchyPostingQuery<decltype(f.layers),decltype(signature),decltype(distance),Owners>
            query(f.owners,f.layers,signature,distance);
        query.SetSearchCapacity(probe+1);
        std::vector<unsigned> consumed;
        query.Expand(anchors,[&](const std::uint32_t* ids,int count) {
            CHECK(std::all_of(checks.begin(),checks.end(),[](int n) { return n==1; }));
            CHECK(std::all_of(scores.begin(),scores.end(),[](int n) { return n==1; }));
            CHECK(count==1);
            consumed.push_back(ids[0]);
            return f.Consumer(2-f.fresh)(ids,count);
        });
        CHECK((consumed==std::vector<unsigned>{static_cast<unsigned>(probe-1),
                                               static_cast<unsigned>(probe-2)}));
        CHECK(f.fresh==2 && f.layers[0].reads[probe]==0);
        for(int id=0;id<probe;++id) CHECK(f.layers[0].reads[id]==(id>=probe-2 ? 1 : 0));
    }
    std::cout<<"PASS every nprobe48/96 anchor contributes owners before shared distance ranking; duplicate owners scored once; best rows can come after anchor8\n";
}
void ChildPrefilterPreservesPromotion() {
    for(bool early : {false,true}) {
        Fixture f({3,2});
        f.layers[0].allowed[2]=false;
        f.layers[0].rows[1]={42};
        f.layers[1].rows={{0,2},{2,1}};
        f.owners.parents[0][0]={0}; f.owners.parents[0][1]={2};
        f.owners.parents[1][0]={0}; f.owners.parents[1][1]={1}; f.owners.parents[1][2]={0,1};
        int rejectedSignatures=0;
        const auto signature=[&](std::size_t level,int id) {
            if(level==0 && id==2) ++rejectedSignatures;
            return f.layers[level].allowed[id];
        };
        auto distance=f.Distance();
        const auto childMayMatch=[&](std::size_t level,int id) {
            return !early || f.layers[level].allowed[id];
        };
        SPANN::HierarchyPostingQuery<decltype(f.layers),decltype(signature),decltype(distance),
            Owners,SPANN::NoPostingPrefetch,SPANN::AdjacentPostingLayout,decltype(childMayMatch)>
            query(f.owners,f.layers,signature,distance,{},0,childMayMatch);
        query.SetSearchCapacity(64);
        query.Expand(0,f.Consumer(1));
        CHECK(f.fresh==0 && f.layers[1].reads[1]==0 && rejectedSignatures==(early?0:1));
        query.Expand(1,f.Consumer(1));
        CHECK(f.fresh==1 && f.visited.count(42)==1 && rejectedSignatures==1);
        CHECK(f.layers[0].reads[2]==0 && f.layers[1].reads[1]==1);
    }
    Fixture invalid({1,1});
    invalid.layers[1].rows[0]={99};
    auto signature=invalid.Signature(); auto distance=invalid.Distance();
    const auto rejectChild=[](std::size_t,int) { return false; };
    SPANN::HierarchyPostingQuery<decltype(invalid.layers),decltype(signature),decltype(distance),
        Owners,SPANN::NoPostingPrefetch,SPANN::AdjacentPostingLayout,decltype(rejectChild)>
        query(invalid.owners,invalid.layers,signature,distance,{},0,rejectChild);
    query.SetSearchCapacity(64);
    bool rejected=false;
    try { query.Expand(0,invalid.Consumer()); } catch(const std::out_of_range&) { rejected=true; }
    CHECK(rejected);
    std::cout<<"PASS exact child prefilter avoids rejection state but retains later owner promotion and invalid-ID errors\n";
}
void CompetingAncestorsAndLevels() {
    for(bool competingLower:{false,true}) {
        Fixture f({4,2});
        f.owners.parents[0][0]={0,1};
        f.owners.parents[1][0]={0};f.owners.parents[1][1]={1};
        f.owners.parents[1][2]={0};f.owners.parents[1][3]={1};
        f.layers[0].distances={1,2,competingLower?100.0f:3.0f,competingLower?5.0f:100.0f};
        f.layers[1].distances={competingLower?3.0f:10.0f,competingLower?4.0f:20.0f};
        f.layers[1].rows={{2},{3}};
        f.layers[0].rows[2]={10};f.layers[0].rows[3]={20};
        f.Run(1);
        CHECK(f.layers[0].reads[0]==1 && f.layers[0].reads[1]==1);
        CHECK(f.layers[1].reads[0]==1 && f.layers[1].reads[1]==(competingLower?1:0));
        CHECK(f.layers[0].reads[2]==(competingLower?0:1));
        CHECK(f.layers[0].reads[3]==(competingLower?1:0));
        CHECK(f.visited.count(competingLower?20:10)==1);
    }
    Fixture filled({2});
    filled.owners.parents[0][0]={0,1};
    filled.layers[0].rows={{100},{1}};
    auto signature=filled.Signature();auto distance=filled.Distance();
    SPANN::HierarchyPostingQuery<decltype(filled.layers),decltype(signature),decltype(distance),Owners>
        query(filled.owners,filled.layers,signature,distance);
    query.SetSearchCapacity(64);
    std::vector<std::uint32_t> members;
    query.Expand(0,[&](const std::uint32_t* ids,int count) {
        members.insert(members.end(),ids,ids+count);
        return RowResult{static_cast<unsigned>(count),static_cast<unsigned>(count),true,
                         static_cast<unsigned>(count),true};
    });
    CHECK((members==std::vector<std::uint32_t>{100,1}));
    CHECK(filled.layers[0].reads[0]==1 && filled.layers[0].reads[1]==1);
    std::cout<<"PASS near ancestors compete before the last owner's branch; upper/lower rows share one queue; a full result heap does not stop exploration\n";
}
void AdaptiveFrontierConvergence() {
    for(bool filled:{false,true}) {
        Fixture f({5});
        f.owners.parents[0][0]={0,1,2,3,4};
        for(int id=0;id<5;++id) {
            f.layers[0].distances[id]=float(id+1);
            f.layers[0].rows[id]={static_cast<unsigned>(id)};
        }
        auto signature=f.Signature();auto distance=f.Distance();
        SPANN::HierarchyPostingQuery<decltype(f.layers),decltype(signature),decltype(distance),Owners>
            query(f.owners,f.layers,signature,distance);
        bool rejected=false;
        try {query.SetSearchCapacity(0);} catch(const std::invalid_argument&) {rejected=true;}
        CHECK(rejected);
        query.SetSearchCapacity(2);
        std::vector<unsigned> consumed;
        query.Expand(0,[&](const std::uint32_t* ids,int count) {
            CHECK(count==1);consumed.push_back(ids[0]);
            return RowResult{1,0,true,0,filled};
        });
        CHECK(query.Converged() && (consumed==std::vector<unsigned>{0,1}));
        for(int id=0;id<5;++id) CHECK(f.layers[0].reads[id]==(id<2?1:0));
        rejected=false;
        try {query.SetSearchCapacity(3);} catch(const std::logic_error&) {rejected=true;}
        CHECK(rejected);
    }
    Fixture f({4,1});
    f.owners.parents[0][0]={0,1};
    f.layers[0].distances={1,2,.75f,100};
    f.layers[1].distances[0]=.5f;
    f.layers[1].rows[0]={2,3};
    for(int id=0;id<4;++id) f.layers[0].rows[id]={static_cast<unsigned>(id)};
    auto signature=f.Signature();auto distance=f.Distance();
    SPANN::HierarchyPostingQuery<decltype(f.layers),decltype(signature),decltype(distance),Owners>
        query(f.owners,f.layers,signature,distance);
    query.SetSearchCapacity(3);
    std::vector<unsigned> consumed;
    query.Expand(0,[&](const std::uint32_t* ids,int count) {
        CHECK(count==1);consumed.push_back(ids[0]);
        return RowResult{1,1,true,1,true};
    });
    CHECK(query.Converged() && (consumed==std::vector<unsigned>{0,2,1}));
    CHECK(f.layers[1].reads[0]==1 && f.layers[0].reads[1]==1 && f.layers[0].reads[3]==0);
    Fixture coarse({2,2});
    coarse.layers[0].allowed[0]=false;
    coarse.layers[0].distances[1]=10;
    coarse.layers[0].rows={{0},{42}};
    coarse.owners.parents[1][0]={0,1};coarse.owners.parents[1][1]={0,1};
    coarse.layers[1].distances={.25f,.5f};
    coarse.layers[1].rows={{0,1},{0,1}};
    auto coarseSignature=coarse.Signature();auto coarseDistance=coarse.Distance();
    SPANN::HierarchyPostingQuery<decltype(coarse.layers),decltype(coarseSignature),decltype(coarseDistance),Owners>
        coarseQuery(coarse.owners,coarse.layers,coarseSignature,coarseDistance);
    coarseQuery.SetSearchCapacity(2);
    coarseQuery.Expand(0,coarse.Consumer());
    CHECK(coarse.fresh==1 && coarse.visited.count(42) && !coarseQuery.Converged());
    CHECK(coarse.layers[0].reads[0]==0 && coarse.layers[0].reads[1]==1);
    std::cout<<"PASS full/underfilled native frontier convergence; closer children update the H2 pool; coarse ancestors cannot occupy H2 beam slots; invalid capacity is rejected\n";
}
void PrefetchPreservesOrder() {
    const auto run = [](auto prefetch, bool invalid) {
        Fixture f({257,2,1});
        for (int id=0;id<257;++id) {
            f.layers[0].allowed[id]=id%3!=0;
            f.layers[0].rows[id]={static_cast<std::uint32_t>(id)};
            f.owners.parents[1][id]={id<128 ? 0 : 1};
        }
        f.Row(1,0,0,128); f.Row(1,1,128,129);
        f.layers[2].rows[0]={0,1};
        if (invalid) f.layers[1].rows[1].push_back((std::numeric_limits<std::uint32_t>::max)());
        std::vector<std::tuple<char,std::size_t,std::uint32_t>> events;
        const auto signature = [&](std::size_t level,int id) {
            events.emplace_back('s',level,id);
            return f.layers[level].allowed[id];
        };
        const auto distance = [&](std::size_t level,int id) {
            events.emplace_back('d',level,id);
            CHECK(f.layers[level].allowed[id]);
            return f.layers[level].distances[id];
        };
        const auto consume = [&](const std::uint32_t* members,int count) {
            for(int i=0;i<count;++i) events.emplace_back('c',0,members[i]);
            return f.Consumer(10000)(members,count);
        };
        SPANN::HierarchyPostingQuery<decltype(f.layers),decltype(signature),decltype(distance),
            Owners,decltype(prefetch)> query(f.owners,f.layers,signature,distance,prefetch);
        query.SetSearchCapacity(512);
        bool rejected=false;
        try { query.Expand(0,consume); }
        catch(const std::out_of_range&) { rejected=true; }
        CHECK(rejected==invalid);
        return std::make_tuple(events,f.visited,f.fresh,f.members,f.consumed);
    };
    for(bool invalid : {false,true}) {
        std::set<std::pair<std::size_t,std::uint32_t>> hints;
        const auto prefetch = [&](std::size_t level,std::uint32_t id) {
            const int counts[]={4096,257,2,1};
            CHECK(level<4 && id<static_cast<std::uint32_t>(counts[level]));
            hints.emplace(level,id);
        };
        const auto expected=run(SPANN::NoPostingPrefetch{},invalid);
        CHECK(run(prefetch,invalid)==expected);
        CHECK(hints.count({1,3}) && hints.count({2,1}));
        if(!invalid) CHECK(hints.count({0,1}));
    }
    std::cout<<"PASS metadata prefetch preserves signature/distance/member order and malformed-ID exceptions across state rehashes\n";
}
void NavigationDistanceCutoff() {
    for (int capacity : {64,192,384}) for (int width : {0,1,2,8}) for (bool ties : {false,true}) {
        Fixture f({6,3,1});
        f.layers[0].allowed[0]=false;
        f.owners.parents[1][0]={0,1,2};
        f.layers[1].distances={1,ties?1.0f:5.0f,50};
        f.layers[1].rows={{1,2},{3,4},{5}};
        f.layers[2].distances={100};
        f.layers[2].rows={{0,1,2}};
        for (int id=1;id<6;++id) {
            f.layers[0].rows[id]={static_cast<unsigned>(id)};
            f.layers[0].distances[id]=float(20+id);
            f.owners.parents[1][id]={(id-1)/2};
        }
        std::set<std::pair<std::size_t,int>> checked;
        const auto signature=[&](std::size_t level,int id) {
            CHECK(checked.emplace(level,id).second);
            return f.layers[level].allowed[id];
        };
        auto distance=f.Distance();
        using Query=SPANN::HierarchyPostingQuery<decltype(f.layers),decltype(signature),decltype(distance),Owners>;
        for (int invalid : {-1,MaxSize}) {
            bool rejected=false;
            try { Query bad(f.owners,f.layers,signature,distance,{},invalid); }
            catch (const std::invalid_argument&) { rejected=true; }
            CHECK(rejected);
        }
        Query query(f.owners,f.layers,signature,distance,{},width);
        query.SetSearchCapacity(capacity);
        COMMON::GraphAccessStats stats;
        {
            COMMON::ScopedGraphAccessStats capture(&stats);
            query.Expand({0,0},f.Consumer(1000));
        }
        const int kept=width==0 || width>=3 ? 3 : ties ? 2 : width;
        CHECK(f.layers[2].reads[0]==1);
        CHECK(query.Converged()==(kept<3));
        for (int parent=0;parent<3;++parent) {
            CHECK(f.layers[1].reads[parent]==(parent<kept?1:0));
            for (auto child : f.layers[1].rows[parent]) {
                CHECK(f.layers[0].reads[child]==(parent<kept?1:0));
                CHECK(checked.count({0,child})==(parent<kept?1U:0U));
            }
        }
        CHECK(f.fresh==(kept==3?5:kept*2));
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
        CHECK(stats.m_navigationDistancePrunes==static_cast<unsigned>(3-kept));
#endif
    }
    std::cout<<"PASS posting-scale per-tier cutoff before CSR/signature descent, nprobe independence, ties, zero-match ascent, dedup and disabled parity\n";
}
int main() {
    try { BoundaryAndOrdering(); UniqueH1CountsAndDepth(); BudgetCacheAndPruning(); PendingChildrenAfterCompleteUpperRow(); RejectedReferenceCounts(); RejectedDescentDoesNotFanOut(); CachedRejectionCanAscend(); SharedAnchorOwners(); NProbeAnchorOwners(); ChildPrefilterPreservesPromotion(); CompetingAncestorsAndLevels(); AdaptiveFrontierConvergence(); PrefetchPreservesOrder(); NavigationDistanceCutoff(); }
    catch(const std::exception& error) { std::cerr<<error.what()<<'\n'; return 1; }
    return 0;
}
