#pragma once
#include "PostingSupplier.h"
#include "Signature.h"
#include "inc/Core/BKT/Index.h"
#include <optional>

namespace NativeReuse {
inline bool Enabled() { return true; }
template<class T,class Catalogs,class Postings,class Signature,class Predicate>
SPTAG::ErrorCode Search(SPTAG::BKT::Index<T>* bkt,SPTAG::COMMON::QueryResultSet<T>* result,
    PostingModel* model,const Catalogs& catalogs,const Postings& postings,
    Signature signature,bool emptyPredicate,Predicate predicate) {
    using namespace SPTAG::COMMON;
    if (emptyPredicate) return bkt->SearchIndex(*result);
    NativeNeighborHooks hooks;
    hooks.capture=hooks.diagnostics=ShortcutFull::capture;
    hooks.auditVisited=hooks.capture;
    hooks.defaultAdmission=false;
    hooks.activationRatio=ShortcutFull::activationRatio;
    hooks.matchEnabled=ShortcutFull::mode!="graph";
    hooks.immutableSnapshot=hooks.matchEnabled;
    const std::function<bool(int)> resultFilter=predicate;
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
    if (ShortcutFull::mode=="posting") hooks.posting=expandPosting;
    const auto status=bkt->SearchIndexWithNativeHooks(*result,hooks,resultFilter,{});
    hooks.posting={};
    if (hooks.capture) {
        ShortcutFull::Last().heads.clear();
        ShortcutFull::Last().distances.clear();
        ShortcutFull::Last().ownIDs.clear();
        ShortcutFull::Last().selectedHeads=ShortcutFull::Last().matchingHeads=0;
        ShortcutFull::Last().nativeSelectedHeadRecords.clear();
    }
    ShortcutFull::Last().native=std::move(hooks);
    ShortcutFull::Last().invoked=true;
    return status;
}
}
