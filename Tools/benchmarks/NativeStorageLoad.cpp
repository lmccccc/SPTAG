// Copyright (c) Microsoft Corporation. All rights reserved.
// Licensed under the MIT License.
#include "inc/Core/VectorIndex.h"
#include <chrono>
#include <fstream>
#include <iostream>
#include <sys/resource.h>
#include <unistd.h>

int main(int argc, char** argv)
{
    if (argc != 2) {
        std::cerr << "Usage: nativestorageload TENANT_DIRECTORY\n";
        return 2;
    }
    const auto start = std::chrono::steady_clock::now();
    std::shared_ptr<SPTAG::VectorIndex> index;
    if (SPTAG::VectorIndex::LoadIndex(argv[1], index) != SPTAG::ErrorCode::Success) {
        std::cerr << "Native core index load failed\n";
        return 1;
    }
    const auto finish = std::chrono::steady_clock::now();
    rusage usage{};
    std::ifstream stat("/proc/self/statm");
    std::uint64_t virtualPages = 0, residentPages = 0;
    stat >> virtualPages >> residentPages;
    const auto pageBytes = sysconf(_SC_PAGESIZE);
    if (!stat || pageBytes <= 0 || getrusage(RUSAGE_SELF, &usage) != 0) {
        std::cerr << "Native loader RSS measurement failed\n";
        return 1;
    }
    std::cout << "{\"scope\":\"native_core_loader_saved_ini\","
              << "\"load_seconds\":" << std::chrono::duration<double>(finish - start).count()
              << ",\"peak_rss_bytes\":" << static_cast<std::uint64_t>(usage.ru_maxrss) * 1024
              << ",\"loaded_rss_bytes\":" << residentPages * pageBytes << "}\n";
    return 0;
}
