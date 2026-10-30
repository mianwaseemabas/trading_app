#!/usr/bin/env bash
# Restore a backup: sudo deploy/restore.sh backups/trading-YYYYmmdd-HHMM.sql.gz
# WARNING: replaces the current database. The engine is stopped during restore.
set -euo pipefail
[[ $# -eq 1 && -f $1 ]] || { echo "usage: $0 <backup.sql.gz>"; exit 1; }
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
set -a; . ./.env; set +a
read -r -p "Replace the current database with $1? Type YES: " ok
[[ $ok == YES ]] || exit 1
docker compose stop worker backend
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
gunzip -c "$1" | docker compose exec -T db psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"
docker compose start backend worker
echo "Restored."
