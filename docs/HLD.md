# langshift — High Level Design

## Overview

langshift is a CLI tool that translates entire code repositories between programming languages using the Claude AI API. It handles source files, test files, and dependency manifests, auto-runs translated output, and auto-fixes runtime errors with up to 3 retry attempts per file.

---

## System Architecture

```
User
  │
  ▼
┌─────────────────────────────────────┐
│            CLI  (cli.py)             │
│  argparse · validation · --estimate  │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│      Translation Agent  (agent.py)   │
│                                      │
│  1. resolve language aliases         │
│  2. translate manifests ─────────────┼──► Manifest Translator (manifest.py)
│  3. collect source files             │
│  4. per-file: translate              │
│     → auto-run → auto-fix loop       │
│  5. run test suite  (optional)       │
└──────────────────┬──────────────────┘
                   │  Claude API calls
                   ▼
┌─────────────────────────────────────┐
│       Anthropic Claude API           │
│    model: claude-sonnet-4            │
└──────────────────┬──────────────────┘
                   │
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
    │                       → Claude → write target manifest to output dir
    │
    ├─► [Collection phase] rglob source extensions, skip build/vendor dirs
    │                       → sorted list of Path objects
    │
    ├─► [Translation phase] per file:
    │       _translate_once() → Claude → translated code
    │       _try_run()        → run with language runner
    │       if fail → retry with error context (max 3 attempts)
    │       write output file
    │
    ├─► [Test phase]  (optional --run-tests)
    │       run pytest / go test / jest in output dir
    │
    └─► [Report phase]
            TranslationReport.save() → .json + .md
```

---

## Components

| Component | File | Responsibility |
|---|---|---|
| CLI | `cli.py` | Argument parsing, path validation, estimate display, confirmation prompt |
| Translation Agent | `agent.py` | File collection, Claude API calls, auto-fix retry loop, cost estimation |
| Manifest Translator | `manifest.py` | Dependency file detection and translation |
| Report | `report.py` | `TranslationReport` / `FileResult` dataclasses, JSON + Markdown output |

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

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| No cross-file context | Keeps each API call independent; avoids context-window limits on large repos |
| Auto-run + retry loop | Catches syntax/runtime errors early without human intervention |
| Model hardcoded (`claude-sonnet-4`) | Consistent cost/quality tradeoff; single place to update |
| Report always returned | Errors are per-file; `translate_repo()` never raises exceptions |
| Cost estimation is local | `estimate_translation()` reads files only — no API calls needed |

---

## External Dependencies

| Dependency | Purpose |
|---|---|
| `anthropic` | Claude API client |
| `pytest` | Test runner for the project's own unit tests |
| Python stdlib | `pathlib`, `subprocess`, `tempfile`, `argparse`, `dataclasses` |
