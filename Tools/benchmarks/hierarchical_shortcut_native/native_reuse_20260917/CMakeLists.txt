cmake_minimum_required(VERSION 3.16)
project(NativeReuseTests LANGUAGES CXX)
enable_testing()
set(CMAKE_CXX_STANDARD 17)
find_package(OpenMP REQUIRED)
find_package(Boost REQUIRED COMPONENTS system thread filesystem regex serialization wserialization)
find_package(TBB REQUIRED)
add_executable(nativereusetests NativeTests.cpp)
target_compile_definitions(nativereusetests PRIVATE TBB NUMA)
target_include_directories(nativereusetests PRIVATE "${SPANN_ROOT}/AnnService")
target_link_libraries(nativereusetests PRIVATE
    "${SPANN_ROOT}/Release/libSPTAGLibStatic.a"
    "${SPANN_ROOT}/Release/libDistanceUtils.a"
    "${SPANN_ROOT}/Release/libRaBitQ2Lib.a"
    "${SPANN_ROOT}/Release/libzstd.a"
    ${Boost_LIBRARIES} TBB::tbb OpenMP::OpenMP_CXX numa dl rt)
add_test(NAME native-reuse-fixtures COMMAND nativereusetests)
