---
name: conventional-commit
description: Write a Conventional Commits-formatted git commit message from staged changes. Use this whenever the user asks to commit, write a commit message, or asks "what should this commit say" — even if they don't say the word "conventional". Also use when preparing a PR title.
---

# Conventional Commit

Produce a commit message in Conventional Commits format from the current staged diff.

## Steps

1. Run `git diff --staged` (or `git diff` if nothing is staged, and note that nothing is staged) to see the actual change. Never write a message without reading the diff.
2. Classify the change into exactly one type: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, `ci`, `chore`.
3. Derive a scope from the primary package/module touched (optional but preferred). Lowercase.
4. Write the summary line: `type(scope): imperative summary` — imperative mood ("add", not "added"/"adds"), ≤72 chars, no trailing period.
5. If the change is non-obvious, add a body (blank line after summary) explaining *why*. Wrap at ~72 cols.
6. If it breaks an API/contract, add `!` before the colon and a `BREAKING CHANGE: <detail>` footer.

## Rules

- One logical change per commit. If the diff mixes concerns (e.g. a refactor plus a feature), say so and propose splitting rather than forcing one message.
- Never invent a rationale not supported by the diff.

## Output

Print the message in a fenced block ready to paste, then offer to run the commit.
