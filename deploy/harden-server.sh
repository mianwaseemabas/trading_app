#!/usr/bin/env bash
# Server hardening for the Qivynex VPS.
#   sudo /opt/trading_app/deploy/harden-server.sh          -> fail2ban + firewall (safe, no lock-out risk)
#   sudo /opt/trading_app/deploy/harden-server.sh --ssh    -> ALSO: SSH keys only (passwords off, root key-only)
# The --ssh step refuses to run unless root already has a working SSH key, so you cannot lock yourself out.
# Keep your current SSH window open until you have tested a NEW login.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
say(){ echo -e "\e[36m==>\e[0m $*"; }

say "fail2ban (bans IPs after 5 wrong SSH logins for 1 hour)"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fail2ban >/dev/null
cat > /etc/fail2ban/jail.d/qivynex.local <<'J'
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5
bantime.increment = true
[sshd]
enabled = true
backend = systemd
J
systemctl enable --now fail2ban >/dev/null 2>&1
systemctl restart fail2ban
sleep 2
fail2ban-client status sshd | sed 's/^/    /' || true

say "Firewall (ufw): allow SSH, HTTP, HTTPS only"
SSH_PORT=$(sshd -T 2>/dev/null | awk '$1=="port"{print $2; exit}'); SSH_PORT=${SSH_PORT:-22}
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow "$SSH_PORT/tcp" comment 'ssh' >/dev/null
ufw allow 80/tcp comment 'http (redirects to https)' >/dev/null
ufw allow 443/tcp comment 'https' >/dev/null
ufw allow 443/udp comment 'https http3' >/dev/null
ufw --force enable >/dev/null
ufw status verbose | sed 's/^/    /'
echo "    (Docker-published ports are handled by Docker; only Caddy 80/443 is published — the database and app stay private.)"

if [ "${1:-}" = "--ssh" ]; then
  say "SSH: keys only"
  K=/root/.ssh/authorized_keys
  # a usable key = a line that is not restricted to a forced command (the GitHub deploy key is)
  n=$(grep -cE '^(ssh-|ecdsa-|sk-)' "$K" 2>/dev/null || true)
  if [ "${n:-0}" -lt 1 ]; then
    echo "    STOP: /root/.ssh/authorized_keys has no normal SSH key yet."
    echo "    Add your key first (see the guide), log in with it in a NEW window, then run this again with --ssh."
    exit 2
  fi
  cat > /etc/ssh/sshd_config.d/00-qivynex-hardening.conf <<'S'
# Qivynex hardening (files are read in name order and the first value wins, so this overrides 50-cloud-init.conf)
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
S
  if sshd -t; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd
    echo "    Done. Password login is OFF. Root can log in with your SSH key only."
    echo "    NOW open a NEW terminal and log in with your key. If it fails, in THIS window run:"
    echo "      rm /etc/ssh/sshd_config.d/00-qivynex-hardening.conf && systemctl reload ssh"
  else
    rm -f /etc/ssh/sshd_config.d/00-qivynex-hardening.conf
    echo "    sshd config test failed — nothing changed."
    exit 3
  fi
else
  echo
  echo "Next: add your SSH key, test it, then run:  sudo $0 --ssh"
fi
say "Re-check: sudo /opt/trading_app/deploy/security-check.sh"
