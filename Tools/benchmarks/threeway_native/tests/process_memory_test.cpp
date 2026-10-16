#include "../benchmark.h"

int main()
{
    constexpr size_t bytes = 64 * 1024 * 1024;
    const auto before = threeway::process_memory();
    void *allocation = ::mmap(nullptr, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    threeway::require(allocation != MAP_FAILED, "Cannot allocate memory fixture");
    auto *data = static_cast<volatile unsigned char *>(allocation);
    for (size_t offset = 0; offset < bytes; offset += static_cast<size_t>(::sysconf(_SC_PAGESIZE)))
        data[offset] = 1;
    const auto resident = threeway::process_memory();
    threeway::require(::munmap(allocation, bytes) == 0, "Cannot release memory fixture");
    const auto released = threeway::process_memory();
    threeway::require(resident.rss_bytes >= before.rss_bytes + bytes * 3 / 4,
                      "RSS does not report committed pages in bytes");
    threeway::require(released.rss_bytes + bytes * 3 / 4 <= resident.rss_bytes,
                      "RSS did not fall after releasing pages");
    threeway::require(resident.peak_rss_bytes >= before.peak_rss_bytes &&
                          released.peak_rss_bytes >= resident.peak_rss_bytes,
                      "Process peak RSS must remain cumulative after release");
}
