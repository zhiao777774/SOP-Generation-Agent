# SOP Generation Agent

Reviewable SOP draft generator with staged human-in-the-loop gates, evidence planning, paragraph-level provenance, and clean DOCX export.

## Run

The default path is Docker Compose:

```bash
docker compose up
```

Open `http://localhost:7860`.

Compose builds the single FastAPI image, builds the React frontend inside that image, mounts `./data/jobs` for job artifacts, and mounts `./backend/models.json` as the generation model catalog.

For local development without Docker, after Python dependencies are installed, one command builds the frontend and starts the combined FastAPI service:

```bash
make dev
```

The script loads `.env` when present and defaults local artifacts to `./data/jobs`. If `.env` still contains the container default `SOP_DATA_ROOT=/data/jobs`, `make dev` maps it to the local `./data/jobs` path.

Initial setup:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
npm --prefix frontend install --legacy-peer-deps
make dev
```

## Provider Configuration

Runtime provider URLs and secrets are configured at service startup through env or mounted config files. The UI only exposes a generation model selector.

- `SOP_MODELS_PATH`: path to an AgentPlayground-style `models.json` for generation LLMs.
- `SOP_LLM_API_KEY`: optional key referenced by `apiKeyEnv` in `models.json`.
- `SOP_EMBEDDING_API_URL`, `SOP_EMBEDDING_API_KEY`, `SOP_EMBEDDING_MODEL`
- `SOP_OCR_API_BASE`, `SOP_OCR_API_KEY`, `SOP_OCR_MODEL`
- `SOP_DATA_ROOT`
- `SOP_FRONTEND_DIST`
- `SOP_JOB_RETENTION_DAYS`: job artifact retention window. Default: `30`. Use `0` to disable TTL cleanup.
- `SOP_JOB_CLEANUP_INTERVAL_SECONDS`: periodic cleanup interval while the service is running. Default: `3600`. Use `0` to disable the background cleanup loop.
- `SOP_CLIENT_COOKIE_NAME`: anonymous browser owner cookie name. Default: `sop_client_id`.
- `SOP_CLIENT_COOKIE_MAX_AGE_DAYS`: owner cookie lifetime. Leave empty to follow `SOP_JOB_RETENTION_DAYS`.
- `SOP_CLIENT_COOKIE_SECURE`: set `true` when serving over HTTPS. Default: `false`.
- `SOP_CLIENT_COOKIE_SAMESITE`: `lax`, `strict`, or `none`. Default: `lax`.
- `SOP_CORS_ORIGINS`: comma-separated allowed origins when running frontend/backend separately. Default: `*`.
- `SOP_CHUNK_SIZE`: chunk size in characters for source/reference text. Default: `900`.
- `SOP_CHUNK_OVERLAP`: character overlap between chunks. Default: `120`.
- `SOP_CHUNK_METHOD`: `vanilla`, `contextual`, or `anthropic`. Default: `vanilla`.
- `SOP_SECTION_DETECTION_MODE`: `rules` or `rules_llm`. Default: `rules_llm`.
- `SOP_RETRIEVAL_MODE`: `dense_sparse_rrf` or `sparse_only`. Default: `dense_sparse_rrf`.
- `SOP_RRF_K`: reciprocal-rank-fusion constant. Default: `60`.
- `SOP_SOURCE_TOP_K`: max source evidence candidates per section before threshold filtering. Default: `6`.
- `SOP_REFERENCE_TOP_K`: max reference evidence candidates per section before threshold filtering. Default: `5`.
- `SOP_REFERENCE_PREFILTER_LIMIT`: lexical prefilter size before embedding/ranking reference records. Default: `80`.
- `SOP_SOURCE_SCORE_THRESHOLD`: minimum score for source candidates. Default: `0.08`.
- `SOP_REFERENCE_SCORE_THRESHOLD`: minimum score for reference candidates. Default: `0.05`.
- `SOP_CJK_TOKENIZER`: `auto`, `ckiptagger`, `jieba`, or `regex`. Default: `auto`.
- `SOP_SCRIPT_NORMALIZATION`: `dual`, `s2t`, `t2s`, or `none`. Default: `dual`.
- `SOP_CKIPTAGGER_DATA_DIR`: optional CKIPTagger model data directory. Required only when forcing `SOP_CJK_TOKENIZER=ckiptagger`.
- `SOP_DOMAIN_DICT_PATH`: permanent domain dictionary file. Docker defaults to `/config/domain_terms.txt`.
- `SOP_CKIPTAGGER_DICT_MODE`: `recommend` or `coerce`. Default: `recommend`.
- `SOP_JIEBA_DICT_PATH`: optional Jieba user dictionary file. Docker defaults to `/config/jieba_terms.txt`.
- `SOP_DOMAIN_TOKEN_EXTRACTION`: preserve model names, part numbers, versions, and error codes as extra BM25 tokens. Default: `true`.
- `SOP_DOMAIN_TERM_LLM_ENABLED`: use the configured LLM to suggest job-local temporary domain terms during analysis. Default: `false`.
- `SOP_DOMAIN_TERM_CONFIDENCE_THRESHOLD`: minimum LLM confidence for temporary terms. Default: `0.75`.

If the configured embedding endpoint fails during a job, retrieval falls back to BM25 sparse mode and records a visible degraded-mode warning in logs, reports, and the evidence plan.

Sparse retrieval uses `bm25s` with a script-aware tokenizer. `auto` uses regex/domain tokens for English-only text, Jieba for simplified or mixed CJK text, and CKIPTagger for traditional-heavy text when CKIPTagger data is configured. OpenCC shadow normalization lets simplified and traditional Chinese match each other without changing the original text shown in review. The domain dictionary uses one term per line, with an optional numeric weight as the last field. LLM-discovered domain terms are job-local by default and do not mutate `config/domain_terms.txt`.

CKIPTagger is optional because its model/runtime footprint is larger. Install the `ckip` extra or bake it into a custom image before setting `SOP_CJK_TOKENIZER=ckiptagger` or `SOP_CKIPTAGGER_DATA_DIR`.

Startup preflight validates important runtime configuration before the app finishes booting. The service fails fast if storage is not writable, the frontend build is missing, `models.json` is empty/invalid, generation providers lack `baseUrl` or API key settings, embedding URL/model settings are missing while dense retrieval is enabled, tokenizer dependencies or configured dictionary paths are invalid, or CKIPTagger is forced without model data. OCR is optional because PDF text/PyMuPDF fallback exists, but partial OCR config is rejected.

The default `backend/models.json` follows the AgentPlayground provider shape:

```json
{
  "providers": {
    "custom": {
      "baseUrl": "http://host.docker.internal:11434/v1",
      "api": "openai-completions",
      "apiKeyEnv": "SOP_LLM_API_KEY",
      "models": [{ "id": "qwen3:8b", "name": "Qwen3 8B (Ollama)" }]
    }
  }
}
```

With Docker Compose, update `.env` and `backend/models.json`, then restart:

```bash
docker compose up --build
```

## Job Flow

1. Create a job with review settings and selected generation model.
2. Upload source PDFs, optional reference files, and one DOCX template.
3. Analyze to produce a reviewable `template_section_resolution.json`.
4. Approve the template section proposal, or apply feedback to refine it first.
5. Evidence planning then produces `evidence_plan.json`; approve it or re-plan from feedback.
6. Generate structured section drafts with paragraph-level provenance.
7. Review, regenerate sections if needed, approve draft, and download `final_sop.docx` plus reports.

Artifacts are stored under `/data/jobs/{job_id}` by default. Expired jobs are cleaned once on service startup, during periodic background cleanup, and before the job list API returns results.
