"""
Tests for repo_translator.webui (jobs.py + main.py).

Uses the offline provider exclusively, so no network calls or mocking needed —
the offline transformer is itself a pure rule-based "fake" translator. Each
test gets its own isolated REPO_TRANSLATOR_DATA_DIR (history file) and tmp
input/output directories so jobs don't leak state between tests.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from repo_translator.webui import jobs


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


@pytest.fixture
def ts_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.ts").write_text(
        "function add(a: number, b: number): number {\n  return a + b;\n}\n"
    )
    return repo


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
