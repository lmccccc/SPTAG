#pragma once
#include "FullHooks.h"
#include <array>

namespace NativeReuse {
struct PostingModel {
    using Row=std::pair<const std::uint32_t*,const std::uint32_t*>;
    std::array<int,3> counts{};
    std::array<std::vector<std::array<int,8>>,2> owners;
    std::function<Row(int,int)> children;
    void BuildOwners() {
        for (int count:counts) if (count<=0) throw std::runtime_error("Empty native hierarchy");
        for (int level=1;level<=2;++level) {
            owners[level-1].resize(counts[level-1]);
            std::vector<int> sizes(counts[level-1],0);
            for (int id=0;id<counts[level];++id) {
                const auto row=children(level,id);
                for (auto p=row.first;p!=row.second;++p) {
                    if (*p>=static_cast<unsigned>(counts[level-1])) throw std::runtime_error("Invalid owner");
                    auto& out=owners[level-1][*p];
                    auto& n=sizes[*p];
                    if (n==8 || std::find(out.begin(),out.begin()+n,id)!=out.begin()+n)
                        throw std::runtime_error("Duplicate or excess owner");
                    out[n++]=id;
                }
            }
            for (int n:sizes) if (n!=8) throw std::runtime_error("Expected original eight owners");
        }
    }
};
class PostingSupplier {
    PostingModel& model;
    SPTAG::COMMON::NativeNeighborHooks& hooks;
    SPTAG::COMMON::NativeFunctionRef<bool(int,int)> signature;
    SPTAG::COMMON::NativeFunctionRef<float(int,int)> distance;
    struct State { unsigned char status=0; bool scored=false,retained=false; float spatial=0; };
    std::array<std::vector<State>,2> state;
    std::vector<int> discovered;
    bool Allowed(int level,int id) {
        auto& s=state[level-1][id].status;
        if (s>=2) return false;
        if (s==1) return true;
        ++hooks.signatureChecks;
        const bool allowed=signature(level,id);
        s=allowed?1:3;
        if (!allowed) {
            ++hooks.signatureRejects;
            if (hooks.capture) hooks.rejectedRows.emplace_back(level,id);
        }
        return allowed;
    }
    float Representative(int level,int id) {
        auto& s=state[level-1][id];
        if (!s.scored) {
            s.spatial=distance(level,id);
            if (!std::isfinite(s.spatial) || s.spatial<0) throw std::runtime_error("Invalid representative");
            s.scored=true; ++hooks.parentDistances;
        }
        return s.spatial;
    }
    template<class Range> int Nearest(int level,const Range& ids,int best=-1) {
        float closest=best<0?0:Representative(level,best);
        for (int id:ids) if (Allowed(level,id)) {
            const auto d=Representative(level,id);
            if (best<0 || std::make_pair(d,id)<std::make_pair(closest,best)) { best=id; closest=d; }
        }
        return best;
    }
    bool Open(int id,const SPTAG::COMMON::NativeEdgeConsumer& native) {
        if (!native.withinBudget()) return false;
        ++hooks.selectedActions;
        const auto row=model.children(1,id);
        const auto n=row.second-row.first;
        ++hooks.h2Rows;
        const auto w=native.consumeRow(row.first,static_cast<int>(n));
        state[0][id].status=2;
        hooks.members+=w.members; hooks.postingWork+=w;
        if (hooks.capture) hooks.rows.push_back({1,id,static_cast<std::uint64_t>(n),w.members});
        return w.fresh>0;
    }
public:
    PostingSupplier(PostingModel& m,SPTAG::COMMON::NativeNeighborHooks& h,
        SPTAG::COMMON::NativeFunctionRef<bool(int,int)> s,
        SPTAG::COMMON::NativeFunctionRef<float(int,int)> d):model(m),hooks(h),signature(s),distance(d) {
        state[0].resize(m.counts[1]); state[1].resize(m.counts[2]);
        discovered.reserve(m.counts[1]);
    }
    bool Expand(int head,const SPTAG::COMMON::NativeEdgeConsumer& native) {
        ++hooks.calls;
        const auto& owners=model.owners[0].at(head);
        int best=Nearest(1,discovered,Nearest(1,owners));
        if (best<0) {
            std::array<int,64> upper{};
            int at=0;
            for (int id:owners) for (int parent:model.owners[1][id]) upper[at++]=parent;
            std::sort(upper.begin(),upper.end());
            const int top=Nearest(2,upper);
            if (top>=0) {
                const auto row=model.children(2,top);
                ++hooks.h3Rows;
                std::uint64_t n=0;
                for (auto p=row.first;p!=row.second;++p) {
                    ++n;
                    if (Allowed(1,*p) && !state[0][*p].retained) {
                        state[0][*p].retained=true;
                        discovered.push_back(*p);
                    }
                }
                state[1][top].status=2; hooks.members+=n;
                if (hooks.capture) hooks.rows.push_back({2,top,n,n});
                best=Nearest(1,discovered);
            }
        }
        const bool used=best>=0 && Open(best,native);
        ++hooks.returns;
        return used;
    }
};
}
