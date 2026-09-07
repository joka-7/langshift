# Security

## Reporting a vulnerability

Report privately through GitHub's [Report a
vulnerability](https://github.com/joka-7/langshift/security/advisories/new) form
rather than opening a public issue. Please include what you did, what happened,
and the commit you saw it on. This is a personal project, not a staffed product:
expect a best-effort reply, not an SLA.

## Threat model — read this before running langshift

langshift is a **local developer tool**. It assumes one trusted user on one
machine, translating a repository they already trust. It is not hardened for
multi-tenant use, and nothing here should be exposed to a network.

### With `--run`, it runs code the model wrote, on your machine, unsandboxed

Execution is **off by default**: a plain run translates and writes files without
executing any of them. `--run` turns it on (and `--run-tests` implies it),
because the auto-fix loop works by running the translation and feeding the error
back to the model. Once on, concretely:

- `_try_run()` (`repo_translator/agent.py`) writes each translated file to a
  temp file and executes it with the target language's interpreter
  (`LANGUAGE_META[lang]["runner"]`), with a 15s timeout. Reached only when
  execution is enabled.
- `run_tests()` (`repo_translator/agent.py`) runs the target language's test
  runner in the output directory (`--run-tests`), with a 120s timeout. For some
  languages that alone executes project-defined scripts — `npm test` runs
  whatever `package.json` says, `cargo test` compiles `build.rs`.
- `repo_translator/cpp_test_runner.py` invokes `cmake`/`ctest` or `g++`/`clang++`
  and executes the binary it produces.

Both timeouts bound how *long* that code runs, not what it may do. It runs with
your user's full privileges: your filesystem, your network, your credentials.

There is no sandbox. Two consequences worth being explicit about:

1. **A model can emit destructive code by accident.** A mistranslated file that
   happens to be runnable is still run.
2. **Source repositories are untrusted input.** Text in the files you translate
   reaches the model as part of the prompt, so a repository containing prompt
   injection can influence what gets generated — and therefore what gets
   executed. Do not point langshift at a repository you would not run.

So the safe path is the default one: omit `--run` and nothing generated is
executed. The cost is quality — the auto-fix loop has no runtime error to learn
from, and nothing verifies the translation runs at all.

`execute` is deliberately tri-state on `translate_repo()` and `POST /api/jobs`.
Unset means "infer": `run_tests_after` turns execution on, so asking for the
suite can never silently do nothing. An explicit `execute=False` is a refusal
and is never overridden — pairing it with `run_tests_after` raises, and the CLI
rejects `--no-run --run-tests` the same way, rather than quietly honouring one.

For no execution *and* no network, add `--provider offline`, which is pure
rule-based rewriting (`repo_translator/offline/`) and calls no model at all.
Running in a container or VM remains the stronger boundary for `--run`.

### The web UI is localhost-only, and depends on staying that way

`repo-translate-ui` binds `127.0.0.1:8765` (`run()` in
`repo_translator/webui/main.py`) and has **no authentication**. Anyone who can
reach the port can start jobs, read job output, and spend your API credits.

Requests carry an `input_path`/`output_path` chosen by the caller, and
`GET /api/jobs/{id}/file` serves files beneath a job's `output_path`, so the API
can read any file the running user can. `_safe_join()` blocks `../` traversal
and absolute paths *within* a job root; it deliberately does not constrain which
root a job may declare, because pointing the tool at an arbitrary directory is
its purpose on the CLI.

That capability is safe only while the caller is you. Two things keep it that
way, and neither should be loosened:

- **Do not bind it to `0.0.0.0`** or put it behind a tunnel or reverse proxy.
- **CORS is an allowlist** of `localhost`/`127.0.0.1` on ports 5173 and 8765
  (`ALLOWED_ORIGINS`, `repo_translator/webui/main.py`). Binding to localhost does
  not stop a website you visit from POSTing to `127.0.0.1`; the allowlist is what
  stops it *reading the reply*. With `allow_origins=["*"]` any page could
  register a job rooted at `/` and read your disk through the file endpoint.

### API keys

Keys are passed explicitly or fall back to the environment —
`OPENAI_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `GROQ_API_KEY` and
`OPENAI_COMPAT_API_KEY` are read in `repo_translator/providers/`, and
`ANTHROPIC_API_KEY` is picked up by the `anthropic` SDK itself when no key is
passed. None are hardcoded anywhere.

A key supplied to the web UI reaches only the provider constructor: `Job` has no
`api_key` field, so it is not in `Job.to_dict()` and therefore not in the API
response, the history file, or the checkpoint. It is not written to the report
either.

### Your source code leaves your machine

Every provider except `offline` sends the contents of the files being translated
to that provider's API. Additionally, when a file fails every retry, langshift
prints third-party chat URLs with up to 1500 characters of that file's source
embedded in the query string
(`repo_translator/providers/external_chat.py`). Nothing is sent unless you click
one, but the code is in the link — treat those URLs as you would the source.

## Automated checks

Every PR runs `.github/workflows/security.yml`: `pip-audit`, `npm audit`
(`--audit-level=high`), `bandit`, ESLint with `eslint-plugin-security`, a
full-history `gitleaks` scan, and CodeQL for Python and JavaScript/TypeScript.

CodeQL is a real gate: its analyze step carries no `continue-on-error`, so a
failed analysis or a failed upload fails the job. That depends on code scanning
staying enabled for the repository — if it is ever turned off, the upload starts
erroring and the Security workflow goes red, which is the intended signal rather
than something to paper over.
