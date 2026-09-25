# Implementation A/B builds must not replace either established benchmark binary.
set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
set(GPU OFF CACHE BOOL "" FORCE)
set(LIBRARYONLY OFF CACHE BOOL "" FORCE)
set(ROCKSDB OFF CACHE BOOL "" FORCE)
set(SPDK OFF CACHE BOOL "" FORCE)
set(TBB ON CACHE BOOL "" FORCE)
set(URING OFF CACHE BOOL "" FORCE)
set(USE_ASAN OFF CACHE BOOL "" FORCE)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY
    "/mnt/nvme/baotonglu/mocheng/datasets/sift1b/toolchains/sptag_hierarchy_latency/bin"
    CACHE PATH "" FORCE)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY
    "/mnt/nvme/baotonglu/mocheng/datasets/sift1b/toolchains/sptag_hierarchy_latency/lib"
    CACHE PATH "" FORCE)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY
    "/mnt/nvme/baotonglu/mocheng/datasets/sift1b/toolchains/sptag_hierarchy_latency/lib"
    CACHE PATH "" FORCE)
