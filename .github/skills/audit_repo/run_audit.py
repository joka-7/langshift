#!/usr/bin/env python3
"""run_audit.py — collect objective repository-health signals for the audit_repo skill.

Algorithm overview
------------------
1. ``RepoScanner`` walks the target project once, pruning noise directories
   (``.git``, ``node_modules``, build output, caches, …), reading each
   remaining text file into a ``SourceFile``, and detecting the tech stack
   from file extensions and marker files (``pyproject.toml``,
   ``package.json``, ...). Python files are parsed once into an AST and
   cached on the resulting ``ProjectScan`` so every analyzer that needs a
   syntax tree reuses it instead of re-parsing.
2. Six ``DomainAnalyzer`` implementations (Architecture, Clean Code,
   Documentation, Security, Scalability, Testing) each receive the same
   ``ProjectScan`` and independently compute a 0-100 score, a confidence
   level, and a list of concrete findings with file/line evidence. Each
   analyzer is a self-contained strategy so new domains can be added
   without touching the others.
3. ``AuditOrchestrator`` runs every analyzer and folds the results into one
   ``AuditReport``. The overall score is a weighted mean of the six domain
   scores, renormalized so it stays 0-100 regardless of the weights used.
4. If the audited project has its own ``ai-project-config.toml`` (scaffolded
   by the sibling ``customize_config`` skill), ``ProjectOverrides.load``
   reads its ``[audit.weights]`` table to bias that mean toward the domains
   the project cares about most, and passes its ``[rules.custom]``
   conventions through into the report untouched, for the auditing agent to
   apply during its own manual review (see this script's own ``SKILL.md``).

This script only produces the *mechanical* half of the audit: countable,
re-derivable signals (annotation ratios, docstring presence, secret regexes,
nesting depth, test-to-source ratio, …). Judgment calls a static scan cannot
make reliably — e.g. whether a docstring actually explains the algorithm
rather than restating the signature, or whether a design is genuinely
decoupled — are left to the agent that consumes this output, per
``SKILL.md``. Confidence is marked low/medium on exactly those domains.

Usage
-----
    python run_audit.py --project /path/to/repo --output audit_data.json
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import os
import re
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

Severity = Literal["info", "low", "medium", "high"]
Confidence = Literal["high", "medium", "low"]

PROJECT_CONFIG_FILENAME = "ai-project-config.toml"

EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn", ".ai", "node_modules", "__pycache__",
        ".venv", "venv", "env", ".env-dir", "dist", "build", ".next",
        ".nuxt", ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage",
        ".idea", ".vscode", ".tox", "target", "vendor", "site-packages",
    }
)

LANGUAGE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}

MARKER_FILE_STACK: dict[str, str] = {
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "setup.py": "python",
    "package.json": "javascript",
    "tsconfig.json": "typescript",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "build.gradle": "kotlin",
    "build.gradle.kts": "kotlin",
}

# Files worth reading even though they aren't "source" (stack/security signals).
CONFIG_FILENAMES: frozenset[str] = frozenset(
    {
        "package.json", "tsconfig.json", "pyproject.toml", "requirements.txt",
        "setup.py", "go.mod", "Cargo.toml", "build.gradle", "build.gradle.kts",
        ".gitignore", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        ".eslintrc", ".eslintrc.json", ".eslintrc.js", ".prettierrc",
    }
)

MAX_FILE_BYTES_DEFAULT = 2_000_000
DEFAULT_LINE_LENGTH = 99


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """Clamp ``value`` into ``[low, high]``."""
    return max(low, min(high, value))


_SHEBANG_LANGUAGE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^#!.*\bpython3?\b"), "python"),
    (re.compile(r"^#!.*\bnode\b"), "javascript"),
)


def _detect_shebang_language(text: str) -> str | None:
    """Infer a language from a file's shebang line, for extensionless executables.

    Needed because extensionless scripts (e.g. ``bin/ai-sync`` in this very
    repo) carry no file-extension signal at all; the interpreter named on
    the ``#!`` line is the only clue available.
    """
    first_line = text.splitlines()[0] if text else ""
    if not first_line.startswith("#!"):
        return None
    for pattern, language in _SHEBANG_LANGUAGE:
        if pattern.match(first_line):
            return language
    return None


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass(slots=True)
class SourceFile:
    """One scanned file: its path, detected language, and text content."""

    absolute_path: Path
    relative_path: str
    language: str
    line_count: int
    text: str


@dataclass(slots=True)
class ProjectScan:
    """The full result of walking a project once: files, stack, parsed ASTs."""

    root: Path
    stack: list[str]
    files: list[SourceFile]
    python_modules: dict[str, ast.Module]
    skipped: list[str] = field(default_factory=list)

    def files_by_language(self, language: str) -> list[SourceFile]:
        """Return every scanned file detected as ``language``."""
        return [f for f in self.files if f.language == language]


@dataclass(slots=True)
class Finding:
    """A single concrete observation, always traceable to a file (and ideally a line)."""

    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None


@dataclass(slots=True)
class DomainResult:
    """One domain's score, confidence, findings, and the raw metrics behind them."""

    domain: str
    score: int
    confidence: Confidence
    findings: list[Finding]
    metrics: dict[str, Any]


