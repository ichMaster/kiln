# Multi-agent setup (v1.2)

How to run and use kiln's multi-agent environment as it exists today (v1.2). One server process hosts
several independent agents at once; you talk to one at a time by connecting to it.

For the design behind this — what is shared between agents and what is isolated — see
[../spec/features/server-architecture.en.md §13](../spec/features/server-architecture.en.md). For the
single-agent server basics (install, the WS protocol, the HTTP endpoints), see
[server.md](server.md); this document only adds the multi-agent parts on top.

## What v1.2 gives you (and what it doesn't yet)

What works today:

- One server hosts **several agents at once**, each addressed by its own `agent_id`. Out of the box
  that's two: **agnika** (the home agent) and **pashu** (a calm companion).
- Each agent runs on **its own thread** with **fully isolated state** — its own needs, memory/store,
  persona, and calibration. A turn on one agent never touches another's.
- You attach a client to one chosen agent. The agent keeps living server-side when you disconnect.

What is **not** here yet, so you are not surprised:

- **Agents cannot talk to each other.** They are deliberately isolated in v1.2; inter-agent messaging
  arrives in phase 1.6.
- **You cannot address several agents from one window.** One client talks to one agent; to use both,
  open two clients. A single window that holds several agents, and group rooms where everyone sees
  everything, come later (1.6 and 1.10).
- **There is no operator UI** to add, start, or stop agents. You register an agent in a config file
  and restart the server (a management panel is a v2 concern).
- **Permission scopes are set but not enforced.** Each agent carries a scope (the home agent is broad,
  companions are narrow), but nothing acts on it until tools land in phase 1.5.

## 1. Which agents the server runs

The set of agents the server boots is the `agents:` list in `server.yaml`:

```yaml
host: 127.0.0.1
port: 8000
agent: agnika            # connect.sh's default attach target
agents: [agnika, pashu]  # the agents the server boots, each isolated
```

`agnika` is the default (home) agent and keeps the flat layout (`state/`, `.kiln/`). Every other id —
here `pashu` — lives under its own subfolders (`state/pashu/`, `.kiln/pashu/`). You can override the
list for a single run without editing the file by setting `KILN_AGENTS` (comma-separated), for example
`KILN_AGENTS=agnika ./serve.sh` to boot only Agnika.

## 2. Start the server

The helper script boots **every** agent in that list:

```bash
pip install -e '.[server,tui]'   # fastapi · uvicorn · websockets · the TUI client
./serve.sh                        # boots agnika + pashu; prints "agents: agnika,pashu"
```

(Under the hood that is `KILN_SERVE=1 uvicorn server.app:app`; the `KILN_SERVE` guard is what tells the
app to actually start the agents — a plain import never does, so tests stay paid-call-free.)

The home agent boots **live**, so it has the usual two requirements: an `ANTHROPIC_API_KEY` in `.env`
for the chat branch, and a logged-in **Claude Code CLI** for the deep branch. Without them the agents
still tick, but model turns come back as error strings instead of replies.

## 3. See what's running

```bash
curl http://127.0.0.1:8000/agents
# [{"agent_id":"agnika","scope":"broad","status":{…latest tick…}},
#  {"agent_id":"pashu","scope":"narrow","status":{…latest tick…}}]
```

Each entry carries the agent's permission **scope** (`broad` / `narrow` — set, not yet enforced) and its
latest status snapshot. Both agents are already ticking server-side here, before anyone has connected.

## 4. Connect to an agent

The connection itself is the address: you pick the agent when you start the client, and everything you
type on that connection goes to that one agent.

```bash
./connect.sh            # attach to the default agent (agnika)
./connect.sh pashu      # attach to Pashu instead
./connect.sh agnika     # attach to Agnika explicitly
```

Or drive the client directly, naming the agent in the URL:

```bash
kiln --tui --remote ws://localhost:8000/agent/pashu
```

To talk to **both** agents, open **two terminals** — one running `./connect.sh agnika`, the other
`./connect.sh pashu`. They are independent conversations; a message to one never reaches the other.
Quitting a client just detaches — the agent keeps living, ticking, and (if it gets lonely) reaching
out, server-side.

## 5. What is isolated per agent, and what is shared

The rule of thumb: each agent's **mind and memory are its own**; only the **server process and your
operator credentials** are shared.

