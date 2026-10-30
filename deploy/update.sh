#!/usr/bin/env bash
# Pull the latest code from GitHub and rebuild/restart the stack. Data is preserved.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
BRANCH="${BRANCH:-main}"
git fetch --quiet origin "$BRANCH"
git pull --ff-only --quiet origin "$BRANCH"
echo "Code at $(git rev-parse --short HEAD)"
set -a; . ./.env; set +a
profile=()
[[ ${PROXY_MODE:-} == caddy ]] && profile=(--profile caddy)
./deploy/backup.sh || echo "warning: pre-update backup failed"
docker compose "${profile[@]}" up -d --build --remove-orphans
docker image prune -f >/dev/null
for i in $(seq 1 40); do
  curl -fsS "http://127.0.0.1:${WEB_PORT:-8090}/api/health" >/dev/null 2>&1 && { echo "Healthy."; exit 0; }
  sleep 3
done
echo "App not healthy yet — check: docker compose logs --tail=100 backend worker" >&2
exit 1
