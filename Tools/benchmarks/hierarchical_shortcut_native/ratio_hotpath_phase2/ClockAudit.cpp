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

namespace {
struct Counts {
    std::mutex lock;
    std::map<std::pair<int, std::uintptr_t>, std::uint64_t> values;
};
Counts& counts() { static auto* value = new Counts; return *value; }
volatile sig_atomic_t samples = 0, dropped = 0;
std::uintptr_t pcs[100000];
bool recording = true;

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

void samplingBoundary(int kind) {
#ifdef AUDIT_SAMPLE
    if (kind != 0) return;
    static int boundary = 0;
    if (++boundary == 1) {
        struct sigaction action{};
        action.sa_sigaction = sample;
        action.sa_flags = SA_SIGINFO | SA_RESTART;
        sigemptyset(&action.sa_mask);
        sigaction(SIGPROF, &action, nullptr);
        itimerval timer{};
        timer.it_interval.tv_usec = timer.it_value.tv_usec = 1000;
        setitimer(ITIMER_PROF, &timer, nullptr);
    } else if (boundary == 2) {
        itimerval stopped{};
        setitimer(ITIMER_PROF, &stopped, nullptr);
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
    itimerval stopped{};
    setitimer(ITIMER_PROF, &stopped, nullptr);
    auto* output = std::fopen("clock_audit.tsv", "w");
    if (!output) std::abort();
    for (const auto& pair : counts().values)
        location(output, pair.first.first ? "system" : "steady", pair.first.second, pair.second);
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
    samplingBoundary(0);
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
