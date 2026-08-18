from __future__ import annotations

import os

from repo_translator.providers.base import LLMProvider


class GeminiProvider(LLMProvider):
    def __init__(self, model_id: str, api_key: str | None = None):
        try:
            import google.generativeai as genai
        except ImportError as e:
            raise ImportError(
                "google-generativeai package not installed. Run: pip install google-generativeai"
            ) from e
        # GOOGLE_API_KEY is accepted as a fallback since Google's own docs and
        # some other tools use that name for the same Gemini API key.
        resolved_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        genai.configure(api_key=resolved_key)
        self._model = genai.GenerativeModel(model_id)

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        response = self._model.generate_content(prompt)
        return response.text.strip()
