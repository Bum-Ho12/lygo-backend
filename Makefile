.PHONY: dev test tenancy eval lint migrate

dev:
	uvicorn app.main:app --reload

test:
	pytest

tenancy:
	pytest tests/tenancy

eval:
	pytest tests/eval

lint:
	ruff check app tests && mypy app

migrate:
	alembic upgrade head