@dataclass(slots=True)
class AuditReport:
    """The complete, JSON-serializable output of one audit run."""

    root: str
    generated_at: str
    stack: list[str]
    file_count: int
    domains: dict[str, DomainResult]
    overall_score: float
    domain_weights: dict[str, float] = field(default_factory=dict)
    custom_rules: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain JSON-safe ``dict`` (dataclasses -> dicts, no ``Path`` objects)."""
        return dataclasses.asdict(self)


@dataclass(slots=True)
class ProjectOverrides:
    """Project-local overrides loaded from ``ai-project-config.toml``, if present.

    Deliberately self-contained rather than imported from the sibling
    ``customize_config`` skill's ``init_config.py``: ``ai-sync`` can place
    ``skills/`` in either symlink or copy mode, and a skill script should
    never assume where a *different* skill's files end up on disk. The two
    parsers are intentionally similar, not shared.
    """

    custom_rules: list[str] = field(default_factory=list)
    domain_weights: dict[str, float] = field(default_factory=dict)

    @classmethod
    def load(cls, project_root: Path) -> ProjectOverrides:
        """Load ``ai-project-config.toml`` from ``project_root``, defaulting to no overrides.

        Algorithm: look for :data:`PROJECT_CONFIG_FILENAME` at the project
        root; if it's absent or fails to parse as TOML, return an empty
        :class:`ProjectOverrides` (every domain implicitly stays at weight
        ``1.0``) rather than raising — a missing or hand-broken override file
        should degrade to the unweighted default, not crash the whole audit.
        When present, ``[rules.custom].conventions`` entries are read as-is
        (blank ones dropped) and ``[audit.weights]`` values are coerced to
        ``float`` and clamped to ``>= 0.0``; a value that won't parse as a
        number is skipped rather than defaulted, so a typo doesn't silently
        turn into "normal priority".
        """
        path = project_root / PROJECT_CONFIG_FILENAME
        if not path.is_file():
            return cls()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            return cls()

        rules_table = data.get("rules", {})
        custom_table = rules_table.get("custom", {}) if isinstance(rules_table, dict) else {}
        raw_rules = custom_table.get("conventions", []) if isinstance(custom_table, dict) else []
        custom_rules = [str(rule).strip() for rule in raw_rules if str(rule).strip()]

        audit_table = data.get("audit", {})
        raw_weights = audit_table.get("weights", {}) if isinstance(audit_table, dict) else {}
        domain_weights: dict[str, float] = {}
        for name, raw_value in raw_weights.items():
            try:
                domain_weights[name] = max(0.0, float(raw_value))
            except (TypeError, ValueError):
                continue

        return cls(custom_rules=custom_rules, domain_weights=domain_weights)


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


class RepoScanner:
    """Walks a project directory once and builds a :class:`ProjectScan`.

    Algorithm: a single ``os.walk`` pass prunes ``EXCLUDED_DIRS`` in place
    (so excluded subtrees are never descended into), reads each remaining
    file whose extension is in ``LANGUAGE_EXTENSIONS`` or whose name is in
    ``CONFIG_FILENAMES``, skipping anything larger than ``max_file_bytes``.
    Python files are parsed into an AST immediately and cached by relative
    path so downstream analyzers never re-parse the same file twice.
    """

    def __init__(self, max_file_bytes: int = MAX_FILE_BYTES_DEFAULT) -> None:
        self._max_file_bytes = max_file_bytes

    def scan(self, root: Path) -> ProjectScan:
        """Walk ``root`` and return the populated :class:`ProjectScan`."""
        files: list[SourceFile] = []
        python_modules: dict[str, ast.Module] = {}
        skipped: list[str] = []
        marker_hits: set[str] = set()

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in EXCLUDED_DIRS and (not d.startswith(".") or d == ".github")
            ]
            for filename in filenames:
                path = Path(dirpath) / filename
                relative = str(path.relative_to(root))
                extension = path.suffix
                language = LANGUAGE_EXTENSIONS.get(extension)
                is_config = filename in CONFIG_FILENAMES
                is_extensionless = extension == "" and language is None
                if language is None and not is_config and not is_extensionless:
                    continue
                try:
                    if path.stat().st_size > self._max_file_bytes:
                        skipped.append(relative)
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    skipped.append(relative)
                    continue

                if language is None and is_extensionless:
                    language = _detect_shebang_language(text)
                    if language is None and not is_config:
                        continue  # no extension, no shebang, not a known config file: skip

                if filename in MARKER_FILE_STACK:
                    marker_hits.add(MARKER_FILE_STACK[filename])
                if filename == "package.json" and '"react"' in text:
                    marker_hits.add("react")

                source = SourceFile(
                    absolute_path=path,
                    relative_path=relative,
                    language=language or "config",
                    line_count=text.count("\n") + 1,
                    text=text,
                )
                files.append(source)

                if language == "python":
                    try:
                        python_modules[relative] = ast.parse(text, filename=relative)
                    except SyntaxError:
                        skipped.append(f"{relative} (syntax error, excluded from AST analysis)")

        real_languages = {f.language for f in files if f.language != "config"}
        detected_stack = sorted(marker_hits | real_languages)
        return ProjectScan(
            root=root,
            stack=detected_stack,
            files=files,
            python_modules=python_modules,
            skipped=skipped,
        )


def _ts_js_files(scan: ProjectScan) -> list[SourceFile]:
    """Return every scanned TypeScript or JavaScript file."""
    return [*scan.files_by_language("typescript"), *scan.files_by_language("javascript")]


# --------------------------------------------------------------------------
# Shared AST helpers (Python)
# --------------------------------------------------------------------------


def _iter_functions(module: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return every function/method definition in ``module``, at any nesting depth."""
    return [
        node for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _is_fully_annotated(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """A function is fully annotated when every non-self/cls arg and the return are typed."""
    if fn.returns is None:
        return False
    args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    for arg in args:
        if arg.arg in {"self", "cls"}:
            continue
        if arg.annotation is None:
            return False
    return True


def _docstring_detail_score(doc: str) -> float:
    """Heuristic 0..1 score for whether a docstring likely explains *how*, not just *what*.

    Algorithm: score rises with word count (a one-liner caps low) and with
    the presence of process/flow vocabulary ("algorithm", "steps", "because",
    "raises", "complexity", "then", "first"/"finally", ...), which correlates
    with narrating a solution rather than restating a signature. This is a
    proxy, not ground truth — the SKILL.md instructs the agent to verify by
    reading flagged docstrings directly.
    """
    words = doc.split()
    length_score = clamp(len(words) / 40.0, 0.0, 1.0)
    keywords = (
        "algorithm", "step", "steps", "because", "raises", "complexity",
        "then", "first", "finally", "iterat", "recurs", "flow", "why",
    )
    lowered = doc.lower()
    keyword_hits = sum(1 for kw in keywords if kw in lowered)
    keyword_score = clamp(keyword_hits / 3.0, 0.0, 1.0)
    return clamp(0.6 * length_score + 0.4 * keyword_score, 0.0, 1.0)


def _max_loop_nesting(node: ast.AST) -> int:
    """Return the deepest nesting of ``for``/``while`` loops under ``node``.

    Algorithm: recursive descent that increments a depth counter on entering
    a loop body and takes the max across all children, so ``for`` inside
    ``for`` inside ``if`` inside ``for`` correctly reports depth 3, not the
    naive count of loop keywords.
    """
    best = 0
    for child in ast.iter_child_nodes(node):
        child_depth = _max_loop_nesting(child)
        if isinstance(child, (ast.For, ast.AsyncFor, ast.While)):
            child_depth += 1
        best = max(best, child_depth)
    return best


# --------------------------------------------------------------------------
# Domain analyzers
# --------------------------------------------------------------------------


class DomainAnalyzer(Protocol):
    """Strategy interface: turn a :class:`ProjectScan` into one :class:`DomainResult`."""

    domain: str

    def analyze(self, scan: ProjectScan) -> DomainResult:
        ...


class ArchitectureAnalyzer:
    """Scores Architecture & Design: file-size distribution, layering, import cycles.

    Confidence is always capped at "medium": a static scan can flag god
    files and import cycles, but "decoupled architecture" and "designated,
    long-term design patterns" require a human/agent read of the code.
    """

    domain = "Architecture & Design"
    GOD_FILE_LINES = 500

    def analyze(self, scan: ProjectScan) -> DomainResult:
        findings: list[Finding] = []
        score = 100.0

        god_files = [f for f in scan.files if f.line_count > self.GOD_FILE_LINES]
        for f in god_files:
            findings.append(Finding(
                severity="medium",
                message=f"{f.line_count}-line file — likely doing more than one job; "
                         "consider splitting by responsibility.",
                file=f.relative_path,
            ))
        score -= min(30.0, 6.0 * len(god_files))

        top_level_dirs = {
            p.relative_path.split("/", 1)[0]
            for p in scan.files if "/" in p.relative_path
        }
        layered_hints = {"api", "domain", "core", "services", "infra", "models",
                          "controllers", "repositories", "adapters", "handlers"}
        if scan.files and not (top_level_dirs & layered_hints):
            findings.append(Finding(
                severity="low",
                message="No conventional layering directories detected "
                         f"(looked for {sorted(layered_hints)}); verify concerns are "
                         "separated some other way before treating this as a smell.",
            ))
            score -= 5.0

        cycles = self._find_python_import_cycles(scan)
        for cycle in cycles:
            findings.append(Finding(
                severity="high",
                message=f"Likely circular import: {' -> '.join(cycle)}",
            ))
        score -= min(30.0, 15.0 * len(cycles))

        metrics = {
            "god_file_count": len(god_files),
            "top_level_dirs": sorted(top_level_dirs),
            "import_cycles_detected": len(cycles),
        }
        return DomainResult(self.domain, int(clamp(score)), "medium", findings, metrics)

    @staticmethod
    def _module_name(relative_path: str) -> str:
        if not relative_path.endswith(".py"):
            return relative_path
        return relative_path[:-3].replace("/", ".")

    def _find_python_import_cycles(self, scan: ProjectScan) -> list[list[str]]:
        """Best-effort cycle detection over intra-repo Python imports.

        Algorithm: build a directed graph where an edge A -> B means module A
        imports something whose dotted path is a suffix/prefix match of
        module B's name (exact resolution isn't attempted — this is a
        heuristic proxy), then run iterative DFS with a recursion stack to
        find back-edges. Approximate by design; flagged cycles should be
        verified by reading the actual imports.
        """
        modules = {self._module_name(rel): rel for rel in scan.python_modules}
        graph: dict[str, set[str]] = {name: set() for name in modules}

        for rel, tree in scan.python_modules.items():
            name = self._module_name(rel)
            for node in ast.walk(tree):
                imported: str | None = None
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported = alias.name
                        self._add_edge_if_match(graph, name, imported, modules)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self._add_edge_if_match(graph, name, node.module, modules)

        visited: set[str] = set()
        stack: list[str] = []
        cycles: list[list[str]] = []

        def dfs(node: str) -> None:
            visited.add(node)
            stack.append(node)
            for neighbor in graph.get(node, ()):
                if neighbor in stack:
                    cycle_start = stack.index(neighbor)
                    cycles.append([*stack[cycle_start:], neighbor])
                elif neighbor not in visited:
                    dfs(neighbor)
            stack.pop()

        for name in graph:
            if name not in visited:
                dfs(name)
        return cycles

    @staticmethod
    def _add_edge_if_match(
        graph: dict[str, set[str]], source: str, imported: str, modules: dict[str, str],
    ) -> None:
        for candidate in modules:
            if candidate == source:
                continue
            if candidate.endswith(imported) or imported.endswith(candidate):
                graph[source].add(candidate)


class CleanCodeAnalyzer:
    """Scores Clean Code: type-annotation coverage, PEP 8 line length, bare excepts,
    and (for TS/JS) ``any`` usage and ``@ts-ignore`` suppressions."""

    domain = "Clean Code"

    def __init__(self, max_line_length: int = DEFAULT_LINE_LENGTH) -> None:
        self._max_line_length = max_line_length

    def analyze(self, scan: ProjectScan) -> DomainResult:
        findings: list[Finding] = []
        score = 100.0
        metrics: dict[str, Any] = {}

        py_functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for rel, tree in scan.python_modules.items():
            fns = _iter_functions(tree)
            py_functions.extend(fns)
            unannotated = [fn for fn in fns if not _is_fully_annotated(fn)]
            for fn in unannotated[:5]:
                findings.append(Finding(
                    severity="low",
                    message=f"`{fn.name}` is missing type annotations on one or more "
                             "parameters or its return value.",
                    file=rel, line=fn.lineno,
                ))
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    findings.append(Finding(
                        severity="medium",
                        message="Bare `except:` swallows all exceptions including "
                                "KeyboardInterrupt/SystemExit — catch a specific type.",
                        file=rel, line=node.lineno,
                    ))

        if py_functions:
            annotated_ratio = sum(_is_fully_annotated(f) for f in py_functions) / len(py_functions)
            metrics["python_annotation_ratio"] = round(annotated_ratio, 2)
            score -= (1.0 - annotated_ratio) * 30.0

        line_violations = 0
        for f in scan.files_by_language("python"):
            for i, line in enumerate(f.text.splitlines(), start=1):
                if len(line) > self._max_line_length:
                    line_violations += 1
                    if line_violations <= 5:
                        findings.append(Finding(
                            severity="info",
                            message=f"Line exceeds {self._max_line_length} characters "
                                     f"({len(line)}).",
                            file=f.relative_path, line=i,
                        ))
        metrics["pep8_line_length_violations"] = line_violations
        score -= min(15.0, 0.5 * line_violations)

        ts_js_files = _ts_js_files(scan)
        any_hits = 0
        ts_ignore_hits = 0
        for f in ts_js_files:
            any_hits += len(re.findall(r":\s*any\b", f.text))
            ts_ignore_hits += len(re.findall(r"@ts-ignore", f.text))
        if ts_js_files:
            metrics["typescript_any_usages"] = any_hits
            metrics["ts_ignore_suppressions"] = ts_ignore_hits
            score -= min(20.0, 2.0 * any_hits)
            score -= min(15.0, 3.0 * ts_ignore_hits)
            if any_hits:
                findings.append(Finding(
                    severity="medium",
                    message=f"`{any_hits}` uses of `: any` across TypeScript/JS files — "
                             "defeats strict typing.",
                ))

        return DomainResult(self.domain, int(clamp(score)), "high", findings, metrics)


class DocumentationAnalyzer:
    """Scores Documentation: docstring/JSDoc presence and a detail heuristic.

    Confidence is "low": whether a docstring truly explains the algorithm
    (vs. just restating inputs/outputs) is a judgment call this script can
    only proxy by length and process vocabulary — the agent must verify.
    """

    domain = "Documentation"

    def analyze(self, scan: ProjectScan) -> DomainResult:
        findings: list[Finding] = []
        score = 100.0
        metrics: dict[str, Any] = {}

        documentable: list[ast.AST] = []
        documented = 0
        shallow: list[tuple[str, int, str]] = []
        for rel, tree in scan.python_modules.items():
            module_doc = ast.get_docstring(tree)
            if module_doc is None:
                findings.append(Finding(
                    severity="low", message="Module has no top-level docstring.", file=rel, line=1,
                ))
            nodes = [
                n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            documentable.extend(nodes)
            for node in nodes:
                doc = ast.get_docstring(node)
                if doc is None:
                    continue
                documented += 1
                if _docstring_detail_score(doc) < 0.34:
                    shallow.append((rel, node.lineno, node.name))

        for rel, line, name in shallow[:8]:
            findings.append(Finding(
                severity="low",
                message=f"`{name}` has a docstring but it reads as I/O-only — "
                         "confirm it explains the approach/flow, not just params/return.",
                file=rel, line=line,
            ))

        if documentable:
            ratio = documented / len(documentable)
            metrics["python_docstring_ratio"] = round(ratio, 2)
            metrics["shallow_docstring_count"] = len(shallow)
            score -= (1.0 - ratio) * 40.0
            score -= min(25.0, 3.0 * len(shallow))

        ts_js_files = _ts_js_files(scan)
        if ts_js_files:
            exported_fns = 0
            jsdoc_fns = 0
            for f in ts_js_files:
                exported = re.findall(r"^export (?:async )?function\s+\w+", f.text, re.MULTILINE)
                exported_fns += len(exported)
                jsdoc_pattern = r"/\*\*.*?\*/\s*export (?:async )?function"
                jsdoc_fns += len(re.findall(jsdoc_pattern, f.text, re.DOTALL))
            if exported_fns:
                ratio = clamp(jsdoc_fns / exported_fns, 0.0, 1.0)
                metrics["ts_js_jsdoc_ratio"] = round(ratio, 2)
                score -= (1.0 - ratio) * 25.0

        readme_present = any(f.name.lower() == "readme.md" for f in scan.root.glob("*"))
        metrics["readme_present"] = readme_present
        if not readme_present:
            findings.append(Finding(severity="medium", message="No README.md at project root."))
            score -= 10.0

        score -= self._score_doc_set(scan, findings, metrics)

        return DomainResult(self.domain, int(clamp(score)), "low", findings, metrics)

    def _score_doc_set(self, scan: ProjectScan, findings: list[Finding],
                       metrics: dict[str, Any]) -> float:
        """Check the standard doc set, per rules/practices/documentation.md.

        This is the signal that answers "which of my repos are missing what"
        across a whole account, so it reports each piece separately rather than
        as one pass/fail. Staleness is checked by delegating to the repo_tree
        skill's own generator — the only thing that knows how to rebuild a tree
        — rather than reimplementing the comparison here.
        """
        present = {
            "readme_tree": self._readme_has_tree(scan.root),
            "structure": (scan.root / "docs" / "STRUCTURE.md").is_file(),
            "hld": (scan.root / "docs" / "HLD.md").is_file(),
            "lld": (scan.root / "docs" / "LLD.md").is_file(),
        }
        metrics["doc_set_present"] = present

        labels = {
            "readme_tree": "README.md has no repo tree (a reader can't tell which file to open)",
            "structure": "No docs/STRUCTURE.md (annotated file tree)",
            "hld": "No docs/HLD.md (system-level design)",
            "lld": "No docs/LLD.md (per-file/function design)",
        }
        penalty = 0.0
        for key, ok in present.items():
            if not ok:
                findings.append(Finding(severity="medium", message=labels[key]))
                penalty += 5.0

        stale = self._structure_doc_is_stale(scan.root) if present["structure"] else None
        metrics["structure_doc_stale"] = stale
        if stale:
            findings.append(Finding(
                severity="high",
                message="docs/STRUCTURE.md no longer matches the real tree — a directory "
                        "map nothing verifies actively misleads. Regenerate it.",
                file="docs/STRUCTURE.md",
            ))
            penalty += 10.0
        return penalty

    @staticmethod
    def _readme_has_tree(root: Path) -> bool:
        readme = root / "README.md"
        if not readme.is_file():
            return False
        try:
            text = readme.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return "BEGIN GENERATED TREE" in text

    @staticmethod
    def _structure_doc_is_stale(root: Path) -> bool | None:
        """True/False from the generator, or None when it can't be run.

        None is not False: "we could not check" and "it is current" are different
        answers, and the agent's second pass needs to be able to tell them apart.
        """
        generator = Path(__file__).resolve().parent.parent / "repo_tree" / "gen_tree.py"
        if not generator.is_file():
            return None
        try:
            result = subprocess.run(
                [sys.executable, str(generator), "--project", str(root), "--check"],
                capture_output=True, text=True, timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode not in (0, 1):
            return None
        return result.returncode == 1


class SecurityAnalyzer:
    """Scores Security: hardcoded-secret patterns, dangerous calls, and infra hygiene."""

    domain = "Security"

    _SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str], Severity], ...] = (
        ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}"), "high"),
        ("Private key block",
         re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "high"),
        ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"), "high"),
        ("Generic API key assignment",
         re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][\w\-]{16,}['\"]"), "medium"),
        ("Hardcoded password assignment",
         re.compile(r"(?i)password\s*[:=]\s*['\"][^'\"]{4,}['\"]"), "medium"),
    )
    _DANGEROUS_CALLS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("eval(", re.compile(r"\beval\(")),
        ("exec(", re.compile(r"\bexec\(")),
        ("shell=True", re.compile(r"shell\s*=\s*True")),
        ("pickle.loads", re.compile(r"pickle\.loads?\(")),
        ("innerHTML assignment", re.compile(r"\.innerHTML\s*=")),
    )

    def analyze(self, scan: ProjectScan) -> DomainResult:
        findings: list[Finding] = []
        score = 100.0
        secret_hits = 0
        dangerous_hits = 0

        for f in scan.files:
            for i, line in enumerate(f.text.splitlines(), start=1):
                for label, pattern, severity in self._SECRET_PATTERNS:
                    if pattern.search(line):
                        secret_hits += 1
                        findings.append(Finding(
                            severity=severity, message=f"Possible hardcoded secret ({label}).",
                            file=f.relative_path, line=i,
                        ))
                for label, pattern in self._DANGEROUS_CALLS:
                    if pattern.search(line):
                        dangerous_hits += 1
                        findings.append(Finding(
                            severity="medium",
                            message=f"Use of `{label}` — review for injection risk.",
                            file=f.relative_path, line=i,
                        ))

        score -= min(50.0, 12.0 * secret_hits)
        score -= min(25.0, 5.0 * dangerous_hits)

        gitignore = next((f for f in scan.files if f.relative_path == ".gitignore"), None)
        if gitignore is None:
            findings.append(Finding(severity="medium", message="No .gitignore found."))
            score -= 5.0
        elif ".env" not in gitignore.text:
            findings.append(Finding(
                severity="medium", message="`.gitignore` does not exclude `.env` files.",
                file=".gitignore",
            ))
            score -= 5.0

        dockerfile = next((f for f in scan.files if f.relative_path.lower() == "dockerfile"), None)
        if dockerfile is not None and "USER " not in dockerfile.text:
            findings.append(Finding(
                severity="medium",
                message="Dockerfile never switches to a non-root `USER` — container runs as root.",
                file=dockerfile.relative_path,
            ))
            score -= 10.0

        metrics = {"secret_pattern_hits": secret_hits, "dangerous_call_hits": dangerous_hits}
        return DomainResult(self.domain, int(clamp(score)), "high", findings, metrics)


