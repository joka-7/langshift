# Scaffold template

Baseline files for `scaffold-python-service`, built around a placeholder package named
`example_service`. See `../SKILL.md` step 2 — the skill reads these, renames every
occurrence of `example_service` to the real package name, and adapts the FastAPI pieces
away for a plain-library scaffold. Verified against `ruff check .`, `mypy --strict src`,
and `pytest` before being checked in; keep it passing all three when editing.

