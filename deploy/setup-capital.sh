#!/usr/bin/env bash
# Connect the app to a Capital.com account (REST API).
#   sudo /opt/trading_app/deploy/setup-capital.sh
# Needs: your Capital.com login email, an API key and the API key's custom password
# (Capital.com -> Settings -> API integrations -> Generate API key).
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
[[ -f .env ]] || { echo "Run deploy/install.sh first"; exit 1; }

setvar() {  # setvar KEY VALUE -> replace or append in .env, single-quoted so $ # spaces stay literal
  local k="$1" v="$2"
  [[ $v == *"'"* ]] && { echo "Values containing a single quote (') are not supported for $k"; exit 1; }
  local line="$k='$v'"
  local tmp; tmp="$(mktemp)"
  grep -v "^$k=" .env > "$tmp" || true
  printf '%s\n' "$line" >> "$tmp"
  cat "$tmp" > .env && rm -f "$tmp"
}
ask() {
  local __v
  if [[ ${3:-} == secret ]]; then read -r -s -p "$2: " __v; echo; else read -r -p "$2: " __v; fi
  __v="$(printf '%s' "$__v" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  [[ -n $__v ]] || { echo "Value required"; exit 1; }
  printf -v "$1" '%s' "$__v"
}

echo "Capital.com connection setup (secrets are not shown while typing)."
ask EMAIL "Capital.com login email"
ask KEY "API key" secret
ask PASS "API key password (the custom password you set for the key)" secret
read -r -p "Environment [demo/live] (default demo): " ENVIRONMENT
ENVIRONMENT="${ENVIRONMENT:-demo}"
[[ $ENVIRONMENT == demo || $ENVIRONMENT == live ]] || { echo "Enter demo or live"; exit 1; }

setvar CAPITAL_IDENTIFIER "$EMAIL"
setvar CAPITAL_API_KEY "$KEY"
setvar CAPITAL_API_PASSWORD "$PASS"
setvar CAPITAL_ENV "$ENVIRONMENT"
grep -q '^ALLOW_REAL_ACCOUNT=' .env || setvar ALLOW_REAL_ACCOUNT false
if grep -q '^COMPOSE_PROFILES=' .env; then
  grep -q '^COMPOSE_PROFILES=.*capital' .env || sed -i 's/^COMPOSE_PROFILES=\(.*\)/COMPOSE_PROFILES=\1,capital/; s/^COMPOSE_PROFILES=,/COMPOSE_PROFILES=/' .env
else
  echo "COMPOSE_PROFILES=capital" >> .env
fi
# Only one broker connector should feed the app at a time.
if grep -q '^COMPOSE_PROFILES=.*ctrader' .env; then
  echo "Note: the cTrader connector is also enabled. Disabling it so only Capital.com feeds the app."
  sed -i 's/,ctrader//; s/ctrader,//; s/^COMPOSE_PROFILES=ctrader$/COMPOSE_PROFILES=/' .env
  docker compose stop ctrader 2>/dev/null || true
fi
chmod 600 .env

docker compose up -d --build capital
echo "Waiting for the connector to log in..."
sleep 20
docker compose logs --tail=20 capital
echo
echo "Look for 'Logged in to Capital.com demo' and lines like 'EURUSD: epic EURUSD, 1 size = ...'."
echo "Then in the dashboard: System -> 'Use broker data', and Dashboard -> mode PAPER or BROKER DEMO."
echo "Logs: cd $APP_DIR && docker compose logs -f capital"
