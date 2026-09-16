.PHONY: help up down infra schema migrate seed api worker web test typecheck lint

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up:  ## Start the whole stack in Docker
	docker compose up -d

down:  ## Stop everything
	docker compose down

infra:  ## Start only Postgres, Redis and EMQX
	docker compose up -d db redis emqx

schema:  ## Create the schema directly (fast path; production uses migrate)
	cd backend && python scripts/create_schema.py --drop

migrate:  ## Apply Alembic migrations
	cd backend && alembic upgrade head

seed:  ## Seed the demo plant with six weeks of history
	cd backend && python scripts/seed.py

api:  ## Run the API with reload
	cd backend && uvicorn app.main:app --reload --port 8080

worker:  ## Run the background worker
	cd backend && python -m app.workers.runner

web:  ## Run the frontend
	cd frontend && npm run dev

test:  ## Run the backend test suite
	cd backend && pytest -q

typecheck:  ## Typecheck the frontend
	cd frontend && npx tsc --noEmit

lint:  ## Lint the backend
	cd backend && ruff check app tests scripts
