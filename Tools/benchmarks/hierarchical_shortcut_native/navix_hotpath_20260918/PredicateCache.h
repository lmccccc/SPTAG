#pragma once
#include <array>
#include <cstdint>
#include <memory>
#include <vector>
#include <xmmintrin.h>

namespace NativeReuse {
// Only immutable predicate components are memoized. Native deletion, visited,
// alias enumeration and result competitiveness remain at their admission sites.
struct PredicateCache {
    enum Component { Support, Exact, Posting };
    struct Entry { std::uint64_t generation=0; unsigned char known=0, values=0; };
    std::vector<Entry> entries;
    std::uint64_t generation=0;
    int size=0;
    bool busy=false, diagnostics=false;
    std::array<std::uint64_t, 5> counts{};
    void Begin(int count,bool observe) {
        size=count; diagnostics=observe; counts={};
        if (++generation==0) {
            for (auto& entry:entries) entry.generation=0;
            ++generation;
        }
    }
    void Prefetch(int id) const {
        if (id>=0 && static_cast<std::size_t>(id)<entries.size())
            _mm_prefetch(reinterpret_cast<const char*>(&entries[id]),_MM_HINT_T0);
    }
    template<class Evaluate> bool Get(int id,Component component,Evaluate&& evaluate) {
        if (diagnostics) ++counts[0];
        if (id<0 || id>=size) {
            if (diagnostics) ++counts[1];
            return evaluate();
        }
        if (static_cast<std::size_t>(id)>=entries.size()) {
            const auto capacity=entries.capacity();
            entries.resize(static_cast<std::size_t>(id)+1);
            if (diagnostics && capacity!=entries.capacity()) ++counts[4];
        }
        auto& entry=entries[id];
        if (entry.generation!=generation) {
            entry.generation=generation; entry.known=entry.values=0;
        }
        const unsigned char bit=1u<<component;
        if (entry.known&bit) {
            const bool value=(entry.values&bit)!=0;
            if (diagnostics) ++counts[value?2:3];
            return value;
        }
        if (diagnostics) ++counts[1];
        // Evaluation may fill a different component and grow the vector.
        const bool value=evaluate();
        entries[id].known|=bit;
        if (value) entries[id].values|=bit;
        return value;
    }
};

class PredicateLease {
    std::unique_ptr<PredicateCache> nested;
    PredicateCache* cache=nullptr;
public:
    PredicateLease(bool enabled,int size,bool diagnostics) {
        if (!enabled) return;
        static thread_local PredicateCache reusable;
        if (reusable.busy) {
            nested=std::make_unique<PredicateCache>();
            cache=nested.get();
        } else cache=&reusable;
        cache->busy=true;
        cache->Begin(size,diagnostics);
    }
    ~PredicateLease() { if (cache) cache->busy=false; }
    PredicateLease(const PredicateLease&)=delete;
    PredicateLease& operator=(const PredicateLease&)=delete;
    PredicateCache* Get() const { return cache; }
};
}
