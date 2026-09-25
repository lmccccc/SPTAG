#pragma once
#include "PostingSupplier.h"
#include "Signature.h"
#include "inc/Core/BKT/Index.h"
#include <optional>

namespace NativeReuse {
inline bool Enabled() { return ShortcutFull::mode!="ordinary"; }
template<class T,class Catalogs,class Postings,class Posting,class Own,class Signature>
SPTAG::ErrorCode Search(SPTAG::BKT::Index<T>* bkt,SPTAG::COMMON::QueryResultSet<T>* result,
    PostingModel* model,const Catalogs& catalogs,const Postings& postings,Posting posting,Own own,
    Signature signature,bool emptyPredicate) {
    using namespace SPTAG::COMMON;
    const bool nativeDefault=emptyPredicate || !Enabled();
    if (nativeDefault && !ShortcutFull::capture) return bkt->SearchIndex(*result);
    NativeNeighborHooks hooks;
    hooks.capture=hooks.diagnostics=ShortcutFull::capture;
    hooks.defaultAdmission=nativeDefault;
    std::function<bool(int)> resultFilter;
    if (!nativeDefault) {
        resultFilter=posting; hooks.ownGain=own;
        hooks.ownPoint=[&](int id,float d) { own(id,d); };
    }
    auto distance=[&](int level,int id) {
        return bkt->ComputeDistance(result->GetQuantizedTarget(),catalogs[level-1]->GetVector(id));
    };
    std::optional<PostingSupplier> supplier;
    auto afterGraph=[&](int head,const CostArbitration::Work& work,int width,const NativeEdgeConsumer& native) {
        if (!supplier) {
            if (!model || model->counts[0]!=bkt->GetNumSamples() || catalogs.size()!=2 || postings.size()!=2)
                throw std::runtime_error("Missing native owner hierarchy");
            supplier.emplace(*model,hooks,signature,distance,ShortcutFull::costs);
        }
        supplier->AfterGraph(head,work,width,native);
    };
    if (!nativeDefault && (ShortcutFull::mode!="graph" || hooks.capture)) hooks.afterGraph=afterGraph;
    // Result-only filtering: ordinary and auxiliary offers share native navigation.
    const auto status=bkt->SearchIndexWithNativeHooks(*result,hooks,resultFilter,{});
    hooks.ownPoint={}; hooks.ownGain={}; hooks.afterGraph={};
    ShortcutFull::Last().native=std::move(hooks);
    ShortcutFull::Last().invoked=true;
    return status;
}
}