class ScalabilityAnalyzer:
    """Scores Scalability: loop-nesting depth, generator usage, blocking I/O in loops."""

    domain = "Scalability"
    _BLOCKING_IN_LOOP = re.compile(r"\b(requests\.(get|post|put|delete)|time\.sleep)\s*\(")

    def analyze(self, scan: ProjectScan) -> DomainResult:
        findings: list[Finding] = []
        score = 100.0
        deep_loops = 0
        generator_functions = 0
        list_building_functions = 0

        for rel, tree in scan.python_modules.items():
            for fn in _iter_functions(tree):
                depth = _max_loop_nesting(fn)
                if depth >= 2:
                    deep_loops += 1
                    findings.append(Finding(
                        severity="medium" if depth == 2 else "high",
                        message=f"`{fn.name}` nests loops {depth} deep — likely O(n^{depth + 1}) "
                                 "or worse; check the input sizes this runs against.",
                        file=rel, line=fn.lineno,
                    ))
                has_yield = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(fn))
                if has_yield:
                    generator_functions += 1
                elif self._returns_accumulated_list(fn):
                    list_building_functions += 1

            for node in ast.walk(tree):
                if isinstance(node, (ast.For, ast.AsyncFor)):
                    body_text = ast.unparse(node) if hasattr(ast, "unparse") else ""
                    if self._BLOCKING_IN_LOOP.search(body_text):
                        findings.append(Finding(
                            severity="medium",
                            message="Blocking network/sleep call inside a loop — "
                                     "consider batching or async I/O.",
                            file=rel, line=node.lineno,
                        ))

        score -= min(40.0, 10.0 * deep_loops)
        metrics = {
            "deep_loop_functions": deep_loops,
            "generator_functions": generator_functions,
            "list_accumulating_functions": list_building_functions,
        }
        if generator_functions + list_building_functions:
            gen_ratio = generator_functions / (generator_functions + list_building_functions)
            metrics["generator_usage_ratio"] = round(gen_ratio, 2)
            score -= (1.0 - gen_ratio) * 15.0

        return DomainResult(self.domain, int(clamp(score)), "medium", findings, metrics)

    @staticmethod
    def _returns_accumulated_list(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """True if the function builds a list via ``.append``/``.extend`` and returns it."""
        appends = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in {"append", "extend"}
            for n in ast.walk(fn)
        )
        returns_value = any(
            isinstance(n, ast.Return) and n.value is not None for n in ast.walk(fn)
        )
        return appends and returns_value


