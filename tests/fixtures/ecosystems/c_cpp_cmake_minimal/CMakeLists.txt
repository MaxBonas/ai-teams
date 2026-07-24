cmake_minimum_required(VERSION 3.16)
project(aiteam_cmake_fixture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()
add_executable(
  calculator_test
  src/calculator.cpp
  tests/calculator_test.cpp
)
target_include_directories(calculator_test PRIVATE include)
add_test(NAME calculator_test COMMAND calculator_test)
