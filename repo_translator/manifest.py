"""
Manifest translator — converts package.json / go.mod / Gemfile etc.
to the target language's equivalent dependency file.
"""

from __future__ import annotations

import json
from pathlib import Path

import anthropic

_DEFAULT_MODEL_ID = "claude-sonnet-4-6"

# Maps (from_lang, to_lang) → list of manifest filenames to look for
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
    patterns = MANIFEST_FILES.get(from_lang, [])
    found = []
    for pattern in patterns:
        found.extend(repo_path.glob(pattern))
        found.extend(repo_path.glob(f"*/{pattern}"))
    return list(set(found))


def translate_manifest(
    client: anthropic.Anthropic,
    repo_path: Path,
    output_path: Path,
    from_lang: str,
    to_lang: str,
    verbose: bool = True,
    model_id: str = _DEFAULT_MODEL_ID,
) -> dict:
    """
    Find and translate dependency manifests.
    Returns {"found": int, "translated": [paths], "skipped": reason|None}
    """
    manifests = _find_manifests(repo_path, from_lang)
    if not manifests:
        return {"found": 0, "translated": [], "skipped": "no manifest files found"}

    target_name = TARGET_MANIFEST.get(to_lang, f"dependencies.{to_lang}")
    results = []

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
            message = client.messages.create(
                model=model_id,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            translated_content = message.content[0].text.strip()

            # Determine output location — mirror the relative path but rename the file
            dest_dir = output_path / rel.parent
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / target_name
            dest_file.write_text(translated_content, encoding="utf-8")
            results.append(str(dest_file))

            if verbose:
                print(f"    ✓ Written to {dest_file.relative_to(output_path)}")
        except Exception as e:
            if verbose:
                print(f"    ✗ Failed: {e}")

    return {"found": len(manifests), "translated": results, "skipped": None}
