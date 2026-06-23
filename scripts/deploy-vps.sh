#!/usr/bin/env bash
#
# deploy-vps.sh — provision a VPS and deploy the Varyshop SMS gateway (Odoo) on it.
#
# Connects over SSH, installs Docker + nginx + certbot, copies the deployment
# compose file and sms_modules, configures an nginx reverse proxy with a
# Let's Encrypt certificate and starts the stack.
#
# Usage:
#   ./scripts/deploy-vps.sh <ssh-target> <domain> <email> [remote-dir]
#
#   <ssh-target>  user@ip  (e.g. root@203.0.113.10)
#   <domain>      public domain pointing at the VPS (e.g. sms.varyshop.eu)
#   <email>       email for Let's Encrypt registration
#   [remote-dir]  install path on the VPS (default: /opt/varyshop-sms)
#
# Requirements (local): ssh, rsync. The SSH user must have sudo (or be root).
# The domain's DNS A record must already point at the VPS IP.
#
set -euo pipefail

SSH_TARGET="${1:-}"
DOMAIN="${2:-}"
EMAIL="${3:-}"
REMOTE_DIR="${4:-/opt/varyshop-sms}"

if [ -z "$SSH_TARGET" ] || [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
  echo "Usage: $0 <user@ip> <domain> <email> [remote-dir]" >&2
  exit 1
fi

for bin in ssh rsync; do
  command -v "$bin" >/dev/null || { echo "ERROR: '$bin' not found in PATH." >&2; exit 1; }
done

# Module root = parent of this script's directory (extra/sms)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_DIR="$ROOT/scripts/deploy"

for f in "$ROOT/sms_modules" "$ROOT/.env" "$DEPLOY_DIR/docker-compose.vps.yml" "$DEPLOY_DIR/nginx.conf.template"; do
  [ -e "$f" ] || { echo "ERROR: required path missing: $f" >&2; exit 1; }
done

SSH_OPTS="-o StrictHostKeyChecking=accept-new"
SUDO="sudo"
# If we log in as root, sudo is unnecessary (and may be absent).
if [ "${SSH_TARGET%%@*}" = "root" ]; then SUDO=""; fi

# Retry a command a few times — tolerates sshd bouncing during droplet first-boot.
retry() {
  local n=0 max=12
  until "$@"; do
    n=$((n+1))
    if [ "$n" -ge "$max" ]; then
      echo "ERROR: command failed after $max attempts: $*" >&2
      return 1
    fi
    echo "    retry $n/$max in 10s: $*" >&2
    sleep 10
  done
}

# Fresh droplets (esp. DigitalOcean Marketplace images) run first-boot setup that
# stops/restarts sshd, so SSH may be refused for a few minutes after creation. Wait
# for it to stay reachable instead of failing on the first refusal.
echo "==> 1/5 Waiting for SSH on $SSH_TARGET to be ready"
SSH_WAIT_TRIES=30   # ~5 min at 10s intervals
for i in $(seq 1 "$SSH_WAIT_TRIES"); do
  if ssh $SSH_OPTS -o ConnectTimeout=8 -o BatchMode=yes "$SSH_TARGET" 'true' 2>/dev/null; then
    echo "    connected: $(ssh $SSH_OPTS "$SSH_TARGET" 'hostname')"
    break
  fi
  if [ "$i" -eq "$SSH_WAIT_TRIES" ]; then
    echo "ERROR: SSH not reachable after $((SSH_WAIT_TRIES*10))s." >&2
    echo "       The droplet may still be initializing, or a firewall blocks port 22." >&2
    echo "       Check the DigitalOcean console, then re-run this script." >&2
    exit 1
  fi
  echo "    not ready yet (attempt $i/$SSH_WAIT_TRIES), retrying in 10s..."
  sleep 10
done

echo "==> 2/5 Creating remote directory $REMOTE_DIR"
retry ssh $SSH_OPTS "$SSH_TARGET" "$SUDO mkdir -p '$REMOTE_DIR' && $SUDO chown \$(id -u):\$(id -g) '$REMOTE_DIR'"

# The compose file mounts sms_modules over the image path, so upload the local
# sms_modules too — this keeps the deployed modules in sync with the sms-gateway
# submodule even when the frozen image tag is behind. __pycache__ is excluded.
echo "==> 3/5 Uploading docker-compose, sms_modules and .env (rsync)"
retry rsync -az -e "ssh $SSH_OPTS" \
  "$DEPLOY_DIR/docker-compose.vps.yml" "$SSH_TARGET:$REMOTE_DIR/docker-compose.yml"
retry rsync -az --delete --exclude='__pycache__' -e "ssh $SSH_OPTS" \
  "$ROOT/sms_modules/" "$SSH_TARGET:$REMOTE_DIR/sms_modules/"
retry rsync -az -e "ssh $SSH_OPTS" \
  "$ROOT/.env" "$SSH_TARGET:$REMOTE_DIR/.env"

# Render the nginx config locally (substitute domain + web port) and upload it.
WEB_PORT="$(grep -E '^WEB_HTTP_PORT=' "$ROOT/.env" | cut -d= -f2 | tr -d '[:space:]')"
WEB_PORT="${WEB_PORT:-8069}"
TMP_NGINX="$(mktemp)"
trap 'rm -f "$TMP_NGINX"' EXIT
sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s/__WEB_PORT__/$WEB_PORT/g" \
  "$DEPLOY_DIR/nginx.conf.template" > "$TMP_NGINX"
retry rsync -az -e "ssh $SSH_OPTS" "$TMP_NGINX" "$SSH_TARGET:$REMOTE_DIR/nginx-sms.conf"

echo "==> 4/5 Provisioning VPS (Docker, nginx, certbot) and starting the stack"
retry ssh $SSH_OPTS "$SSH_TARGET" \
  "REMOTE_DIR='$REMOTE_DIR' DOMAIN='$DOMAIN' EMAIL='$EMAIL' SUDO='$SUDO' bash -s" <<'REMOTE'
set -euo pipefail

# Odoo + Postgres on a 1 GB droplet can OOM. Add a 2 GB swap file if there is no
# swap yet, so provisioning (image pull + DB init) does not exhaust memory.
if [ "$(swapon --show --noheadings | wc -l)" -eq 0 ]; then
  echo "    -> No swap found, creating a 2 GB swap file"
  $SUDO fallocate -l 2G /swapfile || $SUDO dd if=/dev/zero of=/swapfile bs=1M count=2048
  $SUDO chmod 600 /swapfile
  $SUDO mkswap /swapfile
  $SUDO swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
fi

echo "    -> Installing Docker if missing"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | $SUDO sh
fi
# Pick docker compose (plugin) vs legacy docker-compose.
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null; then
  COMPOSE="docker-compose"
else
  $SUDO apt-get update -y && $SUDO apt-get install -y docker-compose-plugin
  COMPOSE="docker compose"
fi

echo "    -> Installing nginx and certbot if missing"
if ! command -v nginx >/dev/null || ! command -v certbot >/dev/null; then
  $SUDO apt-get update -y
  $SUDO apt-get install -y nginx certbot python3-certbot-nginx
fi

echo "    -> Pulling images and starting the stack"
cd "$REMOTE_DIR"
$SUDO $COMPOSE pull
# Force-recreate so an updated sms_modules bind-mount is picked up on re-deploy
# (a changed volume's contents alone do not trigger a container recreate).
$SUDO $COMPOSE up -d --force-recreate

echo "    -> Configuring nginx reverse proxy for $DOMAIN"
$SUDO cp "$REMOTE_DIR/nginx-sms.conf" /etc/nginx/sites-available/varyshop-sms.conf
$SUDO ln -sf /etc/nginx/sites-available/varyshop-sms.conf /etc/nginx/sites-enabled/varyshop-sms.conf
$SUDO rm -f /etc/nginx/sites-enabled/default
$SUDO nginx -t
$SUDO systemctl reload nginx

# DNS pre-check: certbot only succeeds if $DOMAIN resolves to THIS droplet. Compare
# the domain's A record against the droplet's public IP and skip (don't fail the
# whole deploy) with a clear message if they differ.
echo "    -> Checking DNS for $DOMAIN before requesting a certificate"
MY_IP="$(curl -fsS --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')"
DOMAIN_IP="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1)"
echo "       droplet IP: ${MY_IP:-unknown}   |   $DOMAIN -> ${DOMAIN_IP:-unresolved}"
if [ -n "$MY_IP" ] && [ "$DOMAIN_IP" = "$MY_IP" ]; then
  echo "    -> Requesting Let's Encrypt certificate for $DOMAIN"
  $SUDO certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect || \
    echo "    !! certbot failed — see output above. Re-run after fixing, e.g.:" \
         "certbot --nginx -d $DOMAIN -m $EMAIL --agree-tos --redirect"
else
  echo "    !! SKIPPING certbot: $DOMAIN does not point at this droplet (${MY_IP:-?})."
  echo "       Set the DNS A record: $DOMAIN -> $MY_IP, wait for propagation, then run"
  echo "       this on the droplet: certbot --nginx -d $DOMAIN -m $EMAIL --agree-tos --redirect"
  echo "       The site is served over plain HTTP (port 80) until then."
fi

$SUDO systemctl reload nginx
echo "    -> Remote provisioning done."
REMOTE

echo "==> 5/5 Deployment complete."
echo "    App should be reachable at: https://$DOMAIN"
echo "    Remote dir: $SSH_TARGET:$REMOTE_DIR"
