.PHONY: up down infra langfuse api web test golden ingest

up:            ## build and run the whole stack on http://localhost:8080
	docker compose up --build -d

down:
	docker compose down

infra:         ## only postgres + qdrant, for local development
	docker run -d --name portdues-pg -e POSTGRES_USER=portdues -e POSTGRES_PASSWORD=portdues -e POSTGRES_DB=portdues -p 5434:5432 postgres:16-alpine
	docker run -d --name portdues-qdrant -p 6333:6333 qdrant/qdrant:v1.19.2

langfuse:      ## only the Langfuse stack, on http://localhost:3000 (admin@portdues.local / portdues-admin)
	docker compose up -d langfuse-web langfuse-worker

api:
	cd backend && .venv/bin/uvicorn portdues.api.app:app --reload --port 8000

web:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/python -m pytest -q

golden:        ## re-phrase the golden questions (labels come from scripts/golden/za_reference.py)
	cd backend && .venv/bin/python scripts/golden/build_golden.py

ingest:
	cd backend && .venv/bin/portdues ingest "../Port Tariff.pdf"
