import asyncio
import logging
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
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
    TemplateRefineRequest,
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
    allow_origins=list(config.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_CLIENT_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


def _is_valid_client_id(value: Optional[str]) -> bool:
    return bool(value and _CLIENT_ID_PATTERN.fullmatch(value))


def _set_client_cookie(response: Response, owner_id: str) -> None:
    max_age = None
    if config.client_cookie_max_age_days > 0:
        max_age = int(config.client_cookie_max_age_days * 24 * 60 * 60)
    response.set_cookie(
        key=config.client_cookie_name,
        value=owner_id,
        max_age=max_age,
        httponly=True,
        secure=config.client_cookie_secure,
        samesite=config.client_cookie_samesite,
    )


def _get_or_create_owner_id(request: Request, response: Response) -> str:
    current = request.cookies.get(config.client_cookie_name)
    if _is_valid_client_id(current):
        return current
    owner_id = uuid4().hex
    _set_client_cookie(response, owner_id)
    return owner_id


def _require_owner_id(request: Request) -> str:
    owner_id = request.cookies.get(config.client_cookie_name)
    if not _is_valid_client_id(owner_id):
        raise HTTPException(status_code=404, detail="Unknown job_id")
    return owner_id


def _ensure_owned_job(job_id: str, owner_id: str) -> None:
    try:
        artifacts.ensure_job_owner(job_id, owner_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/models")
def list_models():
    return {"models": [catalog_model_to_dict(model) for model in model_catalog.list_models()]}


@app.get("/api/jobs")
def list_jobs(request: Request, response: Response):
    owner_id = _get_or_create_owner_id(request, response)
    removed = artifacts.cleanup_expired_jobs(config.job_retention_days)
    return {
        "jobs": artifacts.list_jobs(owner_id),
        "retention_days": config.job_retention_days,
        "cleanup_interval_seconds": config.job_cleanup_interval_seconds,
        "removed_expired_jobs": removed,
    }


@app.post("/api/jobs")
def create_job(request: Request, response: Response, job_request: CreateJobRequest):
    owner_id = _get_or_create_owner_id(request, response)
    job_id = artifacts.create_job(job_request.review_settings, owner_id=owner_id)
    artifacts.write_json(job_id, "generation_profile", job_request.generation_profile)
    artifacts.write_json(job_id, "model_config", job_request.model_selection)
    return {"job_id": job_id, "status": artifacts.read_json(job_id, "status")}


@app.post("/api/jobs/{job_id}/upload")
async def upload_files(
    request: Request,
    job_id: str,
    source_files: Optional[List[UploadFile]] = File(default=None),
    reference_files: Optional[List[UploadFile]] = File(default=None),
    template_file: Optional[UploadFile] = File(default=None),
):
    _ensure_owned_job(job_id, _require_owner_id(request))
    try:
        uploaded = await artifacts.save_uploads(job_id, source_files, reference_files, template_file)
        return uploaded
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.delete("/api/jobs/{job_id}")
def delete_job(request: Request, job_id: str):
    _ensure_owned_job(job_id, _require_owner_id(request))
    try:
        artifacts.delete_job(job_id)
        return {"deleted": True, "job_id": job_id}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/jobs/{job_id}/analyze")
def analyze(
    request: Request,
    job_id: str,
    background_tasks: BackgroundTasks,
    background: bool = Query(default=False),
):
    _ensure_owned_job(job_id, _require_owner_id(request))
    if background:
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
def approve_gate(request: Request, job_id: str, gate: GateName, decision_request: ReviewDecisionRequest):
    _ensure_owned_job(job_id, _require_owner_id(request))
    try:
        return jobs.approve_gate(job_id, gate, decision_request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/jobs/{job_id}/template/refine")
def refine_template(request: Request, job_id: str, refine_request: TemplateRefineRequest):
    _ensure_owned_job(job_id, _require_owner_id(request))
    try:
        return jobs.refine_template_sections(job_id, refine_request.feedback)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/jobs/{job_id}/evidence/replan")
def replan_evidence(request: Request, job_id: str, decision_request: ReviewDecisionRequest):
    _ensure_owned_job(job_id, _require_owner_id(request))
    try:
        return jobs.replan_evidence(job_id, decision_request)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/jobs/{job_id}/generate")
def generate(request: Request, job_id: str, generate_request: GenerateRequest):
    _ensure_owned_job(job_id, _require_owner_id(request))
    try:
        return jobs.generate(job_id, generate_request.generation_profile, generate_request.global_feedback)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jobs/{job_id}/sections/{section_id}/regenerate")
def regenerate_section(request: Request, job_id: str, section_id: str, regenerate_request: RegenerateSectionRequest):
    _ensure_owned_job(job_id, _require_owner_id(request))
    try:
        return jobs.regenerate_section(job_id, section_id, regenerate_request.feedback, regenerate_request.generation_profile)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/jobs/{job_id}/status")
def status(request: Request, job_id: str):
    _ensure_owned_job(job_id, _require_owner_id(request))
    try:
        return normalize_status_for_ui(artifacts.read_json(job_id, "status"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/jobs/{job_id}/logs")
def logs(request: Request, job_id: str):
    _ensure_owned_job(job_id, _require_owner_id(request))
    try:
        return {"logs": artifacts.read_logs(job_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/jobs/{job_id}/artifacts/{artifact_name}")
def get_artifact(request: Request, job_id: str, artifact_name: str):
    _ensure_owned_job(job_id, _require_owner_id(request))
    try:
        path = artifacts.artifact_path(job_id, artifact_name)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_name}")
        if path.suffix == ".json":
            return artifacts.read_json(job_id, artifact_name)
        return FileResponse(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/jobs/{job_id}/images/{image_id}")
def get_job_image(request: Request, job_id: str, image_id: str):
    _ensure_owned_job(job_id, _require_owner_id(request))
    if not re.fullmatch(r"[A-Za-z0-9_-]+", image_id):
        raise HTTPException(status_code=400, detail="Invalid image_id")
    image_root = (artifacts.job_dir(job_id) / "intermediate" / "images").resolve()
    path = (image_root / f"{image_id}.png").resolve()
    try:
        path.relative_to(image_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid image_id") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type="image/png", filename=f"{image_id}.png")


@app.get("/api/jobs/{job_id}/download/{artifact_name}")
def download_artifact(request: Request, job_id: str, artifact_name: str):
    _ensure_owned_job(job_id, _require_owner_id(request))
    try:
        path = artifacts.artifact_path(job_id, artifact_name)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_name}")
        return FileResponse(path, filename=Path(path).name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


if config.frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(config.frontend_dist), html=True), name="frontend")
