cmake_minimum_required(VERSION 3.16)
project(MatchedBaseline LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
enable_testing()
find_package(OpenMP REQUIRED)
find_package(Boost REQUIRED COMPONENTS system thread filesystem regex serialization wserialization)
find_package(TBB REQUIRED)
foreach(core original current)
    if(core STREQUAL "original")
        set(root "${ORIGINAL_TOOL}")
    else()
        set(root "${CURRENT_TOOL}")
    endif()
    add_executable(matched-${core} MatchedBench.cpp
        "${root}/build/AnnService/CMakeFiles/spannaclbench.dir/__/Wrappers/src/CoreInterface.cpp.o")
    target_compile_definitions(matched-${core} PRIVATE TBB NUMA)
    if(core STREQUAL "current")
        target_compile_definitions(matched-${core} PRIVATE MATCHED_CURRENT)
    endif()
    target_compile_options(matched-${core} PRIVATE -fno-omit-frame-pointer)
    target_include_directories(matched-${core} PRIVATE "${root}/source/AnnService"
        "${root}/source/Wrappers")
    target_link_libraries(matched-${core} PRIVATE
        "${root}/source/Release/libSPTAGLibStatic.a" "${root}/source/Release/libDistanceUtils.a"
        "${root}/source/Release/libRaBitQ2Lib.a" "${root}/source/Release/libzstd.a"
        ${Boost_LIBRARIES} TBB::tbb OpenMP::OpenMP_CXX numa dl rt)
    add_executable(protocol-test-${core} ProtocolTests.cpp)
    target_compile_definitions(protocol-test-${core} PRIVATE TBB NUMA)
    target_include_directories(protocol-test-${core} PRIVATE "${root}/source/AnnService")
    target_link_libraries(protocol-test-${core} PRIVATE
        "${root}/source/Release/libSPTAGLibStatic.a" "${root}/source/Release/libDistanceUtils.a"
        "${root}/source/Release/libRaBitQ2Lib.a" "${root}/source/Release/libzstd.a"
        ${Boost_LIBRARIES} TBB::tbb OpenMP::OpenMP_CXX numa dl rt)
    add_test(NAME protocol-${core} COMMAND protocol-test-${core})
endforeach()
