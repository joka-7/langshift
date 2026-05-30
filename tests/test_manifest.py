"""
Tests for repo_translator.manifest
"""

from __future__ import annotations

import pytest

from repo_translator.providers.base import LLMProvider
from repo_translator.manifest import (
    TARGET_MANIFEST,
    _find_manifests,
    translate_manifest,
)


# ─────────────────────────────────────────────
# Mock provider
# ─────────────────────────────────────────────

class MockProvider(LLMProvider):
    def __init__(self, response: str = "", raises: Exception | None = None):
        self._response = response
        self._raises   = raises
        self.calls: list[str] = []

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        self.calls.append(prompt)
        if self._raises:
            raise self._raises
        return self._response


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
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out      = tmp_path / "out"
        provider = MockProvider(raises=Exception("rate limit"))

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
