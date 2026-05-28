"""
Summary report generator — pretty-prints and saves a JSON + Markdown report.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class FileResult:
    path: str
    status: str          # "ok" | "ok_with_warnings" | "failed" | "skipped"
    attempts: int = 1
    error: str | None = None
    run_output: str | None = None


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
    tests_passed: bool | None = None   # None = not run
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
            print(f"   🧪 Tests   : ✅ passed")
        elif self.tests_passed is False:
            print(f"   🧪 Tests   : ❌ failed")
        if self.failed:
            print(f"\n   ✗ Failed  : {self.failed} files")
            for f in self.files:
                if f.status == "failed":
                    err_line = (f.error or "unknown error").splitlines()[0][:100]
                    print(f"     • {f.path}")
                    print(f"       └─ {err_line}")

        print(f"\n   📁 Output : {self.output_path}")
        print("  ══════════════════════════════════════════════\n")

    def save(self, output_path: Path) -> None:
        """Save report as both JSON and Markdown."""
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
            },
            "files": [asdict(f) for f in self.files],
            "manifest_translated": self.manifest_translated,
        }
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # Markdown
        md_path = report_dir / "translation_report.md"
        md = self._to_markdown()
        md_path.write_text(md, encoding="utf-8")

        print(f"  📄 Report saved → {json_path.name}  +  {md_path.name}\n")

    def _to_markdown(self) -> str:
        ok_files    = [f for f in self.files if f.status in ("ok", "ok_with_warnings")]
        failed_files = [f for f in self.files if f.status == "failed"]
        skipped_files = [f for f in self.files if f.status == "skipped"]

        lines = [
            f"# Translation Report",
            f"",
            f"| | |",
            f"|---|---|",
            f"| **From** | `{self.from_lang}` |",
            f"| **To** | `{self.to_lang}` |",
            f"| **Date** | {self.started_at} |",
            f"| **Duration** | {self.elapsed_seconds:.1f}s |",
            f"| **Files translated** | {self.translated} / {self.total} |",
            f"| **Failed** | {self.failed} |",
            f"| **Auto-fixed** | {self.needed_retry} |",
            f"",
        ]

        if self.manifest_translated:
            lines += [
                "## 📦 Manifests Translated",
                "",
            ] + [f"- `{m}`" for m in self.manifest_translated] + [""]

        if ok_files:
            lines += ["## ✅ Translated Files", ""]
            for f in ok_files:
                suffix = " *(needed retry)*" if f.attempts > 1 else ""
                warn = " ⚠️" if f.status == "ok_with_warnings" else ""
                lines.append(f"- `{f.path}`{warn}{suffix}")
            lines.append("")

        if failed_files:
            lines += ["## ✗ Failed Files", ""]
            for f in failed_files:
                lines.append(f"- `{f.path}`")
                if f.error:
                    err = f.error.splitlines()[0][:200]
                    lines.append(f"  - Error: `{err}`")
            lines.append("")

        if skipped_files:
            lines += ["## ⏭ Skipped (empty)", ""]
            for f in skipped_files:
                lines.append(f"- `{f.path}`")
            lines.append("")

        return "\n".join(lines)
