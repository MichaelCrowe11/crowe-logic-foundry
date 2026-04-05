# Crowe Logic IDE — Deployment

## Prerequisites
- Azure VM: Standard B2s, Ubuntu 24.04 LTS
- DNS: A record for ide.southwestmushrooms.com pointing to VM public IP
- NSG: Allow ports 80, 443. SSH via Bastion or IP-locked rule.

## First-time Setup

1. SSH into the VM
2. Run bootstrap: `curl -sSL <raw-url>/setup.sh | sudo bash`
3. Copy deployment files: `scp -r deploy/ide/* user@vm:/opt/crowe-ide/`
4. Install Session Router deps: `cd /opt/crowe-ide/session-router && npm install`
5. Configure environment: `cp /opt/crowe-ide/session-router/.env.example /opt/crowe-ide/session-router/.env` and fill in values
6. Install Nginx config: `sudo cp /opt/crowe-ide/nginx/ide.conf /etc/nginx/sites-available/ide && sudo ln -s /etc/nginx/sites-available/ide /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx`
7. Get TLS cert: `sudo certbot --nginx -d ide.southwestmushrooms.com`
8. Install systemd service: `sudo cp /opt/crowe-ide/systemd/crowe-ide-router.service /etc/systemd/system/ && sudo systemctl enable --now crowe-ide-router`
9. Build and start containers: `cd /opt/crowe-ide && sudo docker compose build && sudo docker compose up -d`

## Updating

1. Pull latest: `cd /opt/crowe-logic-foundry && git pull`
2. Copy new files: `scp -r deploy/ide/* user@vm:/opt/crowe-ide/`
3. Rebuild image: `cd /opt/crowe-ide && sudo docker compose build`
4. Restart: `sudo systemctl restart crowe-ide-router && sudo docker compose up -d`
