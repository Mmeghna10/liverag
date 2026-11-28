# Deploying LiveRAG for free

The `Dockerfile` in the repository root runs the whole system in **one container**: PostgreSQL 16
with pgvector (source and index databases), Redpanda, the CDC reader, the streaming indexer and the
API, which also serves the built web app on port 7860. On start it loads the synthetic demo data
and builds the index with the gap-free bootstrap (about 1-2 minutes).

## Free option used: this laptop + Cloudflare quick tunnel

Requirements: Docker Desktop running, Ollama running with `qwen2.5:3b` (answers use the GPU).

```powershell
.\deploy\share.ps1            # first run builds the image (10-15 minutes), then prints the link
.\deploy\stop.ps1             # stop; the link stops working
.\deploy\share.ps1 -Rebuild   # after code changes
```

The script starts the container, waits until it is ready, starts `cloudflared` (Cloudflare's
official tunnel, run as a Docker image) and prints a public link such as
`https://<random>.trycloudflare.com`. No Cloudflare account is needed.

Things to know:
- The link changes on every start and works only while the laptop is on and the containers run.
- Demo data is synthetic and resets on every start; new accounts are not kept.
- The demo password is public on purpose (the sign-in buttons fill it in). Anyone with the link can
  use the shared admin account and change demo access rights until the next restart.
- Stop the demo when you are not presenting it.

## Language model options
The container talks to any OpenAI-compatible chat API (`LLM_PROVIDER=openai`):
- **Local Ollama (used by `share.ps1`):** `LLM_BASE_URL=http://host.docker.internal:11434/v1`,
  `LLM_API_KEY=ollama`, `LLM_MODEL=qwen2.5:3b`.
- **Hosted, for servers without a GPU:** for example Groq: `LLM_BASE_URL=https://api.groq.com/openai/v1`,
  `LLM_API_KEY=<your key, kept secret>`, `LLM_MODEL=<a model from Groq's list>`.

## Other hosts
The same image runs on any Linux server with Docker and about 4-8 GB of RAM (for example an Azure
for Students VM or Oracle Cloud's free tier):

```bash
docker build -t liverag .
docker run -d -p 80:7860 -e LLM_PROVIDER=openai -e LLM_BASE_URL=... -e LLM_API_KEY=... -e LLM_MODEL=... -e JWT_SECRET=<32+ random characters> liverag
```

Hugging Face Docker Spaces are not an option for free: they now require a paid PRO plan.
