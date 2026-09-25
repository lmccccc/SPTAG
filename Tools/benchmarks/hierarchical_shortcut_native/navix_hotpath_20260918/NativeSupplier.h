#pragma once
#include "PostingSupplier.h"
#include "Signature.h"
#include "inc/Core/BKT/Index.h"
#include "PredicateCache.h"
#include <optional>

namespace NativeReuse {
inline bool Enabled() { return ShortcutFull::mode!="ordinary"; }
template<class T,class Catalogs,class Postings,class Posting,class Own,class Signature,class Eligible>
SPTAG::ErrorCode Search(SPTAG::BKT::Index<T>* bkt,SPTAG::COMMON::QueryResultSet<T>* result,
    PostingModel* model,const Catalogs& catalogs,const Postings& postings,Posting posting,Own own,
    Signature signature,bool emptyPredicate,Eligible eligible,PredicateCache* cache=nullptr) {
    using namespace SPTAG::COMMON;
    const bool nativeDefault=emptyPredicate || !Enabled();
    if (nativeDefault && !ShortcutFull::capture) return bkt->SearchIndex(*result);
    NativeNeighborHooks hooks;
    hooks.capture=hooks.diagnostics=ShortcutFull::capture;
    hooks.defaultAdmission=nativeDefault;
    hooks.navix=!nativeDefault;
    hooks.postingThreshold=ShortcutFull::mode=="posting"?ShortcutFull::postingThreshold:0;
    std::function<bool(int)> resultFilter;
    auto prefetch=[&](int id) { if (cache) cache->Prefetch(id); };
    if (!nativeDefault) {
        resultFilter=posting; hooks.ownGain=own; hooks.eligible=eligible;
        hooks.ownPoint=[&](int id,float d) { own(id,d); };
        if (cache) hooks.prefetchPredicate=prefetch;
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
    hooks.ownPoint={}; hooks.ownGain={}; hooks.eligible={}; hooks.posting={}; hooks.prefetchPredicate={};
    if (cache && hooks.diagnostics) hooks.predicateCache=cache->counts;
    ShortcutFull::Last().native=std::move(hooks);
    ShortcutFull::Last().invoked=true;
    return status;
}
}