class TestingAnalyzer:
    """Scores Testing: test-to-source ratio, parsed coverage reports, and mock usage."""

    domain = "Testing"
    _TEST_NAME = re.compile(r"(^test_.*\.py$|.*_test\.py$|.*\.test\.[tj]sx?$|.*\.spec\.[tj]sx?$)")

    def analyze(self, scan: ProjectScan) -> DomainResult:
        findings: list[Finding] = []
        score = 100.0
        metrics: dict[str, Any] = {}

        code_languages = {"python", "typescript", "javascript"}
        source_files = [f for f in scan.files if f.language in code_languages]
        test_files = [f for f in source_files if self._TEST_NAME.match(Path(f.relative_path).name)]
        non_test_files = [f for f in source_files if f not in test_files]

        if non_test_files:
            ratio = len(test_files) / len(non_test_files)
            metrics["test_to_source_file_ratio"] = round(ratio, 2)
            if ratio < 0.3:
                findings.append(Finding(
                    severity="high",
                    message=f"Only {len(test_files)} test file(s) for {len(non_test_files)} "
                             "source file(s) — thin test coverage by file count.",
                ))
            score -= clamp((0.5 - min(ratio, 0.5)) * 100.0, 0.0, 40.0)
        elif not test_files:
            findings.append(Finding(severity="high", message="No source or test files detected."))

        coverage_percent = self._parse_coverage_xml(scan.root)
        if coverage_percent is not None:
            metrics["reported_coverage_percent"] = coverage_percent
            if coverage_percent < 60.0:
                findings.append(Finding(
                    severity="high",
                    message=f"Reported line coverage is {coverage_percent:.1f}% (< 60%).",
                ))
            score = clamp(0.5 * score + 0.5 * coverage_percent)

        mock_hits = 0
        for f in test_files:
            mock_hits += len(re.findall(r"unittest\.mock|mocker\.|jest\.mock|sinon\.", f.text))
        metrics["mock_usage_hits"] = mock_hits
        if test_files and mock_hits == 0 and len(source_files) > len(test_files):
            findings.append(Finding(
                severity="info",
                message="No mock/stub usage detected in tests — verify true I/O boundaries "
                         "aren't being hit in unit tests.",
            ))

        return DomainResult(self.domain, int(clamp(score)), "medium", findings, metrics)

    @staticmethod
    def _parse_coverage_xml(root: Path) -> float | None:
        """Parse a Cobertura-style ``coverage.xml``, if present, as an overall % line rate."""
        path = root / "coverage.xml"
        if not path.exists():
            return None
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return None
        line_rate = tree.getroot().attrib.get("line-rate")
        if line_rate is None:
            return None
        try:
            return float(line_rate) * 100.0
        except ValueError:
            return None


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


