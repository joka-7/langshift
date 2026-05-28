"""
Tests for repo_translator.agent

All tests are pure unit tests — no real API calls, no network.
Claude is mocked everywhere.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repo_translator.agent import (
    LANGUAGE_META,
    SKIP_DIRS,
    _ALIAS_MAP,
    _output_path,
    _try_run,
    collect_files,
    resolve_language,
    translate_repo,
)


# ─────────────────────────────────────────────
# resolve_language
# ─────────────────────────────────────────────

class TestResolveLanguage:
    def test_canonical_names_resolve_to_themselves(self):
        for name in LANGUAGE_META:
            assert resolve_language(name) == name

    def test_alias_ts_resolves_to_typescript(self):
        assert resolve_language("ts") == "typescript"

    def test_alias_js_resolves_to_javascript(self):
        assert resolve_language("js") == "javascript"

    def test_alias_py_resolves_to_python(self):
        assert resolve_language("py") == "python"

    def test_alias_rs_resolves_to_rust(self):
        assert resolve_language("rs") == "rust"

    def test_alias_kt_resolves_to_kotlin(self):
        assert resolve_language("kt") == "kotlin"

    def test_alias_cs_resolves_to_csharp(self):
        assert resolve_language("cs") == "csharp"

    def test_case_insensitive(self):
        assert resolve_language("TS") == "typescript"
        assert resolve_language("Python") == "python"
        assert resolve_language("GO") == "go"

    def test_unknown_language_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown language"):
            resolve_language("brainfuck")

    def test_unknown_language_error_includes_name(self):
        with pytest.raises(ValueError, match="cobol"):
            resolve_language("cobol")


# ─────────────────────────────────────────────
# collect_files
# ─────────────────────────────────────────────

class TestCollectFiles:
    def test_finds_ts_files(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x = 1;")
        (tmp_path / "utils.ts").write_text("export function f() {}")
        files = collect_files(tmp_path, "typescript")
        assert len(files) == 2

    def test_skips_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "lib"
        nm.mkdir(parents=True)
        (nm / "index.ts").write_text("ignored")
        (tmp_path / "src.ts").write_text("real")
        files = collect_files(tmp_path, "typescript")
        assert len(files) == 1
        assert files[0].name == "src.ts"

    def test_skips_all_known_skip_dirs(self, tmp_path):
        for skip_dir in ["node_modules", ".git", "__pycache__", "dist", "build"]:
            d = tmp_path / skip_dir
            d.mkdir()
            (d / "file.ts").write_text("should be ignored")
        (tmp_path / "real.ts").write_text("keep me")
        files = collect_files(tmp_path, "typescript")
        assert len(files) == 1

    def test_finds_nested_files(self, tmp_path):
        src = tmp_path / "src" / "utils"
        src.mkdir(parents=True)
        (src / "helpers.ts").write_text("export {}")
        files = collect_files(tmp_path, "typescript")
        assert len(files) == 1

    def test_ignores_wrong_extension(self, tmp_path):
        (tmp_path / "app.js").write_text("js file")
        (tmp_path / "app.ts").write_text("ts file")
        files = collect_files(tmp_path, "typescript")
        assert len(files) == 1
        assert files[0].suffix == ".ts"

    def test_returns_sorted_list(self, tmp_path):
        (tmp_path / "z.ts").write_text("")
        (tmp_path / "a.ts").write_text("")
        (tmp_path / "m.ts").write_text("")
        files = collect_files(tmp_path, "typescript")
        names = [f.name for f in files]
        assert names == sorted(names)

    def test_empty_repo_returns_empty_list(self, tmp_path):
        assert collect_files(tmp_path, "python") == []

    def test_tsx_extension_collected_for_typescript(self, tmp_path):
        (tmp_path / "App.tsx").write_text("export default function App() {}")
        files = collect_files(tmp_path, "typescript")
        assert len(files) == 1

    def test_python_extensions(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hi')")
        (tmp_path / "main.ts").write_text("console.log('hi')")
        files = collect_files(tmp_path, "python")
        assert len(files) == 1
        assert files[0].suffix == ".py"


# ─────────────────────────────────────────────
# _output_path
# ─────────────────────────────────────────────

class TestOutputPath:
    def test_changes_extension_to_python(self, tmp_path):
        src = tmp_path / "src" / "index.ts"
        out = tmp_path / "out"
        result = _output_path(src, tmp_path, out, "python")
        assert result == out / "src" / "index.py"

    def test_preserves_directory_structure(self, tmp_path):
        src = tmp_path / "a" / "b" / "c" / "file.ts"
        out = tmp_path / "out"
        result = _output_path(src, tmp_path, out, "python")
        assert result == out / "a" / "b" / "c" / "file.py"

    def test_go_extension(self, tmp_path):
        src = tmp_path / "main.py"
        out = tmp_path / "out"
        result = _output_path(src, tmp_path, out, "go")
        assert result.suffix == ".go"

    def test_rust_extension(self, tmp_path):
        src = tmp_path / "main.ts"
        out = tmp_path / "out"
        result = _output_path(src, tmp_path, out, "rust")
        assert result.suffix == ".rs"


# ─────────────────────────────────────────────
# _try_run
# ─────────────────────────────────────────────

class TestTryRun:
    def test_language_without_runner_returns_true(self):
        ok, msg = _try_run("typescript", "const x = 1;")
        assert ok is True
        assert "not supported" in msg

    def test_language_without_runner_java(self):
        ok, _ = _try_run("java", "public class Main {}")
        assert ok is True

    def test_valid_python_returns_true(self):
        ok, output = _try_run("python", "print('hello')")
        assert ok is True

    def test_invalid_python_returns_false(self):
        ok, error = _try_run("python", "this is not valid python !!!@@@")
        assert ok is False
        assert len(error) > 0

    def test_python_output_captured(self):
        ok, output = _try_run("python", "print('test-output-123')")
        assert ok is True
        assert "test-output-123" in output

    def test_runtime_error_python(self):
        ok, error = _try_run("python", "x = 1/0")
        assert ok is False
        assert "ZeroDivisionError" in error or "division" in error.lower()

    def test_missing_runner_gracefully_handled(self):
        # Temporarily patch runner to something that doesn't exist
        with patch.dict(LANGUAGE_META, {"python": {**LANGUAGE_META["python"], "runner": ["nonexistent_runtime_xyz"]}}):
            ok, msg = _try_run("python", "print('hi')")
            assert ok is True  # Missing runtime is treated as non-fatal
            assert "not found" in msg


# ─────────────────────────────────────────────
# translate_repo (mocked Claude)
# ─────────────────────────────────────────────

def _make_mock_client(translated_code: str) -> MagicMock:
    """Build a mock anthropic.Anthropic client that returns `translated_code`."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=translated_code)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    return mock_client


