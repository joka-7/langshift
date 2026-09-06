# CLAUDE.md — repo-translator

This project was started in a Claude.ai chat session. You are continuing the work.

---

## What this project is

A CLI tool (plus an optional web UI) that translates an entire code repository from one
programming language to another using an LLM. It translates source files, test files, and
dependency manifests, auto-runs the output, and auto-fixes errors with up to 3 retries per file
(fewer for providers that opt into less, e.g. the offline provider retries 0 times since its
output is deterministic).

**Install and run:**
```bash
pip install -e ".[dev,webui]"
export ANTHROPIC_API_KEY=sk-ant-...
repo-translate --input ./my-ts-repo --from ts --to python
repo-translate --input ./my-repo --from ts --to python --run-tests  # also run translated tests
repo-translate --input ./my-ts-repo --from ts --to python --provider offline  # no API key needed
repo-translate --input ./my-repo --from ts --to python --no-run  # never execute translated code
repo-translate --input ./my-repo --from ts --to python --in-place  # write beside the sources
pytest         # full suite (unit + integration)
pytest -m "not integration"   # fast lane only — mocked providers, no subprocesses/threads
ruff check .   # lint
mypy repo_translator  # type check
```

Multiple LLM providers are supported beyond Claude — see `repo_translator/providers/`.

---

## File structure

```
repo_translator/
├── agent.py       — core logic: file collection, provider calls, auto-fix loop, test detection
├── manifest.py    — translates dependency files (package.json → requirements.txt etc.)
├── report.py      — TranslationReport dataclass, saves .json + .md summary
├── cli.py         — argparse CLI entry point
├── cpp_test_runner.py — runs translated C++ tests, via CMake+ctest when there's a
│                    CMakeLists.txt, else direct g++/clang++ compilation. Invoked as
│                    a subprocess: it is cpp's `test_runner` in LANGUAGE_META.
├── providers/     — one module per LLM backend (claude, openai, gemini, groq, ollama,
│                    openai_compat) plus base.py (LLMProvider ABC) and retry.py
│                    (rate-limit-aware backoff wrapper used by both agent.py and manifest.py).
│                    model_dispatcher_provider.py is an opt-in alternative backend
│                    (--backend model-dispatcher / $LANGSHIFT_BACKEND) routing
│                    claude/openai/gemini/groq through the shared ModelDispatcher gateway
│                    instead of this repo's own SDK calls + retry.py; requires the optional
│                    `model-dispatcher` extra (public on PyPI since v0.3.0 — a normal
│                    dependency, no git access or credentials needed) — see README.md
│                    § "Backend: native vs. model-dispatcher".
│                    external_chat.py builds the free-AI-chat deep links agent.py attaches to
│                    a FileResult once a file fails every retry — see README.md § "When a
│                    file's translation fails: a free external-AI fallback".
├── offline/       — rule-based, LLM-free transformers used by the `offline` provider:
│                    ts_to_py.py (ts/js → python, ~75% coverage) and cpp_to_py.py
│                    (cpp/c → python, ~70%); transformer.py is the pair registry
└── webui/         — FastAPI backend for the web UI (jobs.py: background job manager +
                      JSON history; main.py: API routes incl. SSE progress streaming)

frontend/          — React + Vite SPA for the web UI (talks to webui/ over /api)
frontend/e2e/      — Playwright end-to-end suite, drives the built SPA against a real backend

docs/
├── HLD.md         — high-level architecture / design docs
└── LLD.md         — low-level design docs (module map, data structures, route list, etc.)

tests/
├── conftest.py      — shared fixtures (ts_repo, ts_repo_with_tests)
├── helpers.py       — shared provider doubles (MockProvider, CapturingProvider)
├── test_agent.py    — unit tests, mocked providers
├── test_manifest.py — unit tests, mocked providers
├── test_offline.py  — unit tests for the offline ts→py transformer
├── test_cpp_offline.py — unit tests for the offline cpp/c→py transformer
├── test_cpp_test_runner.py — unit tests for cpp_test_runner.py (subprocess mocked)
├── test_providers.py— unit tests, one class per provider, SDKs mocked via sys.modules
├── test_model_dispatcher_provider.py — unit tests for the model-dispatcher backend
├── test_retry.py    — unit tests for the backoff/retry wrapper (time.sleep always mocked)
├── test_report.py   — unit tests
├── test_external_chat.py — unit tests for the free-AI-chat fallback URL builders
├── test_webui.py    — webui backend tests (FastAPI TestClient + offline provider)
│                       [pytest.mark.integration]
└── test_integration.py — real subprocess CLI runs, offline provider [pytest.mark.integration]

.github/workflows/
├── ci.yml           — lint (ruff+mypy), unit tests, integration tests, coverage gate,
│                       frontend lint/build, Playwright e2e
├── security.yml      — pip-audit, npm audit, bandit, eslint-security, gitleaks, CodeQL
└── translate.yml     — workflow_dispatch: run a translation via GitHub Actions, push to branch

pyproject.toml — package config, entry points: repo-translate = repo_translator.cli:main,
                  repo-translate-ui = repo_translator.webui.main:run (requires [webui] extra)
```

