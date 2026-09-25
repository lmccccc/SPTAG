cmake_minimum_required(VERSION 3.16)
project(ShortcutFullTests LANGUAGES CXX)
enable_testing()
set(CMAKE_CXX_STANDARD 17)
if(NOT EXISTS "${SPANN_ROOT}/Release/libSPTAGLibStatic.a")
    message(FATAL_ERROR "SPANN_ROOT must be the isolated, compiled frozen source")
endif()
find_package(OpenMP REQUIRED)
find_package(Boost REQUIRED COMPONENTS system thread filesystem regex serialization wserialization)
find_package(TBB REQUIRED)
add_executable(fullnativetests FullNativeTests.cpp)
target_compile_definitions(fullnativetests PRIVATE TBB NUMA)
target_include_directories(fullnativetests PRIVATE . .. "${SPANN_ROOT}/AnnService")
target_link_libraries(fullnativetests PRIVATE
    "${SPANN_ROOT}/Release/libSPTAGLibStatic.a"
    "${SPANN_ROOT}/Release/libDistanceUtils.a"
    "${SPANN_ROOT}/Release/libRaBitQ2Lib.a"
    "${SPANN_ROOT}/Release/libzstd.a"
    ${Boost_LIBRARIES} TBB::tbb OpenMP::OpenMP_CXX numa dl rt)
add_test(NAME full-native-fixtures COMMAND fullnativetests)
add_executable(fulladjacencytests ../HookTests.cpp)
target_include_directories(fulladjacencytests PRIVATE ..)
add_test(NAME full-adjacency-fixtures COMMAND fulladjacencytests)
