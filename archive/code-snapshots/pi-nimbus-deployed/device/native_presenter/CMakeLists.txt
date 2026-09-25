cmake_minimum_required(VERSION 3.16)
project(nimbus_native_presenter LANGUAGES CXX)

find_package(SDL2 CONFIG QUIET)
if(NOT SDL2_FOUND)
  find_package(PkgConfig REQUIRED)
  pkg_check_modules(SDL2 REQUIRED IMPORTED_TARGET sdl2)
endif()

add_library(nimbus_native_presenter SHARED native_presenter.cpp)
target_compile_features(nimbus_native_presenter PRIVATE cxx_std_17)
target_compile_options(nimbus_native_presenter PRIVATE -Wall -Wextra -Wpedantic)
target_include_directories(nimbus_native_presenter PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(nimbus_native_presenter PROPERTIES
  CXX_VISIBILITY_PRESET hidden
  VISIBILITY_INLINES_HIDDEN YES
)

if(TARGET SDL2::SDL2)
  target_link_libraries(nimbus_native_presenter PRIVATE SDL2::SDL2)
elseif(TARGET PkgConfig::SDL2)
  target_link_libraries(nimbus_native_presenter PRIVATE PkgConfig::SDL2)
else()
  target_include_directories(nimbus_native_presenter PRIVATE ${SDL2_INCLUDE_DIRS})
  target_link_libraries(nimbus_native_presenter PRIVATE ${SDL2_LIBRARIES})
endif()
