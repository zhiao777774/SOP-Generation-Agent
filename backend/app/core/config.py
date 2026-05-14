import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class ProviderConfig:
    api_base: Optional[str]
    api_key: Optional[str]
    model: Optional[str]
    timeout_seconds: float = 15.0


@dataclass(frozen=True)
class AppConfig:
    data_root: Path
    frontend_dist: Path
    models_path: Path
    job_retention_days: float
    job_cleanup_interval_seconds: float
    chunk_size: int
    chunk_overlap: int
    chunk_method: str
    section_detection_mode: str
    retrieval_mode: str
    rrf_k: int
    source_top_k: int
    reference_top_k: int
    reference_prefilter_limit: int
    source_score_threshold: float
    reference_score_threshold: float
    cjk_tokenizer: str
    script_normalization: str
    ckiptagger_data_dir: Optional[Path]
    domain_dict_path: Optional[Path]
    ckiptagger_dict_mode: str
    jieba_dict_path: Optional[Path]
    domain_token_extraction: bool
    domain_term_llm_enabled: bool
    domain_term_confidence_threshold: float
    llm: ProviderConfig
    embedding: ProviderConfig
    ocr: ProviderConfig


def _default_project_path(relative_path: str) -> Path:
    return PROJECT_ROOT / relative_path


def _path_from_env(env_name: str, default: Path) -> Path:
    value = os.getenv(env_name)
    if not value:
        return default

    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _float_from_env(env_name: str, default: float) -> float:
    value = os.getenv(env_name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be a number, got {value!r}") from exc


def _int_from_env(env_name: str, default: int) -> int:
    value = os.getenv(env_name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer, got {value!r}") from exc


def _bool_from_env(env_name: str, default: bool) -> bool:
    value = os.getenv(env_name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{env_name} must be a boolean, got {value!r}")


def _optional_path_from_env(env_name: str) -> Optional[Path]:
    value = os.getenv(env_name)
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_config() -> AppConfig:
    data_root = Path(os.getenv("SOP_DATA_ROOT", "/data/jobs"))
    frontend_dist = _path_from_env("SOP_FRONTEND_DIST", _default_project_path("frontend/dist"))
    models_path = _path_from_env("SOP_MODELS_PATH", _default_project_path("backend/models.json"))
    return AppConfig(
        data_root=data_root,
        frontend_dist=frontend_dist,
        models_path=models_path,
        job_retention_days=max(_float_from_env("SOP_JOB_RETENTION_DAYS", 30), 0),
        job_cleanup_interval_seconds=max(_float_from_env("SOP_JOB_CLEANUP_INTERVAL_SECONDS", 3600), 0),
        chunk_size=max(_int_from_env("SOP_CHUNK_SIZE", 900), 1),
        chunk_overlap=max(_int_from_env("SOP_CHUNK_OVERLAP", 120), 0),
        chunk_method=os.getenv("SOP_CHUNK_METHOD", "vanilla").strip().lower(),
        section_detection_mode=os.getenv("SOP_SECTION_DETECTION_MODE", "rules").strip().lower(),
        retrieval_mode=os.getenv("SOP_RETRIEVAL_MODE", "dense_sparse_rrf").strip().lower(),
        rrf_k=max(_int_from_env("SOP_RRF_K", 60), 1),
        source_top_k=max(_int_from_env("SOP_SOURCE_TOP_K", 6), 0),
        reference_top_k=max(_int_from_env("SOP_REFERENCE_TOP_K", 5), 0),
        reference_prefilter_limit=max(_int_from_env("SOP_REFERENCE_PREFILTER_LIMIT", 80), 0),
        source_score_threshold=_float_from_env("SOP_SOURCE_SCORE_THRESHOLD", 0.08),
        reference_score_threshold=_float_from_env("SOP_REFERENCE_SCORE_THRESHOLD", 0.05),
        cjk_tokenizer=os.getenv("SOP_CJK_TOKENIZER", "auto").strip().lower(),
        script_normalization=os.getenv("SOP_SCRIPT_NORMALIZATION", "dual").strip().lower(),
        ckiptagger_data_dir=_optional_path_from_env("SOP_CKIPTAGGER_DATA_DIR"),
        domain_dict_path=(
            _optional_path_from_env("SOP_DOMAIN_DICT_PATH")
            or _optional_path_from_env("SOP_CKIPTAGGER_DICT_PATH")
            or _default_project_path("config/domain_terms.txt")
        ),
        ckiptagger_dict_mode=os.getenv("SOP_CKIPTAGGER_DICT_MODE", "recommend").strip().lower(),
        jieba_dict_path=(
            _optional_path_from_env("SOP_JIEBA_DICT_PATH")
            or _optional_path_from_env("SOP_DOMAIN_DICT_PATH")
            or _default_project_path("config/jieba_terms.txt")
        ),
        domain_token_extraction=_bool_from_env("SOP_DOMAIN_TOKEN_EXTRACTION", True),
        domain_term_llm_enabled=_bool_from_env("SOP_DOMAIN_TERM_LLM_ENABLED", False),
        domain_term_confidence_threshold=_float_from_env("SOP_DOMAIN_TERM_CONFIDENCE_THRESHOLD", 0.75),
        llm=ProviderConfig(
            api_base=os.getenv("SOP_LLM_API_BASE"),
            api_key=os.getenv("SOP_LLM_API_KEY"),
            model=os.getenv("SOP_LLM_MODEL"),
            timeout_seconds=float(os.getenv("SOP_LLM_TIMEOUT_SECONDS", "120")),
        ),
        embedding=ProviderConfig(
            api_base=os.getenv("SOP_EMBEDDING_API_URL"),
            api_key=os.getenv("SOP_EMBEDDING_API_KEY"),
            model=os.getenv("SOP_EMBEDDING_MODEL"),
            timeout_seconds=float(os.getenv("SOP_EMBEDDING_TIMEOUT_SECONDS", "8")),
        ),
        ocr=ProviderConfig(
            api_base=os.getenv("SOP_OCR_API_BASE"),
            api_key=os.getenv("SOP_OCR_API_KEY"),
            model=os.getenv("SOP_OCR_MODEL"),
            timeout_seconds=float(os.getenv("SOP_OCR_TIMEOUT_SECONDS", "120")),
        ),
    )
