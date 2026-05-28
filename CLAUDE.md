# CLAUDE.md — repo-translator

This project was started in a Claude.ai chat session. You are continuing the work.

---

## What this project is

A CLI tool that translates an entire code repository from one programming language to another using the Claude API. It translates source files, test files, and dependency manifests, auto-runs the output, and auto-fixes errors with up to 3 retries per file.

**Install and run:**
```bash
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...
repo-translate --input ./my-ts-repo --from ts --to python
repo-translate --input ./my-repo --from ts --to python --run-tests  # also run translated tests
pytest  # run the project's own unit tests
```

---

## File structure

```
repo_translator/
├── agent.py      — core logic: file collection, Claude API calls, auto-fix loop, test detection
├── manifest.py   — translates dependency files (package.json → requirements.txt etc.)
├── report.py     — TranslationReport dataclass, saves .json + .md summary
└── cli.py        — argparse CLI entry point

tests/
├── test_agent.py    — 39 unit tests (all Claude calls are mocked)
├── test_manifest.py — 13 unit tests
└── test_report.py   — 19 unit tests

.github/workflows/translate.yml — GitHub Actions: run translation via UI, push to branch
pyproject.toml — package config, entry point: repo-translate = repo_translator.cli:main
```

---

## Architecture

### translate_repo() — main flow (agent.py)
1. Resolve language aliases (ts → typescript, py → python, etc.)
2. `translate_manifest()` — find and translate package.json / go.mod / Cargo.toml etc.
3. `collect_files()` — rglob for source extensions, skip SKIP_DIRS
4. `_is_test_file()` — detect test files by pattern (*.test.ts, test_*.py, _test.go etc.)
5. Per file: `_translate_once()` → `_try_run()` → retry loop up to MAX_FIX_ATTEMPTS (3)
6. Optionally `run_tests()` — run pytest / go test / jest on the output directory
7. Return `TranslationReport` with per-file results

### Key design decisions
- **Claude model:** `claude-sonnet-4-20250514` hardcoded in `agent.py` (MODEL constant)
- **Test files** get a special prompt note specifying the target test framework (e.g. jest → pytest). See `TEST_FRAMEWORK_MAP` in agent.py
- **Auto-fix loop:** if `_try_run()` fails, the error is passed back to `_translate_once()` as `error_context` for the next attempt
- **Manifests** are translated separately before source files, saved to the output root
- **No state between files** — each file is translated independently; there's no cross-file context passed to Claude yet (see TODO below)

### Supported languages (13 total)
typescript, javascript, python, java, go, rust, ruby, csharp, php, kotlin, swift, cpp, c

Each entry in `LANGUAGE_META` has: aliases, extensions, runner (for auto-run), test_patterns, test_runner.

---

## Current test status

**71/71 tests passing.** Run with:
```bash
pytest
```
All tests mock the Anthropic client — no real API calls needed to run the test suite.

---

## Known limitations / TODOs (good next tasks)

1. **No cross-file context** — each file is translated in isolation. Claude doesn't know what other files exist or how they import each other. For large repos with complex interdependencies, this causes broken imports in the output. Fix: build a dependency graph first, pass relevant context per file.

2. **Test runner for compiled languages** — Java, Kotlin, C#, Rust, C++ don't have auto-run support yet. Their `test_runner` is `None`. Fix: add compile + run steps.

3. **`--test` flag runs test suite but doesn't retry on failure** — unlike source files which get 3 fix attempts, the test suite just runs once and reports. Fix: implement the same retry loop for tests.

4. **No `requirements.txt` → `package.json` validation** — manifest translation is Claude-only with no verification that the output is valid JSON / TOML / etc. Fix: add schema validation per target format.

5. **Large files** — files over ~4000 lines may hit the context window. Fix: add chunking logic, translate function-by-function for large files.

6. **Monorepos with mixed languages** — `collect_files()` only handles one source language. Fix: detect language per directory.

7. **Progress persistence** — if a run is interrupted mid-way, it starts from scratch. Fix: add a `.translation_state.json` checkpoint file.

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

---

## Environment

- Python 3.10+
- `anthropic>=0.50.0` (installed)
- Dev deps: `pytest>=8.0`, `pytest-mock>=3.12`
- `pip install -e ".[dev]"` installs everything

---

## Conventions

- All Claude mocking in tests uses `unittest.mock.MagicMock` — no pytest-mock fixtures yet
- `verbose=False` in all test calls to suppress output
- `translate_manifests=False` or mock `translate_manifest` in agent tests to isolate
- Report is always returned from `translate_repo()`, never raised as exception — errors are captured per-file
