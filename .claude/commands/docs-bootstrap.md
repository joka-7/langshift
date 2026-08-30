---
description: Create a target repo's standard doc set from scratch — annotated repo tree, README structure section, HLD and LLD — for a repo that doesn't have them yet
---
Bring the target given in $ARGUMENTS up to the standard doc set: `README.md` (with a repo tree), `docs/STRUCTURE.md`, `docs/HLD.md`, `docs/LLD.md`. A local path, or a git URL to clone; if $ARGUMENTS is empty, use the current directory.

This **creates** a doc set that doesn't exist yet. `/sync-docs` is what keeps it current afterwards — use that instead if the repo already has these files and they've just drifted.

**1. Resolve the target.**
As in `/role-review`: clone a git URL shallow into a scratch workdir if $ARGUMENTS looks like one, otherwise treat it as a local path. Record the repo name and `<short-sha>`.

**2. Require a clean worktree.**
Run `git status --short` in `<workdir>`. If it is not empty, stop and say so — this command writes several files and the diff has to be attributable to this run, the same requirement `/sync-docs` and `/role-implement` hold.

**3. Survey before writing.**
Run the audit collector once and read its Documentation domain to see what's actually missing, rather than assuming:
```
python .ai/skills/audit_repo/run_audit.py --project <workdir> --output <workdir>/.ai-reviews/audit_data.json
```
`metrics.doc_set_present` names which of the four pieces exist. Only create what's absent; if a file exists, leave it and note it as skipped. Never overwrite someone's hand-written README.

**4. Generate the maps.** Load the `repo-tree` skill and follow it. Stage new files first (`git add -A`) so `git ls-files` sees them, then:
```
python .ai/skills/repo_tree/gen_tree.py --project <workdir> --output docs/STRUCTURE.md
```
For a large repo, bound it with `--max-depth 4 --max-entries 25`. Then read the generated tree and write `docs/.structure-notes.toml` entries for every file whose note came out blank or useless, and regenerate. This pass is the whole value of the map — a tree of bare filenames helps nobody.

Add a `## Repo structure` section to `README.md` with the marker pair, then:
```
python .ai/skills/repo_tree/gen_tree.py --project <workdir> --output README.md --max-depth 1
```

**5. Write the design docs.** Load the `write-design-doc` skill in **document mode** and follow it for `docs/HLD.md` and `docs/LLD.md`. Every claim traces to real code — that skill's rule, and it holds here. Its HLD "Static view" section should link to `docs/STRUCTURE.md` rather than restate the file list. Diagrams are mermaid.

**6. Finish the README** if it's thin: what this is, why it exists, how to run it and test it, and links into `docs/`. Don't invent features — describe what you read.

**7. Verify before reporting.**
```
python .ai/skills/repo_tree/gen_tree.py --project <workdir> --check
```
Must exit 0. Then re-run the audit collector from step 3 and confirm the Documentation domain improved.

**8. Persist.**
Write a report to `<workdir>/.ai-reviews/docs-bootstrap.md` — what was created, what was skipped as already present, and any `## Open questions` the design docs left — then:
```
python .ai/skills/role_review/run_manifest.py --project <workdir> --record docs-bootstrap=docs-bootstrap.md
```

**9. Never commit or push.** Report `git diff --stat` and the list of new files. The doc set is reviewed by a human before it lands — a generated map is only as good as the notes, and those need your reader's eye.

$ARGUMENTS
