#!/usr/bin/env bash
# connect.sh — attach a TUI client to a local kiln tick-server.
#
# Config lives in .env (repo root); a matching shell env var overrides it:
#   KILN_AGENT  (default agnika)   — also overridable as the first CLI argument
#   KILN_HOST   (default 127.0.0.1)
#   KILN_PORT   (default 8000)
#   KILN_BIN    (optional: pin the kiln client binary; auto-detected if unset)
#
# Usage:
#   ./connect.sh                 # agent from .env (KILN_AGENT) or agnika
#   ./connect.sh pashu           # the CLI arg wins over .env
#   KILN_PORT=9000 ./connect.sh  # override one value for this run
#
# The agent keeps living server-side when you quit the client (the TUI just detaches).
set -euo pipefail

# Run from the repo root (this script's directory).
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

# Agent: CLI arg ($1) > shell env KILN_AGENT > .env KILN_AGENT > "agnika".
AGENT="${1:-${KILN_AGENT:-$(env_get KILN_AGENT agnika)}}"
HOST="${KILN_HOST:-$(env_get KILN_HOST 127.0.0.1)}"
PORT="${KILN_PORT:-$(env_get KILN_PORT 8000)}"
URL="ws://${HOST}:${PORT}/agent/${AGENT}"
KILN="${KILN_BIN:-$(env_get KILN_BIN)}"

# Resolve the kiln client binary if not pinned: prefer the project venv, then PATH.
if [ -z "$KILN" ]; then
  if [ -d ".venv" ]; then
    if [ -x ".venv/bin/kiln" ]; then
      KILN=".venv/bin/kiln"
    else
      echo "kiln isn't in .venv — install the TUI + server extras:" >&2
      echo "  source .venv/bin/activate && pip install -e '.[tui,server]'" >&2
      exit 1
    fi
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
