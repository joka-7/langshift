from __future__ import annotations

import os

from repo_translator.providers.base import LLMProvider

# Common base URLs for reference
KNOWN_BASE_URLS = {
    "together":   "https://api.together.xyz/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "anyscale":   "https://api.endpoints.anyscale.com/v1",
    "lmstudio":   "http://localhost:1234/v1",
}


class OpenAICompatProvider(LLMProvider):
    """
    Works with any OpenAI-compatible API: Together AI, OpenRouter, Anyscale, LM Studio, etc.
    Pass --base-url to point at the right endpoint.
    """
    def __init__(self, model_id: str, base_url: str, api_key: str | None = None):
        try:
            import openai as _openai
        except ImportError as e:
            raise ImportError("openai package not installed. Run: pip install openai") from e
        self._client = _openai.OpenAI(
            api_key=api_key or os.environ.get("OPENAI_COMPAT_API_KEY", "dummy"),
            base_url=base_url,
        )
        self._model = model_id

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("Response had no text content")
        return content.strip()
