"""
Tests for repo_translator.diagram

All tests are pure unit tests — no real API calls, no network. Providers
are mocked via MockProvider (tests/helpers.py).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from helpers import CapturingProvider, MockProvider

from repo_translator.diagram import (
    DIAGRAM_TYPES,
    DiagramReport,
    DiagramResult,
    _build_diagram_prompt,
    _parse_diagram_response,
    generate_diagrams,
)


def _well_formed_response(mermaid: str = "graph TD; A-->B;", xml: str = "<mxGraphModel/>") -> str:
    return (
        "## Explanation\n"
        "This diagram shows the module structure.\n\n"
        "## Mermaid\n"
        f"```mermaid\n{mermaid}\n```\n\n"
        "## Draw.io XML\n"
        f"```xml\n{xml}\n```\n"
    )


# ─────────────────────────────────────────────
# _parse_diagram_response
# ─────────────────────────────────────────────

class TestParseDiagramResponse:
    def test_parses_well_formed_response(self):
        explanation, mermaid, xml = _parse_diagram_response(_well_formed_response())
        assert explanation == "This diagram shows the module structure."
        assert mermaid == "graph TD; A-->B;"
        assert xml == "<mxGraphModel/>"

    def test_missing_mermaid_block_raises(self):
        raw = "## Explanation\nSomething.\n\n## Draw.io XML\n```xml\n<mxGraphModel/>\n```\n"
        with pytest.raises(ValueError, match="mermaid"):
            _parse_diagram_response(raw)

    def test_missing_xml_block_raises(self):
        raw = "## Explanation\nSomething.\n\n## Mermaid\n```mermaid\ngraph TD;\n```\n"
        with pytest.raises(ValueError, match="xml"):
            _parse_diagram_response(raw)

    def test_missing_explanation_raises(self):
        raw = "## Mermaid\n```mermaid\ngraph TD;\n```\n\n## Draw.io XML\n```xml\n<a/>\n```\n"
        with pytest.raises(ValueError, match="Explanation"):
            _parse_diagram_response(raw)

    def test_missing_everything_lists_all_sections(self):
        with pytest.raises(ValueError) as exc:
            _parse_diagram_response("nothing useful here")
        msg = str(exc.value)
        assert "Explanation" in msg
        assert "mermaid" in msg
        assert "xml" in msg


# ─────────────────────────────────────────────
# _build_diagram_prompt
# ─────────────────────────────────────────────

class TestBuildDiagramPrompt:
    def test_includes_repo_map_and_type_guidance(self):
        prompt = _build_diagram_prompt("static", "python", "- a.py: foo, Bar")
        assert "a.py: foo, Bar" in prompt
        assert "STATIC" in prompt

    def test_error_context_becomes_retry_note(self):
        prompt = _build_diagram_prompt("hld", "python", "- a.py", error_context="missing xml")
        assert "missing xml" in prompt
        assert "previous response didn't match" in prompt

    def test_no_error_context_omits_retry_note(self):
        prompt = _build_diagram_prompt("lld", "python", "- a.py")
        assert "previous response didn't match" not in prompt

    def test_unknown_diagram_type_raises(self):
        with pytest.raises(KeyError):
            _build_diagram_prompt("bogus", "python", "- a.py")


# ─────────────────────────────────────────────
# generate_diagrams
# ─────────────────────────────────────────────

class TestGenerateDiagrams:
    def test_empty_repo_returns_empty_report_without_calling_provider(self, tmp_path):
        provider = MockProvider(raises=Exception("should not be called"))
        report = generate_diagrams(
            tmp_path, tmp_path / "out", "python", provider=provider, verbose=False,
        )
        assert report.results == []

    def test_writes_mermaid_and_drawio_for_every_requested_type(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n\n\nclass Bar:\n    pass\n")
        out = tmp_path / "out"
        provider = MockProvider(_well_formed_response())

        report = generate_diagrams(
            tmp_path, out, "python", provider=provider,
            diagram_types=("static", "dynamic"), verbose=False,
        )

        assert report.succeeded == 2
        assert report.failed == 0
        for diagram_type in ("static", "dynamic"):
            md = out / "diagrams" / f"{diagram_type}.md"
            xml = out / "diagrams" / f"{diagram_type}.drawio"
            assert md.exists() and xml.exists()
            assert "graph TD; A-->B;" in md.read_text()
            assert xml.read_text() == "<mxGraphModel/>"

    def test_defaults_to_all_four_diagram_types(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        provider = MockProvider(_well_formed_response())

        report = generate_diagrams(tmp_path, tmp_path / "out", "python",
                                    provider=provider, verbose=False)

        assert {r.diagram_type for r in report.results} == set(DIAGRAM_TYPES)

    def test_malformed_response_retries_then_succeeds(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        provider = MockProvider("not well formed at all", _well_formed_response())

        report = generate_diagrams(
            tmp_path, tmp_path / "out", "python", provider=provider,
            diagram_types=("static",), verbose=False,
        )

        assert report.results[0].status == "ok"
        assert report.results[0].attempts == 2

    def test_malformed_response_exhausts_attempts_and_records_failure(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        provider = MockProvider("still not well formed")
        provider.max_fix_attempts = 2

        report = generate_diagrams(
            tmp_path, tmp_path / "out", "python", provider=provider,
            diagram_types=("static",), verbose=False,
        )

        result = report.results[0]
        assert result.status == "failed"
        assert result.attempts == 2
        assert not (tmp_path / "out" / "diagrams" / "static.md").exists()

    def test_provider_error_recorded_as_failed_without_extra_retry(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        provider = MockProvider(raises=Exception("rate limited"))

        with patch("repo_translator.diagram.complete_with_backoff",
                    side_effect=Exception("rate limited")):
            report = generate_diagrams(
                tmp_path, tmp_path / "out", "python", provider=provider,
                diagram_types=("static",), verbose=False,
            )

        result = report.results[0]
        assert result.status == "failed"
        assert "rate limited" in (result.error or "")

    def test_unknown_diagram_type_raises(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        with pytest.raises(ValueError, match="Unknown diagram type"):
            generate_diagrams(
                tmp_path, tmp_path / "out", "python", provider=MockProvider(),
                diagram_types=("bogus",), verbose=False,
            )

    def test_one_failing_type_does_not_abort_the_others(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        # static fails to parse forever, dynamic succeeds on first try.
        provider = MockProvider("garbage", "garbage", "garbage", _well_formed_response())

        report = generate_diagrams(
            tmp_path, tmp_path / "out", "python", provider=provider,
            diagram_types=("static", "dynamic"), verbose=False,
        )

        statuses = {r.diagram_type: r.status for r in report.results}
        assert statuses["static"] == "failed"
        assert statuses["dynamic"] == "ok"

    def test_progress_events_emitted(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        provider = MockProvider(_well_formed_response())
        events: list[dict] = []

        generate_diagrams(
            tmp_path, tmp_path / "out", "python", provider=provider,
            diagram_types=("static",), verbose=False, on_progress=events.append,
        )

        types = [e["type"] for e in events]
        assert types == ["diagram_start", "diagram_done", "finished"]
        assert events[1]["status"] == "ok"
        assert events[2]["summary"]["succeeded"] == 1

    def test_repo_map_reaches_the_prompt(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        provider = CapturingProvider(_well_formed_response())

        generate_diagrams(
            tmp_path, tmp_path / "out", "python", provider=provider,
            diagram_types=("static",), verbose=False,
        )

        assert "a.py" in provider.last_prompt
        assert "foo" in provider.last_prompt


# ─────────────────────────────────────────────
# generate_diagrams — verbose output
# ─────────────────────────────────────────────

class TestGenerateDiagramsVerboseOutput:
    def test_empty_repo_prints_notice(self, tmp_path, capsys):
        generate_diagrams(tmp_path, tmp_path / "out", "python",
                           provider=MockProvider(), verbose=True)
        assert "No python files found" in capsys.readouterr().out

    def test_success_prints_checkmark(self, tmp_path, capsys):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        generate_diagrams(
            tmp_path, tmp_path / "out", "python", provider=MockProvider(_well_formed_response()),
            diagram_types=("static",), verbose=True,
        )
        out = capsys.readouterr().out
        assert "static" in out
        assert "✓" in out

    def test_retry_and_failure_print_messages(self, tmp_path, capsys):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        provider = MockProvider("garbage")
        provider.max_fix_attempts = 2

        generate_diagrams(
            tmp_path, tmp_path / "out", "python", provider=provider,
            diagram_types=("static",), verbose=True,
        )

        out = capsys.readouterr().out
        assert "retrying" in out
        assert "✗" in out


# ─────────────────────────────────────────────
# DiagramReport
# ─────────────────────────────────────────────

class TestDiagramReport:
    def test_save_writes_json_summary(self, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    pass\n")
        provider = MockProvider(_well_formed_response())
        out = tmp_path / "out"

        report = generate_diagrams(
            tmp_path, out, "python", provider=provider,
            diagram_types=("static",), verbose=False,
        )
        report.save(out)

        data = json.loads((out / "diagram_report.json").read_text())
        assert data["summary"]["succeeded"] == 1
        assert data["summary"]["failed"] == 0
        assert data["results"][0]["diagram_type"] == "static"

    def test_print_summary_does_not_raise(self, capsys):
        report = DiagramReport(lang="python", input_path="/in", output_path="/out")
        report.print_summary()
        assert "Diagram Report" in capsys.readouterr().out

    def test_print_summary_lists_ok_and_failed_results(self, capsys):
        report = DiagramReport(lang="python", input_path="/in", output_path="/out")
        report.results = [
            DiagramResult(diagram_type="static", status="ok",
                           mermaid_path="out/diagrams/static.md",
                           drawio_path="out/diagrams/static.drawio"),
            DiagramResult(diagram_type="dynamic", status="failed", error="boom"),
        ]
        report.print_summary()
        out = capsys.readouterr().out
        assert "static" in out and "static.md" in out
        assert "dynamic" in out and "boom" in out
