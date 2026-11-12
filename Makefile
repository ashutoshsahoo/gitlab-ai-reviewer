.PHONY: help pull-model up bootstrap demo down logs check test reset runner

help:
	@echo "make pull-model  - pull the Ollama model on the host"
	@echo "make up          - start GitLab, Postgres, Redis, agent"
	@echo "make bootstrap   - create tokens + bot user (run once GitLab is up)"
	@echo "make demo        - create demo project + merge request"
	@echo "make check       - verify GitLab / Ollama / Redis connectivity"
	@echo "make logs        - follow agent logs"
	@echo "make test        - run unit tests locally"
	@echo "make runner      - start + register an optional GitLab Runner"
	@echo "make reset       - DANGER: stop and delete all volumes"

pull-model:
	ollama pull $${OLLAMA_MODEL:-qwen2.5-coder:14b}

up:
	@[ -f .env ] || cp .env.example .env
	docker compose up -d --build

bootstrap:
	./scripts/bootstrap.sh

demo:
	docker compose exec agent-api python -m app.cli demo

check:
	docker compose exec agent-api python -m app.cli check

logs:
	docker compose logs -f agent-api agent-worker

test:
	cd agent && pip install -q -r requirements.txt -r requirements-dev.txt && python -m pytest -q

runner:
	./scripts/register-runner.sh

down:
	docker compose down

reset:
	docker compose --profile ci down -v
