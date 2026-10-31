#!/usr/bin/env bash
# Read-only security check of the Qivynex server. Changes NOTHING.
#   sudo /opt/trading_app/deploy/security-check.sh
# Paste the output to your assistant (it contains no passwords or keys).
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
G="\e[32m✓\e[0m"; R="\e[31m✗\e[0m"; Y="\e[33m!\e[0m"
ok(){ echo -e "$G $*"; }; bad(){ echo -e "$R $*"; }; warn(){ echo -e "$Y $*"; }
echo "== Qivynex security check $(date -u '+%F %T UTC') on $(hostname) =="

echo; echo "-- System"
. /etc/os-release 2>/dev/null; echo "OS: ${PRETTY_NAME:-?} · kernel $(uname -r) · up $(uptime -p 2>/dev/null)"
n=$(apt-get -s upgrade 2>/dev/null | grep -c '^Inst'); s=$(apt-get -s upgrade 2>/dev/null | grep -ci 'security')
[ "${n:-0}" -eq 0 ] && ok "no pending package updates" || warn "$n package updates pending ($s security) — run: sudo apt update && sudo apt upgrade"
dpkg -l unattended-upgrades 2>/dev/null | grep -q '^ii' && ok "automatic security updates installed" || warn "unattended-upgrades not installed"
[ -f /var/run/reboot-required ] && warn "reboot required" || ok "no reboot pending"

echo; echo "-- SSH"
SSHD=$(sshd -T 2>/dev/null)
v(){ echo "$SSHD" | awk -v k="$1" '$1==k{print $2}'; }
[ "$(v permitrootlogin)" = "no" ] && ok "root login disabled" || warn "PermitRootLogin = $(v permitrootlogin) (recommended: no, after you have a sudo user with an SSH key)"
[ "$(v passwordauthentication)" = "no" ] && ok "password login disabled (keys only)" || bad "PasswordAuthentication = yes — attackers can guess passwords; switch to SSH keys"
echo "  SSH port: $(v port)"
systemctl is-active --quiet fail2ban && ok "fail2ban running" || warn "fail2ban not running (blocks SSH brute force)"
f=$(journalctl -u ssh -u sshd --since '24 hours ago' 2>/dev/null | grep -c 'Failed password\|Invalid user')
echo "  failed SSH logins last 24 h: ${f:-?}"

echo; echo "-- Firewall and open ports"
if command -v ufw >/dev/null; then ufw status | head -1 | grep -q active && ok "ufw firewall active" || bad "ufw installed but INACTIVE"; ufw status | sed -n '1,20p' | sed 's/^/  /'
else warn "ufw not installed (check another firewall)"; fi
echo "  listening on the internet (not 127.0.0.1):"
ss -tlnH 2>/dev/null | awk '{print $4}' | grep -vE '^(127\.|\[::1\]|::1)' | sort -u | sed 's/^/    /'
if ss -tlnH | awk '{print $4}' | grep -vE '^(127\.|\[::1\])' | grep -qE ':(5432|8000|8090)$'; then bad "database/app port exposed publicly"; else ok "database (5432) and app (8000/8090) not public"; fi

echo; echo "-- App files"
cd "$APP_DIR" || exit 1
p=$(stat -c '%a %U' .env 2>/dev/null); [ "${p%% *}" = "600" ] && ok ".env permissions 600 ($p)" || bad ".env permissions are '$p' — run: chmod 600 $APP_DIR/.env"
for f in deploy/update.sh deploy/backup.sh; do o=$(stat -c '%U %a' "$f"); [ "${o%% *}" = "root" ] && ok "$f owned by root ($o)" || warn "$f owned by ${o%% *} — the deploy user could change what runs as root; run: sudo chown -R root:root $APP_DIR/deploy"; done
grep -q '^SECRET_KEY=.\{32,\}' .env && ok "SECRET_KEY set (32+ chars)" || bad "SECRET_KEY missing/short"
grep -qE '^INITIAL_ADMIN_PASSWORD=.+' .env && warn "INITIAL_ADMIN_PASSWORD still in .env — remove it after first login" || ok "no bootstrap password left in .env"
grep -qE '^ALLOW_REAL_ACCOUNT=(1|true|yes)' .env && warn "ALLOW_REAL_ACCOUNT is on (real money possible at connector level)" || ok "real-money connector switch off"
git status --porcelain 2>/dev/null | grep -v '^??' | head -3 | sed 's/^/  local change: /'

echo; echo "-- Containers"
docker compose ps --format '{{.Service}}\t{{.State}}\t{{.Status}}' 2>/dev/null | sed 's/^/  /'
docker compose ps --format '{{.Service}} {{.Publishers}}' 2>/dev/null | grep -E '0\.0\.0\.0|:::' && bad "a container port is published on all interfaces" || ok "no container published on 0.0.0.0"

echo; echo "-- HTTPS"
D=$(grep -E '^DOMAIN=' .env | cut -d= -f2)
if [ -n "$D" ]; then
  end=$(echo | timeout 8 openssl s_client -servername "$D" -connect "$D:443" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  [ -n "$end" ] && { d=$(( ($(date -d "$end" +%s) - $(date +%s)) / 86400 )); [ $d -gt 14 ] && ok "TLS certificate valid $d more days" || bad "TLS certificate expires in $d days"; } || warn "could not read TLS certificate for $D"
  h=$(curl -sI --max-time 8 "https://$D/" | tr -d '\r'); echo "$h" | grep -qi strict-transport-security && ok "HSTS header present" || warn "HSTS header missing"
  echo "$h" | grep -qi content-security-policy && ok "CSP header present" || warn "CSP header missing"
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://$D/"); [ "$c" = "301" ] || [ "$c" = "308" ] && ok "HTTP redirects to HTTPS" || warn "http:// answered $c (expected redirect)"
fi

echo; echo "-- Backups"
l=$(ls -t backups/trading-*.sql.gz 2>/dev/null | head -1)
[ -n "$l" ] && { a=$(( ($(date +%s) - $(stat -c %Y "$l")) / 3600 )); [ $a -lt 36 ] && ok "last backup $a h ago ($(du -h "$l" | cut -f1))" || bad "last backup $a h ago"; } || bad "no backups in $APP_DIR/backups"
[ -f /etc/cron.d/qivynex-trade-backup ] && ok "nightly backup cron installed" || bad "backup cron missing (/etc/cron.d/qivynex-trade-backup)"
p=$(stat -c '%a' backups 2>/dev/null); [ "$p" = "700" ] && ok "backups folder private (700)" || warn "backups folder permissions $p"

echo; echo "-- Disk"
df -h / | tail -1 | awk '{print "  root disk: "$5" used, "$4" free"}'
echo; echo "Done. Nothing was changed."
