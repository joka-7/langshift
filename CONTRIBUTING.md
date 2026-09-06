# Contributing

Thanks for taking a look. This is a personal project, so expect a best-effort
review rather than a fast one.

## Setup

```bash
git clone --recurse-submodules https://github.com/joka-7/langshift
cd langshift
pip install -e ".[dev,webui]"
```

`--recurse-submodules` pulls in `.ai` ([ogen-ai](https://github.com/joka-7/ogen-ai)),
which carries the shared coding rules and the doc generators. Nothing in the build
needs it — CI checks out without submodules — so a plain clone works fine if you
are not regenerating docs.

## The checks CI will run

Run these before opening a PR; they are exactly what `.github/workflows/ci.yml`
runs, so anything green here is green there.

```bash
ruff check .                  # lint
mypy repo_translator          # types
pytest                        # full suite
pytest -m "not integration"   # fast lane: mocked providers, no subprocesses
pytest --cov=repo_translator --cov-fail-under=92   # the coverage gate CI enforces
```

Frontend, if you touched `frontend/`:

```bash
cd frontend
npm ci && npm run lint && npm test && npm run build
npx playwright install --with-deps chromium && npm run test:e2e
```

No API key is needed for any of it. The unit suite mocks every provider, and the
integration-marked tests run against the rule-based `offline` provider.

## House rules

The full set lives in [AGENTS.md](AGENTS.md) (generated — edit `ai-config.local.md`,
not the copies). The parts that come up most:

- **Conventional Commits**: `type(scope): summary`, imperative, ≤72 chars. One
  logical change per commit; the body explains *why*.
- **Every bug fix starts with a failing test** that reproduces it.
- **Test behavior, not implementation.** A refactor that preserves behavior
  shouldn't break tests.
- **Docs change in the same commit as the behavior they describe.**
- Generated regions (`<!-- BEGIN GENERATED … -->`) are never hand-edited.
  Refresh them with `python .ai/skills/repo_tree/gen_tree.py --project . --check`
  to see drift, then re-run without `--check` to fix it. CI fails on drift.

## Adding a language

`LANGUAGE_META` in `repo_translator/agent.py` is the single table to extend — see
the "How to add a new language" section of [AGENTS.md](AGENTS.md) for the field
meanings and the `test_patterns: []` gotcha.

## Security

Please don't open a public issue for a vulnerability — see
[SECURITY.md](SECURITY.md), which also documents the threat model you should read
before running this on anything you care about.
