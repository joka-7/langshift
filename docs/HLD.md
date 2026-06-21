# langshift — High Level Design

## Overview

langshift translates entire code repositories between programming languages. It supports
seven pluggable LLM backends (Claude, OpenAI, Gemini, Groq, Ollama, any OpenAI-compatible
endpoint, and a network-free rule-based "offline" translator), translates source files, test
files, and dependency manifests, auto-runs translated output, auto-fixes runtime errors with
up to 3 retries per file, and scores translation confidence. It ships as a CLI
(`repo-translate`) and as a local single-user web UI (FastAPI backend + React/Vite SPA).

---

## System Architecture

```
                         User
                          │
            ┌─────────────┴─────────────┐
            ▼                           ▼
 ┌─────────────────────┐     ┌─────────────────────────┐
 │   CLI  (cli.py)      │     │  Web UI                 │
 │  argparse · estimate │     │  React SPA (frontend/)  │
 └──────────┬───────────┘     │     │ /api (fetch + SSE)│
            │                 │     ▼                   │
            │                 │  FastAPI (webui/main.py)│
            │                 │  Job manager (webui/jobs.py) │
            │                 └──────────┬──────────────┘
            └─────────────┬───────────────┘
                          ▼
        ┌─────────────────────────────────────┐
        │      Translation Agent  (agent.py)   │
        │                                      │
        │  1. resolve language aliases         │
        │  2. translate manifests ─────────────┼──► Manifest Translator (manifest.py)
        │  3. collect source files             │
        │  4. per-file: translate              │
        │     → auto-run → auto-fix loop       │
        │     → score confidence (optional)    │
        │  5. run test suite  (optional)        │
        │  6. emit progress events (optional)  │
        └──────────────────┬───────────────────┘
                          │  provider.complete(prompt)
                          ▼
        ┌─────────────────────────────────────┐
        │     LLMProvider  (providers/)        │
        │  base.py — ABC: complete()           │
        │  retry.py — rate-limit backoff        │
        │                                      │
        │  claude · openai · gemini · groq ·   │
        │  ollama · openai_compat · offline    │
        └──────────────────┬───────────────────┘
                          │
            ┌─────────────┴─────────────┐
            ▼                           ▼
  Remote LLM API                offline/transformer.py
  (Anthropic/OpenAI/             rule-based, no network,
   Gemini/Groq/Ollama/           e.g. ts_to_py.py
   any OpenAI-compatible)
            │                           │
            └─────────────┬─────────────┘
                          ▼
        ┌─────────────────────────────────────┐
        │         Report  (report.py)          │
        │  TranslationReport dataclass         │
        │  → translation_report.json           │
        │  → translation_report.md             │
        └─────────────────────────────────────┘
```

---

## Data Flow

```
Input repo
    │
    ├─► [Manifest phase]   find package.json / go.mod / Cargo.toml etc.
    │                       → provider.complete() → write target manifest to output dir
    │
    ├─► [Collection phase] rglob source extensions, skip build/vendor dirs
    │                       → sorted list of Path objects
    │
    ├─► [Translation phase] per file:
    │       _translate_once() → provider.complete() (with backoff/retry on rate limits)
    │       _try_run()        → run with language runner
    │       if fail → retry with error context (max MAX_FIX_ATTEMPTS)
    │       score_confidence() → optional second provider call, 0-100 score
    │       on_progress() callback fired (webui streams this over SSE; CLI ignores it)
    │       write output file
    │
    ├─► [Test phase]  (optional --run-tests)
    │       run pytest / go test / jest in output dir
    │
    └─► [Report phase]
            TranslationReport.save() → .json + .md
```

### Web UI request/response flow

```
Browser ──POST /api/estimate──────────► FastAPI ──► agent.estimate_translation()  (no LLM call)
Browser ──POST /api/jobs──────────────► FastAPI ──► jobs.start_job() spawns a background thread
                                                      running agent.translate_repo(on_progress=...)
Browser ──GET  /api/jobs/{id}/stream──► FastAPI ──► Server-Sent Events, replays backlog then live-tails
Browser ──GET  /api/jobs/{id}/report──► FastAPI ──► reads translation_report.json from disk
Browser ──GET  /api/jobs/{id}/tree────► FastAPI ──► walks the output directory
Browser ──GET  /api/jobs/{id}/file────► FastAPI ──► returns translated + best-effort matching source
Browser ──GET  /api/jobs─────────────► FastAPI ──► JSON history file (DATA_DIR/webui_history.json)
```

---

## Components

