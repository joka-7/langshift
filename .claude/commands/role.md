---
description: Run a single role agent (qa, architect, product, engineering-manager, sre, senior-dev, ciso) against a target repo
---
Run one role review. $ARGUMENTS is `<role> [path-or-url]` — the role name first, then an optional target; default the target to the current directory.

Valid roles: `qa`, `architect`, `product`, `engineering-manager`, `sre`, `senior-dev`, `ciso`. If the named role isn't one of these, say so and list the valid ones rather than guessing at the closest match.

`planner` is not directly invocable here — use `/role-backlog` to aggregate reports that already exist on disk. `developer` is not invocable here either; use `/role-implement` after a human approves a backlog.

**Steps:**

1. Resolve the target as in `/role-review`: clone a git URL shallow into a workdir, or use the local path. Record the repo name and `<short-sha>`.
2. `mkdir -p <workdir>/.ai-reviews` and add `.ai-reviews/` to `<workdir>/.git/info/exclude` if absent. Then:
   ```
   python .ai/skills/role_review/run_manifest.py --project <workdir> --begin --sha <short-sha>
   ```
   This reuses the current run if HEAD hasn't moved since the last review, or archives the prior run's reports before starting fresh.
3. Check for `<workdir>/.ai-reviews/audit_data.json`. If it's missing, or its `generated_at` predates the current HEAD commit, regenerate it:
   ```
   python .ai/skills/audit_repo/run_audit.py --project <workdir> --output <workdir>/.ai-reviews/audit_data.json
   ```
   Otherwise reuse it — it's shared across roles and re-scanning gains nothing.
4. Launch the named role with the workdir path, repo name, `<short-sha>`, and the `audit_data.json` path.
5. Write its returned report verbatim to `<workdir>/.ai-reviews/<role>.md`, then:
   ```
   python .ai/skills/role_review/run_manifest.py --project <workdir> --record <role>=<role>.md
   ```

Report the file written and a one-line summary of what the role found. Note that this is a single lens — mention `/role-review` if the user likely wants the full pass and the deduplicated backlog. $ARGUMENTS
