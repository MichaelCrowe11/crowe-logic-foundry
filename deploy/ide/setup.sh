#!/usr/bin/env bash
# setup.sh — Bootstrap Azure VM for Crowe Logic IDE
# Copy to VM and run: sudo bash setup.sh

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: This script must be run as root (or with sudo)." >&2
  exit 1
fi

echo "=== Crowe Logic IDE — VM Bootstrap ==="

# 1. System updates
export DEBIAN_FRONTEND=noninteractive
apt-get update && apt-get upgrade -y

# 2. Install Docker Engine
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 3. Install Nginx
apt-get install -y nginx

# 4. Install Certbot
apt-get install -y certbot python3-certbot-nginx

# 5. Install Node.js 22 LTS
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs

# 6. Create application directory
mkdir -p /opt/crowe-ide/session-router
mkdir -p /opt/crowe-ide/sandbox-template
mkdir -p /opt/crowe-ide/nginx
mkdir -p /opt/crowe-ide/systemd

# 7. Create non-root service user
useradd -r -s /bin/false crowe-ide || true
chown -R crowe-ide:crowe-ide /opt/crowe-ide

# 8. Clone crowe-logic-foundry (for admin bind mount)
if [ ! -d /opt/crowe-logic-foundry ]; then
  git clone https://github.com/crowelogic/crowe-logic-foundry.git /opt/crowe-logic-foundry
fi

echo "=== Bootstrap complete ==="
echo "Next steps:"
echo "  1. Copy deploy/ide/* files to /opt/crowe-ide/"
echo "  2. cd /opt/crowe-ide/session-router && npm install"
echo "  3. Copy nginx/ide.conf to /etc/nginx/sites-available/ide"
echo "  4. ln -s /etc/nginx/sites-available/ide /etc/nginx/sites-enabled/"
echo "  5. certbot --nginx -d ide.southwestmushrooms.com"
echo "  6. systemctl enable --now crowe-ide-router"
echo "  7. docker compose up -d"
