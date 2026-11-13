# LiveRAG — Complete Project Document (v2)

Updated 13 Nov 2026 · Meghna · Repo: github.com/Mmeghna10/liverag

> v2 changes compared with the original PDF: research positioning and related work (section 3),
> decisions taken during Steps 1–3 (section 15), current progress (section 16), and an updated
> starter prompt for a new chat (section 19).

---

## 1. Overview

LiveRAG is a retrieval-augmented AI assistant whose knowledge index stays in sync with a live
Postgres database. It streams every insert, update, delete and permission change from the
database's write-ahead log (WAL) into a vector index within seconds, with exactly-once effects,
even if parts of the pipeline crash. It is an 8-week project (also proposed as a Dissertation-2
project), built in three stages that are each a finished project on their own.

**One-line vision:** an AI assistant that never answers from stale or revoked data.

What we are building:

- A company's data (products, help articles, internal policies, who may read what) lives in a
  normal Postgres database (the **source DB**).
- A **CDC reader** reads every change from Postgres's WAL and publishes it to **Redpanda**
  (Kafka-compatible log).
- An **indexer** consumes those changes, re-chunks and re-embeds only what changed, and updates a
  **pgvector** index (the **index DB**), exactly once.
- A **chat API** answers questions with hybrid search, reranking and an LLM, citing its sources,
  and only retrieves what the asking user is allowed to see.
- A **live ops dashboard** shows how many seconds the index lags behind the database, and a
  **demo editor** lets you change a row and watch the AI's answer change.

Why it stands out: almost every RAG demo embeds documents once. LiveRAG treats the index as a
replica of the source data, with database-grade guarantees: no missed changes, no duplicates,
deletes and revocations that actually take effect, and a measured freshness number.

Three stages (details in section 13):

1. **Solid RAG** (weeks 1–3): permission-aware hybrid RAG with citations and evals.
2. **Live sync** (weeks 4–6): WAL → Redpanda → indexer, exactly-once, crash-tested.
3. **Production grade** (weeks 7–8): zero-downtime model switch, snapshot backfill, schema
   changes, deployment and results write-up.

---

## 2. Problem, goals and the claim we will test

Most RAG systems are built with a batch job: export, chunk, embed, load, repeat nightly (or
never). That causes four failures:

1. **Stale answers.** A price changes at 10:00; the assistant quotes the old one until tomorrow.
2. **Ghost documents.** Deleted or retracted documents keep being retrieved, because batch jobs
   often only upsert and never delete.
3. **Permission leaks.** A user loses access to a confidential file, but its chunks are still in
   the index and still retrievable for them.
4. **Wasted cost.** Re-embedding everything nightly, even when 1% of the data changed.

**Goals**

- Changes become searchable within seconds (target p95 < 5 s on a laptop; measured).
- Every change is applied exactly once in effect: none lost, none duplicated, even after crashes.
- A revoked permission or deleted record can never be retrieved after it is applied (leak rate 0).
- Only changed content is re-embedded.
- Answers cite sources and say "I don't know" when retrieval finds nothing relevant.
- The embedding model can be swapped with zero downtime.

**Non-goals (v1):** multiple or non-Postgres sources; a general-purpose CDC product; multi-region,
Kubernetes or massive scale; fine-tuning an LLM.

**The claim we will test:** compared with (a) a nightly batch re-index and (b) polling an
`updated_at` column, log-based CDC with idempotent, LSN-guarded sinks gives much lower staleness,
zero missed deletes and zero permission leaks, at lower embedding cost. Section 12 defines the
measurements. If a result is worse than expected, we report it as it is.

---

## 3. Research positioning (new in v2)

**Domain:** Computer Science — Information Retrieval / NLP (Retrieval-Augmented Generation),
drawing on Database Systems (WAL, logical replication, vector indexing), Data Engineering /
Distributed Systems (CDC, stream processing, exactly-once delivery) and Information Security
(access control, leakage prevention).

**Keywords:** Retrieval-Augmented Generation, Change Data Capture, Vector Search, Data Freshness,
Exactly-Once Processing, Access Control.

### Related work (found Nov 2026; read the full papers before citing)

| Area | Representative work | What is missing for this problem |
|---|---|---|
| Streaming / versioned RAG | LiveVectorLake (arXiv 2601.05270) — closest prior work; Streaming RAG (arXiv 2508.05662); VectraFlow (CIDR 2025) | Changes do not come from a database log; deletes, permissions and exactly-once are not addressed |
| Freshness in retrieval | Recency prior for temporal RAG (arXiv 2509.19376); RAGPerf (arXiv 2603.10765) | Rank or benchmark freshness; do not keep the index consistent with the source |
| Permission-aware RAG | ARBITER (arXiv 2512.20535); PERMIT (arXiv 2605.09480); participant-aware access control (arXiv 2509.14608); secure-RAG review (arXiv 2603.21654) | Permissions treated as static; leakage right after revocation not measured |
| Reliability benchmarks | LayerRAG-Bench (arXiv 2607.27353) | Shows stale-index and permission failures; proposes and compares no fixes |
| Change data capture | DBLog (arXiv 2010.12597); Kafka exactly-once (SIGMOD 2021, doi 10.1145/3448016.3457556); Debezium → vector store pipelines and PyDebeziumAI (industry, 2025–26) | Guarantees not carried into a permission-aware vector index, nor measured for RAG |

