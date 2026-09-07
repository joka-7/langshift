"""
Architecture-diagram generation — analyzes a repo's structure via the same
regex-based symbol-extraction heuristic as --cross-file-context
(agent._build_repo_map/_format_repo_map) and asks the provider for a
mermaid + draw.io rendering of it at four levels: static, dynamic,
high-level (HLD), and low-level (LLD).

Structurally different from agent.translate_repo(): this makes a handful of
prompts total (one per diagram type), not one per file, and produces no
runnable output to auto-verify.
"""

from __future__ import annotations

import json
import re
import textwrap
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from repo_translator.agent import (
    MAX_FIX_ATTEMPTS,
    _build_repo_map,
    _format_repo_map,
    collect_files,
    resolve_language,
)
from repo_translator.providers.base import LLMProvider
from repo_translator.providers.retry import complete_with_backoff

DIAGRAM_TYPES: tuple[str, ...] = ("static", "dynamic", "hld", "lld")

# What each diagram type should show. Interpolated into _build_diagram_prompt;
# mermaid diagram kind is suggested, not mandated, since the best fit
# (classDiagram vs flowchart vs sequenceDiagram) depends on the repo.
_DIAGRAM_GUIDANCE: dict[str, str] = {
    "static": (
        "a STATIC structure diagram: the repo's modules/files and classes, and the "
        "relationships between them (imports, inheritance, composition). Use mermaid's "
        "classDiagram, or a flowchart of module dependencies if that fits the repo map better."
    ),
    "dynamic": (
        "a DYNAMIC diagram: one key runtime flow through the repo's main entry point and the "
        "components it calls, in order. Use mermaid's sequenceDiagram, or a flowchart if the "
        "flow isn't naturally actor/message-shaped."
    ),
    "hld": (
        "a HIGH-LEVEL DESIGN diagram: the repo's component/module boundaries and how they "
        "connect — the block diagram an HLD doc's 'static view' would carry. Use mermaid's "
        "flowchart/graph."
    ),
    "lld": (
        "a LOW-LEVEL DESIGN diagram: class/interface contracts and method-level interactions "
        "for the repo's most central component(s) — more detail than the static diagram, down "
        "to key methods. Use mermaid's classDiagram."
    ),
}

# Caps the repo map's contribution to the diagram prompt. Larger than
# translation's per-file _REPO_MAP_MAX_CHARS (agent.py): here the map is the
# *entire* content the model has to work from, not a supplement to a file.
_DIAGRAM_MAP_MAX_CHARS = 8000
_DIAGRAM_MAX_TOKENS = 4096

_MERMAID_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_XML_RE = re.compile(r"```xml\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_EXPLANATION_RE = re.compile(
    r"##\s*Explanation\s*\n(.*?)(?=\n##\s*Mermaid|\Z)", re.DOTALL | re.IGNORECASE
)


def _build_diagram_prompt(
    diagram_type: str, lang: str, repo_map_text: str, error_context: str | None = None,
) -> str:
    guidance = _DIAGRAM_GUIDANCE[diagram_type]
    retry_note = ""
    if error_context:
        retry_note = textwrap.dedent(f"""
            Your previous response didn't match the required format:
            ---
            {error_context}
            ---
            Follow the structure below exactly this time.
        """)

    instructions = textwrap.dedent(f"""
        You are a software architect. Based on the {lang} repository map below (files and
        their top-level symbols), produce {guidance}
        {retry_note}
        Respond in exactly this structure, with no extra commentary outside it:

        ## Explanation
        <2-4 sentences describing what the diagram shows>

        ## Mermaid
        ```mermaid
        <diagram>
        ```

        ## Draw.io XML
        ```xml
        <mxGraphModel>...</mxGraphModel>
        ```
    """).strip()

    return f"{instructions}\n\nRepository map ({lang}):\n{repo_map_text}"


def _parse_diagram_response(raw: str) -> tuple[str, str, str]:
    """Extract (explanation, mermaid, drawio_xml) from a diagram response.

    Raises:
        ValueError: naming every section that couldn't be found — fed back
            to the model as error_context on retry.
    """
    missing: list[str] = []

    exp_match = _EXPLANATION_RE.search(raw)
    explanation = exp_match.group(1).strip() if exp_match else ""
    if not explanation:
        missing.append("an '## Explanation' section")

    mermaid_match = _MERMAID_RE.search(raw)
    mermaid = mermaid_match.group(1).strip() if mermaid_match else ""
    if not mermaid:
        missing.append("a fenced ```mermaid code block")

    xml_match = _XML_RE.search(raw)
    drawio_xml = xml_match.group(1).strip() if xml_match else ""
    if not drawio_xml:
        missing.append("a fenced ```xml draw.io block")

    if missing:
        raise ValueError(f"Response is missing {', '.join(missing)}.")

    return explanation, mermaid, drawio_xml


@dataclass
class DiagramResult:
    diagram_type: str
    status: str  # "ok" | "failed"
    attempts: int = 1
    error: str | None = None
    mermaid_path: str | None = None
    drawio_path: str | None = None


