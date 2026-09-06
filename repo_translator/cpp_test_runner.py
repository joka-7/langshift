"""
C++ test runner — compiles and runs C++ tests using CMake + ctest or direct compilation.

Usage: python3 -m repo_translator.cpp_test_runner [working_dir]
Exit code: 0 if tests pass, 1 if tests fail or can't compile.
"""

import shutil
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
    """Configure, build and run a CMake project's tests.

    Args:
        work_dir: Directory holding CMakeLists.txt.

    Returns:
        0 if every test passed, non-zero otherwise.
    """
    build_dir = work_dir / "build"
    try:
        build_dir.mkdir(exist_ok=True)

        configure = subprocess.run(
            ["cmake", "-B", str(build_dir), "-S", str(work_dir)],
            capture_output=True, text=True, timeout=60,
        )
        if configure.returncode != 0:
            print(f"CMake configuration failed:\n{configure.stderr}")
            return 1

        # Build the default target, not "test". In the Makefile and Ninja
        # generators "test" is ctest's own target -- it runs the suite and
        # builds nothing, so asking for it here compiled nothing and ctest
        # then failed with "Unable to find executable" on a perfectly good
        # project. Compile first, then run the suite below.
        build = subprocess.run(
            ["cmake", "--build", str(build_dir)],
            capture_output=True, text=True, timeout=120,
        )
        if build.returncode != 0:
            print(f"CMake build failed:\n{build.stderr}")
            return 1

        tests = subprocess.run(
            ["ctest", "--output-on-failure"],
            cwd=build_dir, capture_output=True, text=True, timeout=120,
        )
        print(tests.stdout)
        if tests.stderr:
            print(tests.stderr)
        return tests.returncode

    except FileNotFoundError as e:
        print(f"CMake/ctest not found: {e}")
        return 1
    except subprocess.TimeoutExpired:
        print("CMake/ctest test run timed out")
        return 1


def _run_with_direct_compilation(work_dir: Path) -> int:
    """Compile every .cpp in the tree into one binary and run it.

    Args:
        work_dir: Directory to search for test and source files.

    Returns:
        0 if the compiled tests passed, non-zero otherwise.
    """
    output_file = work_dir / "run_tests"
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

        return test_result.returncode

    except subprocess.TimeoutExpired:
        print("C++ test compilation or run timed out")
        return 1
    finally:
        # Also on the timeout path: the binary is ours, and leaving it behind
        # puts a stray executable in the user's translated output.
        output_file.unlink(missing_ok=True)


def _find_compiler() -> str | None:
    """First available C++ compiler on PATH.

    Returns:
        "g++", "clang++" or "c++" — whichever resolves first — or None if none
        of them is on PATH.
    """
    for compiler in ("g++", "clang++", "c++"):
        if shutil.which(compiler):
            return compiler
    return None


if __name__ == "__main__":
    work_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    exit_code = run_cpp_tests(work_dir)
    sys.exit(exit_code)
