"""
C++ test runner — compiles and runs C++ tests using CMake + ctest or direct compilation.

Usage: python3 -m repo_translator.cpp_test_runner [working_dir]
Exit code: 0 if tests pass, 1 if tests fail or can't compile.
"""

import subprocess
import sys
from pathlib import Path


def run_cpp_tests(work_dir: Path) -> int:
    """
    Try to run C++ tests using CMake + ctest if CMakeLists.txt exists,
    otherwise try to compile and run tests with g++/clang++.

    Returns:
        0 if tests pass, 1 if they fail or compilation fails.
    """
    # Try CMake + ctest first (most standard for C++ projects)
    cmake_file = work_dir / "CMakeLists.txt"
    if cmake_file.exists():
        return _run_with_cmake(work_dir)

    # Fall back to direct compilation of test files
    return _run_with_direct_compilation(work_dir)


def _run_with_cmake(work_dir: Path) -> int:
    """Build with CMake and run tests with ctest."""
    try:
        # Create build directory
        build_dir = work_dir / "build"
        build_dir.mkdir(exist_ok=True)

        # Run cmake
        cmake_result = subprocess.run(
            ["cmake", "-B", str(build_dir), "-S", str(work_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if cmake_result.returncode != 0:
            print(f"CMake configuration failed:\n{cmake_result.stderr}")
            return 1

        # Build tests
        build_result = subprocess.run(
            ["cmake", "--build", str(build_dir), "--target", "test"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if build_result.returncode != 0:
            print(f"CMake build failed:\n{build_result.stderr}")
            return 1

        # Run ctest
        test_result = subprocess.run(
            ["ctest", "--output-on-failure"],
            cwd=build_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )

        print(test_result.stdout)
        if test_result.stderr:
            print(test_result.stderr)

        return test_result.returncode

    except FileNotFoundError as e:
        print(f"CMake/ctest not found: {e}")
        return 1
    except subprocess.TimeoutExpired:
        print("CMake/ctest test run timed out")
        return 1
    except Exception as e:
        print(f"CMake test run failed: {e}")
        return 1


def _run_with_direct_compilation(work_dir: Path) -> int:
    """Compile and run test files directly with g++/clang++."""
    try:
        # Find all test files
        test_files = list(work_dir.glob("**/*_test.cpp")) + list(
            work_dir.glob("**/Test*.cpp")
        )

        if not test_files:
            print("No C++ test files found (expected *_test.cpp or Test*.cpp)")
            return 1

        # Also gather any non-test .cpp files that might be needed
        src_files = [
            f
            for f in work_dir.glob("**/*.cpp")
            if f not in test_files and not f.name.startswith(".")
        ]

        # Try g++ or clang++
        compiler = _find_compiler()
        if not compiler:
            print("Neither g++ nor clang++ found in PATH")
            return 1

        # Compile all files together
        output_file = work_dir / "run_tests"
        cmd = [
            compiler,
            "-std=c++17",
            "-O2",
            "-fPIC",
        ] + [str(f) for f in src_files + test_files] + [
            "-o",
            str(output_file),
        ]

        compile_result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if compile_result.returncode != 0:
            print(f"Compilation failed:\n{compile_result.stderr}")
            return 1

        # Run compiled tests
        test_result = subprocess.run(
            [str(output_file)],
            capture_output=True,
            text=True,
            timeout=60,
        )

        print(test_result.stdout)
        if test_result.stderr:
            print(test_result.stderr)

        # Clean up
        output_file.unlink(missing_ok=True)

        return test_result.returncode

    except subprocess.TimeoutExpired:
        print("C++ test compilation or run timed out")
        return 1
    except Exception as e:
        print(f"Direct compilation test run failed: {e}")
        return 1


def _find_compiler() -> str | None:
    """Find g++ or clang++ in PATH."""
    for compiler in ["g++", "clang++", "c++"]:
        result = subprocess.run(
            ["which", compiler],
            capture_output=True,
        )
        if result.returncode == 0:
            return compiler
    return None


if __name__ == "__main__":
    work_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    exit_code = run_cpp_tests(work_dir)
    sys.exit(exit_code)