### Research gap

CDC-to-vector pipelines exist in industry, and freshness and access control have each been
studied separately in RAG research. No existing work (a) carries CDC's correctness guarantees
end-to-end into a permission-aware vector index, including deletes and permission revocations
through the same pipeline, and (b) empirically measures staleness, missed deletes, revocation
leak rate, crash correctness and embedding cost against batch and polling baselines.

**Contribution = combining these + measuring them** (systems research with an empirical
evaluation, not a new algorithm).

**Research question:** Can log-based change data capture keep a permission-aware RAG index
consistent with a live relational database, with lower staleness, zero missed deletes, zero
permission leaks and lower embedding cost than batch or polling approaches?

The guide-approval abstract and email are in `docs/proposal/guide-proposal.md`.

---

## 4. Demo scenario, dataset and users

The demo company is a fictional outdoor-gear retailer, **Northwind Gear**. All data is synthetic,
generated by our own deterministic script (no licensing or privacy issues).

| Data | Size | Who can read it |
|---|---|---|
| Products (name, description, price, stock status) | 500 rows | Everyone |
| Public help articles (returns, shipping, warranty, care guides, packing lists) | 300 articles | Everyone |
| Internal policies (discount rules, supplier terms, bulk pricing, HR notes, procedures) | 80 documents (120 ACL rows) | Only certain groups: support, sales, managers, hr |
| Users and groups | 20 users, 5 groups (14 memberships) | — |

Demo users (password from `.env` → `DEMO_USER_PASSWORD`):

| Email | Groups | Role in demo |
|---|---|---|
| `customer@example.com` | none | public data only |
| `agent@northwind.test` | support | support agent |
| `manager@northwind.test` | managers | sees supplier terms |
| `admin@northwind.test` | admins, managers | pipeline dashboard, re-index, model switch |

**The 60-second demo:**

1. As customer: "How much is the Alpine 2 tent?" → "₹12,999" with a citation.
2. In the data editor, change the price to ₹10,499.
3. The dashboard shows the change flowing WAL → Redpanda → indexer → index in ~2 s.
4. Ask again → the new price, citing the updated record.
5. As support agent, ask about the damaged-item discount policy → answered. Revoke the agent's
   `support` group → ask again → "I don't have information on that."
6. Kill the indexer mid-stream, restart → reconciliation shows index = source, no duplicates.

A simulator keeps editing random rows (configurable rate) and a canary writer updates one row
every 10 s to measure freshness continuously.

---

## 5. Architecture

Two paths that meet only in the index DB: a **sync path** (source → index) and a **query path**
(question → answer). A slow LLM never slows syncing; a sync backlog never breaks chat.

```
SYNC PATH
 source app / simulator ──► Source DB (Postgres 16, wal_level=logical)
                               │ WAL via replication slot (pgoutput)
                               ▼
                           CDC reader ──► Redpanda (topic cdc.<table>, key = PK)
                                               │
                                               ▼
                                           Indexer ──► Index DB (Postgres 16 + pgvector)
QUERY PATH                                                  ▲
 Next.js web app ──► LiveRAG API (FastAPI) ─────────────────┘
                         │ hybrid search + rerank + LLM (Claude / Ollama)
 Observability: Prometheus/Grafana (pipeline), Langfuse (retrieval + LLM traces)
```

| Service | Responsibility |
|---|---|
| Source app + simulator | Demo CRUD; background random edits; canary row |
| Source DB | System of record; publishes changes through logical replication |
| CDC reader | Reads WAL from a slot, decodes pgoutput, publishes ordered events, confirms LSNs only after acks |
| Redpanda | Durable, replayable change log; one topic per table, partitioned by primary key |
| Indexer | Chunk, embed (with cache), upsert; deletes; ACL updates; offsets and data in one transaction |
| Index DB | Chunks with vectors, full-text, ACL arrays, LSN guard, offsets, freshness samples, chat history |
| LiveRAG API | Auth, permission-filtered hybrid retrieval, reranking, LLM answer with validated citations, admin endpoints |
| Next.js web app | Chat, "view as" user switch, data editor, live pipeline dashboard |
| Observability | Prometheus/Grafana for pipeline health; Langfuse for every retrieval and LLM call |

