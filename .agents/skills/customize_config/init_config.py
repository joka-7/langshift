#!/usr/bin/env python3
"""init_config.py — scaffold ``ai-project-config.toml`` for the customize_config skill.

Why this exists
----------------
``ogen-ai`` is a git submodule mounted at ``.ai/`` in every consuming project; its
files are shared and re-synced from upstream, so a project should never hand-edit
anything under ``.ai/`` to get project-specific behavior — those edits would be
silently lost on the next ``git submodule update --remote``. This script instead
scaffolds ``ai-project-config.toml`` in the *parent* project's own root: a small,
hand-edited override file the audit_repo skill (and, per its own SKILL.md, the
agent applying custom rules) reads back at audit time.

Decoupled design
----------------
Two concerns are kept fully independent, per the brief:

- **Parsing** (:func:`parse_project_config`, :class:`ProjectAuditConfig`) turns
  TOML text into a validated in-memory config. It never writes anything and has
  no knowledge of the default template's exact wording.
- **Generation** (:func:`render_default_toml`) renders the canonical starter
  template as a plain string. It never reads a file and does not call the parser
  — the template is authored directly, so generation has no runtime dependency
  on parsing succeeding.
- **I/O** (:class:`ConfigWriter`) is the only piece that touches the filesystem
  for writing, and only decides *whether* to write (never clobbering an existing,
  presumably hand-edited file without ``--force``); it has no opinion on content.

Usage
-----
    python init_config.py --project /path/to/parent/repo   # scaffold the file
    python init_config.py --check /path/to/ai-project-config.toml  # validate one
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FILENAME = "ai-project-config.toml"

# Must match the `domain` attribute of each DomainAnalyzer in
# skills/audit_repo/run_audit.py exactly, since these are the keys the audit
# skill will (once wired up, see the customize_config SKILL.md) look up weights
# by. Kept here as plain data, not imported from audit_repo, so the two skills
# stay independently self-contained (see SKILL.md's "Why two separate skills").
KNOWN_AUDIT_DOMAINS: tuple[str, ...] = (
    "Architecture & Design",
    "Clean Code",
    "Documentation",
    "Security",
    "Scalability",
    "Testing",
)

DEFAULT_WEIGHT = 1.0


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ProjectAuditConfig:
    """The resolved, validated contents of one ``ai-project-config.toml`` file."""

    custom_rules: list[str] = field(default_factory=list)
    domain_weights: dict[str, float] = field(
        default_factory=lambda: {domain: DEFAULT_WEIGHT for domain in KNOWN_AUDIT_DOMAINS}
    )

    @classmethod
    def default(cls) -> ProjectAuditConfig:
        """Return the baseline config: no custom rules, every domain at weight 1.0."""
        return cls()


def parse_project_config(text: str) -> tuple[ProjectAuditConfig, list[str]]:
    """Parse ``ai-project-config.toml`` content into a :class:`ProjectAuditConfig`.

    Algorithm: load the TOML text with :mod:`tomllib`, then read two tables
    defensively so a file that only overrides one section still parses cleanly:

    1. ``[rules.custom].conventions`` — a list of freeform rule strings, used
       as-is (order preserved; blank entries dropped).
    2. ``[audit.weights]`` — a table mapping domain name to a numeric
       multiplier. Each value is coerced to ``float``; a negative weight would
       invert a domain's contribution to the overall score, which can never be
       the user's intent, so it is clamped to ``0.0`` and reported as a
       warning rather than silently accepted or hard-rejected. A domain name
       that doesn't match :data:`KNOWN_AUDIT_DOMAINS` is kept in the result
       (forward-compatible with domains added later) but also reported, since
       it's more often a typo than a genuinely new domain.

    Returns the parsed config plus a list of human-readable warning strings —
    never raises for a malformed *table*, only for invalid TOML syntax itself
    (which propagates as :class:`tomllib.TOMLDecodeError`).
    """
    data = tomllib.loads(text)
    warnings: list[str] = []

    rules_table = data.get("rules", {})
    custom_table = rules_table.get("custom", {}) if isinstance(rules_table, dict) else {}
    raw_rules = custom_table.get("conventions", []) if isinstance(custom_table, dict) else []
    custom_rules = [str(rule).strip() for rule in raw_rules if str(rule).strip()]

    weights_table = data.get("audit", {})
    raw_weights = weights_table.get("weights", {}) if isinstance(weights_table, dict) else {}
    domain_weights = {domain: DEFAULT_WEIGHT for domain in KNOWN_AUDIT_DOMAINS}
    for name, raw_value in raw_weights.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            warnings.append(f"[audit.weights] '{name}' is not a number ({raw_value!r}) — ignored.")
            continue
        if value < 0.0:
            warnings.append(f"[audit.weights] '{name}' is negative ({value}) — clamped to 0.0.")
            value = 0.0
        if name not in KNOWN_AUDIT_DOMAINS:
            warnings.append(
                f"[audit.weights] '{name}' does not match a known audit domain "
                f"({', '.join(KNOWN_AUDIT_DOMAINS)}) — check for a typo."
            )
        domain_weights[name] = value

    config = ProjectAuditConfig(custom_rules=custom_rules, domain_weights=domain_weights)
    return config, warnings


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def render_default_toml() -> str:
    """Render the canonical starter ``ai-project-config.toml`` as literal text.

    Pure string generation: this function never parses anything and never
    touches disk, so it has no dependency on :func:`parse_project_config` or
    on the filesystem — it can be unit-tested as a plain string in, string out
    function.
    """
    weight_lines = "\n".join(f'"{domain}" = 1.0' for domain in KNOWN_AUDIT_DOMAINS)
    return f'''\
# ai-project-config.toml — local overrides for the ogen-ai audit_repo skill.
#
# This file lives in YOUR project root, not inside the .ai/ submodule — it is
# never touched by `git submodule update` and is yours to edit freely. Re-running
# `init_config.py` will not overwrite it unless you pass --force.

[rules.custom]
# Project-specific coding conventions the audit_repo skill's agent should apply
# on top of the base rules in .ai/rules/ when scoring Architecture & Design and
# Clean Code. One sentence per convention. These are read and judged by the
# agent doing the audit, not mechanically enforced by a script.
conventions = [
    # "All public functions must use a leading verb (get_, set_, is_, has_).",
    # "Repository classes may only be imported from the `services/` layer.",
]

[audit.weights]
# Multiplier applied to each domain's 0-100 score before the overall score is
# recomputed (weighted mean, renormalized so overall stays 0-100).
# 1.0 = normal priority. Suggested bands: 0.5 = low, 1.0 = normal, 1.5 = high,
# 2.0 = critical. Setting a domain to 0.0 excludes it from the overall score
# entirely (its own score is still reported, just not averaged in).
{weight_lines}
'''


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------


class ConfigWriter:
    """Writes rendered template text to disk without clobbering a hand-edited file."""

    def __init__(self, force: bool = False) -> None:
        self._force = force

    def write(self, path: Path, content: str) -> bool:
        """Write ``content`` to ``path``; return ``True`` if written, ``False`` if skipped.

        Unlike ``ai-sync``'s ``AGENTS.md`` (which is fully regenerated on every
        run), this file is meant to be hand-edited immediately after scaffolding
        — so the safety rule is simpler and stricter: if ``path`` already
        exists at all, do nothing unless ``--force`` was passed.
        """
        if path.exists() and not self._force:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _run_check(path: Path) -> int:
    """Parse an existing config file and print the resolved settings; report warnings."""
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 1
    try:
        config, warnings = parse_project_config(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        print(f"invalid TOML in {path}: {exc}", file=sys.stderr)
        return 1

    for warning in warnings:
        print(f"! {warning}", file=sys.stderr)
    print(f"{path}: {len(config.custom_rules)} custom rule(s)")
    for rule in config.custom_rules:
        print(f"  - {rule}")
    print("domain weights:")
    for domain, weight in config.domain_weights.items():
        print(f"  {domain:<24} {weight}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: scaffold a new config, or validate an existing one with ``--check``."""
    parser = argparse.ArgumentParser(
        description="Scaffold ai-project-config.toml: local rule/weight overrides "
                     "for the audit_repo skill, without touching the ogen-ai submodule."
    )
    parser.add_argument("--project", type=Path, default=Path("."),
                         help="Parent project root to scaffold into (default: cwd).")
    parser.add_argument("--output", type=Path, default=None,
                         help=f"Output path (default: <project>/{DEFAULT_FILENAME}).")
    parser.add_argument("--force", action="store_true",
                         help="Overwrite an existing config file.")
    parser.add_argument("--check", type=Path, default=None,
                         help="Parse and validate an existing config file instead of "
                              "scaffolding a new one; prints the resolved settings.")
    args = parser.parse_args(argv)

    if args.check is not None:
        return _run_check(args.check)

    output = args.output or (args.project.resolve() / DEFAULT_FILENAME)
    written = ConfigWriter(force=args.force).write(output, render_default_toml())
    if not written:
        print(f"skipped: {output} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    print(f"wrote: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
