#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="deploy/.env"
COMPOSE_FILE="deploy/docker-compose.prod.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE"
  exit 1
fi

IMAGE_TAG="${1:-${IMAGE_TAG:-latest}}"
export IMAGE_TAG

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull api worker caddy

echo "Applying Alembic migrations..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm --no-deps api alembic upgrade head

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