---

## 6. Change lifecycle

1. An app or the simulator commits a transaction in the source DB.
2. Postgres writes it to the WAL. Publication `liverag_pub` covers the indexed tables.
3. The CDC reader receives pgoutput messages (Begin, Relation, Insert/Update/Delete, Commit) and
   turns each row change into a JSON event: table, primary key, new values, op, xid, LSN, commit
   timestamp, schema_version.
4. It publishes to topic `cdc.<table>`, keyed by primary key (all changes to one row stay ordered).
5. Only after Redpanda confirms (`acks=all`) does the reader report that LSN to Postgres as flushed.
6. The indexer applies the event in one index-DB transaction that also stores the consumer offset,
   then records end-to-end lag.

| Change | Indexer action | Re-embeds? |
|---|---|---|
| Insert document/product | Chunk, embed new chunks (cache by content hash), insert with row's ACL | New chunks only |
| Update with text change | Re-chunk; keep unchanged-hash chunks, embed new ones, delete removed ones | Changed chunks only |
| Update without text change (e.g. stock status) | Update chunk metadata only | No |
| Delete (or soft-delete `deleted_at`) | Delete all chunks of the record; mark deleted in `source_state` | No |
| ACL change (`document_acl`) | Recompute `allowed_group_ids` on that document's chunks | No |
| User-group change (`user_groups`) | Update `user_groups_replica` used at query time | No |

**Guard rule for every write:** apply an event only if its LSN > `source_state.last_lsn` for that
row. Replayed or out-of-date events become no-ops, which makes retries safe.

Example CDC event (topic `cdc.products`, key = product id):

```json
{"op": "update", "table": "products", "pk": {"id": "7e38ca50-641e-5c94-82c7-3437d64d1258"},
 "after": {"name": "Alpine 2 Tent", "price_inr": 10499, "status": "in_stock"},
 "lsn": "0/1A3F2B8", "xid": 90412, "commit_ts": "2026-11-12T10:04:29.812Z", "schema_version": 3}
```

---

## 7. Tech stack

Python for every backend service, TypeScript for the UI. Everything runs locally with Docker
Compose on a laptop (16 GB RAM), with no paid GPU.

