.PHONY: setup dev test unit tenancy eval lint fix migrate db-up
# All commands run inside the uv-managed environment (Python pinned in .python-version).
RUN = uv run

setup:
	uv sync

dev:
	$(RUN) uvicorn app.main:app --reload

test:
	$(RUN) pytest

unit:
	$(RUN) pytest tests/unit

tenancy:
	REQUIRE_DB=1 $(RUN) pytest tests/tenancy tests/integration

eval:
	$(RUN) pytest tests/eval

lint:
	$(RUN) ruff check app tests && $(RUN) mypy app

fix:
	$(RUN) ruff check app tests --fix

migrate:
	$(RUN) alembic upgrade head   # uses DATABASE_MIGRATOR_URL (owner role)

db-up:
	docker compose up -d db redis minio
