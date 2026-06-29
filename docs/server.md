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

The server reuses the same `.env` as live mode (loaded from the repo root). Relevant keys:

| Key | Why | Needed for |
|-----|-----|------------|
| `ANTHROPIC_API_KEY` | the **chat** branch (Haiku via the Messages API) | a live agent |
| *(Claude Code CLI logged in)* | the **deep** branch shells out to `claude -p` (Opus) | a live agent |
| `CHAT_MODEL` / `DEEP_MODEL` | override the models | optional |
| `TICK_SECONDS` | tick cadence | optional |
| `USER_NAME` / `AGENT_NAME` | labels | optional |

The home agent boots **live** (real models), so it has the same two requirements as
`KILN_LIVE=1 kiln`: the `ANTHROPIC_API_KEY` for chat, and an installed, logged-in **Claude Code CLI**
(`claude`) for the deep branch. Without them the agent still ticks, but model turns degrade to error
strings instead of replies.

The helper scripts (`serve.sh` / `connect.sh`) read their own settings from the **same `.env`** — a
matching shell env var overrides the file, and `connect.sh`'s first CLI argument overrides the agent:

| Key | Used by | Default |
|-----|---------|---------|
| `KILN_HOST` | both | `127.0.0.1` |
| `KILN_PORT` | both | `8000` |
| `KILN_AGENT` | `connect.sh` (the agent to attach to) | `agnika` |
| `KILN_UVICORN` | `serve.sh` (pin the uvicorn binary) | auto-detect `.venv`/PATH |
| `KILN_BIN` | `connect.sh` (pin the kiln binary) | auto-detect `.venv`/PATH |

> `KILN_SERVE` is **deliberately not** an `.env` key. kiln's config auto-loads `.env`, so a value
> there would boot a live agent on any plain import (including the test suite); `serve.sh` sets
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

## 6. HTTP read endpoints

| Method · path | Returns |
|---------------|---------|
| `GET /health` | `{"ok": true}` — liveness |
| `GET /agents` | the hosted agents + each one's latest status snapshot |
| `GET /agent/{id}/history?limit=N` | the last `N` **persisted** turns for an agent (404 if unhosted) |
| `WS /agent/{id}` | attach → snapshot → live stream; send `user.message` / `command` |

The live (not-yet-persisted) turns of the current session aren't in `history` — they stream as
`agent` events from the moment you attach. `history` carries prior, closed sessions.

## 7. Where data lives

Persistence is **`agent_id`-scoped**. The default agent (`agnika`) keeps the existing flat paths, so
nothing migrates:

| Data | `agnika` (default) | any other `agent_id` |
|------|--------------------|----------------------|
| need levels | `state/needs.json` | `state/{id}/needs.json` |
| canon / prompts | `state/canon.md`, `state/prompts.md` | `state/{id}/…` |
| store (summaries, transcripts, facts, thoughts) | `.kiln/store.json` | `.kiln/{id}/store.json` |
| usage ledger / report | `.kiln/usage-*.{jsonl,md}` | `.kiln/{id}/usage-*` |

Two agents never share a store. (The mood / needs-model *config* is still global in v1.1; per-agent
config arrives with the second agent in v1.2.)

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
- [../spec/ROADMAP.md](../spec/ROADMAP.md) — §1.1 (this server) and what's next.
- [architecture.md](architecture.md) · [how-it-works.md](how-it-works.md) — the engine itself.
