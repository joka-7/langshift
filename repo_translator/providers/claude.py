from __future__ import annotations
import anthropic
from repo_translator.providers.base import LLMProvider

CLAUDE_MODELS: dict[str, str] = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-8",
}


class ClaudeProvider(LLMProvider):
    def __init__(self, model_id: str, api_key: str | None = None):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model  = model_id

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
