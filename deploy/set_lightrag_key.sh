#!/usr/bin/env bash
set -e
ENV=/opt/freelance-responder/.env
LINE=$(docker inspect rag --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^LIGHTRAG_API_KEY=')
if grep -q '^LIGHTRAG_API_KEY=' "$ENV"; then
  sed -i "s|^LIGHTRAG_API_KEY=.*|$LINE|" "$ENV"
else
  echo "$LINE" >> "$ENV"
fi
echo "lightrag_key_len=$(echo "$LINE" | awk -F= '{print length($2)}')"
