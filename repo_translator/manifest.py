"""
Manifest translator — converts package.json / go.mod / Gemfile etc.
to the target language's equivalent dependency file.
"""

from __future__ import annotations

from pathlib import Path

from repo_translator.providers.base import LLMProvider
from repo_translator.providers.retry import complete_with_backoff

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
    Returns {"found": int, "translated": [paths], "skipped": reason|None}
    """
    manifests = _find_manifests(repo_path, from_lang)
    if not manifests:
        return {"found": 0, "translated": [], "skipped": "no manifest files found"}

    target_name = TARGET_MANIFEST.get(to_lang, f"dependencies.{to_lang}")
    results: list[str] = []
    used_dest_files: set[Path] = set()

    for manifest in manifests:
        rel = manifest.relative_to(repo_path)
        if verbose:
            print(f"  📦 Translating manifest: {rel} → {target_name}", flush=True)

        content = manifest.read_text(encoding="utf-8", errors="replace")

        prompt = f"""You are an expert in software dependency management.

Convert this {from_lang} dependency manifest to an equivalent {to_lang} manifest ({target_name}).

Rules:
- Output ONLY the file content, no markdown fences, no explanation.
- Map each dependency to the closest {to_lang} equivalent package.
- If no equivalent exists, add a comment: # TODO: no equivalent for <package>
- Use current stable versions where possible.
- Preserve dev-dependencies vs runtime-dependencies distinction.

Source ({from_lang} - {manifest.name}):
{content}
"""

        try:
            translated_content = complete_with_backoff(provider, prompt, max_tokens=4096)

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
        except Exception as e:
            if verbose:
                print(f"    ✗ Failed: {e}")

    return {"found": len(manifests), "translated": results, "skipped": None}
