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

echo "Disk usage before server cleanup:"
df -h
docker system df || true

# If disk space is critically low, do an aggressive cleanup first.
available_gb="$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')"
if [[ -n "${available_gb}" && "${available_gb}" -lt 8 ]]; then
  echo "Low free disk (${available_gb}G). Running aggressive Docker cleanup..."
  docker container prune -f || true
  docker image prune -af || true
  docker builder prune -af || true
fi

# Keep deploy host healthy: remove stale objects older than a week.
# This is safe for running containers and avoids no-space failures on image extract.
docker container prune -f || true
docker image prune -af --filter "until=168h" || true
docker builder prune -af --filter "until=168h" || true

echo "Disk usage after server cleanup:"
df -h

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" pull api worker caddy

echo "Applying Alembic migrations..."
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" run --rm --no-deps api alembic upgrade head

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps

echo "Disk usage after deploy:"
df -h
docker system df || true
