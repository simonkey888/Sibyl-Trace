#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then echo "Run as root" >&2; exit 1; fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg ufw unattended-upgrades
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
install -d -m 0750 -o ubuntu -g ubuntu /opt/sibyl-trace
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable
systemctl enable --now docker
printf '%s\n' 'Oracle host prepared. Upload release bundle to /opt/sibyl-trace and create .env.'
