import json
import shutil
import time
from pathlib import Path
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import UploadFile
from pydantic import BaseModel

from backend.app.pipeline.schemas import (
    GateName,
    JobLogEntry,
    JobStatus,
    JobStatusValue,
    ReviewSettings,
    UploadedFiles,
    model_to_dict,
)


class ArtifactService:
    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.data_root.mkdir(parents=True, exist_ok=True)

    def create_job(self, review_settings: ReviewSettings, owner_id: Optional[str] = None) -> str:
        job_id = uuid4().hex
        for subdir in [
            "uploads/source",
            "uploads/reference",
            "uploads/template",
            "intermediate",
            "outputs",
            "logs",
        ]:
            (self.job_dir(job_id) / subdir).mkdir(parents=True, exist_ok=True)
        self.write_json(
            job_id,
            "status",
            JobStatus(
                job_id=job_id,
                status=JobStatusValue.PENDING,
                owner_id=owner_id,
                current_step="created",
                progress=0,
                message="Job created.",
                review_settings=review_settings,
            ),
        )
        self.write_json(job_id, "uploaded_files", UploadedFiles())
        self.append_log(job_id, "created", "Job created.")
        return job_id

    def job_dir(self, job_id: str) -> Path:
        return self.data_root / job_id

    def artifact_path(self, job_id: str, name: str) -> Path:
        if name in {"status", "uploaded_files"}:
            return self.job_dir(job_id) / f"{name}.json"
        if name.endswith(".docx"):
            return self.job_dir(job_id) / "outputs" / name
        if name.endswith("_report") or name in {"coverage_report", "debug_report", "provenance_report"}:
            return self.job_dir(job_id) / "outputs" / f"{name}.json"
        return self.job_dir(job_id) / "intermediate" / f"{name}.json"

    def ensure_job(self, job_id: str) -> None:
        if not self.job_dir(job_id).exists():
            raise FileNotFoundError(f"Unknown job_id: {job_id}")

    def owner_id(self, job_id: str) -> Optional[str]:
        status = self.read_json(job_id, "status", JobStatus)
        return status.owner_id

    def ensure_job_owner(self, job_id: str, owner_id: str) -> None:
        self.ensure_job(job_id)
        if self.owner_id(job_id) != owner_id:
            raise FileNotFoundError(f"Unknown job_id: {job_id}")

    def delete_job(self, job_id: str) -> None:
        target = self._safe_job_dir(job_id)
        if not target.exists():
            raise FileNotFoundError(f"Unknown job_id: {job_id}")
        shutil.rmtree(target)

    def cleanup_expired_jobs(self, retention_days: float) -> List[str]:
        if retention_days <= 0 or not self.data_root.exists():
            return []

        cutoff = time.time() - retention_days * 24 * 60 * 60
        removed: List[str] = []
        for status_path in self.data_root.glob("*/status.json"):
            try:
                if status_path.stat().st_mtime >= cutoff:
                    continue
                job_dir = self._safe_job_dir(status_path.parent.name)
                if not job_dir.exists():
                    continue
                shutil.rmtree(job_dir)
                removed.append(status_path.parent.name)
            except Exception:
                continue
        return removed

    async def save_uploads(
        self,
        job_id: str,
        source_files: Optional[List[UploadFile]],
        reference_files: Optional[List[UploadFile]],
        template_file: Optional[UploadFile],
    ) -> UploadedFiles:
        self.ensure_job(job_id)
        uploaded = self.read_json(job_id, "uploaded_files", UploadedFiles)

        if source_files:
            for item in source_files:
                uploaded.source_files.append(await self._save_upload(job_id, "source", item))
        if reference_files:
            for item in reference_files:
                uploaded.reference_files.append(await self._save_upload(job_id, "reference", item))
        if template_file:
            uploaded.template_file = await self._save_upload(job_id, "template", template_file)

        self.write_json(job_id, "uploaded_files", uploaded)
        self.append_log(
            job_id,
            "upload",
            "Files uploaded.",
            f"sources={len(uploaded.source_files)}, references={len(uploaded.reference_files)}, template={uploaded.template_file}",
        )
        return uploaded

    async def _save_upload(self, job_id: str, bucket: str, upload: UploadFile) -> str:
        file_name = Path(upload.filename or f"upload-{uuid4().hex}").name
        target = self.job_dir(job_id) / "uploads" / bucket / file_name
        with target.open("wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        return str(target)

    def write_json(self, job_id: str, name: str, value: Any) -> Path:
        self.ensure_job(job_id)
        target = self.artifact_path(job_id, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = _to_jsonable(value)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def read_json(self, job_id: str, name: str, model: Optional[type] = None) -> Any:
        self.ensure_job(job_id)
        target = self.artifact_path(job_id, name)
        data = json.loads(target.read_text(encoding="utf-8"))
        if model:
            return model(**data)
        return data

    def maybe_read_json(self, job_id: str, name: str, model: Optional[type] = None) -> Any:
        target = self.artifact_path(job_id, name)
        if not target.exists():
            return None
        return self.read_json(job_id, name, model)

    def delete_artifacts(self, job_id: str, names: List[str]) -> None:
        self.ensure_job(job_id)
        for name in names:
            target = self.artifact_path(job_id, name)
            if target.exists():
                target.unlink()

    def delete_intermediate_dir(self, job_id: str, name: str) -> None:
        self.ensure_job(job_id)
        target = (self.job_dir(job_id) / "intermediate" / name).resolve()
        root = (self.job_dir(job_id) / "intermediate").resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Invalid intermediate directory: {name}") from exc
        if target.exists():
            shutil.rmtree(target)

    def update_status(
        self,
        job_id: str,
        status: JobStatusValue,
        step: str,
        progress: float,
        message: Optional[str] = None,
        error: Optional[str] = None,
        pending_gates: Optional[List] = None,
    ) -> JobStatus:
        current = self.read_json(job_id, "status", JobStatus)
        current.status = status
        current.current_step = step
        current.progress = progress
        current.message = message
        current.error = error
        if pending_gates is not None:
            current.pending_gates = pending_gates
        self.write_json(job_id, "status", current)
        self.append_log(job_id, step, message or status.value, error)
        return current

    def append_log(
        self,
        job_id: str,
        step: str,
        message: str,
        technical_detail: Optional[str] = None,
        level: str = "info",
    ) -> None:
        self.ensure_job(job_id)
        entry = JobLogEntry(
            level=level, step=step, message=message, technical_detail=technical_detail
        )
        target = self.job_dir(job_id) / "logs" / "events.jsonl"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(model_to_dict(entry), ensure_ascii=False) + "\n")

    def read_logs(self, job_id: str) -> List[Dict]:
        self.ensure_job(job_id)
        target = self.job_dir(job_id) / "logs" / "events.jsonl"
        if not target.exists():
            return []
        return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line]

    def copy_output(self, job_id: str, source: Path, output_name: str) -> Path:
        target = self.job_dir(job_id) / "outputs" / output_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target

    def list_artifacts(self, job_id: str) -> Dict[str, List[str]]:
        self.ensure_job(job_id)
        result: Dict[str, List[str]] = {}
        for folder in ["intermediate", "outputs", "logs"]:
            root = self.job_dir(job_id) / folder
            result[folder] = [str(path.relative_to(self.job_dir(job_id))) for path in root.rglob("*") if path.is_file()]
        return result

    def list_jobs(self, owner_id: Optional[str] = None) -> List[Dict]:
        if not self.data_root.exists():
            return []
        jobs = []
        for status_path in self.data_root.glob("*/status.json"):
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if owner_id is not None and status.get("owner_id") != owner_id:
                continue
            job_dir = status_path.parent
            uploaded_path = job_dir / "uploaded_files.json"
            uploaded = {}
            if uploaded_path.exists():
                try:
                    uploaded = json.loads(uploaded_path.read_text(encoding="utf-8"))
                except Exception:
                    uploaded = {}
            display_status = normalize_status_for_ui(status)
            jobs.append(
                {
                    "job_id": display_status.get("job_id", job_dir.name),
                    "status": display_status.get("status"),
                    "current_step": display_status.get("current_step"),
                    "progress": display_status.get("progress", 0),
                    "message": display_status.get("message"),
                    "updated_at": status_path.stat().st_mtime,
                    "source_count": len(uploaded.get("source_files") or []),
                    "reference_count": len(uploaded.get("reference_files") or []),
                    "template_file": uploaded.get("template_file"),
                }
            )
        jobs.sort(key=lambda item: item["updated_at"], reverse=True)
        return jobs

    def _safe_job_dir(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id or job_id in {".", ".."}:
            raise ValueError(f"Invalid job_id: {job_id}")

        root = self.data_root.resolve()
        target = (self.data_root / job_id).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Invalid job_id: {job_id}") from exc
        return target


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return model_to_dict(value)
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def normalize_status_for_ui(status: Dict) -> Dict:
    if status.get("status") != JobStatusValue.COMPLETED.value or status.get("current_step") != "draft_ready":
        return status

    normalized = dict(status)
    review_settings = normalized.get("review_settings") or {}
    pending_gates = []
    if review_settings.get("draft_review_enabled", True):
        pending_gates = [GateName.DRAFT.value]
    normalized.update(
        {
            "status": JobStatusValue.NEEDS_REVIEW.value,
            "progress": 0.95,
            "message": "Draft generated. Review is pending.",
            "pending_gates": pending_gates,
        }
    )
    return normalized
