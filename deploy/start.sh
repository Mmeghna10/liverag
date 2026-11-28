#!/usr/bin/env bash
# Start the whole LiveRAG stack inside one container (see Dockerfile and deploy/README.md):
# PostgreSQL (source + index databases), Redpanda, demo data, migrations, gap-free bootstrap,
# CDC reader, streaming indexer and the API, which also serves the web app.
set -euo pipefail

# ---------------------------------------------------------------- settings
# Both databases live in one PostgreSQL server inside the container. Passwords are not secret
# here: PostgreSQL listens on 127.0.0.1 only and is never exposed outside the container.
export DB_HOST=127.0.0.1 SOURCE_DB_PORT=5432 INDEX_DB_PORT=5432
export SOURCE_DB_USER=northwind SOURCE_DB_PASSWORD=northwind SOURCE_DB_NAME=northwind
export INDEX_DB_USER=liverag INDEX_DB_PASSWORD=liverag INDEX_DB_NAME=liverag
export KAFKA_BOOTSTRAP=127.0.0.1:9092
export DEMO_USER_PASSWORD="${DEMO_USER_PASSWORD:-northwind-demo}"
export WEB_DIST=/app/web-dist
# Set JWT_SECRET as a secret to keep sign-ins valid across restarts; otherwise a random one.
export JWT_SECRET="${JWT_SECRET:-$(python -c 'import secrets; print(secrets.token_urlsafe(48))')}"
PORT="${PORT:-7860}"
PGDATA="$HOME/pgdata"
LOGS="$HOME/logs"
mkdir -p "$LOGS"
log() { echo "[start] $*"; }

# ---------------------------------------------------------------- PostgreSQL
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  log "Initialising PostgreSQL"
  # trust: connections are only possible from inside this container (listen 127.0.0.1)
  initdb -D "$PGDATA" -U postgres --auth=trust >/dev/null
  cat >> "$PGDATA/postgresql.conf" <<EOF
listen_addresses = '127.0.0.1'
unix_socket_directories = '/tmp'
wal_level = logical
max_replication_slots = 4
max_wal_senders = 4
max_slot_wal_keep_size = 1GB
EOF
  FRESH=1
fi
pg_ctl -D "$PGDATA" -l "$LOGS/postgres.log" -w start >/dev/null
PSQL="psql -h 127.0.0.1 -v ON_ERROR_STOP=1 -q"
if [ "${FRESH:-0}" = 1 ]; then
  log "Creating databases"
  $PSQL -U postgres -c "CREATE ROLE northwind LOGIN REPLICATION PASSWORD 'northwind'" \
        -c "CREATE DATABASE northwind OWNER northwind" \
        -c "CREATE ROLE liverag LOGIN PASSWORD 'liverag'" \
        -c "CREATE DATABASE liverag OWNER liverag"
  for f in /app/infra/postgres/source-db/init/*.sql; do $PSQL -U northwind -d northwind -f "$f"; done
  $PSQL -U postgres -d liverag -f /app/infra/postgres/index-db/init/01-extensions.sql
fi

# ---------------------------------------------------------------- Redpanda
log "Starting Redpanda"
rpk redpanda start --mode dev-container --smp 1 --memory 1G --overprovisioned \
  --kafka-addr 127.0.0.1:9092 --advertise-kafka-addr 127.0.0.1:9092 \
  --rpc-addr 127.0.0.1:33145 --advertise-rpc-addr 127.0.0.1:33145 \
  >"$LOGS/redpanda.log" 2>&1 &
for _ in $(seq 1 90); do
  if rpk cluster health -X brokers=127.0.0.1:9092 2>/dev/null | grep -q 'Healthy:.*true'; then break; fi
  sleep 1
done
rpk cluster health -X brokers=127.0.0.1:9092 | grep -q 'Healthy:.*true' \
  || { log "Redpanda did not start"; tail -50 "$LOGS/redpanda.log"; exit 1; }

# ---------------------------------------------------------------- data, schema, index
log "Loading demo data"
(cd /app/services/simulator && python -m simulator.seed)
log "Migrating the index database"
(cd /app/services/api && alembic upgrade head)
log "Building the index (gap-free bootstrap)"
(cd /app/services/indexer && python -m indexer.bootstrap --force)

# ---------------------------------------------------------------- long-running processes
log "Starting CDC reader, streaming indexer and API on port $PORT"
(cd /app/services/cdc && exec python -m cdc.reader) >"$LOGS/reader.log" 2>&1 &
(cd /app/services/indexer && exec python -m indexer.stream) 2>&1 | tee "$LOGS/indexer.log" &
(cd /app/services/api && exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT") &

# If any process exits, stop the container so the platform restarts it.
wait -n
log "A process exited; stopping"
exit 1
