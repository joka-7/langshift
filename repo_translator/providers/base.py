from __future__ import annotations
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        """Send a prompt and return the response text."""
        ...
