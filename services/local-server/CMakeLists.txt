cmake_minimum_required(VERSION 3.21)
project(CodeViewLocalServer LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.8 REQUIRED COMPONENTS Core Network HttpServer)

add_library(code_view_local_core
    src/Config.cpp
    src/Graph.cpp
    src/LegacySemantics.cpp
    src/Runtime.cpp
)
target_include_directories(code_view_local_core
    PUBLIC
        src
        ../../src/lib/data
        ../../src/lib/data/graph
        ../../src/lib/data/graph/token_component
        ../../src/lib/utility
)
target_link_libraries(code_view_local_core PUBLIC Qt6::Core Qt6::Network)
if(APPLE)
    target_link_libraries(code_view_local_core PRIVATE proc)
endif()

add_library(code_view_http_host src/HttpHost.cpp)
target_include_directories(code_view_http_host PUBLIC src)
target_link_libraries(code_view_http_host PUBLIC code_view_local_core Qt6::HttpServer)

add_executable(code-view-local-server src/main.cpp)
target_link_libraries(code-view-local-server PRIVATE code_view_http_host)

include(CTest)
if(BUILD_TESTING)
    add_executable(code-view-local-server-tests tests/test_main.cpp)
    target_link_libraries(code-view-local-server-tests PRIVATE code_view_http_host)
    target_compile_definitions(code-view-local-server-tests PRIVATE
        CODE_VIEW_SOURCE_ROOT="${CMAKE_SOURCE_DIR}/../.."
        CODE_VIEW_SERVER_PATH="$<TARGET_FILE:code-view-local-server>"
    )
    add_test(NAME code-view-local-server-tests COMMAND code-view-local-server-tests)

    add_executable(code-view-local-server-eval evals/eval_main.cpp)
    target_link_libraries(code-view-local-server-eval PRIVATE code_view_local_core)
    target_compile_definitions(code-view-local-server-eval PRIVATE CODE_VIEW_SOURCE_ROOT="${CMAKE_SOURCE_DIR}/../..")
endif()
