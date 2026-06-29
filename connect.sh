#!/usr/bin/env bash
# connect.sh — attach a TUI client to a local kiln tick-server.
#
# The agent is the first CLI arg, else server.yaml's `agent` (via config.py); host/port also from
# server.yaml. A KILN_HOST / KILN_PORT / KILN_AGENT env var still overrides; KILN_BIN pins the
# kiln client binary.
#   ./connect.sh                 # agent from server.yaml (default agnika)
#   ./connect.sh pashu           # the CLI arg wins
#   KILN_PORT=9000 ./connect.sh  # override the address for this run
#
# The agent keeps living server-side when you quit the client (the TUI just detaches).
set -euo pipefail

# Run from the repo root (this script's directory).
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

# Resolve host/port/default-agent from config.py (server.yaml + env overrides + defaults).
read -r HOST PORT DEF_AGENT < <(
  "$PY" -c "from kiln import config as c; print(c.SERVER_HOST, c.SERVER_PORT, c.SERVER_AGENT)"
)
AGENT="${1:-$DEF_AGENT}"  # CLI arg wins over server.yaml's agent
URL="ws://${HOST}:${PORT}/agent/${AGENT}"

# Resolve the kiln client binary: KILN_BIN pin, else the project venv, else PATH.
KILN="${KILN_BIN:-}"
if [ -z "$KILN" ]; then
  if [ -x ".venv/bin/kiln" ]; then
    KILN=".venv/bin/kiln"
  elif command -v kiln >/dev/null 2>&1; then
    KILN="kiln"
  else
    echo "kiln not found — install the extras:  pip install -e '.[tui,server]'" >&2
    exit 1
  fi
fi

# Best-effort reachability check (non-fatal) so a down server gives a clear hint.
if command -v curl >/dev/null 2>&1; then
  if ! curl -fsS "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
    echo "warning: nothing responding at http://${HOST}:${PORT} — start it with ./serve.sh" >&2
  fi
fi

echo "attaching TUI → ${URL}"
exec "$KILN" --tui --remote "$URL"
