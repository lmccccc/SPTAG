#pragma once
#include "FullHooks.h"
#include <array>

namespace NativeReuse {
struct PostingModel {
    using Row=std::pair<const std::uint32_t*,const std::uint32_t*>;
    std::array<int,3> counts{};
    std::array<std::vector<std::array<int,8>>,2> owners;
    std::array<std::vector<std::uint32_t>,2> lengths;
    std::function<Row(int,int)> children;
    double meanH1Row=0;
    void BuildOwners() {
        for (int count:counts) if (count<=0) throw std::runtime_error("Empty native hierarchy");
        for (int level=1;level<=2;++level) {
            owners[level-1].resize(counts[level-1]);
            lengths[level-1].resize(counts[level]);
            std::vector<int> sizes(counts[level-1],0);
            std::uint64_t total=0;
            for (int id=0;id<counts[level];++id) {
                const auto row=children(level,id);
                lengths[level-1][id]=row.second-row.first;
                total+=lengths[level-1][id];
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
            if (level==1) meanH1Row=double(total)/counts[level];
        }
    }
};
class PostingSupplier {
    PostingModel& model;
    SPTAG::COMMON::NativeNeighborHooks& hooks;
    SPTAG::COMMON::NativeFunctionRef<bool(int,int)> signature;
    SPTAG::COMMON::NativeFunctionRef<float(int,int)> distance;
    CostArbitration::Config config;
    std::array<std::vector<unsigned char>,2> state;
    std::array<CostArbitration::Work,3> history{};
    bool Allowed(int level,int id) {
        auto& s=state.at(level-1).at(id);
        if (s==2 || s==3) return false;
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
    CostArbitration::Prediction PredictRow(int level,int id,double remaining,int source=0) const {
        const int historyLevel=source?source:level;
        const double length=level==1 ? model.lengths[0][id] :
            (model.lengths[1][id]?model.meanH1Row:0);
        const double setup=config.postingSetup+config.distance+
            (level==2 ? config.postingSetup+config.distance+
                model.lengths[1][id]*(config.member+config.signature) : 0);
        return CostArbitration::Predict(config,history[historyLevel],
            historyLevel==1?config.postingNovelty:config.discoveryNovelty,length,remaining,setup);
    }
    void Representative(int level,int id) {
        const float d=distance(level,id);
        if (!std::isfinite(d) || d<0) throw std::runtime_error("Invalid representative distance");
        ++hooks.parentDistances;
    }
    CostArbitration::Work OpenH1(int id,int source,const SPTAG::COMMON::NativeEdgeConsumer& native) {
        if (!Allowed(1,id) || !native.withinBudget()) return {};
        Representative(1,id);
        const auto row=model.children(1,id);
        ++hooks.h2Rows;
        const auto work=native.consumeRow(row.first,static_cast<int>(row.second-row.first));
        hooks.members+=work.members;
        hooks.postingWork+=work;
        history[source]+=work;
        state[0][id]=2;
        if (hooks.capture) hooks.rows.push_back({1,id,model.lengths[0][id],work.members});
        return work;
    }
public:
    PostingSupplier(PostingModel& m,SPTAG::COMMON::NativeNeighborHooks& h,
        SPTAG::COMMON::NativeFunctionRef<bool(int,int)> s,
        SPTAG::COMMON::NativeFunctionRef<float(int,int)> d,CostArbitration::Config c)
        :model(m),hooks(h),signature(s),distance(d),config(c) {
        config.Validate();
        state[0].resize(m.counts[1]); state[1].resize(m.counts[2]);
    }
    void AfterGraph(int head,const CostArbitration::Work& work,int width,
                    const SPTAG::COMMON::NativeEdgeConsumer& native) {
        history[0]+=work; hooks.graphWork+=work;
        CostArbitration::Decision decision;
        decision.head=head; decision.previousGraph=work;
        decision.checkedBefore=native.checked();
        decision.remaining=native.remaining();
        decision.remainingChecks=native.remainingChecks();
        decision.graph=CostArbitration::Predict(config,history[0],config.graphNovelty,
                                               width,decision.remaining,config.graphSetup);
        if (decision.graph.novelty>0 && width*decision.graph.novelty>decision.remainingChecks)
            decision.graph=CostArbitration::Predict(config,history[0],config.graphNovelty,
                decision.remainingChecks/decision.graph.novelty,decision.remaining,config.graphSetup);
        const auto publish=[&] {
            decision.checkedAfter=native.checked();
            if (hooks.capture) hooks.decisions.push_back(decision);
        };
        if (ShortcutFull::mode=="graph" || !native.withinBudget() || !history[0].useful) {
            ++hooks.graphDecisions; publish(); return;
        }
        ++hooks.estimates;
        const auto consider=[&](int level,int id) {
            if (!Allowed(level,id)) return;
            const auto prediction=PredictRow(level,id,decision.remaining);
            if (prediction.gain>0 && (decision.row<0 || prediction.Score()<decision.posting.Score() ||
                (prediction.Score()==decision.posting.Score() && std::make_pair(level,id)<
                    std::make_pair(decision.level,decision.row)))) {
                decision.level=level; decision.row=id; decision.posting=prediction;
            }
        };
        const auto& owners=model.owners[0].at(head);
        for (int id:owners) consider(1,id);
        // A zero-descriptor lower bound avoids inspecting upper owners that cannot win.
        const auto lower=CostArbitration::Predict(config,history[2],config.discoveryNovelty,
            model.meanH1Row,decision.remaining,2*(config.postingSetup+config.distance));
        if (lower.gain>0 && lower.Score()<(std::min)(decision.graph.Score(),decision.posting.Score())) {
            std::array<int,64> upper;
            auto end=upper.begin();
            for (int id:owners) end=std::copy(model.owners[1][id].begin(),model.owners[1][id].end(),end);
            std::sort(upper.begin(),end); end=std::unique(upper.begin(),end);
            for (auto p=upper.begin();p!=end;++p) consider(2,*p);
        }
        if (decision.row<0 || decision.posting.Score()>=decision.graph.Score() ||
            ShortcutFull::mode=="estimate_only") {
            ++hooks.graphDecisions; publish(); return;
        }
        decision.chosen=decision.level;
        ++hooks.calls;
        const auto before=hooks.parentDistances;
        if (decision.level==1) {
            ++hooks.postingDecisions;
            decision.realized=OpenH1(decision.row,1,native);
        } else {
            ++hooks.discoveryDecisions;
            Representative(2,decision.row);
            const auto row=model.children(2,decision.row);
            ++hooks.h3Rows;
            int best=-1;
            CostArbitration::Prediction bestPrediction;
            for (auto p=row.first;p!=row.second;++p) {
                ++decision.descriptorMembers; ++hooks.members;
                if (!Allowed(1,*p)) continue;
                const auto prediction=PredictRow(1,*p,decision.remaining,2);
                if (prediction.gain>0 && (best<0 || prediction.Score()<bestPrediction.Score() ||
                    (prediction.Score()==bestPrediction.Score() && static_cast<int>(*p)<best))) {
                    best=*p; bestPrediction=prediction;
                }
            }
            state[1][decision.row]=2;
            decision.childRow=best;
            decision.tailPosting=bestPrediction;
            if (hooks.capture) hooks.rows.push_back({2,decision.row,model.lengths[1][decision.row],
                                                    decision.descriptorMembers});
            // Discovery is paid now; compare only the remaining H2 action with graph continuation.
            if (best>=0 && bestPrediction.Score()<decision.graph.Score() && native.withinBudget()) {
                ++hooks.postingDecisions;
                decision.realized=OpenH1(best,2,native);
            }
        }
        decision.representativeDistances=hooks.parentDistances-before;
        ++hooks.returns;
        publish();
    }
};
}
