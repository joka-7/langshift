# langshift — Low Level Design

## Module Map

| Module | Lines | Key exports |
|---|---|---|
| `repo_translator/cli.py` | ~185 | `main()`, `build_parser()` |
| `repo_translator/agent.py` | ~680 | `translate_repo()`, `estimate_translation()`, `collect_files()`, `resolve_language()`, `LANGUAGE_META`, `PRICING` |
| `repo_translator/manifest.py` | ~110 | `translate_manifest()`, `_find_manifests()` |
| `repo_translator/report.py` | ~215 | `TranslationReport`, `FileResult` |
| `repo_translator/providers/__init__.py` | ~60 | `SUPPORTED_PROVIDERS`, `make_provider()`, `make_offline_provider()` |
| `repo_translator/providers/base.py` | ~10 | `LLMProvider` (ABC) |
| `repo_translator/providers/retry.py` | ~105 | `complete_with_backoff()`, `is_rate_limit_error()`, `parse_retry_after()` |
| `repo_translator/providers/claude.py` | ~25 | `ClaudeProvider`, `CLAUDE_MODELS` |
| `repo_translator/providers/openai.py` | ~20 | `OpenAIProvider` |
| `repo_translator/providers/gemini.py` | ~20 | `GeminiProvider` |
| `repo_translator/providers/groq.py` | ~20 | `GroqProvider` |
| `repo_translator/providers/ollama.py` | ~20 | `OllamaProvider` |
| `repo_translator/providers/openai_compat.py` | ~35 | `OpenAICompatProvider` |
| `repo_translator/providers/offline.py` | ~75 | `OfflineProvider` |
| `repo_translator/offline/transformer.py` | ~35 | `OfflineTransformer` |
| `repo_translator/offline/ts_to_py.py` | ~850 | `transform()` (rule-based TS/JS → Python) |
| `repo_translator/webui/jobs.py` | ~265 | `Job`, `start_job()`, `estimate()`, `list_jobs()`, `get_job()`, `supported_languages()` |
| `repo_translator/webui/main.py` | ~290 | FastAPI `app`, routes, `run()` |

---

## Data Structures

### `FileResult`  _(report.py)_

| Field | Type | Values |
|---|---|---|
| `path` | `str` | relative path from input root |
| `status` | `str` | `"ok"` · `"ok_with_warnings"` · `"failed"` · `"skipped"` |
| `attempts` | `int` | 1–`MAX_FIX_ATTEMPTS` |
| `error` | `str \| None` | last error message if failed or warned |
| `run_output` | `str \| None` | stdout/stderr from auto-run |
| `confidence` | `int \| None` | 0–100 score from `score_confidence()`, `None` if disabled |
| `confidence_reason` | `str \| None` | one-line rationale from the confidence-scoring prompt |

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

Computed properties: `total`, `translated`, `failed`, `skipped`, `needed_retry`,
`high_confidence` (confidence ≥ `_CONFIDENCE_THRESHOLD` = 70), `needs_review` (below it).

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

## `providers/` — `LLMProvider` abstraction

### `LLMProvider`  _(base.py)_

```python
class LLMProvider(ABC):
    max_fix_attempts: int = 3

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 8096) -> str: ...
```

Every backend (`claude.py`, `openai.py`, `gemini.py`, `groq.py`, `ollama.py`,
`openai_compat.py`, `offline.py`) implements `complete()` only; `OfflineProvider` overrides
`max_fix_attempts = 1` since retrying a deterministic rule-based transform is pointless.

### Factories  _(providers/__init__.py)_

```python
SUPPORTED_PROVIDERS = ("claude", "openai", "gemini", "ollama", "groq", "openai-compat", "offline")

def make_provider(provider: str, model: str, api_key: str | None, base_url: str | None) -> LLMProvider
def make_offline_provider(from_lang: str, to_lang: str) -> OfflineProvider
```

