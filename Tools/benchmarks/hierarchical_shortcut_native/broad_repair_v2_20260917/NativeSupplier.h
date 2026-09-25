#pragma once
#include "FullHooks.h"
#include "FilterCost.h"
#include "PostingSupplier.h"
#include "Signature.h"
#include "inc/Core/BKT/Index.h"
#include <optional>

namespace NativeReuse {
inline bool Enabled() { return ShortcutFull::mode != "ordinary"; }
template<class T, class Catalogs, class Postings, class Posting, class Own, class Traversal,
         class Signature, class Qualifier = std::function<SPTAG::COMMON::NativeEligibility(int)>>
SPTAG::ErrorCode Search(SPTAG::BKT::Index<T>* bkt, SPTAG::COMMON::QueryResultSet<T>* result,
                       PostingModel* model, const Catalogs& catalogs, const Postings& postings,
                       Posting posting, Own own, Traversal traversal, Signature signature,
                       bool defaultAdmission = false, Qualifier qualify = {},
                       SPTAG::COMMON::NativeFunctionRef<void(int, float, SPTAG::COMMON::NativeEligibility*)>
                           ownQualified = {}, bool pureQualification = false) {
    using namespace SPTAG::COMMON;
    if ((!Enabled() || defaultAdmission) && !ShortcutFull::capture && !ShortcutFull::profile)
        return bkt->SearchIndex(*result);
    NativeNeighborHooks hooks;
    hooks.diagnostics = ShortcutFull::profile || ShortcutFull::capture;
    hooks.capture = ShortcutFull::capture;
    hooks.retainedRatio = ShortcutFull::retainedRatio;
    hooks.minimumPhysicalDegree = ShortcutFull::minBaseDegree;
    hooks.defaultAdmission = !Enabled() || defaultAdmission;
    hooks.pureQualification = pureQualification && !hooks.defaultAdmission;
    auto parentDistance = [&](int level, int id) {
        return bkt->ComputeDistance(result->GetQuantizedTarget(), catalogs[level - 1]->GetVector(id));
    };
    std::optional<PostingSupplier> supplier;
    auto getSupplier = [&]() -> PostingSupplier& {
        if (!supplier) {
            if (!model || model->counts[0] != bkt->GetNumSamples() ||
                catalogs.size() != 2 || postings.size() != 2)
                throw std::runtime_error("Native owner metadata was not prepared during load");
            supplier.emplace(*model, hooks, signature, parentDistance);
        }
        return *supplier;
    };
    std::vector<int> scratch;
    if (Enabled() && (!defaultAdmission || hooks.capture))
        scratch.resize(2 * bkt->GetNeighborhoodSize() + 1);
    auto supply = [&](NativeDegreeFrame& frame, const int* row, int width,
                      const NativeEdgeConsumer& native) {
        NativeDeficitIdentities identities(scratch.data(), static_cast<int>(scratch.size()), hooks.capture);
        identities.Seed(frame.head);
        for (int i = 0; i < width && row[i] >= 0; ++i)
            if (row[i] < bkt->GetNumSamples()) identities.Seed(row[i]);
        const auto before = hooks.calls;
        getSupplier().Supply(frame.head, frame.required - frame.eligible, identities, traversal, frame, native);
        frame.calls = hooks.calls - before;
    };
    auto completeOrdinary = [&](NativeDegreeFrame& frame, const int* row, int width,
                                const NativeEdgeConsumer& native) {
        const bool eligibleDegree = frame.physical >= ShortcutFull::minBaseDegree;
        frame.required = eligibleDegree ? static_cast<int>(
            std::ceil(ShortcutFull::retainedRatio * frame.physical)) : 0;
        if (eligibleDegree && frame.eligible < ShortcutFull::retainedRatio * frame.physical &&
            ShortcutFull::mode == "supplier" && native.withinBudget())
            supply(frame, row, width, native);
        frame.after = native.checked();
        if (hooks.capture) hooks.frames.push_back(std::move(frame));
    };
    auto expand = [&](int head, const int* row, int width, bool startup, const NativeEdgeConsumer& native) {
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
                    if (row[i] < bkt->GetNumSamples() && row[i] != head) native.consume(row[i], false);
            }
            if (native.withinBudget()) supply(frame, row, width, native);
        }
        frame.after = native.checked();
        if (hooks.capture) hooks.frames.push_back(std::move(frame));
    };
    std::function<bool(int)> resultFilter, traversalFilter;
    if (Enabled() && (!defaultAdmission || hooks.capture)) {
        if (!defaultAdmission) {
            resultFilter = posting;
            traversalFilter = traversal;
            hooks.ownPoint = [&](int id, float distance) { own(id, &distance); };
            hooks.ownQualified = ownQualified;
            hooks.qualify = qualify;
        }
        if (ShortcutFull::mode != "control") {
            FilterCost::Last().observer = true;
            hooks.completeOrdinary = completeOrdinary;
            hooks.expand = expand;
        }
    }
    const auto status = bkt->SearchIndexWithNativeHooks(*result, hooks, resultFilter, traversalFilter, 0);
    hooks.ownPoint = {};
    hooks.ownQualified = {};
    hooks.qualify = {};
    hooks.expand = {};
    hooks.completeOrdinary = {};
    ShortcutFull::Last().native = std::move(hooks);
    ShortcutFull::Last().invoked = true;
    return status;
}
}
