from __future__ import annotations

import os

from repo_translator.providers.base import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(self, model_id: str, api_key: str | None = None):
        try:
            import openai as _openai
        except ImportError as e:
            raise ImportError("openai package not installed. Run: pip install openai") from e
        self._client = _openai.OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        self._model  = model_id

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if content is None:
            raise ValueError("OpenAI response had no text content")
        return content.strip()
