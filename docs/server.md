# Tick-server: setup & connecting

How to run kiln as an always-on **tick-server** and attach clients to it (v1.1). For the design —
why the engine becomes a server and how the seams map onto the network — see
[../spec/features/server-architecture.en.md](../spec/features/server-architecture.en.md); for the
single-process engine, see [architecture.md](architecture.md) and [how-it-works.md](how-it-works.md).

## What it is

Normally `kiln` runs the agent in your terminal — when you quit, the agent stops. The **tick-server**
instead hosts the agent as a process: it keeps **ticking** (drifting needs, self-triggering, thinking)
**with no client connected**, and clients attach over a WebSocket to watch and talk to it. Close the
client and the agent lives on, server-side.

- The server hosts agents keyed by **`agent_id`**. v1.1 boots one — the home agent **`agnika`**.
- A client attaches to `WS /agent/{agent_id}`, gets a one-shot **snapshot**, then a live event stream.
- Two clients on the same agent see the **same** session; disconnecting one doesn't stop the agent.
- A blocking model call runs on the agent's own thread, so it never freezes the server.

## 1. Install

The server lives behind the optional `[server]` extra (the base install stays stdlib):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[server]'      # fastapi · uvicorn · websockets · httpx
```

This adds nothing to the engine itself — `kiln` (dry-run, local live, the TUI) works exactly as
before; the extra only enables `server/` and the remote client.

## 2. Configure

Config is three layers, resolved **env var > YAML file > default**:

- **`.env`** (gitignored) — secrets + personal only: `ANTHROPIC_API_KEY`, `USER_NAME`,
  `USER_LOCATION`, `TIMEZONE` (and the launch flag `KILN_LIVE`).
- **`state/config.yaml`** (committed) — the agent tunables (`chat_model`, `tick_seconds`, …).
- **`server.yaml`** (committed) — the server's `host` / `port` / `agent`:

| Key (server.yaml) | Used by | Env override | Default |
|-------------------|---------|--------------|---------|
| `host` | server bind + clients | `KILN_HOST` | `127.0.0.1` |
| `port` | server bind + clients | `KILN_PORT` | `8000` |
| `agent` | `connect.sh` (default attach target) | `KILN_AGENT` | `agnika` |

`serve.sh`/`connect.sh` read these from `config.py` (not the shell), so they honour `server.yaml` +
any env override. Binary pins `KILN_UVICORN` / `KILN_BIN` (optional, env-only) override the
auto-detected `.venv`/PATH binaries.

The home agent boots **live** (real models), so it has the same two requirements as
`KILN_LIVE=1 kiln`: the `ANTHROPIC_API_KEY` for chat, and an installed, logged-in **Claude Code CLI**
(`claude`) for the deep branch. Without them the agent still ticks, but model turns degrade to error
strings instead of replies.

> `KILN_SERVE` is **deliberately not** in any committed file. kiln's config auto-loads `.env`, so a
> value there would boot a live agent on any plain import (including the test suite); `serve.sh` sets
> `KILN_SERVE=1` itself, only when you actually run it.

> `.env` is gitignored. Never commit your API key.

## 3. Start the server

The quickest way is the helper script at the repo root — it picks the project venv, sets the
`KILN_SERVE=1` guard, and runs uvicorn for you:

```bash
./serve.sh                                    # http://127.0.0.1:8000  (agent: agnika)
KILN_HOST=0.0.0.0 KILN_PORT=9000 ./serve.sh   # override address (or set it in .env)
./serve.sh --reload                           # extra args pass through to uvicorn
```

Or run uvicorn directly:

```bash
KILN_SERVE=1 uvicorn server.app:app
```

- **`KILN_SERVE=1` is required to boot the agent.** It's a guard: importing the app (e.g. in tests)
  must never spin up a live, paid agent. With the flag set, the app's lifespan starts `agnika` on
  boot; without it, the server runs but hosts no agents (`GET /agents` is empty, and
  `WS /agent/agnika` is rejected).
- Default address is **`http://127.0.0.1:8000`** (uvicorn's default). Override with
  `--host` / `--port`, e.g. `KILN_SERVE=1 uvicorn server.app:app --port 9000`.
- On a clean shutdown (Ctrl-C) the host stops each agent cooperatively, so its session is **persisted
  and summarized** before exit.

> **Security:** v1.1 has **no authentication**. Bind to `127.0.0.1` (the default) and do not expose
> it on a public interface. `--host 0.0.0.0` puts an unauthenticated agent on your network.

## 4. Verify it's up

```bash
curl http://127.0.0.1:8000/health
# {"ok":true}

curl http://127.0.0.1:8000/agents
# [{"agent_id":"agnika","status":{...latest tick snapshot...}}]
```

## 5. Connect a client

### The TUI (recommended)

The Textual client has a remote mode — same UI as local, but driven by the server instead of an
in-process engine (needs the `[tui]` extra too: `pip install -e '.[tui,server]'`).

Use the helper script at the repo root — the **agent name is its argument** (default `agnika`):

```bash
./connect.sh                        # attach to KILN_AGENT from .env (default agnika)
./connect.sh pashu                  # the CLI arg overrides the .env agent
KILN_PORT=9000 ./connect.sh agnika  # override address (or set KILN_HOST/KILN_PORT in .env)
```

Or invoke the client directly:

```bash
kiln --tui --remote ws://localhost:8000/agent/agnika
```

On attach it renders the snapshot (latest status + recent transcript) as scrollback, then streams the
agent's replies, usage lines, and the live status/needs panel — identical to local mode. **Quitting
the client just detaches**: the agent keeps living server-side, and you can re-attach later.

### Raw WebSocket (any language)

The wire protocol is JSON. **Client → server** messages are typed:

