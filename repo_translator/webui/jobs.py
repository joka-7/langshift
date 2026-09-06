"""
Background job manager for the web UI.

Each translation run is a Job: a background thread that calls translate_repo()
with an on_progress callback that pushes events onto a per-job queue. The API
layer streams that queue out over SSE.

Jobs live in memory for the life of the process (single-user local dev tool —
no need for a task queue). Completed jobs are additionally appended to a
JSON history file on disk so they survive a server restart; the report itself
is always re-read from <output_path>/translation_report.json rather than kept
in memory, so history entries are just pointers.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repo_translator.agent import (
    LANGUAGE_META,
    price_label,
    resolve_language,
    translate_repo,
)
from repo_translator.agent import (
    estimate_translation as _estimate_translation,
)
from repo_translator.providers import make_offline_provider, make_provider
from repo_translator.providers.base import LLMProvider

DATA_DIR = Path(os.environ.get("REPO_TRANSLATOR_DATA_DIR", Path.home() / ".repo_translator"))
HISTORY_FILE = DATA_DIR / "webui_history.json"

# Sentinel pushed onto a job's queue to signal "no more events".
DONE = object()


class JobError(ValueError):
    """Raised for client-correctable input errors (bad language, missing path, etc.)."""


@dataclass
class Job:
    id: str
    input_path: str
    output_path: str
    from_lang: str
    to_lang: str
    provider: str
    model: str
    status: str = "running"  # running | done | failed
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    events: queue.Queue[Any] = field(default_factory=queue.Queue)
    # Replayable log of every event emitted so far, for clients that connect
    # to the SSE stream after the job has already started.
    history: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "input_path": self.input_path,
            "output_path": self.output_path,
            "from_lang": self.from_lang,
            "to_lang": self.to_lang,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
        }

    def emit(self, event: dict) -> None:
        with self.lock:
            self.history.append(event)
        self.events.put(event)


JOBS: dict[str, Job] = {}


def _build_provider(
    provider_name: str,
    model: str,
    from_lang: str,
    to_lang: str,
    api_key: str | None,
    base_url: str | None,
) -> LLMProvider:
    if provider_name == "offline":
        return make_offline_provider(from_lang, to_lang)
    return make_provider(provider_name, model, api_key, base_url)


def estimate(
    input_path: str,
    from_lang: str,
    to_lang: str,
    provider: str,
    model: str,
    translate_manifests: bool = True,
    score_confidence: bool = True,
    base_url: str | None = None,
) -> dict:
    repo_path = Path(input_path).expanduser()
    if not repo_path.exists():
        raise JobError(f"Input path does not exist: {repo_path}")

    est = _estimate_translation(
        repo_path=repo_path,
        from_lang=from_lang,
        to_lang=to_lang,
        translate_manifests=translate_manifests,
        provider=provider,
        model=model,
        score_confidence=score_confidence,
    )
    est["price_label"] = price_label(provider, model, base_url)
    return est


def start_job(
    input_path: str,
    from_lang: str,
    to_lang: str,
    provider_name: str,
    model: str,
    output_path: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    run_tests: bool = False,
    translate_manifests: bool = True,
    score_confidence: bool = True,
    resume: bool = False,  # default_output_path (below) isn't unique per job, so
                           # resuming by default could silently reuse a checkpoint
                           # from an unrelated earlier run against the same repo.
    cross_file_context: bool = False,
    execute: bool = True,
) -> Job:
    repo_path = Path(input_path).expanduser()
    if not repo_path.exists():
        raise JobError(f"Input path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise JobError(f"Input path is not a directory: {repo_path}")

    try:
        from_resolved = resolve_language(from_lang)
        to_resolved   = resolve_language(to_lang)
    except ValueError as e:
        raise JobError(str(e)) from e

    out_path = (
        Path(output_path).expanduser()
        if output_path
        else repo_path.parent / f"{repo_path.name}_{to_resolved}"
    )

    try:
        provider = _build_provider(
            provider_name, model, from_resolved, to_resolved, api_key, base_url,
        )
    except (ImportError, ValueError) as e:
        raise JobError(str(e)) from e

    job = Job(
        id=uuid.uuid4().hex[:12],
        input_path=str(repo_path),
        output_path=str(out_path),
        from_lang=from_resolved,
        to_lang=to_resolved,
        provider=provider_name,
        model=model,
    )
    JOBS[job.id] = job

    thread = threading.Thread(
        target=_run_job,
        args=(job, provider, run_tests, translate_manifests, score_confidence, resume,
              cross_file_context, execute),
        daemon=True,
    )
    thread.start()
    return job


def _run_job(
    job: Job,
    provider: LLMProvider,
    run_tests: bool,
    translate_manifests: bool,
    score_confidence: bool,
    resume: bool,
    cross_file_context: bool,
    execute: bool,
) -> None:
    try:
        report = translate_repo(
            repo_path=Path(job.input_path),
            output_path=Path(job.output_path),
            from_lang=job.from_lang,
            to_lang=job.to_lang,
            provider=provider,
            verbose=False,
            translate_manifests=translate_manifests,
            run_tests_after=run_tests,
            score_confidence=score_confidence,
            resume=resume,
            cross_file_context=cross_file_context,
            execute=execute,
            on_progress=job.emit,
        )
        report.save(Path(job.output_path))
        job.status = "done"
        _append_history(job)
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        job.emit({"type": "error", "message": str(e)})
    finally:
        job.events.put(DONE)


def _append_history(job: Job) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entries = _load_history()
    entries.append(job.to_dict())
    HISTORY_FILE.write_text(json.dumps(entries[-200:], indent=2), encoding="utf-8")


def _load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def list_jobs() -> list[dict]:
    """In-memory running/just-finished jobs, merged with persisted history (newest first)."""
    live = [j.to_dict() for j in JOBS.values()]
    live_ids = {j["id"] for j in live}
    persisted = [h for h in _load_history() if h["id"] not in live_ids]
    return sorted(live + persisted, key=lambda j: j["created_at"], reverse=True)


def get_job(job_id: str) -> Job | None:
    return JOBS.get(job_id)


def get_job_dict(job_id: str) -> dict | None:
    """Look up a job by id, checking live jobs first, then persisted history."""
    job = JOBS.get(job_id)
    if job:
        return job.to_dict()
    for entry in _load_history():
        if entry["id"] == job_id:
            return entry
    return None


def supported_languages() -> list[dict]:
    return [
        {
            "name": name,
            "aliases": meta["aliases"],
            "has_runner": meta["runner"] is not None,
            "has_test_runner": meta["test_runner"] is not None,
        }
        for name, meta in LANGUAGE_META.items()
    ]
