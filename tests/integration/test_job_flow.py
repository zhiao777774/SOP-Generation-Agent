import os
from pathlib import Path
import time

from backend.app.core.config import AppConfig, ProviderConfig
from backend.app.pipeline.schemas import (
    GateName,
    GenerationProfile,
    JobStatus,
    JobStatusValue,
    ReviewDecisionRequest,
    ReviewSettings,
    UploadedFiles,
)
from backend.app.services.artifact_service import ArtifactService
from backend.app.services.job_service import JobService


def make_service(tmp_path: Path, chunk_method: str = "vanilla", retrieval_mode: str = "dense_sparse_rrf"):
    config = AppConfig(
        data_root=tmp_path / "jobs",
        frontend_dist=tmp_path / "dist",
        models_path=tmp_path / "models.json",
        job_retention_days=30,
        job_cleanup_interval_seconds=3600,
        chunk_size=900,
        chunk_overlap=120,
        chunk_method=chunk_method,
        section_detection_mode="rules",
        retrieval_mode=retrieval_mode,
        rrf_k=60,
        source_top_k=6,
        reference_top_k=5,
        reference_prefilter_limit=80,
        source_score_threshold=0.08,
        reference_score_threshold=0.05,
        cjk_tokenizer="regex",
        script_normalization="none",
        ckiptagger_data_dir=None,
        domain_dict_path=None,
        ckiptagger_dict_mode="recommend",
        jieba_dict_path=None,
        domain_token_extraction=True,
        domain_term_llm_enabled=False,
        domain_term_confidence_threshold=0.75,
        llm=ProviderConfig(None, None, None),
        embedding=ProviderConfig(None, None, None),
        ocr=ProviderConfig(None, None, None),
    )
    artifacts = ArtifactService(config.data_root)
    return artifacts, JobService(config, artifacts)


def seed_uploads(artifacts: ArtifactService, job_id: str):
    job_dir = artifacts.job_dir(job_id)
    source = job_dir / "uploads" / "source" / "manual.txt"
    reference = job_dir / "uploads" / "reference" / "records.txt"
    template = job_dir / "uploads" / "template" / "template.txt"
    source.write_text("1. Maintenance\nPump maintenance requires lockout. Release pressure before repair.", encoding="utf-8")
    reference.write_text("Field record: pump seal wear was solved by replacing the gasket.", encoding="utf-8")
    template.write_text("1. Maintenance\nExisting example text.", encoding="utf-8")
    artifacts.write_json(
        job_id,
        "uploaded_files",
        UploadedFiles(source_files=[str(source)], reference_files=[str(reference)], template_file=str(template)),
    )


def test_full_job_auto_approves_disabled_gates_and_outputs_reports(tmp_path):
    artifacts, service = make_service(tmp_path)
    job_id = artifacts.create_job(
        ReviewSettings(
            template_review_enabled=False,
            evidence_review_enabled=False,
            draft_review_enabled=False,
        )
    )
    seed_uploads(artifacts, job_id)

    service.analyze(job_id)
    generation = service.generate(job_id, GenerationProfile())
    status = artifacts.read_json(job_id, "status", JobStatus)

    assert status.status == JobStatusValue.NEEDS_REVIEW
    assert status.current_step == "draft_ready"
    assert status.progress == 0.95
    assert status.pending_gates == []
    assert generation.sections[0].blocks[0].source_chunk_ids
    assert artifacts.artifact_path(job_id, "coverage_report").exists()
    assert artifacts.artifact_path(job_id, "provenance_report").exists()
    assert artifacts.artifact_path(job_id, "final_sop.docx").exists()
    assert artifacts.maybe_read_json(job_id, "review_template_review")["auto_approved"] is True


def test_enabled_gates_block_generation_until_approved(tmp_path):
    artifacts, service = make_service(tmp_path)
    job_id = artifacts.create_job(ReviewSettings())
    seed_uploads(artifacts, job_id)

    service.analyze(job_id)
    status = artifacts.read_json(job_id, "status", JobStatus)
    assert status.status == JobStatusValue.NEEDS_REVIEW
    assert GateName.TEMPLATE in status.pending_gates

    try:
        service.generate(job_id, GenerationProfile())
    except PermissionError:
        pass
    else:
        raise AssertionError("generation should wait for template/evidence approvals")

    service.approve_gate(job_id, GateName.TEMPLATE, ReviewDecisionRequest())
    service.approve_gate(job_id, GateName.EVIDENCE, ReviewDecisionRequest(global_feedback="Use field language."))
    service.generate(job_id, GenerationProfile())
    status = artifacts.read_json(job_id, "status", JobStatus)
    assert status.status == JobStatusValue.NEEDS_REVIEW
    assert status.current_step == "draft_ready"
    assert status.progress == 0.95
    assert status.pending_gates == [GateName.DRAFT]


