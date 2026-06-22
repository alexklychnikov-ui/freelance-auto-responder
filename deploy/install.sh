#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/freelance-responder"
VENV="$APP_DIR/.venv"

echo "[1/6] system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip rsync curl \
  libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libgbm1 libasound2t64 libpango-1.0-0 libcairo2

if ! swapon --show | grep -q '/swapfile'; then
  echo "[swap] creating 2G swapfile"
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "[2/6] python venv"
mkdir -p "$APP_DIR/data" "$APP_DIR/data/examples" "$APP_DIR/logs"
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"
"$VENV/bin/playwright" install chromium
"$VENV/bin/playwright" install-deps chromium 2>/dev/null || true

echo "[3/6] normalize .env paths for Linux"
ENV_FILE="$APP_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  sed -i 's|\\|/|g' "$ENV_FILE"
  sed -i 's|C:/Python/Projects/Zerocode2md/ResponseJournal/Мои отклики.xlsx|/opt/freelance-responder/data/response_journal.xlsx|g' "$ENV_FILE"
  sed -i 's|C:/Python/Projects/Zerocode2md/Output/Отклики|/opt/freelance-responder/data/examples|g' "$ENV_FILE"
  grep -q '^BROWSER_ADAPTER=' "$ENV_FILE" && sed -i 's|^BROWSER_ADAPTER=.*|BROWSER_ADAPTER=playwright|' "$ENV_FILE" || echo 'BROWSER_ADAPTER=playwright' >> "$ENV_FILE"
  grep -q '^LIGHTRAG_BASE_URL=' "$ENV_FILE" || echo 'LIGHTRAG_BASE_URL=http://127.0.0.1:9621' >> "$ENV_FILE"
  grep -q '^DATABASE_PATH=' "$ENV_FILE" && sed -i 's|^DATABASE_PATH=.*|DATABASE_PATH=/opt/freelance-responder/data/seen_projects.db|' "$ENV_FILE"
  grep -q '^RESPONSE_JOURNAL=' "$ENV_FILE" && sed -i 's|^RESPONSE_JOURNAL=.*|RESPONSE_JOURNAL=/opt/freelance-responder/data/response_journal.xlsx|' "$ENV_FILE"
  grep -q '^RESPONSE_EXAMPLES_DIR=' "$ENV_FILE" && sed -i 's|^RESPONSE_EXAMPLES_DIR=.*|RESPONSE_EXAMPLES_DIR=/opt/freelance-responder/data/examples|' "$ENV_FILE"
  # EU VPS: direct OpenAI (OPENAI_API_KEY), not ProxyAPI
  sed -i 's|^OPENAI_BASE_URL=.*|OPENAI_BASE_URL=https://api.openai.com/v1|' "$ENV_FILE" 2>/dev/null || true
  grep -q '^OPENAI_BASE_URL=' "$ENV_FILE" || echo 'OPENAI_BASE_URL=https://api.openai.com/v1' >> "$ENV_FILE"
fi

echo "[4/6] systemd unit"
cp "$APP_DIR/deploy/freelance-responder.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable freelance-responder.service

echo "[5/6] journal xlsx placeholder"
if [[ ! -f "$APP_DIR/data/response_journal.xlsx" ]]; then
  touch "$APP_DIR/data/response_journal.xlsx"
fi

echo "[6/6] done — run: cd $APP_DIR && $VENV/bin/python -m src.scheduler run-test"
