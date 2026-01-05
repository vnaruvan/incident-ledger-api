# Incident Intelligence

Incident Intelligence is a production-minded incident log service focused on:

1. Safe storage of incident text with redaction
2. Search that continues to work when external embedding providers are unavailable
3. Auditability via a tamper-evident per-tenant audit chain

It exposes a FastAPI REST API, stores data in Postgres, and uses pgvector for similarity search.

## Why this exists

Many incident tools either store raw JSON blobs with weak governance, or they bolt on embedding search that degrades or breaks when the provider rate limits. This project is built to be reviewable from a clean local clone and to keep core functionality working at $0.

## Core features

- Multi-tenant incident storage with explicit fields (not JSON-only)
- Redaction pipeline for message text (emails, PANs, tokens, etc.)
- RBAC via API keys (viewer, responder, auditor, admin)
- Similarity search using pgvector
- Deterministic local embeddings mode for offline, zero-cost demos
- Audit log with a per-tenant SHA256 hash chain (tamper-evident)
- Health and readiness endpoints (`/health`, `/ready`)
- Lightweight demo UI (`/ui`) and API docs (`/docs`, `/redoc`)

## Architecture

### Tenant isolation and request path

```mermaid
flowchart LR
    C[Client] -->|X-API-Key| API[FastAPI]
    
    API --> AUTH[Auth + RBAC]
    AUTH -->|tenant_id + role| API
    
    API --> REDACT[Redaction]
    API --> EMB[Embeddings: local or OpenAI]
    API --> DB[(Postgres + pgvector)]
    
    API --> AUDIT[Audit append]
    AUDIT --> DB
```

### Create + search flow

```mermaid
sequenceDiagram
    participant C as Client
    participant API as FastAPI
    participant R as Redaction
    participant E as Embeddings
    participant DB as PostgreSQL

    C->>API: POST /api/incidents (X-API-Key)
    API->>API: Validate key, derive tenant_id and role
    API->>R: Redact message
    R-->>API: message_redacted
    API->>E: Embed message (local or OpenAI)
    E-->>API: vector (1536)
    API->>DB: INSERT incident_logs
    API->>DB: INSERT audit_logs (hash chained)
    API-->>C: 200 IncidentLogRead

    C->>API: GET /api/search?q=...
    API->>E: Embed query
    E-->>API: query_vector
    API->>DB: Similarity search scoped to tenant_id
    DB-->>API: top_k results
    API->>DB: INSERT audit_logs (hash chained)
    API-->>C: 200 [IncidentLogRead]
```

### Component overview

| Component | Purpose |
|-----------|---------|
| Multi-tenancy | All data filtered by tenant_id, enforced at query level |
| PII redaction | Emails, PANs, AWS keys, JWTs stripped before storage |
| Dual storage | message_raw (privileged) + message_redacted (default) |
| Vector search | pgvector similarity search on 1536-d embeddings |
| Audit chain | Per-tenant SHA256 hash chain for tamper evidence |
| RBAC | 4 roles with escalating permissions |

## Quickstart (one command)

### Prerequisites

- Python 3.12
- Docker Desktop with WSL integration enabled
- `docker compose` available in your shell
- `jq` (optional)

### Run

From the repo root:

```bash
./scripts/dev_up.sh
```

This brings up Postgres, runs migrations, bootstraps demo API keys, seeds demo incidents, and starts the API.

### Open

- UI: http://localhost:8000/ui
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health
- Readiness: http://localhost:8000/ready

## Demo flow (copy paste)

Export a demo admin key printed by `bootstrap_demo_keys`:

```bash
export DEMO_KEY="paste_demo_admin_key_here"
```

Create an incident:

```bash
curl -s -X POST "http://localhost:8000/api/incidents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $DEMO_KEY" \
  --data-binary '{"service":"payments","severity":"high","title":"pg timeout","message":"postgres timeout for user test@example.com","source":"api","reporter":"oncall","tags":["db","timeout"]}' | jq
```

Search:

```bash
curl -s "http://localhost:8000/api/search?q=postgres%20timeout&top_k=5" \
  -H "X-API-Key: $DEMO_KEY" | jq '.[].message_redacted'
```

View audit logs:

```bash
curl -s "http://localhost:8000/api/audit-logs?limit=20" \
  -H "X-API-Key: $DEMO_KEY" | jq
```

### Tenant isolation sanity check

Create an incident using an `acme` key, then try to read it using a `demo` key. You should get 404.

```bash
export ACME_KEY="paste_acme_admin_key_here"

ACME_ID=$(curl -s -X POST "http://localhost:8000/api/incidents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ACME_KEY" \
  --data-binary '{"service":"payments","severity":"high","title":"acme incident","message":"acme db timeout","source":"api","reporter":"oncall","tags":["acme","db"]}' | jq -r '.id')

curl -i -s "http://localhost:8000/api/incidents/$ACME_ID?include_deleted=false" \
  -H "X-API-Key: $DEMO_KEY" | head -n 20
```

Expected: `HTTP/1.1 404 Not Found`

## Configuration

Copy `.env.example` to `.env` and adjust as needed.

**Required:**
- `DATABASE_URL`

**Optional:**
- `EMBEDDINGS_MODE` (default: local deterministic)
- `OPENAI_API_KEY` (only if you want external embeddings)
- `VECTOR_DIM` (default: 1536)

**Embeddings behavior:**
- Create does not fail if the external embedding provider is unavailable (it records failure status and continues)
- Search works in local mode without any external provider
- Default demo posture is deterministic local embeddings, so reviewers can run it without billing setup

## Tech stack

- FastAPI
- SQLAlchemy
- Alembic migrations
- Postgres 16
- pgvector extension
- Docker Compose for local DB

## Data model (high level)

### `incident_logs`

- `tenant_id`, `service`, `severity`, `title`, `tags`
- `message_raw`, `message_redacted`
- `embedding`, `embedding_model`, `embedding_dim`, `embedding_status`
- soft delete fields

### `api_keys`

- `tenant_id`, `actor_id`, `role`
- `key_hash`, `is_active`

### `audit_logs`

- `tenant_id`, `actor_id`, `action`, `resource_type`, `resource_id`
- `request_meta`, `result_ids`
- `prev_hash`, `hash`

## Testing

```bash
pytest -q
```

## Project structure

- `app/main.py` - App factory, docs, UI mount, health endpoints
- `app/api/` - HTTP routes
- `app/models/` - SQLAlchemy models
- `app/crud/` - DB operations and search
- `app/security/` - Redaction logic
- `app/llm/` - Embedding implementations
- `app/scripts/` - Bootstrap and seed scripts
- `alembic/` - Migrations

## Troubleshooting

### Port 5432 already allocated

Check:

```bash
docker ps --format "table {{.Names}}\t{{.Ports}}" | grep 5432 || true
```

**Fix:**
- Stop the conflicting container, or
- Change the host port mapping in `docker-compose.yaml`

### Docker not found inside WSL

Docker Desktop needs WSL integration enabled for your distro. Enable it in Docker Desktop settings, then restart the terminal.

### 401 invalid or missing API key

You are hitting a fresh DB without bootstrapped keys.

**Fix:**

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/incident_intel"
python -m app.scripts.bootstrap_demo_keys
```

### 404 incident not found when reading cross-tenant

Expected behavior. Incidents are tenant-scoped and filtered by `tenant_id` derived from the API key.

### Search fails with pgvector type errors

This usually means the query embedding passed into pgvector is not a flat list of floats with the correct dimension. Validate that the embedding function returns exactly one vector (not a tuple or nested list) and that `VECTOR_DIM` matches what is stored.


