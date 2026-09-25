#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <map>
#include <mutex>
#include <signal.h>
#include <sys/time.h>
#include <ucontext.h>
#include <time.h>
#include <sys/syscall.h>
#include <unistd.h>

namespace {
struct Counts {
    std::mutex lock;
    std::map<std::pair<int, std::uintptr_t>, std::uint64_t> values;
};
Counts& counts() { static auto* value = new Counts; return *value; }
volatile sig_atomic_t samples = 0, dropped = 0;
std::uintptr_t pcs[100000];
bool recording = true;
timer_t timer;
bool timerCreated = false;
int boundary = 0;
struct Boundary { int number, capture, profile; };
Boundary flags[64]{};
int flagCount = 0;
timespec cpuStart{}, cpuFinish{};
double sampledCpuSeconds = 0;

void sample(int, siginfo_t*, void* context) {
    const auto* registers = static_cast<ucontext_t*>(context);
    const auto position = samples;
    if (position < 100000) {
        pcs[position] = registers->uc_mcontext.gregs[REG_RIP];
        samples = position + 1;
    } else ++dropped;
}

void count(int kind, void* address) {
    if (!recording) return;
    auto& c = counts();
    std::lock_guard<std::mutex> guard(c.lock);
    ++c.values[{kind, reinterpret_cast<std::uintptr_t>(address)}];
}

void samplingBoundary(int kind, void* address) {
    if (kind != 0) return;
    ++boundary;
    Dl_info image{};
    if (!dladdr(address, &image) || !image.dli_fbase) std::abort();
    const auto base = reinterpret_cast<std::uintptr_t>(image.dli_fbase);
    const bool capture = *reinterpret_cast<const bool*>(base + CAPTURE_OFFSET);
    const bool profile = *reinterpret_cast<const bool*>(base + PROFILE_OFFSET);
    if (flagCount >= 64) std::abort();
    flags[flagCount++] = {boundary, capture, profile};
    const bool begin = boundary >= 5 && (boundary - 5) % 6 == 0;
    const bool end = boundary >= 6 && (boundary - 6) % 6 == 0;
    if (begin && (capture || profile)) std::abort();
#ifdef AUDIT_SAMPLE
    if (begin) {
        struct sigaction action{};
        action.sa_sigaction = sample;
        action.sa_flags = SA_SIGINFO | SA_RESTART;
        sigemptyset(&action.sa_mask);
        if (sigaction(SIGPROF, &action, nullptr)) std::abort();
        if (!timerCreated) {
            sigevent event{};
            event.sigev_notify = SIGEV_THREAD_ID;
            event.sigev_signo = SIGPROF;
            event._sigev_un._tid = static_cast<pid_t>(syscall(SYS_gettid));
            if (timer_create(CLOCK_THREAD_CPUTIME_ID, &event, &timer)) {
                std::perror("timer_create"); std::abort();
            }
            timerCreated = true;
        }
        if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &cpuStart)) std::abort();
        itimerspec period{};
        period.it_interval.tv_nsec = period.it_value.tv_nsec = 250000;
        if (timer_settime(timer, 0, &period, nullptr)) std::abort();
    } else if (end) {
        itimerspec stopped{};
        if (timer_settime(timer, 0, &stopped, nullptr) ||
            clock_gettime(CLOCK_THREAD_CPUTIME_ID, &cpuFinish)) std::abort();
        sampledCpuSeconds += cpuFinish.tv_sec - cpuStart.tv_sec +
            (cpuFinish.tv_nsec - cpuStart.tv_nsec) * 1e-9;
    }
#endif
}

void location(FILE* output, const char* kind, std::uintptr_t pc, std::uint64_t n) {
    Dl_info info{};
    if (!dladdr(reinterpret_cast<void*>(pc), &info) || !info.dli_fname) {
        std::fprintf(output, "%s\tunknown\t0x%lx\t%lu\n", kind, pc, n);
        return;
    }
    const auto offset = pc - reinterpret_cast<std::uintptr_t>(info.dli_fbase);
    std::fprintf(output, "%s\t%s\t0x%lx\t%lu\n", kind, info.dli_fname, offset, n);
}

void finish() {
    recording = false;
    if (timerCreated) timer_delete(timer);
    auto* output = std::fopen("clock_audit.tsv", "w");
    if (!output) std::abort();
    for (const auto& pair : counts().values)
        location(output, pair.first.first ? "system" : "steady", pair.first.second, pair.second);
    std::fclose(output);
    output = std::fopen("ordinary_flags.tsv", "w");
    if (!output) std::abort();
    for (int i = 0; i < flagCount; ++i)
        std::fprintf(output, "%d\t%d\t%d\n", flags[i].number, flags[i].capture, flags[i].profile);
    std::fprintf(output, "thread_cpu_seconds\t%.9f\n", sampledCpuSeconds);
    std::fclose(output);
    output = std::fopen("cpu_samples.tsv", "w");
    if (!output) std::abort();
    std::map<std::uintptr_t, std::uint64_t> frequency;
    for (sig_atomic_t i = 0; i < samples; ++i) ++frequency[pcs[i]];
    for (const auto& pair : frequency) location(output, "pc", pair.first, pair.second);
    std::fprintf(output, "dropped\t-\t0\t%d\n", dropped);
    std::fclose(output);
}

__attribute__((constructor)) void start() {
    std::atexit(finish);
}
}

namespace std { namespace chrono { inline namespace _V2 {
steady_clock::time_point steady_clock::now() noexcept {
    using Function = steady_clock::time_point (*)() noexcept;
    static const auto original = reinterpret_cast<Function>(
        dlsym(RTLD_NEXT, "_ZNSt6chrono3_V212steady_clock3nowEv"));
    if (!original) std::abort();
    count(0, __builtin_return_address(0));
    samplingBoundary(0, __builtin_return_address(0));
    return original();
}
system_clock::time_point system_clock::now() noexcept {
    using Function = system_clock::time_point (*)() noexcept;
    static const auto original = reinterpret_cast<Function>(
        dlsym(RTLD_NEXT, "_ZNSt6chrono3_V212system_clock3nowEv"));
    if (!original) std::abort();
    count(1, __builtin_return_address(0));
    return original();
}
}}}
