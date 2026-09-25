cmake_minimum_required(VERSION 3.16)
project(H1SupplierTests LANGUAGES CXX)
enable_testing()
set(CMAKE_CXX_STANDARD 17)
find_package(OpenMP REQUIRED)
find_package(Boost REQUIRED COMPONENTS system thread filesystem regex serialization wserialization)
find_package(TBB REQUIRED)
add_executable(suppliertests SupplierTests.cpp)
target_compile_definitions(suppliertests PRIVATE TBB NUMA)
target_include_directories(suppliertests PRIVATE "${SPANN_ROOT}/AnnService")
target_link_libraries(suppliertests PRIVATE
    "${SPANN_ROOT}/Release/libSPTAGLibStatic.a"
    "${SPANN_ROOT}/Release/libDistanceUtils.a"
    "${SPANN_ROOT}/Release/libRaBitQ2Lib.a"
    "${SPANN_ROOT}/Release/libzstd.a"
    ${Boost_LIBRARIES} TBB::tbb OpenMP::OpenMP_CXX numa dl rt)
add_test(NAME native-supplier-fixtures COMMAND suppliertests)
