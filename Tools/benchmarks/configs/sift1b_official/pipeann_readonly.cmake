# Fixed pure-search profile from PipeANN/docs/cpp-interface.md.
set(CMAKE_BUILD_TYPE Release CACHE STRING "Native build profile" FORCE)
set(CMAKE_CXX_FLAGS "-DREAD_ONLY_TESTS -DNO_MAPPING" CACHE STRING "Pure-search definitions" FORCE)
set(IO_ENGINE uring CACHE STRING "Required native I/O backend" FORCE)
set(USE_TCMALLOC ON CACHE BOOL "Use the native default allocator" FORCE)
# Unmodified Ubuntu gperftools packages, extracted privately without sudo.
set(CMAKE_EXE_LINKER_FLAGS "-L/mnt/nvme/baotonglu/mocheng/datasets/sift1b/toolchains/dependencies/gperftools/usr/lib/x86_64-linux-gnu -Wl,-rpath,/mnt/nvme/baotonglu/mocheng/datasets/sift1b/toolchains/dependencies/gperftools/usr/lib/x86_64-linux-gnu" CACHE STRING "Fixed allocator link and runtime search directory" FORCE)
set(BUILD_PYTHON_INTERFACE OFF CACHE BOOL "Build only native benchmark tools" FORCE)
set(BUILD_MILVUS_SERVER OFF CACHE BOOL "No server dependencies for this benchmark" FORCE)
