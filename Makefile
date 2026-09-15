.PHONY: up down test lint config-hash dev-reset env

env:
	@test ! -e .env || (echo ".env already exists; refusing to overwrite" && exit 1)
	@cp .env.example .env
	@chmod 600 .env
	@echo "Created .env"

up:
	docker compose up -d

down:
	docker compose down

test:
	python3 -m uv run pytest

lint:
	python3 -m uv run ruff check .

config-hash:
	python3 -m uv run python -c "from vsf.config import load_config; from vsf.config.hashing import config_hash; import sys; c=load_config(sys.argv[1]); print(config_hash(c.model_dump(mode='json')))" $(CONFIG)

dev-reset:
	@test "$(CONFIRM_DEV_RESET)" = "yes" || (echo "Set CONFIRM_DEV_RESET=yes to reset development volumes" && exit 1)
	@test ! -d runs/release || (echo "Refusing reset: release manifests exist" && exit 1)
	docker compose down -v
