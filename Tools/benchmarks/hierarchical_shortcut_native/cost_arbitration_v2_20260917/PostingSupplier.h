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
    struct RowState {
        unsigned char status=0;
        bool scored=false, discovered=false;
        float spatial=0;
        std::uint64_t considered=0;
    };
    std::array<std::vector<RowState>,2> state;
    std::vector<int> discovered;
    std::uint64_t generation=0;
    std::array<CostArbitration::Work,3> history{};
    bool Allowed(int level,int id) {
        auto& s=state[level-1][id].status;
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
    CostArbitration::Prediction PredictRow(int level,int id,double remaining,
            const std::array<CostArbitration::Probability,3>& probabilities,int source,
            double& setup) const {
        const double length=level==1 ? model.lengths[0][id] :
            (model.lengths[1][id]?model.meanH1Row:0);
        // The candidate representative is sunk once scored; H3's hypothetical H2
        // representative is still unpaid. Descriptor inspection is a full row.
        setup=config.postingSetup+(state[level-1][id].scored?0:config.distance)+
            (level==2 ? config.postingSetup+config.distance+
                model.lengths[1][id]*(config.member+config.signature) : 0);
        return CostArbitration::Predict(config,probabilities[source],length,remaining,setup);
    }
    float Representative(int level,int id) {
        auto& row=state[level-1][id];
        if (row.scored) return row.spatial;
        const float d=distance(level,id);
        if (!std::isfinite(d) || d<0) throw std::runtime_error("Invalid representative distance");
        ++hooks.parentDistances;
        row.scored=true; row.spatial=d;
        return d;
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
        state[0][id].status=2;
        if (hooks.capture) hooks.rows.push_back({1,id,model.lengths[0][id],work.members});
        return work;
    }
public:
    PostingSupplier(PostingModel& m,SPTAG::COMMON::NativeNeighborHooks& h,
        SPTAG::COMMON::NativeFunctionRef<bool(int,int)> s,
        SPTAG::COMMON::NativeFunctionRef<float(int,int)> d,CostArbitration::Config c)
        :model(m),hooks(h),signature(s),distance(d),config(c) {
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
        const auto before=hooks.parentDistances;
        const std::array<CostArbitration::Probability,3> probabilities{
            CostArbitration::Probabilities(config,history[0],config.graphNovelty),
            CostArbitration::Probabilities(config,history[1],config.postingNovelty),
            CostArbitration::Probabilities(config,history[2],config.discoveryNovelty)};
        decision.graph=CostArbitration::Predict(config,probabilities[0],
                                               width,decision.remaining,config.graphSetup);
        if (decision.graph.novelty>0 && width*decision.graph.novelty>decision.remainingChecks)
            decision.graph=CostArbitration::Predict(config,probabilities[0],
                decision.remainingChecks/decision.graph.novelty,decision.remaining,config.graphSetup);
        const auto publish=[&] {
            decision.checkedAfter=native.checked();
            decision.representativeDistances=hooks.parentDistances-before;
            if (hooks.capture) hooks.decisions.push_back(decision);
        };
        if (ShortcutFull::mode=="graph" || !native.withinBudget()) {
            ++hooks.graphDecisions; publish(); return;
        }
        ++hooks.estimates;
        ++generation;
        struct Candidate {
            int id=-1,source=0;
            float spatial=0;
            double setup=0;
            CostArbitration::Prediction prediction;
        };
        std::array<Candidate,2> candidates;
        const auto consider=[&](int level,int id) {
            auto& row=state[level-1][id];
            if (row.status>=2 || row.considered==generation) return;
            row.considered=generation;
            const int source=level==2 || row.discovered?2:1;
            double setup=0;
            auto prediction=PredictRow(level,id,decision.remaining,probabilities,source,setup);
            // Optimistic unpaid cost is a valid screen, even before paying the
            // representative. No degree, selectivity or observed-success gate.
            if (!(prediction.gain>0 && prediction.Score()<decision.graph.Score())) return;
            if (!Allowed(level,id)) return;
            const bool unpaid=!row.scored;
            const float spatial=Representative(level,id);
            if (unpaid) { setup-=config.distance; prediction.cost-=config.distance; }
            auto& best=candidates[level-1];
            // Native distance-only nearest-parent ordering within an economic
            // family; geometry is not mixed into the operation-count score.
            if (best.id<0 || std::make_pair(spatial,id)<std::make_pair(best.spatial,best.id))
                best={id,source,spatial,setup,prediction};
        };
        const auto& owners=model.owners[0].at(head);
        for (int id:owners) consider(1,id);
        discovered.erase(std::remove_if(discovered.begin(),discovered.end(),[&](int id) {
            return state[0][id].status>=2;
        }),discovered.end());
        for (int id:discovered) consider(1,id);
        // A zero-descriptor lower bound avoids inspecting upper owners that cannot win.
        const auto lower=CostArbitration::Predict(config,probabilities[2],
            model.meanH1Row,decision.remaining,2*config.postingSetup);
        if (lower.gain>0 && lower.Score()<(std::min)(decision.graph.Score(),candidates[0].prediction.Score())) {
            for (int id:owners) for (int upper:model.owners[1][id]) consider(2,upper);
        }
        for (int level=1;level<=2;++level) {
            const auto& candidate=candidates[level-1];
            if (candidate.id>=0 && (decision.row<0 || candidate.prediction.Score()<decision.posting.Score())) {
                decision.level=level; decision.row=candidate.id; decision.source=candidate.source;
                decision.posting=candidate.prediction; decision.postingSetup=candidate.setup;
                decision.selectedDistance=candidate.spatial;
            }
        }
        if (decision.row<0 || decision.posting.Score()>=decision.graph.Score() ||
            ShortcutFull::mode=="estimate_only") {
            ++hooks.graphDecisions; publish(); return;
        }
        decision.chosen=decision.level;
        ++hooks.calls;
        if (decision.level==1) {
            ++hooks.postingDecisions;
            decision.retainedChosen=state[0][decision.row].discovered;
            decision.realized=OpenH1(decision.row,decision.source,native);
        } else {
            ++hooks.discoveryDecisions;
            Representative(2,decision.row);
            const auto row=model.children(2,decision.row);
            ++hooks.h3Rows;
            int best=-1;
            float bestDistance=0;
            CostArbitration::Prediction bestPrediction;
            for (auto p=row.first;p!=row.second;++p) {
                ++decision.descriptorMembers; ++hooks.members;
                if (!Allowed(1,*p)) continue;
                auto& metadata=state[0][*p];
                if (!metadata.discovered) {
                    metadata.discovered=true; discovered.push_back(*p); ++decision.retainedCount;
                }
                double setup=0;
                auto prediction=PredictRow(1,*p,decision.remaining,probabilities,2,setup);
                if (!(prediction.gain>0 && prediction.Score()<decision.graph.Score())) continue;
                const bool unpaid=!metadata.scored;
                const auto spatial=Representative(1,*p);
                if (unpaid) { setup-=config.distance; prediction.cost-=config.distance; }
                if (best<0 || std::make_pair(spatial,static_cast<int>(*p))<std::make_pair(bestDistance,best)) {
                    best=*p; bestPrediction=prediction; bestDistance=spatial;
                    decision.tailSetup=setup; decision.tailDistance=spatial;
                }
            }
            state[1][decision.row].status=2;
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
        ++hooks.returns;
        publish();
    }
};
}
