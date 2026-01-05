# Incident Intelligence

Incident Intelligence is a small, production-minded incident log service that prioritizes three things:

1. Safe storage of incident text with redaction
2. Search that still works when external embedding providers are unavailable
3. Auditability via a tamper-evident per-tenant audit chain

It exposes a FastAPI REST API, stores data in Postgres, and uses pgvector for similarity search.

## Why this exists

Most incident tools either store raw JSON blobs with weak governance, or they bolt on “AI search” in a way that breaks the moment the embedding provider rate limits. This project is built to be reviewable in a clean local clone and to keep its core features functioning at $0.

## Core features

- Multi-tenant incident storage with explicit fields (not JSON-only)
- Redaction pipeline for message text (emails, PANs, tokens, etc.)
- RBAC via API keys (viewer, responder, auditor, admin)
- Semantic search via pgvector
- Deterministic local embeddings mode for offline, zero-cost demo
- Audit log with a per-tenant hash chain (tamper-evident)
- Health and readiness endpoints (`/health`, `/ready`)
- Lightweight demo UI (`/ui`) and API docs (`/docs`, `/redoc`)

## Architecture

flowchart LR
    subgraph Tenant Isolation
        direction TB
        TenantA[Tenant: demo]
        TenantB[Tenant: acme]
    end
    
    subgraph RBAC Roles
        direction TB
        Viewer[viewer<br/>read incidents, search]
        Responder[responder<br/>+ read raw, update]
        Auditor[auditor<br/>+ audit logs, deleted]
        Admin[admin<br/>+ delete, create keys]
    end
    
    TenantA --> Viewer
    TenantA --> Responder
    TenantA --> Auditor
    TenantA --> Admin
    
    TenantB --> Viewer
    TenantB --> Responder
    TenantB --> Auditor
    TenantB --> Admin

    style Viewer fill:#a8e6cf
    style Responder fill:#88d8b0
    style Auditor fill:#ffeaa7
    style Admin fill:#ff7675

sequenceDiagram
    participant C as Client
    participant API as FastAPI
    participant R as Redaction
    participant E as Embeddings
    participant DB as PostgreSQL

    C->>API: POST /api/incidents<br/>X-API-Key: xxx
    API->>API: Validate API Key + RBAC
    API->>R: Redact PII from message
    R-->>API: message_redacted
    API->>E: Generate embedding<br/>(local or OpenAI)
    E-->>API: vector[1536]
    API->>DB: INSERT incident_logs
    API->>DB: INSERT audit_logs<br/>(with hash chain)
    API-->>C: 200 IncidentLogRead

    C->>API: GET /api/search?q=postgres
    API->>E: Embed query
    E-->>API: query_vector
    API->>DB: cosine_distance search<br/>WHERE tenant_id = X
    DB-->>API: top_k results
    API->>DB: INSERT audit_logs
    API-->>C: 200 [IncidentLogRead]

Component	Purpose
Multi-tenancy:	All data filtered by tenant_id, enforced at query level
PII Redaction:	Emails, PANs, AWS keys, JWTs stripped before storage
Dual storage:	message_raw (privileged) + message_redacted (default)
Vector search:	pgvector cosine similarity on 1536-dim embeddings
Audit chain:	Per-tenant SHA256 hash chain for tamper evidence
RBAC: 4 roles with escalating permissions



## Request path in practice

1. Client sends request with `X-API-Key`
2. API authenticates key, derives `tenant_id` and role
3. Payload is validated, message is redacted
4. Incident is inserted
5. An embedding is generated
6. Local deterministic mode is default for demos
7. External provider can be enabled via environment
8. Audit log entry is appended with a chained hash per tenant

---

## Tech stack

- FastAPI
- SQLAlchemy
- Alembic migrations
- Postgres 16
- pgvector extension
- Docker Compose for local DB

---

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

---

## Local quickstart

### Prereqs

- Python 3.12
- Docker Desktop with WSL integration enabled
- `jq` (optional, but useful)

### 1) Start Postgres (pgvector image)

```bash
docker compose up -d db
docker compose ps
```
```markdown
# Incident Intel Setup Guide

## Prerequisites

If port 5432 is already in use, stop the other container or change the host port in `docker-compose.yaml`.

## Setup Instructions

### 1. Create Database and Enable pgvector

```bash
docker compose exec -T db createdb -U postgres incident_intel || true
docker compose exec -T db psql -U postgres -d incident_intel -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker compose exec -T db psql -U postgres -d incident_intel -c "\dx"
```

### 2. Run Migrations

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/incident_intel"
alembic upgrade head
alembic current
```

