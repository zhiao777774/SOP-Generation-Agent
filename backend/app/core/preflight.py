import json
import os
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set

from backend.app.core.config import AppConfig


class PreflightError(RuntimeError):
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("Startup preflight failed:\n" + "\n".join(f"- {error}" for error in errors))


@dataclass(frozen=True)
class PreflightReport:
    warnings: List[str] = field(default_factory=list)


def run_preflight(config: AppConfig) -> PreflightReport:
    errors: List[str] = []
    warnings: List[str] = []

    _check_data_root(config.data_root, errors)
    _check_frontend_dist(config.frontend_dist, errors)
    _check_model_catalog(config.models_path, errors)
    _check_embedding_provider(config, errors)
    _check_ocr_provider(config, errors, warnings)
    _check_pipeline_settings(config, errors)
    _check_tokenizer_settings(config, errors)
    _check_retention(config, warnings)

    if errors:
        raise PreflightError(errors)
    return PreflightReport(warnings=warnings)


def _check_data_root(data_root: Path, errors: List[str]) -> None:
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        probe = data_root / ".preflight-write-check"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        errors.append(f"SOP_DATA_ROOT is not writable: {data_root} ({exc})")


def _check_frontend_dist(frontend_dist: Path, errors: List[str]) -> None:
    if not frontend_dist.exists():
        errors.append(f"SOP_FRONTEND_DIST does not exist: {frontend_dist}")
        return
    if not (frontend_dist / "index.html").exists():
        errors.append(f"SOP_FRONTEND_DIST is missing index.html: {frontend_dist}")


def _check_model_catalog(models_path: Path, errors: List[str]) -> None:
    if not models_path.exists():
        errors.append(f"SOP_MODELS_PATH does not exist: {models_path}")
        return

    try:
        raw = json.loads(models_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"SOP_MODELS_PATH is not valid JSON: {models_path} ({exc})")
        return

    providers = raw.get("providers")
    if not isinstance(providers, dict) or not providers:
        errors.append(f"SOP_MODELS_PATH must define at least one provider: {models_path}")
        return

    seen_model_ids: Set[str] = set()
    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            errors.append(f"Provider {provider_name} must be an object in {models_path}")
            continue
        if not provider.get("baseUrl"):
            errors.append(f"Provider {provider_name} is missing baseUrl in {models_path}")

        api_key_env = provider.get("apiKeyEnv")
        api_key = provider.get("apiKey")
        if api_key_env and not os.getenv(api_key_env, api_key):
            errors.append(f"Provider {provider_name} references unset apiKeyEnv {api_key_env}")
        if not api_key_env and not api_key:
            errors.append(f"Provider {provider_name} must define apiKeyEnv or apiKey")

        models = provider.get("models")
        if not isinstance(models, list) or not models:
            errors.append(f"Provider {provider_name} must define at least one model")
            continue
        for index, model in enumerate(models):
            model_id = model.get("id") if isinstance(model, dict) else None
            if not model_id:
                errors.append(f"Provider {provider_name} model #{index + 1} is missing id")
                continue
            if model_id in seen_model_ids:
                errors.append(f"Duplicate model id in catalog: {model_id}")
            seen_model_ids.add(model_id)


def _check_embedding_provider(config: AppConfig, errors: List[str]) -> None:
    if config.retrieval_mode == "sparse_only":
        return
    if not config.embedding.api_base:
        errors.append("SOP_EMBEDDING_API_URL is required")
    if not config.embedding.model:
        errors.append("SOP_EMBEDDING_MODEL is required")


def _check_ocr_provider(config: AppConfig, errors: List[str], warnings: List[str]) -> None:
    has_base = bool(config.ocr.api_base)
    has_model = bool(config.ocr.model)
    if has_base != has_model:
        errors.append("SOP_OCR_API_BASE and SOP_OCR_MODEL must be configured together")
    elif not has_base and not has_model:
        warnings.append("OCR provider is not configured; PDF parsing will use text/PyMuPDF fallback paths.")


def _check_pipeline_settings(config: AppConfig, errors: List[str]) -> None:
    if config.chunk_overlap >= config.chunk_size:
        errors.append("SOP_CHUNK_OVERLAP must be smaller than SOP_CHUNK_SIZE")
    if config.chunk_method not in {"vanilla", "contextual", "anthropic"}:
        errors.append("SOP_CHUNK_METHOD must be one of: vanilla, contextual, anthropic")
    if config.section_detection_mode not in {"rules", "rules_llm"}:
        errors.append("SOP_SECTION_DETECTION_MODE must be one of: rules, rules_llm")
    if config.retrieval_mode not in {"dense_sparse_rrf", "sparse_only"}:
        errors.append("SOP_RETRIEVAL_MODE must be one of: dense_sparse_rrf, sparse_only")


def _check_tokenizer_settings(config: AppConfig, errors: List[str]) -> None:
    if importlib.util.find_spec("bm25s") is None:
        errors.append("bm25s package is required for sparse retrieval")
    if config.cjk_tokenizer not in {"auto", "ckiptagger", "jieba", "regex"}:
        errors.append("SOP_CJK_TOKENIZER must be one of: auto, ckiptagger, jieba, regex")
    if config.script_normalization not in {"none", "s2t", "t2s", "dual"}:
        errors.append("SOP_SCRIPT_NORMALIZATION must be one of: none, s2t, t2s, dual")
    if config.script_normalization != "none" and importlib.util.find_spec("opencc") is None:
        errors.append("opencc package is required when SOP_SCRIPT_NORMALIZATION is enabled")
    if config.cjk_tokenizer in {"auto", "jieba"} and importlib.util.find_spec("jieba") is None:
        errors.append("jieba package is required when SOP_CJK_TOKENIZER is auto or jieba")
    if config.ckiptagger_dict_mode not in {"recommend", "coerce"}:
        errors.append("SOP_CKIPTAGGER_DICT_MODE must be one of: recommend, coerce")
    if config.domain_dict_path and not config.domain_dict_path.is_file():
        errors.append(f"SOP_DOMAIN_DICT_PATH does not exist: {config.domain_dict_path}")
    if config.jieba_dict_path and not config.jieba_dict_path.is_file():
        errors.append(f"SOP_JIEBA_DICT_PATH does not exist: {config.jieba_dict_path}")
    if not config.ckiptagger_data_dir and config.cjk_tokenizer != "ckiptagger":
        return
    if not config.ckiptagger_data_dir:
        errors.append("SOP_CKIPTAGGER_DATA_DIR is required when SOP_CJK_TOKENIZER=ckiptagger")
        return
    if importlib.util.find_spec("ckiptagger") is None:
        errors.append("ckiptagger package is required when SOP_CKIPTAGGER_DATA_DIR is set")
    if not config.ckiptagger_data_dir.exists():
        errors.append(f"SOP_CKIPTAGGER_DATA_DIR does not exist: {config.ckiptagger_data_dir}")
        return
    if not (config.ckiptagger_data_dir / "model_ws").exists():
        errors.append(f"SOP_CKIPTAGGER_DATA_DIR is missing model_ws: {config.ckiptagger_data_dir}")


def _check_retention(config: AppConfig, warnings: List[str]) -> None:
    if config.job_retention_days == 0:
        warnings.append("SOP_JOB_RETENTION_DAYS=0; expired job cleanup is disabled.")
    if config.job_retention_days > 0 and config.job_cleanup_interval_seconds == 0:
        warnings.append("SOP_JOB_CLEANUP_INTERVAL_SECONDS=0; expired jobs clean on startup and job listing only.")
