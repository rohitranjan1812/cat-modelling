.PHONY: setup dev api web test lint schemas demo up down

setup:            ## install backend + web dependencies
	cd backend && uv sync
	cd web && npm ci

api:              ## run API with reload on :8000
	cd backend && uv run catmod serve --reload

web:              ## run web dev server on :5173 (proxies /api to :8000)
	cd web && npm run dev

test:             ## backend tests
	cd backend && uv run pytest

lint:             ## all static checks
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy src
	cd web && npm run typecheck

schemas:          ## regenerate contracts and web types
	cd backend && uv run catmod export-schemas --out ../schemas
	cd web && npm run gen:api

demo:             ## write a 50k-location OED demo portfolio to data/demo
	cd backend && uv run catmod generate --preset us-cat-demo --out ../data/demo

up:               ## full stack in Docker on :8080
	docker compose up --build -d

down:
	docker compose down
