---
name: repo-tree
description: Generate or refresh a repository's annotated file tree — the map of which files exist and what is inside each one — into docs/STRUCTURE.md and a short version in README.md, and verify an existing map hasn't drifted from the real tree. Use whenever the user asks for a repo tree, a directory map, a file structure doc, "what files are in this repo", "document the layout", or when a STRUCTURE.md needs regenerating after files were added, renamed, or deleted.
---

# Repo Tree

Produce the answer to the question no other doc in a repo answers: **which file do I open?**
`HLD.md` says how the system is shaped, `LLD.md` how a function behaves, `INVENTORY.md` what
each unit does — none of them lists the files.

The tree is **generated, not written**. A directory map that nothing verifies drifts within
weeks and then actively misleads; that is exactly what happened to the four hand-written
layout lists this skill replaced in `ogen-ai` itself.

## Steps

1. **Generate the full map**, creating `docs/STRUCTURE.md` with a default header if absent:

   ```bash
   python .ai/skills/repo_tree/gen_tree.py --project <path> --output docs/STRUCTURE.md
   ```

   For a large application repo, bound it: `--max-depth 4 --max-entries 25`. The parameters
   are stamped into the BEGIN marker, so later `--check` runs rebuild the block the same way
   without being told.

2. **Generate the short map for the README.** Add the marker pair where the tree belongs —
   conventionally a `## Repo structure` section after the intro — then:

   ```bash
   python .ai/skills/repo_tree/gen_tree.py --project <path> --output README.md --max-depth 1
   ```

3. **Fill the gaps.** Read the generated tree. Every line whose note is blank, or whose note
   is a bare restatement of the filename, is a file that cannot describe itself — write its
   line in `docs/.structure-notes.toml` under `[notes]`, keyed by repo-relative path, then
   re-run step 1. Overrides always beat derived notes, so use one to fix a weak heading too.

4. **Verify**, and wire it into the repo's CI next to the test step:

   ```bash
   python .ai/skills/repo_tree/gen_tree.py --project <path> --check
   ```

   This regenerates every file containing a tree block and exits non-zero with a unified diff
   if any is stale.

5. **Point the prose at it.** Any hand-written layout list elsewhere in the repo (a README
   "Layout" section, an HLD component map with files embedded in it) should keep only its
   orientation value — what each directory is *for* — and link to `docs/STRUCTURE.md` for the
   file list. Two maps means one of them is wrong.

## Where the notes come from

Per file, first source that answers wins:

| Priority | Source | Applies to |
|---|---|---|
| 1 | `docs/.structure-notes.toml` `[notes]` | anything — always wins |
| 2 | YAML frontmatter `description:` | `SKILL.md`, agent and command files |
| 3 | first markdown heading | any `.md` |
| 4 | Python module docstring, first line | `.py`, and extensionless files with a Python shebang |
| 5 | a directory's own `README.md` heading | directories |

## Rules

- **Never hand-edit between the markers.** The next `--check` will fail and the next
  generation will overwrite it. Put durable text in `.structure-notes.toml` or in the prose
  outside the block.
- The tree covers **git-tracked files** (`git ls-files`). A newly created file appears only
  after `git add` — which is the right behavior (untracked files aren't part of the repo yet),
  but it means you should stage before generating. Outside a git checkout the script falls
  back to a walk that prunes the usual noise directories.
- Notes say **what is inside** the file, not what its name already says. "Custom exception
  hierarchy, one base class per package" earns its line; "errors module" does not.
- This skill maps files. It does not review them (`audit_repo`, or the role-review fan-out)
  and does not design or document architecture (`write-design-doc`) — stay in lane.
