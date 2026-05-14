import json
from pathlib import Path
from typing import Optional

import pytest

from backend.app.core.config import AppConfig, ProviderConfig
from backend.app.core.preflight import PreflightError, run_preflight


def make_config(tmp_path: Path, models: Optional[dict] = None, ocr: Optional[ProviderConfig] = None):
    frontend_dist = tmp_path / "dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text("<div id='root'></div>", encoding="utf-8")

    models_path = tmp_path / "models.json"
    models_path.write_text(
        json.dumps(
            models
            or {
                "providers": {
                    "custom": {
                        "baseUrl": "http://llm.local/v1",
                        "apiKey": "test-key",
                        "models": [{"id": "test-model", "name": "Test Model"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    return AppConfig(
        data_root=tmp_path / "jobs",
        frontend_dist=frontend_dist,
        models_path=models_path,
        job_retention_days=30,
        job_cleanup_interval_seconds=3600,
        chunk_size=900,
        chunk_overlap=120,
        chunk_method="vanilla",
        section_detection_mode="rules",
        retrieval_mode="dense_sparse_rrf",
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
        embedding=ProviderConfig("http://embedding.local/v1/embeddings", None, "embed-model"),
        ocr=ocr or ProviderConfig(None, None, None),
    )


def test_preflight_passes_with_required_runtime_config(tmp_path):
    report = run_preflight(make_config(tmp_path))

    assert report.warnings == [
        "OCR provider is not configured; PDF parsing will use text/PyMuPDF fallback paths."
    ]


def test_preflight_fails_when_frontend_build_is_missing(tmp_path):
    config = make_config(tmp_path)
    (config.frontend_dist / "index.html").unlink()

    with pytest.raises(PreflightError) as exc:
        run_preflight(config)

    assert "SOP_FRONTEND_DIST is missing index.html" in str(exc.value)


def test_preflight_fails_when_model_catalog_has_no_base_url(tmp_path):
    config = make_config(
        tmp_path,
        models={
            "providers": {
                "custom": {
                    "apiKey": "test-key",
                    "models": [{"id": "test-model"}],
                }
            }
        },
    )

    with pytest.raises(PreflightError) as exc:
        run_preflight(config)

    assert "Provider custom is missing baseUrl" in str(exc.value)


def test_preflight_fails_when_embedding_config_is_missing(tmp_path):
    config = make_config(tmp_path)
    config = AppConfig(
        data_root=config.data_root,
        frontend_dist=config.frontend_dist,
        models_path=config.models_path,
        job_retention_days=config.job_retention_days,
        job_cleanup_interval_seconds=config.job_cleanup_interval_seconds,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        chunk_method=config.chunk_method,
        section_detection_mode=config.section_detection_mode,
        retrieval_mode=config.retrieval_mode,
        rrf_k=config.rrf_k,
        source_top_k=config.source_top_k,
        reference_top_k=config.reference_top_k,
        reference_prefilter_limit=config.reference_prefilter_limit,
        source_score_threshold=config.source_score_threshold,
        reference_score_threshold=config.reference_score_threshold,
        cjk_tokenizer=config.cjk_tokenizer,
        script_normalization=config.script_normalization,
        ckiptagger_data_dir=config.ckiptagger_data_dir,
        domain_dict_path=config.domain_dict_path,
        ckiptagger_dict_mode=config.ckiptagger_dict_mode,
        jieba_dict_path=config.jieba_dict_path,
        domain_token_extraction=config.domain_token_extraction,
        domain_term_llm_enabled=config.domain_term_llm_enabled,
        domain_term_confidence_threshold=config.domain_term_confidence_threshold,
        llm=config.llm,
        embedding=ProviderConfig(None, None, None),
        ocr=config.ocr,
    )

    with pytest.raises(PreflightError) as exc:
        run_preflight(config)

    assert "SOP_EMBEDDING_API_URL is required" in str(exc.value)
    assert "SOP_EMBEDDING_MODEL is required" in str(exc.value)


def test_preflight_allows_missing_embedding_for_sparse_only(tmp_path):
    config = make_config(tmp_path)
    config = AppConfig(
        data_root=config.data_root,
        frontend_dist=config.frontend_dist,
        models_path=config.models_path,
        job_retention_days=config.job_retention_days,
        job_cleanup_interval_seconds=config.job_cleanup_interval_seconds,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        chunk_method=config.chunk_method,
        section_detection_mode=config.section_detection_mode,
        retrieval_mode="sparse_only",
        rrf_k=config.rrf_k,
        source_top_k=config.source_top_k,
        reference_top_k=config.reference_top_k,
        reference_prefilter_limit=config.reference_prefilter_limit,
        source_score_threshold=config.source_score_threshold,
        reference_score_threshold=config.reference_score_threshold,
        cjk_tokenizer=config.cjk_tokenizer,
        script_normalization=config.script_normalization,
        ckiptagger_data_dir=config.ckiptagger_data_dir,
        domain_dict_path=config.domain_dict_path,
        ckiptagger_dict_mode=config.ckiptagger_dict_mode,
        jieba_dict_path=config.jieba_dict_path,
        domain_token_extraction=config.domain_token_extraction,
        domain_term_llm_enabled=config.domain_term_llm_enabled,
        domain_term_confidence_threshold=config.domain_term_confidence_threshold,
        llm=config.llm,
        embedding=ProviderConfig(None, None, None),
        ocr=config.ocr,
    )

    run_preflight(config)


def test_preflight_rejects_invalid_pipeline_modes(tmp_path):
    config = make_config(tmp_path)
    config = AppConfig(
        data_root=config.data_root,
        frontend_dist=config.frontend_dist,
        models_path=config.models_path,
        job_retention_days=config.job_retention_days,
        job_cleanup_interval_seconds=config.job_cleanup_interval_seconds,
        chunk_size=100,
        chunk_overlap=100,
        chunk_method="unknown",
        section_detection_mode="ai",
        retrieval_mode="hybrid",
        rrf_k=config.rrf_k,
        source_top_k=config.source_top_k,
        reference_top_k=config.reference_top_k,
        reference_prefilter_limit=config.reference_prefilter_limit,
        source_score_threshold=config.source_score_threshold,
        reference_score_threshold=config.reference_score_threshold,
        cjk_tokenizer="unknown",
        script_normalization="bad",
        ckiptagger_data_dir=config.ckiptagger_data_dir,
        domain_dict_path=config.domain_dict_path,
        ckiptagger_dict_mode=config.ckiptagger_dict_mode,
        jieba_dict_path=config.jieba_dict_path,
        domain_token_extraction=config.domain_token_extraction,
        domain_term_llm_enabled=config.domain_term_llm_enabled,
        domain_term_confidence_threshold=config.domain_term_confidence_threshold,
        llm=config.llm,
        embedding=config.embedding,
        ocr=config.ocr,
    )

    with pytest.raises(PreflightError) as exc:
        run_preflight(config)

    assert "SOP_CHUNK_OVERLAP must be smaller than SOP_CHUNK_SIZE" in str(exc.value)
    assert "SOP_CHUNK_METHOD must be one of" in str(exc.value)
    assert "SOP_SECTION_DETECTION_MODE must be one of" in str(exc.value)
    assert "SOP_RETRIEVAL_MODE must be one of" in str(exc.value)
    assert "SOP_CJK_TOKENIZER must be one of" in str(exc.value)
    assert "SOP_SCRIPT_NORMALIZATION must be one of" in str(exc.value)


def test_preflight_fails_when_ocr_config_is_partial(tmp_path):
    config = make_config(tmp_path, ocr=ProviderConfig("http://ocr.local/v1", None, None))

    with pytest.raises(PreflightError) as exc:
        run_preflight(config)

    assert "SOP_OCR_API_BASE and SOP_OCR_MODEL must be configured together" in str(exc.value)


def test_preflight_rejects_invalid_ckiptagger_settings(tmp_path):
    config = make_config(tmp_path)
    config = AppConfig(
        data_root=config.data_root,
        frontend_dist=config.frontend_dist,
        models_path=config.models_path,
        job_retention_days=config.job_retention_days,
        job_cleanup_interval_seconds=config.job_cleanup_interval_seconds,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        chunk_method=config.chunk_method,
        section_detection_mode=config.section_detection_mode,
        retrieval_mode=config.retrieval_mode,
        rrf_k=config.rrf_k,
        source_top_k=config.source_top_k,
        reference_top_k=config.reference_top_k,
        reference_prefilter_limit=config.reference_prefilter_limit,
        source_score_threshold=config.source_score_threshold,
        reference_score_threshold=config.reference_score_threshold,
        cjk_tokenizer="ckiptagger",
        script_normalization=config.script_normalization,
        ckiptagger_data_dir=tmp_path / "missing-ckip",
        domain_dict_path=tmp_path / "missing-terms.txt",
        ckiptagger_dict_mode="force",
        jieba_dict_path=tmp_path / "missing-jieba.txt",
        domain_token_extraction=True,
        domain_term_llm_enabled=config.domain_term_llm_enabled,
        domain_term_confidence_threshold=config.domain_term_confidence_threshold,
        llm=config.llm,
        embedding=config.embedding,
        ocr=config.ocr,
    )

    with pytest.raises(PreflightError) as exc:
        run_preflight(config)

    assert "SOP_CKIPTAGGER_DICT_MODE must be one of" in str(exc.value)
    assert "SOP_DOMAIN_DICT_PATH does not exist" in str(exc.value)
    assert "SOP_JIEBA_DICT_PATH does not exist" in str(exc.value)
    assert "SOP_CKIPTAGGER_DATA_DIR does not exist" in str(exc.value)