Verify tables:

```bash
docker compose exec -T db psql -U postgres -d incident_intel -c "\dt"
```

### 3. Bootstrap Demo API Keys

This prints plaintext keys once. Store them locally.

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/incident_intel"
python -m app.scripts.bootstrap_demo_keys
```

**Expected tenants:**
- `demo` (admin, auditor, viewer)
- `acme` (admin, viewer)

### 4. Seed Demo Incidents (Optional but Recommended)

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/incident_intel"
python -m app.scripts.seed_demo_incidents --count 150
```

### 5. Run the API

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/incident_intel"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Available endpoints:**
- Home: http://localhost:8000/
- Demo UI: http://localhost:8000/ui
- Swagger: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and adjust as needed.

**Required:**
- `DATABASE_URL`

**Optional:**
- `OPENAI_API_KEY` (only if you want external embeddings)
- `EMBEDDINGS_MODE` (for example `local` or `openai`, depending on your config)
- `VECTOR_DIM` (default 1536)

### Embeddings Behavior

This project is intentionally resilient:
- Create must not fail if the external embedding provider is unavailable
- Search must not fail in local mode
- Default demo posture is local deterministic embeddings, so reviewers can run it without spending money or setting up billing

## API Usage

### Set an API Key

```bash
export KEY="paste_key_here"
```

### Create an Incident

```bash
curl -s -X POST "http://localhost:8000/api/incidents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  --data-binary '{"service":"payments","severity":"high","title":"pg timeout","message":"postgres timeout for user test@example.com","source":"api","reporter":"oncall","tags":["db","timeout"]}' | jq
```

### Search

```bash
curl -s "http://localhost:8000/api/search?q=postgres%20timeout&top_k=5" \
  -H "X-API-Key: $KEY" | jq
```

**Note:** If you pass `q=` with an empty string, FastAPI returns 422 because `q` has `min_length=1`.

### Read an Incident

```bash
curl -s "http://localhost:8000/api/incidents/1?include_deleted=false" \
  -H "X-API-Key: $KEY" | jq
```

### Audit Logs

```bash
curl -s "http://localhost:8000/api/audit-logs?limit=50" \
  -H "X-API-Key: $KEY" | jq
```

## RBAC Rules (Summary)

- **viewer**: can read incidents, can search, cannot view raw
- **responder**: can create, update, read raw
- **auditor**: can list audit logs, can read with `include_deleted=true`
- **admin**: can do everything, can mint API keys in-tenant

**Tenant isolation** is enforced by deriving `tenant_id` from the API key context. A valid key for `demo` must not read `acme` incidents, even if the incident ID exists.

## Health and Readiness

### `GET /health`
Returns `{"status":"ok"}` if the service is up.

### `GET /ready`
Checks DB connectivity and returns 200 only when dependencies are reachable.

Use `/ready` for container orchestration readiness probes.

## Testing

```bash
pytest -q
```

## Project Structure

- `app/main.py` - FastAPI app factory, docs, UI mount, health endpoints
- `app/api/routes.py` - HTTP routes
- `app/models/` - SQLAlchemy models
- `app/crud/` - DB operations and search
- `app/security/` - Redaction logic
- `app/llm/` - Embedding implementations
- `app/scripts/` - Bootstrap and seed scripts
- `alembic/` - Migrations

## Troubleshooting

### Port 5432 Already Allocated

You have another Postgres container bound to the host port.

Check:

```bash
docker ps --format "table {{.Names}}\t{{.Ports}}" | grep 5432 || true
```

**Fix:** Stop the conflicting container or change the compose port mapping.

### 401 Invalid or Missing API Key

The key is not present in `api_keys`, or you are hitting a fresh DB without bootstrapping.

**Fix:**

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/incident_intel"
python -m app.scripts.bootstrap_demo_keys
```

### 404 Incident Not Found When Reading Cross-Tenant

Expected behavior. Incidents are tenant-scoped.

### Search 500 with pgvector ValueError

This usually means the query embedding being passed into the pgvector operator is not a flat list of floats with the correct dimension. Validate that your embedding function returns exactly one vector, not a tuple or nested list, and that `VECTOR_DIM` matches what is stored.
```


