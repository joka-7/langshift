"""
FastAPI backend for the repo-translator web UI.

Run with:
    repo-translate-ui
or directly:
    uvicorn repo_translator.webui.main:app --reload
"""
from __future__ import annotations

import json
import queue
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from repo_translator.agent import LANGUAGE_META, PRICING
from repo_translator.offline.transformer import OfflineTransformer
from repo_translator.providers.claude import CLAUDE_MODELS
from repo_translator.webui import jobs

app = FastAPI(title="repo-translator UI")

# Binding to 127.0.0.1 keeps other machines out, but it does not keep *other
# websites* out: a page the user has open can still POST to 127.0.0.1 from their
# browser. CORS is what decides whether it may read the reply, and a caller picks
# its own job output_path -- which /api/jobs/{id}/file then serves files from. With
# allow_origins=["*"] any site could register a job rooted at "/" and read the
# user's disk through it. So the allowlist is exactly the two origins that are
# really us: the Vite dev server, and the API's own port (where the production
# build is served same-origin).
_DEV_SERVER_PORT = 5173
_API_PORT = 8765
ALLOWED_ORIGINS = [
    f"http://{host}:{port}"
    for port in (_DEV_SERVER_PORT, _API_PORT)
    for host in ("localhost", "127.0.0.1")
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class EstimateRequest(BaseModel):
    input_path: str
    from_lang: str
    to_lang: str
    provider: str = "claude"
    model: str = "sonnet"
    translate_manifests: bool = True
    score_confidence: bool = True
    base_url: str | None = None


class TranslateRequest(BaseModel):
    input_path: str
    from_lang: str
    to_lang: str
    provider: str = "claude"
    model: str = "sonnet"
    output_path: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    run_tests: bool = False
    translate_manifests: bool = True
    score_confidence: bool = True
    resume: bool = False
    cross_file_context: bool = False


# ---------------------------------------------------------------------------
# Metadata endpoints
# ---------------------------------------------------------------------------

# Curated default model lists per provider, for populating the model dropdown.
# ollama / openai-compat have no fixed catalog — the UI lets the user type a model id freely.
_PROVIDER_MODELS = {
    "claude": list(CLAUDE_MODELS.keys()),
    "openai": sorted({m for (p, m) in PRICING if p == "openai"}),
    "gemini": sorted({m for (p, m) in PRICING if p == "gemini"}),
    "groq":   sorted({m for (p, m) in PRICING if p == "groq"}),
    "ollama": [],
    "openai-compat": [],
    "offline": [],
}


@app.get("/api/languages")
def get_languages():
    return jobs.supported_languages()


@app.get("/api/providers")
def get_providers():
    return {
        "providers": [
            {"name": name, "models": models, "freeform_model": not models}
            for name, models in _PROVIDER_MODELS.items()
        ],
        "offline_pairs": OfflineTransformer.supported_pairs(),
    }


# ---------------------------------------------------------------------------
# Estimate
# ---------------------------------------------------------------------------

@app.post("/api/estimate")
def post_estimate(req: EstimateRequest):
    try:
        return jobs.estimate(
            input_path=req.input_path,
            from_lang=req.from_lang,
            to_lang=req.to_lang,
            provider=req.provider,
            model=req.model,
            translate_manifests=req.translate_manifests,
            score_confidence=req.score_confidence,
            base_url=req.base_url,
        )
    except jobs.JobError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@app.post("/api/jobs")
def post_job(req: TranslateRequest):
    try:
        job = jobs.start_job(
            input_path=req.input_path,
            from_lang=req.from_lang,
            to_lang=req.to_lang,
            provider_name=req.provider,
            model=req.model,
            output_path=req.output_path,
            api_key=req.api_key,
            base_url=req.base_url,
            run_tests=req.run_tests,
            translate_manifests=req.translate_manifests,
            score_confidence=req.score_confidence,
            resume=req.resume,
            cross_file_context=req.cross_file_context,
        )
    except jobs.JobError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return job.to_dict()


@app.get("/api/jobs")
def get_jobs():
    return jobs.list_jobs()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job_dict = jobs.get_job_dict(job_id)
    if job_dict is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_dict


@app.get("/api/jobs/{job_id}/stream")
def stream_job(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found (or server restarted)")

    def event_source():
        # Replay everything emitted before this client connected, then live-tail.
        with job.lock:
            backlog = list(job.history)
        for event in backlog:
            yield f"data: {json.dumps(event)}\n\n"
        if job.status != "running":
            yield "event: close\ndata: {}\n\n"
            return
        while True:
            try:
                event = job.events.get(timeout=30)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if event is jobs.DONE:
                yield "event: close\ndata: {}\n\n"
                return
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}/report")
def get_report(job_id: str):
    output_path = _resolve_job_output_path(job_id)
    report_path = output_path / "translation_report.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not available yet")
    return json.loads(report_path.read_text(encoding="utf-8"))


@app.get("/api/jobs/{job_id}/tree")
def get_tree(job_id: str):
    output_path = _resolve_job_output_path(job_id)
    if not output_path.exists():
        return {"name": output_path.name, "type": "dir", "children": []}
    return _build_tree(output_path)


@app.get("/api/jobs/{job_id}/file")
def get_file(job_id: str, path: str = Query(...)):
    job_dict = _job_dict(job_id)
    output_root = Path(job_dict["output_path"]).resolve()
    input_root = Path(job_dict["input_path"]).resolve()

    translated_path = _safe_join(output_root, path)
    if not translated_path.exists() or not translated_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    translated = translated_path.read_text(encoding="utf-8", errors="replace")

    # Best-effort: find the corresponding source file under the input root —
    # same relative path/stem, but with one of the source language's extensions.
    source = None
    rel = translated_path.relative_to(output_root)
    from_meta = LANGUAGE_META.get(job_dict["from_lang"], {})
    for ext in from_meta.get("extensions", []):
        candidate = input_root / rel.with_suffix(ext)
        if candidate.exists():
            source = candidate.read_text(encoding="utf-8", errors="replace")
            break

    return {"path": path, "translated": translated, "source": source}


def _job_dict(job_id: str) -> dict:
    job_dict = jobs.get_job_dict(job_id)
    if job_dict is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job_dict


def _resolve_job_output_path(job_id: str) -> Path:
    return Path(_job_dict(job_id)["output_path"]).resolve()


def _safe_join(root: Path, rel_path: str) -> Path:
    """
    Resolve rel_path under root, rejecting any attempt to escape it — either via
    '../' traversal or by smuggling in an absolute path (which `root / abs_path`
    would otherwise silently resolve to the absolute path, discarding root).
    """
    if Path(rel_path).is_absolute():
        raise HTTPException(status_code=400, detail="Invalid path")
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid path") from e
    return candidate


def _build_tree(path: Path) -> dict:
    if path.is_file():
        return {"name": path.name, "type": "file"}
    children = []
    try:
        for child in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name)):
            if child.name in ("translation_report.json", "translation_report.md"):
                continue
            children.append(_build_tree(child))
    except OSError:
        pass
    return {"name": path.name, "type": "dir", "children": children}


# ---------------------------------------------------------------------------
# Static frontend (production build), if present
# ---------------------------------------------------------------------------

_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")


def run() -> None:
    """Entry point for the `repo-translate-ui` console script."""
    import uvicorn
    uvicorn.run("repo_translator.webui.main:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    run()
