# Digis Test Task — Invoice-to-Accounting Sync

Integration service that synchronizes invoices from source invoicing systems to target accounting systems.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   ORCHESTRATOR                           │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Sync Engine │─▶│ Normalizer   │─▶│ State Store   │  │
│  │             │  │ (in-process) │  │ (PostgreSQL)  │  │
│  └─────────────┘  └──────────────┘  └───────────────┘  │
│         │                                               │
│    ┌────┴────┐                                          │
│    ▼        ▼                                          │
│ Source    Target                                        │
│ Connector Connector                                     │
└────┬────────┬───────────────────────────────────────────┘
     │        │
     ▼        ▼
┌─────────┐ ┌─────────┐
│  Fake   │ │  Fake   │
│ Source  │ │ Target  │
│  API    │ │  API    │
│(FastAPI)│ │(FastAPI)│
└─────────┘ └─────────┘
```

## Project Structure

```
digis-test-task/
├── docker-compose.yml          # All services
│
├── digis/                      # Main sync service
│   ├── pyproject.toml          # Poetry config
│   ├── Dockerfile              # Orchestrator container
│   ├── main.py                 # CLI entry point
│   │
│   ├── src/
│   │   ├── abstract/           # SourceConnector, TargetConnector ABCs
│   │   ├── clients/            # APIClient (thin HTTP wrapper)
│   │   ├── connectors/         # SourceAPIConnector, TargetAPIConnector
│   │   ├── models/             # Pydantic models (UnifiedInvoice, SyncState, etc.)
│   │   ├── services/           # APIService ABC, AiohttpAPIService, FakeAPIService,
│   │   │                       # SyncStateService, SyncRunService
│   │   ├── managers/           # Persistence layer (async ORM sessions via get_db_session)
│   │   ├── tables/             # SQLAlchemy ORM table declarations (DeclarativeBase)
│   │   ├── db/                 # Async engine, session factory, get_db_session, init_db
│   │   ├── sync/               # SyncEngine
│   │   ├── utils.py            # Retry-After-aware tenacity wait strategy
│   │   └── const.py            # ErrorCategory, Status enums
│   │
│   ├── migrations/             # Alembic migrations (sync_states, sync_runs)
│   │
│   └── tests/                  # 44 tests (unit + integration); conftest.py redirects
│                               # them to an auto-created "<db>_test" database
│
└── fake_apis/
    ├── source_api/             # Fake invoicing system (port 8001)
    └── target_api/             # Fake accounting system (port 8002)
```

## Running

### Makefile (recommended)

```bash
# Build all services
make build

# Build individual services
make build-source
make build-target
make build-orchestrator

# Start all services
make up

# Run one sync
make run

# Replay failed records once
make replay

# Run tests
make db               # tests need PostgreSQL (sync engine tests use the state store)
make migrate          # apply migrations (or let tests create tables via init_db)
make test              # all tests
make test-unit         # unit tests only (needs PostgreSQL, not the fake APIs)
make test-integration  # integration tests (requires Docker)

# Logs
make logs              # all services
make logs-orchestrator # orchestrator only

# Stop / restart / clean
make down
make restart
make clean             # stop + remove volumes + prune

# Full rebuild and run
make all
```

### Docker Compose (manual)

```bash
# Start all services and run one sync
docker compose up --build

# View sync results
docker logs orchestrator

# Restart and re-run (clears fake API state)
docker compose restart
docker compose up orchestrator
```

### Tests (manual)

```bash
cd digis

# Install dependencies
poetry install --extras dev

# Unit tests (needs PostgreSQL running for sync engine tests)
docker compose up -d postgres
poetry run pytest tests/ -v --ignore=tests/test_integration.py

# Integration tests (requires Docker services running)
docker compose up -d fake-source-api fake-target-api
poetry run pytest tests/test_integration.py -v

