"""
Tests for repo_translator.manifest
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from helpers import MockProvider

from repo_translator.manifest import (
    TARGET_MANIFEST,
    _find_manifests,
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
