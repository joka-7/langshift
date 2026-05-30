"""
Rate-limit-aware wrapper around LLMProvider.complete().

Detects 429 / rate-limit errors, parses "try again in Xh Ym Z.Zs" from the
error message, and waits + retries automatically.  If the required wait exceeds
MAX_WAIT (e.g. a daily-quota exhaustion), it re-raises immediately so the
caller gets a clear failure rather than sleeping for hours.
"""

from __future__ import annotations

import re
import time

from repo_translator.providers.base import LLMProvider

MAX_RETRIES = 6
MAX_WAIT    = 120   # seconds — auto-retry for short limits; fail fast for daily limits
_BASE_WAIT  = 1     # exponential-backoff base (doubles each attempt)


def is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return (
        "429" in msg
        or "rate_limit" in msg
        or "rate limit" in msg
        or "too many requests" in msg
        or "tokens per minute" in msg
        or "requests per minute" in msg
    )


def parse_retry_after(error_msg: str) -> float | None:
    """
    Parse provider retry-after hints like:
      "Please try again in 48m55.872s"
      "Please try again in 1h2m47.904s"
      "retry after 30"
    Returns seconds as float, or None if not found.
    """
    # "Xh Ym Z.Zs" format (Groq style)
    m = re.search(
        r'try again in\s+(?:(\d+)h\s*)?(?:(\d+)m\s*)?([\d.]+)s',
        error_msg, re.IGNORECASE,
    )
    if m:
        total = (
            float(m.group(1) or 0) * 3600
            + float(m.group(2) or 0) * 60
            + float(m.group(3) or 0)
        )
        if total > 0:
            return total

    # "retry after N" (plain seconds)
    m = re.search(r'retry.{0,10}after\s+(\d+)', error_msg, re.IGNORECASE)
    if m:
        return float(m.group(1))

    return None


def complete_with_backoff(
    provider: LLMProvider,
    prompt: str,
    max_tokens: int = 8096,
) -> str:
    """
    Call provider.complete(), retrying on rate-limit errors with backoff.
    Non-rate-limit errors are re-raised immediately.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            return provider.complete(prompt, max_tokens=max_tokens)
        except Exception as e:
            if not is_rate_limit_error(e):
                raise

            if attempt == MAX_RETRIES:
                raise

            required = parse_retry_after(str(e))

            if required is not None and required > MAX_WAIT:
                # Daily / hourly quota — not worth auto-waiting
                raise

            wait = _BASE_WAIT * (2 ** attempt)       # exponential: 1, 2, 4, 8, 16, 32
            if required is not None:
                wait = required + 1                   # honour provider hint + 1s buffer
            wait = min(wait, MAX_WAIT)

            print(
                f"\n    ⏳ rate limit — waiting {wait:.0f}s "
                f"(retry {attempt + 1}/{MAX_RETRIES})...",
                end=" ", flush=True,
            )
            time.sleep(wait)

    raise RuntimeError("unreachable")
