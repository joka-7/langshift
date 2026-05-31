"""
Offline provider — rule-based code translation with no API calls.

Parses the source code out of the LLM prompt that agent.py constructs
and applies the appropriate language-pair transformer.
"""
from __future__ import annotations

import re

from repo_translator.providers.base import LLMProvider
from repo_translator.offline.transformer import OfflineTransformer


class OfflineProvider(LLMProvider):
    """
    Implements LLMProvider without any network calls.

    The prompt passed by agent._translate_once() always contains:
        Source ({from_lang}):
        ```
        {source_code}
        ```
    We extract the code block and apply the rule-based transformer.

    For confidence-scoring prompts we return a fixed low-confidence JSON so the
    caller gets a result rather than crashing.
    """

    def __init__(self, from_lang: str, to_lang: str) -> None:
        self._from_lang  = from_lang
        self._to_lang    = to_lang
        self._transformer = OfflineTransformer()

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        # Confidence scoring prompt — return a fixed marker
        if '"score"' in prompt or 'Respond with JSON' in prompt:
            return (
                '{"score": 55, "reason": '
                '"Offline rule-based translation — manual review recommended."}'
            )

        code = _extract_code(prompt)
        if code is None:
            raise ValueError(
                "OfflineProvider: could not extract source code from prompt.\n"
                "Expected a fenced code block starting with ``` in the prompt."
            )

        return self._transformer.transform(code, self._from_lang, self._to_lang)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_code(prompt: str) -> str | None:
    """Pull the source code out of the prompt's fenced block."""
    # The prompt ends with:
    #   Source ({lang}):
    #   ```
    #   {code}
    #   ```
    m = re.search(r'```\s*\n(.*?)```', prompt, re.DOTALL)
    if m:
        return m.group(1)
    # Fallback: try any ``` block
    m = re.search(r'```\w*\s*\n(.*?)```', prompt, re.DOTALL)
    if m:
        return m.group(1)
    return None
