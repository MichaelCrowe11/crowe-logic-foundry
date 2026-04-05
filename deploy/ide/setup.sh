#!/usr/bin/env bash
# setup.sh — Bootstrap Azure VM for Crowe Logic IDE
# Run as root on a fresh Ubuntu 24.04 LTS VM:
#   curl -sSL <raw-url>/setup.sh | sudo bash

set -euo pipefail

echo "=== Crowe Logic IDE — VM Bootstrap ==="

# 1. System updates
apt-get update && apt-get upgrade -y

# 2. Install Docker Engine
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
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

# 7. Create non-root service user
useradd -r -s /bin/false crowe-ide || true

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
