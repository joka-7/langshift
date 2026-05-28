"""
Tests for repo_translator.report
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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

    def test_stores_error(self):
        f = FileResult(path="x.ts", status="failed", error="SyntaxError")
        assert f.error == "SyntaxError"


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

    def test_markdown_mentions_manifest(self, tmp_path):
        r = self._full_report()
        r.save(tmp_path)
        md = (tmp_path / "translation_report.md").read_text()
        assert "requirements.txt" in md

    def test_creates_output_dir_if_missing(self, tmp_path):
        r = self._full_report()
        new_dir = tmp_path / "deep" / "nested" / "dir"
        r.save(new_dir)
        assert (new_dir / "translation_report.json").exists()
