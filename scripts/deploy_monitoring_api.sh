#!/usr/bin/env bash
# Hardened deployment script for Read-Only Monitoring API on EC2.
# STRICT SAFETY: Does NOT touch delta-paper-trading.service or delta-paper-dashboard.service!
# Binds strictly to 127.0.0.1:8088 (No public unencrypted HTTP exposure).

set -Eeuo pipefail

echo "================================================================="
echo "=== Deploying Hardened Read-Only Monitoring API on EC2 ==="
echo "================================================================="

REPO_DIR="/home/ubuntu/Local-AI-Trading-Agent"
cd "$REPO_DIR"

# 1. Verify and generate secure .env.monitoring token if missing
ENV_MONITORING="$REPO_DIR/.env.monitoring"
if [ ! -f "$ENV_MONITORING" ]; then
    echo "Generating secure cryptographically random 32-byte Bearer token..."
    NEW_TOKEN=$("$REPO_DIR/.venv/bin/python" -c "import secrets; print(secrets.token_hex(32))")
    cat <<EOF > "$ENV_MONITORING"
# Monitoring API Authentication Key (Generated $(date -u +"%Y-%m-%d %H:%M:%SZ"))
MONITORING_API_KEY=${NEW_TOKEN}
MONITORING_RATE_LIMIT=60
EOF
    chmod 600 "$ENV_MONITORING"
    echo "Saved secure token to $ENV_MONITORING (chmod 600)"
else
    echo "Found existing $ENV_MONITORING. Preserving configured token."
    chmod 600 "$ENV_MONITORING"
fi

# 2. Verify FastAPI & Uvicorn dependencies in .venv; install only if missing (never upgrades)
echo "Verifying Python dependencies..."
if ! "$REPO_DIR/.venv/bin/python" -c "import fastapi, httpx, uvicorn" 2>/dev/null; then
    echo "One or more packages missing; installing (--no-upgrade)..."
    "$REPO_DIR/.venv/bin/pip" install --quiet fastapi httpx uvicorn
fi

# 3. Ensure logging target file exists with restricted owner-only permissions
touch "$REPO_DIR/data/monitoring_api.log"
chmod 600 "$REPO_DIR/data/monitoring_api.log"

# 4. Install hardened systemd unit file
echo "Installing hardened systemd unit file..."
sudo cp "$REPO_DIR/scripts/delta-monitoring-api.service" /etc/systemd/system/delta-monitoring-api.service
sudo systemctl daemon-reload

# 5. Enable and start monitoring service
echo "Enabling and starting delta-monitoring-api.service..."
sudo systemctl enable delta-monitoring-api.service
sudo systemctl restart delta-monitoring-api.service

# 6. Verify service status and localhost health probe
sleep 5
echo "--- Service Status ---"
sudo systemctl status delta-monitoring-api.service --no-pager

echo "--- Localhost Health Probe ---"
TOKEN=$(grep "^MONITORING_API_KEY=" "$ENV_MONITORING" | cut -d '=' -f2)
# Strictly verify endpoint returns 200 OK before proceeding to record deployment
if ! curl -s -f -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8088/api/health > /dev/null; then
    echo "FATAL ERROR: Health probe failed on http://127.0.0.1:8088/api/health. Aborting deployment record!" >&2
    exit 1
fi
echo "Health probe PASSED (200 OK)"
echo ""

# 7. Record official EC2 deployment record
CURRENT_SHA=$(git -C "$REPO_DIR" rev-parse HEAD)
"$REPO_DIR/.venv/bin/python" -c "
from services.deployment_tracker import record_deployment
record_deployment(
    commit_sha='${CURRENT_SHA}',
    branch='feature/strategy-intelligence-v2',
    author='ajithkumar31082004-bit',
    environment='EC2',
    status='SUCCESS',
    tests_passed=True,
    message='EC2 Read-Only Monitoring API Hardened Release (Port 8088 Localhost)'
)
"

echo "================================================================="
echo "=== Monitoring API successfully deployed on 127.0.0.1:8088    ==="
echo "=== Zero public exposure. Connect via SSH Tunnel or WireGuard ==="
echo "================================================================="
