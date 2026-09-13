#!/usr/bin/env bash
# Start the WorkBuddy2OpenAI proxy.
#
# Loads .env from the repository root, picks the project virtual environment,
# and installs the runtime dependencies when they are missing. Any arguments
# are passed through to converter.py.
#
#   ./scripts/start.sh
#   ./scripts/start.sh --port 9000 --log converter.log
#
# The API key comes from WORKBUDDY2OPENAI_KEY in .env or from --api-key. The
# launcher does not set one, so the proxy stays open on localhost unless the
# environment supplies a key.
set -e
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

# Prefer the project virtual environment: bare `python3` may be the system
# interpreter, which lacks the runtime dependencies.
PY=""
for candidate in .venv/bin/python .venv/Scripts/python.exe venv/bin/python venv/Scripts/python.exe; do
  if [ -x "$candidate" ]; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "warning: no virtual environment found; falling back to the system interpreter" >&2
  echo "  create one with: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" >&2
  PY=python3
  command -v python3 >/dev/null 2>&1 || PY=python
fi

"$PY" -c 'import httpx, fastapi, uvicorn' 2>/dev/null || "$PY" -m pip install -q -r requirements.txt

exec "$PY" converter.py "$@"
