"""
Tests for repo_translator.providers.model_dispatcher_provider.

`model-dispatcher` is an optional extra (not part of `dev`/`all-providers` —
see pyproject.toml) so these tests are skipped entirely when it isn't
installed, same as CI's default `pip install -e ".[dev,webui]"`. Unlike this
repo's other provider tests, the wiring is exercised against ModelDispatcher's
own real `MockProvider` rather than a mocked-out SDK: the whole point of this
provider is that `ModelGateway.dispatch()`'s retry/backoff logic runs for
real, so a scripted double at the vendor-SDK layer wouldn't actually prove
anything about that.
"""

from __future__ import annotations

import pytest

model_dispatcher = pytest.importorskip("model_dispatcher")

from repo_translator.providers import (  # noqa: E402
    MODEL_DISPATCHER_SUPPORTED_PROVIDERS,
    SUPPORTED_BACKENDS,
    make_provider,
)
from repo_translator.providers.model_dispatcher_provider import (  # noqa: E402
    ModelDispatcherProvider,
    _build_backing_provider,
)


class TestBackendSelection:
    def test_model_dispatcher_is_a_supported_backend(self):
        assert "model-dispatcher" in SUPPORTED_BACKENDS

    def test_unknown_backend_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            make_provider("claude", "sonnet", backend="not-a-backend")

    def test_make_provider_with_model_dispatcher_backend_returns_it(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        p = make_provider("claude", "sonnet", backend="model-dispatcher")
        assert isinstance(p, ModelDispatcherProvider)

    def test_ollama_is_not_model_dispatcher_backed(self):
        # No ModelDispatcher equivalent exists — always native regardless of
        # --backend, exactly like `offline`/`openai-compat`.
        assert "ollama" not in MODEL_DISPATCHER_SUPPORTED_PROVIDERS

    def test_a_provider_with_no_model_dispatcher_equivalent_still_runs_natively(self):
        # ollama has no ModelDispatcher adapter — --backend model-dispatcher
        # silently falls through to the native OllamaProvider rather than
        # erroring, exactly as documented.
        from unittest.mock import MagicMock, patch

        from repo_translator.providers.ollama import OllamaProvider

        with patch.dict("sys.modules", {"ollama": MagicMock()}):
            p = make_provider("ollama", "llama3", backend="model-dispatcher")
        assert isinstance(p, OllamaProvider)


class TestBuildBackingProvider:
    def test_claude_maps_to_anthropic_provider(self):
        from model_dispatcher.providers import AnthropicProvider

        p = _build_backing_provider("claude", "claude-sonnet-4-6", "k")
        assert isinstance(p, AnthropicProvider)
        assert p.name == "anthropic:claude-sonnet-4-6"

    def test_openai_maps_to_openai_provider(self):
        from model_dispatcher.providers import OpenAIProvider

        p = _build_backing_provider("openai", "gpt-4o", "k")
        assert isinstance(p, OpenAIProvider)

    def test_gemini_maps_to_gemini_provider(self):
        from model_dispatcher.providers import GeminiProvider

        p = _build_backing_provider("gemini", "gemini-1.5-pro", "k")
        assert isinstance(p, GeminiProvider)

    def test_groq_maps_to_groq_provider(self):
        from model_dispatcher.providers import GroqProvider

        p = _build_backing_provider("groq", "llama-3.1-70b-versatile", "k")
        assert isinstance(p, GroqProvider)

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="doesn't support provider"):
            _build_backing_provider("fakeai", "model-x", None)


class TestModelDispatcherProviderComplete:
    """Exercises the real gateway/dispatch path via ModelDispatcher's MockProvider."""

    def _provider_backed_by_mock(self, mock_provider) -> ModelDispatcherProvider:
        from model_dispatcher import (
            ModelGateway,
            ProviderRegistry,
            TenantContext,
            TenantId,
            TenantQuota,
        )

        instance = ModelDispatcherProvider.__new__(ModelDispatcherProvider)
        registry = ProviderRegistry()
        registry.register(mock_provider)
        instance._gateway = ModelGateway.create(registry)
        instance._tenant = TenantContext(
            tenant_id=TenantId("test-tenant"),
            quota=TenantQuota(
                requests_per_min=1_000, tokens_per_min=1_000_000, tokens_per_day=1_000_000
            ),
        )
        return instance

    def test_complete_returns_the_final_message_text(self):
        from model_dispatcher.providers import MockProvider
        from model_dispatcher.types import ModelTier

        mock = MockProvider("mock:free", tier=ModelTier.FREE, reply="translated code")
        provider = self._provider_backed_by_mock(mock)

        result = provider.complete("translate this")

        assert result == "translated code"

    def test_complete_strips_the_response(self):
        from model_dispatcher.providers import MockProvider
        from model_dispatcher.types import ModelTier

        mock = MockProvider("mock:free", tier=ModelTier.FREE, reply="  padded  ")
        provider = self._provider_backed_by_mock(mock)

        assert provider.complete("hi") == "padded"

    def test_a_transient_failure_is_retried_and_still_succeeds(self):
        # Proves the gateway's own retry logic is actually in the loop, not
        # bypassed — this is the behaviour repo_translator.providers.retry's
        # complete_with_backoff previously had to supply itself.
        from model_dispatcher.providers import MockProvider
        from model_dispatcher.types import ErrorClass, ModelTier

        mock = MockProvider(
            "mock:free",
            tier=ModelTier.FREE,
            fail_times=2,
            fail_with=ErrorClass.TRANSIENT,
            reply="recovered",
        )
        provider = self._provider_backed_by_mock(mock)

        assert provider.complete("hi") == "recovered"

    def test_an_exhausted_provider_raises_a_model_dispatcher_error(self):
        from model_dispatcher.exceptions import AllProvidersExhausted
        from model_dispatcher.providers import MockProvider
        from model_dispatcher.types import ModelTier

        mock = MockProvider("mock:free", tier=ModelTier.FREE, fail_times=99)
        provider = self._provider_backed_by_mock(mock)

        with pytest.raises(AllProvidersExhausted):
            provider.complete("hi")

    def test_none_content_raises_a_clear_error(self):
        from model_dispatcher import Message, Role
        from model_dispatcher.providers import MockProvider
        from model_dispatcher.types import ModelTier

        mock = MockProvider("mock:free", tier=ModelTier.FREE, scripted=[Message(role=Role.ASSISTANT)])
        provider = self._provider_backed_by_mock(mock)

        with pytest.raises(ValueError, match="no text content"):
            provider.complete("hi")


class TestModelDispatcherProviderInit:
    def test_missing_package_raises_a_clear_import_error(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "model_dispatcher" or name.startswith("model_dispatcher."):
                raise ImportError("simulated: not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(ImportError, match="pip install 'repo-translator\\[model-dispatcher\\]'"):
            ModelDispatcherProvider("claude", "claude-sonnet-4-6", api_key="k")
