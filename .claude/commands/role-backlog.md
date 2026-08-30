---
description: Re-aggregate the role reports already on disk into a fresh prioritized backlog, without re-running the reviewers
---
Run the `planner` role alone over the reports already sitting in `<workdir>/.ai-reviews/` for the target given in $ARGUMENTS — a local path, or a git URL to clone. If $ARGUMENTS is empty, use the current directory. Use this after re-running a single role with `/role` and wanting the backlog to reflect it, without paying for a full `/role-review` pass.

**1. Resolve the target.**
As in `/role-review`: clone a git URL shallow into a scratch workdir if $ARGUMENTS looks like one, otherwise treat it as a local path. Record the repo name and `<short-sha>`.

**2. Require reports to aggregate.**
Check `<workdir>/.ai-reviews/` for at least two `<role>.md` files. If fewer than two exist, stop and say so — point at `/role-review` to produce a full set first. Aggregating one report is not the point of this command.

**3. Check currency.**
```
python .ai/skills/role_review/run_manifest.py --project <workdir> --status
```
If this reports the reports as stale (exit code 1), say so plainly — name the sha the reports describe and the current HEAD — but proceed anyway; a stale re-aggregation of what's on disk is still what the user asked for. Do not regenerate anything yourself.

**4. Aggregate.**
Launch `planner` with the workdir path, repo name, `<short-sha>`, and the list of report files found in step 2. It reads them from disk and returns a prioritized backlog.

**5. Persist.**
Write the backlog verbatim to `<workdir>/.ai-reviews/BACKLOG.md`, overwriting any prior one. Then:
```
python .ai/skills/role_review/run_manifest.py --project <workdir> --backlog BACKLOG.md
```

**6. Stop and ask for approval.**
Present the backlog summary and the top items. State plainly that nothing has been changed and that the `developer` role has **not** run. `planner` is not invocable directly outside this command and `/role-review`; `developer` is not invocable here either — use `/role-implement` once specific items are approved by name.

Report the file written, which reports were aggregated, and anything skipped. $ARGUMENTS
