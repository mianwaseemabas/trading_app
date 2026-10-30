#!/usr/bin/env bash
# Qivynex Trade — one-command installer for an Ubuntu/Debian VPS.
#
#   curl -fsSL https://raw.githubusercontent.com/mianwaseemabas/trading_app/main/deploy/install.sh -o install.sh
#   sudo bash install.sh
#
# Environment overrides:
#   DOMAIN=trade.qivynex.com  APP_DIR=/opt/trading_app  ADMIN_USER=abbas  ACME_EMAIL=you@example.com
#   PROXY_MODE=auto|nginx|apache|traefik|caddy|none   BRANCH=main
#
# Safe to re-run: existing .env secrets and the database are kept.
set -euo pipefail

DOMAIN="${DOMAIN:-trade.qivynex.com}"
APP_DIR="${APP_DIR:-/opt/trading_app}"
REPO_URL="${REPO_URL:-https://github.com/mianwaseemabas/trading_app.git}"
BRANCH="${BRANCH:-main}"
ADMIN_USER="${ADMIN_USER:-abbas}"
ACME_EMAIL="${ACME_EMAIL:-}"
PROXY_MODE="${PROXY_MODE:-auto}"

c_ok() { printf "\033[32m✔ %s\033[0m\n" "$*"; }
c_info() { printf "\033[36m➜ %s\033[0m\n" "$*"; }
c_warn() { printf "\033[33m⚠ %s\033[0m\n" "$*"; }
c_err() { printf "\033[31m✖ %s\033[0m\n" "$*" >&2; }

[[ $EUID -eq 0 ]] || { c_err "Run as root (sudo bash install.sh)"; exit 1; }

rand() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "${1:-40}"; }
admin_pw() {
  local p
  while :; do
    p="$(openssl rand -base64 24 | tr -dc 'A-HJ-NP-Za-km-z2-9' | head -c 14)"
    [[ $p =~ [a-z] && $p =~ [A-Z] && $p =~ [0-9] ]] && { printf '%s#%s' "${p:0:7}" "${p:7}"; return; }
  done
}
port_in_use() { ss -ltn "( sport = :$1 )" 2>/dev/null | grep -q LISTEN; }

