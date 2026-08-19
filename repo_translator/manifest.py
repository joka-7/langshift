"""
Manifest translator — converts package.json / go.mod / Gemfile etc.
to the target language's equivalent dependency file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from repo_translator.providers.base import LLMProvider
from repo_translator.providers.retry import complete_with_backoff

try:
    import tomllib
except ImportError:
    tomllib = None  # Python 3.10 — tomllib is 3.11+; TOML validation is skipped there.

MAX_MANIFEST_ATTEMPTS = 2

# A deliberately loose match for a pip requirement line — this is a sanity
# check against an LLM leaking prose/markdown into the file (e.g. "Here are
# the translated dependencies:"), not a full PEP 508 parser. After the
# package name, only recognized specifier syntax may follow — free-floating
# words are what this is actually here to catch.
_PIP_OP = r'==|>=|<=|~=|!=|===|>|<'
_PIP_SPEC = rf'\s*(?:{_PIP_OP})\s*[A-Za-z0-9_.\-\*+!]+'  # one "op version" pair
_REQUIREMENT_LINE_RE = re.compile(
    r'^-[a-zA-Z].*$'  # pip flags, e.g. -e ./local-pkg, -r other.txt, --index-url ...
    r'|^[A-Za-z0-9][A-Za-z0-9_.\-]*(\[[A-Za-z0-9_,\-]+\])?'  # package name + extras
    r'('
    r'\s*@\s*\S+'  # direct URL/path reference
    rf'|({_PIP_SPEC})(\s*,{_PIP_SPEC})*'  # comma-separated version specifiers
    r')?'
    r'(\s*;\s*.+)?$'  # environment marker
)

# Maps from_lang → list of manifest filenames to look for
MANIFEST_FILES: dict[str, list[str]] = {
    "typescript":   ["package.json"],
    "javascript":   ["package.json"],
    "python":       ["requirements.txt", "pyproject.toml", "Pipfile"],
    "java":         ["pom.xml", "build.gradle"],
    "go":           ["go.mod", "go.sum"],
    "rust":         ["Cargo.toml"],
    "ruby":         ["Gemfile", "Gemfile.lock"],
    "csharp":       ["*.csproj", "packages.config"],
    "php":          ["composer.json"],
    "kotlin":       ["build.gradle", "build.gradle.kts"],
    "swift":        ["Package.swift"],
}

TARGET_MANIFEST: dict[str, str] = {
    "python":       "requirements.txt",
    "javascript":   "package.json",
    "typescript":   "package.json",
    "java":         "pom.xml",
    "go":           "go.mod",
    "rust":         "Cargo.toml",
    "ruby":         "Gemfile",
    "csharp":       "project.csproj",
    "php":          "composer.json",
    "kotlin":       "build.gradle.kts",
    "swift":        "Package.swift",
}


def _validate_manifest(target_name: str, content: str) -> str | None:
    """
    Sanity-check translated manifest content against its target format.
    Returns None if valid (or the format has no validator here), else a
    short description of what's wrong — fed back to the provider as
    error_context on retry, the same way agent.py's source-file loop does.
    """
    if not content.strip():
        return "translated manifest is empty"

    if target_name in ("package.json", "composer.json"):
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return f"invalid JSON: {e}"
        return None

    if target_name == "Cargo.toml":
        if tomllib is None:
            return None  # Python 3.10: tomllib unavailable, skip TOML validation
        try:
            tomllib.loads(content)
        except tomllib.TOMLDecodeError as e:
            return f"invalid TOML: {e}"
        return None

    if target_name == "requirements.txt":
        for line in content.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if not _REQUIREMENT_LINE_RE.match(line):
                return f"line does not look like a pip requirement: {line!r}"
        return None

    if target_name == "go.mod":
        if not any(line.strip().startswith("module ") for line in content.splitlines()):
            return "missing a 'module' directive"
        return None

    return None  # no validator for this format (pom.xml, Gemfile, etc.)


def _find_manifests(repo_path: Path, from_lang: str) -> list[Path]:
    """
    Returns manifests in a stable, deterministic order: by pattern (as listed
    in MANIFEST_FILES), then alphabetically within a pattern. Path.glob()
    itself makes no ordering guarantee and repo_path.glob(pattern) /
    repo_path.glob(f"*/{pattern}") can overlap, so results used to be
    round-tripped through set() — which meant the manifest order (and, since
    translate_manifest writes same-named collisions in that order, which
    manifest's translation "won") varied from run to run.
    """
    patterns = MANIFEST_FILES.get(from_lang, [])
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        matches = sorted(repo_path.glob(pattern)) + sorted(repo_path.glob(f"*/{pattern}"))
        for m in matches:
            if m not in seen:
                seen.add(m)
                found.append(m)
    return found


def _manifest_prompt(
    from_lang: str, to_lang: str, target_name: str, manifest_name: str,
    content: str, error_context: str | None = None,
) -> str:
    fix_note = ""
    if error_context:
        fix_note = f"""
The previous attempt produced invalid output: {error_context}
Please fix it so the result is valid {target_name} syntax.
"""

    return f"""You are an expert in software dependency management.

Convert this {from_lang} dependency manifest to an equivalent {to_lang} manifest ({target_name}).

Rules:
- Output ONLY the file content, no markdown fences, no explanation.
- Map each dependency to the closest {to_lang} equivalent package.
- If no equivalent exists, add a comment: # TODO: no equivalent for <package>
- Use current stable versions where possible.
- Preserve dev-dependencies vs runtime-dependencies distinction.
{fix_note}
Source ({from_lang} - {manifest_name}):
{content}
"""


def translate_manifest(
    provider: LLMProvider,
    repo_path: Path,
    output_path: Path,
    from_lang: str,
    to_lang: str,
    verbose: bool = True,
) -> dict:
    """
    Find and translate dependency manifests.

    Each translation is validated against its target format (see
    _validate_manifest) before being written; on failure the validation
    error is fed back to the provider for one retry, mirroring the
    source-file auto-fix loop in agent.py. A manifest that's still invalid
    after MAX_MANIFEST_ATTEMPTS is skipped rather than written broken.

    Returns {"found": int, "translated": [paths], "skipped": reason|None,
             "validation_failed": [{"manifest": str, "error": str}]}
    """
    manifests = _find_manifests(repo_path, from_lang)
    if not manifests:
        return {"found": 0, "translated": [], "skipped": "no manifest files found",
                "validation_failed": []}

    target_name = TARGET_MANIFEST.get(to_lang, f"dependencies.{to_lang}")
    results: list[str] = []
    validation_failed: list[dict] = []
    used_dest_files: set[Path] = set()

    for manifest in manifests:
        rel = manifest.relative_to(repo_path)
        if verbose:
            print(f"  📦 Translating manifest: {rel} → {target_name}", flush=True)

        content = manifest.read_text(encoding="utf-8", errors="replace")

        translated_content: str | None = None
        validation_error: str | None = None
        error_ctx: str | None = None
        api_failed = False

        for attempt in range(1, MAX_MANIFEST_ATTEMPTS + 1):
            prompt = _manifest_prompt(
                from_lang, to_lang, target_name, manifest.name, content, error_ctx,
            )
            try:
                translated_content = complete_with_backoff(provider, prompt, max_tokens=4096)
            except Exception as e:
                if verbose:
                    print(f"    ✗ Failed: {e}")
                api_failed = True
                break

            validation_error = _validate_manifest(target_name, translated_content)
            if validation_error is None:
                break
            error_ctx = validation_error
            if verbose and attempt < MAX_MANIFEST_ATTEMPTS:
                print(f"    ↺ attempt {attempt} produced invalid output "
                      f"({validation_error}), retrying...")

        if api_failed:
            continue

        if validation_error is not None:
            if verbose:
                print(f"    ✗ Skipped: still invalid after {MAX_MANIFEST_ATTEMPTS} "
                      f"attempt(s) ({validation_error})")
            validation_failed.append({"manifest": str(rel), "error": validation_error})
            continue

        if translated_content is None:
            continue  # unreachable: validation_error is None only after a successful call

        try:
            dest_dir = output_path / rel.parent
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / target_name
            if dest_file in used_dest_files:
                # Another manifest in this repo already translated to the same
                # target filename (e.g. both requirements.txt and pyproject.toml
                # map to requirements.txt). Disambiguate instead of silently
                # overwriting the earlier translation.
                target = Path(target_name)
                dest_file = dest_dir / f"{target.stem}.from-{manifest.stem}{target.suffix}"
                if verbose:
                    print(f"    ⚠ {target_name} already written from another manifest; "
                          f"saving as {dest_file.name}")
            used_dest_files.add(dest_file)
            dest_file.write_text(translated_content, encoding="utf-8")
            results.append(str(dest_file))

            if verbose:
                print(f"    ✓ Written to {dest_file.relative_to(output_path)}")
        except OSError as e:
            if verbose:
                print(f"    ✗ Failed to write output: {e}")

    return {"found": len(manifests), "translated": results, "skipped": None,
            "validation_failed": validation_failed}