| Layer | Technology | Why |
|---|---|---|
| Source DB | PostgreSQL 16, `wal_level=logical` | System of record; its WAL is the change feed |
| CDC reader | Python 3.12 + psycopg2 logical replication (pgoutput) | Learn the protocol; psycopg 3 lacks the replication protocol, so only this service uses psycopg2 |
| CDC fallback | Debezium Server | Only if the custom reader blocks > 1 week; also a comparison point |
| Event log | Redpanda (single node, Kafka API) | Kafka-compatible, one container, light on RAM |
| Kafka client | confluent-kafka (idempotent producer, manual offsets) | Delivery controls we need |
| Event schema | Pydantic models + JSON Schema in `packages/liverag-core` | Typed events shared by reader, indexer, tests |
| Index DB | PostgreSQL 16 + pgvector (HNSW) + full-text search | Vectors, keywords, ACL filters and offsets in one transactional store |
| Embeddings | BAAI/bge-small-en-v1.5 (384-d) v1; bge-base-en-v1.5 (768-d) v2 for blue/green | Free, CPU-friendly, strong retrieval |
| Reranker | BAAI/bge-reranker-base | Big quality boost on top 20–30, runs on CPU |
| LLM | Claude API (`claude-sonnet-5-5` for answers, a smaller model for cheap tasks) behind a provider interface; **Ollama local model for development** | Strong cited answers; swappable; free dev |
| API | FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic | Typed endpoints, SSE streaming, migrations |
| Auth | JWT + Argon2; users belong to groups | Permission-aware retrieval |
| Frontend | Next.js (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query | Chat, view-as, data editor, dashboard |
| LLM tracing | Langfuse | Every retrieval + LLM call traced |
| Metrics | OpenTelemetry + Prometheus + Grafana | Replication lag, consumer lag, staleness p50/p95 |
| Evaluation | Ragas + own retrieval and freshness harness | Quality and freshness both measured |
| Testing | Pytest, Testcontainers, Toxiproxy, Playwright | Real Postgres/Redpanda in tests; network faults; e2e |
| Quality / CI | Ruff, mypy, ESLint, Prettier, pre-commit, GitHub Actions | Clean code, CI on every push |
| Packaging | Docker, Docker Compose, **uv** | One-command run, fast installs |

**Not used on purpose:** Kubernetes; a separate vector database (pgvector keeps vectors, ACLs and
offsets in one transaction, which exactly-once needs); LangChain/LlamaIndex in the core pipeline.

---

## 8. Core design (the part interviewers and examiners will ask about)

### 8.1 CDC reader (WAL → Redpanda)
- Source DB: `wal_level=logical`, `max_replication_slots=4`, `max_wal_senders=4`,
  `max_slot_wal_keep_size=1GB` (already configured).
- Publication `liverag_pub` (already created); slot `liverag_slot` with `pgoutput` (Week 4).
- Buffer one transaction Begin → Commit, then publish its row events; all carry the commit LSN.
- Producer: `enable.idempotence=true`, `acks=all`. Send flush-LSN feedback only after Redpanda
  acks every event up to that LSN. A crash before feedback → Postgres resends → possible
  duplicates → harmless because the indexer is idempotent.
- Watch-out: an abandoned slot makes Postgres keep WAL forever; alert on slot lag; the 1 GB cap.

### 8.2 Exactly-once into the index
1. **Offsets stored with the data.** Auto-commit off. Each batch writes chunks and
   `(topic, partition, next_offset)` into `sink_offsets` in one transaction. On start, seek to
   `sink_offsets`, not Kafka's. A crash mid-batch rolls back both.
2. **LSN guard per row.** `source_state(source_table, source_id, last_lsn)`; events at or below
   it are skipped.
- A **reconciliation job** compares source and index (row counts, content hashes, ACLs) — the
  proof, run after every crash test.

### 8.3 Permission-aware retrieval
- Each chunk stores `allowed_group_ids int[]` and `is_public bool`, denormalised from
  `document_acl` / `documents.is_public`.
- Filter **inside** the search SQL: `WHERE is_public OR allowed_group_ids && :user_groups`.
  Filtering after retrieval is not allowed (leaks via scores, silently returns too few results).
- User groups come from `user_groups_replica` in the index DB, itself synced by CDC, so a
  revocation flows through the same pipeline.
- ACL changes update arrays without re-embedding → revocation is the fastest path.
- Tests assert leak rate exactly 0 after a revocation is applied.

### 8.4 Zero-downtime embedding model switch (blue/green)
`index_versions` (model, dims, status building/active/retired); one table per version
(`chunks_v1`, `chunks_v2`) because HNSW needs a fixed dimension. Create v2 → indexer dual-writes →
backfill v2 from a snapshot → reconciliation passes → eval on v2 → admin flips `active` in one
transaction → retire v1. Queries always read the single active version.

### 8.5 Initial snapshot and backfill with no gaps or overlaps
Create the slot with `EXPORT_SNAPSHOT`; read all rows inside that snapshot
(`SET TRANSACTION SNAPSHOT`), tag with the slot's start LSN, then stream. Re-backfill one table
live by PK chunks; the LSN guard makes overlap harmless. Stretch: Netflix DBLog watermarks.

### 8.6 Schema changes without stopping
Cache pgoutput Relation messages; include `schema_version` in events. Added columns are ignored
unless configured (config change → re-index that table only). Dropped/renamed configured columns
→ stop that table's consumer and alert; other tables keep flowing. Contract test runs
`ALTER TABLE ... ADD COLUMN` under simulator load.

---

## 9. RAG design

Retrieval quality is built and measured in Stage 1, before streaming exists.

**Chunking**
- Products: one chunk per product (name, description, price, status + structured metadata).
- Articles and policies: split by `## ` headings (the generator already writes them), then
  ~400-token chunks with ~50-token overlap; each chunk starts with the document title.
- `content_hash` = SHA-256 of chunk text; `embedding_cache (model, content_hash) → vector`.

**Retrieval pipeline**
1. Load the user's groups from `user_groups_replica`.
2. Two searches on the active index, both with the same permission filter: vector top-30
   (cosine, HNSW) and keyword top-30 (Postgres full-text rank; catches names like "Alpine 2").
3. Merge with Reciprocal Rank Fusion (RRF).
4. Rerank top 30 with the cross-encoder; keep top 6.
5. Best rerank score below a tuned threshold → "I don't have information on that" (no LLM call).

**Answering**
- The LLM gets the question + 6 chunks with IDs and returns structured output:
  `answer`, `citations: [chunk_id]`, `confidence`.
- The API removes any cited ID not in the retrieved set and flags the answer.
- Retrieved text is wrapped as data, not instructions; system prompt ignores instructions inside
  documents.
- Responses stream over SSE; citations render as cards with source record and last-updated time.
- Conversation: keep last few turns; rewrite follow-ups into standalone queries with a cheap call.

---

## 10. Database design

Two separate Postgres instances on purpose: the source DB plays the company's real database (we
only read its WAL); the index DB belongs to LiveRAG.

### 10.1 Source DB (built — `infra/postgres/source-db/init/`)

