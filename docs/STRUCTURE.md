# Repository structure

Every file in this repo and what is inside it. The tree below is **generated** —
run `python .ai/skills/repo_tree/gen_tree.py --project . --output docs/STRUCTURE.md`
to refresh it, and never edit between the markers by hand.

<!-- BEGIN GENERATED TREE (depth=all entries=all) -->
```text
langshift/
├── .github/
│   ├── workflows/
│   │   ├── ci.yml
│   │   ├── docs.yml
│   │   ├── security.yml
│   │   └── translate.yml
│   └── copilot-instructions.md            # Copilot's copy of AGENTS.md (generated)
├── .idea/
│   └── runConfigurations/
│       ├── Web_UI.xml
│       └── repo_translate_CLI.xml
├── docs/
│   ├── screenshots/
│   │   ├── cost-estimate.png
│   │   ├── history.png
│   │   ├── output-browser.png
│   │   ├── report.png
│   │   └── translate-form.png
│   ├── .structure-notes.toml
│   ├── HLD.md                             # Langshift — High Level Design
│   ├── LLD.md                             # Langshift — Low Level Design
│   └── STRUCTURE.md                       # Repository structure
├── frontend/                              # React + Vite + TypeScript web UI, talking to repo_translator/webui's API
│   ├── e2e/                               # Playwright end-to-end specs
│   │   ├── fixtures/
│   │   │   └── ts_repo/
│   │   │       └── main.ts
│   │   ├── screenshots.spec.ts
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
│   ├── offline/
│   │   ├── __init__.py
│   │   ├── transformer.py                 # Registry of offline rule-based code transformers.
│   │   └── ts_to_py.py                    # TypeScript → Python offline rule-based transformer.
│   ├── providers/                         # One LLM provider adapter per file, behind a shared base interface
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
├── tests/                                 # Pytest suite for repo_translator (agent, CLI, providers, offline mode)
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
├── .ai                                    # Ogen-ai submodule — the shared source of rules, skills and the ai-sync…
├── .gitignore
├── .gitmodules
├── AGENTS.md                              # The compiled coding rules every AI assistant reads — generated, do not…
├── CLAUDE.md                              # Claude Code's copy of AGENTS.md (generated)
├── GEMINI.md                              # Gemini CLI's copy of AGENTS.md (generated)
├── README.md                              # Langshift 🔄
├── ai-config.local.md                     # Project-specific rules appended verbatim to the generated AGENTS.md
├── ai-config.toml                         # Which rule fragments and target tools ai-sync compiles for this repo
├── pyproject.toml
└── uv.lock
```
<!-- END GENERATED TREE -->
