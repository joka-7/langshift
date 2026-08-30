---
name: write-design-doc
description: Produce an HLD/LLD design document — either documenting an existing repo's architecture as it's actually built, or proposing the design for a new feature/system before code is written — following this config's HLD/LLD checklist (rules/practices/architecture.md). Use whenever the user asks for a design doc, an HLD, an LLD, "high-level design", "low-level design", an ADR-style writeup, or asks to "design this before we build it" or "document how this system is architected".
---

# Write Design Doc

Two modes, chosen by what you're given — ask if it's ambiguous, since "document what we have"
and "help me design X" pull from different sources of truth (code vs. conversation) and
producing the wrong one wastes the whole exercise:

- **Document mode** — a target repo (existing code) is named or already in context. Read the
  system as it's actually built and write it up, matching each checklist item to real evidence
  (a `file:line`, a real config key, a real class name) — never a plausible-sounding guess.
- **Design mode** — a feature or system is described but not yet built. Fill in the same
  checklist as a proposal: state assumptions explicitly, and put a genuinely open decision
  under `## Open questions` rather than inventing an answer to make the doc look complete.

Both modes follow the exact checklist in `rules/practices/architecture.md` — the compiled rule
every project sees when it opts into `practices = ["architecture"]` — and the same categories
`architect`'s review checklist checks a repo against. This skill, that rule, and that subagent
are three faces of one checklist, kept in the same shape on purpose: a review finding, a
design-doc section, and a build-time rule should all mean the same thing when they name the
same category.

## Steps

1. Determine mode (document vs. design) and scope (whole system, or one feature/module). For
   a single small feature, one combined `DESIGN.md` with both sections below is fine instead
   of two files — ask if unsure which the user wants.
2. **Document mode**: read manifests/README first to establish intent, then the real code —
   package layout, config loading, error handling, logging setup, the data-access layer. Every
   checklist item needs real evidence; if the codebase doesn't answer one (e.g. no documented
   backup/DR strategy), say so under that section rather than skipping it silently.
   **Design mode**: work from the feature description and any stated constraints. Where a
   decision genuinely isn't made yet — a store hasn't been chosen, an NFR hasn't been set —
   that's an `## Open questions` entry, not a guess dressed up as a decision.
3. Write `HLD.md` using the HLD outline, then `LLD.md` using the LLD outline (or the combined
   `DESIGN.md`, per step 1).
4. Cross-link: `LLD.md` should name which HLD decision each low-level choice implements — point
   at it, don't repeat it.

## HLD outline (`HLD.md`)

```markdown
# <System/Feature> — High-Level Design

## Requirements
- Functional: ...
- Non-functional (these drive the architecture): latency/throughput, availability, compliance, ...

## Static view
<components/modules and what each owns — a block-diagram description, or a real directory map>

## Dynamic view
<end-to-end flow for the primary use case, step by step>

## Data storage
- Primary store: ... (and why)
- Cache: ...
- Blob storage: ...
- Backup/DR: ...

## Config management
<precedence: secrets manager > env > local config > defaults; fail-fast at startup>

## Data validation / I/O
<schema at the edge, format, encoding>

## Integration test strategy
<what's verified across component boundaries, and how>
```

## LLD outline (`LLD.md`)

```markdown
# <System/Feature> — Low-Level Design

## Class / interface contracts
<the actual contracts — interface/ABC/trait — business logic depends on, never the concrete class>

## Pseudocode
<for non-obvious logic only — skip anything a signature already makes clear>

## I/O schemas
<exact request/response shapes or function signatures>

## Config keys
<the actual keys, their defaults, and where each is read>

## DI wiring
<where the composition root is, and what it wires — never a hardcoded instantiation buried in logic>

## Error contract
<the exception hierarchy or typed Result/discriminated union — not both, not neither>

## Logging plan
<what's logged, at what level, which context fields — never PII>

## Unit test plan
<AAA per contract; mocking targets — true I/O boundaries only>
```

## Rules

- Document mode: every claim traces to real code — a file path, a class name, a config key
  that actually exists. Never invent an architecture the code doesn't have just to make a
  section look filled in.
- Design mode: never silently resolve a genuine unknown. An honest `## Open questions` entry
  is more useful to a reviewer than a plausible guess they can't tell is fabricated.
- Keep it a checklist filled with real content, not narrative padding — this repo's own rules
  on output discipline apply to what you write here too.
- This skill only writes design docs. It does not edit source code (that's `developer`, and
  only against approved backlog items) and it does not review a repo end-to-end across other
  domains (that's `audit_repo`, or the role-review fan-out) — stay in lane.