| Table | Key columns | Notes |
|---|---|---|
| users | id uuid, email (unique), display_name, password_hash (Argon2), is_active, created_at | API logs users in against this (read-only) |
| groups | id int, name | 1 support, 2 sales, 3 managers, 4 hr, 5 admins |
| user_groups | user_id, group_id (composite PK, FKs, cascade) | Replicated to index DB via CDC |
| products | id uuid, sku (unique), name, description, price_inr int (≥0), status (check), updated_at | `updated_at` trigger |
| documents | id uuid, kind (article/policy), title, body, is_public (default false), deleted_at, updated_at | Soft delete via `deleted_at`; `updated_at` trigger |
| document_acl | document_id, group_id (composite PK, FKs, cascade) | Which groups can read a non-public document |
| canary | id, value bigint, written_at | Freshness probe row (id 1) |

Publication: `CREATE PUBLICATION liverag_pub FOR TABLE products, documents, document_acl,
user_groups, canary;` (`users` and `groups` are read directly by the API, not indexed.)

### 10.2 Index DB (next — Alembic migrations in `services/api`)

| Table | Key columns | Notes |
|---|---|---|
| index_versions | id, embedding_model, dims, status, created_at, activated_at | Exactly one active (partial unique index) |
| chunks_v{n} | id uuid, source_table, source_id, chunk_no, text, tsv tsvector, embedding vector(dims), content_hash, is_public, allowed_group_ids int[], metadata jsonb, source_lsn pg_lsn, updated_at | HNSW on embedding; GIN on tsv and allowed_group_ids; unique (source_table, source_id, chunk_no) |
| source_state | source_table, source_id, last_lsn, deleted, updated_at | LSN guard |
| sink_offsets | consumer, topic, partition, next_offset | Same transaction as chunks |
| user_groups_replica | user_id, group_id, last_lsn | Query-time permission source |
| embedding_cache | model, content_hash, embedding vector, created_at | Avoids re-embedding |
| freshness_samples | id, source_table, source_id, commit_ts, indexed_ts, lag_ms | Staleness p50/p95 |
| conversations, messages | id, user_id, created_at / role, content, citations jsonb, trace_id | Chat history + Langfuse link |
| feedback | message_id, rating, comment | Thumbs up/down |
| reconciliation_runs | id, started_at, finished_at, mismatches jsonb | Proof that index = source |

Rules: UUIDs for record IDs; every index row carries its `source_lsn`; offsets and data in one
transaction; no FKs from index DB into source DB; Alembic for the index DB, plain SQL for the
source DB.

---

## 11. API design

FastAPI under `/api/v1`, docs at `/docs`. The demo data editor talks to a separate source-app
service, so LiveRAG never writes to the source DB.

| Method | Endpoint | Purpose | Role |
|---|---|---|---|
| POST | /auth/login | Email + password → JWT (checked against source `users`) | public |
| GET | /me | Current user and groups | any |
| POST | /chat | Ask; streams answer (SSE) with citations | any |
| GET | /conversations, /conversations/{id} | Chat history | owner |
| POST | /messages/{id}/feedback | Thumbs up/down + comment | owner |
| POST | /search | Raw retrieval results with scores (debug) | any |
| GET | /sources/{table}/{id} | Cited source record, permission-checked | any |
| GET | /admin/pipeline/status | Slot lag (bytes), consumer lag, staleness p50/p95, last reconciliation | admin |
| GET/POST | /admin/index-versions | List / start building a version | admin |
| POST | /admin/index-versions/{id}/activate | Flip active version (checks reconciliation + eval) | admin |
| POST | /admin/backfill | Backfill one table into a version | admin |
| POST | /admin/reconcile | Run reconciliation now | admin |
| GET | /health, /metrics | Liveness, Prometheus | internal |

Source-app API (demo only): CRUD on `/products`, `/documents`, plus
`POST/DELETE /documents/{id}/acl/{group}` and `POST/DELETE /users/{id}/groups/{group}`.

Rules: Pydantic validation everywhere; one error shape `{"error": {"code", "message",
"request_id"}}`; rate limit on `/chat`; `X-Request-ID` propagated; admin routes behind `admins`.

---

## 12. Evaluation, baselines and success criteria

### 12.1 Answer quality (Stage 1)
- Golden set: ~120 questions with expected record IDs (stable thanks to uuid5, section 15),
  + 20 no-answer questions, + 20 group-restricted questions.
- Retrieval: recall@6, MRR, nDCG@6 — vector-only vs keyword-only vs hybrid vs hybrid+rerank.
- Answers: Ragas faithfulness and answer relevancy; citation precision; correct-abstention rate.
- Permissions: leak rate on restricted questions by the wrong user — must be 0.

### 12.2 Freshness and sync correctness (Stage 2)

