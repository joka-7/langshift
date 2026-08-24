"""
Summary report generator — pretty-prints and saves a JSON + Markdown report.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_CONFIDENCE_THRESHOLD = 70


@dataclass
class FileResult:
    path: str
    status: str          # "ok" | "ok_with_warnings" | "failed" | "skipped"
    attempts: int = 1
    error: str | None = None
    run_output: str | None = None
    confidence: int | None = None
    confidence_reason: str | None = None
    chunks: int | None = None  # >1 if the file was too large for one call and got split
    # Set only when status == "failed": {provider_name: url} deep links into a
    # free public AI chat, pre-filled with a plain-English version of this
    # file's translation request — see providers/external_chat.py.
    external_chat_urls: dict[str, str] | None = None


@dataclass
class TranslationReport:
    from_lang: str
    to_lang: str
    input_path: str
    output_path: str
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    elapsed_seconds: float = 0.0
    files: list[FileResult] = field(default_factory=list)
    manifest_translated: list[str] = field(default_factory=list)
    tests_passed: bool | None = None
    test_output: str | None = None

    # ------------------------------------------------------------------ #
    # Computed properties
    # ------------------------------------------------------------------ #
    @property
    def total(self) -> int:
        return len(self.files)

    @property
    def translated(self) -> int:
        return sum(1 for f in self.files if f.status in ("ok", "ok_with_warnings"))

    @property
    def failed(self) -> int:
        return sum(1 for f in self.files if f.status == "failed")

    @property
    def skipped(self) -> int:
        return sum(1 for f in self.files if f.status == "skipped")

    @property
    def needed_retry(self) -> int:
        return sum(1 for f in self.files if f.attempts > 1 and f.status != "failed")

    @property
    def high_confidence(self) -> int:
        return sum(
            1 for f in self.files
            if f.confidence is not None and f.confidence >= _CONFIDENCE_THRESHOLD
        )

    @property
    def needs_review(self) -> int:
        return sum(
            1 for f in self.files
            if f.confidence is not None and f.confidence < _CONFIDENCE_THRESHOLD
        )

    # ------------------------------------------------------------------ #
    # Output
    # ------------------------------------------------------------------ #
    def print_summary(self) -> None:
        status_icon = "✅" if self.failed == 0 else "⚠️ "
        print(f"""
  ══════════════════════════════════════════════
   {status_icon}  Translation Report
  ══════════════════════════════════════════════
   From      : {self.from_lang}
   To        : {self.to_lang}
   Duration  : {self.elapsed_seconds:.1f}s

   Files     : {self.translated} / {self.total} translated""")

        if self.skipped:
            print(f"   Skipped   : {self.skipped} (empty files)")
        if self.needed_retry:
            print(f"   Auto-fixed: {self.needed_retry} (needed retries)")
        if self.manifest_translated:
            print(f"   Manifests : {len(self.manifest_translated)} translated")
        if self.tests_passed is True:
            print("   🧪 Tests   : ✅ passed")
        elif self.tests_passed is False:
            print("   🧪 Tests   : ❌ failed")

        scored = self.high_confidence + self.needs_review
        if scored:
            print(f"   Confidence: {self.high_confidence} high (≥{_CONFIDENCE_THRESHOLD}) "
                  f"/ {self.needs_review} need review (<{_CONFIDENCE_THRESHOLD})")

        if self.failed:
            print(f"\n   ✗ Failed  : {self.failed} files")
            for f in self.files:
                if f.status == "failed":
                    err_line = (f.error or "unknown error").splitlines()[0][:100]
                    print(f"     • {f.path}")
                    print(f"       └─ {err_line}")
                    if f.external_chat_urls:
                        print("       └─ Or ask directly:")
                        for name, url in f.external_chat_urls.items():
                            print(f"          {name}: {url}")

        print(f"\n   📁 Output : {self.output_path}")
        print("  ══════════════════════════════════════════════\n")

    def save(self, output_path: Path) -> None:
        report_dir = output_path
        report_dir.mkdir(parents=True, exist_ok=True)

        # JSON
        json_path = report_dir / "translation_report.json"
        data = {
            "from_lang": self.from_lang,
            "to_lang": self.to_lang,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "started_at": self.started_at,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "summary": {
                "total": self.total,
                "translated": self.translated,
                "failed": self.failed,
                "skipped": self.skipped,
                "needed_retry": self.needed_retry,
                "manifests_translated": len(self.manifest_translated),
                "high_confidence": self.high_confidence,
                "needs_review": self.needs_review,
            },
            "files": [asdict(f) for f in self.files],
            "manifest_translated": self.manifest_translated,
            "tests_passed": self.tests_passed,
            "test_output": self.test_output,
        }
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # Markdown
        md_path = report_dir / "translation_report.md"
        md_path.write_text(self._to_markdown(), encoding="utf-8")

        print(f"  📄 Report saved → {json_path.name}  +  {md_path.name}\n")

    def _to_markdown(self) -> str:
        ok_files      = [f for f in self.files if f.status in ("ok", "ok_with_warnings")]
        failed_files  = [f for f in self.files if f.status == "failed"]
        skipped_files = [f for f in self.files if f.status == "skipped"]

        scored = self.high_confidence + self.needs_review
        conf_row = (
            f"| **Confidence** | {self.high_confidence} high / {self.needs_review} need review |\n"
            if scored else ""
        )

        lines = [
            "# Translation Report",
            "",
            "| | |",
            "|---|---|",
            f"| **From** | `{self.from_lang}` |",
            f"| **To** | `{self.to_lang}` |",
            f"| **Date** | {self.started_at} |",
            f"| **Duration** | {self.elapsed_seconds:.1f}s |",
            f"| **Files translated** | {self.translated} / {self.total} |",
            f"| **Failed** | {self.failed} |",
            f"| **Auto-fixed** | {self.needed_retry} |",
        ]
        if conf_row:
            lines.append(conf_row.strip())
        lines.append("")

        if self.manifest_translated:
            lines += [
                "## 📦 Manifests Translated", "",
            ] + [f"- `{m}`" for m in self.manifest_translated] + [""]

        if ok_files:
            lines += ["## ✅ Translated Files", ""]
            for f in ok_files:
                suffix = " *(needed retry)*" if f.attempts > 1 else ""
                suffix += f" *(split into {f.chunks} chunks)*" if f.chunks else ""
                warn   = " ⚠️" if f.status == "ok_with_warnings" else ""
                if f.confidence is not None:
                    flag = " ⚠️ needs review" if f.confidence < _CONFIDENCE_THRESHOLD else ""
                    conf_str = f"  — {f.confidence}/100{flag}"
                    if f.confidence_reason:
                        conf_str += f" *({f.confidence_reason})*"
                else:
                    conf_str = ""
                lines.append(f"- `{f.path}`{warn}{suffix}{conf_str}")
            lines.append("")

        if failed_files:
            lines += ["## ✗ Failed Files", ""]
            for f in failed_files:
                lines.append(f"- `{f.path}`")
                if f.error:
                    err = f.error.splitlines()[0][:200]
                    lines.append(f"  - Error: `{err}`")
                if f.external_chat_urls:
                    lines.append("  - Or ask directly: " + ", ".join(
                        f"[{name}]({url})" for name, url in f.external_chat_urls.items()
                    ))
            lines.append("")

        if skipped_files:
            lines += ["## ⏭ Skipped (empty)", ""]
            for f in skipped_files:
                lines.append(f"- `{f.path}`")
            lines.append("")

        return "\n".join(lines)
