# langshift 🔄

Translate an entire code repository from one programming language to another using Claude AI.

The agent:
1. **Translates** dependency manifests (`package.json` → `requirements.txt` etc.)
2. **Translates** every source file via Claude
3. **Runs** the translated code automatically (where supported)
4. **Auto-fixes** runtime errors — up to 3 attempts per file
5. **Saves** a detailed Markdown + JSON report

---

## Supported languages

| Language | Aliases | Auto-run |
|---|---|---|
| TypeScript | `ts` | ✗ |
| JavaScript | `js` | ✓ (node) |
| Python | `py` | ✓ (python3) |
| Java | — | ✗ |
| Go | — | ✓ (go run) |
| Rust | `rs` | ✗ |
| Ruby | `rb` | ✓ (ruby) |
| C# | `cs`, `c#` | ✗ |
| PHP | — | ✓ (php) |
| Kotlin | `kt` | ✗ |
| Swift | — | ✗ |
| C++ | `c++` | ✗ |
| C | — | ✗ |

---

## Quickstart (new user, 2 minutes)

```bash
git clone https://github.com/joka-7/langshift
cd langshift
pip install -e .

# Try it immediately with no API key — uses the free rule-based offline provider
mkdir demo && echo 'function add(a, b) { return a + b; } console.log(add(2, 3));' > demo/main.ts
repo-translate --input ./demo --from ts --to python --provider offline
cat demo_python/main.py    # → def add(a, b): return a + b
python demo_python/main.py # → 5
```

That's the whole loop. Once you're ready to use a real LLM for higher-quality output, pick a provider below and set its API key.

---

## Providers

| Provider | Flag value | Needs API key | Models |
|---|---|---|---|
| Claude (Anthropic) | `claude` (default) | `ANTHROPIC_API_KEY` | `haiku`, `sonnet`, `opus` |
| OpenAI | `openai` | `OPENAI_API_KEY` | `gpt-4-turbo`, `gpt-4o`, `gpt-4o-mini` |
| Gemini | `gemini` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | `gemini-1.5-flash`, `gemini-1.5-pro`, `gemini-2.0-flash` |
| Groq | `groq` | `GROQ_API_KEY` | any Groq-hosted model id, free tier |
| Ollama | `ollama` | none (local) | any locally pulled model |
| OpenAI-compatible | `openai-compat` | varies | any model, requires `--base-url` |
| Offline (rule-based) | `offline` | none | n/a — free, no network calls |

> **Offline provider scope:** only translates `typescript`/`javascript` → `python` source files.
> It does not translate dependency manifests (`package.json`, etc.) — use a real LLM provider
> for those, or pass `--no-manifest`.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
repo-translate --input ./my-repo --from ts --to python --provider claude --model sonnet
```

Install the SDK for the provider(s) you use: `pip install -e ".[all-providers]"` pulls in all of them, or `pip install -e ".[webui]"` for just the web UI's deps.

### Backend: native vs. model-dispatcher

By default (`--backend native`), `claude`/`openai`/`gemini`/`groq` call the vendor SDK directly
and this repo's own `repo_translator/providers/retry.py` handles rate-limit backoff — nothing
here changes unless you opt in.

`--backend model-dispatcher` (or `$LANGSHIFT_BACKEND=model-dispatcher`) routes those same four
providers through [ModelDispatcher](https://github.com/joka-7/ModelDispatcher) instead — the
shared gateway this project's other apps (AppMyTrip, HighFive, JobFlowTracker, KanDOne,
StepByLearn) are standardising their own LLM calls on, so retry/backoff behaviour stays
identical across all of them instead of drifting in separate implementations. `ollama`,
`openai-compat`, and `offline` always run natively — ModelDispatcher has no equivalent for
any of them yet.

```bash
pip install -e ".[model-dispatcher]"   # requires Python >=3.11 — see note below
repo-translate --input ./my-repo --from ts --to python --provider claude --backend model-dispatcher
```

> **Requires Python ≥3.11.** ModelDispatcher itself needs it, which is newer than this repo's
> own minimum (3.10) — though it's the same version this repo's own CI already runs on.
> `pip install ".[model-dispatcher]"` fails clearly with a Python-version error if you're on
> 3.10 — everything else in this repo still works fine either way.
>
> **Requires git access to ModelDispatcher.** It's currently a private repo, so the extra's
> `git+https://` URL only resolves for someone with access — `pip install` will prompt for
> credentials or use your normal git credential helper.

