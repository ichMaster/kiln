#!/usr/bin/env bash
# serve.sh — start the kiln tick-server (v1.1): hosts the home agent (agnika) over WS/HTTP.
#
# Config lives in .env (repo root); a matching shell env var overrides it:
#   KILN_HOST     (default 127.0.0.1)
#   KILN_PORT     (default 8000)
#   KILN_UVICORN  (optional: pin the uvicorn binary; auto-detected if unset)
#
# Usage:
#   ./serve.sh                  # use .env / defaults
#   KILN_PORT=9000 ./serve.sh   # override one value for this run
#   ./serve.sh --reload         # extra args are passed through to uvicorn
#
# KILN_SERVE=1 (the guard that actually boots the agent) is set HERE, not in .env: kiln's config
# auto-loads .env, so a value there would boot a live agent on plain imports (and in tests).
set -euo pipefail

# Run from the repo root (this script's directory), so `server.app:app` resolves.
cd "$(dirname "$0")"

# Read KEY from .env (repo root); echo its value, or $2 if absent. A real shell env var still wins.
env_get() {
  local key="$1" default="${2:-}" line val
  [ -f .env ] || { printf '%s' "$default"; return; }
  line=$(grep -E "^[[:space:]]*${key}=" .env | tail -n1 || true)
  [ -n "$line" ] || { printf '%s' "$default"; return; }
  val="${line#*=}"; val="${val%$'\r'}"                  # strip 'KEY=' and any CR
  val="${val#"${val%%[![:space:]]*}"}"                  # ltrim
  val="${val%"${val##*[![:space:]]}"}"                  # rtrim
  case "$val" in \"*\") val="${val#\"}"; val="${val%\"}";; \'*\') val="${val#\'}"; val="${val%\'}";; esac
  printf '%s' "$val"
}

HOST="${KILN_HOST:-$(env_get KILN_HOST 127.0.0.1)}"
PORT="${KILN_PORT:-$(env_get KILN_PORT 8000)}"
UVICORN="${KILN_UVICORN:-$(env_get KILN_UVICORN)}"

# Resolve the uvicorn binary if not pinned: prefer the project venv, then PATH.
if [ -z "$UVICORN" ]; then
  if [ -d ".venv" ]; then
    if [ -x ".venv/bin/uvicorn" ]; then
      UVICORN=".venv/bin/uvicorn"
    else
      echo "uvicorn isn't in .venv — install the server extra:" >&2
      echo "  source .venv/bin/activate && pip install -e '.[server]'" >&2
      exit 1
    fi
  elif command -v uvicorn >/dev/null 2>&1; then
    UVICORN="uvicorn"
  else
    echo "uvicorn not found — install the server extra:  pip install -e '.[server]'" >&2
    exit 1
  fi
fi

echo "kiln tick-server → http://${HOST}:${PORT}  (agent: agnika)  — Ctrl-C to stop"
exec env KILN_SERVE=1 "$UVICORN" server.app:app --host "$HOST" --port "$PORT" "$@"
