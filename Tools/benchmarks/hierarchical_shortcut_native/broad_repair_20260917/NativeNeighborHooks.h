#pragma once
#include <cstdint>
#include <functional>
#include <limits>
#include <utility>
#include <vector>
#include <unordered_set>

namespace SPTAG { namespace COMMON {
struct NativeEligibility {
    bool traversal = true;
    bool posting = false;
    bool postingKnown = false;
};
struct NativeSupplyAccounting;
struct NativeEdgeConsumer {
    std::function<void(int, bool)> consume;
    std::function<bool()> withinBudget;
    std::function<int()> checked;
    std::function<void(const std::uint32_t*, int, NativeSupplyAccounting&)> consumeRow;
};
struct NativeDegreeFrame {
    int head, physical, eligible, required, before, after;
    bool startup;
    std::uint64_t calls;
    std::vector<std::pair<int, bool>> ordinary;
    std::vector<int> supplied;
};
struct NativeSupplyAccounting {
    std::unordered_set<int>& identities;
    int& added;
    NativeDegreeFrame& frame;
};
struct NativeNeighborHooks {
    bool diagnostics = false, capture = false, entry = true, injected = false;
    bool originalDistanceFunction = false;
    bool defaultAdmission = false, filteredAdmission = false;
    std::uint64_t rowEligibilityEvaluations = 0;
    double retainedRatio = 0.5;
    int anchor = -1;
    float anchorDistance = (std::numeric_limits<float>::max)();
    int checked = 0;
    std::function<void(int, float)> ownPoint;
    std::function<NativeEligibility(int)> qualify;
    std::function<void(int, const int*, int, bool, const NativeEdgeConsumer&)> expand;
    std::function<void(NativeDegreeFrame&, const int*, int, const NativeEdgeConsumer&)> completeOrdinary;
    std::uint64_t fusedRows = 0, fusedMembers = 0, degreeVisited = 0, degreeFresh = 0;
    std::uint64_t traversalReuse = 0, resultReuse = 0, partialRows = 0;
    std::uint64_t shortRowProofQualifications = 0;
    std::uint64_t batchRows = 0, csrQualifications = 0, csrTraversalReuse = 0;
    int minimumPhysicalDegree = 16;
    bool auditVisited = false;
    std::vector<std::pair<int, bool>> visitedEdges;
    std::uint64_t headDistances = 0, routingDistances = 0, childDistances = 0;
    std::uint64_t calls = 0, parentDistances = 0, members = 0, returns = 0;
    std::uint64_t signatureChecks = 0, signatureRejects = 0, h2Rows = 0, h3Rows = 0;
    std::uint64_t queueOffers = 0, queueAccepted = 0, queueRejected = 0;
    std::vector<std::pair<int, float>> evaluated;
    std::vector<NativeDegreeFrame> frames;
    struct Row { int level, id; std::uint64_t size, consumed; };
    std::vector<Row> rows;
    std::vector<std::pair<int, int>> rejectedRows;

    void Distance(int id, float distance, bool routing) {
        if (diagnostics) {
            if (routing) ++routingDistances;
            else if (injected) ++childDistances;
            else ++headDistances;
            if (capture) evaluated.emplace_back(id, distance);
        }
        if (entry && expand && (anchor < 0 || std::make_pair(distance, id) <
                                std::make_pair(anchorDistance, anchor))) {
            anchor = id;
            anchorDistance = distance;
        }
    }
};
}}
