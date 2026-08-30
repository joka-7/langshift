#!/usr/bin/env python3
"""gen_tree.py — render an annotated file tree of a repository into a docs file.

Algorithm overview
------------------
1. ``collect_paths`` asks git for the tracked file list (``git ls-files``), so
   whatever the repo already gitignores is excluded for free and no separate
   ignore-file parser has to exist. When the target is not a git checkout it
   falls back to an ``os.walk`` that prunes a fixed set of noise directories.
2. ``Annotator`` turns each path into the one-line "what is inside this file"
   note that makes a tree worth reading at all. It tries four sources in a fixed
   precedence: a hand-written override, YAML frontmatter ``description``, the
   first markdown heading, then a Python module docstring. The precedence
   matters more than the coverage — a human's override must always beat a
   heuristic, or the override file is pointless.
3. ``render_tree`` folds the flat path list into a nested mapping and draws it
   with box-drawing connectors, stopping at ``--max-depth`` and collapsing any
   directory wider than ``--max-entries`` into a "… N more" line, so the same
   script is usable on a 40-file config repo and a 4000-file application.
4. ``splice`` writes the rendered block between HTML marker comments in the
   target file, leaving hand-written prose around it untouched. The BEGIN marker
   is stamped with the parameters used to build it, which is what later lets
   ``--check`` rebuild any marked file without being told how it was made.

``--check`` regenerates every marked file in the project and exits non-zero with
a unified diff if any is stale. That is the entire reason this is generated
rather than hand-written: a directory map that no CI step verifies is a lie, and
this repo already carries four hand-maintained restatements of its own layout
that drifted independently.

Usage
-----
    python gen_tree.py --project . --output docs/STRUCTURE.md
    python gen_tree.py --project . --output README.md --max-depth 1
    python gen_tree.py --project . --check
"""

from __future__ import annotations

import argparse
import ast
import difflib
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

MARKER_BEGIN_RE = re.compile(r"<!-- BEGIN GENERATED TREE(?: \(([^)]*)\))? -->")
MARKER_END = "<!-- END GENERATED TREE -->"

#: Directories never worth mapping. Only consulted in the non-git fallback path;
#: a real checkout gets this for free from .gitignore via ``git ls-files``.
PRUNE_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", "target", ".next", ".nuxt", "coverage", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vscode", ".gradle",
})

#: Overrides live here so a file that cannot describe itself (LICENSE, a
#: template, a lockfile) still gets a human line, and keeps it across every
#: regeneration.
OVERRIDES_FILE = "docs/.structure-notes.toml"

MAX_NOTE_CHARS = 78
MAX_FILE_BYTES = 400_000

DEFAULT_STRUCTURE_HEADER = """# Repository structure

Every file in this repo and what is inside it. The tree below is **generated** —
run `python .ai/skills/repo_tree/gen_tree.py --project . --output docs/STRUCTURE.md`
to refresh it, and never edit between the markers by hand.

<!-- BEGIN GENERATED TREE -->
<!-- END GENERATED TREE -->
"""


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def _git_ls_files(root: Path) -> list[str] | None:
    """Return git's tracked-file list, or None if this isn't a usable checkout.

    Preferred over walking because it honours .gitignore, nested ignore files,
    and per-repo excludes without this script reimplementing any of them.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, check=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    entries = [e.decode("utf-8", "replace") for e in result.stdout.split(b"\0") if e]
    return sorted(entries)


def _walk(root: Path) -> list[str]:
    """Fallback discovery for a non-git directory: walk, pruning noise dirs."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNE_DIRS)
        for name in sorted(filenames):
            rel = Path(dirpath, name).relative_to(root).as_posix()
            found.append(rel)
    return sorted(found)


def collect_paths(root: Path) -> list[str]:
    """Every file worth putting in the map, as repo-relative POSIX paths."""
    tracked = _git_ls_files(root)
    return tracked if tracked is not None else _walk(root)


# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------

def _trim(note: str, *, shorten: bool = True) -> str:
    """Collapse a note to one clean line, cut at a word boundary if too long.

    ``shorten`` is False for hand-written overrides: someone chose those words,
    so only the hard length cap applies. Derived notes get the extra clause-level
    trim because a pushy skill ``description`` is a paragraph, not a label.
    """
    note = " ".join(note.split())
    note = note.replace("``", "`")
    if shorten:
        for stop in (". ", " — ", "; "):
            head, sep, _ = note.partition(stop)
            if sep and len(head) >= 20:
                note = head
                break
    if len(note) > MAX_NOTE_CHARS:
        note = note[:MAX_NOTE_CHARS].rsplit(" ", 1)[0] + "…"
    # Docstrings conventionally open lowercase ("collect signals for…"); the tree
    # reads as a list of sentences, so lift the first letter.
    return note[:1].upper() + note[1:] if note else note


def load_overrides(root: Path) -> dict[str, str]:
    """Read hand-written notes from docs/.structure-notes.toml, if present.

    A missing or malformed override file degrades to "no overrides" rather than
    failing the whole generation — the tree is still worth having without them.
    """
    path = root / OVERRIDES_FILE
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    notes = data.get("notes", {})
    if not isinstance(notes, dict):
        return {}
    return {str(k): str(v) for k, v in notes.items() if isinstance(v, str)}


