"""
Tests for providers/retry.py — rate-limit detection, retry-after parsing, backoff logic.
No real network calls; time.sleep is always mocked out.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from helpers import MockProvider

from repo_translator.providers.retry import (
    MAX_RETRIES,
    MAX_WAIT,
    complete_with_backoff,
    is_rate_limit_error,
    parse_retry_after,
)


def _rate_limit(msg: str = "Rate limit reached. Please try again in 30s.") -> Exception:
    return Exception(f"Error code: 429 - {msg}")


# ─────────────────────────────────────────────
# is_rate_limit_error
# ─────────────────────────────────────────────

class TestIsRateLimitError:
    def test_detects_429_code(self):
        assert is_rate_limit_error(Exception("Error code: 429 - too fast"))

    def test_detects_rate_limit_text(self):
        assert is_rate_limit_error(Exception("rate limit exceeded"))

    def test_detects_rate_limit_underscore(self):
        assert is_rate_limit_error(Exception("rate_limit_exceeded"))

    def test_detects_too_many_requests(self):
        assert is_rate_limit_error(Exception("Too Many Requests"))

    def test_detects_tokens_per_minute(self):
        assert is_rate_limit_error(Exception("tokens per minute exceeded"))

    def test_does_not_match_auth_error(self):
        assert not is_rate_limit_error(Exception("401 invalid x-api-key"))

    def test_does_not_match_generic_error(self):
        assert not is_rate_limit_error(Exception("internal server error"))

    def test_does_not_match_model_error(self):
        assert not is_rate_limit_error(Exception("model not found"))

    def test_does_not_match_413_too_large(self):
        assert not is_rate_limit_error(Exception(
            "Error code: 413 - Request too large for model on tokens per minute (TPM): Limit 6000, Requested 8488"
        ))

    def test_does_not_retry_413_in_complete_with_backoff(self):
        err = Exception("Error code: 413 - Request too large for model on TPM: Limit 6000, Requested 9000")
        provider = MockProvider(err, "ok")
        with patch("repo_translator.providers.retry.time.sleep") as mock_sleep:
            with pytest.raises(Exception, match="413"):
                complete_with_backoff(provider, "prompt")
        mock_sleep.assert_not_called()


# ─────────────────────────────────────────────
# parse_retry_after
# ─────────────────────────────────────────────

class TestParseRetryAfter:
    def test_parses_seconds_only(self):
        assert parse_retry_after("Please try again in 30.0s") == pytest.approx(30.0)

    def test_parses_minutes_and_seconds(self):
        assert parse_retry_after("Please try again in 2m30.5s") == pytest.approx(150.5)

    def test_parses_hours_minutes_seconds(self):
        assert parse_retry_after("try again in 1h2m47.904s") == pytest.approx(3767.904)

    def test_parses_groq_style_tpm(self):
        msg = "Rate limit reached on tokens per minute (TPM): please try again in 48m55.872s"
        assert parse_retry_after(msg) == pytest.approx(48 * 60 + 55.872)

    def test_parses_plain_retry_after(self):
        assert parse_retry_after("retry after 45") == pytest.approx(45.0)

    def test_returns_none_when_not_present(self):
        assert parse_retry_after("something went wrong") is None

    def test_returns_none_for_empty_string(self):
        assert parse_retry_after("") is None


# ─────────────────────────────────────────────
# complete_with_backoff
# ─────────────────────────────────────────────

class TestCompleteWithBackoff:
    def test_returns_on_first_success(self):
        provider = MockProvider("result")
        with patch("repo_translator.providers.retry.time.sleep") as mock_sleep:
            out = complete_with_backoff(provider, "prompt")
        assert out == "result"
        mock_sleep.assert_not_called()

    def test_retries_on_rate_limit_then_succeeds(self):
        provider = MockProvider(_rate_limit("try again in 1s"), "ok")
        with patch("repo_translator.providers.retry.time.sleep"):
            out = complete_with_backoff(provider, "prompt")
        assert out == "ok"

    def test_does_not_retry_non_rate_limit_errors(self):
        provider = MockProvider(Exception("401 auth error"), "ok")
        with pytest.raises(Exception, match="401 auth error"):
            complete_with_backoff(provider, "prompt")

    def test_raises_after_max_retries(self):
        provider = MockProvider(*[_rate_limit("try again in 1s")] * (MAX_RETRIES + 1))
        with patch("repo_translator.providers.retry.time.sleep"):
            with pytest.raises(Exception, match="429"):
                complete_with_backoff(provider, "prompt")

    def test_sleeps_with_parsed_retry_after(self):
        provider = MockProvider(_rate_limit("try again in 10.0s"), "ok")
        with patch("repo_translator.providers.retry.time.sleep") as mock_sleep:
            complete_with_backoff(provider, "prompt")
        # Should wait retry-after + 1s buffer = 11s
        mock_sleep.assert_called_once_with(11.0)

    def test_uses_exponential_backoff_when_no_retry_after(self):
        provider = MockProvider(
            _rate_limit("rate limit exceeded"),  # no "try again in X" hint
            _rate_limit("rate limit exceeded"),
            "ok",
        )
        with patch("repo_translator.providers.retry.time.sleep") as mock_sleep:
            complete_with_backoff(provider, "prompt")
        waits = [c.args[0] for c in mock_sleep.call_args_list]
        assert waits[1] > waits[0]  # second wait > first wait (exponential)

    def test_fails_fast_when_required_wait_exceeds_max(self):
        # Daily limit: "try again in 12h" — should not auto-wait
        provider = MockProvider(_rate_limit("try again in 12h0m0.0s"), "ok")
        with patch("repo_translator.providers.retry.time.sleep") as mock_sleep:
            with pytest.raises(Exception, match="429"):
                complete_with_backoff(provider, "prompt")
        mock_sleep.assert_not_called()

    def test_caps_wait_at_max_wait(self):
        # Hint just under MAX_WAIT threshold
        provider = MockProvider(_rate_limit(f"try again in {MAX_WAIT - 1}.0s"), "ok")
        with patch("repo_translator.providers.retry.time.sleep") as mock_sleep:
            complete_with_backoff(provider, "prompt")
        wait = mock_sleep.call_args.args[0]
        assert wait <= MAX_WAIT
