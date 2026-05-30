from __future__ import annotations
import os
from repo_translator.providers.base import LLMProvider


class GeminiProvider(LLMProvider):
    def __init__(self, model_id: str, api_key: str | None = None):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError(
                "google-generativeai package not installed. Run: pip install google-generativeai"
            )
        genai.configure(api_key=api_key or os.environ.get("GEMINI_API_KEY"))
        self._model = genai.GenerativeModel(model_id)

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        response = self._model.generate_content(prompt)
        return response.text.strip()
