#pragma once
#include "NativeFunctionRef.h"
#include "CostModel.h"
#include <functional>
#include <vector>

namespace SPTAG { namespace COMMON {
struct NativeEdgeConsumer {
    NativeFunctionRef<CostArbitration::Work(const std::uint32_t*, int)> consumeRow;
    NativeFunctionRef<bool()> withinBudget;
    NativeFunctionRef<int()> checked;
    NativeFunctionRef<int()> remaining;
    NativeFunctionRef<int()> remainingChecks;
};
struct NativeNeighborHooks {
    bool diagnostics=false, capture=false, injected=false;
    bool originalDistanceFunction=false, defaultAdmission=false, filteredAdmission=false;
    bool auditVisited=false;
    int checked=0;
    std::function<void(int,float)> ownPoint;
    NativeFunctionRef<bool(int,float)> ownGain;
    NativeFunctionRef<void(int,const CostArbitration::Work&,int,const NativeEdgeConsumer&)> afterGraph;
    std::uint64_t headDistances=0, routingDistances=0, childDistances=0;
    std::uint64_t calls=0, returns=0, parentDistances=0, members=0;
    std::uint64_t signatureChecks=0, signatureRejects=0, h2Rows=0, h3Rows=0;
    std::uint64_t queueOffers=0, queueAccepted=0, queueRejected=0;
    std::uint64_t graphDecisions=0, postingDecisions=0, discoveryDecisions=0, estimates=0;
    CostArbitration::Work graphWork, postingWork;
    std::vector<std::pair<int,bool>> visitedEdges;
    std::vector<std::pair<int,float>> evaluated;
    struct Row { int level,id; std::uint64_t size,consumed; };
    std::vector<Row> rows;
    std::vector<std::pair<int,int>> rejectedRows;
    std::vector<CostArbitration::Decision> decisions;
    void Distance(int id,float d,bool routing) {
        if (!diagnostics) return;
        if (routing) ++routingDistances;
        else if (injected) ++childDistances;
        else ++headDistances;
        if (capture) evaluated.emplace_back(id,d);
    }
};
}}
