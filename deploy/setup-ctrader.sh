#!/usr/bin/env bash
# Connect the app to a cTrader account via the cTrader Open API.
#   sudo /opt/trading_app/deploy/setup-ctrader.sh
# Asks for the credentials from https://openapi.ctrader.com (your app -> Credentials / Playground),
# stores them in .env (chmod 600) and starts the "ctrader" connector container.
set -euo pipefail
echo "Tip: you can now enter broker credentials on the dashboard (Brokers page) instead of this script."
echo "Values entered here are only used if the dashboard has none for this broker yet."
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
ask() {  # ask VAR "Prompt" [secret]
  local __v
  if [[ ${3:-} == secret ]]; then read -r -s -p "$2: " __v; echo; else read -r -p "$2: " __v; fi
  __v="$(printf '%s' "$__v" | tr -d '[:space:]')"
  [[ -n $__v ]] || { echo "Value required"; exit 1; }
  printf -v "$1" '%s' "$__v"
}

echo "cTrader Open API setup (values are not shown on screen for secrets)."
ask CID "Client ID"
ask CSECRET "Client Secret" secret
ask ATOKEN "Access token (from Playground)" secret
ask RTOKEN "Refresh token (from Playground)" secret
read -r -p "cTrader account number to trade (blank = the only demo account): " LOGIN
read -r -p "Broker symbol names if they have a suffix, e.g. EURUSD=EURUSD.r (blank = none): " SMAP

setvar CTRADER_CLIENT_ID "$CID"
setvar CTRADER_CLIENT_SECRET "$CSECRET"
setvar CTRADER_ACCESS_TOKEN "$ATOKEN"
setvar CTRADER_REFRESH_TOKEN "$RTOKEN"
setvar CTRADER_ACCOUNT_LOGIN "${LOGIN// /}"
setvar CTRADER_SYMBOL_MAP "${SMAP// /}"
grep -q '^ALLOW_REAL_ACCOUNT=' .env || setvar ALLOW_REAL_ACCOUNT false
if grep -q '^COMPOSE_PROFILES=' .env; then
  grep -q '^COMPOSE_PROFILES=.*ctrader' .env || sed -i 's/^COMPOSE_PROFILES=\(.*\)/COMPOSE_PROFILES=\1,ctrader/; s/^COMPOSE_PROFILES=,/COMPOSE_PROFILES=/' .env
else
  echo "COMPOSE_PROFILES=ctrader" >> .env
fi
# Only one broker connector should feed the app at a time.
if grep -q '^COMPOSE_PROFILES=.*capital' .env; then
  echo "Note: the Capital.com connector is also enabled. Disabling it so only cTrader feeds the app."
  sed -i 's/,capital//; s/capital,//; s/^COMPOSE_PROFILES=capital$/COMPOSE_PROFILES=/' .env
  docker compose stop capital 2>/dev/null || true
fi
chmod 600 .env

docker compose up -d --build ctrader
echo "Waiting for the connector to log in..."
sleep 15
docker compose logs --tail=20 ctrader
echo
echo "If you see 'Using cTrader account ...', it is connected. In the dashboard:"
echo "  System -> Market data source -> 'Use broker data', then Dashboard -> mode PAPER or MT5/BROKER DEMO."
echo "Logs: cd $APP_DIR && docker compose logs -f ctrader"