---

## Installation

```bash
# Clone
git clone https://github.com/joka-7/langshift
cd langshift

# Install (core CLI only)
pip install -e .

# ...or with everything needed for development, the web UI, and all providers
pip install -e ".[dev,webui,all-providers]"
```

---

## Usage

```bash
# Basic (defaults to the claude provider)
repo-translate --input ./my-ts-repo --from ts --to python

# Choose a provider / model
repo-translate --input ./my-repo --from go --to python --provider openai --model gpt-4o
repo-translate --input ./my-repo --from ts --to python --provider offline   # free, no API key

# Custom output directory
repo-translate --input ./my-repo --from go --to python --output ./translated

# Skip manifest translation
repo-translate --input ./my-repo --from ts --to python --no-manifest

# Skip report
repo-translate --input ./my-repo --from ts --to python --no-report

# Also run the translated test suite
repo-translate --input ./my-repo --from ts --to python --run-tests

# Resume an interrupted run — skips files already completed last time
# (reads .translation_state.json from the output dir; on by default, use --no-resume to disable)
repo-translate --input ./my-repo --from ts --to python --resume

# Give every file's prompt a read-only map of the other files' top-level symbols,
# to help keep cross-file imports/calls consistent (off by default — bigger prompts)
repo-translate --input ./my-repo --from ts --to python --cross-file-context

# All flags
repo-translate \
  --input     ./my-repo   \
  --from      ts          \
  --to        python      \
  --output    ./out       \
  --provider  claude       \
  --model     sonnet      \
  --base-url  https://... \
  --api-key   sk-ant-...  \
  --run-tests             \
  --no-manifest           \
  --no-confidence         \
  --no-report             \
  --no-resume             \
  --cross-file-context    \
  --quiet
```

---

## Output structure

```
my-repo_python/
├── src/
│   ├── index.py
│   └── utils/
│       └── parser.py
├── requirements.txt          ← translated from package.json
├── translation_report.md     ← human-readable summary
└── translation_report.json   ← machine-readable report
```

---

## GitHub Actions

Run translations automatically via the **Actions** tab → **Translate Repository** → **Run workflow**.

Required secret: `ANTHROPIC_API_KEY`

The translated code is pushed to the branch you specify (default: `translated`), and the report is uploaded as a workflow artifact.

---

## Web UI

A local single-user web UI is available as an alternative to the CLI: pick a repo, an
offline/online provider, see a cost estimate, kick off a translation, and watch progress
live, then browse the translated output side-by-side with the original.

```bash
pip install -e ".[webui]"
repo-translate-ui   # serves the API on http://127.0.0.1:8765
```

For frontend development:

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173, proxies /api to the backend above
```

To serve the built frontend from the same process as the API:

```bash
cd frontend && npm run build   # outputs to frontend/dist
repo-translate-ui               # now also serves the built UI at /
```

---

## Testing

```bash
pip install -e ".[dev,webui]"

pytest                          # full suite (unit + integration), no network/API key needed
pytest -m "not integration"     # fast unit tests only (Claude/provider calls mocked)
pytest -m integration           # real CLI subprocess + webui tests, offline provider

cd frontend
npm run lint                    # ESLint, incl. eslint-plugin-security
npm run build
npm run test:e2e                # Playwright, drives a real browser against the built SPA
```

Every PR runs the same checks in CI (`.github/workflows/ci.yml`) plus a security gate
(`.github/workflows/security.yml`): `pip-audit`, `npm audit`, `bandit`, ESLint security
rules, `gitleaks`, and CodeQL.

---

## Example report

```
  ══════════════════════════════════════════════
   ✅  Translation Report
  ══════════════════════════════════════════════
   From      : typescript
   To        : python
   Duration  : 47.3s

   Files     : 12 / 12 translated
   Auto-fixed: 2 (needed retries)
   Manifests : 1 translated

   📁 Output : /home/user/my-ts-app_python
  ══════════════════════════════════════════════

  📄 Report saved → translation_report.md  +  translation_report.json
```

---

## Contributing

PRs welcome! Ideas for next steps:
- `--test` flag to run existing test suites post-translation
- Interactive mode with confirmation per file
- Support for monorepos with mixed languages