| `type` | `text` | Effect |
|--------|--------|--------|
| `attach` | — | handshake (optional; the snapshot is sent on connect regardless) |
| `user.message` | a chat line | goes onto the agent's inbox as user input |
| `command` | a slash line, e.g. `/status` | goes onto the inbox; the engine's `handle_command` runs it |

**Server → client** events carry a `kind` (the same kinds the local UI bus uses, so any client renders
them the same):

| `kind` | Payload | Meaning |
|--------|---------|---------|
| `snapshot` | `status`, `history` | one-shot on attach: latest status + recent transcript |
| `agent` | `text`, `is_self`, `model`, `is_thought`, `is_curiosity`, `lead` | the agent's reply |
| `usage` | `usage`, `latency` | tokens + latency for the last turn |
| `notice` | `text` | operator notice (e.g. `[exit] …`) |
| `status` | `snapshot` | per-tick needs/stats snapshot (drives the status bar / needs panel) |
| `tick` | `n` | optional heartbeat |

A minimal Python client (uses the `websockets` package from the `[server]` extra):

```python
import json
from websockets.sync.client import connect

with connect("ws://localhost:8000/agent/agnika") as ws:
    print("snapshot:", json.loads(ws.recv()))           # one-shot on attach
    ws.send(json.dumps({"type": "user.message", "text": "привіт"}))
    for raw in ws:                                       # live stream
        event = json.loads(raw)
        if event["kind"] == "agent":
            print("agnika:", event["text"])
            break
```

## 6. HTTP & control endpoints

| Method · path | Effect |
|---------------|--------|
| `GET /health` | `{"ok": true}` — liveness |
| `GET /agents` | the hosted agents + each one's latest status snapshot |
| `GET /agent/{id}/history?limit=N` | the last `N` stored turns (404 if unhosted) |
| `POST /agent/{id}/reload` | re-read the agent's canon/prompts/memory — **no session drop** |
| `POST /agent/{id}/rotate` | close+summarize the current session and start a fresh one (**non-blocking**) |
| `WS /agent/{id}` | attach → snapshot → live stream; send `user.message` / `command` |

**Real-time storage:** every turn upserts the open session into the store, so `history` and the
attach `snapshot` include the **current** conversation — not just prior closed sessions — and a
`kill -9` can't lose turns. The same control verbs also work as slash commands over the WS
(`/reload`, `/rotate`).

**Session lifecycle on a server.** The agent runs one session from boot to shutdown. To refresh
*without* a restart:
- **`/reload`** (or `POST …/reload`) re-reads `canon.md` / `prompts.md` / memory and rebuilds the
  system prompt in place — use it after editing the canon. (`config.yaml` knobs are loaded at
  startup and still need a restart.)
- **`/rotate`** (or `POST …/rotate`) cuts over to a fresh `session_id` instantly; the previous
  session's summary/facts are computed on a worker thread and folded in a moment later — so memory
  refreshes and the agent never pauses. Two notices arrive: `[rotate] session rotated…` (cutover)
  and `[rotate] previous session summarized…` (completion). With multiple clients attached, all of
  them share the one session and see these notices together.
- **Automatic rotation** — set `rotate_every_hours: N` in `state/config.yaml` (default `0` = off) and
  the agent runs the same non-blocking `/rotate` every `N` hours of real time (only when the session
  has content to summarize). Needs a restart to pick up the config change.

## 7. Where data lives

Persistence is **`agent_id`-scoped**, split into committed **config** (`state/`) and generated
**runtime** (`.kiln/`, gitignored). The default agent (`agnika`) keeps the flat layout:

| Data | `agnika` (default) | any other `agent_id` |
|------|--------------------|----------------------|
| canon / prompts (committed config) | `state/canon.md`, `state/prompts.md` | `state/{id}/…` |
| need **levels** (live, auto-written) | `.kiln/needs.json` | `.kiln/{id}/needs.json` |
| store (summaries, transcripts, facts, thoughts) | `.kiln/store.json` | `.kiln/{id}/store.json` |
| usage ledger / report | `.kiln/usage-*.{jsonl,md}` | `.kiln/{id}/usage-*` |

Two agents never share a store. The mood / needs-model / config is **also per-agent** (v1.2) — each
agent runs on its own calibration. For hosting more than one agent at once, see
[multi-agent-setup.md](multi-agent-setup.md).

## 8. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `ImportError: the kiln tick-server needs the [server] extra` | run `pip install -e '.[server]'` |
| `GET /agents` is `[]`, `WS /agent/agnika` closes immediately (code 4404) | you started uvicorn **without** `KILN_SERVE=1` — no agent was booted |
| replies come back as `(chat error: …)` / `(deep error: …)` | the live agent can't reach a model: set `ANTHROPIC_API_KEY` (chat) and log in to the Claude Code CLI (deep) |
| `Address already in use` | another process holds the port — use `--port 9000` |
| TUI: `(disconnected: …)` in the log | the server isn't running, or the URL/port is wrong — check `curl /health` first |
| `kiln --tui --remote` says it needs an extra | install both: `pip install -e '.[tui,server]'` |

## See also

- [../spec/features/server-architecture.en.md](../spec/features/server-architecture.en.md) — the
  design: seams reused, process/concurrency model, the network bus, the event protocol, `agent_id`
  scoping, and the v1.2 second agent (Pashu).
- [multi-agent-setup.md](multi-agent-setup.md) — hosting **several agents at once** (v1.2): the
  `agents:` list, connecting to each, what's isolated, and adding a companion.
- [../spec/ROADMAP.md](../spec/ROADMAP.md) — §1.1 (this server) and what's next.
- [architecture.md](architecture.md) · [how-it-works.md](how-it-works.md) — the engine itself.
