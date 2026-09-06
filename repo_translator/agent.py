"""
Core translation agent — reads files, translates via LLM provider, runs & fixes.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import textwrap
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from repo_translator.manifest import _find_manifests, translate_manifest
from repo_translator.providers.base import LLMProvider
from repo_translator.providers.external_chat import (
    build_external_chat_urls,
    build_translation_question,
)
from repo_translator.providers.retry import complete_with_backoff
from repo_translator.report import FileResult, TranslationReport

# ---------------------------------------------------------------------------
# Language metadata
# ---------------------------------------------------------------------------

LANGUAGE_META: dict[str, dict] = {
    "typescript": {
        "aliases": ["ts"],
        "extensions": [".ts", ".tsx"],
        "runner": None,
        "test_patterns": [".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx"],
        "test_runner": ["npx", "jest", "--passWithNoTests"],
    },
    "javascript": {
        "aliases": ["js"],
        "extensions": [".js", ".jsx", ".mjs"],
        "runner": ["node"],
        "test_patterns": [".test.js", ".spec.js"],
        "test_runner": ["npx", "jest", "--passWithNoTests"],
    },
    "python": {
        "aliases": ["py"],
        "extensions": [".py"],
        "runner": ["python3"],
        "test_patterns": ["test_", "_test.py"],
        "test_runner": ["python3", "-m", "pytest", "-x", "-q"],
    },
    "java": {
        "aliases": [],
        "extensions": [".java"],
        "runner": None,
        "test_patterns": ["Test.java", "Tests.java"],
        "test_runner": None,
    },
    "go": {
        "aliases": [],
        "extensions": [".go"],
        "runner": ["go", "run"],
        "test_patterns": ["_test.go"],
        "test_runner": ["go", "test", "./..."],
    },
    "rust": {
        "aliases": ["rs"],
        "extensions": [".rs"],
        "runner": None,
        "test_patterns": [],
        "test_runner": ["cargo", "test"],
    },
    "ruby": {
        "aliases": ["rb"],
        "extensions": [".rb"],
        "runner": ["ruby"],
        "test_patterns": ["_spec.rb", "_test.rb"],
        "test_runner": ["bundle", "exec", "rspec"],
    },
    "csharp": {
        "aliases": ["cs", "c#"],
        "extensions": [".cs"],
        "runner": None,
        "test_patterns": ["Tests.cs", "Test.cs"],
        "test_runner": None,
    },
    "php": {
        "aliases": [],
        "extensions": [".php"],
        "runner": ["php"],
        "test_patterns": ["Test.php"],
        "test_runner": ["./vendor/bin/phpunit"],
    },
    "kotlin": {
        "aliases": ["kt"],
        "extensions": [".kt", ".kts"],
        "runner": None,
        "test_patterns": ["Test.kt", "Tests.kt"],
        "test_runner": None,
    },
    "swift": {
        "aliases": [],
        "extensions": [".swift"],
        "runner": None,
        "test_patterns": ["Tests.swift"],
        "test_runner": ["swift", "test"],
    },
    "cpp": {
        "aliases": ["c++"],
        "extensions": [".cpp", ".cc", ".cxx", ".h", ".hpp"],
        "runner": None,
        "test_patterns": ["_test.cpp", "Test.cpp"],
        "test_runner": ["python3", "-m", "repo_translator.cpp_test_runner"],
    },
    "c": {
        "aliases": [],
        "extensions": [".c", ".h"],
        "runner": None,
        "test_patterns": ["_test.c"],
        "test_runner": None,
    },
}

# Maps (from_lang, to_lang) → target test framework name
TEST_FRAMEWORK_MAP: dict[tuple[str, str], str] = {
    ("typescript", "python"): "pytest",
    ("javascript", "python"): "pytest",
    ("go",         "python"): "pytest",
    ("java",       "python"): "pytest",
    ("ruby",       "python"): "pytest",
    ("cpp",        "python"): "pytest",
    ("c",          "python"): "pytest",
    ("python", "javascript"): "jest",
    ("python", "typescript"): "jest",
    ("python",       "go"):   "Go testing package (testing.T)",
    ("python",     "java"):   "JUnit 5",
    ("python",     "rust"):   "Rust built-in #[test]",
}

# Build flat alias → canonical-name map
_ALIAS_MAP: dict[str, str] = {}
for _name, _meta in LANGUAGE_META.items():
    _ALIAS_MAP[_name] = _name
    for _alias in _meta["aliases"]:
        _ALIAS_MAP[_alias] = _name


def resolve_language(name: str) -> str:
    key = name.lower().strip()
    if key not in _ALIAS_MAP:
        raise ValueError(
            f"Unknown language '{name}'. Supported: {', '.join(LANGUAGE_META)}"
        )
    return _ALIAS_MAP[key]


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", "target", "vendor", ".idea", ".vscode",
    "out", "bin", "obj", ".gradle", ".mvn",
}


def _is_test_file(path: Path, lang: str) -> bool:
    patterns = LANGUAGE_META[lang].get("test_patterns", [])
    name = path.name
    for pattern in patterns:
        if pattern.startswith("."):
            if name.endswith(pattern):
                return True
        elif pattern.endswith(".py") or pattern.endswith(".go") or "." in pattern:
            if name.endswith(pattern):
                return True
        else:
            if name.startswith(pattern):
                return True
    return False


def collect_files(repo_path: Path, from_lang: str) -> list[Path]:
    """Every source file of ``from_lang`` under ``repo_path``.

    Args:
        repo_path: Repository root to search.
        from_lang: Resolved source language; its extensions decide what counts.

    Returns:
        Matching files, sorted. Symlinks resolving outside ``repo_path`` are
        skipped: the repository being translated is untrusted input, and its
        contents are sent to the provider, so a link named like a source file
        must not pull in a file outside the tree. ``rglob`` already declines to
        recurse into symlinked *directories*; this covers symlinked files.
    """
    exts = set(LANGUAGE_META[from_lang]["extensions"])
    root = repo_path.resolve()
    files: list[Path] = []
    for path in repo_path.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not (path.is_file() and path.suffix in exts):
            continue
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            continue
        files.append(path)
    return sorted(files)


# ---------------------------------------------------------------------------
# Provider defaults + pricing
# ---------------------------------------------------------------------------

DEFAULT_PROVIDER = "claude"
DEFAULT_MODEL    = "sonnet"
MAX_FIX_ATTEMPTS = 3
_CHARS_PER_TOKEN      = 4
_PROMPT_OVERHEAD_TOKS = 200

# Files larger than this get split into several translation calls (see
# _split_into_chunks) instead of one — both to stay well under the
# max_tokens=8096 output cap in _translate_chunk (a same-sized-or-larger
# translated file could otherwise get truncated) and to avoid overrunning
# smaller providers' context windows. Kept as a module constant, not a
# hardcoded literal, so tests can lower it instead of generating a
# multi-thousand-line fixture to exercise chunking.
CHUNK_THRESHOLD_CHARS = 12000

# ---------------------------------------------------------------------------
# Cross-file context (--cross-file-context, off by default)
# ---------------------------------------------------------------------------
# Each file is still translated independently — there's no shared
# translation state — but when enabled, every prompt gets a short read-only
# "repo map" listing the *other* source files and their top-level symbols,
# so the model can keep cross-file imports/calls consistent instead of
# guessing at names it's never seen.
#
# Regex-based, not a real parser: good enough to hint an LLM, not a
# guarantee of completeness or precision. Unmapped languages (cpp, c —
# their header/impl split and lack of an `export` keyword make a single
# regex unreliable) fall back to listing filenames only, no symbols.
_SYMBOL_PATTERNS: dict[str, re.Pattern[str]] = {
    "typescript": re.compile(
        r"^export\s+(?:default\s+)?(?:async\s+)?"
        r"(?:function|class|const|let|var|interface|type|enum)\s+(\w+)",
        re.MULTILINE,
    ),
    "javascript": re.compile(
        r"^export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)",
        re.MULTILINE,
    ),
    "python": re.compile(r"^(?:def|class)\s+(\w+)", re.MULTILINE),
    "go": re.compile(r"^(?:func\s+(?:\([^)]*\)\s+)?|type\s+)(\w+)", re.MULTILINE),
    # Rust has an explicit visibility keyword, unlike the other languages
    # here — so unlike them, `pub` is required, not optional: an item
    # without it is invisible outside its module and listing it as
    # "exported" would be actively misleading for cross-file references.
    "rust": re.compile(r"^pub\s+(?:fn|struct|enum|trait)\s+(\w+)", re.MULTILINE),
    "ruby": re.compile(r"^(?:def|class|module)\s+(\w+)", re.MULTILINE),
    "csharp": re.compile(
        r"^\s*public\s+(?:static\s+|abstract\s+|sealed\s+)*(?:class|interface|enum|struct)\s+(\w+)",
        re.MULTILINE,
    ),
    "php": re.compile(r"^(?:function|class|interface|trait)\s+(\w+)", re.MULTILINE),
    "kotlin": re.compile(r"^(?:public\s+)?(?:fun|class|interface|object)\s+(\w+)", re.MULTILINE),
    "swift": re.compile(
        r"^(?:public\s+|open\s+)?(?:func|class|struct|enum|protocol)\s+(\w+)", re.MULTILINE,
    ),
    "java": re.compile(
        r"^\s*public\s+(?:static\s+|abstract\s+|final\s+)*(?:class|interface|enum)\s+(\w+)",
        re.MULTILINE,
    ),
}
_MAX_SYMBOLS_PER_FILE  = 20
_REPO_MAP_MAX_CHARS    = 4000  # caps the repo map's contribution to prompt size


def _extract_symbols(source_code: str, lang: str) -> list[str]:
    """Best-effort top-level symbol names for lang. See _SYMBOL_PATTERNS."""
    pattern = _SYMBOL_PATTERNS.get(lang)
    if pattern is None:
        return []
    names: list[str] = []
    for m in pattern.finditer(source_code):
        name = m.group(1)
        if name not in names:
            names.append(name)
        if len(names) >= _MAX_SYMBOLS_PER_FILE:
            break
    return names


def _build_repo_map(files: list[Path], repo_path: Path, from_lang: str) -> dict[str, list[str]]:
    """{relative_path: [top-level symbol names]} for every collected source file."""
    repo_map: dict[str, list[str]] = {}
    for f in files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        repo_map[str(f.relative_to(repo_path))] = _extract_symbols(source, from_lang)
    return repo_map


def _format_repo_map(
    repo_map: dict[str, list[str]], exclude: str, threshold: int = _REPO_MAP_MAX_CHARS,
) -> str:
    """
    Render repo_map as a prompt-ready listing, excluding the file currently
    being translated (it doesn't need a map entry for itself). Stops adding
    entries once `threshold` chars are reached and notes how many were left
    out, rather than growing the prompt unbounded for large repos.
    """
    lines: list[str] = []
    total = 0
    omitted = 0
    for path, symbols in repo_map.items():
        if path == exclude:
            continue
        entry = f"- {path}" + (f": {', '.join(symbols)}" if symbols else "")
        if total + len(entry) > threshold:
            omitted += 1
            continue
        lines.append(entry)
        total += len(entry)
    if omitted:
        lines.append(f"... ({omitted} more file(s) omitted)")
    return "\n".join(lines)

# (provider, model_name) → (input $/MTok, output $/MTok)
# Groq has a free tier (rate-limited); prices below are for paid/on-demand usage.
# openai-compat pricing is unknown (varies by service) — will show None in estimate.
PRICING: dict[tuple[str, str], tuple[float, float]] = {
    ("claude",  "haiku"):                       (1.00,   5.00),
    ("claude",  "sonnet"):                      (2.00,  10.00),
    ("claude",  "opus"):                        (5.00,  25.00),
    ("openai",  "gpt-4o"):                      (5.00,  15.00),
    ("openai",  "gpt-4o-mini"):                 (0.15,   0.60),
    ("openai",  "gpt-4-turbo"):                 (10.00, 30.00),
    ("gemini",  "gemini-1.5-pro"):              (3.50,  10.50),
    ("gemini",  "gemini-1.5-flash"):            (0.35,   1.05),
    ("gemini",  "gemini-2.0-flash"):            (0.10,   0.40),
    ("groq",    "llama-3.3-70b-versatile"):     (0.59,   0.79),
    ("groq",    "llama-3.1-8b-instant"):        (0.05,   0.08),
    ("groq",    "qwen/qwen3-32b"):              (0.29,   0.59),
    ("groq",    "meta-llama/llama-4-scout-17b-16e-instruct"): (0.11, 0.34),
}
# Confidence scoring: extra tokens per file (truncated src + translated + prompt)
_CONFIDENCE_INPUT_TOKS  = 1200
_CONFIDENCE_OUTPUT_TOKS = 60


def price_label(provider: str, model: str, base_url: str | None = None) -> str:
    """Human-readable pricing description for a provider/model combo."""
    pricing_info = PRICING.get((provider, model))
    if provider == "offline":
        return "free (no API — rule-based offline)"
    if provider == "ollama":
        return "free (local)"
    if provider == "groq" and not pricing_info:
        return "free tier (rate-limited)"
    if provider == "groq" and pricing_info:
        return f"${pricing_info[0]:.2f}/${pricing_info[1]:.2f} per MTok  (free tier available)"
    if provider == "openai-compat":
        return f"varies by service  ({base_url or 'no base URL set'})"
    if pricing_info:
        return f"${pricing_info[0]:.2f}/${pricing_info[1]:.2f} per MTok in/out"
    return "pricing unknown"


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------

def _split_into_chunks(
    source_code: str, threshold: int = CHUNK_THRESHOLD_CHARS,
) -> list[str]:
    """
    Split source_code into chunks at blank-line boundaries — a language-
    agnostic stand-in for "top-level boundary" that keeps a chunk from
    cutting a function/class body in half — greedily packing consecutive
    blocks up to `threshold` chars each. A single block that alone exceeds
    the threshold is kept whole rather than split further (an oversized
    chunk is recoverable; a syntactically broken one usually isn't).

    Returns [source_code] unchanged when it's already at or under the
    threshold, which is the common case and keeps single-call callers
    (the vast majority of files) on the original one-prompt-per-file path.
    """
    if len(source_code) <= threshold:
        return [source_code]

    # Capture the blank-line separators themselves so concatenating the
    # blocks back together reproduces the original text exactly.
    parts = re.split(r"(\n[ \t]*\n)", source_code)
    blocks = [
        parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
        for i in range(0, len(parts), 2)
    ]

    chunks: list[str] = []
    current = ""
    for block in blocks:
        if current and len(current) + len(block) > threshold:
            chunks.append(current)
            current = block
        else:
            current += block
    if current:
        chunks.append(current)
    return chunks or [source_code]


def _translate_chunk(
    provider: LLMProvider,
    source_code: str,
    from_lang: str,
    to_lang: str,
    is_test: bool = False,
    error_context: str | None = None,
    chunk_index: int | None = None,
    chunk_total: int | None = None,
    repo_context: str | None = None,
) -> str:
    fix_note = ""
    if error_context:
        fix_note = textwrap.dedent(f"""
            The previous translation produced this runtime error:
            ---
            {error_context}
            ---
            Please fix the translation so it runs without errors.
        """)

    test_note = ""
    if is_test:
        framework = TEST_FRAMEWORK_MAP.get((from_lang, to_lang), "the standard test framework")
        test_note = f"""
        - This is a TEST file. Translate test cases using {framework}.
        - Preserve all test names, assertions, and test structure.
        - Use {framework} idioms (describe/it, def test_, #[test], etc.).
        """

    chunk_note = ""
    if chunk_total is not None and chunk_total > 1:
        chunk_note = f"""
        - This is chunk {chunk_index} of {chunk_total} of ONE larger file, split only because
          of its size. Translate just this chunk's code, exactly as given, with no added
          file-level framing (no extra imports, no repeated boilerplate). The chunks'
          translations will be concatenated in order to form the final file.
        """

    # A single short line, not a multi-line block: it's interpolated inside
    # the dedented template below (see the NOTE further down), and unlike
    # source_code it never contains a fenced code block that reindenting
    # could break — but it still shouldn't introduce a *different* common
    # indentation than the rest of the template. The repo map's actual
    # (multi-line, zero-indent) content is appended after the dedent instead.
    repo_note = (
        "\n        - Other files in this repo and their exported top-level symbols are "
        "listed after the source below — use them only to keep cross-file references "
        "(imports, calls) consistent; do not translate or restate that list."
        if repo_context else ""
    )

    # NOTE: source_code is appended *after* dedent. If it were interpolated
    # inside the dedented block, its un-indented lines would defeat
    # textwrap.dedent's common-prefix calculation and leak the template's
    # indentation into the fenced code block (breaks the offline provider,
    # which extracts the block verbatim).
    instructions = textwrap.dedent(f"""
        You are an expert programmer. Translate the following {from_lang} code to {to_lang}.

        Rules:
        - Output ONLY the translated code, no markdown fences, no explanation.
        - Preserve the original logic, structure, and comments (translated).
        - Use idiomatic {to_lang} patterns and standard library where possible.
        - Replace language-specific imports/packages with {to_lang} equivalents.
        - If a direct equivalent doesn't exist, write a clear TODO comment.
        {test_note}{fix_note}{chunk_note}{repo_note}
    """).strip()

    prompt = f"{instructions}\n\nSource ({from_lang}):\n```\n{source_code}\n```"
    if repo_context:
        prompt += f"\n\nOther files in this repo:\n{repo_context}"

    return complete_with_backoff(provider, prompt, max_tokens=8096)


def _translate_once(
    provider: LLMProvider,
    source_code: str,
    from_lang: str,
    to_lang: str,
    is_test: bool = False,
    error_context: str | None = None,
    repo_context: str | None = None,
) -> str:
    """
    Translate one file's source. Transparent to callers: this always
    returns one translated string per call, whether it took one provider
    call or — for a file over CHUNK_THRESHOLD_CHARS — several, chunked by
    _split_into_chunks and concatenated in order. repo_context, if given
    (see _format_repo_map), is a read-only "here's the rest of the repo"
    note included in every chunk's prompt — it isn't shared translation
    state, just extra context for that one call.
    """
    chunks = _split_into_chunks(source_code, threshold=CHUNK_THRESHOLD_CHARS)
    if len(chunks) == 1:
        return _translate_chunk(
            provider, source_code, from_lang, to_lang, is_test, error_context,
            repo_context=repo_context,
        )
    return "".join(
        _translate_chunk(
            provider, chunk, from_lang, to_lang, is_test, error_context,
            chunk_index=idx, chunk_total=len(chunks), repo_context=repo_context,
        )
        for idx, chunk in enumerate(chunks, 1)
    )


def _score_confidence(
    provider: LLMProvider,
    source_code: str,
    translated_code: str,
    from_lang: str,
    to_lang: str,
    attempts: int,
    run_ok: bool,
) -> tuple[int, str]:
    """Ask the provider to score the translation quality 0-100."""
    todo_count = translated_code.lower().count("# todo") + translated_code.lower().count("// todo")
    src_excerpt   = source_code[:2000]   + ("…" if len(source_code)   > 2000 else "")
    trans_excerpt = translated_code[:2000] + ("…" if len(translated_code) > 2000 else "")

    prompt = textwrap.dedent(f"""
        You just translated a {from_lang} file to {to_lang}. Score the translation 0–100.

        Consider:
        - Constructs with no direct equivalent ({from_lang} → {to_lang}): goroutines,
          ownership, generics, async differences, etc.
        - Cross-file imports that may be broken — this file was translated in isolation
          with no knowledge of other files in the repo.
        - TODO comments added: {todo_count}
        - Auto-run: {"passed" if run_ok else "failed"}, attempts needed: {attempts}
        - Structural distance between {from_lang} and {to_lang}

        Be honest about real-world usability. A file that runs but has broken cross-file
        imports should score 40–60, not 90.

        Source ({from_lang}):
        ```
        {src_excerpt}
        ```

        Translation ({to_lang}):
        ```
        {trans_excerpt}
        ```

        Respond with JSON only, no markdown fences:
        {{"score": <0-100>, "reason": "<one sentence>"}}
    """).strip()

    # Scoring is advisory -- a failure here must never sink an otherwise good
    # translation -- but the two ways it fails are different, and the reason is
    # reported rather than collapsed into a bare 50 the reader can't explain.
    try:
        raw = complete_with_backoff(provider, prompt, max_tokens=256).strip()
    except Exception as e:  # provider/SDK errors are an open set
        return 50, f"confidence scoring failed: {type(e).__name__}"

    try:
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data   = json.loads(raw.strip())
        score  = max(0, min(100, int(data["score"])))
        reason = str(data.get("reason", ""))[:200]
    except (IndexError, KeyError, TypeError, ValueError) as e:
        # json.JSONDecodeError subclasses ValueError, as does int() on a
        # non-numeric string. Anything outside this set is a bug in the block
        # above, and should surface rather than be reported as a score of 50.
        return 50, f"confidence scoring failed: unparsable response ({type(e).__name__})"
    return score, reason


# Recorded as a file's run_output when execute=False, so a report makes clear the
# translation was never validated by running it rather than silently passing.
_NOT_EXECUTED_NOTE = "(execution disabled — translated code was not run)"


def _try_run(to_lang: str, code: str) -> tuple[bool, str]:
    runner = LANGUAGE_META[to_lang].get("runner")
    if runner is None:
        return True, "(auto-run not supported for this language)"

    with tempfile.NamedTemporaryFile(
        suffix=LANGUAGE_META[to_lang]["extensions"][0],
        mode="w",
        delete=False,
    ) as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            runner + [tmp_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return True, result.stdout
        return False, result.stderr or result.stdout
    except subprocess.TimeoutExpired:
        return True, "(execution timed out — likely interactive or long-running)"
    except FileNotFoundError:
        return True, f"('{runner[0]}' not found in PATH — skipping auto-run)"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def run_tests(output_path: Path, to_lang: str, verbose: bool = True) -> tuple[bool, str]:
    test_runner = LANGUAGE_META[to_lang].get("test_runner")
    if not test_runner:
        return True, "(test runner not configured for this language)"

    if verbose:
        print(f"\n  🧪 Running translated tests ({' '.join(test_runner)})...")

    try:
        result = subprocess.run(
            test_runner,
            cwd=output_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        passed = result.returncode == 0
        output = result.stdout + result.stderr
        if verbose:
            status = "✅ Tests passed" if passed else "❌ Tests failed"
            print(f"  {status}")
            if not passed:
                for line in output.splitlines()[-20:]:
                    print(f"    {line}")
        return passed, output
    except subprocess.TimeoutExpired:
        return False, "Test run timed out after 120s"
    except FileNotFoundError:
        return True, f"(test runner '{test_runner[0]}' not found — skipping)"


# ---------------------------------------------------------------------------
# Output path helper
# ---------------------------------------------------------------------------

def _output_path(
    source_file: Path,
    repo_root: Path,
    output_root: Path,
    to_lang: str,
) -> Path:
    rel = source_file.relative_to(repo_root)
    new_ext = LANGUAGE_META[to_lang]["extensions"][0]
    return output_root / rel.with_suffix(new_ext)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def estimate_translation(
    repo_path: Path,
    from_lang: str,
    to_lang: str,
    translate_manifests: bool = True,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    score_confidence: bool = True,
) -> dict:
    """Estimate token usage and cost without making any API calls."""
    from_lang = resolve_language(from_lang)
    to_lang   = resolve_language(to_lang)
    files     = collect_files(repo_path, from_lang)

    src_chars = 0
    for f in files:
        try:
            src_chars += len(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass

    manifest_chars = 0
    manifest_count = 0
    if translate_manifests:
        for m in _find_manifests(repo_path, from_lang):
            try:
                manifest_chars += len(m.read_text(encoding="utf-8", errors="replace"))
                manifest_count += 1
            except OSError:
                pass

    total_calls = len(files) + manifest_count
    input_toks  = (
        (src_chars + manifest_chars) // _CHARS_PER_TOKEN
        + total_calls * _PROMPT_OVERHEAD_TOKS
    )
    output_toks = (src_chars + manifest_chars) // _CHARS_PER_TOKEN

    if score_confidence:
        input_toks  += len(files) * _CONFIDENCE_INPUT_TOKS
        output_toks += len(files) * _CONFIDENCE_OUTPUT_TOKS

    if provider in ("ollama", "openai-compat", "offline"):
        # ollama/offline are free; openai-compat pricing varies by service
        cost_usd: float | None = 0.0 if provider in ("ollama", "offline") else None
    else:
        pricing = PRICING.get((provider, model))
        if pricing:
            inp_price, out_price = pricing
            cost_usd = (
                input_toks  / 1_000_000 * inp_price +
                output_toks / 1_000_000 * out_price
            )
        else:
            cost_usd = None  # unknown pricing

    return {
        "from_lang":      from_lang,
        "to_lang":        to_lang,
        "provider":       provider,
        "model":          model,
        "file_count":     len(files),
        "manifest_count": manifest_count,
        "input_tokens":   input_toks,
        "output_tokens":  output_toks,
        "estimated_cost": cost_usd,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _summary(report: TranslationReport) -> dict:
    return {
        "total": report.total,
        "translated": report.translated,
        "failed": report.failed,
        "skipped": report.skipped,
        "needed_retry": report.needed_retry,
        "high_confidence": report.high_confidence,
        "needs_review": report.needs_review,
        "tests_passed": report.tests_passed,
        "elapsed_seconds": round(report.elapsed_seconds, 2),
    }


CHECKPOINT_FILENAME = ".translation_state.json"


def _load_checkpoint(
    output_path: Path, repo_path: Path, from_lang: str, to_lang: str,
) -> dict[str, dict]:
    """
    Returns {rel_path: FileResult-dict} for files already successfully
    translated in a previous run, if a matching checkpoint exists at
    output_path/.translation_state.json — matching means the same input
    repo and language pair, so a checkpoint from a different translation
    that happened to reuse the same output directory is never applied.
    Returns {} if there's no usable checkpoint (missing, corrupt, or for a
    different run).
    """
    state_file = output_path / CHECKPOINT_FILENAME
    if not state_file.exists():
        return {}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if (
        data.get("input_path") != str(repo_path)
        or data.get("from_lang") != from_lang
        or data.get("to_lang") != to_lang
    ):
        return {}
    return {
        f["path"]: f
        for f in data.get("files", [])
        if f.get("status") in ("ok", "ok_with_warnings")
    }


def _save_checkpoint(
    output_path: Path, repo_path: Path, from_lang: str, to_lang: str,
    report: TranslationReport,
) -> None:
    """
    Persist progress so an interrupted run can resume instead of starting
    over. Called after every file, so a crash mid-run loses at most the
    file in flight. Write failures are logged and otherwise ignored —
    a broken checkpoint should never abort an in-progress translation.
    """
    state_file = output_path / CHECKPOINT_FILENAME
    data = {
        "input_path": str(repo_path),
        "from_lang": from_lang,
        "to_lang": to_lang,
        "files": [asdict(f) for f in report.files],
    }
    try:
        output_path.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _run_tests_with_retry(
    provider: LLMProvider,
    repo_path: Path,
    output_path: Path,
    from_lang: str,
    to_lang: str,
    test_files: list[Path],
    verbose: bool,
    on_progress: Callable[[dict], None] | None,
    repo_map: dict[str, list[str]] | None = None,
) -> tuple[bool, str]:
    """
    Run the translated test suite, retrying like the source-file auto-fix
    loop does: on failure, re-translate every test file with the runner's
    output as error_context and run again, up to the provider's
    max_fix_attempts (the offline provider's max_fix_attempts=1 means no
    retries, same as for source files).

    Unlike the source-file loop, there's no reliable way to tell which
    individual test file caused a failure from arbitrary test-runner output
    across 13 languages' test frameworks — so a retry re-translates every
    test file, not just the failing one(s).
    """
    def _emit(event: dict) -> None:
        if on_progress:
            on_progress(event)

    max_attempts = getattr(provider, "max_fix_attempts", MAX_FIX_ATTEMPTS)
    passed, test_output = run_tests(output_path, to_lang, verbose=verbose)

    attempt = 1
    while not passed and attempt < max_attempts and test_files:
        attempt += 1
        if verbose:
            print(f"\n  🔁 Test suite failed, re-translating {len(test_files)} "
                  f"test file(s) (attempt {attempt}/{max_attempts})...")
        _emit({"type": "tests_retry", "attempt": attempt, "total": max_attempts})

        for src_file in test_files:
            source_code = src_file.read_text(encoding="utf-8", errors="replace")
            if not source_code.strip():
                continue
            rel = str(src_file.relative_to(repo_path))
            repo_context = _format_repo_map(repo_map, exclude=rel) if repo_map else None
            try:
                translated_code = _translate_once(
                    provider, source_code, from_lang, to_lang,
                    is_test=True, error_context=test_output, repo_context=repo_context,
                )
            except Exception as e:
                if verbose:
                    print(f"    ✗ Failed to re-translate {src_file.name}: {e}")
                continue  # leave the previous translation of this file in place
            dest = _output_path(src_file, repo_path, output_path, to_lang)
            dest.write_text(translated_code, encoding="utf-8")

        passed, test_output = run_tests(output_path, to_lang, verbose=verbose)

    return passed, test_output


def translate_repo(
    repo_path: Path,
    output_path: Path,
    from_lang: str,
    to_lang: str,
    provider: LLMProvider,
    verbose: bool = True,
    translate_manifests: bool = True,
    run_tests_after: bool = False,
    score_confidence: bool = True,
    resume: bool = True,
    cross_file_context: bool = False,
    execute: bool = True,
    on_progress: Callable[[dict], None] | None = None,
) -> TranslationReport:
    """
    on_progress, if given, is called with a dict for each notable event:
      {"type": "manifest_done", "count": int}
      {"type": "file_start", "index": int, "total": int, "path": str, "is_test": bool}
      {"type": "file_done", "index": int, "total": int, "path": str, "status": str,
       "attempts": int, "confidence": int | None}
      {"type": "tests_retry", "attempt": int, "total": int}
      {"type": "tests_done", "passed": bool}
      {"type": "finished", "summary": dict}
    Consumers (e.g. the web UI) use this to stream live progress; the CLI doesn't pass it.

    execute: if False, translated code is never handed to an interpreter --
    the per-file auto-run is skipped (so the auto-fix loop has no error to
    feed back, and every file is accepted on its first attempt) and the
    test-suite run is suppressed even when run_tests_after is True, since
    running a test suite executes translated code too. Output quality drops:
    nothing validates that what the model wrote actually runs.

    resume: if True (the default) and output_path already holds a checkpoint
    (.translation_state.json) from a previous run of this exact repo_path/
    from_lang/to_lang, files already recorded "ok"/"ok_with_warnings" there
    are skipped rather than re-translated — the checkpoint's dest file is
    trusted only if it still exists on disk. A checkpoint for a different
    repo or language pair (e.g. output_path reused for an unrelated run) is
    never applied. Pass resume=False to always start from scratch.

    cross_file_context: off by default, to keep existing prompt shapes and
    cost estimates unchanged unless opted in. When True, every file's
    prompt gets a read-only "repo map" listing the *other* source files and
    their top-level symbols (see _build_repo_map/_format_repo_map), to help
    the model keep cross-file imports/calls consistent. Each file is still
    translated independently — there's no translation state shared between
    files, just this one extra note per prompt.
    """
    def _emit(event: dict) -> None:
        if on_progress:
            on_progress(event)

    from_lang = resolve_language(from_lang)
    to_lang   = resolve_language(to_lang)

    report = TranslationReport(
        from_lang=from_lang,
        to_lang=to_lang,
        input_path=str(repo_path),
        output_path=str(output_path),
    )

    start = time.time()

    # ── 1. Translate manifests ─────────────────────────────────────────────
    if translate_manifests:
        manifest_result = translate_manifest(
            provider, repo_path, output_path, from_lang, to_lang, verbose=verbose,
        )
        report.manifest_translated = manifest_result.get("translated", [])
        _emit({"type": "manifest_done", "count": len(report.manifest_translated)})

    # ── 2. Collect source files ────────────────────────────────────────────
    files = collect_files(repo_path, from_lang)
    if not files:
        if verbose:
            print(f"  No {from_lang} files found in {repo_path}")
        report.elapsed_seconds = time.time() - start
        _emit({"type": "finished", "summary": _summary(report)})
        return report

    test_files = [f for f in files if _is_test_file(f, from_lang)]
    src_files  = [f for f in files if not _is_test_file(f, from_lang)]

    if verbose:
        print(f"\n  Found {len(src_files)} source + {len(test_files)} test file(s) → {to_lang}\n")

    repo_map = _build_repo_map(files, repo_path, from_lang) if cross_file_context else {}

    checkpoint = _load_checkpoint(output_path, repo_path, from_lang, to_lang) if resume else {}
    if checkpoint and verbose:
        print(f"  ↻ Resuming: {len(checkpoint)} file(s) already completed in a previous run\n")

    # ── 3. Translate each file ─────────────────────────────────────────────
    for i, src_file in enumerate(files, 1):
        rel     = src_file.relative_to(repo_path)
        dest    = _output_path(src_file, repo_path, output_path, to_lang)
        dest.parent.mkdir(parents=True, exist_ok=True)
        is_test = _is_test_file(src_file, from_lang)
        tag     = " [test]" if is_test else ""

        if verbose:
            print(f"  [{i}/{len(files)}] {rel}{tag}", end=" ", flush=True)
        _emit({"type": "file_start", "index": i, "total": len(files), "path": str(rel),
               "is_test": is_test})

        checkpointed = checkpoint.get(str(rel))
        if checkpointed is not None and dest.exists():
            report.files.append(FileResult(**checkpointed))
            if verbose:
                print("→ ✓ (resumed from checkpoint)")
            _emit({"type": "file_done", "index": i, "total": len(files), "path": str(rel),
                   "status": checkpointed["status"], "attempts": checkpointed["attempts"],
                   "confidence": checkpointed.get("confidence")})
            continue

        source_code = src_file.read_text(encoding="utf-8", errors="replace")

        if not source_code.strip():
            dest.write_text("", encoding="utf-8")
            report.files.append(FileResult(path=str(rel), status="skipped"))
            _save_checkpoint(output_path, repo_path, from_lang, to_lang, report)
            if verbose:
                print("→ (empty, skipped)")
            _emit({"type": "file_done", "index": i, "total": len(files), "path": str(rel),
                   "status": "skipped", "attempts": 0, "confidence": None})
            continue

        chunk_count = len(_split_into_chunks(source_code, threshold=CHUNK_THRESHOLD_CHARS))
        if chunk_count > 1 and verbose:
            print(f"(split into {chunk_count} chunks)", end=" ", flush=True)

        repo_context = _format_repo_map(repo_map, exclude=str(rel)) if cross_file_context else None

        error_ctx  = None
        final_code = ""
        attempts   = 0
        run_ok     = False

        fix_attempts = getattr(provider, 'max_fix_attempts', MAX_FIX_ATTEMPTS)
        for attempt in range(1, fix_attempts + 1):
            attempts = attempt
            try:
                translated_code = _translate_once(
                    provider, source_code, from_lang, to_lang,
                    is_test=is_test, error_context=error_ctx, repo_context=repo_context,
                )
            except Exception as e:
                if verbose:
                    print(f"→ ✗ API error: {e}")
                chat_urls = build_external_chat_urls(
                    build_translation_question(source_code, from_lang, to_lang)
                )
                if verbose:
                    print("   Or ask directly:")
                    for name, url in chat_urls.items():
                        print(f"     {name}: {url}")
                report.files.append(FileResult(
                    path=str(rel), status="failed",
                    attempts=attempt, error=str(e),
                    chunks=chunk_count if chunk_count > 1 else None,
                    external_chat_urls=chat_urls,
                ))
                _save_checkpoint(output_path, repo_path, from_lang, to_lang, report)
                _emit({"type": "file_done", "index": i, "total": len(files), "path": str(rel),
                       "status": "failed", "attempts": attempt, "confidence": None})
                break

            if execute:
                ok, run_output = _try_run(to_lang, translated_code)
            else:
                ok, run_output = True, _NOT_EXECUTED_NOTE
            final_code = translated_code
            run_ok     = ok

            if ok:
                dest.write_text(final_code, encoding="utf-8")
                status = "ok" if attempt == 1 else "ok_with_warnings"

                confidence, confidence_reason = None, None
                if score_confidence:
                    confidence, confidence_reason = _score_confidence(
                        provider, source_code, final_code,
                        from_lang, to_lang, attempts, run_ok,
                    )

                report.files.append(FileResult(
                    path=str(rel), status=status,
                    attempts=attempt, run_output=run_output,
                    confidence=confidence, confidence_reason=confidence_reason,
                    chunks=chunk_count if chunk_count > 1 else None,
                ))
                _save_checkpoint(output_path, repo_path, from_lang, to_lang, report)
                if verbose:
                    extra = f" (fixed in {attempt} attempt(s))" if attempt > 1 else ""
                    conf  = f"  confidence {confidence}/100" if confidence is not None else ""
                    print(f"→ ✓{extra}{conf}")
                _emit({"type": "file_done", "index": i, "total": len(files), "path": str(rel),
                       "status": status, "attempts": attempt, "confidence": confidence})
                break
            else:
                error_ctx = run_output
                if verbose and attempt < fix_attempts:
                    print(f"\n    ↺ attempt {attempt} failed, retrying...", end=" ", flush=True)
        else:
            if final_code:
                warning = (
                    f"# WARNING: auto-run failed after {fix_attempts} attempts.\n"
                    f"# Last error: {(error_ctx or '').splitlines()[0][:120]}\n\n"
                )
                dest.write_text(warning + final_code, encoding="utf-8")

                confidence, confidence_reason = None, None
                if score_confidence:
                    confidence, confidence_reason = _score_confidence(
                        provider, source_code, final_code,
                        from_lang, to_lang, attempts, run_ok,
                    )

                report.files.append(FileResult(
                    path=str(rel), status="ok_with_warnings",
                    attempts=attempts, error=error_ctx,
                    confidence=confidence, confidence_reason=confidence_reason,
                    chunks=chunk_count if chunk_count > 1 else None,
                ))
                _save_checkpoint(output_path, repo_path, from_lang, to_lang, report)
                if verbose:
                    print(f"→ ✓ (saved with warnings after {fix_attempts} attempts)")
                _emit({
                    "type": "file_done", "index": i, "total": len(files), "path": str(rel),
                    "status": "ok_with_warnings", "attempts": attempts, "confidence": confidence,
                })

    # ── 4. Run translated test suite ──────────────────────────────────────
    # Most languages only need to run the test runner when test_files were
    # actually found (test_patterns detects them by filename). A language
    # whose test_runner covers the whole project regardless of individual
    # file names (test_patterns == [], e.g. rust's `cargo test`) has no way
    # to report test_files, so it must not be gated on that list being non-empty.
    to_test_patterns = LANGUAGE_META[to_lang].get("test_patterns", [])
    if execute and run_tests_after and (test_files or not to_test_patterns):
        passed, test_output = _run_tests_with_retry(
            provider, repo_path, output_path, from_lang, to_lang,
            test_files, verbose, on_progress, repo_map=repo_map,
        )
        report.tests_passed = passed
        report.test_output  = test_output
        _emit({"type": "tests_done", "passed": passed})

    report.elapsed_seconds = time.time() - start
    _emit({"type": "finished", "summary": _summary(report)})
    return report
