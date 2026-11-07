.PHONY: dev test unit tenancy eval lint fix migrate

dev:
	uvicorn app.main:app --reload

test:
	pytest

unit:
	pytest tests/unit

tenancy:
	pytest tests/tenancy

eval:
	pytest tests/eval

lint:
	ruff check app tests && mypy app

fix:
	ruff check app tests --fix

migrate:
	alembic upgrade head
