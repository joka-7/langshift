from __future__ import annotations
from repo_translator.providers.base import LLMProvider
from repo_translator.providers.claude import ClaudeProvider, CLAUDE_MODELS
from repo_translator.providers.openai import OpenAIProvider
from repo_translator.providers.gemini import GeminiProvider
from repo_translator.providers.ollama import OllamaProvider
from repo_translator.providers.groq import GroqProvider
from repo_translator.providers.openai_compat import OpenAICompatProvider

SUPPORTED_PROVIDERS = ("claude", "openai", "gemini", "ollama", "groq", "openai-compat")


def make_provider(
    provider: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMProvider:
    if provider == "claude":
        model_id = CLAUDE_MODELS.get(model, model)
        return ClaudeProvider(model_id=model_id, api_key=api_key)
    if provider == "openai":
        return OpenAIProvider(model_id=model, api_key=api_key)
    if provider == "gemini":
        return GeminiProvider(model_id=model, api_key=api_key)
    if provider == "ollama":
        return OllamaProvider(model_id=model, api_key=api_key)
    if provider == "groq":
        return GroqProvider(model_id=model, api_key=api_key)
    if provider == "openai-compat":
        if not base_url:
            raise ValueError(
                "--base-url is required for openai-compat provider.\n"
                "  Examples: --base-url https://api.together.xyz/v1\n"
                "            --base-url https://openrouter.ai/api/v1"
            )
        return OpenAICompatProvider(model_id=model, base_url=base_url, api_key=api_key)
    raise ValueError(
        f"Unknown provider '{provider}'. Supported: {', '.join(SUPPORTED_PROVIDERS)}"
    )
