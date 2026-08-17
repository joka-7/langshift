from __future__ import annotations

from repo_translator.providers.base import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, model_id: str, api_key: str | None = None):
        try:
            import ollama as _ollama
            self._ollama = _ollama
        except ImportError as e:
            raise ImportError("ollama package not installed. Run: pip install ollama") from e
        self._model = model_id

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        raw = self._ollama.generate(model=self._model, prompt=prompt)
        text = raw.response if hasattr(raw, "response") else raw["response"]
        return text.strip()
