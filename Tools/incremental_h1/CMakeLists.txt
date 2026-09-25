cmake_minimum_required(VERSION 3.16)
project(PreservedHeadBuilder LANGUAGES CXX)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
set(SPTAG_SOURCE_ROOT "" CACHE PATH "Frozen source snapshot")
set(SPTAG_ARCHIVES "" CACHE PATH "Authenticated accepted native archives")
find_package(OpenMP REQUIRED)
find_package(Boost REQUIRED COMPONENTS system thread wserialization regex filesystem atomic serialization)
find_package(TBB REQUIRED)
find_library(NUMA_LIBRARY numa REQUIRED)
add_executable(indexbuilder "${SPTAG_SOURCE_ROOT}/AnnService/src/IndexBuilder/main.cpp")
target_compile_features(indexbuilder PRIVATE cxx_std_17)
target_compile_definitions(indexbuilder PRIVATE NUMA TBB)
target_compile_options(indexbuilder PRIVATE -fno-omit-frame-pointer)
target_include_directories(indexbuilder PRIVATE "${SPTAG_SOURCE_ROOT}/AnnService")
target_link_libraries(indexbuilder PRIVATE
    "${SPTAG_ARCHIVES}/libSPTAGLibStatic.a"
    "${SPTAG_ARCHIVES}/libDistanceUtils.a"
    "${SPTAG_ARCHIVES}/libRaBitQ2Lib.a"
    "${SPTAG_ARCHIVES}/libzstd.a"
    ${Boost_LIBRARIES} OpenMP::OpenMP_CXX TBB::tbb ${NUMA_LIBRARY} m rt)
