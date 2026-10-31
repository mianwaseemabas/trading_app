#!/usr/bin/env bash
# Dump the PostgreSQL database to backups/ (gzip). Keeps 14 days.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
set -a; . ./.env; set +a
mkdir -p backups && chmod 700 backups
f="backups/trading-$(date -u +%Y%m%d-%H%M).sql.gz"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$f"
chmod 600 "$f"
find backups -name 'trading-*.sql.gz' -mtime +14 -delete
echo "$(date -u) backup written: $f ($(du -h "$f" | cut -f1))"
# Tell the app (health monitor) and, if switched on in System → Backups, send an encrypted copy to Telegram.
docker compose exec -T worker python -m app.backup_tool record-local --name "$(basename "$f")" --bytes "$(stat -c%s "$f")" || true
if [ "${1:-}" != "--no-offsite" ]; then
  docker compose exec -T worker python -m app.backup_tool send --name "$(basename "$f")" < "$f" || true
fi