# All tests
poetry run pytest tests/ -v
```

**Test database isolation:** `tests/conftest.py` redirects `DATABASE_URL` to a dedicated
`<db>_test` database (auto-created on first run) before any app module is imported,
so test runs never touch application state — the fixtures call `manager.clear()`,
which deletes all rows.

**Integration tests and rate limiting:** the fake target in `docker-compose.yml`
currently runs with `RATE_LIMIT_AFTER=15` to demonstrate retry/backoff behavior.
Integration tests sync all 25 invoices and expect zero failures, so set it back to
`0` and recreate the container (`docker compose up -d fake-target-api`) before
running `make test-integration`. Unit tests are unaffected (they use `FakeAPIService`).

### Rate-Limit Demo

With `RATE_LIMIT_AFTER=15` on the fake target, a sync run demonstrates the retry
behavior live: after 15 requests the target returns `429` with `retry_after` in
the body, and the engine retries with the requested delay (tenacity +
`wait_retry_after_aware`), then marks records `failed` after `MAX_RETRIES + 1`
attempts:

```bash
make restart-target      # reset rate-limit counter and in-memory invoices
cd digis && poetry run python -m main
```

To recover the failed records, reset the target and replay them — only FAILED
states are reprocessed, each re-fetched from the source:

```bash
make restart-target
make replay              # or: cd digis && poetry run python -m main --replay
```

### Configuration

Environment variables for the orchestrator:

| Variable | Default | Description |
|----------|---------|-------------|
| `SOURCE_API_URL` | `http://localhost:8001` | Source API base URL |
| `TARGET_API_URL` | `http://localhost:8002` | Target API base URL |
| `TENANT_ID` | `demo-tenant` | Tenant identifier |
| `SOURCE_API_KEY` | `test-key` | Source API key |
| `TARGET_API_KEY` | `test-key` | Target API key |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5433/digis` | State store connection |
| `BATCH_SIZE` | `10` | Invoices per fetch batch |
| `MAX_RETRIES` | `3` | Export retry attempts (honors `Retry-After`) |
| `RETRY_BASE_DELAY` | `1.0` | Exponential backoff base delay (seconds) |

## Decision Record

### Assumptions

1. **Single-node execution** — No distributed locking needed for this exercise. Production would need Redis/etcd for multi-node coordination.
2. **One tenant per process run** — The engine is scoped to a single `tenant_id`; multi-tenant execution means a scheduler fanning out runs per tenant (see "Next Production Improvements"). All state is keyed by `tenant_id` and the DB enforces uniqueness per `(tenant_id, source_connector, source_record_id)`, so a record can never leak or collide across tenants.
3. **Full-scan + hash** — Source API doesn't support `updated_at` filtering, so we fetch all invoices and use content hash for change detection.
4. **Synchronous processing** — Invoices processed sequentially. Production would use async queue with per-tenant ordering.
5. **Demo credentials via env vars** — `SOURCE_API_KEY`/`TARGET_API_KEY` are shared defaults for the exercise. Production would store per-tenant credentials in a secrets manager (Vault, AWS Secrets Manager), fetched at run time and never logged; connector instances hold tokens only in memory for the duration of a run.

### Trade-offs

| Decision | Trade-off | Rationale |
|----------|-----------|-----------|
| Connectors as libraries, not microservices | Less isolation vs. simpler deployment | Connectors are dumb pipes, platform owns orchestration |
| PostgreSQL state store | Extra infrastructure vs. durability and restart-safe resume | Sync state must survive restarts for replay/recovery to be meaningful; unique constraint also gives a hard guarantee against double export |
| Content hash for change detection | Computation cost vs. reliability | Clocks lie (clock skew); hashes don't. Prevents unnecessary API calls. |
| Session-per-request HTTP | Connection overhead vs. lifecycle simplicity | No connection pool management needed; acceptable for exercise scale |

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| Microservices for each connector | Over-engineering for this scope; adds deployment complexity without demonstrating core competencies |
| Separate normalizer service | Normalization is a pure function; no I/O, no benefit from isolation |
| Celery/Redis for async processing | Adds infrastructure complexity; synchronous processing sufficient to demonstrate idempotency and error handling |
| In-memory state store | Cannot survive restarts; replay and idempotency guarantees become meaningless across deployments |

### Known Limitations

1. **No true incremental sync** — Fetches all invoices every run; relies on content hash to skip unchanged
2. **No reconciliation report** — Can trace individual invoices (sync_states maps source ↔ target record IDs) but no automated source-vs-target comparison
3. **No distributed locking** — Single-node only
4. **No schema versioning** — Connector changes require deployment
5. **Replay is tenant-scoped** — `replay_failed()` reprocesses all FAILED states for the tenant and its connector pair (states don't record which run they belonged to)
6. **Source-side fetches are not retried** — Retry with backoff (honoring `Retry-After`) applies to target exports; a rate-limited or unavailable source aborts the run, which can simply be re-run safely

### Next Production Improvements

1. **Tenant scheduler** — Control plane that fans out sync runs across thousands of tenants (cron + queue), with per-tenant concurrency limits
2. **Queue-based async processing** — SQS/RabbitMQ with per-tenant FIFO ordering
3. **Distributed rate limiting** — Redis token bucket per tenant + global
4. **Reconciliation engine** — Automated source-vs-target comparison with discrepancy reports
5. **Dead letter queue** — For permanent failures with operator UI
6. **Circuit breakers** — For failing connectors
7. **Webhook notifications** — For sync completion/failure
8. **Metrics and alerting** — Prometheus metrics, PagerDuty alerts
9. **Per-tenant secret storage** — Vault/AWS Secrets Manager with scoped access and rotation
10. **Scheduled replay with attempt cap** — Cron job running `replay_failed()` for
    FAILED states where `attempt_count < N`, moving exhausted records to a terminal
    dead-letter state; replay is already idempotency-safe (idempotency keys are
    honored by the target) and each replay writes its own `sync_runs` row

### Multi-Source / Multi-Target Design (Planned)

The current engine is deliberately single-pair. Scaling to N sources and M targets:

1. **Config: one pair → list of pairs.** Replace the single `SOURCE_API_URL` /
   `TARGET_API_URL` settings with a list of connection configs, each with a stable
   `name` (e.g. `{name, source: {type, url, api_key}, targets: [...]}`). The name
   is the identity key in `sync_states`, so renaming a connector orphans its state.
2. **Connector factory.** A registry mapping `type` → connector class; each entry
   builds its own `APIClient`/`APIService`. New connectors implement the existing
   ABCs in `src/abstract/`.
3. **Orchestration: keep `SyncEngine` single-pair.** An orchestrator builds one
   engine per (source, target) pair and runs them with `asyncio.gather`, with a
   per-target semaphore to respect per-target rate limits. Each pair gets its own
   `sync_runs` row, and failure isolation comes for free — target B being down
   does not block source → target A.
4. **Schema change only for fan-out.** Multi-source → one target needs no schema
   change. But one source record exported to two targets does: the unique
   constraint must become `(tenant_id, source_connector, source_record_id,
   target_connector)` — otherwise the second target's upsert overwrites the first
   target's state. Requires an Alembic migration plus threading `target_connector`
   through `get_state()` / `save_state()` / `replay_failed()`.
5. **`replay_failed` per pair.** Optional source/target filters so a scheduled
   replay job reprocesses one failing pipe instead of the whole tenant.

Suggested order: steps 1–3 first (no schema changes), step 4 when a second target
actually exists.

### State Store Schema (Implemented)

The following tables are created by the Alembic migration in `digis/migrations/`:

```sql
CREATE TABLE sync_states (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(255) NOT NULL,
    source_connector VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(255) NOT NULL,
    target_connector VARCHAR(50) NOT NULL,
    target_record_id VARCHAR(255),
    content_hash VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL,
    attempt_count INT,
    last_attempt_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, source_connector, source_record_id)
);

