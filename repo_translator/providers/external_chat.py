"""Free public AI chat hand-off — the "nothing left to retry" escape hatch.

Mirrors ``EXTERNAL_CHAT_PROVIDERS``/``openExternalChat`` in
``@joka-7/modeldispatcher-browser-agent`` (the shared browser package a few
of this project's sibling apps use for the same thing) — reimplemented here
in Python since this is a CLI tool, not a browser page. Rather than opening
a window, ``translate_repo`` prints these URLs to the terminal for a file
whose translation ultimately failed after every retry (most modern
terminals turn a printed ``http(s)`` URL into a clickable link on their
own), so a run that ends in a real failure still leaves the user a
concrete next step instead of just an error.

Same caveat as the TS package: the query-prefill URL parameters below are
undocumented, reverse-engineered conventions, not a stable API any vendor
promises to keep working.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlencode

__all__ = ["EXTERNAL_CHAT_PROVIDER_NAMES", "build_external_chat_urls", "build_translation_question"]

# A full source file is often far larger than what's sane to cram into a URL
# query param (most browsers/proxies start truncating or rejecting well
# before 8000 chars) — this cap keeps every generated URL safely short while
# still giving the receiving chat product enough to work with.
_MAX_QUESTION_SOURCE_CHARS = 1500


def _chatgpt_url(question: str) -> str:
    return f"https://chatgpt.com/?{urlencode({'q': question, 'hints': 'search'})}"


def _claude_url(question: str) -> str:
    return f"https://claude.ai/new?{urlencode({'q': question})}"


def _gemini_url(question: str) -> str:
    # gemini.google.com itself has no known prefill parameter. Google
    # Search's AI Mode does (udm=50), and is the more reliable target.
    return f"https://www.google.com/search?{urlencode({'q': question, 'udm': '50'})}"


_GROQ_HOME_URL = "https://groq.com/"

# name -> URL builder, or None for a provider with no known prefill parameter
# (falls back to its plain homepage).
_BUILDERS: dict[str, Callable[[str], str] | None] = {
    "ChatGPT": _chatgpt_url,
    "Claude": _claude_url,
    "Gemini (Google AI Mode)": _gemini_url,
    "Groq": None,
}

EXTERNAL_CHAT_PROVIDER_NAMES: tuple[str, ...] = tuple(_BUILDERS)


def build_external_chat_urls(question: str) -> dict[str, str]:
    """Return ``{provider_name: url}`` for every provider, pre-filled with
    ``question`` where a provider has a known prefill parameter."""
    return {
        name: (builder(question) if builder else _GROQ_HOME_URL)
        for name, builder in _BUILDERS.items()
    }


def build_translation_question(source_code: str, from_lang: str, to_lang: str) -> str:
    """A plain-English version of the translation request that just failed —
    what gets handed to an external AI chat product instead of this file's
    full internal prompt (schema JSON, formatting instructions, etc. aren't
    useful to paste into a chat box)."""
    truncated = len(source_code) > _MAX_QUESTION_SOURCE_CHARS
    snippet = source_code[:_MAX_QUESTION_SOURCE_CHARS]
    suffix = "\n... (truncated)" if truncated else ""
    return f"Translate this {from_lang} code to {to_lang}:\n\n{snippet}{suffix}"
