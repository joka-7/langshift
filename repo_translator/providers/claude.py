from __future__ import annotations

import anthropic
from anthropic.types import TextBlock

from repo_translator.providers.base import LLMProvider

# Friendly alias -> exact model ID. IDs are complete as written; they take no
# date suffix. `--model` also accepts a raw ID, so a model released after this
# table was last touched needs no code change.
CLAUDE_MODELS: dict[str, str] = {
    "haiku":  "claude-haiku-4-5",
    "sonnet": "claude-sonnet-5",
    "opus":   "claude-opus-5",
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
        block = message.content[0]
        if not isinstance(block, TextBlock):
            raise ValueError(f"Expected a text response block, got {type(block).__name__}")
        return block.text.strip()
