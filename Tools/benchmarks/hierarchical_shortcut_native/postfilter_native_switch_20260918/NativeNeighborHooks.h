#pragma once
#include "NativeFunctionRef.h"
#include "PostingPolicy.h"
#include <functional>
#include <vector>

namespace SPTAG { namespace COMMON {
struct NativeEdgeConsumer {
    NativeFunctionRef<Postfilter::Work(const std::uint32_t*,int)> consumeRow;
    NativeFunctionRef<bool()> withinBudget;
};
struct NativeNeighborHooks {
    bool diagnostics=false,capture=false,injected=false;
    bool originalDistanceFunction=false,defaultAdmission=false,filteredAdmission=false,auditVisited=false;
    double activationRatio=.01;
    int checked=0;
    std::function<void(int,float)> ownPoint;
    NativeFunctionRef<bool(int,float)> ownGain;
    NativeFunctionRef<bool(int)> auxiliaryEligible;
    NativeFunctionRef<bool(int,const NativeEdgeConsumer&)> posting;
    std::uint64_t headDistances=0,routingDistances=0,childDistances=0;
    std::uint64_t calls=0,returns=0,parentDistances=0,members=0;
    std::uint64_t signatureChecks=0,signatureRejects=0,h2Rows=0,h3Rows=0;
    std::uint64_t queueOffers=0,queueAccepted=0,queueRejected=0;
    std::uint64_t sparseActivations=0,selectedActions=0;
    std::uint64_t ordinaryPredicateCalls=0,ordinaryPredicatePasses=0,checksZeroRows=0;
    std::uint64_t auxiliaryPredicateCalls=0;
    std::uint64_t outerExpansions=0,ordinaryNeighborEntries=0;
    Postfilter::Work graphWork,postingWork;
    std::vector<std::pair<int,bool>> visitedEdges;
    std::vector<std::pair<int,bool>> allVisited;
    std::vector<std::pair<int,float>> evaluated;
    struct Row { int level,id; std::uint64_t size,consumed; };
    std::vector<Row> rows;
    std::vector<std::pair<int,int>> rejectedRows;
    std::vector<Postfilter::Decision> decisions;
    void Distance(int id,float d,bool routing) {
        if (!diagnostics) return;
        if (routing) ++routingDistances;
        else if (injected) ++childDistances;
        else ++headDistances;
        if (capture) evaluated.emplace_back(id,d);
    }
};
}}
