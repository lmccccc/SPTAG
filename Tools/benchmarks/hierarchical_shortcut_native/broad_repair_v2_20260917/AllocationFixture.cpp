#include <NativeNeighborHooks.h>
#include <cstdlib>
#include <iostream>
#include <new>

static bool recording = false;
static std::size_t allocations = 0;
void* operator new(std::size_t size) {
    if (recording) ++allocations;
    void* p = std::malloc(size ? size : 1);
    if (!p) throw std::bad_alloc();
    return p;
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }

int main() {
    using namespace SPTAG::COMMON;
    int checked = 0;
    recording = true;
    for (int row = 0; row < 1000; ++row) {
        int scratch[65];
        NativeDeficitIdentities identities(scratch, 65, false);
        for (int id = 0; id <= 32; ++id) identities.Seed(id);
        int added = 0;
        NativeDegreeFrame frame{};
        NativeSupplyAccounting accounting{identities, added, frame, 16};
        auto consume = [&](int, bool) { ++checked; };
        auto within = [&] { return checked < 10000000; };
        auto count = [&] { return checked; };
        auto batch = [&](const std::uint32_t* ids, int size, NativeSupplyAccounting& a) {
            for (int i = 0; i < size; ++i) {
                const int id = ids[i];
                if (a.identities.NeedsQualification(id, a.added, a.deficit) && id % 2) {
                    a.identities.Accept(id, a.added, a.deficit);
                    ++a.added;
                }
                consume(id, true);
            }
        };
        NativeEdgeConsumer native{consume, within, count, batch};
        const std::uint32_t members[] = {33, 34, 35, 36, 33, 37, 38, 39};
        native.consumeRow(members, 8, accounting);
        if (added != 4) std::abort();
    }
    recording = false;
    if (allocations != 0 || checked != 8000) std::abort();
    std::cout << "1000 expansion callback frames and deficit scratch: zero C++ allocations\n";
}
