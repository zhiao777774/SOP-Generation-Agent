import json

from backend.app.core.config import PROJECT_ROOT, load_config
from backend.app.core.model_catalog import ModelCatalog, catalog_model_to_dict


def test_model_catalog_lists_agentplayground_style_models(tmp_path):
    models_path = tmp_path / "models.json"
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "custom": {
                        "baseUrl": "http://model.local/v1",
                        "apiKey": "token",
                        "models": [
                            {
                                "id": "qwen3:8b",
                                "name": "Qwen3 8B",
                                "contextWindow": 32768,
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    catalog = ModelCatalog(models_path)
    models = catalog.list_models()

    assert catalog_model_to_dict(models[0]) == {
        "id": "qwen3:8b",
        "name": "Qwen3 8B",
        "provider": "custom",
        "contextWindow": 32768,
    }
    assert catalog.resolve_llm("qwen3:8b").api_base == "http://model.local/v1"
    assert catalog.resolve_llm("qwen3:8b").api_key == "token"
    assert catalog.resolve_llm("qwen3:8b").model == "qwen3:8b"


def test_default_model_catalog_path_is_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SOP_MODELS_PATH", raising=False)
    monkeypatch.delenv("SOP_FRONTEND_DIST", raising=False)

    config = load_config()

    assert config.models_path == PROJECT_ROOT / "backend/models.json"
    assert config.frontend_dist == PROJECT_ROOT / "frontend/dist"


def test_retrieval_tuning_loads_from_env(monkeypatch):
    monkeypatch.setenv("SOP_CHUNK_SIZE", "700")
    monkeypatch.setenv("SOP_CHUNK_OVERLAP", "80")
    monkeypatch.setenv("SOP_CHUNK_METHOD", "contextual")
    monkeypatch.setenv("SOP_SECTION_DETECTION_MODE", "rules_llm")
    monkeypatch.setenv("SOP_RETRIEVAL_MODE", "sparse_only")
    monkeypatch.setenv("SOP_RRF_K", "42")
    monkeypatch.setenv("SOP_SOURCE_TOP_K", "3")
    monkeypatch.setenv("SOP_REFERENCE_TOP_K", "4")
    monkeypatch.setenv("SOP_REFERENCE_PREFILTER_LIMIT", "25")
    monkeypatch.setenv("SOP_SOURCE_SCORE_THRESHOLD", "0.2")
    monkeypatch.setenv("SOP_REFERENCE_SCORE_THRESHOLD", "0.15")
    monkeypatch.setenv("SOP_CJK_TOKENIZER", "jieba")
    monkeypatch.setenv("SOP_SCRIPT_NORMALIZATION", "dual")
    monkeypatch.setenv("SOP_CKIPTAGGER_DATA_DIR", "models/ckiptagger")
    monkeypatch.setenv("SOP_DOMAIN_DICT_PATH", "config/domain_terms.txt")
    monkeypatch.setenv("SOP_CKIPTAGGER_DICT_MODE", "coerce")
    monkeypatch.setenv("SOP_JIEBA_DICT_PATH", "config/jieba_terms.txt")
    monkeypatch.setenv("SOP_DOMAIN_TOKEN_EXTRACTION", "false")
    monkeypatch.setenv("SOP_DOMAIN_TERM_LLM_ENABLED", "true")
    monkeypatch.setenv("SOP_DOMAIN_TERM_CONFIDENCE_THRESHOLD", "0.9")

    config = load_config()

    assert config.chunk_size == 700
    assert config.chunk_overlap == 80
    assert config.chunk_method == "contextual"
    assert config.section_detection_mode == "rules_llm"
    assert config.retrieval_mode == "sparse_only"
    assert config.rrf_k == 42
    assert config.source_top_k == 3
    assert config.reference_top_k == 4
    assert config.reference_prefilter_limit == 25
    assert config.source_score_threshold == 0.2
    assert config.reference_score_threshold == 0.15
    assert config.cjk_tokenizer == "jieba"
    assert config.script_normalization == "dual"
    assert config.ckiptagger_data_dir == PROJECT_ROOT / "models/ckiptagger"
    assert config.domain_dict_path == PROJECT_ROOT / "config/domain_terms.txt"
    assert config.ckiptagger_dict_mode == "coerce"
    assert config.jieba_dict_path == PROJECT_ROOT / "config/jieba_terms.txt"
    assert config.domain_token_extraction is False
    assert config.domain_term_llm_enabled is True
    assert config.domain_term_confidence_threshold == 0.9


def test_client_cookie_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("SOP_JOB_RETENTION_DAYS", "14")
    monkeypatch.delenv("SOP_CLIENT_COOKIE_MAX_AGE_DAYS", raising=False)
    config = load_config()

    assert config.client_cookie_name == "sop_client_id"
    assert config.client_cookie_max_age_days == 14
    assert config.client_cookie_secure is False
    assert config.client_cookie_samesite == "lax"
    assert config.cors_origins == ("*",)

    monkeypatch.setenv("SOP_CLIENT_COOKIE_NAME", "custom_owner")
    monkeypatch.setenv("SOP_CLIENT_COOKIE_MAX_AGE_DAYS", "7")
    monkeypatch.setenv("SOP_CLIENT_COOKIE_SECURE", "true")
    monkeypatch.setenv("SOP_CLIENT_COOKIE_SAMESITE", "none")
    monkeypatch.setenv("SOP_CORS_ORIGINS", "http://localhost:5173,https://sop.example")
    config = load_config()

    assert config.client_cookie_name == "custom_owner"
    assert config.client_cookie_max_age_days == 7
    assert config.client_cookie_secure is True
    assert config.client_cookie_samesite == "none"
    assert config.cors_origins == ("http://localhost:5173", "https://sop.example")
