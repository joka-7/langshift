#!/usr/bin/env python3
"""run_manifest.py — track role-review runs in a target repo's .ai-reviews/ directory.

Why this exists
---------------
Reports in ``.ai-reviews/`` are pinned to a commit: every role stamps its report
with ``<short-sha>``, and a finding's ``file:line`` is only meaningful against the
code that produced it. Without a ledger, re-running a review after new commits
silently overwrites the old reports, and nothing downstream can tell whether
``BACKLOG.md`` describes the working tree in front of you or one from last week.

This script keeps ``.ai-reviews/manifest.json`` so that:

* ``--begin`` archives the previous run's reports under ``archive/<old-sha>/``
  when HEAD has moved, and reuses the current run when it has not, so re-running
  a review at one commit stays idempotent the way ``ai-sync`` re-runs do.
* ``--record`` notes which role reports were actually persisted.
* ``--status`` tells a command whether the reports on disk still describe HEAD,
  via its exit code, so ``/role-implement`` can refuse to act on a stale backlog.

Determinism is the point: the orchestrating agent would otherwise hand-write JSON
and compare timestamps by eye.

Usage
-----
    python run_manifest.py --project /path/to/repo --begin
    python run_manifest.py --project /path/to/repo --record qa=qa.md --record ciso=ciso.md
    python run_manifest.py --project /path/to/repo --backlog BACKLOG.md --audit-score 72
    python run_manifest.py --project /path/to/repo --status

Exit codes for --status: 0 reports match HEAD, 1 reports are stale, 2 no run recorded.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REVIEWS_DIRNAME = ".ai-reviews"
MANIFEST_NAME = "manifest.json"
ARCHIVE_DIRNAME = "archive"
SCHEMA_VERSION = 1

# Everything a run produces. manifest.json and archive/ are excluded: the ledger
# outlives the runs it describes, and archived reports are already filed.
ARTIFACT_GLOBS = ("*.md", "audit_data.json")

STATUS_CURRENT = 0
STATUS_STALE = 1
STATUS_NO_RUN = 2


def git(project: Path, *args: str) -> str | None:
    """Run a read-only git command in `project`, or return None if it is not a repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def head_sha(project: Path) -> str | None:
    return git(project, "rev-parse", "--short", "HEAD") or None


def is_dirty(project: Path) -> bool | None:
    status = git(project, "status", "--porcelain")
    if status is None:
        return None
    return bool(status.strip())