| Isolated — one per agent | Shared — one per server |
|--------------------------|--------------------------|
| its thread (ticks, drifts, self-triggers on its own) | the Python process + the async layer |
| its need **levels** (`.kiln/{id}/needs.json`) | the agent registry (the host) |
| its memory/store (`.kiln/{id}/store.json`) | the `ANTHROPIC_API_KEY` + the `claude` CLI — **one key for all → shared billing** |
| its usage ledger/report | the bind address (host/port) |
| its persona (`state/{id}/canon.md`, `prompts.md`) | |
| its calibration (`needs_model.yaml`, `mood.json`, `config.yaml`) | |
| its permission scope (broad / narrow) | |

Because the calibration is per-agent, two agents genuinely differ. Pashu, for instance, is tuned to be
calmer than Agnika — she reaches out less often (a higher loneliness threshold) and stays quiet longer
after speaking first. She has her own voice (`state/pashu/canon.md`) and her own felt-state cues
(`state/pashu/mood.json`).

## 6. Add a new companion agent

There is no UI for this yet — you author the agent's files and register it in config. Three steps, using
a new agent called `lumi` as the example:

1. **Author its data** under `state/lumi/`. The simplest start is to copy Pashu's folder and edit it:

   ```bash
   cp -r state/pashu state/lumi
   ```

   Then edit the five files to give `lumi` its own character:
   - `canon.md` — its persona and voice (this is the system prompt).
   - `needs_model.yaml` — its need model (drift, satiation, trigger thresholds).
   - `mood.json` — its felt-state bands, labels, and behavioural cues.
   - `prompts.md` — its self-trigger (reach-out and inner-thought) prompts.
   - `config.yaml` — its tunables. At minimum set a distinct `agent_name`. Any key you omit falls back
     to the built-in default, so you only set what differs.

   You do **not** create `.kiln/lumi/` — the server makes it on first boot and writes the runtime data
   (needs levels, store, usage) there. It is gitignored.

2. **Register it** by adding the id to `server.yaml`:

   ```yaml
   agents: [agnika, pashu, lumi]
   ```

3. **Restart the server** (`./serve.sh`). It now boots all three; `curl /agents` lists `lumi`, and
   `./connect.sh lumi` attaches to it.

A quick way to sanity-check an agent's files before booting:

```bash
.venv/bin/python -c "from kiln.config import AgentConfig; print(AgentConfig.for_agent('lumi').agent_name)"
```

If a file is missing or malformed it heals to the built-in default rather than crashing, so a partial
agent still runs — but check the values are what you intended.

## 7. Refreshing an agent without a restart

Two control verbs work per agent, both as slash commands in the TUI and as HTTP calls (handy for the
companion you're not currently attached to):

- **`/reload`** (or `POST /agent/{id}/reload`) re-reads that agent's `canon.md` / `prompts.md` / memory
  and rebuilds its system prompt in place, without dropping the session. Use it after editing a persona.
- **`/rotate`** (or `POST /agent/{id}/rotate`) closes and summarizes the current session and starts a
  fresh one, without pausing the agent.

Note that **`config.yaml` / `needs_model.yaml` / `mood.json` and the `agents:` list are read at boot**,
so changing calibration or adding an agent needs a restart — `/reload` only refreshes the persona text.

## 8. Endpoints (per agent)

Everything is keyed by `agent_id`, so the v1.1 endpoints simply work for the second agent too:

| Method · path | Effect |
|---------------|--------|
| `GET /agents` | every hosted agent + its scope + latest status |
| `GET /agent/{id}/history?limit=N` | the last `N` stored turns for that agent (404 if unhosted) |
| `POST /agent/{id}/reload` | re-read that agent's canon/prompts/memory (no session drop) |
| `POST /agent/{id}/rotate` | close+summarize and start a fresh session (non-blocking) |
| `WS /agent/{id}` | attach → snapshot → live stream; send `user.message` / `command` |

## 9. Security note

There is still **no authentication** (as in v1.1). All agents share one process and one API key, so
anyone who can reach the port can talk to any agent and spend on your key. Bind to `127.0.0.1` (the
default) and do not expose it on a public interface. The per-agent permission scope is groundwork for
later enforcement, not a security boundary today.

## See also

- [server.md](server.md) — the single-agent server: install, the WS protocol, the HTTP endpoints.
- [../spec/features/server-architecture.en.md](../spec/features/server-architecture.en.md) — the design:
  §13 (what's shared vs isolated, and the per-agent config), §14 (how agents *will* talk to each other,
  in 1.6).
- [../spec/ROADMAP.md](../spec/ROADMAP.md) — §1.2 (this phase) and what comes next (1.6 inter-agent
  messaging, 1.10 group rooms).
