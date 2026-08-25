"""
Tests for repo_translator.agent

All tests are pure unit tests — no real API calls, no network.
Providers are mocked via MockProvider.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from helpers import CapturingProvider, MockProvider

import repo_translator.agent as agent
from repo_translator.agent import (
    LANGUAGE_META,
    _build_repo_map,
    _extract_symbols,
    _format_repo_map,
    _is_test_file,
    _output_path,
    _score_confidence,
    _split_into_chunks,
    _translate_once,
    _try_run,
    collect_files,
    estimate_translation,
    price_label,
    resolve_language,
    run_tests,
    translate_repo,
)
from repo_translator.providers.base import LLMProvider


def _provider(code: str = "x = 1") -> MockProvider:
    return MockProvider(code)


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
        with patch.dict(LANGUAGE_META, {"python": {**LANGUAGE_META["python"], "runner": ["nonexistent_runtime_xyz"]}}):
            ok, msg = _try_run("python", "print('hi')")
            assert ok is True
            assert "not found" in msg

    def test_timeout_treated_as_soft_pass(self):
        import subprocess
        with patch("repo_translator.agent.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd=["python3"], timeout=15)):
            ok, msg = _try_run("python", "while True: pass")
        assert ok is True
        assert "timed out" in msg


# ─────────────────────────────────────────────
# _is_test_file
# ─────────────────────────────────────────────

class TestIsTestFile:
    def test_dot_suffix_pattern_matches(self):
        # typescript's patterns are dot-prefixed: ".test.ts", ".spec.ts", ...
        assert _is_test_file(Path("math.test.ts"), "typescript") is True
        assert _is_test_file(Path("util.spec.ts"), "typescript") is True

    def test_dot_suffix_pattern_does_not_match_source_file(self):
        assert _is_test_file(Path("math.ts"), "typescript") is False

    def test_suffix_pattern_with_embedded_dot_matches(self):
        # python's "_test.py" pattern has a dot but doesn't start with one —
        # matched via the endswith branch, not the startswith branch.
        assert _is_test_file(Path("math_test.py"), "python") is True

    def test_prefix_pattern_matches(self):
        # python's "test_" pattern has no dot at all — matched via startswith.
        assert _is_test_file(Path("test_math.py"), "python") is True

    def test_prefix_pattern_does_not_match_mid_name(self):
        assert _is_test_file(Path("mytest_math.py"), "python") is False

    def test_language_with_no_test_patterns_never_matches(self):
        # rust's test_patterns is [] (cargo test covers the whole crate).
        assert _is_test_file(Path("anything.rs"), "rust") is False


# ─────────────────────────────────────────────
# run_tests
# ─────────────────────────────────────────────

class TestRunTests:
    def test_no_test_runner_configured_is_a_soft_pass(self, tmp_path):
        # java's test_runner is None.
        passed, output = run_tests(tmp_path, "java", verbose=False)
        assert passed is True
        assert "not configured" in output

    def test_passing_suite_reports_passed(self, tmp_path):
        mock_result = MagicMock(returncode=0, stdout="3 passed", stderr="")
        with patch("repo_translator.agent.subprocess.run", return_value=mock_result):
            passed, output = run_tests(tmp_path, "python", verbose=False)
        assert passed is True
        assert "3 passed" in output

    def test_failing_suite_reports_failed(self, tmp_path):
        mock_result = MagicMock(returncode=1, stdout="", stderr="2 failed, 1 passed")
        with patch("repo_translator.agent.subprocess.run", return_value=mock_result):
            passed, output = run_tests(tmp_path, "python", verbose=False)
        assert passed is False
        assert "2 failed" in output

    def test_verbose_prints_runner_command_and_pass_status(self, tmp_path, capsys):
        mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("repo_translator.agent.subprocess.run", return_value=mock_result):
            run_tests(tmp_path, "python", verbose=True)
        out = capsys.readouterr().out
        assert "pytest" in out
        assert "Tests passed" in out

    def test_verbose_prints_failure_tail_on_failure(self, tmp_path, capsys):
        # Regression-guard: the last-20-lines tail print (agent.py) was
        # entirely unexercised since every existing test used verbose=False.
        mock_result = MagicMock(returncode=1, stdout="", stderr="AssertionError: boom")
        with patch("repo_translator.agent.subprocess.run", return_value=mock_result):
            run_tests(tmp_path, "python", verbose=True)
        out = capsys.readouterr().out
        assert "Tests failed" in out
        assert "AssertionError: boom" in out

    def test_timeout_reports_failed(self, tmp_path):
        import subprocess
        with patch("repo_translator.agent.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd=["pytest"], timeout=120)):
            passed, output = run_tests(tmp_path, "python", verbose=False)
        assert passed is False
        assert "timed out" in output

    def test_missing_runner_binary_is_a_soft_pass(self, tmp_path):
        with patch("repo_translator.agent.subprocess.run", side_effect=FileNotFoundError()):
            passed, output = run_tests(tmp_path, "python", verbose=False)
        assert passed is True
        assert "not found" in output


# ─────────────────────────────────────────────
# translate_repo
# ─────────────────────────────────────────────

class TestTranslateRepo:
    def test_translates_single_file(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x: number = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert report.translated == 1
        assert report.failed == 0
        assert (out / "index.py").exists()
        assert (out / "index.py").read_text() == "x = 1"

    def test_skips_empty_files(self, tmp_path):
        (tmp_path / "empty.ts").write_text("   \n  ")
        out      = tmp_path / "out"
        provider = MockProvider("# empty")

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert report.skipped == 1
        assert report.translated == 0

    def test_handles_no_source_files(self, tmp_path):
        out      = tmp_path / "out"
        provider = MockProvider()

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert report.translated == 0
        assert report.total == 0

    def test_preserves_directory_structure(self, tmp_path):
        src = tmp_path / "src" / "utils"
        src.mkdir(parents=True)
        (src / "helpers.ts").write_text("export function add(a: number, b: number) { return a + b; }")
        out      = tmp_path / "out"
        provider = MockProvider("def add(a, b): return a + b")

        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                       translate_manifests=False, verbose=False, score_confidence=False)

        assert (out / "src" / "utils" / "helpers.py").exists()

    def test_report_contains_correct_language_info(self, tmp_path):
        (tmp_path / "main.ts").write_text("console.log('hi')")
        out      = tmp_path / "out"
        provider = MockProvider("print('hi')")

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert report.from_lang == "typescript"
        assert report.to_lang == "python"

    def test_api_error_marks_file_as_failed(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider(Exception("API quota exceeded"))

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert report.failed == 1
        assert report.translated == 0
        assert "API quota exceeded" in report.files[0].error

    def test_api_error_offers_external_chat_urls_as_a_fallback(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider(Exception("API quota exceeded"))

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        urls = report.files[0].external_chat_urls
        assert urls is not None
        assert set(urls) == {"ChatGPT", "Claude", "Gemini (Google AI Mode)", "Groq"}
        assert all(url.startswith("https://") for url in urls.values())

    def test_verbose_prints_external_chat_urls_on_failure(self, tmp_path, capsys):
        (tmp_path / "main.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider(Exception("API quota exceeded"))

        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                       translate_manifests=False, verbose=True, score_confidence=False)

        out_text = capsys.readouterr().out
        assert "Or ask directly:" in out_text
        assert "Claude: https://claude.ai/new?" in out_text

    def test_multiple_files_all_translated(self, tmp_path):
        for name in ["a.ts", "b.ts", "c.ts"]:
            (tmp_path / name).write_text(f"const {name[0]} = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert report.translated == 3
        assert report.total == 3

    def test_elapsed_seconds_is_positive(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert report.elapsed_seconds >= 0

    def test_resolves_language_aliases(self, tmp_path):
        (tmp_path / "x.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")

        report = translate_repo(tmp_path, out, "ts", "py", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert report.from_lang == "typescript"
        assert report.to_lang == "python"

    def test_exhaustion_message_uses_providers_own_attempt_count(self, tmp_path, capsys):
        # Regression: the exhaustion branch used to print the module-level
        # MAX_FIX_ATTEMPTS constant (3) instead of the provider's own
        # max_fix_attempts, so a provider capped at 1 attempt (e.g. offline)
        # would misreport "after 3 attempts" having only tried once. Every
        # other test in this suite passes verbose=False, which is how this
        # survived undetected.
        (tmp_path / "x.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("this is not valid python !!!")
        provider.max_fix_attempts = 1

        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                        translate_manifests=False, verbose=True, score_confidence=False)

        out_text = capsys.readouterr().out
        assert "after 1 attempts" in out_text
        assert "after 3 attempts" not in out_text

    def test_rust_run_tests_not_gated_on_test_files(self, tmp_path):
        # Regression: rust has test_patterns=[] (cargo test runs the whole
        # crate, not individual "*_test.rs" files), so gating run_tests_after
        # on `test_files` being non-empty made --run-tests silently a no-op
        # for every rust translation.
        (tmp_path / "main.py").write_text("print('hi')")
        out      = tmp_path / "out"
        provider = MockProvider("fn main() {}")

        with patch("repo_translator.agent.run_tests", return_value=(True, "ok")) as mock_run:
            translate_repo(tmp_path, out, "python", "rust", provider=provider,
                            translate_manifests=False, verbose=False,
                            score_confidence=False, run_tests_after=True)

        mock_run.assert_called_once()


class TestCheckpointResume:
    """
    Closes TODO #7: an interrupted run used to start from scratch. Now
    translate_repo() writes .translation_state.json into the output dir
    after every file, and (resume=True, the default) skips files already
    recorded ok/ok_with_warnings there on the next run against the same
    repo_path/from_lang/to_lang.
    """

    def test_checkpoint_file_written_after_run(self, tmp_path):
        (tmp_path / "a.ts").write_text("const a = 1;")
        (tmp_path / "b.ts").write_text("const b = 2;")
        out = tmp_path / "out"
        provider = MockProvider("x = 1")

        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                        translate_manifests=False, verbose=False, score_confidence=False)

        state_file = out / ".translation_state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["input_path"] == str(tmp_path)
        assert data["from_lang"] == "typescript"
        assert data["to_lang"] == "python"
        assert {f["path"] for f in data["files"]} == {"a.ts", "b.ts"}

    def test_resume_skips_already_completed_files(self, tmp_path):
        (tmp_path / "a.ts").write_text("const a = 1;")
        out = tmp_path / "out"

        translate_repo(tmp_path, out, "ts", "python", provider=MockProvider("x = 1"),
                        translate_manifests=False, verbose=False, score_confidence=False)

        # A second run with a provider that fails every call: if resume didn't
        # skip the already-completed file, this run would report it failed.
        report = translate_repo(
            tmp_path, out, "ts", "python",
            provider=MockProvider(raises=Exception("should not be called")),
            translate_manifests=False, verbose=False, score_confidence=False,
        )

        assert report.total == 1
        assert report.files[0].status == "ok"
        assert report.failed == 0

    def test_resume_false_always_retranslates(self, tmp_path):
        (tmp_path / "a.ts").write_text("const a = 1;")
        out = tmp_path / "out"

        translate_repo(tmp_path, out, "ts", "python", provider=MockProvider("x = 1"),
                        translate_manifests=False, verbose=False, score_confidence=False)

        provider = MockProvider("y = 2")
        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                        translate_manifests=False, verbose=False, score_confidence=False,
                        resume=False)

        assert len(provider.calls) == 1  # was actually called, not skipped
        assert (out / "a.py").read_text() == "y = 2"

    def test_resume_ignores_checkpoint_for_a_different_input_repo(self, tmp_path):
        repo_a = tmp_path / "repo_a"
        repo_a.mkdir()
        (repo_a / "a.ts").write_text("const a = 1;")
        out = tmp_path / "out"
        translate_repo(repo_a, out, "ts", "python", provider=MockProvider("x = 1"),
                        translate_manifests=False, verbose=False, score_confidence=False)

        repo_b = tmp_path / "repo_b"
        repo_b.mkdir()
        (repo_b / "a.ts").write_text("const a = 1;")
        provider = MockProvider("y = 2")
        report = translate_repo(repo_b, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert len(provider.calls) == 1  # not skipped — different repo_path
        assert report.files[0].status == "ok"

    def test_resume_ignores_checkpoint_for_a_different_language_pair(self, tmp_path):
        (tmp_path / "a.ts").write_text("const a = 1;")
        out = tmp_path / "out"
        translate_repo(tmp_path, out, "ts", "python", provider=MockProvider("x = 1"),
                        translate_manifests=False, verbose=False, score_confidence=False)

        provider = MockProvider("fn main() {}")
        translate_repo(tmp_path, out, "ts", "rust", provider=provider,
                       translate_manifests=False, verbose=False, score_confidence=False)

        assert len(provider.calls) == 1  # not skipped — different to_lang

    def test_resume_ignores_checkpoint_entry_if_dest_file_missing(self, tmp_path):
        (tmp_path / "a.ts").write_text("const a = 1;")
        out = tmp_path / "out"
        translate_repo(tmp_path, out, "ts", "python", provider=MockProvider("x = 1"),
                        translate_manifests=False, verbose=False, score_confidence=False)

        (out / "a.py").unlink()  # simulate the output being deleted separately

        provider = MockProvider("y = 2")
        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                        translate_manifests=False, verbose=False, score_confidence=False)

        assert len(provider.calls) == 1  # re-translated, not trusted from the checkpoint
        assert (out / "a.py").read_text() == "y = 2"

    def test_corrupted_checkpoint_is_ignored_not_fatal(self, tmp_path):
        (tmp_path / "a.ts").write_text("const a = 1;")
        out = tmp_path / "out"
        out.mkdir(parents=True)
        (out / ".translation_state.json").write_text("not valid json {{{")

        provider = MockProvider("x = 1")
        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert report.files[0].status == "ok"
        assert len(provider.calls) == 1

    def test_failed_files_are_not_treated_as_resumable(self, tmp_path):
        (tmp_path / "a.ts").write_text("const a = 1;")
        out = tmp_path / "out"
        translate_repo(tmp_path, out, "ts", "python",
                        provider=MockProvider(raises=Exception("quota exceeded")),
                        translate_manifests=False, verbose=False, score_confidence=False)

        provider = MockProvider("x = 1")
        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert len(provider.calls) == 1  # re-attempted, not skipped as "done"
        assert report.files[0].status == "ok"

    def test_verbose_prints_resume_summary_and_per_file_message(self, tmp_path, capsys):
        (tmp_path / "a.ts").write_text("const a = 1;")
        out = tmp_path / "out"
        translate_repo(tmp_path, out, "ts", "python", provider=MockProvider("x = 1"),
                        translate_manifests=False, verbose=False, score_confidence=False)

        provider = MockProvider(raises=Exception("should not be called"))
        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                        translate_manifests=False, verbose=True, score_confidence=False)

        out_text = capsys.readouterr().out
        assert "Resuming: 1 file(s) already completed" in out_text
        assert "resumed from checkpoint" in out_text

    def test_emits_file_done_event_for_resumed_file(self, tmp_path):
        (tmp_path / "a.ts").write_text("const a = 1;")
        out = tmp_path / "out"
        translate_repo(tmp_path, out, "ts", "python", provider=MockProvider("x = 1"),
                        translate_manifests=False, verbose=False, score_confidence=False)

        events: list[dict] = []
        provider = MockProvider(raises=Exception("should not be called"))
        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                        translate_manifests=False, verbose=False, score_confidence=False,
                        on_progress=events.append)

        done_events = [e for e in events if e["type"] == "file_done"]
        assert done_events == [{
            "type": "file_done", "index": 1, "total": 1, "path": "a.ts",
            "status": "ok", "attempts": 1, "confidence": None,
        }]

    def test_partial_run_resumes_only_the_unfinished_files(self, tmp_path):
        (tmp_path / "a.ts").write_text("const a = 1;")
        (tmp_path / "b.ts").write_text("const b = 1;")
        out = tmp_path / "out"

        # First run: "a" succeeds, "b" fails outright (API error every attempt).
        provider = MockProvider("x = 1")
        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                        translate_manifests=False, verbose=False, score_confidence=False)
        # Now hand-corrupt the checkpoint to simulate b.ts having failed, since
        # MockProvider can't easily fail only one of two files by content.
        state_file = out / ".translation_state.json"
        data = json.loads(state_file.read_text())
        data["files"].append({"path": "b.ts", "status": "failed", "attempts": 3,
                              "error": "boom", "run_output": None,
                              "confidence": None, "confidence_reason": None})
        state_file.write_text(json.dumps(data))
        (out / "b.py").unlink(missing_ok=True)

        provider2 = MockProvider("y = 2")
        report = translate_repo(tmp_path, out, "ts", "python", provider=provider2,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert len(provider2.calls) == 1  # only b.ts re-translated
        assert provider2.calls[0].count("const b = 1;") == 1
        statuses = {f.path: f.status for f in report.files}
        assert statuses == {"a.ts": "ok", "b.ts": "ok"}


class TestTranslateRepoVerboseOutput:
    """
    All of these paths print(...) only under verbose=True — every other test
    in this suite passes verbose=False, which is exactly why they'd previously
    gone uncovered.
    """

    def test_empty_repo_prints_no_files_found(self, tmp_path, capsys):
        out = tmp_path / "out"
        translate_repo(tmp_path, out, "ts", "python", provider=MockProvider(),
                        translate_manifests=False, verbose=True, score_confidence=False)
        assert "No typescript files found" in capsys.readouterr().out

    def test_empty_file_prints_skipped(self, tmp_path, capsys):
        (tmp_path / "empty.ts").write_text("   \n  ")
        out = tmp_path / "out"
        translate_repo(tmp_path, out, "ts", "python", provider=MockProvider(),
                        translate_manifests=False, verbose=True, score_confidence=False)
        assert "(empty, skipped)" in capsys.readouterr().out

    def test_api_error_prints_error_message(self, tmp_path, capsys):
        (tmp_path / "main.ts").write_text("const x = 1;")
        out = tmp_path / "out"
        provider = MockProvider(Exception("quota exceeded"))
        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                        translate_manifests=False, verbose=True, score_confidence=False)
        assert "API error: quota exceeded" in capsys.readouterr().out

    def test_retry_then_succeed_prints_fixed_message(self, tmp_path, capsys):
        (tmp_path / "main.ts").write_text("const x = 1;")
        out = tmp_path / "out"
        provider = MockProvider("this is not valid python", "x = 1")
        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                        translate_manifests=False, verbose=True, score_confidence=False)
        out_text = capsys.readouterr().out
        assert "retrying" in out_text
        assert "fixed in 2 attempt" in out_text

    def test_found_source_and_test_file_counts_printed(self, tmp_path, capsys):
        (tmp_path / "math.ts").write_text("export function add(a, b) { return a + b; }")
        (tmp_path / "math.test.ts").write_text("test('adds', () => {});")
        out = tmp_path / "out"
        provider = MockProvider("x = 1")
        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                        translate_manifests=False, verbose=True, score_confidence=False)
        assert "Found 1 source + 1 test file(s)" in capsys.readouterr().out


class TestTranslateRepoTestFileSplit:
    def test_test_file_prompt_gets_framework_note_source_file_does_not(self, tmp_path):
        (tmp_path / "math.ts").write_text("export function add(a, b) { return a + b; }")
        (tmp_path / "math.test.ts").write_text(
            "import { add } from './math';\n"
            "test('adds', () => { expect(add(2, 3)).toBe(5); });\n"
        )
        out = tmp_path / "out"
        provider = CapturingProvider("x = 1")

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert report.total == 2
        test_prompt = next(p for p in provider.calls if "test('adds'" in p)
        src_prompt  = next(p for p in provider.calls if "test('adds'" not in p)
        assert "TEST file" in test_prompt
        assert "pytest" in test_prompt
        assert "TEST file" not in src_prompt

    def test_non_test_file_prompt_has_no_framework_note(self, tmp_path):
        (tmp_path / "math.ts").write_text("export function add(a, b) { return a + b; }")
        out = tmp_path / "out"
        provider = CapturingProvider("x = 1")

        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                        translate_manifests=False, verbose=False, score_confidence=False)

        assert "TEST file" not in provider.last_prompt


class TestRunTestsWithRetry:
    """
    Unlike source files (auto-fixed via _try_run inside the main loop),
    the translated test suite used to run() exactly once regardless of the
    result. This mirrors that same retry-on-failure pattern for the suite.
    """

    def test_retries_and_succeeds_on_second_attempt(self, ts_repo_with_tests, tmp_path):
        out = tmp_path / "out"
        provider = MockProvider("x = 1")

        with patch("repo_translator.agent.run_tests",
                   side_effect=[(False, "1 failed"), (True, "2 passed")]) as mock_run:
            report = translate_repo(
                ts_repo_with_tests, out, "ts", "python", provider=provider,
                translate_manifests=False, verbose=False, score_confidence=False,
                run_tests_after=True,
            )

        assert mock_run.call_count == 2
        assert report.tests_passed is True
        assert report.test_output == "2 passed"

    def test_gives_up_after_max_fix_attempts(self, ts_repo_with_tests, tmp_path):
        out = tmp_path / "out"
        provider = MockProvider("x = 1")

        with patch("repo_translator.agent.run_tests",
                   return_value=(False, "still failing")) as mock_run:
            report = translate_repo(
                ts_repo_with_tests, out, "ts", "python", provider=provider,
                translate_manifests=False, verbose=False, score_confidence=False,
                run_tests_after=True,
            )

        assert mock_run.call_count == 3  # default MAX_FIX_ATTEMPTS
        assert report.tests_passed is False
        assert report.test_output == "still failing"

    def test_offline_style_provider_never_retries(self, ts_repo_with_tests, tmp_path):
        out = tmp_path / "out"
        provider = MockProvider("x = 1")
        provider.max_fix_attempts = 1

        with patch("repo_translator.agent.run_tests",
                   return_value=(False, "failed")) as mock_run:
            translate_repo(
                ts_repo_with_tests, out, "ts", "python", provider=provider,
                translate_manifests=False, verbose=False, score_confidence=False,
                run_tests_after=True,
            )

        mock_run.assert_called_once()

    def test_no_retry_when_there_are_no_test_files(self, tmp_path):
        # rust's cargo test runs against test_files=[] (test_patterns == []
        # for the target language) — nothing to re-translate, so one attempt.
        (tmp_path / "main.py").write_text("print('hi')")
        out = tmp_path / "out"
        provider = MockProvider("fn main() {}")

        with patch("repo_translator.agent.run_tests",
                   return_value=(False, "failed")) as mock_run:
            translate_repo(
                tmp_path, out, "python", "rust", provider=provider,
                translate_manifests=False, verbose=False, score_confidence=False,
                run_tests_after=True,
            )

        mock_run.assert_called_once()

    def test_retry_re_translates_test_files_with_failure_as_context(self, ts_repo_with_tests, tmp_path):
        out = tmp_path / "out"
        provider = CapturingProvider("x = 1")

        with patch("repo_translator.agent.run_tests",
                   side_effect=[(False, "AssertionError: boom"), (True, "ok")]):
            translate_repo(
                ts_repo_with_tests, out, "ts", "python", provider=provider,
                translate_manifests=False, verbose=False, score_confidence=False,
                run_tests_after=True,
            )

        # One prompt per source file on the first pass, plus one more per
        # test file on the retry — the retry prompt must carry the failure.
        # ts_repo_with_tests has two test files (math.test.ts, util.spec.ts).
        retry_prompts = [p for p in provider.calls if "AssertionError: boom" in p]
        assert len(retry_prompts) == 2
        assert all("TEST file" in p for p in retry_prompts)

    def test_verbose_prints_retry_message(self, ts_repo_with_tests, tmp_path, capsys):
        out = tmp_path / "out"
        provider = MockProvider("x = 1")

        with patch("repo_translator.agent.run_tests",
                   side_effect=[(False, "1 failed"), (True, "ok")]):
            translate_repo(
                ts_repo_with_tests, out, "ts", "python", provider=provider,
                translate_manifests=False, verbose=True, score_confidence=False,
                run_tests_after=True,
            )

        out_text = capsys.readouterr().out
        assert "re-translating" in out_text
        assert "attempt 2/3" in out_text

    def test_emits_tests_retry_progress_event(self, ts_repo_with_tests, tmp_path):
        out = tmp_path / "out"
        provider = MockProvider("x = 1")
        events: list[dict] = []

        with patch("repo_translator.agent.run_tests",
                   side_effect=[(False, "1 failed"), (True, "ok")]):
            translate_repo(
                ts_repo_with_tests, out, "ts", "python", provider=provider,
                translate_manifests=False, verbose=False, score_confidence=False,
                run_tests_after=True, on_progress=events.append,
            )

        retry_events = [e for e in events if e["type"] == "tests_retry"]
        assert retry_events == [{"type": "tests_retry", "attempt": 2, "total": 3}]

    def test_re_translation_failure_leaves_previous_file_in_place(self, ts_repo_with_tests, tmp_path):
        # Initial-pass calls (no error_context / fix note) succeed; any retry
        # call (recognizable by the fix note _translate_once adds) raises.
        class _FlakyOnRetryProvider(LLMProvider):
            max_fix_attempts = 3

            def complete(self, prompt: str, max_tokens: int = 8096) -> str:
                if "previous translation produced this runtime error" in prompt:
                    raise Exception("quota exceeded")
                return "x = 1"

        out = tmp_path / "out"

        with patch("repo_translator.agent.run_tests",
                   return_value=(False, "still failing")):
            translate_repo(
                ts_repo_with_tests, out, "ts", "python", provider=_FlakyOnRetryProvider(),
                translate_manifests=False, verbose=False, score_confidence=False,
                run_tests_after=True,
            )

        # Both test files' dest should still hold the original translation,
        # not be deleted or left corrupt by the failed re-translation attempts.
        for name in ("math.test.py", "util.spec.py"):
            dest = out / name
            assert dest.exists()
            assert dest.read_text() == "x = 1"

    def test_verbose_prints_re_translation_failure(self, ts_repo_with_tests, tmp_path, capsys):
        class _FlakyOnRetryProvider(LLMProvider):
            max_fix_attempts = 3

            def complete(self, prompt: str, max_tokens: int = 8096) -> str:
                if "previous translation produced this runtime error" in prompt:
                    raise Exception("quota exceeded")
                return "x = 1"

        out = tmp_path / "out"

        with patch("repo_translator.agent.run_tests", return_value=(False, "still failing")):
            translate_repo(
                ts_repo_with_tests, out, "ts", "python", provider=_FlakyOnRetryProvider(),
                translate_manifests=False, verbose=True, score_confidence=False,
                run_tests_after=True,
            )

        assert "Failed to re-translate" in capsys.readouterr().out

    def test_empty_test_file_is_skipped_on_retry(self, ts_repo_with_tests, tmp_path):
        (ts_repo_with_tests / "util.spec.ts").write_text("   \n  ")
        out = tmp_path / "out"
        provider = MockProvider("x = 1")

        with patch("repo_translator.agent.run_tests",
                   side_effect=[(False, "1 failed"), (True, "ok")]):
            translate_repo(
                ts_repo_with_tests, out, "ts", "python", provider=provider,
                translate_manifests=False, verbose=False, score_confidence=False,
                run_tests_after=True,
            )

        # Empty source files were already written as empty and skipped in the
        # main loop; the retry pass must not choke re-processing them either.
        assert (out / "util.spec.py").read_text() == ""


class TestPriceLabel:
    def test_offline_is_free(self):
        assert "free" in price_label("offline", "sonnet")

    def test_ollama_is_free_local(self):
        assert "local" in price_label("ollama", "llama3")

    def test_groq_known_model_shows_price(self):
        assert "$" in price_label("groq", "llama-3.3-70b-versatile")

    def test_groq_unknown_model_shows_free_tier(self):
        assert "free tier" in price_label("groq", "some-new-model")

    def test_openai_compat_shows_base_url(self):
        label = price_label("openai-compat", "any-model", base_url="https://api.example.com")
        assert "https://api.example.com" in label

    def test_claude_known_model_shows_price(self):
        assert "$" in price_label("claude", "sonnet")

    def test_unknown_pricing(self):
        assert price_label("openai", "some-future-model") == "pricing unknown"

    def test_openai_compat_without_base_url_shows_placeholder(self):
        label = price_label("openai-compat", "any-model")
        assert "no base URL set" in label


class TestTranslateRepoProgress:
    """on_progress is the hook the web UI streams off of."""

    def test_emits_file_start_and_done(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")
        events: list[dict] = []

        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                       translate_manifests=False, verbose=False, score_confidence=False, on_progress=events.append)

        types = [e["type"] for e in events]
        assert "file_start" in types
        assert "file_done" in types
        assert types[-1] == "finished"

        done = next(e for e in events if e["type"] == "file_done")
        assert done["path"] == "index.ts"
        assert done["status"] == "ok"

    def test_emits_finished_summary_even_with_no_files(self, tmp_path):
        out      = tmp_path / "out"
        provider = MockProvider()
        events: list[dict] = []

        translate_repo(tmp_path, out, "ts", "python", provider=provider,
                       translate_manifests=False, verbose=False, score_confidence=False, on_progress=events.append)

        assert events[-1]["type"] == "finished"
        assert events[-1]["summary"]["total"] == 0

    def test_no_progress_callback_is_a_no_op(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=False)

        assert report.translated == 1


# ─────────────────────────────────────────────
# estimate_translation
# ─────────────────────────────────────────────

class TestEstimateTranslation:
    def test_returns_correct_file_count(self, tmp_path):
        (tmp_path / "a.ts").write_text("const x = 1;")
        (tmp_path / "b.ts").write_text("const y = 2;")
        est = estimate_translation(tmp_path, "ts", "python", translate_manifests=False)
        assert est["file_count"] == 2

    def test_resolves_language_aliases(self, tmp_path):
        est = estimate_translation(tmp_path, "ts", "py", translate_manifests=False)
        assert est["from_lang"] == "typescript"
        assert est["to_lang"] == "python"

    def test_empty_repo_returns_zero_cost(self, tmp_path):
        est = estimate_translation(tmp_path, "ts", "python",
                                   translate_manifests=False, score_confidence=False)
        assert est["file_count"] == 0
        assert est["estimated_cost"] == 0.0

    def test_cost_is_positive_for_nonempty_repo(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x: number = 1;" * 100)
        est = estimate_translation(tmp_path, "ts", "python",
                                   translate_manifests=False, score_confidence=False)
        assert est["estimated_cost"] > 0

    def test_input_tokens_exceed_output_tokens(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;" * 50)
        est = estimate_translation(tmp_path, "ts", "python",
                                   translate_manifests=False, score_confidence=False)
        assert est["input_tokens"] > est["output_tokens"]

    def test_manifest_count_zero_when_disabled(self, tmp_path):
        (tmp_path / "package.json").write_text('{"dependencies": {}}')
        est = estimate_translation(tmp_path, "ts", "python", translate_manifests=False)
        assert est["manifest_count"] == 0

    def test_manifest_counted_when_enabled(self, tmp_path):
        (tmp_path / "package.json").write_text('{"dependencies": {}}')
        est = estimate_translation(tmp_path, "ts", "python", translate_manifests=True)
        assert est["manifest_count"] == 1

    def test_confidence_scoring_increases_token_count(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;")
        est_with    = estimate_translation(tmp_path, "ts", "python",
                                           translate_manifests=False, score_confidence=True)
        est_without = estimate_translation(tmp_path, "ts", "python",
                                           translate_manifests=False, score_confidence=False)
        assert est_with["input_tokens"] > est_without["input_tokens"]

    def test_ollama_cost_is_zero(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;" * 100)
        est = estimate_translation(tmp_path, "ts", "python",
                                   translate_manifests=False, provider="ollama")
        assert est["estimated_cost"] == 0.0

    def test_unknown_provider_model_returns_none_cost(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;")
        est = estimate_translation(tmp_path, "ts", "python",
                                   translate_manifests=False,
                                   provider="openai", model="gpt-99-ultra")
        assert est["estimated_cost"] is None

    def test_openai_compat_cost_is_none(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;")
        est = estimate_translation(tmp_path, "ts", "python",
                                   translate_manifests=False, provider="openai-compat")
        assert est["estimated_cost"] is None

    def test_unreadable_source_file_is_skipped_not_raised(self, tmp_path, monkeypatch):
        (tmp_path / "main.ts").write_text("const x = 1;")

        def _raise(self, *a, **kw):
            raise OSError("permission denied")
        monkeypatch.setattr(Path, "read_text", _raise)

        est = estimate_translation(tmp_path, "ts", "python",
                                   translate_manifests=False, score_confidence=False)
        assert est["file_count"] == 1  # collect_files globs by name, doesn't read content

    def test_unreadable_manifest_is_skipped_not_raised(self, tmp_path, monkeypatch):
        (tmp_path / "package.json").write_text('{"dependencies": {}}')
        (tmp_path / "main.ts").write_text("const x = 1;")

        def _raise(self, *a, **kw):
            raise OSError("permission denied")
        monkeypatch.setattr(Path, "read_text", _raise)

        est = estimate_translation(tmp_path, "ts", "python",
                                   translate_manifests=True, score_confidence=False)
        assert est["manifest_count"] == 0  # counted only on successful read


# ─────────────────────────────────────────────
# _score_confidence
# ─────────────────────────────────────────────

class TestScoreConfidence:
    def test_returns_valid_score_and_reason(self):
        resp     = json.dumps({"score": 82, "reason": "Clean translation"})
        provider = MockProvider(resp)
        score, reason = _score_confidence(
            provider, "const x = 1;", "x = 1", "typescript", "python", 1, True
        )
        assert score == 82
        assert reason == "Clean translation"

    def test_score_clamped_to_0_100(self):
        resp     = json.dumps({"score": 150, "reason": "Too high"})
        provider = MockProvider(resp)
        score, _ = _score_confidence(
            provider, "src", "trans", "typescript", "python", 1, True
        )
        assert score == 100

    def test_negative_score_clamped_to_0(self):
        resp     = json.dumps({"score": -20, "reason": "Too low"})
        provider = MockProvider(resp)
        score, _ = _score_confidence(
            provider, "src", "trans", "typescript", "python", 1, True
        )
        assert score == 0

    def test_strips_json_markdown_fence(self):
        resp     = "```json\n" + json.dumps({"score": 70, "reason": "fenced"}) + "\n```"
        provider = MockProvider(resp)
        score, reason = _score_confidence(
            provider, "src", "trans", "typescript", "python", 1, True
        )
        assert score == 70
        assert reason == "fenced"

    def test_strips_bare_markdown_fence(self):
        # Fence without the "json" language tag.
        resp     = "```\n" + json.dumps({"score": 60, "reason": "bare fence"}) + "\n```"
        provider = MockProvider(resp)
        score, reason = _score_confidence(
            provider, "src", "trans", "typescript", "python", 1, True
        )
        assert score == 60

    def test_reason_truncated_to_200_chars(self):
        long_reason = "x" * 300
        resp     = json.dumps({"score": 80, "reason": long_reason})
        provider = MockProvider(resp)
        _, reason = _score_confidence(
            provider, "src", "trans", "typescript", "python", 1, True
        )
        assert len(reason) == 200

    def test_fallback_on_invalid_json(self):
        provider = MockProvider("not valid json at all")
        score, reason = _score_confidence(
            provider, "src", "trans", "typescript", "python", 1, True
        )
        assert score == 50
        assert "failed" in reason

    def test_fallback_on_provider_error(self):
        provider = MockProvider(Exception("network error"))
        score, reason = _score_confidence(
            provider, "src", "trans", "typescript", "python", 1, True
        )
        assert score == 50

    def test_confidence_stored_on_file_result(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x = 1;")
        out = tmp_path / "out"
        conf_json = json.dumps({"score": 75, "reason": "Good translation"})
        # First call returns translation, second returns confidence JSON
        provider  = MockProvider("x = 1", conf_json)

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=True)

        assert report.files[0].confidence == 75
        assert report.files[0].confidence_reason == "Good translation"

    def test_high_confidence_count(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        conf_json = json.dumps({"score": 85, "reason": "Good"})
        provider  = MockProvider("x = 1", conf_json)

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=True)

        assert report.high_confidence == 1
        assert report.needs_review == 0

    def test_needs_review_count(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        conf_json = json.dumps({"score": 45, "reason": "Broken imports"})
        provider  = MockProvider("x = 1", conf_json)

        report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                translate_manifests=False, verbose=False, score_confidence=True)

        assert report.needs_review == 1
        assert report.high_confidence == 0


# ─────────────────────────────────────────────
# Prompt construction — regression: source code must not be re-indented
# ─────────────────────────────────────────────

class TestTranslateOncePrompt:
    """
    Regression for the textwrap.dedent bug: when source_code was interpolated
    *inside* the dedented template, its un-indented lines defeated dedent's
    common-prefix calc and leaked 8 spaces of template indentation into the
    fenced code block — producing invalid Python from the offline provider.
    """

    def _code_block(self, prompt: str) -> str:
        # Extract the content between the last pair of ``` fences.
        first = prompt.index("```") + 3
        first = prompt.index("\n", first) + 1
        last  = prompt.index("```", first)
        return prompt[first:last]

    def test_source_lines_not_reindented(self):
        source = "function add(a, b) {\n  return a + b;\n}\nconsole.log(add(2, 3));\n"
        provider = CapturingProvider()
        _translate_once(provider, source, "typescript", "python")

        block = self._code_block(provider.last_prompt)
        # The original zero-indent lines must remain at zero indent.
        assert "function add(a, b) {" in block
        assert "\nconsole.log(add(2, 3));" in block
        assert "        function add" not in block  # no leaked template indent

    def test_source_code_preserved_verbatim(self):
        source = "const x = 1;\nconst y = 2;\n"
        provider = CapturingProvider()
        _translate_once(provider, source, "typescript", "python")

        block = self._code_block(provider.last_prompt)
        assert block.rstrip("\n") == source.rstrip("\n")

    def test_error_context_included_as_fix_note(self):
        provider = CapturingProvider()
        _translate_once(
            provider, "const x = 1;", "typescript", "python",
            error_context="IndentationError: unexpected indent",
        )
        assert "IndentationError: unexpected indent" in provider.last_prompt
        assert "previous translation produced this runtime error" in provider.last_prompt

    def test_no_error_context_omits_fix_note(self):
        provider = CapturingProvider()
        _translate_once(provider, "const x = 1;", "typescript", "python")
        assert "previous translation produced this runtime error" not in provider.last_prompt

    def test_is_test_injects_target_framework(self):
        provider = CapturingProvider()
        _translate_once(
            provider, "test('adds', () => {});", "typescript", "python", is_test=True,
        )
        assert "TEST file" in provider.last_prompt
        assert "pytest" in provider.last_prompt  # TEST_FRAMEWORK_MAP[("typescript","python")]

    def test_is_test_false_omits_test_note(self):
        provider = CapturingProvider()
        _translate_once(provider, "const x = 1;", "typescript", "python", is_test=False)
        assert "TEST file" not in provider.last_prompt

    def test_unmapped_language_pair_uses_generic_framework_note(self):
        provider = CapturingProvider()
        _translate_once(
            provider, "x = 1", "python", "swift", is_test=True,
        )
        assert "the standard test framework" in provider.last_prompt


# ─────────────────────────────────────────────
# _split_into_chunks / large-file chunking
# ─────────────────────────────────────────────

class TestSplitIntoChunks:
    def test_source_under_threshold_returned_unchanged(self):
        source = "a = 1\n\nb = 2\n"
        assert _split_into_chunks(source, threshold=1000) == [source]

    def test_source_exactly_at_threshold_not_split(self):
        source = "x" * 50
        assert _split_into_chunks(source, threshold=50) == [source]

    def test_splits_at_blank_line_boundaries(self):
        block_a = "def a():\n    pass"
        block_b = "def b():\n    pass"
        block_c = "def c():\n    pass"
        source = f"{block_a}\n\n{block_b}\n\n{block_c}"
        chunks = _split_into_chunks(source, threshold=len(block_a) + 1)
        assert len(chunks) > 1
        # No chunk boundary falls inside a block.
        for block in (block_a, block_b, block_c):
            assert sum(block in c for c in chunks) == 1

    def test_concatenated_chunks_reproduce_source_exactly(self):
        source = "one\n\ntwo\n\nthree\n\nfour\n\nfive\n"
        chunks = _split_into_chunks(source, threshold=8)
        assert len(chunks) > 1
        assert "".join(chunks) == source

    def test_greedily_packs_small_blocks_together(self):
        source = "a\n\nb\n\nc\n\nd\n"
        chunks = _split_into_chunks(source, threshold=100)
        # Well under the threshold — everything fits in one chunk even
        # though there are several blank-line-separated blocks.
        assert chunks == [source]

    def test_single_oversized_block_kept_whole_rather_than_split(self):
        # No blank lines at all: nothing to split on, so the whole thing
        # is returned as one (oversized) chunk rather than dropped or cut
        # mid-line.
        source = "x = 1\n" * 100
        chunks = _split_into_chunks(source, threshold=10)
        assert chunks == [source]

    def test_default_threshold_is_the_module_constant(self):
        short = "a = 1\n"
        assert _split_into_chunks(short) == [short]
        long_source = "a" * (agent.CHUNK_THRESHOLD_CHARS + 1)
        assert _split_into_chunks(long_source) == [long_source]  # no blank line to split on


class TestTranslateOnceChunking:
    def test_small_file_makes_a_single_provider_call(self, monkeypatch):
        monkeypatch.setattr(agent, "CHUNK_THRESHOLD_CHARS", 10_000)
        provider = CapturingProvider("translated")
        result = _translate_once(provider, "x = 1\n", "python", "rust")
        assert len(provider.calls) == 1
        assert result == "translated"

    def test_large_file_is_split_into_multiple_calls(self, monkeypatch):
        monkeypatch.setattr(agent, "CHUNK_THRESHOLD_CHARS", 20)
        source = "a" * 10 + "\n\n" + "b" * 10 + "\n\n" + "c" * 10
        provider = CapturingProvider("OUT")
        result = _translate_once(provider, source, "python", "rust")
        assert len(provider.calls) == 3
        assert result == "OUTOUTOUT"  # concatenated, in order

    def test_chunk_prompts_are_numbered_and_note_the_total(self, monkeypatch):
        monkeypatch.setattr(agent, "CHUNK_THRESHOLD_CHARS", 20)
        source = "a" * 10 + "\n\n" + "b" * 10 + "\n\n" + "c" * 10
        provider = CapturingProvider("OUT")
        _translate_once(provider, source, "python", "rust")
        assert "chunk 1 of 3" in provider.calls[0]
        assert "chunk 2 of 3" in provider.calls[1]
        assert "chunk 3 of 3" in provider.calls[2]

    def test_unchunked_prompt_has_no_chunk_note(self, monkeypatch):
        monkeypatch.setattr(agent, "CHUNK_THRESHOLD_CHARS", 10_000)
        provider = CapturingProvider()
        _translate_once(provider, "x = 1\n", "python", "rust")
        assert "chunk" not in provider.last_prompt.lower()

    def test_error_context_and_is_test_carried_into_every_chunk(self, monkeypatch):
        monkeypatch.setattr(agent, "CHUNK_THRESHOLD_CHARS", 20)
        source = "a" * 10 + "\n\n" + "b" * 10 + "\n\n" + "c" * 10
        provider = CapturingProvider("OUT")
        _translate_once(
            provider, source, "python", "rust",
            is_test=True, error_context="boom: it broke",
        )
        assert len(provider.calls) == 3
        for call in provider.calls:
            assert "TEST file" in call
            assert "boom: it broke" in call

    def test_chunk_boundary_source_preserved_verbatim_across_calls(self, monkeypatch):
        monkeypatch.setattr(agent, "CHUNK_THRESHOLD_CHARS", 20)
        block_a = "a" * 10
        block_b = "b" * 10
        source = f"{block_a}\n\n{block_b}"
        provider = CapturingProvider("OUT")
        _translate_once(provider, source, "python", "rust")
        assert block_a in provider.calls[0]
        assert block_b not in provider.calls[0]
        assert block_b in provider.calls[1]


class TestTranslateRepoChunking:
    def test_large_file_records_chunk_count_in_file_result(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "CHUNK_THRESHOLD_CHARS", 20)
        (tmp_path / "big.py").write_text("a" * 10 + "\n\n" + "b" * 10 + "\n\n" + "c" * 10)
        out = tmp_path / "out"
        provider = MockProvider("OUT")

        report = translate_repo(
            tmp_path, out, "python", "rust", provider=provider,
            translate_manifests=False, verbose=False, score_confidence=False,
        )

        assert report.files[0].status == "ok"
        assert report.files[0].chunks == 3

    def test_small_file_leaves_chunks_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "CHUNK_THRESHOLD_CHARS", 10_000)
        (tmp_path / "small.py").write_text("x = 1\n")
        out = tmp_path / "out"
        provider = MockProvider("y = 1")

        report = translate_repo(
            tmp_path, out, "python", "rust", provider=provider,
            translate_manifests=False, verbose=False, score_confidence=False,
        )

        assert report.files[0].chunks is None

    def test_verbose_output_notes_the_chunk_count(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(agent, "CHUNK_THRESHOLD_CHARS", 20)
        (tmp_path / "big.py").write_text("a" * 10 + "\n\n" + "b" * 10 + "\n\n" + "c" * 10)
        out = tmp_path / "out"
        provider = MockProvider("OUT")

        translate_repo(
            tmp_path, out, "python", "rust", provider=provider,
            translate_manifests=False, verbose=True, score_confidence=False,
        )

        assert "(split into 3 chunks)" in capsys.readouterr().out

    def test_chunked_translation_written_concatenated_to_output_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "CHUNK_THRESHOLD_CHARS", 20)
        (tmp_path / "big.py").write_text("a" * 10 + "\n\n" + "b" * 10)
        out = tmp_path / "out"
        provider = MockProvider("print(1)\n")

        translate_repo(
            tmp_path, out, "python", "rust", provider=provider,
            translate_manifests=False, verbose=False, score_confidence=False,
        )

        # Runner isn't configured for rust, so _try_run soft-passes and the
        # concatenation of both chunk responses is written verbatim.
        assert (out / "big.rs").read_text() == "print(1)\n" * 2


# ─────────────────────────────────────────────
# Cross-file context (--cross-file-context)
# ─────────────────────────────────────────────

class TestExtractSymbols:
    def test_python_top_level_def_and_class(self):
        source = "def foo():\n    pass\n\nclass Bar:\n    def method(self):\n        pass\n"
        assert _extract_symbols(source, "python") == ["foo", "Bar"]

    def test_python_indented_defs_not_top_level(self):
        source = "class Bar:\n    def method(self):\n        pass\n"
        # "method" is indented (inside the class), so it isn't picked up —
        # only the top-level "Bar".
        assert _extract_symbols(source, "python") == ["Bar"]

    def test_typescript_exported_declarations(self):
        source = (
            "export function add(a, b) { return a + b; }\n"
            "export class Widget {}\n"
            "export const PI = 3.14;\n"
            "function helper() {}\n"  # not exported — should be excluded
        )
        assert _extract_symbols(source, "typescript") == ["add", "Widget", "PI"]

    def test_go_func_and_type(self):
        source = "func Add(a, b int) int {\n\treturn a + b\n}\n\ntype Point struct{}\n"
        assert _extract_symbols(source, "go") == ["Add", "Point"]

    def test_go_method_with_receiver_captures_method_name(self):
        source = "func (p *Point) String() string {\n\treturn \"\"\n}\n"
        assert _extract_symbols(source, "go") == ["String"]

    def test_rust_pub_items(self):
        source = "pub fn run() {}\npub struct Config {}\nfn private_helper() {}\n"
        assert _extract_symbols(source, "rust") == ["run", "Config"]

    def test_unmapped_language_returns_empty_list(self):
        assert _extract_symbols("class Foo {}", "cpp") == []
        assert _extract_symbols("int main() {}", "c") == []

    def test_no_duplicate_names(self):
        source = "def foo():\n    pass\n\ndef foo():\n    pass\n"
        assert _extract_symbols(source, "python") == ["foo"]

    def test_symbol_count_capped(self):
        source = "\n\n".join(f"def f{i}():\n    pass" for i in range(50))
        symbols = _extract_symbols(source, "python")
        assert len(symbols) == agent._MAX_SYMBOLS_PER_FILE


class TestBuildRepoMap:
    def test_maps_relative_paths_to_symbols(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        (tmp_path / "b.py").write_text("class Bar:\n    pass\n")
        files = [tmp_path / "a.py", tmp_path / "b.py"]

        repo_map = _build_repo_map(files, tmp_path, "python")

        assert repo_map == {"a.py": ["foo"], "b.py": ["Bar"]}

    def test_nested_paths_are_relative_to_repo_root(self, tmp_path):
        nested = tmp_path / "pkg"
        nested.mkdir()
        (nested / "mod.py").write_text("def f():\n    pass\n")
        files = [nested / "mod.py"]

        repo_map = _build_repo_map(files, tmp_path, "python")

        assert repo_map == {"pkg/mod.py": ["f"]}

    def test_unreadable_file_is_skipped_not_raised(self, tmp_path, monkeypatch):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")

        def _raise(self, *a, **kw):
            raise OSError("permission denied")
        monkeypatch.setattr(Path, "read_text", _raise)

        repo_map = _build_repo_map([tmp_path / "a.py"], tmp_path, "python")
        assert repo_map == {}


class TestFormatRepoMap:
    def test_excludes_the_current_file(self):
        repo_map = {"a.py": ["foo"], "b.py": ["Bar"]}
        formatted = _format_repo_map(repo_map, exclude="a.py")
        assert "a.py" not in formatted
        assert "b.py" in formatted

    def test_lists_symbols_after_the_path(self):
        formatted = _format_repo_map({"a.py": ["foo", "Bar"]}, exclude="")
        assert formatted == "- a.py: foo, Bar"

    def test_file_with_no_symbols_has_no_trailing_colon(self):
        formatted = _format_repo_map({"a.py": []}, exclude="")
        assert formatted == "- a.py"

    def test_empty_map_returns_empty_string(self):
        assert _format_repo_map({}, exclude="") == ""

    def test_truncates_past_threshold_and_notes_omitted_count(self):
        repo_map = {f"file{i}.py": ["sym"] for i in range(20)}
        formatted = _format_repo_map(repo_map, exclude="", threshold=50)
        assert "more file(s) omitted" in formatted
        # Not every file made it into the (small) threshold.
        assert formatted.count("- file") < 20


class TestTranslateOnceCrossFileContext:
    def test_no_repo_context_omits_the_note_and_listing(self):
        provider = CapturingProvider()
        _translate_once(provider, "x = 1\n", "python", "go")
        assert "Other files in this repo" not in provider.last_prompt

    def test_repo_context_appended_after_the_source_block(self):
        provider = CapturingProvider()
        _translate_once(
            provider, "x = 1\n", "python", "go",
            repo_context="- other.py: helper",
        )
        assert "Other files in this repo" in provider.last_prompt
        assert "- other.py: helper" in provider.last_prompt
        # Comes after the fenced source block, not inside it.
        assert provider.last_prompt.index("```\n") < provider.last_prompt.index("other.py")

    def test_repo_context_included_in_every_chunk(self, monkeypatch):
        monkeypatch.setattr(agent, "CHUNK_THRESHOLD_CHARS", 20)
        source = "a" * 10 + "\n\n" + "b" * 10 + "\n\n" + "c" * 10
        provider = CapturingProvider()
        _translate_once(
            provider, source, "python", "go", repo_context="- other.py: helper",
        )
        assert len(provider.calls) == 3
        for call in provider.calls:
            assert "- other.py: helper" in call


class TestTranslateRepoCrossFileContext:
    def test_disabled_by_default_prompt_has_no_repo_map(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        (tmp_path / "b.py").write_text("def bar():\n    pass\n")
        out = tmp_path / "out"
        provider = CapturingProvider("x = 1")

        translate_repo(
            tmp_path, out, "python", "rust", provider=provider,
            translate_manifests=False, verbose=False, score_confidence=False,
        )

        assert all("Other files in this repo" not in c for c in provider.calls)

    def test_enabled_includes_other_files_symbols_excluding_self(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        (tmp_path / "b.py").write_text("def bar():\n    pass\n")
        out = tmp_path / "out"
        provider = CapturingProvider("x = 1")

        translate_repo(
            tmp_path, out, "python", "rust", provider=provider,
            translate_manifests=False, verbose=False, score_confidence=False,
            cross_file_context=True,
        )

        # files are processed in sorted order (a.py, then b.py); each call's
        # prompt should mention the *other* file, never itself.
        a_prompt, b_prompt = provider.calls
        assert "b.py" in a_prompt and "bar" in a_prompt
        assert "a.py" not in a_prompt.split("Other files in this repo")[1]
        assert "a.py" in b_prompt and "foo" in b_prompt

    def test_cross_file_context_reaches_test_retry_translation(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        (tmp_path / "a_test.py").write_text("def test_foo():\n    assert foo() is None\n")
        out = tmp_path / "out"
        provider = CapturingProvider("assert False")

        with patch("repo_translator.agent.run_tests", return_value=(False, "boom")):
            translate_repo(
                tmp_path, out, "python", "rust", provider=provider,
                translate_manifests=False, verbose=False, score_confidence=False,
                run_tests_after=True, cross_file_context=True,
            )

        # The last call is the test-retry re-translation of a_test.py; it
        # should still carry the repo map (mentioning a.py's "foo").
        assert "a.py" in provider.last_prompt
        assert "foo" in provider.last_prompt
