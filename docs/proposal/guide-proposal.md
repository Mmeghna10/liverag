# LiveRAG: Dissertation-2 Proposal (for guide approval)

## Title

**LiveRAG: Consistent, Fresh and Permission-Aware Retrieval-Augmented Generation using Log-Based Change Data Capture**

## Domain

Computer Science: Information Retrieval and NLP (Retrieval-Augmented Generation), drawing on
Database Systems (write-ahead log, logical replication, vector indexing), Data Engineering /
Distributed Systems (change data capture, stream processing, exactly-once delivery) and
Information Security (access control, leakage prevention).

**Keywords:** Retrieval-Augmented Generation, Change Data Capture, Vector Search, Data Freshness,
Exactly-Once Processing, Access Control

---

## Abstract

Retrieval-Augmented Generation (RAG) grounds the answers of Large Language Models in an
organisation's own data, yet most RAG systems build their vector index through periodic batch
jobs. When the source data changes, such systems return stale answers, keep retrieving deleted
records, and may continue to expose documents after a user's access has been revoked. Recent
research has begun to address parts of this problem: streaming and versioned RAG knowledge bases
update vector indexes incrementally, and permission-aware RAG filters retrieval by user role.
Industry tools also stream database changes into vector stores. However, these lines of work
remain separate, and none reports end-to-end correctness guarantees or a quantitative comparison
of synchronisation strategies.

This dissertation proposes LiveRAG, a RAG system whose vector index is kept consistent with a live
PostgreSQL database through log-based Change Data Capture (CDC). A custom reader decodes the
database's write-ahead log and publishes every insert, update, delete and permission change to an
ordered event log (Redpanda/Kafka). An indexer applies each change to a pgvector index with
effectively exactly-once semantics, by committing data and consumer offsets in one transaction and
by discarding out-of-date events using log sequence numbers. Permission revocations travel through
the same pipeline, and only changed text is re-embedded. Retrieval combines vector and full-text
search with a cross-encoder reranker, enforces access control inside the search query, and returns
answers with validated citations, abstaining when evidence is insufficient.

LiveRAG will be evaluated on a synthetic enterprise dataset under continuous change, against
nightly batch re-indexing and timestamp-based polling. Metrics include index staleness (p50/p95),
missed deletes, permission-leak rate after revocation, correctness after injected crashes,
embedding cost per change, and answer quality (retrieval recall, faithfulness and citation
precision). The expected contribution is an open-source reference design and the first empirical
comparison of these synchronisation strategies for fresh, leak-free and cost-efficient RAG over
changing data.

---

## Related work and research gap (short note)

| Area | Representative work | What is missing for this problem |
|---|---|---|
| Streaming / versioned RAG | LiveVectorLake (arXiv 2601.05270); Streaming RAG (arXiv 2508.05662); VectraFlow (CIDR 2025) | Changes do not come from a database log; deletes, permissions and exactly-once delivery are not addressed |
| Freshness in retrieval | Recency prior for temporal RAG (arXiv 2509.19376); RAGPerf (arXiv 2603.10765) | Rank or benchmark freshness, but do not keep the index consistent with the source |
| Permission-aware RAG | ARBITER (arXiv 2512.20535); PERMIT (arXiv 2605.09480); Participant-aware access control (arXiv 2509.14608) | Permissions treated as static; leakage right after a revocation is not measured |
| Reliability benchmarks | LayerRAG-Bench (arXiv 2607.27353) | Shows stale-index and permission failures, but does not propose or compare fixes |
| Change data capture | DBLog (arXiv 2010.12597); Kafka exactly-once (SIGMOD 2021); Debezium to vector-store pipelines (industry) | Correctness guarantees are not carried into a permission-aware vector index or measured for RAG |

**Research gap:** CDC-to-vector pipelines exist in industry, and freshness and access control have
each been studied separately in RAG research. No existing work (a) carries CDC's correctness
guarantees end-to-end into a permission-aware vector index, including deletes and permission
revocations through the same pipeline, and (b) empirically measures staleness, missed deletes,
revocation leak rate, crash correctness and embedding cost against batch and polling baselines.

**Research question:** Can log-based change data capture keep a permission-aware RAG index
consistent with a live relational database, with lower staleness, zero missed deletes, zero
permission leaks and lower embedding cost than batch or polling approaches?

---

## Email to the professor

**Subject:** Request for Dissertation-2 Guidance: Proposal on Fresh and Permission-Aware RAG using Change Data Capture

Dear Prof. [Last name],

I hope you are doing well. I am Meghna [Last name], a student of [Programme, e.g. M.Tech CSE / MCA],
[Semester/Year], Roll No. [number]. I am writing to request you to kindly consider guiding my
Dissertation-2 project.

My proposed work is titled **"LiveRAG: Consistent, Fresh and Permission-Aware Retrieval-Augmented
Generation using Log-Based Change Data Capture."** Most RAG systems refresh their knowledge index in
periodic batches, so answers become stale, deleted records keep being retrieved, and revoked
permissions can still leak data. Recent work has studied streaming RAG indexes and permission-aware
retrieval separately, and industry tools stream database changes into vector stores, but none
provides end-to-end correctness guarantees or measures them. I propose to keep a permission-aware
vector index synchronised with a live PostgreSQL database through its write-ahead log, with
exactly-once updates and revocations flowing through the same pipeline, and to compare it against
batch and polling baselines on staleness, permission leaks, crash correctness and cost.

I have already started the implementation: the database setup, schema and a synthetic dataset are
complete, and the code is on GitHub (github.com/Mmeghna10/liverag). The detailed abstract and a
short note on related work are attached for your reference.

Given your expertise in [the professor's area, e.g. databases / machine learning / information
retrieval], I would be grateful for your guidance on this work. I would be happy to meet at your
convenience to discuss the proposal and incorporate any suggestions you may have.

Thank you for your time and consideration.

Yours sincerely,
Meghna [Last name]
[Programme, Department]
[Roll No.] | [Phone] | rajputmeghna9@gmail.com

---

## Before sending (checklist)

- [ ] Fill in every `[bracket]`
- [ ] Read LiveVectorLake (arXiv 2601.05270) in full; it is the closest prior work
- [ ] Verify each citation yourself on arXiv / Google Scholar
- [ ] Trim the abstract if your department has a word limit (currently about 310 words)
- [ ] Export the abstract and related-work note as a PDF to attach
- [ ] Make the GitHub repo public, or invite the professor, before sharing the link