`make_provider()` dispatches by name; `openai-compat` raises `ValueError` if `base_url` is
missing. `make_offline_provider()` validates the language pair against
`OfflineTransformer.supports()` before constructing the provider, so unsupported pairs fail
fast with a clear message instead of failing inside the translation loop.

### Retry / backoff  _(providers/retry.py)_

```python
def complete_with_backoff(provider: LLMProvider, prompt: str, max_tokens: int = 8096) -> str
def is_rate_limit_error(exc: Exception) -> bool
def parse_retry_after(message: str) -> float | None
```

Wraps `provider.complete()`. On a rate-limit error (429 / "rate limit" / "too many requests" in
the message, but never on 413 "too large"), parses a provider-supplied "try again in Xh Ym Z.Zs"
hint if present, otherwise applies exponential backoff (`_BASE_WAIT=1`, doubling, capped at
`MAX_WAIT=120`s), up to `MAX_RETRIES=6` attempts. If the required wait exceeds `MAX_WAIT`
(daily-quota exhaustion) or the error isn't rate-limit-related, re-raises immediately.

### Offline translator  _(providers/offline.py`, `offline/`)_

`OfflineProvider.complete()`:
- For confidence-scoring prompts, returns a fixed low-confidence JSON payload (`{"score": 55, "reason": "..."}`) — it can't meaningfully judge its own rule-based output.
- For translation prompts, extracts the fenced ```` ```...``` ```` code block from the prompt via regex (`_extract_code`) and runs it through `OfflineTransformer.transform()`.
- Raises `ValueError` if the prompt has no fenced code block — this is why **manifest translation cannot use the offline provider**: manifest prompts aren't fenced.

`OfflineTransformer` (`offline/transformer.py`) is a small registry mapping
`"<from>:<to>"` → transform function, currently `{"typescript:python", "javascript:python"}` →
`ts_to_py.transform`. `supports(from_lang, to_lang)` and `supported_pairs()` let callers
(CLI, web UI form, `make_offline_provider`) check support before attempting a run.

---

## `webui/` — FastAPI backend

### `Job`  _(jobs.py)_

In-memory dataclass tracking one translation run: `id`, `status`
(`"running"`/`"finished"`/`"error"`), `events: queue.Queue` (for SSE), `history: list[dict]`
(replay buffer for clients that connect mid-run), `lock: threading.Lock`, plus the original
request params and the resulting `TranslationReport` once done. `JOBS: dict[str, Job]` is the
process-wide in-memory job table — state is lost on server restart (by design, per `DATA_DIR`
persisting only the *history* of completed jobs, not in-flight state).

### Key functions  _(jobs.py)_

```python
def estimate(input_path, from_lang, to_lang, provider, model, translate_manifests, score_confidence, base_url) -> dict
def start_job(input_path, from_lang, to_lang, provider_name, model, output_path, api_key, base_url, run_tests, translate_manifests, score_confidence) -> Job
def supported_languages() -> list[dict]
```

`start_job()` builds the provider via `_build_provider()`, creates a `Job`, and spawns a daemon
`threading.Thread` running `_run_job()`, which calls `agent.translate_repo(..., on_progress=...)`.
`on_progress` pushes each event onto `job.events` (consumed by the SSE endpoint) and appends to
`job.history` (so a client reconnecting mid-run sees everything emitted so far). On completion,
`_append_history()` persists a summary row to `HISTORY_FILE` for the History tab.

`DATA_DIR = Path(os.environ.get("REPO_TRANSLATOR_DATA_DIR", Path.home() / ".repo_translator"))` —
overridable via env var, which is how both `tests/test_webui.py` and the Playwright e2e suite
isolate their job history from the real one.

