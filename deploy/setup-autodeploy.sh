#!/usr/bin/env bash
# Enable automatic deployment from GitHub Actions to this VPS.
#   sudo /opt/trading_app/deploy/setup-autodeploy.sh
#
# Creates a locked-down "deploy" user that can ONLY run deploy/update.sh via sudo,
# generates an SSH key for GitHub Actions, and prints the values to add as
# repository secrets (Settings -> Secrets and variables -> Actions).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)"; exit 1; }
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME=deploy
HOME_DIR=/home/$USER_NAME
SSH_PORT="$(ss -ltnp 2>/dev/null | awk '/sshd/ {split($4,a,":"); print a[length(a)]; exit}')"
SSH_PORT="${SSH_PORT:-22}"

id "$USER_NAME" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$USER_NAME"
passwd -l "$USER_NAME" >/dev/null   # no password login, key only
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/.ssh"

KEY=/root/github-deploy-key
if [[ ! -f $KEY ]]; then
  ssh-keygen -q -t ed25519 -N "" -C "github-actions@trading_app" -f "$KEY"
fi
# The key may only run the update script (no shell, no forwarding).
RESTRICT="command=\"sudo $APP_DIR/deploy/update.sh\",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty"
grep -qF "$(cut -d' ' -f2 "$KEY.pub")" "$HOME_DIR/.ssh/authorized_keys" 2>/dev/null \
  || echo "$RESTRICT $(cat "$KEY.pub")" >> "$HOME_DIR/.ssh/authorized_keys"
chown "$USER_NAME:$USER_NAME" "$HOME_DIR/.ssh/authorized_keys"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"

echo "$USER_NAME ALL=(root) NOPASSWD: $APP_DIR/deploy/update.sh" > /etc/sudoers.d/trading-app-deploy
chmod 440 /etc/sudoers.d/trading-app-deploy
visudo -cf /etc/sudoers.d/trading-app-deploy >/dev/null

# Allow root-owned repo operations by git (safe.directory) when run via sudo.
git config --system --add safe.directory "$APP_DIR" 2>/dev/null || true

PUBLIC_IP="$(curl -fsS4 --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')"
cat <<EOF

================================================================
Auto-deploy user ready. Add these 4 secrets on GitHub:
  https://github.com/mianwaseemabas/trading_app/settings/secrets/actions
  -> "New repository secret" for each:

  VPS_HOST     = $PUBLIC_IP
  VPS_USER     = $USER_NAME
  VPS_PORT     = $SSH_PORT
  VPS_SSH_KEY  = (everything between the lines below, including the BEGIN/END lines)
----------------------------------------------------------------
$(cat "$KEY")
----------------------------------------------------------------
After saving the secrets, every push to 'main' that passes the tests
is deployed automatically. You can also run it by hand:
  GitHub -> Actions -> "CI / Deploy" -> Run workflow
Then delete the private key from this server:  rm /root/github-deploy-key
================================================================
EOF
