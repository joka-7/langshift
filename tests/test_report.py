"""
Tests for repo_translator.report
"""

from __future__ import annotations

import json

from repo_translator.report import FileResult, TranslationReport

# ─────────────────────────────────────────────
# FileResult
# ─────────────────────────────────────────────

class TestFileResult:
    def test_defaults(self):
        f = FileResult(path="src/main.ts", status="ok")
        assert f.attempts == 1
        assert f.error is None
        assert f.run_output is None
        assert f.chunks is None

    def test_stores_error(self):
        f = FileResult(path="x.ts", status="failed", error="SyntaxError")
        assert f.error == "SyntaxError"

    def test_stores_chunks(self):
        f = FileResult(path="big.ts", status="ok", chunks=3)
        assert f.chunks == 3

    def test_external_chat_urls_defaults_to_none(self):
        f = FileResult(path="src/main.ts", status="ok")
        assert f.external_chat_urls is None

    def test_stores_external_chat_urls(self):
        f = FileResult(
            path="x.ts", status="failed", error="boom",
            external_chat_urls={"Claude": "https://claude.ai/new?q=x"},
        )
        assert f.external_chat_urls == {"Claude": "https://claude.ai/new?q=x"}


# ─────────────────────────────────────────────
# TranslationReport — computed properties
# ─────────────────────────────────────────────

def _make_report(**kwargs) -> TranslationReport:
    defaults = dict(
        from_lang="typescript",
        to_lang="python",
        input_path="/input",
        output_path="/output",
    )
    defaults.update(kwargs)
    return TranslationReport(**defaults)


class TestTranslationReportProperties:
    def test_total_counts_all_files(self):
        r = _make_report()
        r.files = [
            FileResult("a.ts", "ok"),
            FileResult("b.ts", "failed"),
            FileResult("c.ts", "skipped"),
        ]
        assert r.total == 3

    def test_translated_counts_ok_and_ok_with_warnings(self):
        r = _make_report()
        r.files = [
            FileResult("a.ts", "ok"),
            FileResult("b.ts", "ok_with_warnings"),
            FileResult("c.ts", "failed"),
            FileResult("d.ts", "skipped"),
        ]
        assert r.translated == 2

    def test_failed_counts_only_failed(self):
        r = _make_report()
        r.files = [
            FileResult("a.ts", "ok"),
            FileResult("b.ts", "failed"),
            FileResult("c.ts", "failed"),
        ]
        assert r.failed == 2

    def test_skipped_counts_only_skipped(self):
        r = _make_report()
        r.files = [
            FileResult("a.ts", "skipped"),
            FileResult("b.ts", "ok"),
        ]
        assert r.skipped == 1

    def test_needed_retry_counts_multi_attempt_successes(self):
        r = _make_report()
        r.files = [
            FileResult("a.ts", "ok", attempts=1),
            FileResult("b.ts", "ok_with_warnings", attempts=2),
            FileResult("c.ts", "ok", attempts=3),
            FileResult("d.ts", "failed", attempts=3),  # failed doesn't count
        ]
        assert r.needed_retry == 2

    def test_empty_report(self):
        r = _make_report()
        assert r.total == 0
        assert r.translated == 0
        assert r.failed == 0
        assert r.skipped == 0
        assert r.needed_retry == 0


# ─────────────────────────────────────────────
# save() — JSON and Markdown output
# ─────────────────────────────────────────────

