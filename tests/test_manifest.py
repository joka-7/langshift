"""
Tests for repo_translator.manifest
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from helpers import MockProvider

from repo_translator.manifest import (
    TARGET_MANIFEST,
    _find_manifests,
    _validate_manifest,
    translate_manifest,
)

# ─────────────────────────────────────────────
# _find_manifests
# ─────────────────────────────────────────────

class TestFindManifests:
    def test_finds_package_json_for_typescript(self, tmp_path):
        (tmp_path / "package.json").write_text('{"dependencies": {}}')
        found = _find_manifests(tmp_path, "typescript")
        assert len(found) == 1
        assert found[0].name == "package.json"

    def test_finds_package_json_for_javascript(self, tmp_path):
        (tmp_path / "package.json").write_text('{"dependencies": {}}')
        found = _find_manifests(tmp_path, "javascript")
        assert len(found) == 1

    def test_finds_go_mod(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/app\ngo 1.21")
        found = _find_manifests(tmp_path, "go")
        assert any(f.name == "go.mod" for f in found)

    def test_finds_cargo_toml(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"')
        found = _find_manifests(tmp_path, "rust")
        assert len(found) == 1

    def test_returns_empty_when_no_manifest(self, tmp_path):
        found = _find_manifests(tmp_path, "typescript")
        assert found == []

    def test_finds_nested_package_json(self, tmp_path):
        sub = tmp_path / "frontend"
        sub.mkdir()
        (sub / "package.json").write_text('{"dependencies": {}}')
        found = _find_manifests(tmp_path, "typescript")
        assert len(found) == 1

    def test_no_duplicates_returned(self, tmp_path):
        (tmp_path / "package.json").write_text('{}')
        found = _find_manifests(tmp_path, "typescript")
        paths = [str(f) for f in found]
        assert len(paths) == len(set(paths))

    def test_order_is_deterministic_across_repeated_calls(self, tmp_path):
        # Regression: _find_manifests used to return list(set(found)), whose
        # iteration order depends on Path hash values and is not guaranteed
        # stable even within a single process run.
        (tmp_path / "requirements.txt").write_text("")
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "Pipfile").write_text("")
        first = [str(f) for f in _find_manifests(tmp_path, "python")]
        for _ in range(20):
            assert [str(f) for f in _find_manifests(tmp_path, "python")] == first

    def test_order_follows_pattern_priority(self, tmp_path):
        # MANIFEST_FILES["python"] lists requirements.txt before pyproject.toml
        # before Pipfile — that priority should be reflected in the result.
        (tmp_path / "Pipfile").write_text("")
        (tmp_path / "pyproject.toml").write_text("")
        (tmp_path / "requirements.txt").write_text("")
        found = _find_manifests(tmp_path, "python")
        assert [f.name for f in found] == ["requirements.txt", "pyproject.toml", "Pipfile"]


# ─────────────────────────────────────────────
# translate_manifest
# ─────────────────────────────────────────────

SAMPLE_PACKAGE_JSON = """{
  "dependencies": {
    "express": "^4.18.0",
    "lodash": "^4.17.21"
  },
  "devDependencies": {
    "typescript": "^5.0.0"
  }
}"""

SAMPLE_REQUIREMENTS = "flask>=2.3.0\nnumpy>=1.24.0\n"


class TestTranslateManifest:
    def test_translates_package_json_to_requirements(self, tmp_path):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out      = tmp_path / "out"
        provider = MockProvider(SAMPLE_REQUIREMENTS)

        result = translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=False)

        assert result["found"] == 1
        assert len(result["translated"]) == 1
        req_file = out / "requirements.txt"
        assert req_file.exists()
        assert req_file.read_text().strip() == SAMPLE_REQUIREMENTS.strip()

    def test_returns_zero_when_no_manifest(self, tmp_path):
        out      = tmp_path / "out"
        provider = MockProvider()

        result = translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=False)

        assert result["found"] == 0
        assert result["translated"] == []
        assert provider.calls == []

    def test_target_manifest_name_is_correct(self, tmp_path):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out      = tmp_path / "out"
        provider = MockProvider("flask>=2.0")

        translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=False)

        assert (out / TARGET_MANIFEST["python"]).exists()

    def test_api_error_is_handled_gracefully(self, tmp_path):
        # "rate limit" makes complete_with_backoff retry for real before giving
        # up (MAX_RETRIES attempts of real exponential backoff); mock time.sleep
        # so this test doesn't take ~60s. See test_retry.py for backoff coverage.
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out      = tmp_path / "out"
        provider = MockProvider(raises=Exception("rate limit"))

        with patch("repo_translator.providers.retry.time.sleep"):
            result = translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=False)
        assert result["found"] == 1
        assert result["translated"] == []

    def test_creates_output_directory(self, tmp_path):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out      = tmp_path / "deep" / "nested" / "out"
        provider = MockProvider("requests>=2.28")

        translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=False)

        assert out.exists()

    def test_prompt_includes_source_language(self, tmp_path):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out      = tmp_path / "out"
        provider = MockProvider("flask>=2.0")

        translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=False)

        assert provider.calls, "provider was never called"
        prompt = provider.calls[0]
        assert "typescript" in prompt.lower()
        assert "python" in prompt.lower()

    def test_multiple_manifests_mapping_to_same_target_do_not_clobber(self, tmp_path):
        # Regression: requirements.txt and pyproject.toml both target
        # requirements.txt when translating python -> javascript's inverse
        # (here: two python manifests both -> package.json going to js).
        # Both used to write to the identical dest_file, so the second
        # write silently discarded the first manifest's translation.
        (tmp_path / "requirements.txt").write_text(SAMPLE_REQUIREMENTS)
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        out = tmp_path / "out"
        provider = MockProvider('{"dependencies": {"flask": "*"}}',
                                 '{"dependencies": {"other": "*"}}')

        result = translate_manifest(provider, tmp_path, out, "python", "javascript", verbose=False)

        assert result["found"] == 2
        assert len(result["translated"]) == 2
        # Both outputs must exist — neither translation was lost.
        written = {Path(p).name for p in result["translated"]}
        assert len(written) == 2
        assert (out / "package.json").exists()
        assert any(name != "package.json" for name in written)

    def test_repeated_runs_produce_identical_manifest_selection(self, tmp_path):
        (tmp_path / "requirements.txt").write_text(SAMPLE_REQUIREMENTS)
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")

        def _run():
            out = tmp_path / "out"
            provider = MockProvider("flask>=2.0", "numpy>=1.0")
            result = translate_manifest(provider, tmp_path, out, "python", "python", verbose=False)
            names = sorted(Path(p).name for p in result["translated"])
            for p in result["translated"]:
                Path(p).unlink()
            return names

        first = _run()
        for _ in range(5):
            assert _run() == first


class TestTranslateManifestVerboseOutput:
    """verbose=True print branches — every other test in this file passes verbose=False."""

    def test_prints_translating_and_written_lines(self, tmp_path, capsys):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out = tmp_path / "out"
        provider = MockProvider(SAMPLE_REQUIREMENTS)

        translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=True)

        out_text = capsys.readouterr().out
        assert "Translating manifest" in out_text
        assert "package.json" in out_text
        assert "Written to" in out_text

    def test_prints_failure_message_on_error(self, tmp_path, capsys):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out = tmp_path / "out"
        provider = MockProvider(raises=Exception("network unreachable"))

        with patch("repo_translator.providers.retry.time.sleep"):
            translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=True)

        out_text = capsys.readouterr().out
        assert "Failed" in out_text
        assert "network unreachable" in out_text

    def test_prints_collision_warning_when_disambiguating(self, tmp_path, capsys):
        (tmp_path / "requirements.txt").write_text(SAMPLE_REQUIREMENTS)
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        out = tmp_path / "out"
        provider = MockProvider("flask>=2.0", "numpy>=1.0")

        translate_manifest(provider, tmp_path, out, "python", "python", verbose=True)

        out_text = capsys.readouterr().out
        assert "already written from another manifest" in out_text


# ─────────────────────────────────────────────
# _validate_manifest
# ─────────────────────────────────────────────

class TestValidateManifest:
    def test_valid_package_json_passes(self):
        assert _validate_manifest("package.json", '{"dependencies": {"flask": "*"}}') is None

    def test_invalid_package_json_fails(self):
        error = _validate_manifest("package.json", "{not valid json")
        assert error is not None
        assert "JSON" in error

    def test_valid_composer_json_passes(self):
        assert _validate_manifest("composer.json", '{"require": {}}') is None

    def test_valid_cargo_toml_passes(self):
        assert _validate_manifest("Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n') is None

    def test_invalid_cargo_toml_fails(self):
        error = _validate_manifest("Cargo.toml", "[package\nname = x")
        if sys.version_info < (3, 11):
            # tomllib is 3.11+. manifest.py deliberately degrades to "no TOML
            # validation" rather than taking on a `tomli` backport dependency
            # (see its import guard), and pyproject still declares >=3.10 — so
            # on 3.10 the contract is that malformed TOML passes through
            # unvalidated, not that it's caught.
            assert error is None
            return
        assert error is not None
        assert "TOML" in error

    def test_valid_requirements_txt_passes(self):
        assert _validate_manifest("requirements.txt", "flask>=2.0\nnumpy==1.24.0\n") is None

    def test_requirements_txt_with_comments_and_blanks_passes(self):
        content = "# a comment\nflask>=2.0\n\nnumpy==1.24.0  # pinned\n"
        assert _validate_manifest("requirements.txt", content) is None

    def test_requirements_txt_with_pip_flags_passes(self):
        assert _validate_manifest("requirements.txt", "-e ./local-pkg\nflask>=2.0\n") is None

    def test_invalid_requirements_txt_fails(self):
        # A leaked markdown fence or prose line, not a requirement.
        error = _validate_manifest("requirements.txt", "Here are the translated dependencies:\nflask>=2.0\n")
        assert error is not None
        assert "flask>=2.0" not in error  # the bad line is the one flagged, not the good one

    def test_valid_go_mod_passes(self):
        assert _validate_manifest("go.mod", "module example.com/foo\n\ngo 1.21\n") is None

    def test_go_mod_missing_module_directive_fails(self):
        error = _validate_manifest("go.mod", "go 1.21\nrequire foo v1.0.0\n")
        assert error is not None
        assert "module" in error

    def test_empty_content_fails_regardless_of_format(self):
        assert _validate_manifest("package.json", "") is not None
        assert _validate_manifest("requirements.txt", "   \n  ") is not None

    def test_unvalidated_format_always_passes(self):
        # pom.xml, Gemfile, etc. have no validator — anything goes.
        assert _validate_manifest("pom.xml", "this is not even close to XML") is None
        assert _validate_manifest("Gemfile", "whatever") is None

    def test_cargo_toml_validation_skipped_without_tomllib(self):
        # Simulates Python 3.10, where tomllib doesn't exist yet.
        with patch("repo_translator.manifest.tomllib", None):
            assert _validate_manifest("Cargo.toml", "not even close to toml {{{") is None


# ─────────────────────────────────────────────
# translate_manifest — validation retry loop
# ─────────────────────────────────────────────

class TestTranslateManifestValidation:
    def test_invalid_then_valid_response_is_written_after_retry(self, tmp_path, capsys):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out = tmp_path / "out"
        provider = MockProvider("not valid json at all", "flask>=2.0")

        result = translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=True)

        assert len(result["translated"]) == 1
        assert result["validation_failed"] == []
        written = Path(result["translated"][0])
        assert written.read_text() == "flask>=2.0"
        assert "retrying" in capsys.readouterr().out

    def test_invalid_after_all_attempts_is_skipped_not_written(self, tmp_path, capsys):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out = tmp_path / "out"
        provider = MockProvider("still not valid json", "also not valid json")

        result = translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=True)

        assert result["translated"] == []
        assert len(result["validation_failed"]) == 1
        assert result["validation_failed"][0]["manifest"] == "package.json"
        assert not out.exists() or list(out.iterdir()) == []
        assert "Skipped" in capsys.readouterr().out

    def test_error_context_is_included_in_retry_prompt(self, tmp_path):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out = tmp_path / "out"
        provider = MockProvider("Here is the translated file:", "flask>=2.0")

        translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=False)

        assert len(provider.calls) == 2
        assert "previous attempt produced invalid output" in provider.calls[1]
        assert "does not look like a pip requirement" in provider.calls[1]

    def test_valid_first_try_only_calls_provider_once(self, tmp_path):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out = tmp_path / "out"
        provider = MockProvider("flask>=2.0")

        translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=False)

        assert len(provider.calls) == 1

    def test_no_validator_for_format_never_retries(self, tmp_path):
        # pom.xml has no validator, so any response is accepted immediately.
        (tmp_path / "requirements.txt").write_text(SAMPLE_REQUIREMENTS)
        out = tmp_path / "out"
        provider = MockProvider("<project>anything goes</project>")

        result = translate_manifest(provider, tmp_path, out, "python", "java", verbose=False)

        assert len(result["translated"]) == 1
        assert len(provider.calls) == 1

    def test_write_failure_is_logged_not_raised(self, tmp_path, capsys):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out = tmp_path / "out"
        provider = MockProvider("flask>=2.0")

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            result = translate_manifest(provider, tmp_path, out, "typescript", "python", verbose=True)

        assert result["translated"] == []
        assert "Failed to write output" in capsys.readouterr().out
