.PHONY: build up down restart logs test test-unit test-integration clean

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

db-logs:
	docker compose logs -f postgres

# Run sync once
run:
	docker compose up orchestrator

# Run tests
test:
	cd digits && poetry run pytest tests/ -v

test-unit:
	cd digits && poetry run pytest tests/ -v --ignore=tests/test_integration.py

test-integration:
	cd digits && poetry run pytest tests/test_integration.py -v

# Clean up
clean:
	docker compose down -v
	docker system prune -f

# Full rebuild and run
all: clean build up run
