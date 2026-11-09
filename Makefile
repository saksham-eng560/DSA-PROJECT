# AutoApply AI — common tasks.  `make help` lists them.
PY ?= backend/.venv/bin/python
SHELL := /bin/bash

.PHONY: start help setup setup-backend setup-frontend dev api worker beat web demo seed migrate test test-pg e2e lint format build up down logs prod-up

start:  ## One command: install what's missing and run everything (see ./start.sh --help)
	./start.sh

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: setup-backend setup-frontend  ## Install backend + frontend dependencies
	@test -f .env || cp .env.example .env

setup-backend:  ## Create backend virtualenv, install deps and Chromium
	cd backend && python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements-dev.txt
	cd backend && .venv/bin/python -m playwright install chromium

setup-frontend:  ## Install dashboard dependencies
	cd frontend && npm ci

migrate:  ## Apply database migrations
	$(PY) scripts/migrate.py

api:  ## Run the API (reload)
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

worker:  ## Run a Celery worker
	cd backend && .venv/bin/celery -A app.worker.celery_app worker -Q default,browser --loglevel INFO

beat:  ## Run Celery beat (scheduled scans, e-mail polling, reminders)
	cd backend && .venv/bin/celery -A app.worker.celery_app beat --loglevel INFO

web:  ## Run the dashboard (dev)
	cd frontend && npm run dev

demo:  ## Serve the local demo careers site on :8765 (safe end-to-end trials)
	$(PY) scripts/demo_site.py

seed:  ## Create a demo account with sample data
	$(PY) scripts/seed_db.py

test:  ## Backend tests (SQLite)
	cd backend && .venv/bin/python -m pytest

test-pg:  ## Backend tests against PostgreSQL (TEST_DATABASE_URL)
	cd backend && TEST_DATABASE_URL=$${TEST_DATABASE_URL:-postgresql+psycopg://autoapply:autoapply@localhost:5432/autoapply_test} .venv/bin/python -m pytest

e2e:  ## Browser end-to-end test only
	cd backend && .venv/bin/python -m pytest -m e2e

lint:  ## Lint backend, scripts and dashboard
	cd backend && .venv/bin/ruff check app tests alembic/env.py && .venv/bin/ruff check ../scripts
	cd frontend && npm run lint && npm run typecheck

build:  ## Production build of the dashboard
	cd frontend && npm run build

up:  ## Start the full stack with Docker Compose
	docker compose up --build -d

down:  ## Stop the Docker Compose stack
	docker compose down

logs:  ## Tail Docker Compose logs
	docker compose logs -f --tail=100

prod-up:  ## Production stack with HTTPS (needs DOMAIN, SECRET_KEY, ENCRYPTION_KEY, POSTGRES_PASSWORD in .env)
	docker compose -f docker-compose.prod.yml up --build -d
