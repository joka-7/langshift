"""
Tests for repo_translator.providers.external_chat
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from repo_translator.providers.external_chat import (
    EXTERNAL_CHAT_PROVIDER_NAMES,
    build_external_chat_urls,
    build_translation_question,
)

# ─────────────────────────────────────────────
# EXTERNAL_CHAT_PROVIDER_NAMES
# ─────────────────────────────────────────────

class TestExternalChatProviderNames:
    def test_lists_all_four_providers(self):
        assert set(EXTERNAL_CHAT_PROVIDER_NAMES) == {
            "ChatGPT", "Claude", "Gemini (Google AI Mode)", "Groq",
        }


# ─────────────────────────────────────────────
# build_external_chat_urls
# ─────────────────────────────────────────────

class TestBuildExternalChatUrls:
    def test_returns_one_url_per_provider(self):
        urls = build_external_chat_urls("translate this")
        assert set(urls) == set(EXTERNAL_CHAT_PROVIDER_NAMES)

    def test_every_url_is_https(self):
        urls = build_external_chat_urls("translate this")
        assert all(url.startswith("https://") for url in urls.values())

    def test_chatgpt_url_prefills_the_question(self):
        urls = build_external_chat_urls("translate my code")
        parsed = urlparse(urls["ChatGPT"])
        assert parsed.hostname == "chatgpt.com"
        assert parse_qs(parsed.query)["q"] == ["translate my code"]
        assert parse_qs(parsed.query)["hints"] == ["search"]

    def test_claude_url_prefills_the_question(self):
        urls = build_external_chat_urls("translate my code")
        parsed = urlparse(urls["Claude"])
        assert parsed.hostname == "claude.ai"
        assert parsed.path == "/new"
        assert parse_qs(parsed.query)["q"] == ["translate my code"]

    def test_gemini_url_uses_google_ai_mode_search(self):
        urls = build_external_chat_urls("translate my code")
        parsed = urlparse(urls["Gemini (Google AI Mode)"])
        assert parsed.hostname == "www.google.com"
        assert parse_qs(parsed.query)["q"] == ["translate my code"]
        assert parse_qs(parsed.query)["udm"] == ["50"]

    def test_groq_has_no_prefill_and_falls_back_to_homepage(self):
        urls = build_external_chat_urls("translate my code")
        assert urls["Groq"] == "https://groq.com/"

    def test_question_is_url_encoded(self):
        urls = build_external_chat_urls("a & b = c?")
        assert "a & b" not in urls["Claude"]
        parsed = urlparse(urls["Claude"])
        assert parse_qs(parsed.query)["q"] == ["a & b = c?"]


# ─────────────────────────────────────────────
# build_translation_question
# ─────────────────────────────────────────────

class TestBuildTranslationQuestion:
    def test_includes_languages_and_source(self):
        q = build_translation_question("const x = 1;", "typescript", "python")
        assert "typescript" in q
        assert "python" in q
        assert "const x = 1;" in q

    def test_short_source_is_not_truncated(self):
        q = build_translation_question("const x = 1;", "typescript", "python")
        assert "truncated" not in q

    def test_long_source_is_truncated(self):
        long_source = "x" * 5000
        q = build_translation_question(long_source, "typescript", "python")
        assert "truncated" in q
        assert len(q) < len(long_source)

    def test_truncation_keeps_only_the_configured_prefix(self):
        long_source = "a" * 1500 + "b" * 500
        q = build_translation_question(long_source, "typescript", "python")
        assert "a" * 1500 in q
        assert "b" not in q