@dataclass
class DiagramReport:
    lang: str
    input_path: str
    output_path: str
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    elapsed_seconds: float = 0.0
    results: list[DiagramResult] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.status == "ok")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")

    def print_summary(self) -> None:
        status_icon = "✅" if self.failed == 0 else "⚠️ "
        print(f"""
  ══════════════════════════════════════════════
   {status_icon}  Diagram Report
  ══════════════════════════════════════════════
   Language  : {self.lang}
   Duration  : {self.elapsed_seconds:.1f}s

   Diagrams  : {self.succeeded} / {len(self.results)} generated""")
        for r in self.results:
            if r.status == "ok":
                print(f"     • {r.diagram_type}: {r.mermaid_path}, {r.drawio_path}")
            else:
                err_line = (r.error or "unknown error").splitlines()[0][:100]
                print(f"     ✗ {r.diagram_type}: {err_line}")
        print(f"\n   📁 Output : {self.output_path}")
        print("  ══════════════════════════════════════════════\n")

    def save(self, output_path: Path) -> None:
        report_dir = output_path
        report_dir.mkdir(parents=True, exist_ok=True)

        json_path = report_dir / "diagram_report.json"
        data = {
            "lang": self.lang,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "started_at": self.started_at,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "summary": {
                "total": len(self.results),
                "succeeded": self.succeeded,
                "failed": self.failed,
            },
            "results": [asdict(r) for r in self.results],
        }
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"  📄 Report saved → {json_path.name}\n")


def generate_diagrams(
    repo_path: Path,
    output_path: Path,
    lang: str,
    provider: LLMProvider,
    diagram_types: tuple[str, ...] = DIAGRAM_TYPES,
    verbose: bool = True,
    on_progress: Callable[[dict], None] | None = None,
) -> DiagramReport:
    """
    on_progress, if given, is called with a dict for each notable event:
      {"type": "diagram_start", "index": int, "total": int, "diagram_type": str}
      {"type": "diagram_done", "index": int, "total": int, "diagram_type": str,
       "status": str, "attempts": int}
      {"type": "finished", "summary": dict}
    Mirrors translate_repo()'s on_progress shape for consistency; nothing wires it up yet.

    A diagram type that never parses after every retry is recorded as
    status="failed" on its DiagramResult rather than raised — one bad
    diagram type must not abort the others, same fail-soft-and-report
    contract as translate_repo().
    """
    def _emit(event: dict) -> None:
        if on_progress:
            on_progress(event)

    unknown = [t for t in diagram_types if t not in DIAGRAM_TYPES]
    if unknown:
        raise ValueError(
            f"Unknown diagram type(s): {', '.join(unknown)}. Supported: {', '.join(DIAGRAM_TYPES)}"
        )

    lang = resolve_language(lang)
    report = DiagramReport(lang=lang, input_path=str(repo_path), output_path=str(output_path))
    start = time.time()

    files = collect_files(repo_path, lang)
    if not files:
        if verbose:
            print(f"  No {lang} files found in {repo_path}")
        report.elapsed_seconds = time.time() - start
        _emit({"type": "finished", "summary": {"total": 0, "succeeded": 0, "failed": 0}})
        return report

    repo_map = _build_repo_map(files, repo_path, lang)
    repo_map_text = _format_repo_map(repo_map, exclude="", threshold=_DIAGRAM_MAP_MAX_CHARS)

    diagrams_dir = output_path / "diagrams"
    diagrams_dir.mkdir(parents=True, exist_ok=True)

    max_attempts = getattr(provider, "max_fix_attempts", MAX_FIX_ATTEMPTS)

    for i, diagram_type in enumerate(diagram_types, 1):
        if verbose:
            print(f"  [{i}/{len(diagram_types)}] {diagram_type}", end=" ", flush=True)
        _emit({"type": "diagram_start", "index": i, "total": len(diagram_types),
               "diagram_type": diagram_type})

        error_ctx: str | None = None
        result: DiagramResult | None = None
        for attempt in range(1, max_attempts + 1):
            prompt = _build_diagram_prompt(diagram_type, lang, repo_map_text, error_ctx)
            try:
                raw = complete_with_backoff(provider, prompt, max_tokens=_DIAGRAM_MAX_TOKENS)
                explanation, mermaid, drawio_xml = _parse_diagram_response(raw)
            except ValueError as e:
                error_ctx = str(e)
                if attempt < max_attempts:
                    if verbose:
                        print(f"\n    ↺ attempt {attempt} unparsable, retrying...",
                              end=" ", flush=True)
                    continue
                result = DiagramResult(
                    diagram_type=diagram_type, status="failed", attempts=attempt, error=error_ctx,
                )
                break
            except Exception as e:  # provider/SDK errors are an open set
                result = DiagramResult(
                    diagram_type=diagram_type, status="failed", attempts=attempt, error=str(e),
                )
                break

            md_path = diagrams_dir / f"{diagram_type}.md"
            drawio_path = diagrams_dir / f"{diagram_type}.drawio"
            md_path.write_text(
                f"# {diagram_type.upper()} diagram\n\n{explanation}\n\n"
                f"```mermaid\n{mermaid}\n```\n",
                encoding="utf-8",
            )
            drawio_path.write_text(drawio_xml, encoding="utf-8")
            result = DiagramResult(
                diagram_type=diagram_type, status="ok", attempts=attempt,
                mermaid_path=str(md_path), drawio_path=str(drawio_path),
            )
            if verbose:
                extra = f" (fixed in {attempt} attempt(s))" if attempt > 1 else ""
                print(f"→ ✓{extra}")
            break

        assert result is not None  # loop always sets it: success, or failure on the last attempt
        if result.status == "failed" and verbose:
            print(f"→ ✗ {result.error}")
        report.results.append(result)
        _emit({"type": "diagram_done", "index": i, "total": len(diagram_types),
               "diagram_type": diagram_type, "status": result.status, "attempts": result.attempts})

    report.elapsed_seconds = time.time() - start
    _emit({"type": "finished", "summary": {
        "total": len(report.results), "succeeded": report.succeeded, "failed": report.failed,
    }})
    return report