### Routes  _(main.py)_

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/languages` | `LANGUAGE_META` summary for the form's language dropdowns |
| GET | `/api/providers` | Provider list + curated model catalogs + offline-supported pairs |
| POST | `/api/estimate` | `agent.estimate_translation()` — no LLM call |
| POST | `/api/jobs` | Start a background translation job |
| GET | `/api/jobs` | List job history |
| GET | `/api/jobs/{id}` | Single job status |
| GET | `/api/jobs/{id}/stream` | SSE: replays backlog, then live-tails until `finished`/`error` |
| GET | `/api/jobs/{id}/report` | Reads `translation_report.json` from the output dir |
| GET | `/api/jobs/{id}/tree` | Walks the output directory into a `TreeNode` JSON tree |
| GET | `/api/jobs/{id}/file?path=` | Returns translated content + best-effort matching source |

`_safe_join()` rejects absolute paths and `../` traversal before resolving `path` under the
job's output root — the one place this module accepts user-controlled path input.

If `frontend/dist/` exists (i.e. the SPA has been built), `main.py` mounts it at `/`, so a single
`uvicorn` process can serve both the API and the production frontend.

---

## Key Functions  _(agent.py)_

### `translate_repo()`

```python
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
    on_progress: Callable[[dict], None] | None = None,
) -> TranslationReport
```

Main entry point, used identically by the CLI and the web UI (the CLI just omits
`on_progress`). Steps:
1. Resolve language aliases via `resolve_language()`
2. `translate_manifest()` — find and translate dependency files (skipped entirely for the
   offline provider's manifest prompts, since they aren't fenced code blocks)
3. `collect_files()` — rglob source extensions, skip `SKIP_DIRS`
4. Separate source files from test files via `_is_test_file()`
5. Per file: `_translate_once()` → `_try_run()` → retry loop (max `provider.max_fix_attempts`,
   default `MAX_FIX_ATTEMPTS` = 3) → optional `score_confidence()` → `_emit()` progress events
6. Optionally `run_tests()` on the output directory
7. Return `TranslationReport`

### `estimate_translation()`

```python
def estimate_translation(
    repo_path: Path,
    from_lang: str,
    to_lang: str,
    translate_manifests: bool = True,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    score_confidence: bool = True,
) -> dict
```

No API calls — scans files locally. Returns `from_lang`, `to_lang`, `provider`, `model`,
`file_count`, `manifest_count`, `input_tokens`, `output_tokens`, `estimated_cost` (`float`,
`0.0` for `ollama`/`offline`, `None` if pricing for `(provider, model)` is unknown).

Token estimation:
```
input_tokens  = (source_chars + manifest_chars) / 4 + total_calls × _PROMPT_OVERHEAD_TOKS
              + [if score_confidence] file_count × _CONFIDENCE_INPUT_TOKS
output_tokens = (source_chars + manifest_chars) / 4
              + [if score_confidence] file_count × _CONFIDENCE_OUTPUT_TOKS
cost_usd      = input_tokens/1e6 × inp_price + output_tokens/1e6 × out_price   (from PRICING[(provider, model)])
```

### `_translate_once()`

Single provider call (via `complete_with_backoff()`) for one file. Builds a prompt from:
- Language-pair test framework hint (`TEST_FRAMEWORK_MAP`)
- Error context from the previous failed attempt (if retrying)
- Source code

Returns raw translated code string. Raises on provider errors (caught by caller).

### `_try_run()`

Writes code to a `tempfile`, runs it with `LANGUAGE_META[to_lang]["runner"]`.
Returns `(success: bool, output: str)`. Gracefully returns `(True, reason)` for languages
without a runner, missing binaries, or timeouts.

### `collect_files()`

```python
def collect_files(repo_path: Path, from_lang: str) -> list[Path]
```

`rglob("*")` over `repo_path`, skipping any path component in `SKIP_DIRS`.
Filters by `LANGUAGE_META[from_lang]["extensions"]`. Returns sorted list.

### `translate_manifest()`  _(manifest.py)_

Finds dependency files via `_find_manifests()`, sends each to the provider with a
manifest-specific prompt, writes the result to `TARGET_MANIFEST[to_lang]` in the output
directory. Returns a dict with `found`, `translated`, `skipped`.

---

## Retry / Fix Loop

```
for attempt in 1 .. provider.max_fix_attempts:

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
| `--provider` | `-p` | `claude` | One of `SUPPORTED_PROVIDERS` |
| `--model` | `-m` | provider default | Model id/name |
| `--base-url` | — | none | Required for `--provider openai-compat` |
| `--api-key` | — | `$ANTHROPIC_API_KEY` (or provider-specific env var) | API key |
| `--estimate` | `-e` | off | Show cost estimate, confirm, then run |
| `--yes` | `-y` | off | Skip confirmation when using `--estimate` |
| `--run-tests` | — | off | Run translated test suite after translation |
| `--no-manifest` | — | off | Skip manifest translation |
| `--no-confidence` | — | off | Skip confidence scoring |
| `--no-report` | — | off | Skip saving JSON/MD report |
| `--quiet` | `-q` | off | Suppress progress output |

