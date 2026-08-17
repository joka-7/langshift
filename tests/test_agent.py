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

from repo_translator.agent import (
    LANGUAGE_META,
    _is_test_file,
    _output_path,
    _score_confidence,
    _translate_once,
    _try_run,
    collect_files,
    estimate_translation,
    price_label,
    resolve_language,
    run_tests,
    translate_repo,
)


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
