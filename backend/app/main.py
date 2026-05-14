import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.core.config import load_config
from backend.app.core.model_catalog import ModelCatalog, catalog_model_to_dict
from backend.app.core.preflight import run_preflight
from backend.app.pipeline.schemas import (
    CreateJobRequest,
    GateName,
    GenerateRequest,
    JobStatusValue,
    RegenerateSectionRequest,
    ReviewDecisionRequest,
)
from backend.app.services.artifact_service import ArtifactService, normalize_status_for_ui
from backend.app.services.job_service import JobService


logger = logging.getLogger(__name__)

config = load_config()
preflight_report = run_preflight(config)
for warning in preflight_report.warnings:
    logger.warning("Startup preflight warning: %s", warning)
artifacts = ArtifactService(config.data_root)
jobs = JobService(config, artifacts)
model_catalog = ModelCatalog(config.models_path)


async def _cleanup_expired_jobs_loop() -> None:
    while True:
        await asyncio.sleep(config.job_cleanup_interval_seconds)
        artifacts.cleanup_expired_jobs(config.job_retention_days)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    artifacts.cleanup_expired_jobs(config.job_retention_days)
    cleanup_task = None
    if config.job_retention_days > 0 and config.job_cleanup_interval_seconds > 0:
        cleanup_task = asyncio.create_task(_cleanup_expired_jobs_loop())
    try:
        yield
    finally:
        if cleanup_task:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task


app = FastAPI(title="SOP Generation Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/models")
def list_models():
    return {"models": [catalog_model_to_dict(model) for model in model_catalog.list_models()]}


@app.get("/api/jobs")
def list_jobs():
    removed = artifacts.cleanup_expired_jobs(config.job_retention_days)
    return {
        "jobs": artifacts.list_jobs(),
        "retention_days": config.job_retention_days,
        "cleanup_interval_seconds": config.job_cleanup_interval_seconds,
        "removed_expired_jobs": removed,
    }


@app.post("/api/jobs")
def create_job(request: CreateJobRequest):
    job_id = artifacts.create_job(request.review_settings)
    artifacts.write_json(job_id, "generation_profile", request.generation_profile)
    artifacts.write_json(job_id, "model_config", request.model_selection)
    return {"job_id": job_id, "status": artifacts.read_json(job_id, "status")}


@app.post("/api/jobs/{job_id}/upload")
async def upload_files(
    job_id: str,
    source_files: Optional[List[UploadFile]] = File(default=None),
    reference_files: Optional[List[UploadFile]] = File(default=None),
    template_file: Optional[UploadFile] = File(default=None),
):
    try:
        uploaded = await artifacts.save_uploads(job_id, source_files, reference_files, template_file)
        return uploaded
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    try:
        artifacts.delete_job(job_id)
        return {"deleted": True, "job_id": job_id}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/jobs/{job_id}/analyze")
def analyze(
    job_id: str,
    background_tasks: BackgroundTasks,
    background: bool = Query(default=False),
):
    if background:
        artifacts.ensure_job(job_id)
        artifacts.update_status(
            job_id,
            JobStatusValue.ANALYZING,
            "queued_analysis",
            0.12,
            "Analysis queued and running in the background.",
            pending_gates=[],
        )
        background_tasks.add_task(_run_analyze_background, job_id)
        return artifacts.read_json(job_id, "status")
    try:
        return jobs.analyze(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        artifacts.update_status(job_id, JobStatusValue.FAILED, "failed", 0, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


def _run_analyze_background(job_id: str) -> None:
    try:
        jobs.analyze(job_id)
    except Exception as exc:
        artifacts.update_status(
            job_id,
            JobStatusValue.FAILED,
            "failed",
            0,
            "Analysis failed.",
            error=str(exc),
        )


@app.post("/api/jobs/{job_id}/review/{gate}")
def approve_gate(job_id: str, gate: GateName, request: ReviewDecisionRequest):
    try:
        return jobs.approve_gate(job_id, gate, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/jobs/{job_id}/generate")
def generate(job_id: str, request: GenerateRequest):
    try:
        return jobs.generate(job_id, request.generation_profile, request.global_feedback)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jobs/{job_id}/sections/{section_id}/regenerate")
def regenerate_section(job_id: str, section_id: str, request: RegenerateSectionRequest):
    try:
        return jobs.regenerate_section(job_id, section_id, request.feedback, request.generation_profile)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/jobs/{job_id}/status")
def status(job_id: str):
    try:
        return normalize_status_for_ui(artifacts.read_json(job_id, "status"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/jobs/{job_id}/logs")
def logs(job_id: str):
    try:
        return {"logs": artifacts.read_logs(job_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/jobs/{job_id}/artifacts/{artifact_name}")
def get_artifact(job_id: str, artifact_name: str):
    try:
        path = artifacts.artifact_path(job_id, artifact_name)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_name}")
        if path.suffix == ".json":
            return artifacts.read_json(job_id, artifact_name)
        return FileResponse(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/jobs/{job_id}/download/{artifact_name}")
def download_artifact(job_id: str, artifact_name: str):
    try:
        path = artifacts.artifact_path(job_id, artifact_name)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_name}")
        return FileResponse(path, filename=Path(path).name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


if config.frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(config.frontend_dist), html=True), name="frontend")
