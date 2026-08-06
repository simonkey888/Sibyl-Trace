#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo bash infra/oracle/bootstrap.sh <deploy-user>" >&2
  exit 1
fi
DEPLOY_USER=${1:-ubuntu}
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  echo "Unknown deploy user: $DEPLOY_USER" >&2
  exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl gnupg ufw unattended-upgrades
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
install -d -m 0750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" \
  /opt/sibyl-trace /opt/sibyl-trace/releases /opt/sibyl-trace/shared
usermod -aG docker "$DEPLOY_USER"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable
systemctl enable --now docker
printf '%s\n' \
  "Oracle host prepared." \
  "Create /opt/sibyl-trace/shared/.env with mode 0600 and reconnect the deploy user so Docker group membership applies."
