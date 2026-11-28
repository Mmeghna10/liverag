# LiveRAG in ONE container, for free hosting (Hugging Face Spaces, or any Docker host).
# Runs PostgreSQL 16 + pgvector, Redpanda, the CDC reader, the streaming indexer and the API,
# which also serves the built web app on port 7860. See deploy/README.md.
#
#   docker build -t liverag .
#   docker run -p 7860:7860 -e LLM_PROVIDER=openai -e LLM_API_KEY=... liverag

# ---- 1. Build the web app (static files) ----
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
# Public demo: the demo buttons also fill in the shared demo password.
ARG VITE_DEMO_PASSWORD=northwind-demo
ENV VITE_DEMO_PASSWORD=$VITE_DEMO_PASSWORD
RUN npm run build

# ---- 2. Redpanda binaries (self-contained under /opt/redpanda) ----
FROM redpandadata/redpanda:v24.2.18 AS redpanda

# ---- 3. Runtime: PostgreSQL 16 with pgvector, plus Python services ----
FROM pgvector/pgvector:pg16

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
COPY --from=redpanda /opt/redpanda /opt/redpanda

ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    HF_HOME=/opt/hf-cache \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:/usr/lib/postgresql/16/bin:/opt/redpanda/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY packages packages
COPY services services
COPY infra infra

# One virtual environment for all services. CPU-only PyTorch (the CUDA build is several GB).
RUN uv venv /opt/venv --python 3.12 \
    && uv pip install --extra-index-url https://download.pytorch.org/whl/cpu \
        --index-strategy unsafe-best-match torch \
        -e packages/liverag-core \
        -r services/api/pyproject.toml -r services/indexer/pyproject.toml \
        -r services/cdc/pyproject.toml -r services/simulator/pyproject.toml

# Bake the embedding model and the reranker into the image, so start-up needs no download.
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('BAAI/bge-small-en-v1.5'); CrossEncoder('BAAI/bge-reranker-base')"

COPY --from=web /web/dist /app/web-dist
COPY deploy/start.sh /app/start.sh

# Hugging Face runs containers as user 1000; PostgreSQL and Redpanda must not run as root anyway.
RUN useradd -m -u 1000 user \
    && mkdir -p /etc/redpanda /var/lib/redpanda/data \
    && chown -R user:user /app /etc/redpanda /var/lib/redpanda /opt/hf-cache \
    && sed -i 's/\r$//' /app/start.sh && chmod +x /app/start.sh
USER user
ENV HOME=/home/user

EXPOSE 7860
CMD ["/app/start.sh"]
