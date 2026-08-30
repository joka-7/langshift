---
name: customize-config
description: Scaffold and apply ai-project-config.toml — a project-local override file for custom coding rules and per-domain audit scoring weights (e.g. make Security high priority, keep Clean Code normal) — without ever hand-editing files inside the ogen-ai submodule (.ai/). Use whenever the user wants to add a project-specific coding rule, change how strict the audit_repo skill is about one domain, prioritize one audit domain over another, or asks to "customize"/"override" rules or scoring for just this project.
---

# Customize Config

Scaffold `ai-project-config.toml` in the **parent project's root** (never inside `.ai/`)
so a project can add its own coding rules and re-weight the `audit_repo` skill's domain
scores, without modifying the shared submodule.

## Why a separate file from `ai-config.toml` / `ai-config.local.md`

This repo already has two project-local mechanisms — don't confuse them with this one:

- `ai-config.toml` — `ai-sync`'s manifest. Picks which rule *fragments* compile into
  `AGENTS.md`, and which tools get wired up. Unrelated to auditing.
- `ai-config.local.md` — a prose tail appended verbatim to `AGENTS.md`. Always-on context
  for every AI tool reading `AGENTS.md` (build/test commands, "don't touch this dir").
- **`ai-project-config.toml` (this skill)** — read only by the `audit_repo` skill, at audit
  time. Structured, not prose: a list of custom rule strings the auditing agent should apply,
  and a numeric weight per audit domain. It has no effect on `AGENTS.md` or `ai-sync` at all.

## The core boundary

**Never edit anything under `.ai/` to satisfy a customization request.** Files there are
shared and get overwritten by `git submodule update --remote`; anything written there is lost
silently. Every customization in this skill's scope goes into `ai-project-config.toml` at the
parent project's root instead.

## Steps

1. Check whether `ai-project-config.toml` already exists at the project root.
   - If not: `python .ai/skills/customize_config/init_config.py --project <path>` (default
     `.`) to scaffold it with the default template (every domain at weight `1.0`, an empty,
     commented `conventions` list). It refuses to overwrite an existing file — pass `--force`
     only if the user explicitly asks to reset it.
   - If it exists: open it directly; don't re-run the scaffolder.
2. Edit the file to what the user asked for:
   - A new project rule → add a one-sentence string to `[rules.custom].conventions`.
   - "Make X high priority" / "I don't care as much about Y" → change that domain's value
     under `[audit.weights]`. Suggested bands: `0.5` low, `1.0` normal, `1.5` high, `2.0`
     critical, `0.0` excludes the domain from the overall score entirely.
3. Validate what you wrote: `python .ai/skills/customize_config/init_config.py --check <path>`.
   It parses the file and prints the resolved rules/weights plus any warnings (unknown domain
   name — likely a typo against the six audit domains; negative weight — clamped to `0.0`).
   Fix anything it flags before telling the user you're done.
4. **Custom rules are agent-applied, not script-enforced.** `run_audit.py` reads
   `[rules.custom].conventions` and passes them through into `audit_data.json` untouched — it
   never checks them itself. When the `audit_repo` skill's agent does its manual review of
   Architecture & Design / Clean Code, its own SKILL.md has it apply these conventions during
   that read-through. Don't imply to the user that a custom rule is being mechanically
   enforced by a regex; it's a checklist item for the auditing agent's judgment.
5. **Weights are live in `run_audit.py`.** `AuditOrchestrator.run()` resolves each domain's
   weight (default `1.0` for anything not listed in `[audit.weights]`) and computes the
   overall score as a weighted mean, renormalized to stay 0-100 — see
   `skills/audit_repo/run_audit.py`'s `ProjectOverrides.load` and `AuditOrchestrator.run` for
   the exact algorithm. Each domain's own 0-100 score is never itself weighted, only its
   contribution to the overall figure — `audit_data.json`'s `domain_weights` field shows the
   resolved weight per domain so this is never opaque to whoever reads the report.

## Rules

- Don't write or suggest writing anything under `.ai/` to satisfy a customization request —
  that's the one thing this skill exists to prevent.
- Keep `[rules.custom]` entries short and mechanically checkable-by-a-reader — a vague rule
  ("write good code") gives the auditing agent nothing to judge against.
- A domain weighted to `0.0` is excluded from the overall score but still gets its own score
  and findings reported — never omit a domain's section from `AUDIT_REPORT.md` just because
  its weight is `0`.