| Metric | How measured |
|---|---|
| Staleness p50/p95/max | commit_ts → indexer commit (`freshness_samples`) at 10 and 100 changes/s |
| Change-then-ask correctness | Scripted: change price / delete article / revoke group, wait N s, ask |
| Revocation leak rate | Restricted questions right after revoke applied — must be 0 |
| Missed deletes | Deleted records retrievable after catch-up — must be 0 |
| Crash correctness | 20+ random kills of reader/indexer (+ Toxiproxy faults), then reconciliation — 0 mismatches, 0 duplicates |
| Embedding cost | Chunks embedded per real change; cache hit rate |
| Throughput | Max sustained changes/s before staleness p95 > 5 s |

### 12.3 Baselines
1. Nightly batch re-index. 2. Polling `updated_at` every 30 s (misses hard deletes). 3. LiveRAG
(log-based CDC). Same scenarios; the results table is the headline of the write-up and the core
of the dissertation's evaluation chapter.

### 12.4 Acceptance criteria (gates)
- Stage 1: hybrid + rerank beats vector-only on recall@6; leak rate 0; numbers in `eval/reports/`.
- Stage 2: 20 crash runs with 0 mismatches; staleness p95 at two load levels.
- Stage 3: v1 → v2 model switch with no failed queries; baseline table published.
- One-command run (`docker compose up`), CI green, public demo or recorded video.

---

## 13. Staged roadmap

Don't start a stage until the previous gate passes.

**Stage 1 — Solid RAG**
- Week 1: scaffold, docker-compose (source-db, index-db), synthetic data generator + seed ✅;
  index DB migrations, batch indexer, vector search from the command line (next).
- Week 2: hybrid search (vector + full-text + RRF) + reranker, permission filter, JWT auth,
  `/chat` with SSE and citations, Next.js chat with "view as user".
- Week 3: golden set, retrieval + answer evals, abstention threshold, Langfuse.
  **GATE:** eval report v1, leak rate 0.

**Stage 2 — Live sync**
- Week 4: logical replication slot, cdc-reader decoding pgoutput → Redpanda with correct LSN feedback.
- Week 5: streaming indexer (offsets-in-transaction, LSN guard, deletes, ACL fast path, embedding cache).
- Week 6: canary + freshness_samples, Grafana, reconciliation, chaos tests.
  **GATE:** 20 crash runs, 0 mismatches.

**Stage 3 — Production grade**
- Week 7: snapshot + stream handoff (`EXPORT_SNAPSHOT`), blue/green v1 → v2, added-column handling.
- Week 8: baselines vs LiveRAG, deployment or recorded demo, README and results write-up.

If the custom pgoutput reader blocks > 1 week in Week 4, switch to Debezium Server and keep weeks
5–8 unchanged; the indexer's guarantees are the main contribution either way.

If Dissertation-2 runs a full semester, use the extra time for the literature review and the
evaluation chapter.

---

## 14. Repository structure (target)

```
liverag/
├── apps/web/                    # Next.js: chat, view-as, data editor, dashboard
├── services/
│   ├── api/                     # FastAPI: auth, chat, search, admin; alembic/ (index DB)
│   ├── cdc-reader/              # WAL (pgoutput) -> Redpanda
│   ├── indexer/                 # Redpanda -> chunk/embed -> pgvector
│   ├── source-app/              # demo CRUD over the source DB
│   └── simulator/               # data generator ✅, background edits, canary
├── packages/liverag-core/       # event schemas, chunking, embedding client, config
├── eval/{golden,retrieval,answers,freshness,baselines,reports}/
├── infra/
│   ├── postgres/{source-db,index-db}/init/   ✅
│   ├── prometheus/  grafana/
│   └── scripts/healthcheck.py   ✅
├── tests/{e2e,chaos}/
├── docs/{architecture.md, delivery-semantics.md, threat-model.md, adr/, proposal/}
├── .github/workflows/ci.yml
├── docker-compose.yml  ✅   .env.example  ✅   .gitattributes  ✅   .gitignore  ✅
└── README.md
```

---

## 15. Decisions taken during Steps 1–3 (new in v2)

**Environment**
- Windows 11 + Docker Desktop (WSL 2). Project at `C:\dev\liverag` (not OneDrive).
- Hardware: Intel i7-12650H (10 cores / 16 threads), 16 GB RAM, GPU: [fill in — check Task
  Manager → Performance]. Docker memory will be capped in Stage 2.
- No Claude API key yet (Claude Pro ≠ API). Plan: templates for data, Ollama for development,
  buy a few dollars of API credit for evals and the demo (Week 3+).

**Containers and config**
- `liverag-source-db`: `postgres:16`, host port **5433**, logical WAL settings in `command:`.
- `liverag-index-db`: `pgvector/pgvector:pg16` (pgvector 0.8.x), host port **5434**,
  `CREATE EXTENSION vector` in its init script.
