"""
Shared provider doubles.

Before this module existed, MockProvider was copy-pasted into test_agent.py,
test_manifest.py and test_retry.py as three near-identical definitions.

No test in this suite talks to a real LLM: repo_translator abstracts every
backend behind providers.base.LLMProvider, so the doubles below implement that
one interface rather than mocking any vendor SDK. (The vendor SDKs themselves
are mocked in test_providers.py, the only place that cares what they look like.)
"""

from __future__ import annotations

from repo_translator.providers.base import LLMProvider


class MockProvider(LLMProvider):
    """
    Returns queued responses in order, then repeats the last one forever.

    A queued Exception is raised instead of returned, which is how tests drive
    the auto-fix loop and the rate-limit backoff:

        MockProvider("bad code", "good code")      # fails, then succeeds
        MockProvider(rate_limit_error, "ok")       # raises, then succeeds
        MockProvider(raises=Exception("boom"))     # raises on every call

    Every prompt is recorded in .calls, so tests can assert on prompt content
    without needing a separate double.
    """

    def __init__(self, *responses, raises: Exception | None = None):
        self._queue = list(responses) if responses else [""]
        self._idx = 0
        self._raises = raises
        self.calls: list[str] = []

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        self.calls.append(prompt)
        if self._raises:
            raise self._raises
        resp = self._queue[min(self._idx, len(self._queue) - 1)]
        self._idx += 1
        if isinstance(resp, Exception):
            raise resp
        return resp


class CapturingProvider(LLMProvider):
    """Records the most recent prompt and echoes a fixed response."""

    def __init__(self, response: str = "x = 1"):
        self.last_prompt: str | None = None
        self.calls: list[str] = []
        self._response = response

    def complete(self, prompt: str, max_tokens: int = 8096) -> str:
        self.last_prompt = prompt
        self.calls.append(prompt)
        return self._response
