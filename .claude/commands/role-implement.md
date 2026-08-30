---
description: Implement specific, already-approved items from .ai-reviews/BACKLOG.md by launching the developer role — the only command that may invoke it
---
$ARGUMENTS is `<items> [path-or-url]` — a comma-separated list of backlog row numbers (`1,3`) and/or finding IDs (`SEC-02`), then an optional target; default the target to the current directory. This command exists so that giving `developer` write access to a repo is always one explicit, auditable human turn — never an inference from a review's findings.

Follow these steps in order. Do not skip step 2.

**1. Resolve the target and require a backlog.**
As in `/role-review`: clone a git URL shallow into a workdir if the target looks like one, otherwise treat it as a local path. Record the repo name and `<short-sha>`. If `<workdir>/.ai-reviews/BACKLOG.md` does not exist, stop and say so — point at `/role-review` or `/role-backlog` to produce one first. There is nothing to approve without it.

**2. Refuse an empty item list.**
If $ARGUMENTS names no items — the whole string was just a path/URL, or nothing at all — print the backlog table from `BACKLOG.md` and ask which rows or finding IDs to implement. **Do not infer scope. Do not pick "the critical ones." Do not implement the whole backlog.** A human naming items by number or ID is the approval; anything else is you deciding on their behalf, which is exactly what this command exists to prevent.

**3. Verify the backlog is current.**
```
python .ai/skills/role_review/run_manifest.py --project <workdir> --status
```
If this reports the backlog as stale (exit code 1), tell the user the backlog describes an older commit than HEAD and ask them to confirm before proceeding — a `file:line` in a stale finding may no longer point at the same code. Proceed only on explicit confirmation, or if the user names items and reconfirms the same items already.

**4. Require a clean worktree.**
Run `git status --short` in `<workdir>`. If it is not empty, stop and say so — `developer`'s diff needs to be attributable to this run, not tangled with pre-existing changes. Point at whatever is dirty and ask the user to stash, commit, or discard it first.

**5. Echo the resolved scope before launching anything.**
Look up each named row/ID in `BACKLOG.md` and print the resolved item text back to the user as the scope about to be implemented. If an item name doesn't resolve to a real row, say which one and stop rather than guessing at the nearest match.

**6. Launch `developer`.**
Give it the workdir path, `<short-sha>`, and *only* the resolved item text from step 5 — not the rest of the backlog, and not anything noticed along the way. `developer` implements exactly what was approved.

**7. Persist.**
Write its returned summary verbatim to `<workdir>/.ai-reviews/developer.md`, then:
```
python .ai/skills/role_review/run_manifest.py --project <workdir> --record developer=developer.md
```

**8. Never commit or push.** That is `developer`'s own rule and this command does not override it. Report the diffstat (`git diff --stat` in `<workdir>`) and suggest `/role qa <path>` to re-verify before anyone commits. If the target has a Jira or Confluence integration configured, also suggest `/sync-tracker` (to transition the implemented items' tickets) and `/sync-docs` (to catch documentation drift the implementation introduced) as natural next steps — neither runs automatically.

**Never invoke `developer` without having completed steps 1–5 first, under any circumstances** — not for a request that only names a path, not because the backlog "looks obviously right." This is the one command in the role-review layer permitted to invoke it, and that permission exists only because every step above already happened.

$ARGUMENTS
