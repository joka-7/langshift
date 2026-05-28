# langshift — Low Level Design

## Module Map

| Module | Lines | Key exports |
|---|---|---|
| `repo_translator/cli.py` | ~120 | `main()`, `build_parser()` |
| `repo_translator/agent.py` | ~500 | `translate_repo()`, `estimate_translation()`, `collect_files()`, `resolve_language()` |
| `repo_translator/manifest.py` | ~120 | `translate_manifest()`, `_find_manifests()` |
| `repo_translator/report.py` | ~180 | `TranslationReport`, `FileResult` |

---

## Data Structures

### `FileResult`  _(report.py)_

| Field | Type | Values |
|---|---|---|
| `path` | `str` | relative path from input root |
| `status` | `str` | `"ok"` · `"ok_with_warnings"` · `"failed"` · `"skipped"` |
| `attempts` | `int` | 1–3 (`MAX_FIX_ATTEMPTS`) |
| `error` | `str \| None` | last error message if failed or warned |
| `run_output` | `str \| None` | stdout/stderr from auto-run |

### `TranslationReport`  _(report.py)_

| Field | Type | Notes |
|---|---|---|
| `from_lang` | `str` | canonical language name |
| `to_lang` | `str` | canonical language name |
| `input_path` | `str` | absolute path to source repo |
| `output_path` | `str` | absolute path to output dir |
| `started_at` | `str` | ISO-8601 timestamp |
| `elapsed_seconds` | `float` | wall time for the full run |
| `files` | `list[FileResult]` | one entry per source file |
| `manifest_translated` | `list[str]` | paths of written manifest files |
| `tests_passed` | `bool \| None` | `None` = test phase not run |
| `test_output` | `str \| None` | combined stdout/stderr from test run |

Computed properties: `total`, `translated`, `failed`, `skipped`, `needed_retry`.

### `LANGUAGE_META`  _(agent.py)_

Dictionary keyed by canonical language name. Each entry:

```python
{
    "aliases":       list[str],   # short CLI names (e.g. ["ts"])
    "extensions":    list[str],   # source file extensions (e.g. [".ts", ".tsx"])
    "runner":        list[str] | None,  # command to run one file, or None
    "test_patterns": list[str],   # filename patterns identifying test files
    "test_runner":   list[str] | None,  # command to run the test suite, or None
}
```

---

## Key Functions

### `translate_repo()`  _(agent.py)_

```python
def translate_repo(
    repo_path: Path,
    output_path: Path,
    from_lang: str,
    to_lang: str,
    api_key: str | None = None,
    verbose: bool = True,
    translate_manifests: bool = True,
    run_tests_after: bool = False,
) -> TranslationReport
```

Main entry point. Steps:
1. Resolve language aliases via `resolve_language()`
2. Instantiate `anthropic.Anthropic` client
3. `translate_manifest()` — find and translate dependency files
4. `collect_files()` — rglob source extensions, skip `SKIP_DIRS`
5. Separate source files from test files via `_is_test_file()`
6. Per file: `_translate_once()` → `_try_run()` → retry loop (max `MAX_FIX_ATTEMPTS`)
7. Optionally `run_tests()` on the output directory
8. Return `TranslationReport`

### `estimate_translation()`  _(agent.py)_

```python
def estimate_translation(
    repo_path: Path,
    from_lang: str,
    to_lang: str,
    translate_manifests: bool = True,
) -> dict
```

No API calls. Scans files locally and returns:

```python
{
    "from_lang":      str,
    "to_lang":        str,
    "file_count":     int,
    "manifest_count": int,
    "input_tokens":   int,   # estimated
    "output_tokens":  int,   # estimated
    "estimated_cost": float, # USD
}
```

Token estimation formula:
```
input_tokens  = (source_chars + manifest_chars) / 4  +  file_count × 200
output_tokens = (source_chars + manifest_chars) / 4
cost_usd      = input_tokens/1e6 × $3.00  +  output_tokens/1e6 × $15.00
```

Pricing constants (`_INPUT_COST_PER_MTOK = 3.00`, `_OUTPUT_COST_PER_MTOK = 15.00`) reflect claude-sonnet-4 rates at time of writing.

### `_translate_once()`  _(agent.py)_

