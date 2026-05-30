from __future__ import annotations
import os
from repo_translator.providers.base import LLMProvider


class GroqProvider(LLMProvider):
    def __init__(self, model_id: str, api_key: str | None = None):
        try:
            import groq as _groq
        except ImportError:
            raise ImportError("groq package not installed. Run: pip install groq")
        self._client = _groq.Groq(api_key=api_key or os.environ.get("GROQ_API_KEY"))
        self._model  = model_id

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