def load_manifest(reviews: Path) -> dict:
    path = reviews / MANIFEST_NAME
    if not path.exists():
        return {"schema": SCHEMA_VERSION, "repo": reviews.parent.name, "runs": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A corrupt ledger must not block a review. Start a fresh one and say so.
        print(f"  ! {path} is unreadable — starting a new manifest", file=sys.stderr)
        return {"schema": SCHEMA_VERSION, "repo": reviews.parent.name, "runs": []}
    data.setdefault("runs", [])
    return data


def save_manifest(reviews: Path, manifest: dict) -> None:
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def latest_run(manifest: dict) -> dict | None:
    runs = manifest.get("runs", [])
    return runs[-1] if runs else None


def archive_run(reviews: Path, sha: str) -> list[str]:
    """Move the current run's artifacts under archive/<sha>/. Returns what moved."""
    destination = reviews / ARCHIVE_DIRNAME / sha
    destination.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for pattern in ARTIFACT_GLOBS:
        for artifact in sorted(reviews.glob(pattern)):
            if not artifact.is_file():
                continue
            target = destination / artifact.name
            if target.exists():
                target.unlink()
            shutil.move(str(artifact), str(target))
            moved.append(artifact.name)
    return moved


def begin(project: Path, reviews: Path, sha: str | None) -> int:
    """Open a run for `sha`, archiving the previous run if HEAD has moved."""
    resolved = sha or head_sha(project) or "unknown"
    manifest = load_manifest(reviews)
    previous = latest_run(manifest)

    if previous is not None and previous.get("sha") == resolved:
        print(f"reusing run {resolved} (HEAD unchanged; reports left in place)")
        save_manifest(reviews, manifest)
        return 0

    if previous is not None:
        moved = archive_run(reviews, str(previous.get("sha", "unknown")))
        if moved:
            print(f"archived {len(moved)} file(s) to "
                  f"{ARCHIVE_DIRNAME}/{previous.get('sha')}/: {', '.join(moved)}")

    manifest["runs"].append({
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha": resolved,
        "dirty": is_dirty(project),
        "audit_score": None,
        "roles": {},
        "backlog": None,
    })
    save_manifest(reviews, manifest)
    print(f"began run {resolved}")
    return 0


def record(reviews: Path, roles: list[str], backlog: str | None,
           audit_score: int | None) -> int:
    manifest = load_manifest(reviews)
    run = latest_run(manifest)
    if run is None:
        print("  ! no open run — call --begin first", file=sys.stderr)
        return 1

    for pair in roles:
        name, _, relpath = pair.partition("=")
        if not name or not relpath:
            print(f"  ! ignoring malformed --record '{pair}' (expected role=file)",
                  file=sys.stderr)
            continue
        run["roles"][name] = relpath
    if backlog is not None:
        run["backlog"] = backlog
    if audit_score is not None:
        run["audit_score"] = audit_score

    save_manifest(reviews, manifest)
    recorded = ", ".join(sorted(run["roles"])) or "none"
    print(f"run {run['sha']}: roles={recorded} backlog={run['backlog']}")
    return 0


def status(project: Path, reviews: Path) -> int:
    manifest = load_manifest(reviews)
    run = latest_run(manifest)
    current = head_sha(project) or "unknown"
    print(f"HEAD: {current}")

    if run is None:
        print("reports: none recorded — run /role-review first")
        return STATUS_NO_RUN

    print(f"reports: {run['sha']} ({run['started_at']})")
    print(f"roles: {', '.join(sorted(run['roles'])) or 'none'}")
    print(f"backlog: {run['backlog'] or 'none'}")
    if run.get("dirty"):
        print("note: the working tree was dirty when this run started")

    if run["sha"] != current:
        print(f"STALE: reports describe {run['sha']}, HEAD is now {current}. "
              "Findings may cite lines that have moved.")
        return STATUS_STALE
    print("current: reports describe HEAD")
    return STATUS_CURRENT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Track role-review runs in a target repo's .ai-reviews/ directory."
    )
    parser.add_argument("--project", type=Path, default=Path("."),
                        help="Target repo root (default: cwd).")
    parser.add_argument("--begin", action="store_true",
                        help="Open a run, archiving the previous one if HEAD has moved.")
    parser.add_argument("--sha", help="Override the short sha (default: git rev-parse HEAD).")
    parser.add_argument("--record", action="append", default=[], metavar="ROLE=FILE",
                        help="Note a persisted role report. Repeatable.")
    parser.add_argument("--backlog", metavar="FILE", help="Note the persisted backlog file.")
    parser.add_argument("--audit-score", type=int, help="Note the overall audit score.")
    parser.add_argument("--status", action="store_true",
                        help="Report whether the reports on disk still describe HEAD.")
    args = parser.parse_args(argv)

    project = args.project.resolve()
    if not project.is_dir():
        parser.error(f"--project {project} is not a directory")
    reviews = project / REVIEWS_DIRNAME

    if not (args.begin or args.status or args.record or args.backlog
            or args.audit_score is not None):
        parser.error("nothing to do — pass --begin, --record, --backlog, "
                     "--audit-score, or --status")

    if args.begin:
        code = begin(project, reviews, args.sha)
        if code != 0:
            return code
    if args.record or args.backlog or args.audit_score is not None:
        code = record(reviews, args.record, args.backlog, args.audit_score)
        if code != 0:
            return code
    if args.status:
        return status(project, reviews)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
