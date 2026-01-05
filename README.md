# Incident Intelligence

Incident Intelligence is a small, production-minded incident log service focused on:

1. Safe storage of incident text with redaction
2. Search that still works when external embedding providers are unavailable
3. Auditability via a tamper-evident, per-tenant audit chain

It exposes a FastAPI REST API, stores data in Postgres, and uses pgvector for vector similarity search.

## Why this exists

Many incident tools either store raw JSON blobs with weak governance, or they add "AI search" in a way that stops working when an embedding provider rate limits or billing is not configured. This project is built to be reviewable from a clean local clone and to keep its core features working at $0 using deterministic local embeddings.

## Core features

- Multi-tenant incident storage with explicit fields (not JSON-only)
- Redaction pipeline for message text (emails, PANs, tokens, credentials)
- RBAC via API keys (viewer, responder, auditor, admin)
- Vector similarity search via pgvector
- Deterministic local embeddings mode for offline, zero-cost demo
- Audit log with a per-tenant hash chain (tamper-evident)
- Health and readiness endpoints (`/health`, `/ready`)
- Lightweight demo UI (`/ui`) and API docs (`/docs`, `/redoc`)

## Architecture

### Tenant isolation and RBAC

```mermaid
flowchart LR
    subgraph Tenants
        demoTenant[tenant: demo]
        acmeTenant[tenant: acme]
    end

    subgraph Roles
        viewerRole[viewer: read + search]
        responderRole[responder: create + read raw + update]
        auditorRole[auditor: audit logs + include deleted]
        adminRole[admin: full access + key management]
    end

    demoTenant --> viewerRole
    demoTenant --> responderRole
    demoTenant --> auditorRole
    demoTenant --> adminRole

    acmeTenant --> viewerRole
    acmeTenant --> responderRole
    acmeTenant --> auditorRole
    acmeTenant --> adminRole
```

### Request flow

```mermaid
sequenceDiagram
    participant C as Client
    participant API as FastAPI
    participant R as Redaction
    participant E as Embeddings
    participant DB as Postgres

    C->>API: POST /api/incidents (X-API-Key)
    API->>API: Validate key, derive tenant and role
    API->>R: Redact message
    R-->>API: message_redacted
    API->>E: Embed message (local or provider)
    E-->>API: vector
    API->>DB: INSERT incident_logs
    API->>DB: INSERT audit_logs (hash chained)
    API-->>C: 200 IncidentLogRead

    C->>API: GET /api/search?q=...
    API->>E: Embed query
    E-->>API: query_vector
    API->>DB: Vector similarity search scoped to tenant
    DB-->>API: top_k results
    API->>DB: INSERT audit_logs (hash chained)
    API-->>C: 200 [IncidentLogRead]
```

### Component overview

| Component | Purpose |
|-----------|---------|
| Multi-tenancy | All reads and writes are scoped by tenant_id derived from the API key |
| PII redaction | Emails, PANs, keys, JWT-like tokens, and similar secrets are stripped before storage |
| Dual storage | message_raw is privileged, message_redacted is returned by default |
| Vector search | pgvector similarity search on fixed-dimension embeddings |
| Audit chain | Per-tenant SHA-256 hash chain provides tamper evidence |
| RBAC | Roles gate privileged operations such as reading raw text and viewing audit logs |

## Local quickstart

### Prerequisites

- Python 3.12
- Docker Desktop with WSL integration enabled
- `docker compose` available in your shell
- `jq` (optional)

If port 5432 is already in use, stop the other container or change the host port in `docker-compose.yaml`.

### One command (recommended)

If you have `scripts/dev_up.sh`, use it as the primary reviewer path:

```bash
./scripts/dev_up.sh
```

**Expected outcome:**
- Postgres starts via Docker Compose
- Migrations are applied
- Demo API keys are bootstrapped (plaintext printed once)
- Demo incidents are seeded (optional, depending on your script)
- API starts on port 8000

**Open:**
- Home: http://localhost:8000/
- UI: http://localhost:8000/ui
- Swagger: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health: http://localhost:8000/health
- Readiness: http://localhost:8000/ready

### Manual setup (debug path)

Use this if you want to run each step explicitly or troubleshoot.

#### 1) Start Postgres

```bash
docker compose up -d db
docker compose ps
```

#### 2) Create database and enable pgvector

```bash
docker compose exec -T db createdb -U postgres incident_intel || true
docker compose exec -T db psql -U postgres -d incident_intel -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker compose exec -T db psql -U postgres -d incident_intel -c "\dx"
```

