#!/usr/bin/env bash
set -e
ENV=/opt/freelance-responder/.env
sed -i 's/\r$//' "$ENV"
sed -i 's|^OPENAI_BASE_URL=.*|OPENAI_BASE_URL=https://api.openai.com/v1|' "$ENV" 2>/dev/null || true
grep -q '^OPENAI_BASE_URL=' "$ENV" || echo 'OPENAI_BASE_URL=https://api.openai.com/v1' >> "$ENV"
sed -i 's|^BROWSER_ADAPTER=.*|BROWSER_ADAPTER=playwright|' "$ENV"
sed -i 's|^OPENAI_MODEL=.*|OPENAI_MODEL=gpt-4o-mini|' "$ENV"
grep -q '^LIGHTRAG_BASE_URL=' "$ENV" || echo 'LIGHTRAG_BASE_URL=http://127.0.0.1:9621' >> "$ENV"
sed -i 's|^DATABASE_PATH=.*|DATABASE_PATH=/opt/freelance-responder/data/seen_projects.db|' "$ENV"
sed -i 's|^RESPONSE_JOURNAL=.*|RESPONSE_JOURNAL=/opt/freelance-responder/data/response_journal.xlsx|' "$ENV"
sed -i 's|^RESPONSE_EXAMPLES_DIR=.*|RESPONSE_EXAMPLES_DIR=/opt/freelance-responder/data/examples|' "$ENV"
echo "env prepared"
