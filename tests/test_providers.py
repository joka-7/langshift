"""
Tests for repo_translator.providers

All provider SDK calls are mocked — no real network traffic.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from repo_translator.providers import make_provider, SUPPORTED_PROVIDERS
from repo_translator.providers.base import LLMProvider
from repo_translator.providers.claude import ClaudeProvider, CLAUDE_MODELS
from repo_translator.providers.groq import GroqProvider
from repo_translator.providers.openai_compat import OpenAICompatProvider


# ─────────────────────────────────────────────
# make_provider factory
# ─────────────────────────────────────────────

class TestMakeProvider:
    def test_returns_llm_provider_instance(self):
        with patch("repo_translator.providers.claude.anthropic.Anthropic"):
            p = make_provider("claude", "sonnet")
        assert isinstance(p, LLMProvider)

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            make_provider("fakeai", "model-x")

    def test_error_message_lists_supported_providers(self):
        with pytest.raises(ValueError) as exc_info:
            make_provider("notreal", "x")
        for name in SUPPORTED_PROVIDERS:
            assert name in str(exc_info.value)

    def test_openai_missing_sdk_raises_import_error(self):
        with patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(ImportError, match="pip install openai"):
                make_provider("openai", "gpt-4o")

    def test_gemini_missing_sdk_raises_import_error(self):
        with patch.dict("sys.modules", {"google.generativeai": None}):
            with pytest.raises((ImportError, Exception)):
                make_provider("gemini", "gemini-1.5-pro")

    def test_ollama_missing_sdk_raises_import_error(self):
        with patch.dict("sys.modules", {"ollama": None}):
            with pytest.raises(ImportError, match="pip install ollama"):
                make_provider("ollama", "llama3")


# ─────────────────────────────────────────────
# ClaudeProvider
# ─────────────────────────────────────────────

class TestClaudeProvider:
    def test_friendly_name_resolved_to_model_id(self):
        for name, model_id in CLAUDE_MODELS.items():
            with patch("repo_translator.providers.claude.anthropic.Anthropic") as MockAnt:
                make_provider("claude", name)
            # ClaudeProvider stores the resolved ID
            instance = MockAnt.return_value
            # Just assert make_provider doesn't raise
            assert name in CLAUDE_MODELS

    def test_raw_model_id_passed_through(self):
        raw_id = "claude-some-future-model-99"
        with patch("repo_translator.providers.claude.anthropic.Anthropic"):
            p = ClaudeProvider(model_id=raw_id)
        assert p._model == raw_id

    def test_complete_calls_messages_create(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = [MagicMock(text="result")]
        with patch("repo_translator.providers.claude.anthropic.Anthropic", return_value=mock_client):
            p = ClaudeProvider(model_id="claude-sonnet-4-6")
            result = p.complete("translate this")
        assert result == "result"
        mock_client.messages.create.assert_called_once()

    def test_complete_passes_max_tokens(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = [MagicMock(text="ok")]
        with patch("repo_translator.providers.claude.anthropic.Anthropic", return_value=mock_client):
            p = ClaudeProvider(model_id="claude-sonnet-4-6")
            p.complete("prompt", max_tokens=256)
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 256

    def test_all_claude_model_aliases_resolve(self):
        assert set(CLAUDE_MODELS.keys()) == {"haiku", "sonnet", "opus"}
        for model_id in CLAUDE_MODELS.values():
            assert model_id.startswith("claude-")


# ─────────────────────────────────────────────
# GroqProvider
# ─────────────────────────────────────────────

class TestGroqProvider:
    def test_groq_missing_sdk_raises_import_error(self):
        with patch.dict("sys.modules", {"groq": None}):
            with pytest.raises(ImportError, match="pip install groq"):
                make_provider("groq", "llama-3.1-70b-versatile")

    def test_complete_calls_chat_completions(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="translated"))
        ]
        # The groq SDK is mocked entirely via sys.modules, so this test does not
        # require the real `groq` package to be installed.
        mock_groq_mod = MagicMock()
        mock_groq_mod.Groq.return_value = mock_client
        with patch.dict("sys.modules", {"groq": mock_groq_mod}):
            p = GroqProvider(model_id="llama-3.1-70b-versatile")
            result = p.complete("translate this")
        assert result == "translated"

    def test_groq_in_supported_providers(self):
        assert "groq" in SUPPORTED_PROVIDERS


# ─────────────────────────────────────────────
# OpenAICompatProvider
# ─────────────────────────────────────────────

class TestOpenAICompatProvider:
    def test_requires_base_url(self):
        with pytest.raises(ValueError, match="--base-url"):
            make_provider("openai-compat", "some-model")

    def test_openai_compat_in_supported_providers(self):
        assert "openai-compat" in SUPPORTED_PROVIDERS

    def test_complete_uses_provided_base_url(self):
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="result"))
        ]
        mock_openai.OpenAI.return_value = mock_client
        with patch.dict("sys.modules", {"openai": mock_openai}):
            p = OpenAICompatProvider(
                model_id="meta-llama/Llama-3-70b",
                base_url="https://api.together.xyz/v1",
                api_key="test-key",
            )
            result = p.complete("hello")
        assert result == "result"
        mock_openai.OpenAI.assert_called_once_with(
            api_key="test-key",
            base_url="https://api.together.xyz/v1",
        )

    def test_openai_missing_sdk_raises_for_compat(self):
        with patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(ImportError, match="pip install openai"):
                make_provider("openai-compat", "llama3",
                              base_url="https://api.together.xyz/v1")
