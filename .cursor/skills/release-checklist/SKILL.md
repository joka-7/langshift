---
name: release-checklist
description: Walk through cutting a release — determine the version bump from commits since the last tag, run the project's own gate commands, update the manifest version and changelog, and prepare a tagged release commit. Use whenever the user asks to cut, prepare, or ship a release, bump the version, "what's next for v2", or update the changelog for a release — even if they only name a version number.
---

# Release Checklist

Walk a target project from "last tagged release" to "a version-bumped commit and an
annotated tag ready to push," using signals already in the repo rather than inventing a
release process it doesn't have. This skill prepares the release; it does not publish it.

## Steps

1. **Find the last release.** `git describe --tags --abbrev=0` for the most recent tag; if
   none exists, treat this as the first release and say so. Read the version field from the
   manifest (`package.json`, `pyproject.toml`, `Cargo.toml`, `gradle.properties`, …) and
   reconcile it against the tag — if they disagree, say so before doing anything else.
2. **Classify commits since that tag.** `git log <last-tag>..HEAD --oneline`. If they follow
   Conventional Commits (this repo's own convention — see `rules/practices/git-commits.md`),
   derive the bump: any `!` or `BREAKING CHANGE:` footer → major; any `feat` → minor;
   otherwise → patch. If commits don't follow that convention, say so and ask for the bump
   type rather than guessing from prose.
3. **Find the project's own gates**, the same way `role_review`'s roles do: read CI config
   (`.github/workflows/`, `.gitlab-ci.yml`, …) for the real lint/test/build commands, falling
   back to what `README`/`CONTRIBUTING` document if there's no CI. Run them. **Stop and report
   if any fail — do not bump a version over a broken gate.**
4. **Update the manifest's version field** to the new version. Follow the versioning scheme
   already in use (semver unless the existing tags say otherwise); never invent a new scheme.
5. **Update the changelog.** If `CHANGELOG.md` (or equivalent) exists, prepend a new section
   grouped by commit type, matching its existing heading style. If none exists, ask whether to
   create one rather than assuming the project wants one.
6. **Commit the bump**, following the `conventional-commit` skill: one commit, type `chore`,
   e.g. `chore(release): v2.1.0`, covering only the manifest and changelog — nothing else.
7. **Tag it**: `git tag -a v<version> -m "<version>"` (or the project's existing tag prefix,
   if it doesn't use a leading `v`).
8. **Stop.** Report the prepared commit and tag, and the exact commands to publish
   (`git push --follow-tags`, plus a registry publish command if the manifest names one) —
   but do not run them yourself.

## Rules

- Never push, publish to a registry, or create a GitHub release. This skill prepares a
  release; a human decides when it goes out. State the remaining commands instead of running
  them, per this config's rule on large or irreversible actions.
- Never bump the version if a gate command fails. A red test suite is not shippable regardless
  of what the commit history implies about the bump type.
- One commit for the bump. Don't fold in unrelated changes even if they're sitting in the
  working tree — ask the user to handle those separately first.
- Don't invent a changelog entry for a commit that doesn't describe user-visible change
  (`chore`, `ci`, `test` commits usually don't belong in it — use judgment, not a blanket rule).
- If the repo has no CI and no documented gate commands, say that plainly rather than skipping
  verification silently.

## Output

Print a summary: previous version → new version and why (which commits drove the bump),
which gate commands ran and their result, the files changed, and the exact publish commands
left for the user to run themselves.