class TestTranslateRepo:
    def test_translates_single_file(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x: number = 1;")
        out = tmp_path / "out"
        mock_client = _make_mock_client("x = 1")

        with patch("repo_translator.agent.anthropic.Anthropic", return_value=mock_client), \
             patch("repo_translator.manifest.translate_manifest", return_value={"translated": [], "found": 0}):
            report = translate_repo(tmp_path, out, "ts", "python", verbose=False)

        assert report.translated == 1
        assert report.failed == 0
        assert (out / "index.py").exists()
        assert (out / "index.py").read_text() == "x = 1"

    def test_skips_empty_files(self, tmp_path):
        (tmp_path / "empty.ts").write_text("   \n  ")
        out = tmp_path / "out"
        mock_client = _make_mock_client("# empty")

        with patch("repo_translator.agent.anthropic.Anthropic", return_value=mock_client), \
             patch("repo_translator.manifest.translate_manifest", return_value={"translated": [], "found": 0}):
            report = translate_repo(tmp_path, out, "ts", "python", verbose=False)

        assert report.skipped == 1
        assert report.translated == 0

    def test_handles_no_source_files(self, tmp_path):
        out = tmp_path / "out"
        mock_client = MagicMock()

        with patch("repo_translator.agent.anthropic.Anthropic", return_value=mock_client), \
             patch("repo_translator.manifest.translate_manifest", return_value={"translated": [], "found": 0}):
            report = translate_repo(tmp_path, out, "ts", "python", verbose=False)

        assert report.translated == 0
        assert report.total == 0

    def test_preserves_directory_structure(self, tmp_path):
        src = tmp_path / "src" / "utils"
        src.mkdir(parents=True)
        (src / "helpers.ts").write_text("export function add(a: number, b: number) { return a + b; }")
        out = tmp_path / "out"
        mock_client = _make_mock_client("def add(a, b): return a + b")

        with patch("repo_translator.agent.anthropic.Anthropic", return_value=mock_client), \
             patch("repo_translator.manifest.translate_manifest", return_value={"translated": [], "found": 0}):
            translate_repo(tmp_path, out, "ts", "python", verbose=False)

        assert (out / "src" / "utils" / "helpers.py").exists()

    def test_report_contains_correct_language_info(self, tmp_path):
        (tmp_path / "main.ts").write_text("console.log('hi')")
        out = tmp_path / "out"
        mock_client = _make_mock_client("print('hi')")

        with patch("repo_translator.agent.anthropic.Anthropic", return_value=mock_client), \
             patch("repo_translator.manifest.translate_manifest", return_value={"translated": [], "found": 0}):
            report = translate_repo(tmp_path, out, "ts", "python", verbose=False)

        assert report.from_lang == "typescript"
        assert report.to_lang == "python"

    def test_api_error_marks_file_as_failed(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;")
        out = tmp_path / "out"
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API quota exceeded")

        with patch("repo_translator.agent.anthropic.Anthropic", return_value=mock_client), \
             patch("repo_translator.manifest.translate_manifest", return_value={"translated": [], "found": 0}):
            report = translate_repo(tmp_path, out, "ts", "python", verbose=False)

        assert report.failed == 1
        assert report.translated == 0
        assert "API quota exceeded" in report.files[0].error

    def test_multiple_files_all_translated(self, tmp_path):
        for name in ["a.ts", "b.ts", "c.ts"]:
            (tmp_path / name).write_text(f"const {name[0]} = 1;")
        out = tmp_path / "out"
        mock_client = _make_mock_client("x = 1")

        with patch("repo_translator.agent.anthropic.Anthropic", return_value=mock_client), \
             patch("repo_translator.manifest.translate_manifest", return_value={"translated": [], "found": 0}):
            report = translate_repo(tmp_path, out, "ts", "python", verbose=False)

        assert report.translated == 3
        assert report.total == 3

    def test_elapsed_seconds_is_positive(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;")
        out = tmp_path / "out"
        mock_client = _make_mock_client("x = 1")

        with patch("repo_translator.agent.anthropic.Anthropic", return_value=mock_client), \
             patch("repo_translator.manifest.translate_manifest", return_value={"translated": [], "found": 0}):
            report = translate_repo(tmp_path, out, "ts", "python", verbose=False)

        assert report.elapsed_seconds >= 0

    def test_resolves_language_aliases(self, tmp_path):
        """Passing 'ts' should work just like 'typescript'."""
        (tmp_path / "x.ts").write_text("const x = 1;")
        out = tmp_path / "out"
        mock_client = _make_mock_client("x = 1")

        with patch("repo_translator.agent.anthropic.Anthropic", return_value=mock_client), \
             patch("repo_translator.manifest.translate_manifest", return_value={"translated": [], "found": 0}):
            report = translate_repo(tmp_path, out, "ts", "py", verbose=False)

        assert report.from_lang == "typescript"
        assert report.to_lang == "python"
