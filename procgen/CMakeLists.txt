cmake_minimum_required(VERSION 3.24)
project(procgen LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Some Command Line Tools installs ship an empty toolchain libc++ include dir
# that shadows the SDK's headers. Fall back to the SDK copy only when needed.
if(APPLE)
  include(CheckIncludeFileCXX)
  check_include_file_cxx(algorithm PG_HAVE_LIBCXX_HEADERS)
  if(NOT PG_HAVE_LIBCXX_HEADERS AND EXISTS "${CMAKE_OSX_SYSROOT}/usr/include/c++/v1/algorithm")
    message(STATUS "procgen: using SDK libc++ headers from ${CMAKE_OSX_SYSROOT}")
    add_compile_options(-nostdinc++ -isystem "${CMAKE_OSX_SYSROOT}/usr/include/c++/v1")
  endif()
endif()

option(PG_SANITIZE "Build with AddressSanitizer + UndefinedBehaviorSanitizer" OFF)
option(PG_WERROR "Treat warnings as errors" ON)

add_library(pg_warnings INTERFACE)
target_compile_options(pg_warnings INTERFACE -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wno-sign-conversion
                                             $<$<BOOL:${PG_WERROR}>:-Werror>)

if(PG_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
  add_link_options(-fsanitize=address,undefined)
  # libc++ bounds/precondition checks on containers and spans.
  add_compile_definitions(_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_DEBUG)
endif()

find_package(Threads REQUIRED)

add_library(pg_geogram_psm STATIC third_party/geogram_psm/Delaunay_psm.cpp)
target_include_directories(pg_geogram_psm SYSTEM PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/third_party/geogram_psm")
target_compile_definitions(pg_geogram_psm PUBLIC GEO_STATIC_LIBS $<$<NOT:$<BOOL:${PG_SANITIZE}>>:GEO_HAS_BIG_STACK>)
target_compile_options(pg_geogram_psm PRIVATE -ffp-contract=off -w)
target_link_libraries(pg_geogram_psm PUBLIC Threads::Threads)

add_library(pg
  src/exact_kernel.cpp
  src/boxes.cpp
  src/dp_nns.cpp
  src/voronoi_cells.cpp
  src/mesh.cpp
  src/topology.cpp
  src/quality.cpp
  src/intersect.cpp
  src/primitives.cpp
  src/cleanup.cpp
  src/closest.cpp
  src/audit.cpp
  src/graph.cpp
  src/ops/extrude.cpp
  src/ops/deform.cpp
  src/ops/subdivide.cpp
  src/ops/scatter.cpp
  src/ops/bevel.cpp
  src/ops/boolean.cpp
  src/json.cpp
  src/doc/doc_nodes.cpp
  src/doc/doc_reference.cpp
  src/doc/doc_expr.cpp
  src/doc/doc_eval.cpp
  src/doc/doc_ext.cpp
  src/doc/doc_mesh_curve.cpp
  src/doc/doc_detail.cpp
  src/emesh.cpp
  src/emat.cpp)
target_include_directories(pg PUBLIC include)
target_link_libraries(pg PRIVATE pg_warnings pg_geogram_psm)
set_source_files_properties(
  src/exact_kernel.cpp
  src/dp_nns.cpp
  src/voronoi_cells.cpp
  src/intersect.cpp
  src/ops/boolean.cpp
  PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")

add_executable(pg_tests tests/test_main.cpp tests/test_core.cpp tests/test_boxes.cpp tests/test_dp_nns.cpp tests/test_extrude.cpp tests/test_graph.cpp tests/test_deform.cpp tests/test_subdivide.cpp tests/test_scatter.cpp tests/test_bevel.cpp tests/test_boolean.cpp tests/test_closest.cpp tests/test_doc.cpp)
target_compile_definitions(pg_tests PRIVATE PG_TEST_DATA="${CMAKE_CURRENT_SOURCE_DIR}/tests/data" PG_RECIPES="${CMAKE_CURRENT_SOURCE_DIR}/recipes")
target_link_libraries(pg_tests PRIVATE pg pg_warnings)

add_executable(pg_bench bench/bench_main.cpp)
target_link_libraries(pg_bench PRIVATE pg pg_warnings)

# Closest-point search: the scan, the index and the structures tried beside it
# (docs/experiments/closest-point-index.md).
add_executable(pg_bench_closest bench/closest_bench.cpp)
target_compile_definitions(pg_bench_closest PRIVATE PG_RECIPES="${CMAKE_CURRENT_SOURCE_DIR}/recipes")
target_link_libraries(pg_bench_closest PRIVATE pg pg_warnings)

enable_testing()
add_test(NAME pg_tests COMMAND pg_tests)

add_executable(pgm tools/pgm.cpp tools/pgm_commands.cpp tools/preview.cpp tools/render.cpp)
target_link_libraries(pgm PRIVATE pg pg_warnings)
# GCC 12 reports false -Wrestrict / -Wstringop-overflow positives inside
# libstdc++'s std::string at -O3 (GCC PR 105329, PR 105651). The CLI is string
# handling throughout; the library keeps both warnings.
target_compile_options(pgm PRIVATE $<$<CXX_COMPILER_ID:GNU>:-Wno-restrict -Wno-stringop-overflow>)

add_executable(pg_gallery tools/gallery.cpp tools/render.cpp)
target_link_libraries(pg_gallery PRIVATE pg pg_warnings)