class TestTranslationReportSave:
    def _full_report(self) -> TranslationReport:
        r = _make_report()
        r.elapsed_seconds = 12.5
        r.files = [
            FileResult("src/main.ts", "ok", attempts=1),
            FileResult("src/utils.ts", "ok_with_warnings", attempts=2, error="timeout"),
            FileResult("src/empty.ts", "skipped"),
            FileResult("src/broken.ts", "failed", error="IndentationError: unexpected indent"),
        ]
        r.manifest_translated = ["/output/requirements.txt"]
        return r

    def test_saves_json_file(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        assert (tmp_path / "translation_report.json").exists()

    def test_saves_markdown_file(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        assert (tmp_path / "translation_report.md").exists()

    def test_json_summary_counts_are_correct(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        data = json.loads((tmp_path / "translation_report.json").read_text())
        assert data["summary"]["total"] == 4
        assert data["summary"]["translated"] == 2
        assert data["summary"]["failed"] == 1
        assert data["summary"]["skipped"] == 1
        assert data["summary"]["needed_retry"] == 1
        assert data["summary"]["manifests_translated"] == 1

    def test_json_contains_test_results(self, tmp_path):
        # Regression: save() used to omit tests_passed/test_output entirely,
        # so --run-tests results were invisible in translation_report.json
        # (and therefore in GET /api/jobs/{id}/report, which reads this file).
        r = self._full_report()
        r.tests_passed = False
        r.test_output = "2 failed, 3 passed"
        r.save(tmp_path)
        data = json.loads((tmp_path / "translation_report.json").read_text())
        assert data["tests_passed"] is False
        assert data["test_output"] == "2 failed, 3 passed"

    def test_json_test_results_default_to_none(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        data = json.loads((tmp_path / "translation_report.json").read_text())
        assert data["tests_passed"] is None
        assert data["test_output"] is None

    def test_json_contains_language_info(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        data = json.loads((tmp_path / "translation_report.json").read_text())
        assert data["from_lang"] == "typescript"
        assert data["to_lang"] == "python"

    def test_json_contains_all_files(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        data = json.loads((tmp_path / "translation_report.json").read_text())
        assert len(data["files"]) == 4

    def test_json_round_trips_chunks(self, tmp_path):
        r = _make_report()
        r.files = [FileResult("big.ts", "ok", chunks=4), FileResult("small.ts", "ok")]
        r.save(tmp_path)
        data = json.loads((tmp_path / "translation_report.json").read_text())
        assert data["files"][0]["chunks"] == 4
        assert data["files"][1]["chunks"] is None

    def test_markdown_notes_chunked_file(self, tmp_path):
        r = _make_report()
        r.files = [FileResult("big.ts", "ok", chunks=4)]
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "split into 4 chunks" in md

    def test_markdown_unchunked_file_has_no_chunk_note(self, tmp_path):
        r = _make_report()
        r.files = [FileResult("small.ts", "ok")]
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "chunks" not in md

    def test_json_elapsed_seconds(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        data = json.loads((tmp_path / "translation_report.json").read_text())
        assert data["elapsed_seconds"] == 12.5

    def test_markdown_contains_from_lang(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "typescript" in md

    def test_markdown_contains_to_lang(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "python" in md

    def test_markdown_mentions_failed_file(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "src/broken.ts" in md

    def test_markdown_failed_file_with_external_chat_urls_links_them(self, tmp_path):
        r = _make_report()
        r.files = [FileResult(
            "a.ts", "failed", error="boom",
            external_chat_urls={"Claude": "https://claude.ai/new?q=x"},
        )]
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "Or ask directly:" in md
        assert "[Claude](https://claude.ai/new?q=x)" in md

    def test_markdown_failed_file_without_external_chat_urls_has_no_offer(self, tmp_path):
        r = _make_report()
        r.files = [FileResult("a.ts", "failed", error="boom")]
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "Or ask directly:" not in md

    def test_json_round_trips_external_chat_urls(self, tmp_path):
        r = _make_report()
        r.files = [FileResult(
            "a.ts", "failed", error="boom",
            external_chat_urls={"Claude": "https://claude.ai/new?q=x"},
        )]
        r.save(tmp_path)
        data = json.loads((tmp_path / "translation_report.json").read_text())
        assert data["files"][0]["external_chat_urls"] == {"Claude": "https://claude.ai/new?q=x"}

    def test_markdown_mentions_manifest(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "requirements.txt" in md

    def test_markdown_high_confidence_file_has_no_review_flag(self, tmp_path):
        # Regression-guard: _full_report() never set confidence, so the
        # conf_row summary line, the per-file confidence score, and the
        # "needs review" flag were entirely untested.
        r = _make_report()
        r.files = [FileResult("a.ts", "ok", confidence=95, confidence_reason="clean translation")]
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "**Confidence**" in md
        assert "95/100" in md
        assert "clean translation" in md
        assert "needs review" not in md

    def test_markdown_low_confidence_file_flagged_needs_review(self, tmp_path):
        r = _make_report()
        r.files = [FileResult("a.ts", "ok", confidence=40)]
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "40/100" in md
        assert "needs review" in md

    def test_markdown_unscored_file_has_no_confidence_marker(self, tmp_path):
        r = _make_report()
        r.files = [FileResult("a.ts", "ok")]
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "**Confidence**" not in md
        assert "/100" not in md

    def test_markdown_mentions_skipped_file(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "src/empty.ts" in md
        assert "Skipped (empty)" in md

    def test_markdown_retried_file_flagged(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "needed retry" in md

    def test_creates_output_dir_if_missing(self, tmp_path):
        r = self._full_report()
        new_dir = tmp_path / "deep" / "nested" / "dir"
        r.save(new_dir)
        assert (new_dir / "translation_report.json").exists()


# ─────────────────────────────────────────────
# print_summary() — every conditional, all previously uncovered
# ─────────────────────────────────────────────

class TestPrintSummary:
    def test_bare_report_prints_header_and_footer(self, capsys):
        r = _make_report()
        r.print_summary()
        out = capsys.readouterr().out
        assert "Translation Report" in out
        assert "typescript" in out
        assert "python" in out
        assert "0 / 0 translated" in out
        assert "/output" in out
        assert "✅" in out

    def test_failed_report_uses_warning_icon(self, capsys):
        r = _make_report()
        r.files = [FileResult("a.ts", "failed", error="boom")]
        r.print_summary()
        assert "⚠️" in capsys.readouterr().out

    def test_skipped_line_only_when_present(self, capsys):
        r = _make_report()
        r.files = [FileResult("a.ts", "skipped")]
        r.print_summary()
        assert "Skipped" in capsys.readouterr().out

    def test_skipped_line_absent_when_zero(self, capsys):
        r = _make_report()
        r.files = [FileResult("a.ts", "ok")]
        r.print_summary()
        assert "Skipped" not in capsys.readouterr().out

    def test_auto_fixed_line_only_when_retried(self, capsys):
        r = _make_report()
        r.files = [FileResult("a.ts", "ok_with_warnings", attempts=2)]
        r.print_summary()
        assert "Auto-fixed" in capsys.readouterr().out

    def test_manifests_line_only_when_present(self, capsys):
        r = _make_report()
        r.manifest_translated = ["/output/requirements.txt"]
        r.print_summary()
        assert "Manifests" in capsys.readouterr().out

    def test_tests_passed_true_shows_passed(self, capsys):
        r = _make_report()
        r.tests_passed = True
        r.print_summary()
        out = capsys.readouterr().out
        assert "Tests" in out
        assert "passed" in out

    def test_tests_passed_false_shows_failed(self, capsys):
        r = _make_report()
        r.tests_passed = False
        r.print_summary()
        out = capsys.readouterr().out
        assert "Tests" in out
        assert "failed" in out

    def test_tests_passed_none_omits_tests_line(self, capsys):
        r = _make_report()
        r.tests_passed = None
        r.print_summary()
        assert "🧪" not in capsys.readouterr().out

    def test_confidence_line_only_when_scored(self, capsys):
        r = _make_report()
        r.files = [FileResult("a.ts", "ok", confidence=90)]
        r.print_summary()
        assert "Confidence" in capsys.readouterr().out

    def test_confidence_line_absent_when_unscored(self, capsys):
        r = _make_report()
        r.files = [FileResult("a.ts", "ok")]
        r.print_summary()
        assert "Confidence" not in capsys.readouterr().out

    def test_failed_files_are_listed_with_error(self, capsys):
        r = _make_report()
        r.files = [FileResult("src/broken.ts", "failed", error="IndentationError: bad indent")]
        r.print_summary()
        out = capsys.readouterr().out
        assert "src/broken.ts" in out
        assert "IndentationError" in out

    def test_failed_file_without_error_shows_placeholder(self, capsys):
        r = _make_report()
        r.files = [FileResult("src/broken.ts", "failed", error=None)]
        r.print_summary()
        assert "unknown error" in capsys.readouterr().out

    def test_failed_error_truncated_to_first_line_and_100_chars(self, capsys):
        r = _make_report()
        long_error = ("x" * 150) + "\nsecond line should not appear"
        r.files = [FileResult("a.ts", "failed", error=long_error)]
        r.print_summary()
        out = capsys.readouterr().out
        assert "second line should not appear" not in out
        assert ("x" * 100) in out
        assert ("x" * 101) not in out

    def test_failed_file_with_external_chat_urls_prints_them(self, capsys):
        r = _make_report()
        r.files = [FileResult(
            "a.ts", "failed", error="boom",
            external_chat_urls={"Claude": "https://claude.ai/new?q=x", "Groq": "https://groq.com/"},
        )]
        r.print_summary()
        out = capsys.readouterr().out
        assert "Or ask directly:" in out
        assert "Claude: https://claude.ai/new?q=x" in out
        assert "Groq: https://groq.com/" in out

    def test_failed_file_without_external_chat_urls_has_no_offer(self, capsys):
        r = _make_report()
        r.files = [FileResult("a.ts", "failed", error="boom")]
        r.print_summary()
        assert "Or ask directly:" not in capsys.readouterr().out
