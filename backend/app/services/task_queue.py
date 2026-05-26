from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from redis import Redis
from rq import Queue

from backend.app.core.config import AppConfig
from backend.app.pipeline.schemas import JobExecutionState, JobStatus, JobStatusValue, model_to_dict
from backend.app.services.artifact_service import ArtifactService


ANALYZE_TASK = "analyze"
GENERATE_TASK = "generate"
REGENERATE_SECTION_TASK = "regenerate_section"
APPROVE_TEMPLATE_TASK = "approve_template"
REPLAN_EVIDENCE_TASK = "replan_evidence"


@dataclass(frozen=True)
class EnqueueResult:
    status: JobStatus
    enqueued: bool


class TaskQueue:
    def __init__(self, config: AppConfig, artifacts: ArtifactService):
        self.config = config
        self.artifacts = artifacts
        self.redis = Redis.from_url(config.queue_redis_url)
        self.queue = Queue(config.worker_queues[0], connection=self.redis)

    def enqueue_analyze(self, job_id: str) -> EnqueueResult:
        return self._enqueue(
            job_id,
            ANALYZE_TASK,
            "backend.app.worker_tasks.run_analyze",
            status=JobStatusValue.ANALYZING,
            current_step="queued_analysis",
            progress=0.12,
            message="Analysis queued.",
            args=(job_id,),
        )

    def enqueue_generate(
        self,
        job_id: str,
        generation_profile: Dict[str, Any],
        global_feedback: str,
    ) -> EnqueueResult:
        return self._enqueue(
            job_id,
            GENERATE_TASK,
            "backend.app.worker_tasks.run_generate",
            status=JobStatusValue.GENERATING,
            current_step="queued_generation",
            progress=0.72,
            message="Draft generation queued.",
            args=(job_id, generation_profile, global_feedback),
        )

    def enqueue_regenerate_section(
        self,
        job_id: str,
        section_id: str,
        feedback: str,
        generation_profile: Dict[str, Any],
    ) -> EnqueueResult:
        return self._enqueue(
            job_id,
            REGENERATE_SECTION_TASK,
            "backend.app.worker_tasks.run_regenerate_section",
            status=JobStatusValue.GENERATING,
            current_step="queued_regeneration",
            progress=0.82,
            message=f"Section regeneration queued for {section_id}.",
            args=(job_id, section_id, feedback, generation_profile),
        )

    def enqueue_template_approval(self, job_id: str, decision_request: Dict[str, Any]) -> EnqueueResult:
        return self._enqueue(
            job_id,
            APPROVE_TEMPLATE_TASK,
            "backend.app.worker_tasks.run_approve_template",
            status=JobStatusValue.ANALYZING,
            current_step="queued_evidence_planning",
            progress=0.56,
            message="Template approval recorded; evidence planning queued.",
            args=(job_id, decision_request),
        )

    def enqueue_evidence_replan(self, job_id: str, decision_request: Dict[str, Any]) -> EnqueueResult:
        return self._enqueue(
            job_id,
            REPLAN_EVIDENCE_TASK,
            "backend.app.worker_tasks.run_replan_evidence",
            status=JobStatusValue.ANALYZING,
            current_step="queued_evidence_replan",
            progress=0.56,
            message="Evidence re-plan queued.",
            args=(job_id, decision_request),
        )

    def refresh_queue_position(self, job_id: str) -> Optional[JobStatus]:
        status = self.artifacts.read_json(job_id, "status", JobStatus)
        if status.execution_state != JobExecutionState.QUEUED or not status.rq_job_id:
            return status
        position = self._queue_position(status.rq_job_id)
        return self.artifacts.update_queue_state(
            job_id,
            JobExecutionState.QUEUED,
            queue_name=status.queue_name,
            rq_job_id=status.rq_job_id,
            queue_position=position,
            retryable_action=status.retryable_action,
            active_task=status.active_task,
            message=status.message,
            status=status.status,
            current_step=status.current_step,
            progress=status.progress,
            log=False,
        )

    def reconcile_interrupted_jobs(self) -> None:
        for status in self.artifacts.list_active_jobs():
            if status.rq_job_id:
                rq_job = self.queue.fetch_job(status.rq_job_id)
                rq_status = rq_job.get_status(refresh=True) if rq_job else None
                if rq_status in {"queued", "scheduled", "deferred", "started"}:
                    self.refresh_queue_position(status.job_id)
                    continue
            self.artifacts.update_queue_state(
                status.job_id,
                JobExecutionState.INTERRUPTED,
                retryable_action=status.active_task or status.retryable_action,
                active_task=status.active_task,
                message="This job was interrupted while queued or running. Retry the last action.",
                status=JobStatusValue.FAILED,
                current_step="interrupted",
                error="Worker interrupted before the task completed.",
            )

    def _enqueue(
        self,
        job_id: str,
        action: str,
        function_path: str,
        *,
        status: JobStatusValue,
        current_step: str,
        progress: float,
        message: str,
        args: tuple,
    ) -> EnqueueResult:
        with self.artifacts.job_lock(job_id):
            current = self.artifacts._read_json_unlocked(job_id, "status", JobStatus)
            if current.execution_state in {JobExecutionState.QUEUED, JobExecutionState.RUNNING}:
                return EnqueueResult(current, False)

            rq_job = self.queue.enqueue_call(
                func=function_path,
                args=args,
                timeout=7200,
                result_ttl=3600,
                failure_ttl=86400,
            )
            current.execution_state = JobExecutionState.QUEUED
            current.queue_name = self.queue.name
            current.rq_job_id = rq_job.id
            current.queued_at = datetime.utcnow()
            current.started_at = None
            current.finished_at = None
            current.queue_position = self._queue_position(rq_job.id)
            current.retryable_action = action
            current.active_task = action
            current.status = status
            current.current_step = current_step
            current.progress = progress
            current.message = message
            current.error = None
            self.artifacts._write_json_unlocked(
                self.artifacts.artifact_path(job_id, "status"),
                model_to_dict(current),
            )
            self.artifacts._append_log_unlocked(
                job_id,
                current_step,
                message,
                f"rq_job_id={rq_job.id}; queue={self.queue.name}; position={current.queue_position}",
            )
            return EnqueueResult(current, True)

    def _queue_position(self, rq_job_id: str) -> Optional[int]:
        try:
            return list(self.queue.job_ids).index(rq_job_id) + 1
        except ValueError:
            return None