- `.env` variables: `SOURCE_DB_USER/PASSWORD/NAME/PORT`, `INDEX_DB_USER/PASSWORD/NAME/PORT`,
  `DEMO_USER_PASSWORD`. `.env` is git-ignored; `.env.example` is committed.
- Init SQL runs only on an empty volume → `docker compose down -v` to re-run it.
- `.gitattributes` forces LF line endings (`*.ps1` stays CRLF).

**Health check** — `infra/scripts/healthcheck.py` (PEP 723 inline deps, run with
`uv run infra/scripts/healthcheck.py`): checks server version 16, `wal_level=logical`,
slots/senders ≥ 4, pgvector installed, cosine distance sanity check; exit code 0/1.

**Data generator** — `services/simulator` (uv project; run from that folder with
`uv run python -m simulator.seed`):
- `catalog.py`: templates; one `random.Random(42)` drives content.
- **IDs are `uuid5(ID_NAMESPACE, "kind:key")`** — products by SKU, users by email, documents by
  title — so adding data never changes another record's ID. Namespace
  `6f1d3a52-6c1b-4c1e-9a57-2f0c9b7e4d10`.
- Hero record: **Alpine 2 Tent, SKU TNT-0001, ₹12,999, id `7e38ca50-641e-5c94-82c7-3437d64d1258`**.
- `seed.py`: TRUNCATE + all inserts in one transaction; Argon2 password hashes; parameterised SQL.
- Known limitation: adding a category reshuffles which product gets which SKU.
  **Freeze the catalogue before building the golden set (Week 3).**

**Working conventions**
- Run `docker compose` and `git` from `C:\dev\liverag`; the seed from `services\simulator`.
- Use `psql -P pager=off` to avoid the pager (press `q` to exit it).
- One step = one commit; push after each working step; never commit `.env` or `.venv`.

---

## 16. Current progress (as of 13 Nov 2026)

| Step | Status | Commit |
|---|---|---|
| 1. Scaffold, compose (2 DBs), health check | ✅ | 102b2fe |
| 2. Source DB schema, seed groups/canary, publication | ✅ | 509c7aa |
| 3. Data generator (templates, deterministic) | ✅ | 3f91d75 |
| Line endings (.gitattributes) | ✅ | 00c146d |
| uuid5 natural-key IDs | ✅ | 1a870ca |
| Guide-approval proposal (`docs/proposal/guide-proposal.md`) | drafted | not committed |
| **4. Index DB migrations with Alembic** | **next** | — |

Open small self-tasks: HNSW check in the health check; publication check in the health check;
ADR `docs/adr/0001-two-postgres-instances.md`.

Verified source-DB state: 20 users, 14 memberships, 500 products, 300 articles, 80 policies,
120 ACL rows.

---

## 17. Risks and mitigation

| Risk | Mitigation |
|---|---|
| pgoutput decoding takes too long | Start with simple types; time-box to 1 week; fall back to Debezium Server |
| Abandoned replication slot fills the disk | `max_slot_wal_keep_size=1GB` (set), slot-lag alert, drop-slot helper |
| Laptop runs out of memory | Redpanda `--smp 1 --memory 1G`; small embedding model; rerank top 30 only; cap Docker at 8–10 GB |
| Embedding slow on CPU | bge-small, batching, embedding cache, changed chunks only |
| LLM cost while testing | Ollama in development; Claude only for evals and demo; daily cap |
| "Exactly-once" claimed but not proven | Reconciliation + chaos tests in CI; claim only what tests show |
| Permission bugs leak data | Filter inside SQL; leak tests on every PR |
| Prompt injection in documents | Documents as data; citation validation; no side-effect tools in chat path |
| Closest prior work overlaps (LiveVectorLake, Debezium tooling) | Position contribution as guarantees + permission revocation + measured comparison (section 3) |
| Scope creep | Stage gates |
| Windows/Docker quirks | Project outside OneDrive; WSL 2; LF line endings |

---

## 18. Prerequisites and reading list

| Before | Learn | Where |
|---|---|---|
| Stage 1 | Python async, FastAPI, SQL joins and indexes, Docker Compose | FastAPI tutorial; Docker "Get started" |
| Stage 1 | Embeddings, cosine similarity, chunking, hybrid search, reranking, RAG evaluation | pgvector README; sentence-transformers docs; Ragas docs |
| Stage 1 | JWT basics; structured output with an LLM API | Anthropic API docs |
| Stage 2 | Postgres MVCC, WAL, LSNs, logical replication, publications, slots, pgoutput | Postgres docs: WAL, Logical Replication, Streaming Replication Protocol |
| Stage 2 | Kafka topics, partitions, keys/ordering, offsets, consumer groups, idempotent producer | Redpanda University or Confluent Kafka 101 |
| Stage 2 | At-least-once + idempotent sink = effectively exactly-once | DDIA (Kleppmann) ch. 5, 7, 11 |
| Stage 3 | Consistent snapshots, backfill strategies | Postgres replication docs; DBLog paper |
| Stage 3 | Blue/green deployments; Prometheus and Grafana | Getting-started guides |
| Dissertation | Related work in section 3 | arXiv / Google Scholar / IEEE / ACM DL |

