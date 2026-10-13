#ifndef SPTAG_QUERY_STEP_TIMING_H
#define SPTAG_QUERY_STEP_TIMING_H

#include <array>
#include <chrono>
#include <cstdint>
#include <stdexcept>
#include <utility>

namespace SPTAG { namespace COMMON { namespace StepTiming {
enum Stage : std::size_t {
    Wrapper, HeadPrepare, H1, Hierarchy, PostingPrepare, AIOPrepare, AIOLock,
    AIOSubmit, AIOWait, Scan, FilterDedup, Distance, TopK, Finalize, Decompress, Count
};
inline constexpr const char* Names[] = {
    "wrapper", "head_prepare", "h1_search", "hierarchy_supplement", "posting_prepare",
    "aio_prepare", "aio_lock_wait", "aio_submit", "aio_completion_wait", "scan_other",
    "filter_dedup", "distance", "topk_update", "finalize", "decompress"
};
using Values = std::array<std::uint64_t, Count>;
#ifdef SPTAG_QUERY_STEP_TIMING
using Clock = std::chrono::steady_clock;
struct Trace {
    Values values{};
    Stage stage = Wrapper;
    Clock::time_point previous{};
    void Switch(Stage next) {
        const auto now = Clock::now();
        values[stage] += std::chrono::duration_cast<std::chrono::nanoseconds>(now - previous).count();
        previous = now;
        stage = next;
    }
};
inline thread_local Trace* current = nullptr;
class Query {
    Trace trace;
    bool active;
public:
    explicit Query(bool enabled) : active(enabled) {
        if (active) {
            if (current) throw std::logic_error("Nested query step timing");
            trace.previous = Clock::now();
            current = &trace;
        }
    }
    Values Finish() {
        if (!active) return {};
        if (current != &trace || trace.stage != Wrapper)
            throw std::logic_error("Unbalanced query step timing");
        trace.Switch(Wrapper);
        current = nullptr;
        active = false;
        return trace.values;
    }
    ~Query() { if (active) current = nullptr; }
};
class Scope {
    Trace* trace;
    Stage previous;
public:
    explicit Scope(Stage stage) : trace(current), previous(trace ? trace->stage : Wrapper) {
        if (trace) trace->Switch(stage);
    }
    ~Scope() { if (trace) trace->Switch(previous); }
};
template<class F> decltype(auto) Call(Stage stage, F&& function) {
    Scope scope(stage);
    return std::forward<F>(function)();
}
#else
class Query {
public:
    explicit Query(bool enabled) {
        if (enabled) throw std::logic_error("Build with SPTAG_QUERY_STEP_TIMING=ON");
    }
    Values Finish() { return {}; }
};
#endif
}}}

#define SPTAG_STEP_JOIN_(a, b) a##b
#define SPTAG_STEP_JOIN(a, b) SPTAG_STEP_JOIN_(a, b)
#ifdef SPTAG_QUERY_STEP_TIMING
#define SPTAG_STEP_SCOPE(stage) ::SPTAG::COMMON::StepTiming::Scope \
    SPTAG_STEP_JOIN(stepScope_, __LINE__)(::SPTAG::COMMON::StepTiming::stage)
#define SPTAG_STEP_CALL(stage, expression) ::SPTAG::COMMON::StepTiming::Call( \
    ::SPTAG::COMMON::StepTiming::stage, [&]() -> decltype(auto) { return (expression); })
#else
#define SPTAG_STEP_SCOPE(stage) do {} while (false)
#define SPTAG_STEP_CALL(stage, expression) (expression)
#endif
#endif
