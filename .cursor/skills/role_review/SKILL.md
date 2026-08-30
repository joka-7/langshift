---
name: role-review
description: The shared output contract for the role agents (qa, architect, product, engineering-manager, sre, senior-dev, ciso, planner) — fixed report schema, severity scale, finding-ID convention, evidence rules, and the context-budget protocol for exploring a large repo without reading all of it. Load this whenever you are acting as one of those roles reviewing a target repository, before writing any findings, so every role's report is shaped the same way and can be aggregated and deduplicated.
---

# Role Review

The common contract every role agent follows. Roles differ in *what they look for*; this
file fixes *how they look* and *how they report*, so reports written in isolated contexts
can be merged into one backlog without reconciliation work.

## Context budget

The target repo may be large. Never read it all. Build a map first, then open only what the
map points at.

1. **Orient before opening.** Dependency manifests (`package.json`, `pyproject.toml`,
   `go.mod`, `Cargo.toml`) and `README.md` tell you the stack, the entry points, and the
   vocabulary. Read these first, always.
2. **Then CI config** (`.github/workflows/`, `.gitlab-ci.yml`, `Jenkinsfile`) — it names the
   real build, test, and lint gates, which is the fastest way to learn what the project
   actually enforces versus what it claims.
3. **Then the audit slice.** If `.ai-reviews/audit_data.json` exists, read your own domain's
   entry from it. It is a mechanical pre-scan someone already paid for — its `findings` give
   you `file:line` starting points, and its `metrics` give you ratios you would otherwise
   have to compute by hand. Do not re-derive what it already measured.
4. **Rank, then read.** Use `rg -l` / `rg -c` to find *where* the signal concentrates before
   opening anything. Reading the three files with 40 hits beats reading thirty files with one.
5. **Sample big files.** Over ~500 lines, read the first ~80 lines (imports, class/function
   names, module docstring) plus targeted greps, rather than the whole file.
6. **Stop at ~25 full file reads.** If you hit that and still feel blind, say so under
   `## Open questions` — an honest "I could not assess X in the budget" is worth more than a
   confident finding you inferred without looking.

## Output schema

Every role emits exactly this, in this order. Do not add or rename sections.

```markdown
# <Role> Review — <repo> @ <short-sha>

## Summary
<3–5 sentences: the posture of this repo through your role's lens, and the single most
important thing the reader should act on.>

## Findings

### <ID> — <one-line title>
- **Severity:** critical | high | medium | low | info
- **Where:** `path/to/file.ext:123`
- **Evidence:** <what you actually observed — quoted code, config, or command output>
- **Impact:** <why it matters in this repo's context, not in the abstract>

## Recommendations
1. <imperative action> — addresses <ID(s)>, est. <S|M|L>

## Open questions
- <a question whose answer would change one of your recommendations>
```

## Severity

Pick against these definitions, not by feel. A report where everything is `high` is a report
that has not prioritized anything.

- **critical** — exploitable right now, causes data loss, or the build/deploy is broken.
- **high** — likely to cause an incident or block delivery; fix this sprint.
- **medium** — real risk or meaningful debt; schedule it.
- **low** — minor; fix when touching the area anyway.
- **info** — an observation worth recording that needs no action.

## Finding IDs

Prefix by role, two digits, numbered in the order you report them: `QA-01`, `ARC-01`,
`PRD-01`, `EM-01`, `SRE-01`, `SDR-01`, `SEC-01`. The aggregation step cites these when it
merges duplicates, so they must be stable within a report and unique across roles.

## Steps

1. Load the target repo path and, if present, `.ai-reviews/audit_data.json`.
2. Work the context budget above in order. Stop exploring when you can support your findings
   with evidence, not when you have seen everything.
3. Draft findings as you go, each with a real `file:line` you have actually opened.
4. Sort findings by severity, highest first. Renumber IDs so they read in that order.
5. Write recommendations that each reference the finding IDs they resolve — an unlinked
   recommendation means you are guessing at something you did not evidence.
6. Emit the schema above as your final message. Do not write it to disk; the orchestrator
   persists it for you.

## Rules

- Every finding cites a real `file:line` you opened. If you cannot produce one, you do not
  have a finding — you have a suspicion, and it belongs in `## Open questions`.
- Never invent a path, line number, symbol, or command output. Quote what you actually saw.
- Cap at 15 findings. Over that, report the top 15 by severity and note the truncation in
  `## Open questions`. A 60-finding report does not get read.
- Stay in your lane. If you notice something outside your role, mention it in one line under
  `## Open questions` and move on — another role is already looking at it, and duplicate
  coverage is the aggregation step's problem to dedupe, not yours to pre-empt.
- Assess what is there. A missing test suite, absent CI, or undocumented module is a finding;
  padding the report with generic best-practice advice the repo never claimed to follow is not.
- Do not modify the target repo. Reviewing roles have no `Edit`/`Write` for exactly this
  reason; if you find yourself wanting to fix something, that is a recommendation.
