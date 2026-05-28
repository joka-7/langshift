"""
Tests for repo_translator.manifest
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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
        # Should deduplicate even if matched by multiple patterns
        paths = [str(f) for f in found]
        assert len(paths) == len(set(paths))


# ─────────────────────────────────────────────
# translate_manifest (mocked Claude)
# ─────────────────────────────────────────────

def _make_mock_client(response_text: str) -> MagicMock:
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=response_text)]
    mock = MagicMock()
    mock.messages.create.return_value = mock_msg
    return mock


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
        out = tmp_path / "out"
        client = _make_mock_client(SAMPLE_REQUIREMENTS)

        result = translate_manifest(client, tmp_path, out, "typescript", "python", verbose=False)

        assert result["found"] == 1
        assert len(result["translated"]) == 1
        req_file = out / "requirements.txt"
        assert req_file.exists()
        assert req_file.read_text().strip() == SAMPLE_REQUIREMENTS.strip()

    def test_returns_zero_when_no_manifest(self, tmp_path):
        out = tmp_path / "out"
        client = MagicMock()

        result = translate_manifest(client, tmp_path, out, "typescript", "python", verbose=False)

        assert result["found"] == 0
        assert result["translated"] == []
        client.messages.create.assert_not_called()

    def test_target_manifest_name_is_correct(self, tmp_path):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out = tmp_path / "out"
        client = _make_mock_client("flask>=2.0")

        translate_manifest(client, tmp_path, out, "typescript", "python", verbose=False)

        assert (out / TARGET_MANIFEST["python"]).exists()

    def test_api_error_is_handled_gracefully(self, tmp_path):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out = tmp_path / "out"
        client = MagicMock()
        client.messages.create.side_effect = Exception("rate limit")

        # Should not raise — just skip that manifest
        result = translate_manifest(client, tmp_path, out, "typescript", "python", verbose=False)
        assert result["found"] == 1
        assert result["translated"] == []

    def test_creates_output_directory(self, tmp_path):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out = tmp_path / "deep" / "nested" / "out"
        client = _make_mock_client("requests>=2.28")

        translate_manifest(client, tmp_path, out, "typescript", "python", verbose=False)

        assert out.exists()

    def test_prompt_includes_source_language(self, tmp_path):
        (tmp_path / "package.json").write_text(SAMPLE_PACKAGE_JSON)
        out = tmp_path / "out"
        client = _make_mock_client("flask>=2.0")

        translate_manifest(client, tmp_path, out, "typescript", "python", verbose=False)

        call_args = client.messages.create.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "typescript" in prompt.lower()
        assert "python" in prompt.lower()
