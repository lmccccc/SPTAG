#pragma once
#include <algorithm>
#include <stdexcept>
#include <unordered_set>

namespace SPTAG { namespace COMMON {
class NativeDeficitIdentities {
    int* values;
    int size = 0, capacity;
    bool proof;
    std::unordered_set<int> proofIDs;
public:
    NativeDeficitIdentities(int* scratch, int count, bool capture)
        : values(scratch), capacity(count), proof(capture) {}
    void Seed(int id) {
        if (std::find(values, values + size, id) != values + size) return;
        if (size == capacity) throw std::runtime_error("Native degree scratch bound violated");
        values[size++] = id;
        if (proof) proofIDs.insert(id);
    }
    bool NeedsQualification(int id, int added, int deficit) {
        if (proof) return proofIDs.insert(id).second;
        return added < deficit && std::find(values, values + size, id) == values + size;
    }
    void Accept(int id, int added, int deficit) {
        if (!proof && added < deficit) Seed(id);
    }
    int Stored() const { return size; }
};
}}
