"""
Tests for repo_translator.providers

All provider SDK calls are mocked — no real network traffic.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from repo_translator.providers import SUPPORTED_PROVIDERS, make_provider
from repo_translator.providers.base import LLMProvider
from repo_translator.providers.claude import CLAUDE_MODELS, ClaudeProvider
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

    def test_offline_via_make_provider_redirects_to_make_offline_provider(self):
        with pytest.raises(ValueError, match="make_offline_provider"):
            make_provider("offline", "n/a")

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
            with patch("repo_translator.providers.claude.anthropic.Anthropic"):
                p = make_provider("claude", name)
            assert p._model == model_id

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
# GeminiProvider
# ─────────────────────────────────────────────

def _mock_genai_modules() -> tuple[MagicMock, MagicMock]:
    """
    `import google.generativeai as genai` needs both sys.modules entries
    patched: the top-level `google` package (nonexistent in this test env,
    since google-generativeai isn't a dev dependency) and the `generativeai`
    submodule, with the latter set as an attribute of the former — Python
    binds the `as genai` name via sys.modules, but resolving the dotted
    import still requires the parent package to exist and expose it.
    """
    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value = MagicMock()
    mock_google = MagicMock()
    mock_google.generativeai = mock_genai
    return mock_google, mock_genai


class TestGeminiProvider:
    def test_uses_gemini_api_key_env_var(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "from-gemini-key")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        mock_google, mock_genai = _mock_genai_modules()
        with patch.dict("sys.modules", {"google": mock_google, "google.generativeai": mock_genai}):
            from repo_translator.providers.gemini import GeminiProvider
            GeminiProvider(model_id="gemini-1.5-pro")
        mock_genai.configure.assert_called_once_with(api_key="from-gemini-key")

    def test_falls_back_to_google_api_key_env_var(self, monkeypatch):
        # Regression: README documented GOOGLE_API_KEY while the code only
        # read GEMINI_API_KEY, so following the README's own instructions
        # produced an unauthenticated client. GOOGLE_API_KEY is now accepted
        # as a fallback so neither doc reader is left wrong.
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "from-google-key")
        mock_google, mock_genai = _mock_genai_modules()
        with patch.dict("sys.modules", {"google": mock_google, "google.generativeai": mock_genai}):
            from repo_translator.providers.gemini import GeminiProvider
            GeminiProvider(model_id="gemini-1.5-pro")
        mock_genai.configure.assert_called_once_with(api_key="from-google-key")

    def test_gemini_api_key_takes_precedence_over_google_api_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "from-gemini-key")
        monkeypatch.setenv("GOOGLE_API_KEY", "from-google-key")
        mock_google, mock_genai = _mock_genai_modules()
        with patch.dict("sys.modules", {"google": mock_google, "google.generativeai": mock_genai}):
            from repo_translator.providers.gemini import GeminiProvider
            GeminiProvider(model_id="gemini-1.5-pro")
        mock_genai.configure.assert_called_once_with(api_key="from-gemini-key")

    def test_explicit_api_key_takes_precedence_over_env_vars(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "from-env")
        mock_google, mock_genai = _mock_genai_modules()
        with patch.dict("sys.modules", {"google": mock_google, "google.generativeai": mock_genai}):
            from repo_translator.providers.gemini import GeminiProvider
            GeminiProvider(model_id="gemini-1.5-pro", api_key="explicit-key")
        mock_genai.configure.assert_called_once_with(api_key="explicit-key")

    def test_complete_calls_generate_content(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        mock_google, mock_genai = _mock_genai_modules()
        mock_model = MagicMock()
        mock_model.generate_content.return_value = MagicMock(text="translated  ")
        mock_genai.GenerativeModel.return_value = mock_model
        with patch.dict("sys.modules", {"google": mock_google, "google.generativeai": mock_genai}):
            from repo_translator.providers.gemini import GeminiProvider
            p = GeminiProvider(model_id="gemini-1.5-pro")
            result = p.complete("translate this")
        assert result == "translated"
        mock_model.generate_content.assert_called_once_with("translate this")


# ─────────────────────────────────────────────
# OllamaProvider
# ─────────────────────────────────────────────

class TestOllamaProvider:
    def test_complete_calls_generate_and_strips_response(self):
        mock_ollama_mod = MagicMock()
        mock_ollama_mod.generate.return_value = {"response": "  translated code  "}
        with patch.dict("sys.modules", {"ollama": mock_ollama_mod}):
            p = make_provider("ollama", "llama3")
            result = p.complete("translate this")
        assert result == "translated code"
        mock_ollama_mod.generate.assert_called_once_with(model="llama3", prompt="translate this")

    def test_complete_handles_object_style_response(self):
        # Some ollama client versions return an object with a .response
        # attribute instead of a dict.
        mock_ollama_mod = MagicMock()
        mock_ollama_mod.generate.return_value = MagicMock(response="object-style result")
        with patch.dict("sys.modules", {"ollama": mock_ollama_mod}):
            p = make_provider("ollama", "llama3")
            result = p.complete("translate this")
        assert result == "object-style result"


# ─────────────────────────────────────────────
# OpenAIProvider
# ─────────────────────────────────────────────

class TestOpenAIProvider:
    def test_complete_calls_chat_completions(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="  translated  "))
        ]
        mock_openai_mod = MagicMock()
        mock_openai_mod.OpenAI.return_value = mock_client
        with patch.dict("sys.modules", {"openai": mock_openai_mod}):
            p = make_provider("openai", "gpt-4o")
            result = p.complete("translate this", max_tokens=512)
        assert result == "translated"
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 512
        assert call_kwargs["model"] == "gpt-4o"


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