| Component | Path | Responsibility |
|---|---|---|
| CLI | `repo_translator/cli.py` | Argument parsing, path validation, estimate display, confirmation prompt |
| Translation Agent | `repo_translator/agent.py` | File collection, provider calls, auto-fix retry loop, confidence scoring, cost estimation |
| Providers | `repo_translator/providers/` | `LLMProvider` ABC + 7 backends + rate-limit backoff (`retry.py`) |
| Offline translator | `repo_translator/offline/` | Rule-based, network-free transform registry (currently `ts/js → python`) |
| Manifest Translator | `repo_translator/manifest.py` | Dependency file detection and translation |
| Report | `repo_translator/report.py` | `TranslationReport` / `FileResult` dataclasses, JSON + Markdown output |
| Web UI backend | `repo_translator/webui/` | FastAPI routes (`main.py`) + background job manager & SSE (`jobs.py`) |
| Web UI frontend | `frontend/` | React + Vite SPA: form → estimate → live progress → report → output browser → history |

---

## Supported Languages

| Language | Aliases | Auto-run | Test runner |
|---|---|---|---|
| TypeScript | `ts` | — | jest |
| JavaScript | `js` | node | jest |
| Python | `py` | python3 | pytest |
| Java | — | — | — |
| Go | — | go run | go test |
| Rust | `rs` | — | cargo test |
| Ruby | `rb` | ruby | rspec |
| C# | `cs`, `c#` | — | — |
| PHP | — | php | phpunit |
| Kotlin | `kt` | — | — |
| Swift | — | — | swift test |
| C++ | `c++` | — | — |
| C | — | — | — |

## Supported Providers

| Provider | Network | Notes |
|---|---|---|
| `claude` (default) | yes | Anthropic API; model catalog in `providers/claude.py` |
| `openai` | yes | |
| `gemini` | yes | |
| `groq` | yes | |
| `ollama` | local daemon | No API key; freeform model name |
| `openai-compat` | yes | Any OpenAI-compatible endpoint; requires `--base-url` |
| `offline` | none | Rule-based transform, no LLM call; only `ts/js → python` today; `max_fix_attempts = 1` |

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| No cross-file context | Keeps each API call independent; avoids context-window limits on large repos |
| Auto-run + retry loop | Catches syntax/runtime errors early without human intervention |
| Pluggable `LLMProvider` ABC | One agent codepath works across 7 backends; new providers only implement `complete()` |
| Offline provider as a first-class provider | Lets the CLI/web UI/CI exercise the full pipeline with zero network/API-key dependency |
| Centralized retry/backoff (`providers/retry.py`) | Rate-limit handling lives in one place, not duplicated per provider |
| Report always returned | Errors are per-file; `translate_repo()` never raises exceptions |
| Cost estimation is local | `estimate_translation()` reads files only — no API calls needed |
| `on_progress` callback, not a return value | Lets the web UI stream live progress over SSE while the CLI simply ignores it |

---

## Testing Pyramid

| Layer | Location | What it exercises |
|---|---|---|
| Unit | `tests/test_agent.py`, `test_manifest.py`, `test_offline.py`, `test_providers.py`, `test_report.py`, `test_retry.py` | Pure logic, LLM client mocked/stubbed |
| Integration | `tests/test_integration.py` (`pytest -m integration`) | Real `repo-translate` CLI subprocess + real file I/O, offline provider |
| Integration (web) | `tests/test_webui.py` (`pytest -m integration`) | Real FastAPI `TestClient` + real background job threads, offline provider |
| E2E | `frontend/e2e/` (Playwright, `npm run test:e2e`) | Real browser driving the built SPA against a real FastAPI server end-to-end |

---

## Security Gate

CI runs a dedicated `security.yml` workflow on every PR (plus a weekly schedule):

| Tool | Target | Job |
|---|---|---|
| `pip-audit` | Python dependencies | `python-dependency-audit` |
| `npm audit` | JS dependencies (frontend) | `npm-dependency-audit` |
| `bandit` | Python SAST | `python-sast` |
| `eslint-plugin-security` | JS/TS SAST (via `npm run lint`) | `js-sast` |
| `gitleaks` | Secret scanning | `secret-scan` |
| CodeQL | Semantic analysis (Python + JS/TS) | `codeql` |

---

## External Dependencies

| Dependency | Purpose |
|---|---|
| `anthropic`, `openai`, `google-generativeai`, `groq`, `ollama` | Provider SDKs (optional extras) |
| `fastapi`, `uvicorn` | Web UI backend (`webui` extra) |
| `pytest`, `pytest-mock`, `httpx` | Project's own test suite (`dev` extra) |
| `react`, `react-dom`, `vite` | Web UI frontend SPA |
| `@playwright/test` | Frontend e2e test runner |
| Python stdlib | `pathlib`, `subprocess`, `tempfile`, `argparse`, `dataclasses`, `threading`, `queue` |
