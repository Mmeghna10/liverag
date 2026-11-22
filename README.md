# LiveRAG

**A permission-aware RAG assistant whose search index stays in sync with a live PostgreSQL
database through log-based change data capture (CDC), with exactly-once effects.**

Every insert, update, delete and permission change is read from Postgres's write-ahead log,
sent through Redpanda and applied to a pgvector index in about **0.2 seconds**. Revoked access
disappears from search just as fast, and the index stays correct even when the pipeline is
killed mid-transaction.

> Dissertation-2 project. All numbers below come from the scripts in `evals/` and the JSON
> files in `evals/results/`.

## Results

### CDC vs the usual alternatives (same 30 changes for each method)

| Method | Changes visible | Missed | Staleness p50 | Staleness p95 | Leak window p95 | Chunks written |
|---|---|---|---|---|---|---|
| **LiveRAG (log-based CDC)** | **30/30** | **0** | **0.20 s** | **0.26 s** | **0.26 s** | **20** |
| Polling `updated_at` every 5 s | 12/30 | 18 | 2.57 s | 3.81 s | never (12 revocations not applied) | 77 |
| Batch rebuild every 60 s | 30/30 | 0 | 43.2 s | 64.7 s | 64.7 s | 2,947 |

Polling missed **every** hard delete, ACL revoke and group removal: those leave no `updated_at`
to find. Batch is correct but stale for up to a full interval (with a nightly job: hours).
Source: `evals/results/baselines.json`, `uv run python -m evals.baselines`.

### Correctness under failure
- **Chaos test:** 80 random changes with 6 hard kills of the CDC reader and indexer: the index
  ends **byte-for-byte equal** to the source (md5 fingerprints); 4 real duplicate deliveries were
  removed by the LSN guard; freshness p50 0.22 s. (`evals/results/chaos.json`)
- **Snapshot handoff:** under ~50 writes/s, starting CDC the naive way (index first, slot second)
  left 46–49 of 500 products wrong; the exported-snapshot handoff left **0**.
- **Streamed index = fresh batch rebuild** after updates, duplicates, a crash and a full reload.

### Retrieval and answers (44-question golden set, 0 permission leaks)

| Mode | hit@1 | hit@5 | MRR |
|---|---|---|---|
| Hybrid + cross-encoder rerank | 0.90 | 0.97 | 0.93 |
| Hybrid (vector + full-text, RRF) | 0.90 | 0.93 | 0.91 |
| Vector only | 0.86 | 0.90 | 0.88 |
| Keyword only | 0.83 | 0.93 | 0.88 |

End to end (local `qwen2.5:3b`): 100 % of forbidden/unanswerable questions refused, 0 answer
leaks, 92 % of given answers contain the correct fact, 72 % fully correct (fact + citation).

## Architecture

```
 Source DB (Postgres 16, wal_level=logical)
   │  replication slot (pgoutput)
   ▼
 CDC reader ──► Redpanda (1 partition, one message per transaction)
                   │
                   ▼
              Streaming indexer ──► Index DB (Postgres 16 + pgvector + full-text)
                                       ▲
 React web app ──► FastAPI ─────────────┘  hybrid search → rerank → LLM with validated citations
```

- **Reader** confirms a WAL position to Postgres only after Redpanda has acknowledged it.
- **Indexer** applies each source transaction in **one** index transaction together with an LSN
  guard and its Kafka offset, so duplicates are harmless and crashes lose nothing.
- **Permissions** are filtered **inside** the search SQL; group membership is read on every
  request and kept in sync by the same CDC pipeline.
- **Bootstrap** creates the slot with an exported snapshot, so batch and stream meet at one LSN.

## Tech stack
Python 3.12 · uv · PostgreSQL 16 · pgvector (HNSW) · Redpanda · confluent-kafka · psycopg 3 /
psycopg2 (replication) · FastAPI · Alembic · sentence-transformers (bge-small-en-v1.5,
bge-reranker-base) · Ollama (qwen2.5:3b) · React 19 + Vite · Docker Compose

## Run it locally

Requirements: Docker Desktop, Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, Ollama.

```bash
cp .env.example .env          # then fill in passwords and a JWT_SECRET of 32+ characters
docker compose up -d          # source-db :5433, index-db :5434, redpanda :19092
ollama pull qwen2.5:3b

cd services/simulator && uv run python -m simulator.seed            # demo data
cd ../api            && uv run alembic upgrade head                 # index schema
cd ../indexer        && uv run python -m indexer.bootstrap          # index + slot, no gap
```

Then, each in its own terminal:

```bash
cd services/cdc      && uv run python -m cdc.reader                 # WAL -> Redpanda
cd services/indexer  && uv run python -m indexer.stream             # Redpanda -> index
cd services/api      && uv run uvicorn app.main:app                 # API on :8000
cd apps/web          && npm install && npm run dev                  # UI on :5173
```

Sign in as `customer@example.com` or `agent@northwind.test` (password = `DEMO_USER_PASSWORD`)
and ask *"What discount can I give for a damaged item?"*: the agent gets a cited answer, the
customer is refused. Admins can use **View as** to compare users.

## Evaluations

```bash
cd evals
uv run python -m evals.retrieval     # retrieval quality, abstention, leak check
uv run python -m evals.answers       # end-to-end answers (API + Ollama running)
uv run python -m evals.chaos         # random changes + hard kills, fingerprint check
uv run python -m evals.baselines     # CDC vs polling vs batch
```

## Repository layout

```
apps/web/              React + Vite chat UI (login, streaming answers, citations, view-as)
services/api/          FastAPI (auth, search, chat) + Alembic migrations for the index DB
services/cdc/          pgoutput decoder and CDC reader -> Redpanda
services/indexer/      chunking, batch, bootstrap, streaming indexer, polling baseline
services/simulator/    deterministic Northwind Gear demo data
packages/liverag-core/ shared config, embedder, reranker, retrieval SQL
evals/                 golden set, evaluation scripts, results
infra/                 Postgres init scripts, health check
```

## Limitations
Small synthetic dataset and golden set (44 questions); intervals in the baseline experiment are
scaled down (60 s instead of nightly); a single Redpanda partition (global order over
throughput); schema changes (DDL) are not handled automatically; answer quality reflects a
3-billion-parameter local model.