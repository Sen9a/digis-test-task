.PHONY: build up down restart logs test test-unit test-integration clean replay

# Build all services
build:
	docker compose build

# Build individual services
build-source:
	docker compose build fake-source-api

build-target:
	docker compose build fake-target-api

build-orchestrator:
	docker compose build orchestrator

# Start all services
up:
	docker compose up -d

# Start individual services
up-source:
	docker compose up -d fake-source-api

up-target:
	docker compose up -d fake-target-api

up-orchestrator:
	docker compose up -d orchestrator

# Stop all services
down:
	docker compose down

# Restart all services
restart:
	docker compose restart

# Restart individual services
restart-source:
	docker compose restart fake-source-api

restart-target:
	docker compose restart fake-target-api

restart-orchestrator:
	docker compose restart orchestrator

# View logs
logs:
	docker compose logs -f

logs-source:
	docker compose logs -f fake-source-api

logs-target:
	docker compose logs -f fake-target-api

logs-orchestrator:
	docker compose logs -f orchestrator

# Database
db:
	docker compose up -d postgres

migrate:
	cd digis && poetry run alembic upgrade head

migrate-new:
	cd digis && poetry run alembic revision --autogenerate -m "$(msg)"

migrate-history:
	cd digis && poetry run alembic history

db-logs:
	docker compose logs -f postgres

# Run sync once
run:
	docker compose up orchestrator

# Replay failed records once
replay:
	docker compose run --rm orchestrator python -m main --replay

# Run tests (full suite includes integration tests → fresh fake target first)
test: restart-target
	cd digis && poetry run pytest tests/ -v

test-unit:
	cd digis && poetry run pytest tests/ -v --ignore=tests/test_integration.py

# Integration tests need a fresh fake target (in-memory invoices + rate-limit
# counter), otherwise exports from a previous sync run fail as duplicates
test-integration: restart-target
	cd digis && poetry run pytest tests/test_integration.py -v

# Clean up
clean:
	docker compose down -v
	docker system prune -f

# Full rebuild and run
all: clean build up migrate run