---

## 19. Prompt to continue in a new Claude chat

How to use: open a new chat with the project folder `C:\dev\liverag`, attach this document (or
point Claude to `docs/LiveRAG-project-document.md`), fill in the `[fill in]` lines, and paste the
box below.

```
You are my senior engineer and mentor. We are building "LiveRAG": a permission-aware RAG
assistant whose vector index stays in sync with a live Postgres database through log-based
change data capture (WAL -> Redpanda -> indexer -> pgvector), with exactly-once effects,
measured freshness, and zero permission leaks. It is also my Dissertation-2 project. The full
project document is docs/LiveRAG-project-document.md; treat it as the source of truth. If you
think something in it is wrong, tell me and propose the change before deviating.

## How I want to work (important)
I am building this myself to learn. Do NOT create the project files for me.
- One small step per reply. For each step: say what we are building and why, give me the
  complete code for each file (no "...rest of code" placeholders), the exact PowerShell
  commands, and the output I should expect.
- Explain the key idea behind each piece in 2-4 short bullets, so I can explain it in an
  interview or viva.
- End each step with 2-3 small tasks I do on my own, without their code (give hints).
- Then STOP and wait for me to paste my results or errors.
- If something is ambiguous, ask me one focused question instead of guessing.
- Before giving me code, test it yourself in a scratch folder when you can.

## Stack (fixed)
- Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic
- Source DB: PostgreSQL 16 (wal_level=logical). Index DB: PostgreSQL 16 + pgvector (HNSW) +
  full-text search
- CDC reader: psycopg2 logical replication with pgoutput (fallback: Debezium Server, only if
  stuck > 1 week)
- Redpanda (single node) + confluent-kafka (idempotent producer, manual offsets)
- Embeddings: BAAI/bge-small-en-v1.5 (v1), BAAI/bge-base-en-v1.5 (v2); reranker
  BAAI/bge-reranker-base
- LLM: Claude API (claude-sonnet-5-5) behind a provider interface; Ollama for development
  (I have no API key yet)
- Next.js + TypeScript + Tailwind + shadcn/ui; Langfuse; OpenTelemetry + Prometheus + Grafana;
  Ragas
- Pytest, Testcontainers, Toxiproxy, Playwright; Ruff, mypy, ESLint; Docker Compose; GitHub
  Actions
- No Kubernetes, no separate vector database, no LangChain/LlamaIndex in the core pipeline.

## Non-negotiable design rules
1. Permission filter inside the retrieval SQL, never after retrieval. Leak tests on every PR.
2. Indexer writes chunks AND Kafka offsets in one Postgres transaction; on start it seeks to
   stored offsets.
3. LSN guard: apply an event only if its LSN > source_state.last_lsn for that row.
4. CDC reader confirms an LSN to Postgres only after Redpanda acks everything up to it. Events
   keyed by primary key.
5. Re-embed only changed chunks (content-hash embedding cache).
6. Citations must refer to retrieved chunk IDs; abstain below a tuned rerank threshold.
7. Never invent results. Every claim in the README/dissertation comes from eval/reports.
8. Record IDs are uuid5 of natural keys (already implemented); freeze the catalogue before
   the golden set.

## Where we are (see section 16 of the document)
Done: Step 1 (compose: source-db on 5433 with logical WAL, index-db on 5434 with pgvector,
healthcheck), Step 2 (source schema + liverag_pub publication), Step 3 (deterministic data
generator in services/simulator with uuid5 IDs; 20 users, 500 products, 300 articles,
80 policies, 120 ACL rows; Alpine 2 Tent = 7e38ca50-641e-5c94-82c7-3437d64d1258).
Repo: github.com/Mmeghna10/liverag (branch main).

## My context
- OS: Windows 11, Docker Desktop (WSL 2), PowerShell. Project: C:\dev\liverag.
- Hardware: i7-12650H, 16 GB RAM, GPU: [fill in]
- Experience: [fill in, e.g. comfortable with Python, new to Kafka and Postgres replication]

Start with Stage 1, Week 1, Step 4: set up services/api with Alembic and create the index DB
schema (index_versions, chunks_v1 with vector(384) + HNSW + tsvector + GIN on
allowed_group_ids, source_state, sink_offsets, user_groups_replica, embedding_cache), plus a
check that proves the HNSW and GIN indexes exist.
```

**Tip:** keep one chat per stage. When a chat gets long, start a new one with this same prompt
and update section 16 ("Stage 1 done, eval report attached; start Week 4").
