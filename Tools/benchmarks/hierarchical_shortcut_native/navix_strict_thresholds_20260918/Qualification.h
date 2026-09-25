#pragma once
#include <array>
#include <cstdint>
#include <memory>
#include <vector>

namespace NativeReuse {
struct Qualification {
    enum : unsigned char { PostingKnown=1, PostingYes=2, ExactKnown=4, ExactYes=8 };
    std::vector<unsigned char> bytes;
    std::array<std::uint64_t,6> counts{};
    bool busy=false,diagnostics=false;
    void Begin(int size,bool observe) {
        diagnostics=observe;
        counts={};
        const auto capacity=bytes.capacity();
        bytes.assign(size,0);
        if (diagnostics && bytes.capacity()!=capacity) ++counts[5];
    }
    unsigned char* Entry(int id) {
        return id>=0 && static_cast<std::size_t>(id)<bytes.size() ? &bytes[id] : nullptr;
    }
    template<class Evaluate> bool Posting(int id,Evaluate&& evaluate) {
        if (diagnostics) ++counts[1];
        auto* entry=Entry(id);
        if (entry && (*entry&PostingKnown)) return *entry&PostingYes;
        if (diagnostics) ++counts[2];
        const bool value=evaluate();
        if (entry) *entry|=PostingKnown|(value?PostingYes:0);
        return value;
    }
    template<class Evaluate> bool Exact(int id,Evaluate&& evaluate) {
        if (diagnostics) ++counts[3];
        auto* entry=Entry(id);
        if (entry && (*entry&ExactKnown)) return *entry&ExactYes;
        if (diagnostics) ++counts[4];
        const bool value=evaluate();
        if (entry) *entry|=ExactKnown|(value?ExactYes:0);
        return value;
    }
    template<class PostingEval,class ExactEval,class Live>
    bool Eligible(int id,PostingEval&& posting,ExactEval&& exact,Live&& live) {
        if (diagnostics) { ++counts[0]; ++counts[1]; }
        auto* entry=Entry(id);
        if (!entry) {
            if (diagnostics) ++counts[2];
            if (posting()) return true;
            if (!live()) return false;
            if (diagnostics) { ++counts[3]; ++counts[4]; }
            return exact();
        }
        auto value=*entry;
        if (!(value&PostingKnown)) {
            if (diagnostics) ++counts[2];
            value|=PostingKnown|(posting()?PostingYes:0);
            *entry=value;
        }
        if (value&PostingYes) return true;
        // Mutable deletion is checked even for a cached own-only match.
        if (!live()) return false;
        if (diagnostics) ++counts[3];
        if (!(value&ExactKnown)) {
            if (diagnostics) ++counts[4];
            value|=ExactKnown|(exact()?ExactYes:0);
            *entry=value;
        }
        return value&ExactYes;
    }
};

class QualificationLease {
    std::unique_ptr<Qualification> nested;
    Qualification* value=nullptr;
public:
    QualificationLease(bool enabled,int size,bool diagnostics) {
        if (!enabled) return;
        static thread_local Qualification reusable;
        if (reusable.busy) {
            nested=std::make_unique<Qualification>();
            value=nested.get();
        } else value=&reusable;
        value->Begin(size,diagnostics);
        value->busy=true;
    }
    ~QualificationLease() { if (value) value->busy=false; }
    QualificationLease(const QualificationLease&)=delete;
    QualificationLease& operator=(const QualificationLease&)=delete;
    Qualification* Get() const { return value; }
};
}
