from __future__ import annotations

from abc import ABC, abstractmethod


class ResponseTruncatedError(ValueError):
    """The model stopped at the max_tokens cap, so its answer is incomplete.

    Raised rather than returned: a translation cut off mid-file is still text,
    and would otherwise be written out as if it were the whole file.
    """


class LLMProvider(ABC):
    # Override to 1 for deterministic providers where retrying won't help.
    max_fix_attempts: int = 3

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        """Send a prompt and return the response text."""
        ...
