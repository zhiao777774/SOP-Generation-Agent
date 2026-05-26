FROM node:21-bookworm-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps
COPY frontend ./
RUN npm run build

FROM python:3.11-slim AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY backend ./backend
COPY config /config
RUN pip install --no-cache-dir .
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
ENV SOP_DATA_ROOT=/data/jobs \
    SOP_FRONTEND_DIST=/app/frontend/dist \
    SOP_MODELS_PATH=/app/backend/models.example.json \
    SOP_JOB_RETENTION_DAYS=30 \
    SOP_JOB_CLEANUP_INTERVAL_SECONDS=3600 \
    SOP_QUEUE_REDIS_URL=redis://redis:6379/0 \
    SOP_WORKER_QUEUES=default \
    SOP_WORKER_COUNT=4 \
    SOP_MAX_CONCURRENT_ANALYZE=3 \
    SOP_MAX_CONCURRENT_GENERATE=2 \
    SOP_MAX_CONCURRENT_OCR=2 \
    SOP_MAX_CONCURRENT_EMBEDDING=4 \
    SOP_MAX_CONCURRENT_LLM=2 \
    SOP_MAX_CONCURRENT_VLM=1 \
    SOP_CLIENT_COOKIE_NAME=sop_client_id \
    SOP_CLIENT_COOKIE_SECURE=false \
    SOP_CLIENT_COOKIE_SAMESITE=lax \
    SOP_CORS_ORIGINS=* \
    SOP_CHUNK_SIZE=900 \
    SOP_CHUNK_OVERLAP=120 \
    SOP_CHUNK_METHOD=vanilla \
    SOP_SECTION_DETECTION_MODE=rules \
    SOP_RETRIEVAL_MODE=dense_sparse_rrf \
    SOP_RRF_K=60 \
    SOP_SOURCE_TOP_K=6 \
    SOP_REFERENCE_TOP_K=5 \
    SOP_REFERENCE_PREFILTER_LIMIT=80 \
    SOP_SOURCE_SCORE_THRESHOLD=0.08 \
    SOP_REFERENCE_SCORE_THRESHOLD=0.05 \
    SOP_CJK_TOKENIZER=auto \
    SOP_SCRIPT_NORMALIZATION=dual \
    SOP_CKIPTAGGER_DATA_DIR= \
    SOP_DOMAIN_DICT_PATH=/config/domain_terms.txt \
    SOP_CKIPTAGGER_DICT_MODE=recommend \
    SOP_JIEBA_DICT_PATH=/config/jieba_terms.txt \
    SOP_DOMAIN_TOKEN_EXTRACTION=true \
    SOP_DOMAIN_TERM_LLM_ENABLED=false \
    SOP_DOMAIN_TERM_CONFIDENCE_THRESHOLD=0.75 \
    SOP_LLM_API_KEY=ollama \
    SOP_EMBEDDING_API_URL=http://host.docker.internal:11434/api/embeddings \
    SOP_EMBEDDING_MODEL=qwen3-embedding:0.6b \
    SOP_EMBEDDING_TIMEOUT_SECONDS=8 \
    SOP_OCR_API_BASE= \
    SOP_OCR_MODEL= \
    SOP_OCR_TIMEOUT_SECONDS=120
EXPOSE 7860
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "7860"]
