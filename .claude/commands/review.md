---
description: Review the current diff against this repo's coding rules
---
Review the staged changes (`git diff --staged`; fall back to `git diff`) against the conventions in AGENTS.md and the language/framework rules in `.ai/rules/`.

Focus on, in priority order:
1. Correctness and error handling (swallowed errors, missing edge cases, unsafe null/None).
2. Security (input validation, secrets, injection).
3. Rule violations specific to the language(s) touched.
4. Naming and clarity.

Report as a short list of concrete issues with file:line and a suggested fix each. Skip nits if there are real problems. If it's clean, say so briefly. $ARGUMENTS
