# Repository structure

Every file in this repo and what is inside it. The tree below is **generated** —
run `python <ogen-ai>/skills/repo_tree/gen_tree.py --project . --output docs/STRUCTURE.md`
to refresh it, and never edit between the markers by hand.

<!-- BEGIN GENERATED TREE (depth=all entries=all) -->
```text
langshift/
├── .github/
│   └── workflows/
│       ├── ci.yml
│       ├── security.yml
│       └── translate.yml
├── .idea/
│   └── runConfigurations/
│       ├── Web_UI.xml
│       └── repo_translate_CLI.xml
├── docs/
│   ├── .structure-notes.toml
│   ├── HLD.md                             # Langshift — High Level Design
│   ├── LLD.md                             # Langshift — Low Level Design
│   └── STRUCTURE.md                       # Repository structure
├── frontend/                              # React + Vite + TypeScript web UI, talking to repo_translator/webui's API
│   ├── e2e/                               # Playwright end-to-end specs
│   │   ├── fixtures/
│   │   │   └── ts_repo/
│   │   │       └── main.ts
│   │   └── translate.spec.ts
│   ├── public/
│   │   └── favicon.svg
│   ├── src/
│   │   ├── components/
│   │   │   ├── EstimatePanel.test.tsx
│   │   │   ├── EstimatePanel.tsx          # Pre-translation cost/token estimate
│   │   │   ├── HistoryView.test.tsx
│   │   │   ├── HistoryView.tsx            # Past translation runs
│   │   │   ├── OutputBrowser.test.tsx
│   │   │   ├── OutputBrowser.tsx          # Browse the translated output tree
│   │   │   ├── ProgressView.test.tsx
│   │   │   ├── ProgressView.tsx           # Live progress of an in-flight translation
│   │   │   ├── ReportView.test.tsx
│   │   │   ├── ReportView.tsx             # Renders the Markdown/JSON translation report
│   │   │   ├── TranslateForm.test.tsx
│   │   │   └── TranslateForm.tsx          # Job submission form: source repo, target language, provider
│   │   ├── test/
│   │   │   └── setup.ts
│   │   ├── App.css
│   │   ├── App.tsx
│   │   ├── api.test.ts
│   │   ├── api.ts
│   │   ├── index.css
│   │   ├── main.tsx
│   │   └── types.ts
│   ├── .gitignore
│   ├── README.md                          # React + TypeScript + Vite
│   ├── eslint.config.js
│   ├── index.html
│   ├── package-lock.json
│   ├── package.json
│   ├── playwright.config.ts
│   ├── tsconfig.app.json
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   └── vite.config.ts
├── repo_translator/                       # The CLI/agent package — providers, offline mode, report generation
│   ├── __pycache__/
│   │   ├── __init__.cpython-312.pyc
│   │   ├── agent.cpython-312.pyc
│   │   ├── cli.cpython-312.pyc
│   │   ├── manifest.cpython-312.pyc
│   │   └── report.cpython-312.pyc
│   ├── offline/
│   │   ├── __pycache__/
│   │   │   ├── __init__.cpython-312.pyc
│   │   │   ├── transformer.cpython-312.pyc
│   │   │   └── ts_to_py.cpython-312.pyc
│   │   ├── __init__.py
│   │   ├── transformer.py                 # Registry of offline rule-based code transformers.
│   │   └── ts_to_py.py                    # TypeScript → Python offline rule-based transformer.
│   ├── providers/                         # One LLM provider adapter per file, behind a shared base interface
│   │   ├── __pycache__/
│   │   │   ├── __init__.cpython-312.pyc
│   │   │   ├── base.cpython-312.pyc
│   │   │   ├── claude.cpython-312.pyc
│   │   │   ├── gemini.cpython-312.pyc
│   │   │   ├── groq.cpython-312.pyc
│   │   │   ├── ollama.cpython-312.pyc
│   │   │   ├── openai.cpython-312.pyc
│   │   │   ├── openai_compat.cpython-312.pyc
│   │   │   └── retry.cpython-312.pyc
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── claude.py
│   │   ├── external_chat.py               # The "nothing left to retry" escape hatch.
│   │   ├── gemini.py
│   │   ├── groq.py
│   │   ├── model_dispatcher_provider.py   # Adapter that backs an :class:`LLMProvider` with ModelDispatcher's…
│   │   ├── offline.py                     # Rule-based code translation with no API calls.
│   │   ├── ollama.py
│   │   ├── openai.py
│   │   ├── openai_compat.py
│   │   └── retry.py                       # Rate-limit-aware wrapper around LLMProvider.complete().
│   ├── webui/                             # FastAPI backend for the web UI (run via `repo-translate-ui`)
│   │   ├── __init__.py
│   │   ├── jobs.py                        # Background job manager for the web UI.
│   │   └── main.py                        # FastAPI backend for the repo-translator web UI.
│   ├── __init__.py
│   ├── agent.py                           # Reads files, translates via LLM provider, runs & fixes.
│   ├── cli.py                             # Repo-translator CLI
│   ├── manifest.py                        # Converts package.json / go.mod / Gemfile etc.
│   └── report.py                          # Pretty-prints and saves a JSON + Markdown report.
├── repo_translator.egg-info/              # Build metadata (setuptools) — not hand-maintained
│   ├── PKG-INFO
│   ├── SOURCES.txt
│   ├── dependency_links.txt
│   ├── entry_points.txt
│   ├── requires.txt
│   └── top_level.txt
├── tests/                                 # Pytest suite for repo_translator (agent, CLI, providers, offline mode)
│   ├── __pycache__/
│   │   ├── test_agent.cpython-312-pytest-9.0.3.pyc
│   │   ├── test_manifest.cpython-312-pytest-9.0.3.pyc
│   │   ├── test_providers.cpython-312-pytest-9.0.3.pyc
│   │   ├── test_report.cpython-312-pytest-9.0.3.pyc
│   │   └── test_retry.cpython-312-pytest-9.0.3.pyc
│   ├── conftest.py                        # Shared fixtures.
│   ├── helpers.py                         # Shared provider doubles.
│   ├── test_agent.py                      # Tests for repo_translator.agent
│   ├── test_cli.py                        # Unit tests for repo_translator.cli.
│   ├── test_external_chat.py              # Tests for repo_translator.providers.external_chat
│   ├── test_integration.py                # Integration tests for the CLI entry point (repo_translator.cli).
│   ├── test_manifest.py                   # Tests for repo_translator.manifest
│   ├── test_model_dispatcher_provider.py  # Tests for repo_translator.providers.model_dispatcher_provider.
│   ├── test_offline.py                    # Tests for offline rule-based transformer (ts_to_py) and OfflineProvider.
│   ├── test_providers.py                  # Tests for repo_translator.providers
│   ├── test_report.py                     # Tests for repo_translator.report
│   ├── test_retry.py                      # Rate-limit detection, retry-after parsing, backoff logic.
│   └── test_webui.py                      # Tests for repo_translator.webui (jobs.py + main.py).
├── .gitignore
├── CLAUDE.md                              # CLAUDE.md — repo-translator
├── README.md                              # Langshift 🔄
├── pyproject.toml
└── uv.lock
```
<!-- END GENERATED TREE -->
