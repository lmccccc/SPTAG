cmake_minimum_required(VERSION 3.16)
project(VanillaSpannBench LANGUAGES CXX)

# This project consumes an existing native build; it never builds upstream targets.
# cmake -S SPTAG/Tools/benchmarks/vanilla_native -B <prefix>/adapter-build \
#   -DSPANN_UPSTREAM_BUILD=<prefix>/build -DCMAKE_BUILD_TYPE=Release
# cmake --build <prefix>/adapter-build --target vanilla_adapter_object --parallel 1
# cmake --build <prefix>/adapter-build --target vanillaspannbench --parallel 1
set(SPANN_UPSTREAM_BUILD "" CACHE PATH "Existing, pinned Microsoft SPANN build")
if(NOT IS_DIRECTORY "${SPANN_UPSTREAM_BUILD}")
    message(FATAL_ERROR "SPANN_UPSTREAM_BUILD must name the existing native build")
endif()
if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "GNU" OR
   NOT CMAKE_CXX_COMPILER_VERSION VERSION_EQUAL "11.4.0")
    message(FATAL_ERROR "This baseline requires GCC 11.4.0, matching the native build")
endif()
if(CMAKE_BUILD_TYPE AND NOT CMAKE_BUILD_TYPE STREQUAL "Release")
    message(FATAL_ERROR "Only the native Release configuration is supported")
endif()
set(CMAKE_BUILD_TYPE Release)

set(native_target "${SPANN_UPSTREAM_BUILD}/AnnService/CMakeFiles/ssdserving.dir")
foreach(file flags.make link.txt)
    if(NOT EXISTS "${native_target}/${file}")
        message(FATAL_ERROR "Missing native build recipe: ${native_target}/${file}")
    endif()
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${native_target}/${file}")
endforeach()
file(STRINGS "${SPANN_UPSTREAM_BUILD}/CMakeCache.txt" native_home
     REGEX "^CMAKE_HOME_DIRECTORY:INTERNAL=")
string(REPLACE "CMAKE_HOME_DIRECTORY:INTERNAL=" "" native_source "${native_home}")
find_package(Git REQUIRED)
function(require_revision directory expected)
    execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${directory}" rev-parse HEAD
                    OUTPUT_VARIABLE revision OUTPUT_STRIP_TRAILING_WHITESPACE
                    RESULT_VARIABLE result)
    if(NOT result EQUAL 0 OR NOT revision STREQUAL expected)
        message(FATAL_ERROR "Wrong native revision in ${directory}: ${revision}")
    endif()
    execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${directory}"
                            status --porcelain --untracked-files=no
                    OUTPUT_VARIABLE dirty RESULT_VARIABLE result)
    if(NOT result EQUAL 0 OR NOT dirty STREQUAL "")
        message(FATAL_ERROR "Native source must be clean: ${directory}\n${dirty}")
    endif()
endfunction()
require_revision("${native_source}" "2ac3ebcab562bc81cdb8c7c98b35ea72f2703c3b")
require_revision("${native_source}/ThirdParty/zstd" "e47e674cd09583ff0503f0f6defd6d23d8b718d3")

foreach(name DEFINES INCLUDES FLAGS)
    file(STRINGS "${native_target}/flags.make" line REGEX "^CXX_${name} = ")
    string(REGEX REPLACE "^CXX_${name} = " "" native_${name} "${line}")
endforeach()
if(NOT native_DEFINES MATCHES "-DNUMA" OR NOT native_DEFINES MATCHES "-DTBB" OR
   native_DEFINES MATCHES "-D(GPU|ROCKSDB|SPDK|URING)" OR
   NOT native_FLAGS MATCHES "-O3" OR NOT native_FLAGS MATCHES "-DNDEBUG" OR
   NOT native_FLAGS MATCHES "-std=c\\+\\+17" OR NOT native_FLAGS MATCHES "-fopenmp")
    message(FATAL_ERROR "The native build does not match the supported CPU Release baseline")
endif()
file(STRINGS "${native_target}/flags.make" compiler_line REGEX "^# compile CXX with ")
string(REPLACE "# compile CXX with " "" native_compiler "${compiler_line}")
get_filename_component(native_compiler_real "${native_compiler}" REALPATH)
get_filename_component(adapter_compiler_real "${CMAKE_CXX_COMPILER}" REALPATH)
if(NOT native_compiler_real STREQUAL adapter_compiler_real)
    message(FATAL_ERROR "Use the native compiler: ${native_compiler}")
endif()

# Reuse the generated flags verbatim, including optimization and OpenMP flags.
set(CMAKE_CXX_FLAGS "${native_FLAGS}")
set(CMAKE_CXX_FLAGS_RELEASE "")
separate_arguments(native_definitions UNIX_COMMAND "${native_DEFINES}")
separate_arguments(native_includes UNIX_COMMAND "${native_INCLUDES}")
list(TRANSFORM native_definitions REPLACE "^-D" "")
list(TRANSFORM native_includes REPLACE "^-I" "")

# Everything after native '-o <executable>' is its ordered link dependency list.
file(READ "${native_target}/link.txt" native_link_text)
separate_arguments(native_link UNIX_COMMAND "${native_link_text}")
list(FIND native_link "-o" output_position)
if(output_position LESS 0)
    message(FATAL_ERROR "Cannot locate native executable in link.txt")
endif()
math(EXPR output_position "${output_position} + 1")
list(GET native_link ${output_position} native_executable)
get_filename_component(native_bin "${native_executable}" DIRECTORY)
math(EXPR dependencies_position "${output_position} + 1")
list(SUBLIST native_link ${dependencies_position} -1 native_libraries)
if(NOT "${native_libraries}" MATCHES "/libSPTAGLibStatic\\.a(;|$)")
    message(FATAL_ERROR "The native recipe must link the original static SPTAG library")
endif()

add_library(vanilla_adapter_object OBJECT ../VanillaSpannBench.cpp)
target_compile_definitions(vanilla_adapter_object PRIVATE ${native_definitions})
target_include_directories(vanilla_adapter_object PRIVATE ${native_includes})
set_target_properties(vanilla_adapter_object PROPERTIES POSITION_INDEPENDENT_CODE OFF)
# CMake's inferred library ordering otherwise appends libraries after the second
# native -ltbb. Preserve the exact native suffix instead of inferring dependencies.
set(native_link_suffix "")
set(native_link_files "")
foreach(library IN LISTS native_libraries)
    string(REPLACE "\\" "\\\\" quoted_library "${library}")
    string(REPLACE "\"" "\\\"" quoted_library "${quoted_library}")
    string(APPEND native_link_suffix " \"${quoted_library}\"")
    if(IS_ABSOLUTE "${library}")
        list(APPEND native_link_files "${library}")
    endif()
endforeach()
set(CMAKE_CXX_LINK_EXECUTABLE
    "<CMAKE_CXX_COMPILER> <FLAGS> <CMAKE_CXX_LINK_FLAGS> <LINK_FLAGS> <OBJECTS> -o <TARGET>${native_link_suffix}")
add_executable(vanillaspannbench $<TARGET_OBJECTS:vanilla_adapter_object>)
set_target_properties(vanillaspannbench PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${native_bin}" LINK_DEPENDS "${native_link_files}")
message(STATUS "Adapter only: ${native_bin}/vanillaspannbench")
message(STATUS "Native flags: ${native_FLAGS}")
message(STATUS "Native ordered libraries: ${native_libraries}")
