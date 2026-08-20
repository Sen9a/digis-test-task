# Digits Test Task — Invoice-to-Accounting Sync

Integration service that synchronizes invoices from source invoicing systems to target accounting systems.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   ORCHESTRATOR                           │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Sync Engine │─▶│ Normalizer   │─▶│ State Store   │  │
│  │             │  │ (in-process) │  │ (in-memory)   │  │
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
digits-test-task/
├── docker-compose.yml          # All services
│
├── digits/                     # Main sync service
│   ├── pyproject.toml          # Poetry config
│   ├── Dockerfile              # Orchestrator container
│   ├── main.py                 # CLI entry point
│   │
│   ├── src/
│   │   ├── abstract/           # SourceConnector, TargetConnector ABCs
│   │   ├── clients/            # APIClient (thin HTTP wrapper)
│   │   ├── connectors/         # SourceAPIConnector, TargetAPIConnector
│   │   ├── models/             # Pydantic models (UnifiedInvoice, SyncState, etc.)
│   │   ├── services/           # APIService ABC, AiohttpAPIService, FakeAPIService
│   │   ├── sync/               # SyncEngine, StateStore
│   │   └── const.py            # ErrorCategory enum
│   │
│   └── tests/                  # 35 tests (unit + integration)
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

# Run tests
make test              # all tests
make test-unit         # unit tests only (no Docker needed)
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
cd digits

# Install dependencies
poetry install --extras dev

# Unit tests (no Docker needed)
poetry run pytest tests/ -v --ignore=tests/test_integration.py

# Integration tests (requires Docker services running)
docker compose up -d fake-source-api fake-target-api
poetry run pytest tests/test_integration.py -v

# All tests
poetry run pytest tests/ -v
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
| `BATCH_SIZE` | `10` | Invoices per fetch batch |

## Decision Record

### Assumptions

1. **Single-node execution** — No distributed locking needed for this exercise. Production would need Redis/etcd for multi-node coordination.
2. **In-memory state store** — Sufficient for demonstrating sync logic. Production would use PostgreSQL.
3. **Full-scan + hash** — Source API doesn't support `updated_at` filtering, so we fetch all invoices and use content hash for change detection.
4. **Synchronous processing** — Invoices processed sequentially. Production would use async queue with per-tenant ordering.

### Trade-offs

| Decision | Trade-off | Rationale |
|----------|-----------|-----------|
| Connectors as libraries, not microservices | Less isolation vs. simpler deployment | 8-hour constraint; connectors are dumb pipes, platform owns orchestration |
| In-memory state store | No persistence vs. simplicity | Exercise scope; PostgreSQL schema designed but not implemented |
| Content hash for change detection | Computation cost vs. reliability | Clocks lie (clock skew); hashes don't. Prevents unnecessary API calls. |
| Session-per-request HTTP | Connection overhead vs. lifecycle simplicity | No connection pool management needed; acceptable for exercise scale |

### Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| Microservices for each connector | Over-engineering for 8-hour exercise; adds deployment complexity without demonstrating core competencies |
| Separate normalizer service | Normalization is a pure function; no I/O, no benefit from isolation |
| Celery/Redis for async processing | Adds infrastructure complexity; synchronous processing sufficient to demonstrate idempotency and error handling |
| PostgreSQL state store | Designed (see schema below) but in-memory sufficient for exercise |

### Known Limitations

1. **No persistence** — State store is in-memory; restart loses all sync state
2. **No true incremental sync** — Fetches all invoices every run; relies on content hash to skip unchanged
3. **No reconciliation report** — Can trace individual invoices but no automated source-vs-target comparison
4. **No distributed locking** — Single-node only
5. **No schema versioning** — Connector changes require deployment
6. **Replay is basic** — `replay_failed()` exists but doesn't re-fetch from source

### Next Production Improvements

1. **PostgreSQL state store** — Schema designed, see below
2. **Queue-based async processing** — SQS/RabbitMQ with per-tenant FIFO ordering
3. **Distributed rate limiting** — Redis token bucket per tenant + global
4. **Reconciliation engine** — Automated source-vs-target comparison with discrepancy reports
5. **Dead letter queue** — For permanent failures with operator UI
6. **Circuit breakers** — For failing connectors
7. **Webhook notifications** — For sync completion/failure
8. **Metrics and alerting** — Prometheus metrics, PagerDuty alerts

### Production State Store Schema (Designed, Not Implemented)

```sql
CREATE TABLE sync_states (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    source_connector VARCHAR(50) NOT NULL,
    source_record_id VARCHAR(255) NOT NULL,
    target_connector VARCHAR(50) NOT NULL,
    target_record_id VARCHAR(255),
    content_hash VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL,
    attempt_count INT DEFAULT 0,
    last_attempt_at TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(tenant_id, source_connector, source_record_id)
);

CREATE TABLE sync_runs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    source_connector VARCHAR(50) NOT NULL,
    target_connector VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    cursor_position VARCHAR(255),
    records_processed INT DEFAULT 0,
    records_succeeded INT DEFAULT 0,
    records_failed INT DEFAULT 0,
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
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
