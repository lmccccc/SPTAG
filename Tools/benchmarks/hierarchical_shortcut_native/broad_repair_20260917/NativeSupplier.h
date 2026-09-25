#pragma once
#include "FullHooks.h"
#include "FilterCost.h"
#include "PostingSupplier.h"
#include "Signature.h"
#include "inc/Core/BKT/Index.h"
#include <memory>

namespace NativeReuse {
inline bool Enabled() { return ShortcutFull::mode != "ordinary"; }
template<class T, class Catalogs, class Postings, class Posting, class Own, class Traversal, class Signature>
SPTAG::ErrorCode Search(SPTAG::BKT::Index<T>* bkt, SPTAG::COMMON::QueryResultSet<T>* result,
                       PostingModel* model, const Catalogs& catalogs, const Postings& postings,
                       Posting posting, Own own, Traversal traversal, Signature signature,
                       bool defaultAdmission = false,
                       std::function<SPTAG::COMMON::NativeEligibility(int)> qualify = {}) {
    using namespace SPTAG::COMMON;
    NativeNeighborHooks hooks;
    hooks.diagnostics = ShortcutFull::profile || ShortcutFull::capture;
    hooks.capture = ShortcutFull::capture;
    hooks.retainedRatio = ShortcutFull::retainedRatio;
    hooks.minimumPhysicalDegree = ShortcutFull::minBaseDegree;
    hooks.defaultAdmission = !Enabled() || defaultAdmission;
    std::unique_ptr<PostingSupplier> supplier;
    const auto getSupplier = [&]() -> PostingSupplier& {
        if (!supplier) {
            if (!model || model->counts[0] != bkt->GetNumSamples() ||
                catalogs.size() != 2 || postings.size() != 2)
                throw std::runtime_error("Native owner metadata was not prepared during load");
            supplier = std::make_unique<PostingSupplier>(*model, hooks, signature,
                [&](int level, int id) {
                    return bkt->ComputeDistance(result->GetQuantizedTarget(),
                                               catalogs[level - 1]->GetVector(id));
                });
        }
        return *supplier;
    };
    std::function<bool(int)> resultFilter, traversalFilter;
    if (Enabled() && (!defaultAdmission || hooks.capture)) {
        if (!defaultAdmission) {
            resultFilter = posting;
            traversalFilter = traversal;
            hooks.ownPoint = [&](int id, float distance) { own(id, &distance); };
            hooks.qualify = std::move(qualify);
        }
        if (ShortcutFull::mode != "control") {
        FilterCost::Last().observer = true;
        hooks.completeOrdinary = [&](NativeDegreeFrame& frame, const int* row, int width,
                                     const NativeEdgeConsumer& native) {
            const bool eligibleDegree = frame.physical >= ShortcutFull::minBaseDegree;
            frame.required = eligibleDegree ? static_cast<int>(
                std::ceil(ShortcutFull::retainedRatio * frame.physical)) : 0;
            if (eligibleDegree && frame.eligible < ShortcutFull::retainedRatio * frame.physical &&
                ShortcutFull::mode == "supplier" && native.withinBudget()) {
                std::unordered_set<int> identities{frame.head};
                for (int i = 0; i < width && row[i] >= 0; ++i)
                    if (row[i] < bkt->GetNumSamples()) identities.insert(row[i]);
                const auto before = hooks.calls;
                getSupplier().Supply(frame.head, frame.required - frame.eligible, identities,
                                     traversal, frame, native);
                frame.calls = hooks.calls - before;
            }
            frame.after = native.checked();
            if (hooks.capture) hooks.frames.push_back(std::move(frame));
        };
        hooks.expand = [&](int head, const int* row, int width, bool startup,
                           const NativeEdgeConsumer& native) {
            FilterCost::Count(&FilterCost::Counters::degreeRows);
            NativeDegreeFrame frame{head, 0, 0, 0, native.checked(), 0, startup, 0, {}, {}};
            for (int i = 0; i < width && row[i] >= 0; ++i) {
                FilterCost::Count(&FilterCost::Counters::physicalMembers);
                const int id = row[i];
                if (id < bkt->GetNumSamples() && id != head &&
                    std::find(row, row + i, id) == row + i) ++frame.physical;
            }
            const bool eligibleDegree = frame.physical >= ShortcutFull::minBaseDegree;
            if (eligibleDegree || hooks.capture) {
                for (int i = 0; i < width && row[i] >= 0; ++i) {
                    const int id = row[i];
                    if (id >= bkt->GetNumSamples() || id == head ||
                        std::find(row, row + i, id) != row + i) continue;
                    const bool allowed = defaultAdmission || traversal(id);
                    if (!defaultAdmission) ++hooks.rowEligibilityEvaluations;
                    frame.eligible += allowed;
                    if (hooks.capture) frame.ordinary.emplace_back(id, allowed);
                }
            }
            frame.required = eligibleDegree ? static_cast<int>(
                std::ceil(ShortcutFull::retainedRatio * frame.physical)) : 0;
            if (eligibleDegree && frame.eligible < ShortcutFull::retainedRatio * frame.physical &&
                ShortcutFull::mode == "supplier" && native.withinBudget()) {
                if (startup) {
                    for (int i = 0; i < width && row[i] >= 0; ++i)
                        if (row[i] < bkt->GetNumSamples() && row[i] != head)
                            native.consume(row[i], false);
                }
                if (native.withinBudget()) {
                    std::unordered_set<int> identities;
                    identities.insert(head);
                    for (int i = 0; i < width && row[i] >= 0; ++i)
                        if (row[i] < bkt->GetNumSamples()) identities.insert(row[i]);
                    const auto before = hooks.calls;
                    getSupplier().Supply(head, frame.required - frame.eligible, identities,
                                         traversal, frame, native);
                    frame.calls = hooks.calls - before;
                }
            }
            frame.after = native.checked();
            if (hooks.capture) hooks.frames.push_back(std::move(frame));
        };
        }
    }
    const auto status = bkt->SearchIndexWithNativeHooks(*result, hooks, resultFilter, traversalFilter, 0);
    hooks.ownPoint = {};
    hooks.qualify = {};
    hooks.expand = {};
    hooks.completeOrdinary = {};
    ShortcutFull::Last().native = std::move(hooks);
    ShortcutFull::Last().invoked = true;
    return status;
}
}
