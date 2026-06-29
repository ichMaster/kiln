#!/usr/bin/env bash
# serve.sh — start the kiln tick-server (v1.1): hosts the home agent (agnika) over WS/HTTP.
#
# Host/port come from server.yaml (via config.py); a KILN_HOST / KILN_PORT env var still overrides.
#   ./serve.sh                       # address from server.yaml (default 127.0.0.1:8000)
#   KILN_PORT=9000 ./serve.sh        # override for this run
#   KILN_UVICORN=/path/to/uvicorn ./serve.sh --reload   # pin the binary; extra args pass through
#
# KILN_SERVE=1 (the guard that actually boots the agent) is set HERE, not in any committed file:
# config auto-loads, so a value there would boot a live agent on plain imports (and in tests).
set -euo pipefail

# Run from the repo root (this script's directory), so `server.app:app` resolves.
cd "$(dirname "$0")"

# A python from the project venv (else PATH) to read server.yaml via config.py.
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "python not found" >&2
  exit 1
fi

# Resolve host/port from config.py (server.yaml + env overrides + defaults).
read -r HOST PORT < <("$PY" -c "from kiln import config as c; print(c.SERVER_HOST, c.SERVER_PORT)")

# Resolve the uvicorn binary: KILN_UVICORN pin, else the project venv, else PATH.
UVICORN="${KILN_UVICORN:-}"
if [ -z "$UVICORN" ]; then
  if [ -x ".venv/bin/uvicorn" ]; then
    UVICORN=".venv/bin/uvicorn"
  elif command -v uvicorn >/dev/null 2>&1; then
    UVICORN="uvicorn"
  else
    echo "uvicorn not found — install the server extra:  pip install -e '.[server]'" >&2
    exit 1
  fi
fi

echo "kiln tick-server → http://${HOST}:${PORT}  (home agent: agnika)  — Ctrl-C to stop"
exec env KILN_SERVE=1 "$UVICORN" server.app:app --host "$HOST" --port "$PORT" "$@"
