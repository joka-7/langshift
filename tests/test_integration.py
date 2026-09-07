"""
Integration tests for the CLI entry point (repo_translator.cli).

Unlike tests/test_agent.py (which mocks the LLM client to test agent.py's
internal logic in isolation), these tests invoke the actual `repo-translate`
CLI as a subprocess against real files on disk, using the offline provider
so no network calls or API keys are needed. They exercise argument parsing,
path resolution, the real auto-run step (writing/running an actual .py file),
and report persistence end-to-end — the things unit tests mock away.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "repo_translator.cli", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )


class TestOfflineTranslationEndToEnd:
    def test_translates_and_runs_output(self, ts_repo, tmp_path):
        output = tmp_path / "out"
        result = run_cli(
            "--input", str(ts_repo),
            "--from", "ts",
            "--to", "python",
            "--output", str(output),
            "--provider", "offline",
            "--no-manifest",
            "--quiet",
        )
        assert result.returncode == 0, result.stdout + result.stderr

        translated = output / "main.py"
        assert translated.exists()
        assert "def add" in translated.read_text()

        report_json = json.loads((output / "translation_report.json").read_text())
        assert report_json["summary"]["translated"] == 1
        assert report_json["summary"]["failed"] == 0
        assert (output / "translation_report.md").exists()

    def test_no_report_flag_skips_report_files(self, ts_repo, tmp_path):
        output = tmp_path / "out"
        result = run_cli(
            "--input", str(ts_repo),
            "--from", "ts",
            "--to", "python",
            "--output", str(output),
            "--provider", "offline",
            "--no-manifest",
            "--no-report",
            "--quiet",
        )
        assert result.returncode == 0
        assert not (output / "translation_report.json").exists()

    def test_estimate_yes_runs_without_prompting(self, ts_repo, tmp_path):
        output = tmp_path / "out"
        result = run_cli(
            "--input", str(ts_repo),
            "--from", "ts",
            "--to", "python",
            "--output", str(output),
            "--provider", "offline",
            "--no-manifest",
            "--estimate", "--yes",
            "--quiet",
        )
        assert result.returncode == 0
        assert "Cost Estimate" in result.stdout
        assert (output / "main.py").exists()


class TestCliErrorHandling:
    def test_unknown_source_language_exits_nonzero(self, ts_repo, tmp_path):
        result = run_cli(
            "--input", str(ts_repo),
            "--from", "not-a-real-language",
            "--to", "python",
            "--output", str(tmp_path / "out"),
            "--provider", "offline",
        )
        assert result.returncode != 0
        assert "unknown language" in result.stdout.lower()

    def test_missing_input_path_exits_nonzero(self, tmp_path):
        result = run_cli(
            "--input", str(tmp_path / "does-not-exist"),
            "--from", "ts",
            "--to", "python",
            "--provider", "offline",
        )
        assert result.returncode != 0
        assert "does not exist" in result.stdout.lower()

    def test_unsupported_offline_pair_exits_nonzero(self, ts_repo, tmp_path):
        result = run_cli(
            "--input", str(ts_repo),
            "--from", "ts",
            "--to", "java",
            "--output", str(tmp_path / "out"),
            "--provider", "offline",
            "--no-manifest",
        )
        assert result.returncode != 0
        assert "no offline transformer" in (result.stdout + result.stderr).lower()

    def test_comment_mode_rejects_offline_provider(self, ts_repo, tmp_path):
        # The offline transformer registry is keyed by translation pairs
        # (typescript:python etc.) — it has nothing to say about annotating
        # a file in its own language, so the CLI refuses the combination
        # up front rather than handing offline a prompt it can't handle.
        result = run_cli(
            "--input", str(ts_repo),
            "--from", "ts",
            "--mode", "comment",
            "--output", str(tmp_path / "out"),
            "--provider", "offline",
        )
        assert result.returncode != 0
        assert "does not support" in result.stdout.lower()

    def test_diagram_mode_rejects_offline_provider(self, ts_repo, tmp_path):
        result = run_cli(
            "--input", str(ts_repo),
            "--from", "ts",
            "--mode", "diagram",
            "--output", str(tmp_path / "out"),
            "--provider", "offline",
        )
        assert result.returncode != 0
        assert "does not support" in result.stdout.lower()