#### 3) Run migrations

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/incident_intel"
alembic upgrade head
alembic current
```

Verify tables:

```bash
docker compose exec -T db psql -U postgres -d incident_intel -c "\dt"
```

#### 4) Bootstrap demo API keys

This prints plaintext keys once. Store them locally.

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/incident_intel"
python -m app.scripts.bootstrap_demo_keys
```

**Expected tenants:**
- `demo` (admin, auditor, viewer)
- `acme` (admin, viewer)

#### 5) Seed demo incidents (recommended)

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/incident_intel"
python -m app.scripts.seed_demo_incidents --count 150
```

#### 6) Run the API

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/incident_intel"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Demo flow

### 1) Set an API key

```bash
export KEY="paste_key_here"
```

### 2) Create an incident

```bash
curl -s -X POST "http://localhost:8000/api/incidents" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  --data-binary '{"service":"payments","severity":"high","title":"pg timeout","message":"postgres timeout for user test@example.com","source":"api","reporter":"oncall","tags":["db","timeout"]}' | jq
```

### 3) Search

```bash
curl -s "http://localhost:8000/api/search?q=postgres%20timeout&top_k=5" \
  -H "X-API-Key: $KEY" | jq '.[].message_redacted'
```

**Note:** If you pass `q=` with an empty string, FastAPI returns 422 because `q` has `min_length=1`.

### 4) Read an incident

```bash
curl -s "http://localhost:8000/api/incidents/1?include_deleted=false" \
  -H "X-API-Key: $KEY" | jq
```

### 5) View audit logs

```bash
curl -s "http://localhost:8000/api/audit-logs?limit=50" \
  -H "X-API-Key: $KEY" | jq
```

### 6) Tenant isolation check (expected 404)

Use a demo key to attempt reading an acme incident ID:

```bash
export DEMO_KEY="paste_demo_key_here"
export ACME_ID="paste_acme_incident_id_here"

curl -i -s "http://localhost:8000/api/incidents/$ACME_ID?include_deleted=false" \
  -H "X-API-Key: $DEMO_KEY" | head -n 20
```

**Expected:** `404 Not Found` with `{"detail":"Incident not found"}`.

## Configuration

Create a local `.env` (not committed) based on `.env.example`.

### Environment variables

**Required:**
- `DATABASE_URL`

**Optional:**
- `EMBEDDINGS_MODE` (default should be local deterministic for demos)
- `OPENAI_API_KEY` (only required if you enable external embeddings)
- `VECTOR_DIM` (default 1536, must match the database column dimension)

### Embeddings behavior

The default demo posture is deterministic local embeddings so reviewers can run everything without external billing.

If external embeddings are enabled:
- Incident creation should not fail if the provider is unavailable
- Search should continue to work in local mode
- Embedding status is tracked per incident (pending, ready, failed) with error details recorded

## RBAC rules (summary)

- **viewer**: can read incidents and search, cannot read raw
- **responder**: can create, update, and read raw
- **auditor**: can read audit logs and include deleted records
- **admin**: full access, including key management within its tenant

**Tenant isolation** is enforced by deriving `tenant_id` from the API key context. A valid key for `demo` cannot read `acme` incidents even if the incident ID exists.

## Health and readiness

- `GET /health` returns `{"status":"ok"}` if the service is up
- `GET /ready` checks DB connectivity and returns 200 only when dependencies are reachable

## Testing

```bash
pytest -q
```

## Project structure

- `app/main.py` - App factory, docs, UI mount, health endpoints
- `app/api/routes.py` - HTTP routes
- `app/models/` - SQLAlchemy models
- `app/crud/` - DB operations and search
- `app/security/` - Redaction logic
- `app/llm/` - Embedding implementations
- `app/scripts/` - Bootstrap and seed scripts
- `alembic/` - Migrations

## Troubleshooting

### Port 5432 already allocated

You have another Postgres container bound to the host port.

Check:

```bash
docker ps --format "table {{.Names}}\t{{.Ports}}" | grep 5432 || true
```

**Fix:** Stop the conflicting container or change the compose port mapping.

### 401 invalid or missing API key

The key is not present in `api_keys`, or you are hitting a fresh DB without bootstrapping.

**Fix:**

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:postgres@localhost:5432/incident_intel"
python -m app.scripts.bootstrap_demo_keys
```

### Cross-tenant reads return 404

Expected behavior. Incidents are tenant-scoped.

### Search fails with pgvector value errors

This usually means the query embedding being passed into the pgvector operator is not a flat list of floats with the correct dimension. Confirm:
- The embedding function returns a single vector (not a tuple or nested list)
- `VECTOR_DIM` matches the stored embedding dimension
- Only incidents with `embedding_status=ready` are used for vector ranking
```

