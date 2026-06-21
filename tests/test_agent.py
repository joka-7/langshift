"""
Tests for repo_translator.agent

All tests are pure unit tests — no real API calls, no network.
Providers are mocked via MockProvider.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from repo_translator.providers.base import LLMProvider
from repo_translator.agent import (
    LANGUAGE_META,
    SKIP_DIRS,
    _ALIAS_MAP,
    _output_path,
    _translate_once,
    _try_run,
    _score_confidence,
    collect_files,
    estimate_translation,
    price_label,
    resolve_language,
    translate_repo,
)


# ─────────────────────────────────────────────
# Mock provider
# ─────────────────────────────────────────────

class MockProvider(LLMProvider):
    """Returns responses from a queue; raises Exceptions if queued."""
    def __init__(self, *responses):
        self._queue = list(responses) if responses else [""]
        self._idx   = 0

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        resp = self._queue[min(self._idx, len(self._queue) - 1)]
        self._idx += 1
        if isinstance(resp, Exception):
            raise resp
        return resp


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


# ─────────────────────────────────────────────
# translate_repo
# ─────────────────────────────────────────────

class TestTranslateRepo:
    def test_translates_single_file(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x: number = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                    verbose=False, score_confidence=False)

        assert report.translated == 1
        assert report.failed == 0
        assert (out / "index.py").exists()
        assert (out / "index.py").read_text() == "x = 1"

    def test_skips_empty_files(self, tmp_path):
        (tmp_path / "empty.ts").write_text("   \n  ")
        out      = tmp_path / "out"
        provider = MockProvider("# empty")

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                    verbose=False, score_confidence=False)

        assert report.skipped == 1
        assert report.translated == 0

    def test_handles_no_source_files(self, tmp_path):
        out      = tmp_path / "out"
        provider = MockProvider()

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                    verbose=False, score_confidence=False)

        assert report.translated == 0
        assert report.total == 0

    def test_preserves_directory_structure(self, tmp_path):
        src = tmp_path / "src" / "utils"
        src.mkdir(parents=True)
        (src / "helpers.ts").write_text("export function add(a: number, b: number) { return a + b; }")
        out      = tmp_path / "out"
        provider = MockProvider("def add(a, b): return a + b")

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            translate_repo(tmp_path, out, "ts", "python", provider=provider,
                           verbose=False, score_confidence=False)

        assert (out / "src" / "utils" / "helpers.py").exists()

    def test_report_contains_correct_language_info(self, tmp_path):
        (tmp_path / "main.ts").write_text("console.log('hi')")
        out      = tmp_path / "out"
        provider = MockProvider("print('hi')")

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                    verbose=False, score_confidence=False)

        assert report.from_lang == "typescript"
        assert report.to_lang == "python"

    def test_api_error_marks_file_as_failed(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider(Exception("API quota exceeded"))

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                    verbose=False, score_confidence=False)

        assert report.failed == 1
        assert report.translated == 0
        assert "API quota exceeded" in report.files[0].error

    def test_multiple_files_all_translated(self, tmp_path):
        for name in ["a.ts", "b.ts", "c.ts"]:
            (tmp_path / name).write_text(f"const {name[0]} = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                    verbose=False, score_confidence=False)

        assert report.translated == 3
        assert report.total == 3

    def test_elapsed_seconds_is_positive(self, tmp_path):
        (tmp_path / "main.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                    verbose=False, score_confidence=False)

        assert report.elapsed_seconds >= 0

    def test_resolves_language_aliases(self, tmp_path):
        (tmp_path / "x.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "py", provider=provider,
                                    verbose=False, score_confidence=False)

        assert report.from_lang == "typescript"
        assert report.to_lang == "python"


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


class TestTranslateRepoProgress:
    """on_progress is the hook the web UI streams off of."""

    def test_emits_file_start_and_done(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")
        events: list[dict] = []

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            translate_repo(tmp_path, out, "ts", "python", provider=provider,
                           verbose=False, score_confidence=False, on_progress=events.append)

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

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            translate_repo(tmp_path, out, "ts", "python", provider=provider,
                           verbose=False, score_confidence=False, on_progress=events.append)

        assert events[-1]["type"] == "finished"
        assert events[-1]["summary"]["total"] == 0

    def test_no_progress_callback_is_a_no_op(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        provider = MockProvider("x = 1")

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                    verbose=False, score_confidence=False)

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

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                    verbose=False, score_confidence=True)

        assert report.files[0].confidence == 75
        assert report.files[0].confidence_reason == "Good translation"

    def test_high_confidence_count(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        conf_json = json.dumps({"score": 85, "reason": "Good"})
        provider  = MockProvider("x = 1", conf_json)

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                    verbose=False, score_confidence=True)

        assert report.high_confidence == 1
        assert report.needs_review == 0

    def test_needs_review_count(self, tmp_path):
        (tmp_path / "index.ts").write_text("const x = 1;")
        out      = tmp_path / "out"
        conf_json = json.dumps({"score": 45, "reason": "Broken imports"})
        provider  = MockProvider("x = 1", conf_json)

        with patch("repo_translator.agent.translate_manifest", return_value={"translated": []}):
            report = translate_repo(tmp_path, out, "ts", "python", provider=provider,
                                    verbose=False, score_confidence=True)

        assert report.needs_review == 1
        assert report.high_confidence == 0


# ─────────────────────────────────────────────
# Prompt construction — regression: source code must not be re-indented
# ─────────────────────────────────────────────

class CapturingProvider(LLMProvider):
    """Records the last prompt it was given and echoes a fixed response."""
    def __init__(self, response: str = "x = 1"):
        self.last_prompt: str | None = None
        self._response = response

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        self.last_prompt = prompt
        return self._response


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