# ------------------------------------------------------------- packages
c_info "Checking packages"
export DEBIAN_FRONTEND=noninteractive
need=()
for b in git curl openssl ss; do command -v "$b" >/dev/null || need+=("$b"); done
if ((${#need[@]})); then
  apt-get update -qq
  pkgs=()
  for b in "${need[@]}"; do [[ $b == ss ]] && pkgs+=(iproute2) || pkgs+=("$b"); done
  apt-get install -y -qq "${pkgs[@]}" ca-certificates
fi
if ! command -v docker >/dev/null; then
  c_info "Installing Docker"
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || { apt-get install -y -qq docker-compose-plugin; }
systemctl enable --now docker >/dev/null 2>&1 || true
c_ok "Docker $(docker --version | awk '{print $3}' | tr -d ,)"

# ----------------------------------------------------------------- code
if [[ -d $APP_DIR/.git ]]; then
  c_info "Updating code in $APP_DIR"
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" pull --quiet --ff-only origin "$BRANCH"
else
  c_info "Cloning $REPO_URL into $APP_DIR"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
c_ok "Code at $(git rev-parse --short HEAD)"

# ------------------------------------------------------------------ .env
NEW_ADMIN_PW=""
if [[ ! -f .env ]]; then
  c_info "Generating .env with random secrets"
  web_port=8090
  while port_in_use "$web_port"; do web_port=$((web_port + 1)); done
  NEW_ADMIN_PW="$(admin_pw)"
  umask 077
  cat > .env <<EOF
DOMAIN=$DOMAIN
ACME_EMAIL=$ACME_EMAIL
WEB_PORT=$web_port
PROXY_MODE=$PROXY_MODE
POSTGRES_USER=trader
POSTGRES_DB=trading
POSTGRES_PASSWORD=$(rand 40)
SECRET_KEY=$(rand 64)
INITIAL_ADMIN_USERNAME=$ADMIN_USER
INITIAL_ADMIN_PASSWORD=$NEW_ADMIN_PW
BRIDGE_KEY_ID=mt5-bridge-1
BRIDGE_SECRET=$(rand 64)
BRIDGE_ALLOWED_IPS=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
SESSION_IDLE_MINUTES=30
SESSION_MAX_HOURS=12
EOF
  chmod 600 .env
  umask 022
  c_ok ".env created (chmod 600)"
else
  c_ok "Keeping existing .env"
fi
set -a; . ./.env; set +a
DOMAIN="${DOMAIN}"

# --------------------------------------------------------- proxy detection
if [[ $PROXY_MODE == auto ]]; then
  if docker ps --format '{{.Image}} {{.Names}}' | grep -qiE '(^| )traefik|traefik:'; then
    PROXY_MODE=traefik
  elif systemctl is-active --quiet nginx 2>/dev/null; then
    PROXY_MODE=nginx
  elif systemctl is-active --quiet apache2 2>/dev/null; then
    PROXY_MODE=apache
  elif ! port_in_use 80 && ! port_in_use 443; then
    PROXY_MODE=caddy
  else
    PROXY_MODE=none
  fi
  sed -i "s/^PROXY_MODE=.*/PROXY_MODE=$PROXY_MODE/" .env
fi
c_ok "Reverse proxy mode: $PROXY_MODE"

# --------------------------------------------------------------- DNS check
public_ip="$(curl -fsS4 --max-time 5 https://api.ipify.org || true)"
dns_ip="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)"
if [[ -n $public_ip && $dns_ip != "$public_ip" ]]; then
  c_warn "DNS for $DOMAIN resolves to '${dns_ip:-nothing}' but this server is $public_ip."
  c_warn "Create an A record: $DOMAIN -> $public_ip  (HTTPS certificates need it)."
fi

# --------------------------------------------------------------- traefik
if [[ $PROXY_MODE == traefik ]]; then
  tcontainer="$(docker ps --format '{{.Names}} {{.Image}}' | awk 'tolower($0) ~ /traefik/ {print $1; exit}')"
  tnet="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{println $k}}{{end}}' "$tcontainer" | grep -v '^$' | head -1)"
  resolver="$(docker inspect -f '{{join .Args " "}} {{join .Config.Cmd " "}}' "$tcontainer" | grep -oE 'certificatesresolvers\.[A-Za-z0-9_-]+' | head -1 | cut -d. -f2 || true)"
  resolver="${resolver:-letsencrypt}"
  c_info "Traefik container '$tcontainer' on network '$tnet', cert resolver '$resolver'"
  cat > docker-compose.override.yml <<EOF
services:
  web:
    networks: [internal, edge, traefik_ext]
    labels:
      - traefik.enable=true
      - traefik.docker.network=$tnet
      - traefik.http.routers.qivynex-trade.rule=Host(\`$DOMAIN\`)
      - traefik.http.routers.qivynex-trade.entrypoints=websecure
      - traefik.http.routers.qivynex-trade.tls=true
      - traefik.http.routers.qivynex-trade.tls.certresolver=$resolver
      - traefik.http.services.qivynex-trade.loadbalancer.server.port=8080
networks:
  traefik_ext:
    external: true
    name: $tnet
EOF
  c_warn "Check the entrypoint name ('websecure') matches your Traefik config: docker-compose.override.yml"
fi

# ----------------------------------------------------------------- start
profile=()
[[ $PROXY_MODE == caddy ]] && profile=(--profile caddy)
c_info "Building and starting containers (first build takes a few minutes)"
docker compose "${profile[@]}" up -d --build --remove-orphans
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${WEB_PORT}/api/health" >/dev/null 2>&1; then break; fi
  sleep 3
done
curl -fsS "http://127.0.0.1:${WEB_PORT}/api/health" >/dev/null && c_ok "App is up on 127.0.0.1:${WEB_PORT}" || { c_err "App did not become healthy. Check: docker compose logs backend"; exit 1; }

# --------------------------------------------------------- host nginx/apache
need_certbot() {
  command -v certbot >/dev/null || { apt-get update -qq; apt-get install -y -qq certbot "$1"; }
}
email_args=(--register-unsafely-without-email)
[[ -n ${ACME_EMAIL:-} ]] && email_args=(-m "$ACME_EMAIL")

if [[ $PROXY_MODE == nginx ]]; then
  conf_dir=/etc/nginx/sites-available
  [[ -d $conf_dir ]] || conf_dir=/etc/nginx/conf.d
  conf="$conf_dir/$DOMAIN.conf"
  [[ $conf_dir == /etc/nginx/sites-available ]] && conf="$conf_dir/$DOMAIN"
  if [[ ! -f $conf ]]; then
    cat > "$conf" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    client_max_body_size 32m;
    location / {
        proxy_pass http://127.0.0.1:${WEB_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
EOF
    [[ $conf_dir == /etc/nginx/sites-available ]] && ln -sf "$conf" "/etc/nginx/sites-enabled/$DOMAIN"
  fi
  nginx -t && systemctl reload nginx
  need_certbot python3-certbot-nginx
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect "${email_args[@]}" \
    && c_ok "HTTPS certificate installed" || c_warn "certbot failed (DNS not ready?). Re-run later: certbot --nginx -d $DOMAIN"
elif [[ $PROXY_MODE == apache ]]; then
  a2enmod -q proxy proxy_http headers >/dev/null
  conf="/etc/apache2/sites-available/$DOMAIN.conf"
  [[ -f $conf ]] || cat > "$conf" <<EOF
<VirtualHost *:80>
    ServerName $DOMAIN
    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:${WEB_PORT}/
    ProxyPassReverse / http://127.0.0.1:${WEB_PORT}/
</VirtualHost>
EOF
  a2ensite -q "$DOMAIN" >/dev/null && systemctl reload apache2
  need_certbot python3-certbot-apache
  certbot --apache -d "$DOMAIN" --non-interactive --agree-tos --redirect "${email_args[@]}" \
    && c_ok "HTTPS certificate installed" || c_warn "certbot failed (DNS not ready?). Re-run later: certbot --apache -d $DOMAIN"
elif [[ $PROXY_MODE == none ]]; then
  c_warn "Ports 80/443 are in use by an unrecognised server."
  c_warn "Point https://$DOMAIN at http://127.0.0.1:${WEB_PORT} in your reverse proxy."
fi

# ---------------------------------------------------------------- backups
chmod +x deploy/*.sh
cat > /etc/cron.d/qivynex-trade-backup <<EOF
30 2 * * * root $APP_DIR/deploy/backup.sh >> /var/log/qivynex-trade-backup.log 2>&1
EOF
c_ok "Daily database backup scheduled (02:30, kept 14 days in $APP_DIR/backups)"

# ---------------------------------------------------------------- summary
if [[ -n $NEW_ADMIN_PW ]]; then
  for i in $(seq 1 20); do
    docker compose logs backend 2>/dev/null | grep -q "Initial admin" && break
    sleep 2
  done
  # Remove the bootstrap password from disk once the admin exists.
  sed -i "s/^INITIAL_ADMIN_PASSWORD=.*/INITIAL_ADMIN_PASSWORD=/" .env
  cred=/root/qivynex-trade-credentials.txt
  umask 077
  printf "URL: https://%s\nUsername: %s\nTemporary password: %s\n(Change it at first login, then delete this file.)\n" "$DOMAIN" "$ADMIN_USER" "$NEW_ADMIN_PW" > "$cred"
fi

echo
echo "================================================================"
c_ok "Qivynex Trade is installed"
echo "  URL:            https://$DOMAIN"
if [[ -n $NEW_ADMIN_PW ]]; then
echo "  Admin user:     $ADMIN_USER"
echo "  Temp password:  $NEW_ADMIN_PW   (also saved in /root/qivynex-trade-credentials.txt)"
echo "                  You must change it at first sign-in. Then enable 2FA under My account."
fi
echo "  Bridge secret:  grep BRIDGE_SECRET $APP_DIR/.env   (copy into bridge.env on the Windows MT5 host)"
echo "  Update later:   sudo $APP_DIR/deploy/update.sh"
echo "  Emergency stop: cd $APP_DIR && docker compose exec backend python -m app.cli kill-switch on"
echo "================================================================"
