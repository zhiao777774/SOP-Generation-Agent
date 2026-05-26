from __future__ import annotations

from backend.app.core.config import load_config
from backend.app.pipeline.schemas import (
    GenerationProfile,
    GateName,
    JobExecutionState,
    JobStatusValue,
    ReviewDecisionRequest,
)
from backend.app.services.artifact_service import ArtifactService
from backend.app.services.concurrency import stage_limit
from backend.app.services.job_service import JobService


def run_analyze(job_id: str) -> None:
    config, artifacts, jobs = _services()
    artifacts.update_queue_state(
        job_id,
        JobExecutionState.RUNNING,
        status=JobStatusValue.ANALYZING,
        current_step="analysis_running",
        progress=0.13,
        active_task="analyze",
        retryable_action="analyze",
        message="Analysis running.",
    )
    try:
        with stage_limit("analyze"):
            jobs.analyze(job_id)
        artifacts.update_queue_state(job_id, JobExecutionState.IDLE)
    except Exception as exc:
        artifacts.update_queue_state(
            job_id,
            JobExecutionState.IDLE,
            message="Analysis task failed.",
            status=JobStatusValue.FAILED,
            current_step="failed",
            error=str(exc),
        )
        raise


def run_generate(job_id: str, profile_data: dict, global_feedback: str = "") -> None:
    config, artifacts, jobs = _services()
    artifacts.update_queue_state(
        job_id,
        JobExecutionState.RUNNING,
        status=JobStatusValue.GENERATING,
        current_step="generate_sections",
        progress=0.75,
        active_task="generate",
        retryable_action="generate",
        message="Draft generation running.",
    )
    try:
        with stage_limit("generate"):
            jobs.generate(job_id, GenerationProfile(**profile_data), global_feedback)
        artifacts.update_queue_state(job_id, JobExecutionState.IDLE)
    except Exception as exc:
        artifacts.update_queue_state(
            job_id,
            JobExecutionState.IDLE,
            message="Draft generation task failed.",
            status=JobStatusValue.FAILED,
            current_step="failed",
            error=str(exc),
        )
        raise


def run_approve_template(job_id: str, decision_request_data: dict) -> None:
    config, artifacts, jobs = _services()
    artifacts.update_queue_state(
        job_id,
        JobExecutionState.RUNNING,
        status=JobStatusValue.ANALYZING,
        current_step="plan_evidence",
        progress=0.56,
        active_task="approve_template",
        retryable_action="approve_template",
        message="Planning evidence from approved template sections.",
    )
    try:
        with stage_limit("analyze"):
            jobs.approve_gate(job_id, GateName.TEMPLATE, ReviewDecisionRequest(**decision_request_data))
        artifacts.update_queue_state(job_id, JobExecutionState.IDLE)
    except Exception as exc:
        artifacts.update_queue_state(
            job_id,
            JobExecutionState.IDLE,
            message="Evidence planning task failed.",
            status=JobStatusValue.FAILED,
            current_step="failed",
            error=str(exc),
        )
        raise


def run_replan_evidence(job_id: str, decision_request_data: dict) -> None:
    config, artifacts, jobs = _services()
    artifacts.update_queue_state(
        job_id,
        JobExecutionState.RUNNING,
        status=JobStatusValue.ANALYZING,
        current_step="replan_evidence",
        progress=0.56,
        active_task="replan_evidence",
        retryable_action="replan_evidence",
        message="Re-planning evidence from reviewer feedback.",
    )
    try:
        with stage_limit("analyze"):
            jobs.replan_evidence(job_id, ReviewDecisionRequest(**decision_request_data))
        artifacts.update_queue_state(job_id, JobExecutionState.IDLE)
    except Exception as exc:
        artifacts.update_queue_state(
            job_id,
            JobExecutionState.IDLE,
            message="Evidence re-plan task failed.",
            status=JobStatusValue.FAILED,
            current_step="failed",
            error=str(exc),
        )
        raise


def run_regenerate_section(
    job_id: str,
    section_id: str,
    feedback: str,
    profile_data: dict,
) -> None:
    config, artifacts, jobs = _services()
    artifacts.update_queue_state(
        job_id,
        JobExecutionState.RUNNING,
        status=JobStatusValue.GENERATING,
        current_step="regenerate_section",
        progress=0.82,
        active_task="regenerate_section",
        retryable_action="regenerate_section",
        message=f"Regenerating section {section_id}.",
    )
    try:
        with stage_limit("generate"):
            jobs.regenerate_section(job_id, section_id, feedback, GenerationProfile(**profile_data))
        artifacts.update_queue_state(job_id, JobExecutionState.IDLE)
    except Exception as exc:
        artifacts.update_queue_state(
            job_id,
            JobExecutionState.IDLE,
            message="Section regeneration task failed.",
            status=JobStatusValue.FAILED,
            current_step="failed",
            error=str(exc),
        )
        raise


def _services() -> tuple:
    config = load_config()
    artifacts = ArtifactService(config.data_root)
    return config, artifacts, JobService(config, artifacts)
