---
description: Sync a target repo's review backlog and implementation status to Jira via the tracker role
---
Run the `tracker` role against the target given in $ARGUMENTS — a local path, or a git URL to clone. If $ARGUMENTS is empty, use the current directory. Use this after `/role-review` produces a backlog, or after `/role-implement` finishes and Jira should reflect what actually got built.

**1. Resolve the target.**
As in `/role-review`: clone a git URL shallow into a scratch workdir if $ARGUMENTS looks like one, otherwise treat it as a local path. Record the repo name and `<short-sha>`.

**2. Require a backlog.**
Check for `<workdir>/.ai-reviews/BACKLOG.md`. If it doesn't exist, stop and say so — point at `/role-review` or `/role-backlog` to produce one first. There is nothing to sync from without it.

**3. Launch `tracker`.**
Give it the workdir path, repo name, and `<short-sha>`. It reads `BACKLOG.md`, `developer.md` if present, and `manifest.json`, then works Jira via the Atlassian MCP tools.

**4. Persist.**
Write the returned sync report verbatim to `<workdir>/.ai-reviews/tracker.md`, then:
```
python .ai/skills/role_review/run_manifest.py --project <workdir> --record tracker=tracker.md
```

**5. Report.**
Summarize what was created, updated, transitioned, and skipped. If `tracker` reported the Atlassian MCP tools were unavailable, say that plainly rather than presenting an empty sync as success. Suggest re-running this after a future `/role-implement` pass to keep ticket status current — this command does not run itself.

$ARGUMENTS
