"""Adapter that backs an :class:`LLMProvider` with ModelDispatcher's ``ModelGateway``.

Opt-in alternative to this repo's own single-vendor providers (``claude.py``,
``openai.py``, ``gemini.py``, ``groq.py``) plus ``retry.py``'s hand-rolled
backoff loop. ``model-dispatcher`` (https://github.com/joka-7/ModelDispatcher)
is a separate, shared library this project's other apps (AppMyTrip, HighFive,
JobFlowTracker, KanDOne, StepByLearn) are also standardising on, so routing
LangShift's completions through it too means one retry/backoff implementation
instead of two drifting in parallel.

``ModelGateway.dispatch()`` already retries a rate limit internally — waiting
on the provider's own "retry after" hint when it has one, capped the same way
``repo_translator.providers.retry`` caps its own wait — so this provider owns
its resilience end-to-end. ``complete_with_backoff()`` still wraps every call
made through this provider (call sites don't need to know which backend is in
use), but it never actually does anything extra here: a
:class:`~model_dispatcher.exceptions.ModelDispatcherError` message never
contains the "429" / "rate limit" / etc. text ``is_rate_limit_error()`` scans
for, so the wrapper's ``except`` branch never matches and the error just
propagates on the first pass — no double wait, no behaviour change from
``retry.py``'s point of view.

Only wraps the providers ModelDispatcher and LangShift both support today
(``claude``/``openai``/``gemini``/``groq`` — see
:data:`MODEL_DISPATCHER_SUPPORTED_PROVIDERS`). ``ollama``, ``openai-compat``,
and ``offline`` have no ModelDispatcher equivalent (no Ollama adapter exists
there, and arbitrary-base-URL/rule-based translation aren't in its scope), so
they always run on this repo's own native providers regardless of
``--backend`` — see :func:`repo_translator.providers.make_provider`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repo_translator.providers.base import LLMProvider

if TYPE_CHECKING:
    from model_dispatcher.providers import ModelProvider

__all__ = ["ModelDispatcherProvider", "MODEL_DISPATCHER_SUPPORTED_PROVIDERS"]

# A tenant/quota is required by ModelGateway's API but is meaningless for a
# local single-user CLI process: nothing is shared or persisted across runs
# (a fresh in-memory quota store backs every gateway instance), so the caps
# only need to be generous enough not to trip mid-run on a large repo.
_TENANT_ID = "langshift-cli"
_REQUESTS_PER_MIN = 1_000
_TOKENS_PER_MIN = 2_000_000
_TOKENS_PER_DAY = 100_000_000

MODEL_DISPATCHER_SUPPORTED_PROVIDERS = ("claude", "openai", "gemini", "groq")


def _build_backing_provider(
    provider: str, model_id: str, api_key: str | None
) -> ModelProvider:
    """Construct the single ``model_dispatcher`` provider strategy to route to."""
    if provider == "claude":
        from model_dispatcher.providers import AnthropicProvider

        return AnthropicProvider(model=model_id, api_key=api_key)
    if provider == "openai":
        from model_dispatcher.providers import OpenAIProvider

        return OpenAIProvider(model=model_id, api_key=api_key)
    if provider == "gemini":
        from model_dispatcher.providers import GeminiProvider

        return GeminiProvider(model=model_id, api_key=api_key)
    if provider == "groq":
        from model_dispatcher.providers import GroqProvider

        return GroqProvider(model=model_id, api_key=api_key)
    raise ValueError(
        f"--backend model-dispatcher doesn't support provider '{provider}' yet "
        f"(supported: {', '.join(MODEL_DISPATCHER_SUPPORTED_PROVIDERS)}). "
        "Use --backend native for this provider."
    )


class ModelDispatcherProvider(LLMProvider):
    """Routes ``complete()`` through a ``model_dispatcher.ModelGateway``.

    One gateway/tenant pair is built per instance (matching how every other
    provider in this package owns one vendor client for its lifetime) and
    reused for every call.
    """

    def __init__(self, provider: str, model_id: str, api_key: str | None = None) -> None:
        try:
            from model_dispatcher import (
                ModelGateway,
                ProviderRegistry,
                TenantContext,
                TenantId,
                TenantQuota,
            )
        except ImportError as e:
            raise ImportError(
                "model-dispatcher package not installed. Run: "
                "pip install 'repo-translator[model-dispatcher]'"
            ) from e

        registry = ProviderRegistry()
        registry.register(_build_backing_provider(provider, model_id, api_key))
        self._gateway = ModelGateway.create(registry)
        self._tenant = TenantContext(
            tenant_id=TenantId(_TENANT_ID),
            quota=TenantQuota(
                requests_per_min=_REQUESTS_PER_MIN,
                tokens_per_min=_TOKENS_PER_MIN,
                tokens_per_day=_TOKENS_PER_DAY,
            ),
        )

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        from model_dispatcher import CompletionRequest, Message, Role

        request = CompletionRequest(
            messages=(Message(role=Role.USER, content=prompt),),
            tenant=self._tenant.tenant_id,
            max_tokens=max_tokens,
        )
        result = self._gateway.dispatch(request, self._tenant)
        content = result.final_message.content
        if content is None:
            raise ValueError("ModelDispatcher response had no text content")
        return content.strip()