---

## Architecture

### translate_repo() — main flow (agent.py)
1. Resolve language aliases (ts → typescript, py → python, etc.)
2. `translate_manifest()` — find and translate package.json / go.mod / Cargo.toml etc.
3. `collect_files()` — rglob for source extensions, skip SKIP_DIRS
4. `_is_test_file()` — detect test files by pattern (*.test.ts, test_*.py, _test.go etc.)
5. Per file: `_translate_once()` → `_try_run()` → retry loop up to the provider's
   `max_fix_attempts` (default 3; the offline provider overrides this to 1, since its output
   is deterministic and retrying won't change it)
6. Optionally `run_tests()` — run pytest / go test / jest / cargo test etc. on the output
   directory. Gated on test files being found, *except* for languages like Rust whose test
   runner (`cargo test`) covers the whole project regardless of individual filenames
   (`test_patterns == []`) — for those, `--run-tests` always attempts a run.
7. Return `TranslationReport` with per-file results, including `tests_passed`/`test_output`
   when a test run happened

### Key design decisions
- **LLM backend:** pluggable via `LLMProvider` (`providers/base.py`). Claude is the default
  (`providers/claude.py`, models in `CLAUDE_MODELS`); OpenAI, Gemini, Groq, Ollama, and any
  OpenAI-compatible endpoint are also supported. An `offline` provider (`providers/offline.py`)
  wraps a pure rule-based transformer (`offline/`) for testing and CI without API calls.
  Separately, `--backend` (native/model-dispatcher) chooses *how* claude/openai/gemini/groq are
  called — native SDK calls (default) or the shared ModelDispatcher gateway.
- **Test files** get a special prompt note specifying the target test framework (e.g. jest → pytest). See `TEST_FRAMEWORK_MAP` in agent.py
- **Auto-fix loop:** if `_try_run()` fails, the error is passed back to `_translate_once()` as `error_context` for the next attempt
- **Rate limits:** `providers/retry.py` wraps every provider call (used by both `agent.py` and
  `manifest.py`) with exponential backoff, capped at `MAX_WAIT` (120s) before failing fast.
- **Manifests** are translated separately before source files, saved to the output root.
  `_find_manifests()` returns them in a deterministic order (by pattern, then alphabetically);
  when multiple source manifests would map to the same output filename, later ones are saved
  with a disambiguating suffix instead of overwriting the first.
- **No state between files** — each file is translated independently. Optionally
  (`--cross-file-context`, off by default), every prompt gets a read-only "repo map" of the
  other files' top-level symbols — see TODO #1 below — but there's still no state carried
  *between* file translations, just that one extra note per prompt.

### Supported languages (13 total)
typescript, javascript, python, java, go, rust, ruby, csharp, php, kotlin, swift, cpp, c

The `offline` provider covers only a subset as *source* languages — see
`offline/transformer.py`'s `_REGISTRY` for the authoritative pair list.

Each entry in `LANGUAGE_META` has: aliases, extensions, runner (for auto-run), test_patterns, test_runner.

---

## Current test status

Run with:
```bash
pytest                     # full suite
pytest -m "not integration"   # fast lane (mocked providers, no subprocesses/threads)
pytest --cov=repo_translator --cov-report=term-missing   # with coverage
```
CI enforces a coverage floor (`--cov-fail-under`, see `ci.yml`) and runs `ruff check` + `mypy`
as a separate lint job. Everything except `test_integration.py` and `test_webui.py` mocks the
LLM provider — no real API calls needed to run the unit suite. Those two integration-marked
files run real subprocesses / a real FastAPI TestClient against the `offline` provider, so they
still need no API key or network access.

---

## Known limitations / TODOs (good next tasks)

1. ~~**No cross-file context**~~ — **done, opt-in.** `--cross-file-context` (off by default, so
   existing prompt shapes/cost estimates are unchanged unless requested) has `translate_repo()`
   build a lightweight "repo map" — `_build_repo_map()` regex-extracts each source file's
   top-level symbol names (`_extract_symbols()`, one pattern per language in
   `_SYMBOL_PATTERNS`; unmapped languages just list filenames) — and appends a per-file,
   self-excluded slice of it (`_format_repo_map()`, capped at `_REPO_MAP_MAX_CHARS`) after the
   source block in every translation prompt. Still no state shared *between* file translations —
   each prompt just gets this one extra read-only note. It's a regex heuristic, not a real
   parser, so treat it as a hint, not a guarantee.

2. **Test runner for compiled languages** — Java, Kotlin and C# still have `test_runner`
   set to `None`, so `--run-tests` can't run their suites. C++ is done: `cpp_test_runner.py`
   compiles and runs them (CMake+ctest, else g++/clang++). Plain `c` is not — it shares the
   offline transformer with cpp but its `test_runner` is still `None`. None of these five
   has a single-file `runner` for the auto-fix loop either. (Rust's runner works; see the
   `--run-tests` gating note above.)

3. ~~**`--run-tests` runs the test suite but doesn't retry on failure**~~ — **done.** On a failed
   test run, `_run_tests_with_retry()` (`agent.py`) re-translates the failing test files with the
   runner output as `error_context` and re-runs, bounded by `provider.max_fix_attempts`. Emits a
   `tests_retry` progress event per attempt.

4. ~~**No `requirements.txt` → `package.json` validation**~~ — **done.** `manifest.py`'s
   `_validate_manifest()` checks `package.json`/`composer.json` (`json.loads`), `Cargo.toml`
   (`tomllib.loads`, guarded for Python 3.10 where `tomllib` doesn't exist), `requirements.txt`
   (line-shape regex), and `go.mod` (`module` directive). On failure it retries once with the
   parse error fed back as `error_context`, then reports `validation_failed`.

5. ~~**Large files**~~ — **done.** Files over `CHUNK_THRESHOLD_CHARS` (12,000 chars, a module
   constant in `agent.py`) are split at blank-line boundaries (`_split_into_chunks()`) —
   greedily packed so no chunk cuts a function/class body in half, with a single oversized
   block kept whole rather than split further. Each chunk gets its own translation call noting
   "chunk N of M"; the results are concatenated in order. Transparent to callers — the retry
   loop and test-suite retry both call `_translate_once()` once per attempt either way.
   `FileResult.chunks` records the count (`None` when not chunked) and shows up in the report.

6. **Monorepos with mixed languages** — `collect_files()` only handles one source language. Fix: detect language per directory.

   Related: `--in-place` (output == input) writes each translated file beside its
   source. `translate_repo()` detects that case itself (`in_place`) and refuses to
   overwrite any destination it didn't write, recording the refusal as
   `skipped_existing`. Provenance comes from `_recorded_source_paths()`, which
   counts only statuses that actually wrote a file — `failed` and
   `skipped_existing` record a path without writing one, and treating those as
   ours would let the next run clobber the file the previous run protected.

7. ~~**Progress persistence**~~ — **done.** `translate_repo()` writes `.translation_state.json`
   into the output directory after every file (`_save_checkpoint()`); on the next run, if its
   `input_path`/`from_lang`/`to_lang` match and the previously-recorded output file still exists,
   the file is skipped and the checkpointed result reused. Controlled by `--resume` /
   `--no-resume` on the CLI (default: resume) and a `resume` flag in the web UI (default: off,
   since the default output path isn't unique per job).

8. **GitHub Actions** — `.github/workflows/translate.yml` exists but is untested end-to-end. It assumes `repo-translate` is installable from the public GitHub URL.

---

## How to add a new language

In `agent.py`, add an entry to `LANGUAGE_META`:
```python
"mylang": {
    "aliases": ["ml"],           # short aliases for CLI
    "extensions": [".ml"],       # file extensions to collect
    "runner": ["mylang"],        # command to run a single file, or None
    "test_patterns": ["_test.ml"], # filename patterns that indicate test files
    "test_runner": ["mylang", "test"],  # command to run the test suite, or None
},
```
Optionally add entries to `TEST_FRAMEWORK_MAP` for test translation context.

If the language's test runner (like Rust's `cargo test`) tests the whole project rather than
individual files, leave `test_patterns` as `[]` — `translate_repo()` treats an empty
`test_patterns` list for the *target* language as "always attempt `--run-tests`", not "never".

---

## Environment

- Python 3.10+
- `anthropic>=0.50.0` (core dependency; other providers are optional extras — see
  `pyproject.toml`'s `[project.optional-dependencies]`)
- Dev deps: `pytest>=8.0`, `pytest-mock>=3.12`, `pytest-cov>=5.0`, `httpx>=0.27`, `ruff>=0.6`,
  `mypy>=1.10`
- `pip install -e ".[dev,webui]"` installs everything needed to run the full test suite

---

## Conventions

- Provider doubles (`MockProvider`, `CapturingProvider`) live in `tests/helpers.py`; shared
  fixtures (`ts_repo`, `ts_repo_with_tests`) live in `tests/conftest.py` — reuse them rather
  than redefining locally.
- `verbose=False` in test calls unless the test is specifically asserting on printed output
  (then use `capsys` and `verbose=True`).
- `translate_manifests=False` to isolate `translate_repo()` tests from manifest translation.
- Report is always returned from `translate_repo()`, never raised as exception — errors are captured per-file.
- Tests that raise a real rate-limit-shaped `Exception` (message containing "rate limit", "429",
  etc.) will hit the real exponential backoff in `providers/retry.py` unless
  `time.sleep` is mocked — always `patch("repo_translator.providers.retry.time.sleep")` in
  that case (see `test_retry.py` and the `test_api_error_is_handled_gracefully` test in
  `test_manifest.py` for the pattern). Forgetting this doesn't fail the test, it just makes it
  take up to ~60s for real.
- `tests/*` and `repo_translator/offline/ts_to_py.py` are exempted from ruff's line-length
  check (`E501`) — both legitimately contain long inline fixtures / dense pattern tables where
  wrapping would hurt readability more than it helps.