---

## Testing

**235 Python tests** (`pytest`), plus a **2-test Playwright e2e suite** (`npm run test:e2e`
in `frontend/`). All Python tests run offline — either mocking the LLM client directly, or
using the `offline` provider for real (un-mocked) integration coverage.

| Test file | Tests | Coverage |
|---|---|---|
| `tests/test_agent.py` | 68 | `resolve_language`, `collect_files`, `_try_run`, `translate_repo`, `estimate_translation`, confidence scoring, progress events |
| `tests/test_manifest.py` | 13 | `_find_manifests`, `translate_manifest` |
| `tests/test_offline.py` | 75 | `OfflineProvider`, `OfflineTransformer`, `ts_to_py.transform()` rules |
| `tests/test_providers.py` | 18 | `make_provider()`/`make_offline_provider()` factories, each provider's `complete()` |
| `tests/test_report.py` | 19 | `FileResult`, `TranslationReport` properties, JSON/MD save |
| `tests/test_retry.py` | 25 | `complete_with_backoff()`, `is_rate_limit_error()`, `parse_retry_after()` |
| `tests/test_webui.py` (`pytest.mark.integration`) | 11 | Real FastAPI `TestClient` + real background job threads, offline provider |
| `tests/test_integration.py` (`pytest.mark.integration`) | 6 | Real `repo-translate` CLI subprocess, real file I/O, offline provider |
| `frontend/e2e/translate.spec.ts` (Playwright) | 2 | Full browser flow: form → estimate → start job → SSE progress → report → output browser → history → reopen |

Run the Python suite with `pytest` (everything) or `pytest -m integration` /
`pytest -m "not integration"` to select a tier. Run the e2e suite with
`npm run test:e2e` from `frontend/` (starts the FastAPI backend and the Vite dev server
automatically via `playwright.config.ts`'s `webServer` config; requires
`npx playwright install --with-deps chromium` once per machine/CI runner).

---

## Constants Reference

| Constant | Value | Location |
|---|---|---|
| `DEFAULT_PROVIDER` | `"claude"` | `agent.py` |
| `DEFAULT_MODEL` | `"sonnet"` | `agent.py` |
| `MAX_FIX_ATTEMPTS` | `3` | `agent.py` (overridable per-provider via `LLMProvider.max_fix_attempts`) |
| `_CONFIDENCE_THRESHOLD` | `70` | `report.py` — boundary between `high_confidence` and `needs_review` |
| `_CHARS_PER_TOKEN` | `4` | `agent.py` |
| `_PROMPT_OVERHEAD_TOKS` | `200` | `agent.py` |
| `PRICING` | `{(provider, model): (input_$/Mtok, output_$/Mtok)}` | `agent.py` |
| `SKIP_DIRS` | `node_modules`, `.git`, `dist`, etc. | `agent.py` |
| `MAX_RETRIES` | `6` | `providers/retry.py` |
| `_BASE_WAIT` / `MAX_WAIT` | `1` / `120` (seconds) | `providers/retry.py` |
| `_CONFIDENCE_THRESHOLD` (offline) | n/a — offline always returns score `55` | `providers/offline.py` |
