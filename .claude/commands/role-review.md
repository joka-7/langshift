---
description: Run a full multi-role review over a target repo (local path or git URL) and produce a prioritized backlog
---
Run a multi-role review over the target given in $ARGUMENTS — a local path, or a git URL to clone. If $ARGUMENTS is empty, review the current directory.

Follow these steps in order. Do not skip step 7.

**1. Resolve the target.**
If $ARGUMENTS looks like a git URL, clone it shallow (`git clone --depth 50`) into a scratch workdir and use that as `<workdir>`. Otherwise treat it as a local path. Record the repo name and `git rev-parse --short HEAD` as `<short-sha>` — every report is stamped with it so findings stay pinned to a commit.

**2. Prepare the output directory.**
```
mkdir -p <workdir>/.ai-reviews
```
Then append `.ai-reviews/` to `<workdir>/.git/info/exclude` if it isn't already there. This keeps reports out of `git status` without touching the project's committed `.gitignore`. Confirm afterwards that `git status --short` in the target is clean — this order matters, since opening a run before the exclude is set would see `.ai-reviews/` itself as an untracked change.

Then open the run:
```
python .ai/skills/role_review/run_manifest.py --project <workdir> --begin --sha <short-sha>
```
If a prior run exists at a different sha, this archives its reports to `.ai-reviews/archive/<old-sha>/` before starting fresh. At the same sha it reuses the run — re-running the fan-out at one commit stays idempotent.

**3. Run the mechanical scan once.**
```
python .ai/skills/audit_repo/run_audit.py --project <workdir> --output <workdir>/.ai-reviews/audit_data.json
```
Once — not once per role. Every role reads its own domain slice from this file. If the script fails, continue anyway and tell the roles the file is absent; they degrade to unaided exploration.

**4. Fan out the reviewers in parallel.**
Launch `qa`, `architect`, `product`, `engineering-manager`, `sre`, `senior-dev`, and `ciso` **in a single message** so they run concurrently in isolated contexts. Sequential launches waste the whole design. Give each the same brief: the `<workdir>` absolute path, the repo name, `<short-sha>`, and the path to `audit_data.json`.

If $ARGUMENTS named a subset of roles, launch only those.

**5. Persist each report.**
The reviewers have no write access by design — they return their report as their final message. Write each verbatim to `<workdir>/.ai-reviews/<role>.md`. Do not edit, summarize, or reformat them on the way to disk; they are the evidence record the next step and the human both depend on. Record each as it lands:
```
python .ai/skills/role_review/run_manifest.py --project <workdir> --record <role>=<role>.md
```

**6. Aggregate.**
Launch `planner`. It reads the report files from disk and returns a prioritized backlog; write that to `<workdir>/.ai-reviews/BACKLOG.md`. Record it, along with the overall audit score from step 3:
```
python .ai/skills/role_review/run_manifest.py --project <workdir> --backlog BACKLOG.md --audit-score <overall-score>
```

**7. Stop and ask for approval.**
Present the backlog summary and the top items. Then state plainly that nothing has been changed, that the `developer` role has **not** run, and ask which items to implement.

**Never invoke `developer` in this command, under any circumstances** — not even if the findings look trivial, not even if the user's original request implied they want fixes. Approval is a separate human turn. If the user approves items afterwards, invoke `developer` then, scoped to exactly the items they named.

Report at the end: the files written, the overall audit score, the count of findings by severity, and anything that failed or was skipped. $ARGUMENTS
