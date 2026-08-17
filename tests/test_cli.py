"""
Unit tests for repo_translator.cli.

Unlike test_integration.py (real subprocess CLI runs against the offline
provider), these tests call cli.main() in-process with translate_repo,
estimate_translation, and the provider factories mocked out, so every
argument-parsing and control-flow branch can be exercised directly —
including ones no integration test reaches (e.g. every integration test
passes --output, so the default-output-path derivation was never hit).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from repo_translator import cli
from repo_translator.report import FileResult, TranslationReport


def _report(**overrides) -> TranslationReport:
    r = TranslationReport(
        from_lang="typescript", to_lang="python",
        input_path="/in", output_path="/out",
    )
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


def _estimate(**overrides) -> dict:
    est = {
        "from_lang": "typescript", "to_lang": "python",
        "provider": "offline", "model": "n/a",
        "file_count": 1, "manifest_count": 0,
        "input_tokens": 100, "output_tokens": 100,
        "estimated_cost": 0.01, "price_label": "free (offline)",
    }
    est.update(overrides)
    return est


# ─────────────────────────────────────────────
# build_parser
# ─────────────────────────────────────────────

class TestBuildParser:
    def test_requires_input_from_to(self):
        parser = cli.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_parses_minimal_args_with_defaults(self):
        parser = cli.build_parser()
        args = parser.parse_args(["--input", "x", "--from", "ts", "--to", "python"])
        assert args.input == "x"
        assert args.from_lang == "ts"
        assert args.to_lang == "python"
        assert args.output is None
        assert args.provider == cli.DEFAULT_PROVIDER
        assert args.model == cli.DEFAULT_MODEL
        assert args.base_url is None
        assert args.api_key is None
        assert args.estimate is False
        assert args.yes is False
        assert args.run_tests is False
        assert args.no_manifest is False
        assert args.no_confidence is False
        assert args.no_report is False
        assert args.quiet is False

    def test_rejects_unsupported_provider(self):
        parser = cli.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(
                ["--input", "x", "--from", "ts", "--to", "python", "--provider", "not-a-provider"]
            )

    def test_short_flags_and_boolean_flags(self):
        parser = cli.build_parser()
        args = parser.parse_args([
            "-i", "x", "-f", "ts", "-t", "python", "-o", "out",
            "-p", "offline", "-m", "custom-model", "-e", "-y", "-q",
            "--run-tests", "--no-manifest", "--no-confidence", "--no-report",
        ])
        assert args.output == "out"
        assert args.provider == "offline"
        assert args.model == "custom-model"
        assert args.estimate is True
        assert args.yes is True
        assert args.quiet is True
        assert args.run_tests is True
        assert args.no_manifest is True
        assert args.no_confidence is True
        assert args.no_report is True


# ─────────────────────────────────────────────
# main() — argument / path validation
# ─────────────────────────────────────────────

class TestMainValidation:
    def test_unknown_from_language_exits_1(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", [
            "repo-translate", "--input", str(tmp_path), "--from", "brainfuck", "--to", "python",
        ])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        assert "unknown language" in capsys.readouterr().out.lower()

    def test_unknown_to_language_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", [
            "repo-translate", "--input", str(tmp_path), "--from", "ts", "--to", "brainfuck",
        ])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1

    def test_missing_input_path_exits_1(self, tmp_path, monkeypatch, capsys):
        missing = tmp_path / "does-not-exist"
        monkeypatch.setattr(sys, "argv", [
            "repo-translate", "--input", str(missing), "--from", "ts", "--to", "python",
        ])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        assert "does not exist" in capsys.readouterr().out.lower()


# ─────────────────────────────────────────────
# main() — output path + exit codes
# ─────────────────────────────────────────────

class TestMainOutputPathAndExitCode:
    def test_derives_default_output_path_when_not_given(self, tmp_path, monkeypatch):
        # Regression-guard: every test_integration.py case passes --output,
        # so input.parent / f"{name}_{to_lang}" was never exercised anywhere.
        input_dir = tmp_path / "myrepo"
        input_dir.mkdir()
        monkeypatch.setattr(sys, "argv", [
            "repo-translate", "--input", str(input_dir), "--from", "ts", "--to", "python",
            "--provider", "offline", "--no-report",
        ])
        with patch("repo_translator.cli.translate_repo", return_value=_report()) as mock_translate, \
             patch("repo_translator.cli.make_offline_provider", return_value=MagicMock()):
            with pytest.raises(SystemExit) as exc:
                cli.main()
        assert exc.value.code == 0
        assert mock_translate.call_args.kwargs["output_path"] == input_dir.parent / "myrepo_python"

    def test_explicit_output_path_is_respected(self, tmp_path, monkeypatch):
        input_dir = tmp_path / "repo"
        input_dir.mkdir()
        out_dir = tmp_path / "custom-out"
        monkeypatch.setattr(sys, "argv", [
            "repo-translate", "--input", str(input_dir), "--from", "ts", "--to", "python",
            "--provider", "offline", "--output", str(out_dir), "--no-report",
        ])
        with patch("repo_translator.cli.translate_repo", return_value=_report()) as mock_translate, \
             patch("repo_translator.cli.make_offline_provider", return_value=MagicMock()):
            with pytest.raises(SystemExit):
                cli.main()
        assert mock_translate.call_args.kwargs["output_path"] == out_dir

    def test_exit_code_1_when_files_failed(self, tmp_path, monkeypatch):
        input_dir = tmp_path / "repo"
        input_dir.mkdir()
        report = _report()
        report.files = [FileResult(path="a.ts", status="failed", error="boom")]
        monkeypatch.setattr(sys, "argv", [
            "repo-translate", "--input", str(input_dir), "--from", "ts", "--to", "python",
            "--provider", "offline", "--output", str(tmp_path / "out"), "--no-report",
        ])
        with patch("repo_translator.cli.translate_repo", return_value=report), \
             patch("repo_translator.cli.make_offline_provider", return_value=MagicMock()):
            with pytest.raises(SystemExit) as exc:
                cli.main()
        assert exc.value.code == 1

    def test_exit_code_0_when_nothing_failed(self, tmp_path, monkeypatch):
        input_dir = tmp_path / "repo"
        input_dir.mkdir()
        report = _report()
        report.files = [FileResult(path="a.ts", status="ok")]
        monkeypatch.setattr(sys, "argv", [
            "repo-translate", "--input", str(input_dir), "--from", "ts", "--to", "python",
            "--provider", "offline", "--output", str(tmp_path / "out"), "--no-report",
        ])
        with patch("repo_translator.cli.translate_repo", return_value=report), \
             patch("repo_translator.cli.make_offline_provider", return_value=MagicMock()):
            with pytest.raises(SystemExit) as exc:
                cli.main()
        assert exc.value.code == 0

    def test_saves_report_unless_no_report(self, tmp_path, monkeypatch):
        input_dir = tmp_path / "repo"
        input_dir.mkdir()
        report = MagicMock(wraps=_report())
        report.failed = 0
        monkeypatch.setattr(sys, "argv", [
            "repo-translate", "--input", str(input_dir), "--from", "ts", "--to", "python",
            "--provider", "offline", "--output", str(tmp_path / "out"),
        ])
        with patch("repo_translator.cli.translate_repo", return_value=report), \
             patch("repo_translator.cli.make_offline_provider", return_value=MagicMock()):
            with pytest.raises(SystemExit):
                cli.main()
        report.save.assert_called_once()


# ─────────────────────────────────────────────
# main() — --estimate flow
# ─────────────────────────────────────────────

class TestMainEstimateFlow:
    def _argv(self, input_dir, tmp_path, extra=()):
        return [
            "repo-translate", "--input", str(input_dir), "--from", "ts", "--to", "python",
            "--provider", "offline", "--output", str(tmp_path / "out"),
            "--no-report", "--estimate", *extra,
        ]

    def test_declines_with_yes_not_set_and_answer_no(self, tmp_path, monkeypatch, capsys):
        input_dir = tmp_path / "repo"
        input_dir.mkdir()
        monkeypatch.setattr(sys, "argv", self._argv(input_dir, tmp_path))
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        with patch("repo_translator.cli.estimate_translation", return_value=_estimate()):
            with pytest.raises(SystemExit) as exc:
                cli.main()
        assert exc.value.code == 0
        assert "aborted" in capsys.readouterr().out.lower()

    def test_eof_on_prompt_aborts_cleanly(self, tmp_path, monkeypatch, capsys):
        input_dir = tmp_path / "repo"
        input_dir.mkdir()
        monkeypatch.setattr(sys, "argv", self._argv(input_dir, tmp_path))

        def _raise_eof(prompt=""):
            raise EOFError()
        monkeypatch.setattr("builtins.input", _raise_eof)
        with patch("repo_translator.cli.estimate_translation", return_value=_estimate()):
            with pytest.raises(SystemExit) as exc:
                cli.main()
        assert exc.value.code == 0
        assert "aborted" in capsys.readouterr().out.lower()

    def test_keyboard_interrupt_on_prompt_aborts_cleanly(self, tmp_path, monkeypatch):
        input_dir = tmp_path / "repo"
        input_dir.mkdir()
        monkeypatch.setattr(sys, "argv", self._argv(input_dir, tmp_path))

        def _raise_kbi(prompt=""):
            raise KeyboardInterrupt()
        monkeypatch.setattr("builtins.input", _raise_kbi)
        with patch("repo_translator.cli.estimate_translation", return_value=_estimate()):
            with pytest.raises(SystemExit) as exc:
                cli.main()
        assert exc.value.code == 0

    def test_yes_flag_skips_prompt_and_proceeds(self, tmp_path, monkeypatch):
        input_dir = tmp_path / "repo"
        input_dir.mkdir()
        monkeypatch.setattr(sys, "argv", self._argv(input_dir, tmp_path, extra=["--yes"]))

        def _fail_if_called(prompt=""):
            raise AssertionError("input() should not be called when --yes is set")
        monkeypatch.setattr("builtins.input", _fail_if_called)
        with patch("repo_translator.cli.estimate_translation", return_value=_estimate()), \
             patch("repo_translator.cli.translate_repo", return_value=_report()), \
             patch("repo_translator.cli.make_offline_provider", return_value=MagicMock()):
            with pytest.raises(SystemExit) as exc:
                cli.main()
        assert exc.value.code == 0

    def test_unknown_pricing_shows_placeholder(self, tmp_path, monkeypatch, capsys):
        input_dir = tmp_path / "repo"
        input_dir.mkdir()
        monkeypatch.setattr(sys, "argv", self._argv(input_dir, tmp_path))
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        with patch("repo_translator.cli.estimate_translation",
                    return_value=_estimate(estimated_cost=None)):
            with pytest.raises(SystemExit):
                cli.main()
        assert "unknown (pricing not on record)" in capsys.readouterr().out


# ─────────────────────────────────────────────
# main() — provider construction errors
# ─────────────────────────────────────────────

class TestMainProviderConstructionError:
    def test_import_error_from_provider_prints_and_exits_1(self, tmp_path, monkeypatch, capsys):
        input_dir = tmp_path / "repo"
        input_dir.mkdir()
        monkeypatch.setattr(sys, "argv", [
            "repo-translate", "--input", str(input_dir), "--from", "ts", "--to", "python",
            "--provider", "openai", "--output", str(tmp_path / "out"), "--no-report",
        ])
        with patch("repo_translator.cli.make_provider",
                    side_effect=ImportError("openai package not installed")):
            with pytest.raises(SystemExit) as exc:
                cli.main()
        assert exc.value.code == 1
        assert "Error:" in capsys.readouterr().out

    def test_value_error_from_provider_prints_and_exits_1(self, tmp_path, monkeypatch, capsys):
        input_dir = tmp_path / "repo"
        input_dir.mkdir()
        monkeypatch.setattr(sys, "argv", [
            "repo-translate", "--input", str(input_dir), "--from", "ts", "--to", "python",
            "--provider", "openai-compat", "--output", str(tmp_path / "out"), "--no-report",
        ])
        with patch("repo_translator.cli.make_provider",
                    side_effect=ValueError("--base-url is required")):
            with pytest.raises(SystemExit) as exc:
                cli.main()
        assert exc.value.code == 1
        assert "Error:" in capsys.readouterr().out