class AuditOrchestrator:
    """Runs every :class:`DomainAnalyzer` over one :class:`ProjectScan` and aggregates results."""

    def __init__(self, analyzers: list[DomainAnalyzer] | None = None) -> None:
        self._analyzers = analyzers or [
            ArchitectureAnalyzer(),
            CleanCodeAnalyzer(),
            DocumentationAnalyzer(),
            SecurityAnalyzer(),
            ScalabilityAnalyzer(),
            TestingAnalyzer(),
        ]

    def run(
        self,
        scan: ProjectScan,
        domain_weights: dict[str, float] | None = None,
        custom_rules: list[str] | None = None,
    ) -> AuditReport:
        """Execute all analyzers and fold their results into one weighted report.

        Algorithm: run every analyzer independently (as before), then resolve
        a weight per domain — ``domain_weights.get(name, 1.0)``, so a project
        overriding only one domain in ``ai-project-config.toml`` leaves every
        other domain at normal priority. The overall score is the weighted
        mean of the six domain scores, renormalized by the sum of weights so
        it stays on a 0-100 scale no matter how the weights are set; if every
        weight is ``0`` (every domain excluded), overall falls back to
        ``0.0`` instead of dividing by zero.
        """
        domains = {a.domain: a.analyze(scan) for a in self._analyzers}
        overrides = domain_weights or {}
        resolved_weights = {name: overrides.get(name, 1.0) for name in domains}
        weight_total = sum(resolved_weights.values())
        if weight_total > 0:
            weighted_sum = sum(d.score * resolved_weights[name] for name, d in domains.items())
            overall = weighted_sum / weight_total
        else:
            overall = 0.0
        return AuditReport(
            root=str(scan.root),
            generated_at=datetime.now(timezone.utc).isoformat(),
            stack=scan.stack,
            file_count=len(scan.files),
            domains=domains,
            overall_score=round(overall, 1),
            domain_weights=resolved_weights,
            custom_rules=list(custom_rules or []),
        )


