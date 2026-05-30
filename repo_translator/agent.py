"""
Core translation agent — reads files, translates via LLM provider, runs & fixes.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

from repo_translator.providers.base import LLMProvider
from repo_translator.manifest import translate_manifest, _find_manifests
from repo_translator.report import TranslationReport, FileResult

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
        "test_runner": None,
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
    exts = set(LANGUAGE_META[from_lang]["extensions"])
    files: list[Path] = []
    for path in repo_path.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in exts:
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

# (provider, model_name) → (input $/MTok, output $/MTok)
# Groq has a free tier (rate-limited); prices below are for paid/on-demand usage.
# openai-compat pricing is unknown (varies by service) — will show None in estimate.
PRICING: dict[tuple[str, str], tuple[float, float]] = {
    ("claude",  "haiku"):                       (0.80,   4.00),
    ("claude",  "sonnet"):                      (3.00,  15.00),
    ("claude",  "opus"):                        (15.00, 75.00),
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


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------

def _translate_once(
    provider: LLMProvider,
    source_code: str,
    from_lang: str,
    to_lang: str,
    is_test: bool = False,
    error_context: str | None = None,
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

    prompt = textwrap.dedent(f"""
        You are an expert programmer. Translate the following {from_lang} code to {to_lang}.

        Rules:
        - Output ONLY the translated code, no markdown fences, no explanation.
        - Preserve the original logic, structure, and comments (translated).
        - Use idiomatic {to_lang} patterns and standard library where possible.
        - Replace language-specific imports/packages with {to_lang} equivalents.
        - If a direct equivalent doesn't exist, write a clear TODO comment.
        {test_note}{fix_note}
        Source ({from_lang}):
        ```
        {source_code}
        ```
    """).strip()

    return provider.complete(prompt, max_tokens=8096)


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
        - Constructs with no direct equivalent ({from_lang} → {to_lang}): goroutines, ownership, generics, async differences, etc.
        - Cross-file imports that may be broken — this file was translated in isolation with no knowledge of other files in the repo.
        - TODO comments added: {todo_count}
        - Auto-run: {"passed" if run_ok else "failed"}, attempts needed: {attempts}
        - Structural distance between {from_lang} and {to_lang}

        Be honest about real-world usability. A file that runs but has broken cross-file imports should score 40–60, not 90.

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

    try:
        raw = provider.complete(prompt, max_tokens=256).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data   = json.loads(raw.strip())
        score  = max(0, min(100, int(data["score"])))
        reason = str(data.get("reason", ""))[:200]
        return score, reason
    except Exception:
        return 50, "confidence scoring failed"


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
    input_toks  = (src_chars + manifest_chars) // _CHARS_PER_TOKEN + total_calls * _PROMPT_OVERHEAD_TOKS
    output_toks = (src_chars + manifest_chars) // _CHARS_PER_TOKEN

    if score_confidence:
        input_toks  += len(files) * _CONFIDENCE_INPUT_TOKS
        output_toks += len(files) * _CONFIDENCE_OUTPUT_TOKS

    if provider in ("ollama", "openai-compat"):
        # ollama is local/free; openai-compat pricing varies by service
        cost_usd: float | None = 0.0 if provider == "ollama" else None
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
) -> TranslationReport:
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

    # ── 2. Collect source files ────────────────────────────────────────────
    files = collect_files(repo_path, from_lang)
    if not files:
        if verbose:
            print(f"  No {from_lang} files found in {repo_path}")
        report.elapsed_seconds = time.time() - start
        return report

    test_files = [f for f in files if _is_test_file(f, from_lang)]
    src_files  = [f for f in files if not _is_test_file(f, from_lang)]

    if verbose:
        print(f"\n  Found {len(src_files)} source + {len(test_files)} test file(s) → {to_lang}\n")

    # ── 3. Translate each file ─────────────────────────────────────────────
    for i, src_file in enumerate(files, 1):
        rel     = src_file.relative_to(repo_path)
        dest    = _output_path(src_file, repo_path, output_path, to_lang)
        dest.parent.mkdir(parents=True, exist_ok=True)
        is_test = _is_test_file(src_file, from_lang)
        tag     = " [test]" if is_test else ""

        if verbose:
            print(f"  [{i}/{len(files)}] {rel}{tag}", end=" ", flush=True)

        source_code = src_file.read_text(encoding="utf-8", errors="replace")

        if not source_code.strip():
            dest.write_text("", encoding="utf-8")
            report.files.append(FileResult(path=str(rel), status="skipped"))
            if verbose:
                print("→ (empty, skipped)")
            continue

        error_ctx  = None
        final_code = ""
        attempts   = 0
        run_ok     = False

        for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
            attempts = attempt
            try:
                translated_code = _translate_once(
                    provider, source_code, from_lang, to_lang,
                    is_test=is_test, error_context=error_ctx,
                )
            except Exception as e:
                if verbose:
                    print(f"→ ✗ API error: {e}")
                report.files.append(FileResult(
                    path=str(rel), status="failed",
                    attempts=attempt, error=str(e),
                ))
                break

            ok, run_output = _try_run(to_lang, translated_code)
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
                ))
                if verbose:
                    extra = f" (fixed in {attempt} attempt(s))" if attempt > 1 else ""
                    conf  = f"  confidence {confidence}/100" if confidence is not None else ""
                    print(f"→ ✓{extra}{conf}")
                break
            else:
                error_ctx = run_output
                if verbose and attempt < MAX_FIX_ATTEMPTS:
                    print(f"\n    ↺ attempt {attempt} failed, retrying...", end=" ", flush=True)
        else:
            if final_code:
                warning = (
                    f"# WARNING: auto-run failed after {MAX_FIX_ATTEMPTS} attempts.\n"
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
                ))
                if verbose:
                    print(f"→ ✓ (saved with warnings after {MAX_FIX_ATTEMPTS} attempts)")

    # ── 4. Run translated test suite ──────────────────────────────────────
    if run_tests_after and test_files:
        passed, test_output = run_tests(output_path, to_lang, verbose=verbose)
        report.tests_passed = passed
        report.test_output  = test_output

    report.elapsed_seconds = time.time() - start
    return report
