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
  Client["Client (curl or UI)"] -->|"X-API-Key"| API["FastAPI"]

  API --> Auth["API key auth (RBAC)"]
  Auth -->|"tenant_id, role"| Handlers["Route handlers"]

  Handlers --> Redact["Redaction"]
  Redact --> Store[("Postgres: incident_logs")]

  Handlers --> Embed["Embeddings"]
  Embed -->|"local deterministic"| Local["Local embedder"]
  Embed -->|"optional external"| Provider["External provider"]
  Local --> Store
  Provider --> Store

  Handlers --> Search["pgvector similarity query"]
  Search --> Store

  Handlers --> Audit["Audit append (per-tenant hash chain)"]
  Audit --> AuditStore[("Postgres: audit_logs")]

  API --> Health["Health endpoints"]
  Health --> Ready["GET /health and GET /ready"]
  Ready --> DBCheck["DB connectivity check"]
  DBCheck --> Store

