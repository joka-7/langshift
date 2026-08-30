---
description: Sync a target repo's in-code documentation and Confluence space with the current code via the docs-sync role
---
Run the `docs-sync` role against the target given in $ARGUMENTS — a local path, or a git URL to clone. If $ARGUMENTS is empty, use the current directory. Use this after implementing changes (`/role-implement` or otherwise) to catch documentation drift before it accumulates.

**1. Resolve the target.**
As in `/role-review`: clone a git URL shallow into a scratch workdir if $ARGUMENTS looks like one, otherwise treat it as a local path. Record the repo name and `<short-sha>`.

**2. Require a clean worktree.**
Run `git status --short` in `<workdir>`. If it is not empty, stop and say so — `docs-sync`'s diff needs to be attributable to this run, not tangled with pre-existing changes, the same requirement `/role-implement` holds for `developer`.

**3. Launch `docs-sync`.**
Give it the workdir path, repo name, and `<short-sha>`. It reads recent git history, `developer.md` and `BACKLOG.md` if present, updates documentation-shaped files in the repo directly, and works Confluence via the Atlassian MCP tools where connected.

**4. Persist.**
Write the returned sync report verbatim to `<workdir>/.ai-reviews/docs-sync.md`, then:
```
python .ai/skills/role_review/run_manifest.py --project <workdir> --record docs-sync=docs-sync.md
```

**5. Never commit or push.** That is `docs-sync`'s own rule and this command does not override it. Report the diffstat (`git diff --stat` in `<workdir>`) for the in-repo half, and which Confluence pages were touched for the other half.

$ARGUMENTS
