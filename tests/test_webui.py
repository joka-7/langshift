"""
Tests for repo_translator.webui (jobs.py + main.py).

Uses the offline provider exclusively, so no network calls or mocking needed —
the offline transformer is itself a pure rule-based "fake" translator. Each
test gets its own isolated REPO_TRANSLATOR_DATA_DIR (history file) and tmp
input/output directories so jobs don't leak state between tests.
"""
from __future__ import annotations

import queue as queue_module
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from repo_translator.webui import jobs

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(jobs, "DATA_DIR", data_dir)
    monkeypatch.setattr(jobs, "HISTORY_FILE", data_dir / "webui_history.json")
    jobs.JOBS.clear()
    yield
    jobs.JOBS.clear()


@pytest.fixture
def client():
    from repo_translator.webui.main import app
    return TestClient(app)


def _wait_for_job(client, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


# ─────────────────────────────────────────────
# Metadata endpoints
# ─────────────────────────────────────────────

class TestMetadataEndpoints:
    def test_languages_lists_all_supported(self, client):
        resp = client.get("/api/languages")
        assert resp.status_code == 200
        names = {lang["name"] for lang in resp.json()}
        assert "typescript" in names
        assert "python" in names

    def test_providers_includes_offline_with_pairs(self, client):
        resp = client.get("/api/providers")
        assert resp.status_code == 200
        body = resp.json()
        provider_names = {p["name"] for p in body["providers"]}
        assert "offline" in provider_names
        assert "typescript:python" in body["offline_pairs"]


# ─────────────────────────────────────────────
# Estimate
# ─────────────────────────────────────────────

class TestEstimate:
    def test_estimate_offline_is_free(self, client, ts_repo):
        resp = client.post("/api/estimate", json={
            "input_path": str(ts_repo),
            "from_lang": "ts",
            "to_lang": "python",
            "provider": "offline",
            "model": "n/a",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_count"] == 1
        assert body["estimated_cost"] == 0.0
        assert "free" in body["price_label"]

    def test_estimate_missing_path_is_400(self, client):
        resp = client.post("/api/estimate", json={
            "input_path": "/no/such/path",
            "from_lang": "ts",
            "to_lang": "python",
            "provider": "offline",
            "model": "n/a",
        })
        assert resp.status_code == 400


# ─────────────────────────────────────────────
# Job lifecycle
# ─────────────────────────────────────────────

class TestJobLifecycle:
    def test_full_lifecycle_offline(self, client, ts_repo, tmp_path):
        output_path = tmp_path / "out"
        resp = client.post("/api/jobs", json={
            "input_path": str(ts_repo),
            "from_lang": "ts",
            "to_lang": "python",
            "provider": "offline",
            "model": "n/a",
            "output_path": str(output_path),
        })
        assert resp.status_code == 200
        job_id = resp.json()["id"]

        job = _wait_for_job(client, job_id)
        assert job["status"] == "done"

        report = client.get(f"/api/jobs/{job_id}/report").json()
        assert report["summary"]["translated"] == 1

        tree = client.get(f"/api/jobs/{job_id}/tree").json()
        child_names = {c["name"] for c in tree["children"]}
        assert "main.py" in child_names

        file_resp = client.get(f"/api/jobs/{job_id}/file", params={"path": "main.py"})
        assert file_resp.status_code == 200
        body = file_resp.json()
        assert "def add" in body["translated"]
        assert "function add" in body["source"]

        listed = client.get("/api/jobs").json()
        assert any(j["id"] == job_id for j in listed)

    def test_report_surfaces_test_results(self, client, ts_repo_with_tests, tmp_path):
        # Regression: TranslationReport.save() used to omit tests_passed and
        # test_output from translation_report.json, so a --run-tests result
        # could never reach GET /api/jobs/{id}/report or the web UI.
        output_path = tmp_path / "out"
        resp = client.post("/api/jobs", json={
            "input_path": str(ts_repo_with_tests),
            "from_lang": "ts",
            "to_lang": "python",
            "provider": "offline",
            "model": "n/a",
            "output_path": str(output_path),
            "run_tests": True,
        })
        assert resp.status_code == 200
        job_id = resp.json()["id"]

        job = _wait_for_job(client, job_id, timeout=15)
        assert job["status"] == "done"

        report = client.get(f"/api/jobs/{job_id}/report").json()
        assert "tests_passed" in report
        assert report["tests_passed"] is not None
        assert isinstance(report["test_output"], str)

    def test_unsupported_language_pair_is_400(self, client, ts_repo, tmp_path):
        resp = client.post("/api/jobs", json={
            "input_path": str(ts_repo),
            "from_lang": "ts",
            "to_lang": "java",
            "provider": "offline",
            "model": "n/a",
            "output_path": str(tmp_path / "out"),
        })
        assert resp.status_code == 400

    def test_unknown_job_is_404(self, client):
        assert client.get("/api/jobs/does-not-exist").status_code == 404
        assert client.get("/api/jobs/does-not-exist/report").status_code == 404
        assert client.get("/api/jobs/does-not-exist/tree").status_code == 404

    def test_stream_replays_history_then_closes(self, client, ts_repo, tmp_path):
        resp = client.post("/api/jobs", json={
            "input_path": str(ts_repo),
            "from_lang": "ts",
            "to_lang": "python",
            "provider": "offline",
            "model": "n/a",
            "output_path": str(tmp_path / "out"),
        })
        job_id = resp.json()["id"]
        _wait_for_job(client, job_id)

        with client.stream("GET", f"/api/jobs/{job_id}/stream") as stream:
            body = "".join(stream.iter_text())
        assert '"type": "finished"' in body
        assert "event: close" in body


# ─────────────────────────────────────────────
# Path traversal protection on /file
# ─────────────────────────────────────────────

class TestFileEndpointSafety:
    def _completed_job_id(self, client, ts_repo, tmp_path):
        resp = client.post("/api/jobs", json={
            "input_path": str(ts_repo),
            "from_lang": "ts",
            "to_lang": "python",
            "provider": "offline",
            "model": "n/a",
            "output_path": str(tmp_path / "out"),
        })
        job_id = resp.json()["id"]
        _wait_for_job(client, job_id)
        return job_id

    def test_rejects_absolute_path(self, client, ts_repo, tmp_path):
        job_id = self._completed_job_id(client, ts_repo, tmp_path)
        resp = client.get(f"/api/jobs/{job_id}/file", params={"path": "/etc/passwd"})
        assert resp.status_code == 400

    def test_rejects_dot_dot_traversal(self, client, ts_repo, tmp_path):
        job_id = self._completed_job_id(client, ts_repo, tmp_path)
        resp = client.get(
            f"/api/jobs/{job_id}/file", params={"path": "../../../../etc/passwd"}
        )
        assert resp.status_code == 400


class TestCorsPolicy:
    """
    The API binds to 127.0.0.1, but "localhost only" is not a boundary a browser
    enforces for *sending* — any page the user visits can POST to 127.0.0.1. What
    stops it reading the reply is CORS. Since a caller picks a job's output_path
    and /file then serves anything under it, echoing an arbitrary Origin would let
    any website register output_path="/" and read files off the user's disk.
    """

    def test_rejects_arbitrary_origin(self, client):
        resp = client.get("/api/languages", headers={"Origin": "https://evil.example"})
        assert resp.status_code == 200
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}

    @pytest.mark.parametrize(
        "origin",
        [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8765",
            "http://127.0.0.1:8765",
        ],
    )
    def test_allows_local_dev_origins(self, client, origin):
        resp = client.get("/api/languages", headers={"Origin": origin})
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == origin


# ─────────────────────────────────────────────
# 404s and edge cases on /stream, /report, /tree, /file (main.py)
# ─────────────────────────────────────────────

class TestJobSubResourceEdgeCases:
    def test_stream_unknown_job_is_404(self, client):
        resp = client.get("/api/jobs/does-not-exist/stream")
        assert resp.status_code == 404

    def test_report_not_yet_written_is_404(self, tmp_path, client):
        # A job that exists but hasn't finished translating yet (no
        # translation_report.json written) — distinct from an unknown job id,
        # which /report already covers via _job_dict's 404.
        job = jobs.Job(
            id="fake-no-report", input_path=str(tmp_path / "in"),
            output_path=str(tmp_path / "out-not-written"),
            from_lang="typescript", to_lang="python", provider="offline", model="n/a",
        )
        jobs.JOBS[job.id] = job
        resp = client.get(f"/api/jobs/{job.id}/report")
        assert resp.status_code == 404
        assert "not available yet" in resp.json()["detail"]

    def test_tree_on_nonexistent_output_dir_returns_empty(self, tmp_path, client):
        job = jobs.Job(
            id="fake-no-output", input_path=str(tmp_path / "in"),
            output_path=str(tmp_path / "does-not-exist"),
            from_lang="typescript", to_lang="python", provider="offline", model="n/a",
        )
        jobs.JOBS[job.id] = job
        resp = client.get(f"/api/jobs/{job.id}/tree")
        assert resp.status_code == 200
        assert resp.json()["children"] == []

    def test_file_not_found_is_404(self, client, ts_repo, tmp_path):
        job_id = self._completed_job_id(client, ts_repo, tmp_path)
        resp = client.get(f"/api/jobs/{job_id}/file", params={"path": "nonexistent.py"})
        assert resp.status_code == 404

    def test_file_with_no_matching_source_has_null_source(self, client, ts_repo, tmp_path):
        job_id = self._completed_job_id(client, ts_repo, tmp_path)
        job_dict = client.get(f"/api/jobs/{job_id}").json()
        # A translated file with no corresponding *.ts under the input root —
        # the source-lookup loop runs through every extension and finds none.
        extra = Path(job_dict["output_path"]) / "orphan.py"
        extra.write_text("# no source counterpart")

        resp = client.get(f"/api/jobs/{job_id}/file", params={"path": "orphan.py"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["translated"] == "# no source counterpart"
        assert body["source"] is None

    def test_tree_swallows_unreadable_directory(self, client, ts_repo, tmp_path):
        # _build_tree's OSError guard (e.g. a permission-denied subdirectory)
        # was previously unexercised — the tree should degrade to an empty
        # children list for that node rather than raising.
        job_id = self._completed_job_id(client, ts_repo, tmp_path)
        with patch("repo_translator.webui.main.Path.iterdir", side_effect=OSError("denied")):
            resp = client.get(f"/api/jobs/{job_id}/tree")
        assert resp.status_code == 200
        assert resp.json()["children"] == []

    def _completed_job_id(self, client, ts_repo, tmp_path):
        resp = client.post("/api/jobs", json={
            "input_path": str(ts_repo),
            "from_lang": "ts",
            "to_lang": "python",
            "provider": "offline",
            "model": "n/a",
            "output_path": str(tmp_path / "out"),
        })
        job_id = resp.json()["id"]
        _wait_for_job(client, job_id)
        return job_id


# ─────────────────────────────────────────────
# History persistence (jobs.py)
# ─────────────────────────────────────────────

class TestHistoryPersistence:
    def test_finished_job_survives_clearing_in_memory_jobs(self, client, ts_repo, tmp_path):
        resp = client.post("/api/jobs", json={
            "input_path": str(ts_repo),
            "from_lang": "ts",
            "to_lang": "python",
            "provider": "offline",
            "model": "n/a",
            "output_path": str(tmp_path / "out"),
        })
        job_id = resp.json()["id"]
        _wait_for_job(client, job_id)

        jobs.JOBS.clear()

        job_dict = client.get(f"/api/jobs/{job_id}").json()
        assert job_dict["status"] == "done"
        assert any(j["id"] == job_id for j in client.get("/api/jobs").json())

    def test_corrupted_history_file_is_treated_as_empty(self, tmp_path):
        jobs.DATA_DIR.mkdir(parents=True, exist_ok=True)
        jobs.HISTORY_FILE.write_text("not valid json {{{")
        assert jobs._load_history() == []

    def test_missing_history_file_is_treated_as_empty(self):
        assert not jobs.HISTORY_FILE.exists()
        assert jobs._load_history() == []


# ─────────────────────────────────────────────
# _run_job failure path (jobs.py)
# ─────────────────────────────────────────────

class TestRunJobFailure:
    def test_exception_during_translate_marks_job_failed(self, ts_repo, tmp_path):
        with patch("repo_translator.webui.jobs.translate_repo", side_effect=RuntimeError("boom")):
            job = jobs.start_job(
                input_path=str(ts_repo), from_lang="ts", to_lang="python",
                provider_name="offline", model="n/a", output_path=str(tmp_path / "out"),
            )
            deadline = time.time() + 5
            while job.status == "running" and time.time() < deadline:
                time.sleep(0.02)

        assert job.status == "failed"
        assert job.error == "boom"

    def test_failure_is_visible_through_the_api(self, client, ts_repo, tmp_path):
        with patch("repo_translator.webui.jobs.translate_repo", side_effect=RuntimeError("kaboom")):
            resp = client.post("/api/jobs", json={
                "input_path": str(ts_repo),
                "from_lang": "ts",
                "to_lang": "python",
                "provider": "offline",
                "model": "n/a",
                "output_path": str(tmp_path / "out"),
            })
            job_id = resp.json()["id"]
            job = _wait_for_job(client, job_id)

        assert job["status"] == "failed"
        assert job["error"] == "kaboom"


# ─────────────────────────────────────────────
# start_job validation + provider construction (jobs.py)
# ─────────────────────────────────────────────

class TestStartJobValidation:
    def test_missing_input_path_raises_joberror(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        with pytest.raises(jobs.JobError, match="does not exist"):
            jobs.start_job(str(missing), "ts", "python", "offline", "n/a")

    def test_input_path_that_is_a_file_raises(self, tmp_path):
        not_a_dir = tmp_path / "file.txt"
        not_a_dir.write_text("x")
        with pytest.raises(jobs.JobError, match="not a directory"):
            jobs.start_job(str(not_a_dir), "ts", "python", "offline", "n/a")

    def test_unknown_from_language_raises_joberror(self, ts_repo):
        with pytest.raises(jobs.JobError):
            jobs.start_job(str(ts_repo), "brainfuck", "python", "offline", "n/a")

    def test_non_offline_provider_routes_through_make_provider(self, ts_repo, tmp_path):
        # Every other webui test uses the offline provider, so _build_provider's
        # non-offline branch (make_provider(...)) was otherwise never exercised.
        fake_provider = MagicMock()
        fake_report = MagicMock()
        with patch("repo_translator.webui.jobs.make_provider", return_value=fake_provider) as mock_make, \
             patch("repo_translator.webui.jobs.translate_repo", return_value=fake_report):
            job = jobs.start_job(
                str(ts_repo), "ts", "python", "claude", "sonnet",
                output_path=str(tmp_path / "out"), api_key="key123",
            )
            deadline = time.time() + 5
            while job.status == "running" and time.time() < deadline:
                time.sleep(0.02)

        mock_make.assert_called_once_with("claude", "sonnet", "key123", None)
        assert job.status == "done"

    def test_default_output_path_derived_when_not_given(self, ts_repo):
        with patch("repo_translator.webui.jobs.translate_repo", return_value=MagicMock()):
            job = jobs.start_job(str(ts_repo), "ts", "python", "offline", "n/a")
            deadline = time.time() + 5
            while job.status == "running" and time.time() < deadline:
                time.sleep(0.02)
        assert job.output_path == str(ts_repo.parent / f"{ts_repo.name}_python")


# ─────────────────────────────────────────────
# SSE stream live-tail (main.py)
# ─────────────────────────────────────────────

class TestStreamLiveTail:
    def test_keep_alive_then_close_on_done_sentinel(self, client):
        # A real 30s queue.get(timeout=30) is too slow to actually wait out in
        # a test, so job.events is swapped for a fake whose .get() raises
        # queue.Empty once (exercising the keep-alive branch) then returns the
        # DONE sentinel (exercising the close branch) — both previously dark
        # since every other test waits for the job to finish before streaming.
        job = jobs.Job(
            id="fake-live-tail", input_path="/in", output_path="/out",
            from_lang="typescript", to_lang="python", provider="offline", model="n/a",
        )
        job.status = "running"
        fake_queue = MagicMock()
        fake_queue.get.side_effect = [queue_module.Empty(), jobs.DONE]
        job.events = fake_queue
        jobs.JOBS[job.id] = job

        with client.stream("GET", f"/api/jobs/{job.id}/stream") as stream:
            body = "".join(stream.iter_text())

        assert "keep-alive" in body
        assert "event: close" in body

    def test_live_event_is_streamed_before_close(self, client):
        job = jobs.Job(
            id="fake-live-event", input_path="/in", output_path="/out",
            from_lang="typescript", to_lang="python", provider="offline", model="n/a",
        )
        job.status = "running"
        fake_queue = MagicMock()
        fake_queue.get.side_effect = [{"type": "file_start", "path": "a.ts"}, jobs.DONE]
        job.events = fake_queue
        jobs.JOBS[job.id] = job

        with client.stream("GET", f"/api/jobs/{job.id}/stream") as stream:
            body = "".join(stream.iter_text())

        assert '"type": "file_start"' in body
        assert "event: close" in body