@dataclass
class Annotator:
    """Resolves the one-line note for a path, honouring source precedence."""

    root: Path
    overrides: dict[str, str]

    def note_for(self, rel: str, is_dir: bool) -> str:
        override = self.overrides.get(rel) or self.overrides.get(rel + "/")
        if override:
            return _trim(override, shorten=False)
        if is_dir:
            return self._dir_note(rel)
        return self._file_note(rel)

    def _dir_note(self, rel: str) -> str:
        """A directory describes itself through its own README, or not at all."""
        readme = self.root / rel / "README.md"
        if readme.is_file():
            return self._heading(_read_text(readme)) or ""
        return ""

    def _file_note(self, rel: str) -> str:
        path = self.root / rel
        text = _read_text(path)
        if not text:
            return ""
        if rel.endswith(".md"):
            return self._frontmatter(text) or self._heading(text) or ""
        if rel.endswith(".py") or _has_python_shebang(text):
            return self._module_docstring(text) or ""
        return ""

    @staticmethod
    def _frontmatter(text: str) -> str:
        """Pull `description:` out of YAML frontmatter (skills, agents, commands)."""
        if not text.startswith("---"):
            return ""
        _, _, rest = text.partition("\n")
        block, sep, _ = rest.partition("\n---")
        if not sep:
            return ""
        match = re.search(r"^description:\s*(.+?)$", block, re.M)
        return _trim(match.group(1).strip().strip("\"'")) if match else ""

    @staticmethod
    def _heading(text: str) -> str:
        """First markdown heading — rule fragments open with `## <Title>`."""
        match = re.search(r"^#{1,3}\s+(.+?)\s*$", text, re.M)
        return _trim(match.group(1)) if match else ""

    @staticmethod
    def _module_docstring(text: str) -> str:
        """First line of the module docstring, minus a leading `name.py — ` stem."""
        try:
            doc = ast.get_docstring(ast.parse(text))
        except (SyntaxError, ValueError, RecursionError):
            return ""
        if not doc:
            return ""
        first = doc.strip().splitlines()[0]
        _, sep, tail = first.partition(" — ")
        return _trim(tail if sep else first)


def _has_python_shebang(text: str) -> bool:
    """True for an extensionless Python script — `bin/ai-sync` is one."""
    first = text.split("\n", 1)[0]
    return first.startswith("#!") and "python" in first


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Tree rendering
# ---------------------------------------------------------------------------

def build_index(paths: list[str]) -> dict:
    """Fold flat relative paths into nested dicts; a file maps to an empty dict."""
    root: dict = {}
    for rel in paths:
        node = root
        for part in rel.split("/"):
            node = node.setdefault(part, {})
    return root


@dataclass
class TreeRenderer:
    """Draws the nested index, collecting (label, note) rows to align later."""

    annotator: Annotator
    max_depth: int | None
    max_entries: int | None

    def rows(self, index: dict) -> list[tuple[str, str]]:
        collected: list[tuple[str, str]] = []
        self._walk(index, prefix="", rel="", depth=1, out=collected)
        return collected

    def _walk(self, node: dict, prefix: str, rel: str, depth: int,
              out: list[tuple[str, str]]) -> None:
        dirs = sorted(k for k, v in node.items() if v)
        files = sorted(k for k, v in node.items() if not v)
        entries = dirs + files

        shown = entries
        hidden = 0
        if self.max_entries is not None and len(entries) > self.max_entries:
            shown = entries[: self.max_entries]
            hidden = len(entries) - self.max_entries

        for position, name in enumerate(shown):
            is_last = position == len(shown) - 1 and hidden == 0
            connector = "└── " if is_last else "├── "
            child_rel = f"{rel}/{name}" if rel else name
            is_dir = bool(node[name])
            label = f"{prefix}{connector}{name}{'/' if is_dir else ''}"
            out.append((label, self.annotator.note_for(child_rel, is_dir)))

            if not is_dir:
                continue
            if self.max_depth is not None and depth >= self.max_depth:
                continue
            self._walk(
                node[name],
                prefix=prefix + ("    " if is_last else "│   "),
                rel=child_rel,
                depth=depth + 1,
                out=out,
            )

        if hidden:
            out.append((f"{prefix}└── … {hidden} more", ""))


def render_block(root: Path, max_depth: int | None, max_entries: int | None) -> str:
    """The full fenced tree, ready to sit between the markers."""
    annotator = Annotator(root=root, overrides=load_overrides(root))
    index = build_index(collect_paths(root))
    rows = TreeRenderer(annotator, max_depth, max_entries).rows(index)

    width = max((len(label) for label, note in rows if note), default=0)
    width = min(width, 48)

    lines = ["```text", f"{root.name}/"]
    for label, note in rows:
        lines.append(f"{label.ljust(width)}  # {note}".rstrip() if note else label)
    lines.append("```")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Splicing into a target file
# ---------------------------------------------------------------------------