def _print_summary(report: AuditReport) -> None:
    """Print a short human-readable score table to stdout, flagging non-default weights."""
    stack = ", ".join(report.stack) or "(none detected)"
    print(f"Audit: {report.root}")
    print(f"Stack: {stack}  ·  {report.file_count} files scanned")
    print(f"Overall score: {report.overall_score}/100\n")
    for name, result in report.domains.items():
        weight = report.domain_weights.get(name, 1.0)
        weight_note = f", weight {weight}" if weight != 1.0 else ""
        print(f"  {name:<24} {result.score:>3}/100  (confidence: {result.confidence}, "
              f"{len(result.findings)} findings{weight_note})")
    if report.custom_rules:
        print(f"\n{len(report.custom_rules)} custom rule(s) from {PROJECT_CONFIG_FILENAME} "
              "(apply during manual review):")
        for rule in report.custom_rules:
            print(f"  - {rule}")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: scan, audit, write JSON, print a summary."""
    parser = argparse.ArgumentParser(
        description="Collect repository-health signals for audit_repo."
    )
    parser.add_argument("--project", type=Path, default=Path("."), help="Project root to scan.")
    parser.add_argument("--output", type=Path, default=Path("audit_data.json"),
                         help="Where to write the JSON report.")
    parser.add_argument("--max-line-length", type=int, default=DEFAULT_LINE_LENGTH,
                         help="PEP 8 line-length threshold for the Clean Code domain.")
    parser.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES_DEFAULT,
                         help="Skip files larger than this many bytes.")
    args = parser.parse_args(argv)

    root = args.project.resolve()
    if not root.is_dir():
        parser.error(f"--project {root} is not a directory")

    scan = RepoScanner(max_file_bytes=args.max_file_bytes).scan(root)
    overrides = ProjectOverrides.load(root)
    orchestrator = AuditOrchestrator([
        ArchitectureAnalyzer(),
        CleanCodeAnalyzer(max_line_length=args.max_line_length),
        DocumentationAnalyzer(),
        SecurityAnalyzer(),
        ScalabilityAnalyzer(),
        TestingAnalyzer(),
    ])
    report = orchestrator.run(
        scan, domain_weights=overrides.domain_weights, custom_rules=overrides.custom_rules
    )

    args.output.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    _print_summary(report)
    print(f"\nWrote {args.output}")
    if scan.skipped:
        print(f"(skipped {len(scan.skipped)} file(s) — too large or unreadable)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
