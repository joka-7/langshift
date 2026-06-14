from __future__ import annotations
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    # Override to 1 for deterministic providers where retrying won't help.
    max_fix_attempts: int = 3

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        """Send a prompt and return the response text."""
        ...