def format_params(max_depth: int | None, max_entries: int | None) -> str:
    depth = "all" if max_depth is None else str(max_depth)
    entries = "all" if max_entries is None else str(max_entries)
    return f"depth={depth} entries={entries}"


def parse_params(raw: str | None) -> tuple[int | None, int | None]:
    """Read back the parameters stamped into a BEGIN marker.

    An unreadable or absent stamp means "the defaults", never a crash — a file
    someone marked up by hand should still check, using full depth.
    """
    values: dict[str, str] = {}
    for token in (raw or "").split():
        key, sep, value = token.partition("=")
        if sep:
            values[key] = value

    def as_limit(key: str) -> int | None:
        value = values.get(key, "all")
        if value == "all":
            return None
        try:
            return max(1, int(value))
        except ValueError:
            return None

    return as_limit("depth"), as_limit("entries")


def splice(text: str, block: str, params: str) -> str:
    """Replace the marked region, preserving every byte outside it."""
    begin = MARKER_BEGIN_RE.search(text)
    if begin is None:
        raise ValueError(
            "no '<!-- BEGIN GENERATED TREE -->' marker in the target file — add "
            "the BEGIN/END marker pair where the tree should go, then re-run"
        )
    end_index = text.find(MARKER_END, begin.end())
    if end_index == -1:
        raise ValueError("found a BEGIN marker but no matching END marker")

    head = text[: begin.start()]
    tail = text[end_index + len(MARKER_END):]
    return (
        f"{head}<!-- BEGIN GENERATED TREE ({params}) -->\n"
        f"{block}\n{MARKER_END}{tail}"
    )


def regenerate(root: Path, target: Path) -> tuple[str, str]:
    """Return (current_text, refreshed_text) for one already-marked file."""
    current = target.read_text(encoding="utf-8")
    begin = MARKER_BEGIN_RE.search(current)
    max_depth, max_entries = parse_params(begin.group(1) if begin else None)
    block = render_block(root, max_depth, max_entries)
    return current, splice(current, block, format_params(max_depth, max_entries))


def marked_files(root: Path) -> list[Path]:
    """Every tracked markdown file carrying a well-formed generated-tree block.

    A complete BEGIN/END pair is required, not just a BEGIN. Docs that *describe*
    this convention (DESIGN.md, a SKILL.md) mention the marker in prose, and a
    lone match there must not be mistaken for a block to rewrite — nor crash the
    whole check. `docs/STRUCTURE.md` losing its END marker is caught by
    `tests/test_structure_doc.py` instead, where it belongs.
    """
    hits: list[Path] = []
    for rel in collect_paths(root):
        if not rel.endswith(".md"):
            continue
        path = root / rel
        text = _read_text(path)
        begin = MARKER_BEGIN_RE.search(text)
        if begin and text.find(MARKER_END, begin.end()) != -1:
            hits.append(path)
    return hits


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_check(root: Path) -> int:
    targets = marked_files(root)
    if not targets:
        print("no files with a generated-tree block found — nothing to check")
        return 0

    stale: list[Path] = []
    for target in targets:
        current, refreshed = regenerate(root, target)
        if current == refreshed:
            continue
        stale.append(target)
        rel = target.relative_to(root).as_posix()
        diff = difflib.unified_diff(
            current.splitlines(keepends=True), refreshed.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}",
        )
        sys.stdout.writelines(diff)

    if stale:
        names = ", ".join(p.relative_to(root).as_posix() for p in stale)
        print(f"\nstale generated tree in: {names}", file=sys.stderr)
        print("refresh with: python .ai/skills/repo_tree/gen_tree.py "
              "--project . --output <file>", file=sys.stderr)
        return 1

    print(f"ok — {len(targets)} generated tree(s) current")
    return 0


def run_write(root: Path, output: Path, max_depth: int | None,
              max_entries: int | None) -> int:
    if not output.exists():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(DEFAULT_STRUCTURE_HEADER, encoding="utf-8")
        print(f"created {output}")

    block = render_block(root, max_depth, max_entries)
    current = output.read_text(encoding="utf-8")
    try:
        refreshed = splice(current, block, format_params(max_depth, max_entries))
    except ValueError as exc:
        print(f"error: {output}: {exc}", file=sys.stderr)
        return 2

    if refreshed == current:
        print(f"ok (already current) {output}")
        return 0
    output.write_text(refreshed, encoding="utf-8")
    print(f"wrote {output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", default=".", help="repository root (default: .)")
    parser.add_argument("--output", default="docs/STRUCTURE.md",
                        help="file whose marked block to refresh (default: docs/STRUCTURE.md)")
    parser.add_argument("--max-depth", type=int, default=None,
                        help="deepest directory level to render (default: unlimited)")
    parser.add_argument("--max-entries", type=int, default=None,
                        help="collapse directories with more entries than this")
    parser.add_argument("--check", action="store_true",
                        help="verify every marked file is current; exit 1 with a diff if not")
    args = parser.parse_args(argv)

    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    if args.check:
        return run_check(root)
    return run_write(root, root / args.output, args.max_depth, args.max_entries)


if __name__ == "__main__":
    raise SystemExit(main())
