FROM node:21-bookworm-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY frontend ./
RUN npm run build

FROM python:3.11-slim AS runtime
WORKDIR /app
ENV SOP_DATA_ROOT=/data/jobs
ENV SOP_FRONTEND_DIST=/app/frontend/dist
ENV SOP_MODELS_PATH=/app/backend/models.json
ENV SOP_JOB_RETENTION_DAYS=30
ENV SOP_JOB_CLEANUP_INTERVAL_SECONDS=3600
ENV SOP_CLIENT_COOKIE_NAME=sop_client_id
ENV SOP_CLIENT_COOKIE_SECURE=false
ENV SOP_CLIENT_COOKIE_SAMESITE=lax
ENV SOP_CORS_ORIGINS=*
ENV SOP_CHUNK_SIZE=900
ENV SOP_CHUNK_OVERLAP=120
ENV SOP_CHUNK_METHOD=vanilla
ENV SOP_SECTION_DETECTION_MODE=rules
ENV SOP_RETRIEVAL_MODE=dense_sparse_rrf
ENV SOP_RRF_K=60
ENV SOP_SOURCE_TOP_K=6
ENV SOP_REFERENCE_TOP_K=5
ENV SOP_REFERENCE_PREFILTER_LIMIT=80
ENV SOP_SOURCE_SCORE_THRESHOLD=0.08
ENV SOP_REFERENCE_SCORE_THRESHOLD=0.05
ENV SOP_CJK_TOKENIZER=auto
ENV SOP_SCRIPT_NORMALIZATION=dual
ENV SOP_CKIPTAGGER_DATA_DIR=
ENV SOP_DOMAIN_DICT_PATH=/config/domain_terms.txt
ENV SOP_CKIPTAGGER_DICT_MODE=recommend
ENV SOP_JIEBA_DICT_PATH=/config/jieba_terms.txt
ENV SOP_DOMAIN_TOKEN_EXTRACTION=true
ENV SOP_DOMAIN_TERM_LLM_ENABLED=false
ENV SOP_DOMAIN_TERM_CONFIDENCE_THRESHOLD=0.75
ENV SOP_LLM_API_KEY=ollama
ENV SOP_EMBEDDING_API_URL=http://host.docker.internal:11434/api/embeddings
ENV SOP_EMBEDDING_MODEL=qwen3-embedding:0.6b
ENV SOP_EMBEDDING_TIMEOUT_SECONDS=8
ENV SOP_OCR_API_BASE=
ENV SOP_OCR_MODEL=
ENV SOP_OCR_TIMEOUT_SECONDS=120
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY backend ./backend
COPY config /config
RUN pip install --no-cache-dir .
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
EXPOSE 7860
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "7860"]
