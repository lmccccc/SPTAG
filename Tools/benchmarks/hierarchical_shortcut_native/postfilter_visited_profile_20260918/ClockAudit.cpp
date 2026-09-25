#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <signal.h>
#include <sys/syscall.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>

namespace {
constexpr int capacity=100000;
volatile sig_atomic_t active=0,samples=0,dropped=0,wrongThread=0;
std::uintptr_t pcs[capacity];
pid_t queryThread=0;
timer_t timer{};
bool created=false;
int boundary=0;
struct Boundary {int number,capture,profile;std::uintptr_t caller;};
Boundary flags[16]{};
timespec cpuStart{},cpuFinish{},wallStart{},wallFinish{};
double seconds(const timespec& end,const timespec& start) {
    return end.tv_sec-start.tv_sec+(end.tv_nsec-start.tv_nsec)*1e-9;
}
void sample(int,siginfo_t*,void* context) {
    if (!active) return;
    if (syscall(SYS_gettid)!=queryThread) {++wrongThread;return;}
    const auto position=samples;
    if (position<capacity) {
        pcs[position]=static_cast<ucontext_t*>(context)->uc_mcontext.gregs[REG_RIP];
        samples=position+1;
    } else if (dropped<2147483647) ++dropped;
}
void maps(const char* target) {
    FILE* input=std::fopen("/proc/self/maps","r");
    FILE* output=std::fopen(target,"w");
    if (!input || !output) std::abort();
    char line[4096];
    while (std::fgets(line,sizeof(line),input)) std::fputs(line,output);
    std::fclose(input);std::fclose(output);
}
void finish() {
    active=0;
    if (created) timer_delete(timer);
    FILE* output=std::fopen("profile_flags.tsv","w");
    if (!output) std::abort();
    std::fprintf(output,"query_tid\t%d\npid\t%d\ninterval_ns\t%d\ncapacity\t%d\nsamples\t%d\ndropped\t%d\nwrong_thread_samples\t%d\n",
        queryThread,getpid(),PERIOD_NS,capacity,samples,dropped,wrongThread);
    std::fprintf(output,"query_thread_cpu_seconds\t%.9f\nwindow_elapsed_seconds\t%.9f\n",
        seconds(cpuFinish,cpuStart),seconds(wallFinish,wallStart));
    for(int i=0;i<boundary;++i)
        std::fprintf(output,"boundary\t%d\t%d\t%d\t0x%lx\n",
            flags[i].number,flags[i].capture,flags[i].profile,flags[i].caller);
    std::fclose(output);
    output=std::fopen("raw_pcs.tsv","w");
    if (!output) std::abort();
    for(sig_atomic_t i=0;i<samples;++i) {
        Dl_info image{};
        if(dladdr(reinterpret_cast<void*>(pcs[i]),&image)&&image.dli_fname)
            std::fprintf(output,"%d\t0x%lx\t%s\t0x%lx\t0x%lx\n",i,pcs[i],image.dli_fname,
                reinterpret_cast<std::uintptr_t>(image.dli_fbase),
                pcs[i]-reinterpret_cast<std::uintptr_t>(image.dli_fbase));
        else std::fprintf(output,"%d\t0x%lx\tunknown\t0x0\t0x%lx\n",i,pcs[i],pcs[i]);
    }
    std::fclose(output);
}
__attribute__((constructor)) void initialize() {
    queryThread=static_cast<pid_t>(syscall(SYS_gettid));
    std::atexit(finish);
}
void clockBoundary(void* caller) {
    if(syscall(SYS_gettid)!=queryThread)return;
    Dl_info image{};
    if(!dladdr(caller,&image)||!image.dli_fbase||boundary>=16)std::abort();
    const auto base=reinterpret_cast<std::uintptr_t>(image.dli_fbase);
    const bool capture=*reinterpret_cast<const bool*>(base+CAPTURE_OFFSET);
    const bool profile=*reinterpret_cast<const bool*>(base+PROFILE_OFFSET);
    ++boundary;
    flags[boundary-1]={boundary,capture,profile,reinterpret_cast<std::uintptr_t>(caller)-base};
    if(boundary==5) {
        if(capture||profile)std::abort();
        maps("module_maps_start.txt");
        if(PERIOD_NS) {
            struct sigaction action{};
            action.sa_sigaction=sample;
            action.sa_flags=SA_SIGINFO|SA_RESTART;
            sigemptyset(&action.sa_mask);
            if(sigaction(SIGPROF,&action,nullptr))std::abort();
            sigevent event{};
            event.sigev_notify=SIGEV_THREAD_ID;
            event.sigev_signo=SIGPROF;
            event._sigev_un._tid=queryThread;
            if(timer_create(CLOCK_THREAD_CPUTIME_ID,&event,&timer))std::abort();
            created=true;
        }
        if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&cpuStart)||
           clock_gettime(CLOCK_MONOTONIC,&wallStart))std::abort();
        if(PERIOD_NS) {
            itimerspec period{};
            period.it_value.tv_nsec=period.it_interval.tv_nsec=PERIOD_NS;
            active=1;
            if(timer_settime(timer,0,&period,nullptr))std::abort();
        }
    } else if(boundary==6) {
        active=0;
        if(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&cpuFinish)||
           clock_gettime(CLOCK_MONOTONIC,&wallFinish))std::abort();
        if(created) {
            itimerspec stopped{};
            if(timer_settime(timer,0,&stopped,nullptr))std::abort();
        }
        if(capture||profile)std::abort();
        maps("module_maps_finish.txt");
    }
}
}
namespace std {namespace chrono {inline namespace _V2 {
steady_clock::time_point steady_clock::now() noexcept {
    using Function=steady_clock::time_point (*)() noexcept;
    static const auto original=reinterpret_cast<Function>(
        dlsym(RTLD_NEXT,"_ZNSt6chrono3_V212steady_clock3nowEv"));
    if(!original)std::abort();
    clockBoundary(__builtin_return_address(0));
    return original();
}
}}}
