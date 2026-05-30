from __future__ import annotations
from repo_translator.providers.base import LLMProvider
from repo_translator.providers.claude import ClaudeProvider, CLAUDE_MODELS
from repo_translator.providers.openai import OpenAIProvider
from repo_translator.providers.gemini import GeminiProvider
from repo_translator.providers.ollama import OllamaProvider

SUPPORTED_PROVIDERS = ("claude", "openai", "gemini", "ollama")


def make_provider(provider: str, model: str, api_key: str | None = None) -> LLMProvider:
    if provider == "claude":
        model_id = CLAUDE_MODELS.get(model, model)
        return ClaudeProvider(model_id=model_id, api_key=api_key)
    if provider == "openai":
        return OpenAIProvider(model_id=model, api_key=api_key)
    if provider == "gemini":
        return GeminiProvider(model_id=model, api_key=api_key)
    if provider == "ollama":
        return OllamaProvider(model_id=model, api_key=api_key)
    raise ValueError(
        f"Unknown provider '{provider}'. Supported: {', '.join(SUPPORTED_PROVIDERS)}"
    )
