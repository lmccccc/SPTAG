#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <new>

namespace {
struct Entry { std::uintptr_t pc = 0; std::uint64_t calls = 0, bytes = 0; };
Entry entries[2][8192];
thread_local int phase = 0;
int boundary = 0;
void record(void* caller, std::size_t size) noexcept {
    if (!phase) return;
    const auto pc = reinterpret_cast<std::uintptr_t>(caller);
    std::size_t slot = (pc >> 3) % 8192;
    for (int n = 0; n < 8192; ++n, slot = (slot + 1) % 8192) {
        auto& entry = entries[phase - 1][slot];
        if (!entry.pc || entry.pc == pc) {
            entry.pc = pc; ++entry.calls; entry.bytes += size; return;
        }
    }
    std::abort();
}
void* allocate(std::size_t size, void* caller) {
    void* pointer = std::malloc(size ? size : 1);
    if (!pointer) throw std::bad_alloc();
    record(caller, size);
    return pointer;
}
void finish() {
    phase = 0;
    auto* out = std::fopen("allocations.tsv", "w");
    if (!out) std::abort();
    for (int kind = 0; kind < 2; ++kind)
        for (const auto& entry : entries[kind]) {
            if (!entry.pc) continue;
            Dl_info image{};
            if (!dladdr(reinterpret_cast<void*>(entry.pc), &image)) std::abort();
            std::fprintf(out, "%s\t%s\t0x%lx\t%lu\t%lu\n",
                kind ? "capture" : "ordinary", image.dli_fname,
                entry.pc - reinterpret_cast<std::uintptr_t>(image.dli_fbase), entry.calls, entry.bytes);
        }
    std::fprintf(out, "boundaries\t-\t0\t%d\t0\n", boundary);
    std::fclose(out);
}
__attribute__((constructor)) void initialize() { std::atexit(finish); }
}

void* operator new(std::size_t n) { return allocate(n, __builtin_return_address(0)); }
void* operator new[](std::size_t n) { return allocate(n, __builtin_return_address(0)); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }
void* operator new(std::size_t n, std::align_val_t alignment) {
    void* p = nullptr;
    if (posix_memalign(&p, static_cast<std::size_t>(alignment), n ? n : 1)) throw std::bad_alloc();
    record(__builtin_return_address(0), n);
    return p;
}
void* operator new[](std::size_t n, std::align_val_t alignment) {
    void* p = nullptr;
    if (posix_memalign(&p, static_cast<std::size_t>(alignment), n ? n : 1)) throw std::bad_alloc();
    record(__builtin_return_address(0), n);
    return p;
}
void operator delete(void* p, std::align_val_t) noexcept { std::free(p); }
void operator delete[](void* p, std::align_val_t) noexcept { std::free(p); }
void operator delete(void* p, std::size_t, std::align_val_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t, std::align_val_t) noexcept { std::free(p); }

namespace std { namespace chrono { inline namespace _V2 {
steady_clock::time_point steady_clock::now() noexcept {
    phase = 0;
    using Function = steady_clock::time_point (*)() noexcept;
    static const auto original = reinterpret_cast<Function>(
        dlsym(RTLD_NEXT, "_ZNSt6chrono3_V212steady_clock3nowEv"));
    if (!original) std::abort();
    ++boundary;
    Dl_info image{};
    if (!dladdr(__builtin_return_address(0), &image)) std::abort();
    const auto base = reinterpret_cast<std::uintptr_t>(image.dli_fbase);
    if (boundary >= 5 && (boundary - 5) % 6 == 0) {
        if (*reinterpret_cast<const bool*>(base + CAPTURE_OFFSET) ||
            *reinterpret_cast<const bool*>(base + PROFILE_OFFSET)) std::abort();
        phase = 1;
    } else if (boundary >= 7 && (boundary - 7) % 6 == 0) phase = 2;
    return original();
}
}}}
