#pragma once
#include <cstdint>
#include <cstring>
#include <vector>

namespace OrderTrace {
enum Stage { TreeInitial, TreeContinuation, Ordinary, Popped, Alias };
struct Event { std::int32_t query, stage, kind, id, value, extra; };
inline thread_local std::vector<Event>* events=nullptr;
inline thread_local int query=0,stage=TreeInitial;
inline thread_local bool initializing=false;
inline int bits(float value) {
    std::int32_t result;
    std::memcpy(&result,&value,sizeof(result));
    return result;
}
inline void Emit(char kind,int id,int value=0,int extra=0) {
    if(events) events->push_back({query,stage,kind,id,value,extra});
}
struct StageScope {
    int previous=stage;
    explicit StageScope(int value) {stage=value;}
    ~StageScope() {stage=previous;}
};
struct InitScope {
    bool previous=initializing;
    InitScope() {initializing=true;}
    ~InitScope() {initializing=previous;}
};
}
