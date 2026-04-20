#!/usr/bin/env bash
# Client-side usage — the gateway speaks OpenAI format natively, so curl works
# against /v1/chat/completions with no translation layer.

set -euo pipefail

curl -sS http://localhost:1975/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "chat",
    "messages": [{"role": "user", "content": "Say hi in one word."}]
  }' | jq -r '.choices[0].message.content'
