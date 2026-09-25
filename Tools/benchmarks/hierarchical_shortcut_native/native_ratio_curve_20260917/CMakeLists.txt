cmake_minimum_required(VERSION 3.16)
project(NativeRatioCurve LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(OpenMP REQUIRED)
find_package(Boost REQUIRED COMPONENTS system thread filesystem regex serialization wserialization)
find_package(TBB REQUIRED)
add_executable(spannaclbench SpannAclBench.cpp
    "${ACCEPTED_TOOL}/build/AnnService/CMakeFiles/spannaclbench.dir/__/Wrappers/src/CoreInterface.cpp.o")
target_compile_definitions(spannaclbench PRIVATE TBB NUMA)
target_compile_options(spannaclbench PRIVATE -fno-omit-frame-pointer)
target_include_directories(spannaclbench PRIVATE
    "${ACCEPTED_TOOL}/source/AnnService" "${ACCEPTED_TOOL}/source/Wrappers"
    "${ACCEPTED_TOOL}/source/Tools/benchmarks")
target_link_libraries(spannaclbench PRIVATE
    "${ACCEPTED_TOOL}/source/Release/libSPTAGLibStatic.a"
    "${ACCEPTED_TOOL}/source/Release/libDistanceUtils.a"
    "${ACCEPTED_TOOL}/source/Release/libRaBitQ2Lib.a"
    "${ACCEPTED_TOOL}/source/Release/libzstd.a"
    ${Boost_LIBRARIES} TBB::tbb OpenMP::OpenMP_CXX numa dl rt)
