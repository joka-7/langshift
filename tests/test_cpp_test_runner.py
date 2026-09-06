"""Unit tests for the C++ test runner module."""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_translator.cpp_test_runner import (
    _find_compiler,
    _run_with_cmake,
    _run_with_direct_compilation,
    run_cpp_tests,
)


class TestFindCompiler:
    """Tests for _find_compiler."""

    def test_returns_first_compiler_on_path(self) -> None:
        """Prefers g++, falling through to clang++ then c++."""
        with patch("repo_translator.cpp_test_runner.shutil.which") as which:
            which.side_effect = lambda name: "/usr/bin/clang++" if name == "clang++" else None
            assert _find_compiler() == "clang++"

    def test_returns_none_when_no_compiler_on_path(self) -> None:
        with patch("repo_translator.cpp_test_runner.shutil.which", return_value=None):
            assert _find_compiler() is None


class TestRunWithCMake:
    """Tests for _run_with_cmake."""

    def test_cmake_success(self) -> None:
        """Should return 0 when CMake and ctest succeed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            with patch("subprocess.run") as mock_run:
                # CMake config, build, and ctest all succeed
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="tests passed", stderr=""
                )
                result = _run_with_cmake(work_dir)
                assert result == 0
                # Should call cmake and ctest
                assert mock_run.call_count >= 3

    def test_cmake_config_fails(self) -> None:
        """Should return 1 when CMake configuration fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stdout="", stderr="CMake error"
                )
                with patch("builtins.print"):  # Suppress output
                    result = _run_with_cmake(work_dir)
                    assert result == 1

    def test_cmake_not_found(self) -> None:
        """Should return 1 when CMake is not found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError("cmake not found")
                with patch("builtins.print"):  # Suppress output
                    result = _run_with_cmake(work_dir)
                    assert result == 1


class TestRunWithDirectCompilation:
    """Tests for _run_with_direct_compilation."""

    def test_no_test_files(self) -> None:
        """Should return 1 when no test files are found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            with patch("builtins.print"):  # Suppress output
                result = _run_with_direct_compilation(work_dir)
                assert result == 1

    def test_compilation_success(self) -> None:
        """Should return 0 when compilation and tests succeed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            # Create a test file matching the pattern
            test_file = work_dir / "example_test.cpp"
            test_file.write_text("int main() { return 0; }")

            with patch("repo_translator.cpp_test_runner._find_compiler") as mock_find:
                mock_find.return_value = "g++"
                with patch("subprocess.run") as mock_run:
                    # Compilation succeeds, test runs and passes
                    mock_run.return_value = MagicMock(
                        returncode=0, stdout="tests passed", stderr=""
                    )
                    result = _run_with_direct_compilation(work_dir)
                    assert result == 0

    def test_compilation_fails(self) -> None:
        """Should return 1 when compilation fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            # Create a test file matching the pattern
            test_file = work_dir / "example_test.cpp"
            test_file.write_text("int main() { return 0; }")

            with patch("repo_translator.cpp_test_runner._find_compiler") as mock_find:
                mock_find.return_value = "g++"
                with patch("subprocess.run") as mock_run:
                    # Compilation fails
                    mock_run.return_value = MagicMock(
                        returncode=1, stdout="", stderr="compilation error"
                    )
                    with patch("builtins.print"):  # Suppress output
                        result = _run_with_direct_compilation(work_dir)
                        assert result == 1

    def test_test_execution_fails(self) -> None:
        """Should return 1 when test execution fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            # Create a test file matching the pattern
            test_file = work_dir / "example_test.cpp"
            test_file.write_text("int main() { return 0; }")

            with patch("repo_translator.cpp_test_runner._find_compiler") as mock_find:
                mock_find.return_value = "g++"
                with patch("subprocess.run") as mock_run:
                    # First call (compilation) succeeds, second call (test run) fails
                    mock_run.side_effect = [
                        MagicMock(returncode=0, stdout="", stderr=""),
                        MagicMock(returncode=1, stdout="", stderr="test failed"),
                    ]
                    with patch("builtins.print"):  # Suppress output
                        result = _run_with_direct_compilation(work_dir)
                        assert result == 1


class TestRunCppTests:
    """Tests for run_cpp_tests (main entry point)."""

    def test_with_cmake(self) -> None:
        """Should use CMake if CMakeLists.txt exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            # Create CMakeLists.txt
            (work_dir / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)")

            with patch("repo_translator.cpp_test_runner._run_with_cmake") as mock_cmake:
                mock_cmake.return_value = 0
                result = run_cpp_tests(work_dir)
                assert result == 0
                mock_cmake.assert_called_once()

    def test_without_cmake(self) -> None:
        """Should use direct compilation if CMakeLists.txt doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            with patch(
                "repo_translator.cpp_test_runner._run_with_direct_compilation"
            ) as mock_direct:
                mock_direct.return_value = 0
                result = run_cpp_tests(work_dir)
                assert result == 0
                mock_direct.assert_called_once()


@pytest.mark.integration
class TestAgainstRealCMakeProject:
    """
    The mocked tests above assert which commands are issued; they cannot tell
    whether those commands actually work. This drives the real toolchain against
    a minimal project whose single test passes, which is what caught
    `--build --target test` running ctest before anything was compiled.
    """

    @pytest.fixture
    def cmake_project(self, tmp_path: Path) -> Path:
        (tmp_path / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.10)\n"
            "project(demo CXX)\n"
            "enable_testing()\n"
            "add_executable(demo_test demo_test.cpp)\n"
            "add_test(NAME demo_test COMMAND demo_test)\n"
        )
        (tmp_path / "demo_test.cpp").write_text(
            "#include <cassert>\nint main() { assert(1 + 1 == 2); return 0; }\n"
        )
        return tmp_path

    def test_passing_cmake_suite_exits_zero(self, cmake_project: Path) -> None:
        if not (shutil.which("cmake") and shutil.which("ctest") and _find_compiler()):
            pytest.skip("cmake/ctest/C++ compiler not available")
        assert run_cpp_tests(cmake_project) == 0

    def test_failing_cmake_suite_exits_nonzero(self, cmake_project: Path) -> None:
        if not (shutil.which("cmake") and shutil.which("ctest") and _find_compiler()):
            pytest.skip("cmake/ctest/C++ compiler not available")
        (cmake_project / "demo_test.cpp").write_text(
            "#include <cassert>\nint main() { assert(1 + 1 == 3); return 0; }\n"
        )
        assert run_cpp_tests(cmake_project) != 0
