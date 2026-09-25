#include "inc/Core/SPANN/PostingNavigation.h"
#include "inc/Core/BKT/Index.h"
#include "inc/Core/SPANN/HeadNodeMetadata.h"
#include "inc/Core/SPANN/RoutingSignatures.h"
#include <iostream>
#include <map>
#include <set>
#include <atomic>
#include <cstdlib>
#include <fstream>
#include <numeric>

using namespace SPTAG;
#define CHECK(x) do {if(!(x)) throw std::runtime_error("Native test line "+std::to_string(__LINE__));} while(false)
static std::atomic<std::size_t> allocations{0};
static std::atomic<std::size_t> allocationBytes{0};
void* operator new(std::size_t size) {
    ++allocations;
    allocationBytes += size;
    if(auto* p=std::malloc(size?size:1)) return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept {std::free(p);}
void operator delete(void* p,std::size_t) noexcept {std::free(p);}
void* operator new[](std::size_t size) {return ::operator new(size);}
void operator delete[](void* p) noexcept {std::free(p);}
void operator delete[](void* p,std::size_t) noexcept {std::free(p);}

void StorageTests() {
    CHECK(sizeof(COMMON::NavigationVisited)==sizeof(COMMON::OptHashPosVector));
    COMMON::NavigationVisited table;table.Init(8,0);
    auto max=[](int){return true;};
    CHECK(!table.Match(MaxSize-1,max).first);
    std::map<int,int> calls;
    auto predicate=[&](int id){++calls[id];return id%3==0;};
    for(int id=0;id<20000;++id) CHECK(table.Match(id,predicate)==std::make_pair(false,id%3==0));
    for(int id=0;id<20000;++id) CHECK(table.Match(id,predicate)==std::make_pair(true,id%3==0));
    for(int id=0;id<20000;++id) CHECK(table.ContainsMatch(id));
    CHECK(!table.ContainsMatch(20001));
    for(auto c:calls) CHECK(c.second==1);
    CHECK(table.Match(MaxSize-1,max)==std::make_pair(true,true));
    bool rejected=false;
    try {table.Match(MaxSize,max);} catch(const std::out_of_range&) {rejected=true;}
    CHECK(rejected);
    rejected=false;
    try {table.Match(-1,max);} catch(const std::out_of_range&) {rejected=true;}
    CHECK(rejected);
    table.clear();
    CHECK(!table.Match(42,[](int){return false;}).first);
    CHECK(table.Match(42,max)==std::make_pair(true,false));
    table.clear();
    const auto pointer=table.Storage();
    const auto bytes=table.StorageBytes();
    const auto before=allocations.load();
    for(int q=0;q<128;++q) {
        table.clear();
        for(int id=0;id<1000;++id) {
            if(q%2) CHECK(!table.Match(id,max).first);
            else CHECK(!table.CheckAndSet(id));
        }
        CHECK(table.Storage()==pointer && table.StorageBytes()==bytes);
    }
    CHECK(allocations.load()==before);
    COMMON::OptHashPosVector generic;generic.Init(32,1);
    CHECK(!generic.CheckAndSet(MaxSize) && generic.CheckAndSet(MaxSize) && generic.Contains(MaxSize));
    std::cout<<"PASS 4-byte native domain, generic maximum, collisions/rehash/first-once, cached negative visit, 128 mixed queries zero allocations\n";
}

struct OneRow : COMMON::PostingNavigation {
    std::vector<std::uint32_t> ids;
    int calls=0;
    RowResult last;
    void Expand(const std::vector<int>&,const Consumer& consume) override {++calls;last=consume(ids.data(),static_cast<int>(ids.size()));}
};
void SearchTests() {
    BKT::Index<float> index;
    for(auto p:std::vector<std::pair<const char*,const char*>>{
        {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"MaxCheck","2048"},
        {"BKTKmeansK","8"},{"BKTLeafSize","1"},{"NeighborhoodSize","128"},{"TPTNumber","1"},
        {"NumberOfInitialDynamicPivots","1"},{"NumberOfOtherDynamicPivots","0"}})
        index.SetParameter(p.first,p.second);
    constexpr int n=256;
    std::vector<float> data(n*8);
    for(int i=0;i<n;++i) for(int j=0;j<8;++j) data[i*8+j]=float(i*(j+1));
    std::copy_n(data.data()+253*8,8,data.data()+254*8);
    std::copy_n(data.data()+253*8,8,data.data()+255*8);
    CHECK(index.BuildIndex(data.data(),n,8)==ErrorCode::Success);
    using Result=COMMON::QueryResultSet<float>;
    auto values=[](Result& r) {
        std::vector<std::pair<int,float>> out;
        for(int i=0;i<r.GetResultNum();++i) out.emplace_back(r.GetResult(i)->VID,r.GetResult(i)->Dist);
        return out;
    };
    Result ordinary(data.data(),24),disabled(data.data(),24);
    CHECK(index.SearchIndex(ordinary)==ErrorCode::Success);
    CHECK(index.SearchIndexWithPostingNavigation(disabled,{})==ErrorCode::Success);
    CHECK(values(ordinary)==values(disabled));
    for(int budget:{1,8,64,2048}) for(int k:{1,24,256}) {
        Result graph(data.data(),k),bit(data.data(),k);
        CHECK(index.SearchIndexWithResultFilter(graph,[](int){return false;},budget)==ErrorCode::Success);
        CHECK(index.SearchIndexWithPostingNavigation(bit,[](int){return false;},nullptr,budget)==ErrorCode::Success);
        CHECK(values(graph)==values(bit));
    }
    CHECK(index.DeleteIndex(254)==ErrorCode::Success);
    Result graph(data.data(),256),bit(data.data(),256);
    std::vector<int> originalCalls;
    CHECK(index.SearchIndexWithResultFilter(graph,[&](int id){originalCalls.push_back(id);return true;})==ErrorCode::Success);
    CHECK(index.SearchIndexWithPostingNavigation(bit,[](int){return true;})==ErrorCode::Success);
    CHECK(values(graph)==values(bit));
    for(auto value:values(bit)) CHECK(value.first!=254);
    for(int id=0;id<n;++id) std::fill_n(index.GetMutableGraph()[id],128,-1);
    for(int i=0;i<127;++i) index.GetMutableGraph()[0][i]=i+1;
    Result native(data.data(),24),matched(data.data(),24),bits(data.data(),24);
    CHECK(index.SearchIndex(native)==ErrorCode::Success);
    std::map<int,int> counters;
    auto filter=[&](int id){++counters[id];return id>=65 && id<89;};
    CHECK(index.SearchIndexWithResultFilter(matched,filter)==ErrorCode::Success);
    CHECK(index.SearchIndexWithPostingNavigation(bits,[](int id){return id>=65 && id<89;})==ErrorCode::Success);
    CHECK(values(matched)==values(bits));
    for(auto value:values(native)) CHECK(value.first<65);
    for(auto value:values(bits)) CHECK(value.first>=65 && value.first<89);
    CHECK(counters[127]==1);
    std::cout<<"PASS native alias/delete and unfiltered bridges; farther matching24; predicate runs beyond output distance bound\n";
    OneRow posting;
    for(unsigned id=1;id<254;++id) posting.ids.push_back(id);
    for(int id=0;id<n;++id) std::fill_n(index.GetMutableGraph()[id],128,-1);
    index.GetMutableGraph()[0][0]=1;
    Result augmented(data.data(),24);
    std::set<int> initialized;
    CHECK(index.SearchIndexWithPostingNavigation(augmented,[&](int id) {
        initialized.insert(id);return id>=64;
    },&posting,8,false,8,1)==ErrorCode::Success);
    CHECK(posting.calls==1);
    CHECK(posting.last.degree == posting.ids.size() && posting.last.eligible == 190);
    CHECK(posting.last.newCandidates > 0 && posting.last.newCandidates <= posting.last.eligible);
    CHECK(!posting.last.canContinue);
    for(auto id:posting.ids) CHECK(initialized.count(id)==1);
    int present=0;for(auto value:values(augmented)) present+=value.first>=64;
    CHECK(present==24);
    std::cout<<"PASS selected CSR completes beyond remaining budget; match-false ordinary bridge survives\n";
    for(int degree:{100,101}) {
        for(int id=0;id<n;++id) std::fill_n(index.GetMutableGraph()[id],128,-1);
        index.GetMutableGraph()[0][0]=1;
        for(int edge=1;edge<degree;++edge) index.GetMutableGraph()[0][edge]=2;
        OneRow boundary;boundary.ids={102};
        Result result(data.data(),2);
        Result reference(data.data(),2);
        CHECK(index.SearchIndexWithResultFilter(reference,[](int id){return id==1 || id==102;})==ErrorCode::Success);
        int filled=0;for(int i=0;i<2;++i) filled+=reference.GetResult(i)->VID>=0;
        CHECK(index.SearchIndexWithPostingNavigation(result,[](int id){return id==1 || id==102;},&boundary)==ErrorCode::Success);
        CHECK(boundary.calls==(filled<2?1:0));
    }
    std::cout<<"PASS supplementation depends on actual head deficit, not the historical row fraction\n";
    SPANN::LimitedTagSupport support;
    CHECK(support.Initialize(n,2,1,0,2,1));
    index.InitializeHeadNodeMeta(n,0,index.GetHeadNodeHierWidths(),true);
    index.SetHeadNodeOwnTagsAvailable(true);
    for(int id=0;id<n;++id) {
        std::uint32_t attributes[]={std::uint32_t(id+100),std::uint32_t(id)};
        CHECK(support.SetHeadAttributes(id,attributes,2));
        CHECK(support.SetHeadTags(id,{attributes[0]}));
        Cache::PostingBitmask empty;empty.Clear();index.SetHeadNodePS(id,empty);
    }
    std::vector<Cache::PostingBitmask> signatures;
    CHECK(SPANN::CollectHierarchyHeadSignatures(index,support,signatures));
    for(int id=0;id<n;++id) {
        Cache::PostingBitmask own;own.Clear();own.Insert(support.OwnTag(id));
        CHECK(signatures[id].MayIntersect(own) && support.Supports(id,support.OwnTag(id)));
    }
    std::cout<<"PASS empty-posting summary includes own attribute without own qualification\n";
}
struct MockPosting {
    std::vector<std::vector<std::uint32_t>> rows;
    int lower=0, replicas=0;
    mutable int reads=0;
    const std::vector<bool>* allowed=nullptr;
    int ReplicaCount() const {return replicas ? replicas : static_cast<int>(rows.size());}
    int FirstLevelHeadCount() const {return lower;}
    int SecondLevelHeadCount() const {return static_cast<int>(rows.size());}
    const std::uint32_t* Begin(int id) const {
        if(allowed) CHECK(allowed->at(id));
        ++reads;return rows.at(id).data();
    }
    const std::uint32_t* End(int id) const {
        if(allowed) CHECK(allowed->at(id));
        ++reads;return rows.at(id).data()+rows.at(id).size();
    }
};
void SignatureTests() {
    std::array<MockPosting,2> postings;
    postings[0].lower=2;postings[0].rows.assign(8,{0,1});
    postings[1].lower=8;postings[1].rows.assign(8,{0,1,2,3,4,5,6,7});
    SPANN::PostingOwners owners(postings);
    for(int pass=0;pass<3;++pass) {
        std::array<std::vector<bool>,2> allowed{std::vector<bool>(8,false),std::vector<bool>(8,false)};
        std::array<int,2> calls{};
        for(int l=0;l<2;++l) {postings[l].reads=0;postings[l].allowed=&allowed[l];}
        const auto signature=[&](int level,int id) {
            ++calls[level];
            return allowed[level][id]=(pass==1 && level==0 && id==3) || (pass==2 && level==1);
        };
        int reps=0,consumed=0;
        const auto distance=[&](int level,int id) {CHECK(allowed[level][id]);++reps;return float(id);};
        SPANN::HierarchyPostingQuery<decltype(postings),decltype(signature),decltype(distance)>
            query(owners,postings,signature,distance);
        query.SetSearchCapacity(16);
        query.Expand(0,[&](const std::uint32_t* ids,int count) {
            CHECK(count==2 && ids[0]==0 && ids[1]==1);++consumed;
            return COMMON::PostingNavigation::RowResult{2,2,true,2,true};
        });
        CHECK(calls[0]==8);
        if(pass==0) CHECK(reps==0 && postings[0].reads==0 && postings[1].reads==0);
        if(pass==1) CHECK(reps==1 && consumed==1 && postings[1].reads==0 && calls[1]==8);
        if(pass==2) CHECK(reps==8 && consumed==0 && postings[0].reads==0 && postings[1].reads>0);
        for(auto& p:postings) p.allowed=nullptr;
    }
    std::cout<<"PASS H2/H3 signature precedes representative/member access; full selected row\n";
    std::array<MockPosting,3> deep;
    deep[0].lower=3; deep[0].rows={{0},{1},{2}};
    deep[1].lower=3; deep[1].rows={{0},{1,2}};
    deep[2].lower=2; deep[2].rows={{0,1}};
    for(auto& layer:deep) layer.replicas=1;
    SPANN::PostingOwners deepOwners(deep);
    const auto allowed=[](std::size_t level,int id) {return level==2 || id==1;};
    const auto distance=[&](std::size_t level,int id) {CHECK(allowed(level,id));return float(id);};
    SPANN::HierarchyPostingQuery<decltype(deep),decltype(allowed),decltype(distance)>
        deepQuery(deepOwners,deep,allowed,distance);
    deepQuery.SetSearchCapacity(16);
    int selected=0;
    deepQuery.Expand(0,[&](const std::uint32_t* row,int size) {
        CHECK(size==1 && row[0]==1);++selected;
        return COMMON::PostingNavigation::RowResult{1,1,true,1,true};
    });
    CHECK(selected==1);
    CHECK(deepOwners.Levels()==3);
    CHECK(deepOwners.Parents(0,0).last-deepOwners.Parents(0,0).first==1);
    std::cout<<"PASS persisted H1-H4 depth and one-owner shape; signed upper posting discovers H2 adjacency\n";
}
void AliasFixture(const char* folder) {
    BKT::Index<float> index;
    index.SetParameter("NumberOfThreads","1");
    index.SetParameter("NeighborhoodSize","32");
    index.SetParameter("DistCalcMethod","L2");
    std::vector<float> duplicates(256*8,0);
    CHECK(index.BuildIndex(duplicates.data(),256,8)==ErrorCode::Success);
    CHECK(index.DeleteIndex(1)==ErrorCode::Success);
    for(int id=0;id<256;++id) std::fill_n(index.GetMutableGraph()[id],32,-1);
    index.GetMutableGraph()[0][31]=-2;
    CHECK(index.SaveIndex(std::string(folder))==ErrorCode::Success);
    // Explicit native collapsed tree: representative0, identical aliases1..255.
    std::vector<COMMON::BKTNode> nodes;
    for(int id=0;id<256;++id) nodes.emplace_back(id);
    nodes.emplace_back(-1);nodes[0].childStart=-1;nodes[0].childEnd=256;
    const std::int32_t header[]={1,0,257};
    std::ofstream tree(std::string(folder)+"/tree.bin",std::ios::binary);
    tree.write(reinterpret_cast<const char*>(header),sizeof(header));
    tree.write(reinterpret_cast<const char*>(nodes.data()),nodes.size()*sizeof(nodes[0]));
    CHECK(bool(tree));
}
void PostingStateScalingTests() {
    struct Owners {
        int count;
        const int parent[2] = {0, 1};
        std::size_t Levels() const { return 2; }
        int Count(std::size_t) const { return count; }
        SPANN::PostingOwners::Range Parents(std::size_t level, int head) const {
            const int* value = parent + (level == 0 ? head % 2 : 0);
            return {value, value + 1};
        }
    };
    struct Layer {
        bool upper;
        std::uint32_t children[4] = {2, 3, 4, 5};
        const std::uint32_t* Begin(int) const { return children; }
        const std::uint32_t* End(int) const { return children + (upper ? 4 : 2); }
    };
    const std::vector<Layer> layers{{false}, {true}};
    std::size_t expectedAllocations = 0, expectedBytes = 0;
    for (int count : {1000, 14400000, 1000000000}) {
        Owners owners{count};
        int signatures = 0, distances = 0, rows = 0;
        const auto signature = [&](std::size_t level, int id) {
            ++signatures;
            return level == 1 || id >= 2;
        };
        const auto distance = [&](std::size_t, int id) {
            ++distances;
            return static_cast<float>(id);
        };
        const auto beforeAllocations = allocations.load();
        const auto beforeBytes = allocationBytes.load();
        {
            SPANN::HierarchyPostingQuery<decltype(layers), decltype(signature),
                decltype(distance), Owners> query(owners, layers, signature, distance);
            query.SetSearchCapacity(16);
            for (int i = 0; i < 100; ++i)
                query.Expand(i % 2, [&](const std::uint32_t* row, int size) {
                    CHECK(size == 2 && row[0] == 2 && row[1] == 3);
                    ++rows;
                    return COMMON::PostingNavigation::RowResult{2,2,true,2,true};
                });
        }
        const auto usedAllocations = allocations.load() - beforeAllocations;
        const auto usedBytes = allocationBytes.load() - beforeBytes;
        CHECK(signatures == 7 && distances == 5 && rows == 4);
        if (count == 1000) {
            expectedAllocations = usedAllocations;
            expectedBytes = usedBytes;
        }
        CHECK(usedAllocations == expectedAllocations && usedBytes == expectedBytes);
        std::cout << "PASS posting state scaling catalog=" << count
                  << " touched=7 distances=5 signatures=7 rows=4 allocations="
                  << usedAllocations << " allocated_bytes=" << usedBytes << '\n';
    }
}
void NumericRoutingTests() {
    constexpr int count = 256;
    auto index = std::make_shared<BKT::Index<float>>();
    for (auto p : std::vector<std::pair<const char*,const char*>>{
        {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"MaxCheck","2048"},
        {"BKTKmeansK","8"},{"BKTLeafSize","1"},{"NeighborhoodSize","128"},{"TPTNumber","1"},
        {"NumberOfInitialDynamicPivots","1"},{"NumberOfOtherDynamicPivots","0"}})
        index->SetParameter(p.first,p.second);
    std::vector<float> data(count * 8);
    for (int i = 0; i < count; ++i)
        for (int j = 0; j < 8; ++j) data[i*8+j] = float(i*(j+1));
    CHECK(index->BuildIndex(data.data(),count,8) == ErrorCode::Success);
    SPANN::LimitedTagSupport support;
    CHECK(support.Initialize(count,2,1,1,3,1));
    const auto widths = index->GetHeadNodeHierWidths();
    index->InitializeHeadNodeMeta(count,2,widths,true);
    for (int head = 0; head < count; ++head) {
        const std::uint32_t attributes[] = {head >= 64 ? 1000U : 0U,
            static_cast<std::uint32_t>(head == 2 ? 9999 : head + 100),
            static_cast<std::uint32_t>(head)};
        CHECK(support.SetHeadAttributes(head,attributes,3));
        CHECK(support.SetHeadTags(head,{attributes[1]}));
        Cache::HierarchicalPostingMask empty; empty.Clear();
        index->SetHeadNodePostingHierMask(head,empty);
        index->SetHeadNodeGlobalVID(head,head);
        std::fill_n(index->GetMutableGraph()[head],128,-1);
    }
    index->SetHeadNodeNumericDomainFingerprint(123);
    std::vector<SPANN::SecondLevelHeadPostings> csr(1);
    std::vector<std::uint32_t> members(count);
    std::iota(members.begin(),members.end(),0);
    CHECK(csr[0].Initialize(count,2,1,11,1,0,1,{0,128},{0,128,256},
        members,std::vector<Cache::PostingBitmask>(2)));
    SPANN::RoutingSignatures signatures;
    const std::vector<Cache::NumQuantParam> domains = {{0,1000},{0,255}};
    CHECK(signatures.Build(*index,support,{1},{0,2},csr,domains));
    CHECK(signatures.Current(index.get()));
    CHECK(signatures.SharedBytes() == 2 * 2 * (32 + 2 * 32));
    Cache::DNFPredicate sparse; sparse.clauses.push_back({{{0,1000,Cache::DNF_EQ,1}}});
    Cache::DNFPredicate dense; dense.clauses.push_back({{{0,0,Cache::DNF_GE,1}}});
    Cache::DNFPredicate absent; absent.clauses.push_back({{{0,500,Cache::DNF_EQ,1}}});
    Cache::DNFPredicate mixed = sparse; mixed.clauses.push_back({{{1,9999,Cache::DNF_EQ,0}}});
    const auto compile = [&](const Cache::DNFPredicate& dnf) {
        return SPANN::RoutingPredicate(&dnf,nullptr,0,{0,2},domains);
    };
    const auto sparseQuery = compile(sparse), denseQuery = compile(dense);
    const auto mixedQuery = compile(mixed), absentQuery = compile(absent);
    CHECK(!signatures.HeadMayMatch(sparseQuery,1,false));
    CHECK(signatures.HeadMayMatch(sparseQuery,64,false));
    CHECK(signatures.HeadMayMatch(sparseQuery,64,true));
    CHECK(signatures.HeadMayMatch(mixedQuery,2,false));
    CHECK(!signatures.HeadMayMatch(mixedQuery,1,false));
    for (int upper = 0; upper < 2; ++upper) {
        CHECK(signatures.UpperMayMatch(sparseQuery,0,upper,false));
        CHECK(!signatures.UpperMayMatch(absentQuery,0,upper,false));
    }
    for (int degree : {100,101}) {
        index->GetMutableGraph()[0][0] = 64;
        for (int edge = 1; edge < degree; ++edge) index->GetMutableGraph()[0][edge] = 1;
        OneRow posting; posting.ids = {65};
        COMMON::QueryResultSet<float> result(data.data(),24);
        COMMON::QueryResultSet<float> reference(data.data(),24);
        CHECK(index->SearchIndexWithResultFilter(reference,
            [&](int head) { return signatures.HeadMayMatch(sparseQuery,head,false); })==ErrorCode::Success);
        int filled=0;for(int i=0;i<24;++i) filled+=reference.GetResult(i)->VID>=0;
        CHECK(index->SearchIndexWithPostingNavigation(result,
            [&](int head) { return signatures.HeadMayMatch(sparseQuery,head,false); },
            &posting) == ErrorCode::Success);
        CHECK(posting.calls == (filled<24?1:0));
    }
    OneRow unused; unused.ids = {65};
    COMMON::QueryResultSet<float> result(data.data(),24);
    COMMON::QueryResultSet<float> denseReference(data.data(),24);
    CHECK(index->SearchIndexWithResultFilter(denseReference,
        [&](int head) { return signatures.HeadMayMatch(denseQuery,head,false); })==ErrorCode::Success);
    int denseFilled=0;for(int i=0;i<24;++i) denseFilled+=denseReference.GetResult(i)->VID>=0;
    CHECK(index->SearchIndexWithPostingNavigation(result,
        [&](int head) { return signatures.HeadMayMatch(denseQuery,head,false); },
        &unused) == ErrorCode::Success);
    CHECK(unused.calls == (denseFilled<24?1:0));
    const auto allocationsBefore = allocations.load();
    for (int repeat = 0; repeat < 100; ++repeat) for (int head = 0; head < count; ++head)
        CHECK(signatures.HeadMayMatch(sparseQuery,head,false) == (head >= 64));
    CHECK(allocations.load() == allocationsBefore);
    CHECK(SPANN::SaveHeadNodeMetadataV8("numeric_routing_test.bin",index,1));
    CHECK(signatures.Current(index.get()));
    CHECK(SPANN::LoadHeadNodeMetadataV8("numeric_routing_test.bin",index,count,1,
        [](int head) { return head; }));
    CHECK(!signatures.Current(index.get()));
    CHECK(signatures.Build(*index,support,{1},{0,2},csr,domains));
    CHECK(signatures.HeadMayMatch(sparseQuery,64,false));
    CHECK(std::remove("numeric_routing_test.bin") == 0);
    std::cout << "PASS numeric own/region/upper unions, unanchored OR, repeated-edge .01 gate, dense bypass, metadata reload/refresh, zero per-visit allocations\n";
}
template<class T> void AuxiliaryNegativeSkipTest() {
    BKT::Index<T> index;
    for(auto p:std::vector<std::pair<const char*,const char*>>{
        {"DistCalcMethod","L2"},{"NumberOfThreads","1"},{"MaxCheck","64"},
        {"BKTKmeansK","8"},{"BKTLeafSize","1"},{"NeighborhoodSize","32"},{"TPTNumber","1"},
        {"NumberOfInitialDynamicPivots","1"},{"NumberOfOtherDynamicPivots","0"}})
        index.SetParameter(p.first,p.second);
    constexpr int count=256, dim=128;
    std::vector<T> data(count*dim);
    for(int id=0;id<count;++id) std::fill_n(data.data()+id*dim,dim,static_cast<T>(id));
    CHECK(index.BuildIndex(data.data(),count,dim)==ErrorCode::Success);
    for(int id=0;id<count;++id) std::fill_n(index.GetMutableGraph()[id],32,-1);
    index.GetMutableGraph()[0][0]=1;
    OneRow posting;
    for(int repeat=0;repeat<2;++repeat) for(int id=200;id<216;++id) {
        posting.ids.push_back(id);
        index.GetMutableGraph()[id][0]=101;
    }
    std::map<int,int> predicateCalls;
    COMMON::QueryResultSet<T> result(data.data(),24);
    COMMON::GraphAccessStats stats;
    {
        COMMON::ScopedGraphAccessStats scope(&stats);
        CHECK(index.SearchIndexWithPostingNavigation(result,[&](int id) {
            ++predicateCalls[id]; return id==101;
        },&posting,8,false,8,1)==ErrorCode::Success);
    }
    CHECK(posting.calls==1 && posting.last.degree==32);
    CHECK(posting.last.eligible==0 && posting.last.newCandidates==0);
    for(int i=0;i<result.GetResultNum();++i) {
        const auto id=result.GetResult(i)->VID;
        CHECK(id<0 || id==101);
    }
    CHECK(posting.last.canContinue);
    for(int id=200;id<216;++id) CHECK(predicateCalls[id]==1);
#ifdef SPTAG_QUERY_WORK_DIAGNOSTICS
    CHECK(stats.m_auxiliaryFirstVisits==0 && stats.m_auxiliaryNegativeFirstVisits==0);
    CHECK(stats.m_auxiliaryUnvisitedNegativeSkips>0 && stats.m_auxiliaryUnvisitedNegativeSkips<=16);
    CHECK(stats.m_auxiliaryUnvisitedNegativeSkips+stats.m_auxiliaryVisitedSkips==32);
    CHECK(stats.m_auxiliaryPrefetchLines==0 && stats.m_supplementLeaves==0 && stats.m_supplementDistances==0);
    CHECK(stats.m_graphDistances>0 && stats.m_graphLeaves==8);
#endif
    std::cout<<"PASS terminal auxiliary negatives cached once without scoring/prefetch/budget; graph negatives remain scored; vector bytes="<<sizeof(T)*dim<<'\n';
}
int main(int argc,char** argv) {
    try {
        StorageTests();
        if(argc!=2 || std::string(argv[1])!="--storage-only") {
            SearchTests();SignatureTests();PostingStateScalingTests();NumericRoutingTests();
            AuxiliaryNegativeSkipTest<float>();AuxiliaryNegativeSkipTest<std::uint8_t>();
            if(argc==2) AliasFixture(argv[1]);
        }
        return 0;
    }
    catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
