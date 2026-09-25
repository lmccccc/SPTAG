#pragma once
#include "NativeFunctionRef.h"
#include "NavixPolicy.h"
#include <functional>
#include <vector>

namespace SPTAG { namespace COMMON {
struct NativeEdgeConsumer {
    NativeFunctionRef<Navix::Work(const std::uint32_t*,int)> consumeRow;
    NativeFunctionRef<bool()> withinBudget;
};
struct NativeNeighborHooks {
    bool diagnostics=false,capture=false,injected=false,navix=false;
    bool originalDistanceFunction=false,defaultAdmission=false,filteredAdmission=false,auditVisited=false;
    double postingThreshold=0;
    int checked=0;
    std::function<void(int,float)> ownPoint;
    NativeFunctionRef<bool(int,float)> ownGain;
    NativeFunctionRef<bool(int)> eligible;
    NativeFunctionRef<void(int)> prefetchPredicate;
    NativeFunctionRef<bool(int,const NativeEdgeConsumer&)> posting;
    std::uint64_t headDistances=0,routingDistances=0,childDistances=0;
    std::uint64_t calls=0,returns=0,parentDistances=0,members=0;
    std::uint64_t signatureChecks=0,signatureRejects=0,h2Rows=0,h3Rows=0;
    std::uint64_t queueOffers=0,queueAccepted=0,queueRejected=0;
    std::array<std::uint64_t,5> branches{};
    std::uint64_t fallbacks=0,qualificationChecks=0,secondRows=0,intermediateDistances=0;
    Navix::Work graphWork,postingWork;
    std::array<std::uint64_t,5> predicateCache{};
    std::uint64_t scratchGrowths=0;
    std::vector<std::pair<int,bool>> visitedEdges;
    std::vector<std::pair<int,float>> evaluated;
    std::vector<int> intermediates;
    struct Row { int level,id; std::uint64_t size,consumed; };
    std::vector<Row> rows;
    std::vector<std::pair<int,int>> rejectedRows;
    std::vector<Navix::Decision> decisions;
    void Distance(int id,float d,bool routing) {
        if (!diagnostics) return;
        if (routing) ++routingDistances;
        else if (injected) ++childDistances;
        else ++headDistances;
        if (capture) evaluated.emplace_back(id,d);
    }
};
}}
