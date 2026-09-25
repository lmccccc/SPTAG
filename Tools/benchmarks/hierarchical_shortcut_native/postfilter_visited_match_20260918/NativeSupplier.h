#pragma once
#include "PostingSupplier.h"
#include "Signature.h"
#include "inc/Core/BKT/Index.h"
#include <optional>

namespace NativeReuse {
inline bool Enabled() { return true; }
template<class T,class Catalogs,class Postings,class Posting,class Own,class Signature,class Eligible>
SPTAG::ErrorCode Search(SPTAG::BKT::Index<T>* bkt,SPTAG::COMMON::QueryResultSet<T>* result,
    PostingModel* model,const Catalogs& catalogs,const Postings& postings,Posting posting,Own own,
    Signature signature,bool emptyPredicate,Eligible eligible) {
    using namespace SPTAG::COMMON;
    const bool nativeDefault=emptyPredicate || !Enabled();
    if (nativeDefault) return bkt->SearchIndex(*result);
    NativeNeighborHooks hooks;
    hooks.capture=hooks.diagnostics=ShortcutFull::capture;
    hooks.auditVisited=hooks.capture;
    hooks.defaultAdmission=nativeDefault;
    hooks.activationRatio=ShortcutFull::activationRatio;
    hooks.matchEnabled=ShortcutFull::mode!="graph";
    hooks.immutableSnapshot=hooks.matchEnabled;
    std::function<bool(int)> resultFilter;
    if (!nativeDefault) {
        resultFilter=posting; hooks.ownGain=own;
        if (hooks.matchEnabled) hooks.auxiliaryEligible=eligible;
        hooks.ownPoint=[&](int id,float d) { own(id,d); };
    }
    auto distance=[&](int level,int id) {
        return bkt->ComputeDistance(result->GetQuantizedTarget(),catalogs[level-1]->GetVector(id));
    };
    std::optional<PostingSupplier> supplier;
    auto expandPosting=[&](int head,const NativeEdgeConsumer& native) {
        if (!supplier) {
            if (!model || model->counts[0]!=bkt->GetNumSamples() || catalogs.size()!=2 || postings.size()!=2)
                throw std::runtime_error("Missing native owner hierarchy");
            supplier.emplace(*model,hooks,signature,distance);
        }
        return supplier->Expand(head,native);
    };
    if (!nativeDefault && ShortcutFull::mode=="posting") hooks.posting=expandPosting;
    const auto status=bkt->SearchIndexWithNativeHooks(*result,hooks,resultFilter,{});
    hooks.ownPoint={}; hooks.ownGain={}; hooks.auxiliaryEligible={}; hooks.posting={};
    ShortcutFull::Last().native=std::move(hooks);
    ShortcutFull::Last().invoked=true;
    return status;
}
}