Single Claude API call for one file. Builds a prompt from:
- Language-pair test framework hint (`TEST_FRAMEWORK_MAP`)
- Error context from the previous failed attempt (if retrying)
- Source code

Returns raw translated code string. Raises on API errors (caught by caller).

### `_try_run()`  _(agent.py)_

Writes code to a `tempfile`, runs it with `LANGUAGE_META[to_lang]["runner"]`.
Returns `(success: bool, output: str)`.
Gracefully returns `(True, reason)` for languages without a runner, missing binaries, or timeouts.

### `collect_files()`  _(agent.py)_

```python
def collect_files(repo_path: Path, from_lang: str) -> list[Path]
```

`rglob("*")` over `repo_path`, skipping any path component in `SKIP_DIRS`.
Filters by `LANGUAGE_META[from_lang]["extensions"]`. Returns sorted list.

### `translate_manifest()`  _(manifest.py)_

Finds dependency files via `_find_manifests()`, sends each to Claude with a manifest-specific prompt, writes the result to `TARGET_MANIFEST[to_lang]` in the output directory. Returns a dict with `found`, `translated`, `skipped`.

---

## Retry / Fix Loop

```
for attempt in 1 .. MAX_FIX_ATTEMPTS:

    translated = _translate_once(source, error_context=last_error)

    ok, output = _try_run(translated)

    if ok:
        write file
        status = "ok"  (or "ok_with_warnings" if attempt > 1)
        break

    last_error = output   # fed back into the next prompt

else:  # all attempts exhausted, last run still failed
    write file with WARNING header comment
    status = "ok_with_warnings"
```

---

## Prompt Structure

```
You are an expert programmer. Translate the following {from_lang} code to {to_lang}.

Rules:
- Output ONLY the translated code, no markdown fences, no explanation.
- Preserve the original logic, structure, and comments (translated).
- Use idiomatic {to_lang} patterns and standard library where possible.
- Replace language-specific imports/packages with {to_lang} equivalents.
- If a direct equivalent doesn't exist, write a clear TODO comment.
[test note — if is_test file]
[error context — if retry attempt]

Source ({from_lang}):
```
{source_code}
```
```

---

## CLI Flags

| Flag | Short | Default | Effect |
|---|---|---|---|
| `--input` | `-i` | required | Source repo path |
| `--from` | `-f` | required | Source language |
| `--to` | `-t` | required | Target language |
| `--output` | `-o` | `<input>_<to_lang>` | Output directory |
| `--api-key` | — | `$ANTHROPIC_API_KEY` | Anthropic API key |
| `--estimate` | `-e` | off | Show cost estimate, confirm, then run |
| `--yes` | `-y` | off | Skip confirmation when using `--estimate` |
| `--run-tests` | — | off | Run translated test suite after translation |
| `--no-manifest` | — | off | Skip manifest translation |
| `--no-report` | — | off | Skip saving JSON/MD report |
| `--quiet` | `-q` | off | Suppress progress output |

---

## Testing

All 78 unit tests mock the Anthropic client — no real API calls needed.

| Test file | Tests | Coverage |
|---|---|---|
| `tests/test_agent.py` | 46 | `resolve_language`, `collect_files`, `_try_run`, `translate_repo`, `estimate_translation` |
| `tests/test_manifest.py` | 13 | `_find_manifests`, `translate_manifest` |
| `tests/test_report.py` | 19 | `FileResult`, `TranslationReport` properties, JSON/MD save |

Run with:
```bash
pytest
```

---

## Constants Reference

| Constant | Value | Location |
|---|---|---|
| `MODEL` | `claude-sonnet-4-20250514` | `agent.py` |
| `MAX_FIX_ATTEMPTS` | `3` | `agent.py` |
| `_INPUT_COST_PER_MTOK` | `3.00` (USD) | `agent.py` |
| `_OUTPUT_COST_PER_MTOK` | `15.00` (USD) | `agent.py` |
| `_CHARS_PER_TOKEN` | `4` | `agent.py` |
| `_PROMPT_OVERHEAD_TOKS` | `200` | `agent.py` |
| `SKIP_DIRS` | `node_modules`, `.git`, `dist`, etc. | `agent.py` |
