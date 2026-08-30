---
description: Write or extend tests for the code in $ARGUMENTS following the testing rules
---
Write tests for $ARGUMENTS following `.ai/rules/practices/testing.md`.

- Read the target code first; test behavior/contracts, not implementation.
- Cover the failure and boundary paths, not just the happy path.
- Keep tests deterministic — no real network/clock/fs/randomness.
- Match the project's existing test framework and layout.
Show the new tests and the command to run just them.
