---
name: port-module-to-ts
description: Port a JavaScript or Python module to TypeScript — translate its public API, types, and error handling to this config's TypeScript rules while preserving behavior exactly. Use whenever the user asks to port, convert, or migrate a file/module to TypeScript, or asks "what would this look like in TS" — even if they only name the source file.
---

# Port Module to TS

Port one module's behavior into idiomatic TypeScript. This is a port, not a rewrite: the
output must do exactly what the source does. Improvements you notice along the way are
recommendations, not something to slip into the diff.

## Steps

1. **Read the source module in full** and list its public surface: exports, function
   signatures, and the types implied by how each value is constructed and used — the source
   language's own type hints/annotations if present, otherwise infer from usage.
2. **Read the destination project's `tsconfig.json`** if one exists, to match its actual
   strictness settings; otherwise follow `rules/languages/typescript.md` as the baseline.
3. **Translate structure before translating syntax.** Map the source's shape onto TypeScript
   idiom rather than transliterating line by line:
   - JS → TS: add types incrementally, no `any`; convert `require`/`module.exports` to
     ESM `import`/`export` only if the destination project already uses ESM elsewhere —
     check a sibling file before assuming.
   - Python → TS: `Optional[X]` → `X | undefined`; `dataclass`/`TypedDict` → `interface`;
     `list`/`dict` → `Array`/`Record` or `Map` depending on whether keys are known ahead of
     time; custom exceptions → classes extending `Error`; comprehensions → `map`/`filter`/
     `reduce`; `snake_case` → `camelCase` for the ported symbols (see the rule on renames
     below).
4. **Port the module's own tests alongside it**, if it has any, using whatever test runner
   the destination project already uses (check `package.json`) — don't invent a new one.
5. **Apply the TypeScript error-handling rule**: custom classes extending `Error` for thrown
   errors, or a typed `Result`/discriminated union at call sites that already expect one.
6. **Verify**: run `tsc --noEmit` and the ported tests. Report failures as failures — don't
   silently adjust the ported logic to make a test pass without understanding why it failed.

## Rules

- **Behavior parity is the whole point.** Port the logic as it exists; do not fix a bug or
  improve an algorithm while porting. If you notice one, name it in the output instead.
- Never invent a TypeScript API or library that doesn't exist in the destination project's
  dependencies. If the source relied on a library with no TS equivalent already present, say
  so and ask rather than picking one.
- Renaming for convention (`snake_case` → `camelCase`, `PascalCase` type names) is expected,
  but list every renamed symbol explicitly in the output — a caller updating references needs
  the mapping, not just the diff.
- Leave the original source file in place. Don't delete it until the ported version passes
  its tests and the user confirms the port replaces it, per this config's rule on large or
  irreversible changes.
- Follow `rules/languages/typescript.md` exactly: `strict: true`, no `any`, `interface` for
  object shapes, discriminated unions over boolean flags, immutable transformations over
  in-place mutation.

## Output

Print: the ported file(s), the symbol-rename mapping if any, which tests were ported and
their result, and the exact `tsc`/test commands to re-verify. Note anything you could not
port faithfully (a source-language feature with no clean TS equivalent) rather than papering
over it.