def test_reanalysis_invalidates_previous_reviews_and_draft_outputs(tmp_path):
    artifacts, service = make_service(tmp_path)
    job_id = artifacts.create_job(ReviewSettings())
    seed_uploads(artifacts, job_id)

    service.analyze(job_id)
    service.approve_gate(job_id, GateName.TEMPLATE, ReviewDecisionRequest())
    service.approve_gate(job_id, GateName.EVIDENCE, ReviewDecisionRequest(global_feedback="Use field language."))
    service.generate(job_id, GenerationProfile())

    assert artifacts.artifact_path(job_id, "generated_sections").exists()
    assert artifacts.artifact_path(job_id, "coverage_report").exists()
    assert artifacts.artifact_path(job_id, "final_sop.docx").exists()

    service.analyze(job_id)
    status = artifacts.read_json(job_id, "status", JobStatus)

    assert status.status == JobStatusValue.NEEDS_REVIEW
    assert status.current_step == "analysis_ready"
    assert status.progress == 0.65
    assert status.pending_gates == [GateName.TEMPLATE, GateName.EVIDENCE]
    assert artifacts.maybe_read_json(job_id, "review_template_review") is None
    assert artifacts.maybe_read_json(job_id, "review_evidence_review") is None
    assert artifacts.maybe_read_json(job_id, "review_draft_review") is None
    assert not artifacts.artifact_path(job_id, "generated_sections").exists()
    assert not artifacts.artifact_path(job_id, "coverage_report").exists()
    assert not artifacts.artifact_path(job_id, "final_sop.docx").exists()


def test_contextual_analyze_records_chunk_and_retrieval_metadata(tmp_path):
    artifacts, service = make_service(tmp_path, chunk_method="contextual", retrieval_mode="sparse_only")
    job_id = artifacts.create_job(ReviewSettings())
    seed_uploads(artifacts, job_id)

    plan = service.analyze(job_id)

    assert plan.retrieval_metadata.chunk_method == "contextual"
    assert plan.retrieval_metadata.retrieval_mode == "sparse_only"
    source_docs = artifacts.read_json(job_id, "source_docs")["documents"]
    assert source_docs[0]["chunks"][0]["embedding_text"].startswith("Document Context:")


def test_artifact_service_deletes_job_directory(tmp_path):
    artifacts, _service = make_service(tmp_path)
    job_id = artifacts.create_job(ReviewSettings())

    assert artifacts.job_dir(job_id).exists()
    artifacts.delete_job(job_id)

    assert not artifacts.job_dir(job_id).exists()


def test_artifact_service_cleans_expired_jobs_by_status_mtime(tmp_path):
    artifacts, _service = make_service(tmp_path)
    old_job_id = artifacts.create_job(ReviewSettings())
    fresh_job_id = artifacts.create_job(ReviewSettings())

    old_timestamp = time.time() - 3 * 24 * 60 * 60
    old_status = artifacts.artifact_path(old_job_id, "status")
    os.utime(old_status, (old_timestamp, old_timestamp))

    removed = artifacts.cleanup_expired_jobs(retention_days=1)

    assert old_job_id in removed
    assert not artifacts.job_dir(old_job_id).exists()
    assert artifacts.job_dir(fresh_job_id).exists()


def test_job_list_normalizes_legacy_completed_draft_ready_for_ui(tmp_path):
    artifacts, _service = make_service(tmp_path)
    job_id = artifacts.create_job(ReviewSettings())
    artifacts.update_status(
        job_id,
        JobStatusValue.COMPLETED,
        "draft_ready",
        1.0,
        "Legacy completed status.",
    )

    [summary] = artifacts.list_jobs()

    assert summary["status"] == "needs_review"
    assert summary["current_step"] == "draft_ready"
    assert summary["progress"] == 0.95
    assert summary["message"] == "Draft generated. Review is pending."