CREATE TABLE sync_runs (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(255) NOT NULL,
    source_connector VARCHAR(50) NOT NULL,
    target_connector VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    cursor_position VARCHAR(255),
    records_processed INT,
    records_succeeded INT,
    records_failed INT,
    records_skipped INT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
```

## AI and Tool Usage

| Tool | Usage | Outcome |
|------|-------|---------|
| Claude (AI) | Architecture review, edge case identification | Challenged initial assumption about relying solely on target idempotency keys; added state store layer |
| Claude (AI) | Error classification taxonomy | Refined from binary retry/fail to four categories (retryable, permanent, conflict, auth) |
| Claude (AI) | Connector interface design | Designed ABC with capability flags (supports_update, requires_reversal, etc.) |
| Claude (AI) | Test generation | Generated test cases for pagination, idempotency, rate limiting, tenant isolation |
| Manual | Data model design | AI suggestions for JSONB columns simplified initial design; kept for flexibility |
| Manual | Docker Compose setup | Standard patterns, no AI needed |

**Verification approach:**
- Traced through failure scenarios manually (partial failure, target downtime, duplicate webhook)
- Reviewed idempotency design against Stripe, Shopify API patterns
- Validated multi-tenancy isolation with threat model (cross-tenant data leakage)
- All AI-generated code reviewed, tested, and understood before inclusion
