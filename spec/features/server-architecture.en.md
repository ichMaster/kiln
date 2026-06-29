# Feature: Server architecture & client/server separation (v1.1)

Status: **design** (v1.1 — *Tick-server: engine = WS/HTTP server, clients attach*).
Companion: [ROADMAP §1.1](../ROADMAP.md), [ARCHITECTURE.md](../ARCHITECTURE.md). Ukrainian
translation: [server-architecture.uk.md](server-architecture.uk.md).

## 1. Why a server

The engine is an **always-on cheap tick loop**: most ticks are free local state (needs drift), the
model is called rarely, and the loop keeps living between turns — which is what lets the agent
self-trigger. Today that loop runs **in-process**: the TUI starts `engine.run()` on a background
thread and talks to it over an in-memory `Bridge`.

v1.1 lifts the loop into a **standalone server process**. Clients (the TUI now, a web client in v2)
**attach over WebSocket**; the agent keeps living with **no client connected**. This is the
foundation of the multi-agent **hub** — one server hosting several agents, each addressable by an
`agent_id`.

The key realisation from v0: **the engine never depends on the interface.** It already speaks to the
world through two narrow seams — `Channel` (input) and `Output` (render events) — and the TUI already
proved a thread-plus-bus topology (`Bridge`). So v1.1 is **not** a rewrite of the mind; it is a
**network implementation of the bus that already exists**, with `engine.run()` left untouched.

## 2. What v0 already built (and v1.1 reuses)

| v0 seam | What it is | v1.1 role |
|---|---|---|
| `Channel.poll() -> str \| None` | non-blocking input port (`StdinChannel`, `ScriptedChannel`, `TuiChannel`) | a `ServerChannel` drains a per-agent input queue fed by WS `user.message` / `command` |
| `Output` (`user`/`agent`/`usage`/`notice`/`status`) | the core writes **every** reply/usage/notice/status through this port, never `print` | a `ServerOutput` serialises each call to a WS event and **broadcasts** it to all clients on that agent |
| `Bridge` (echo-free inbox/outbox) | in-process thread-safe queues between the engine thread and the UI | generalised into a **network bus**: WS replaces the in-memory queues; echo-free is preserved |
| `status(snapshot)` every tick | `{status, model, branch, tick, needs, thresholds, actions, hottest, cooldowns, stats}` | becomes the `status` WS event verbatim (drives any client's status bar / needs panel) |
| `agent(text, is_self, lead, model, is_thought, is_curiosity)` | reply flags grown across v0.10/v0.11 | the `agnika.message` event carries the same flags so any client renders identically |
| `.kiln/store.json`, `state/*` | per-session store + per-agent calibration (`needs_model.yaml`, `mood.json`, `canon.md`, …) | scoped **per `agent_id`** so multiple agents don't share state |

Because the contract is already drawn, **the engine core (`run`, `respond`, brains, needs) changes by
zero lines** in v1.1. All new code is the host + transport.

## 3. Process & concurrency model

```
                          ┌────────────────────────── server process (one) ─────────────────────────┐
                          │                                                                          │
   ws://…/agent/agnika ───┤  AgentHost (registry: agent_id -> AgentRuntime)                          │
   ws://…/agent/pashu  ───┤                                                                          │
                          │   AgentRuntime("agnika"):                                                │
                          │     ├─ engine.run(channel=ServerChannel, output=ServerOutput,            │
                          │     │             brain=LiveBrain)   ← on its OWN thread (as the TUI does)│
                          │     ├─ inbox queue   (WS -> engine)                                       │
                          │     └─ outbox hub    (engine -> all subscribed WS clients)               │
                          │                                                                          │
                          │   async layer (FastAPI/Starlette + websockets):                          │
                          │     • accepts WS connections, routes by agent_id                         │
                          │     • pumps inbox <- client, outbox -> clients                           │
                          │     • HTTP: health, agent list, history fetch                            │
                          └──────────────────────────────────────────────────────────────────────────┘
        ▲                                   ▲
        │ WS (JSON events)                  │ WS (JSON events)
 ┌──────┴───────┐                    ┌──────┴───────┐
 │ TUI client   │                    │ TUI client #2│   (web client = v2)
 │ (render.py)  │                    │ (read-along) │   thin: render + input, no agent logic
 └──────────────┘                    └──────────────┘
```

- **One server process.** Each hosted agent is an `AgentRuntime` keyed by `agent_id`, holding its own
  `engine.run()`, `State`, store, and bus. v1.1 ships **one** agent (`agnika`), but the host, the
  routes, and the persistence paths are `agent_id`-scoped **from day one**, so adding agents in v2 is
  additive (no reshaping).
- **The tick loop runs server-side, independent of clients.** It starts when the server starts and
  ticks forever — the agent ages, drifts, and self-triggers whether or not anyone is attached.
- **Blocking model calls don't freeze the server.** v0's brains are synchronous (`chat` via the SDK,
  `deep`/`tool` via `claude -p`). v1.1 keeps the **proven TUI pattern**: run each agent's `run()` on a
  **dedicated thread**, so a blocking call blocks only that agent's thread — the async event loop
  (accepting connections, pumping events for other agents) stays responsive. The full event-queue
  **FSM** that makes this explicit is **v1.2**; v1.1 only needs the thread + non-blocking transport.

## 4. Client ↔ server: who owns what

The split is strict, and it is the whole point of the design.

| Concern | **Server** (the agent's mind + host) | **Client** (TUI / web) |
|---|---|---|
| Needs, drift, satiation, mood, thoughts, curiosity | ✅ owns | ❌ |
| Two brains + cost routing + self-triggers | ✅ owns | ❌ |
| Persistence (store, ledger, transcripts, state) | ✅ owns | ❌ |
| Model calls + API keys + `claude -p` | ✅ owns (keys never leave the server) | ❌ |
| The tick loop (lives without a client) | ✅ owns | ❌ |
| Rendering replies / status / needs panel | ❌ | ✅ owns |
| Capturing user input + slash commands | ❌ | ✅ owns (forwards as events) |
| Per-connection view state (scrollback, copy) | ❌ | ✅ owns |
| Agent state of any kind | ❌ (stateless re: the agent) | ❌ |

**The client is a thin terminal onto a stream of events.** It holds no agent logic; it connects,
subscribes to one `agent_id`, renders the events it receives, and forwards what the user types. Two
clients on the same agent see **the same session** (a second client is a read-along view of the same
live state). The engine **never imports a client**; a client **never imports the engine** — they meet
only at the **event protocol**.

## 5. The network bus (Bridge → WebSocket)

The in-process `Bridge` already separates two flows; the server keeps the separation, swapping the
transport:

- **Inbox (client → engine).** A WS `user.message` / `command` is pushed onto the agent's input
  queue. `ServerChannel.poll()` drains it — identical contract to `TuiChannel`. The engine doesn't
  know it came over a socket.
- **Outbox (engine → clients).** `ServerOutput.agent(...)` (and `usage`/`notice`/`status`) serialises
  to a JSON event and hands it to the agent's **broadcast hub**, which fans it out to **every** WS
  client subscribed to that agent. `ServerOutput.user()` stays a **no-op** (echo-free): the client
  shows the typed line itself, the server never echoes it back — exactly as the TUI bridge does today.

So "multiple clients see the same session" falls out for free: they all subscribe to one hub.

## 6. Event protocol (the client/server contract)

JSON messages over WS, mirroring the `Output`/`Channel` seams and the existing `Bridge` event kinds.

**Client → Server**

| event | payload | meaning |
|---|---|---|
| `attach` | `{agent_id}` | subscribe this connection to an agent (also implied by the URL path) |
| `user.message` | `{text}` | a turn from the user |
| `command` | `{text}` | a slash command (`/status`, `/mood`, …) — handled engine-side before routing |

**Server → Client** (broadcast to all subscribers of the agent)

| event | payload | from |
|---|---|---|
| `agnika.message` | `{text, is_self, lead, model, is_thought, is_curiosity}` | `Output.agent` |
| `usage` | `{usage, latency}` | `Output.usage` |
| `notice` | `{text}` | `Output.notice` (command output, `[exit]`, …) |
| `status` | `{snapshot}` (the per-tick snapshot, §2) | `Output.status` |
| `snapshot` | `{status, recent_history[]}` | sent **once on attach** so a late client catches up |
| `tick` | `{tick}` | optional heartbeat (lets a client show liveness even when idle) |

**HTTP** (non-streaming): `GET /health`, `GET /agents` (list hosted `agent_id`s + status),
`GET /agent/{id}/history?limit=N` (transcript fetch for scrollback on attach).

The protocol is **additive over the Bridge kinds** — the TUI's existing `_render(event)` switch
already understands `agent`/`usage`/`notice`/`status`; the remote client reuses it almost verbatim.

## 7. Connection lifecycle

1. **Attach** — client opens `ws://…/agent/{agent_id}` (or sends `attach`). The server registers the
   connection on that agent's hub.
2. **Catch-up** — the server sends a one-shot `snapshot` (current `status` + the last N transcript
   lines) so a client that joined mid-session is immediately consistent.
3. **Live** — the client receives broadcast events and sends `user.message` / `command`.
4. **Disconnect** — the connection drops; **the agent keeps living** (ticking, drifting, maybe
   self-triggering). Nothing about the agent is tied to a client.
5. **Reconnect** — re-attach → fresh `snapshot` → resume. Self-triggered messages emitted while
   disconnected are in the transcript and arrive in the catch-up.

## 8. `agent_id` scoping (multi-agent from day one)

Even though v1.1 hosts one agent, **everything is keyed by `agent_id`** so v2's hub is additive, not a
reshape:

- **Routes:** `ws://…/agent/{agent_id}`, `GET /agent/{agent_id}/…`.
- **Persistence:** per-agent paths — `.kiln/{agent_id}/store.json`, `.kiln/{agent_id}/usage-ledger.jsonl`,
  and a per-agent `state/` (its own `needs.json`, `needs_model.yaml`, `mood.json`, `canon.md`,
  `prompts.md`). The default agent (`agnika`) maps to today's paths for a clean migration.
- **Runtime:** one `AgentRuntime` per id (its own loop, bus, brain). The host is just a `dict`.
- **Permission scope** (the field that later gates tools) is attached to each agent here but **not yet
  enforced** — enforcement is v1.3 (Tools). Designing the field now keeps v1.3 additive.

## 9. Scope of v1.1 (reviewed against v0)

Because v0 finished the seams, v1.1 is tightly bounded.

**In scope**
- Async server (FastAPI/Starlette + websockets); a `GET /health` + `GET /agents`.
- `AgentHost` + `AgentRuntime`, `agent_id`-scoped routes and persistence paths.
- **Two agents hosted concurrently — Agnika *and* the companion Pashu (§12)** — each a separate
  `AgentRuntime` with its own thread, bus, `state/{agent_id}/`, and `.kiln/{agent_id}/` (developing
  Pashu's data + connecting a client to it is part of this version, not a later one).
- `ServerChannel` + `ServerOutput` + a per-agent broadcast hub (the network `Bridge`).
- The WS **event protocol** above (serialise the existing `Output`/`status` events; echo-free).
- The tick loop runs server-side **with no client**; **non-blocking** model calls (per-agent thread).
- A **remote-mode TUI client** that connects over WS to a chosen `agent_id`, reusing `tui/render.py`
  (the in-process `Bridge` stays for local/dev).
- Tests against `MockBrain`: server ticks with no client; a client attaches and holds a turn; a
  **second client sees the same session**; **Agnika and Pashu tick concurrently with isolated state**;
  zero paid calls.

**Out of scope (later phases)**
- The explicit event-queue **FSM** and `idle/thinking/responding/cooling` states → **v1.2**.
- A typed-argument **tool registry** + per-agent permission **enforcement** → **v1.3**.
- **RAG** recall over transcripts → **v1.4**.
- The **web** client and the operator **multi-agent management** UI (add/start/stop/inspect agents from
  a panel — v1.1 registers Agnika + Pashu in config) → **v2**.
- Auth / TLS / multi-user accounts (single-operator localhost assumed for v1.x).

## 10. Implementation plan

Ordered, each step shippable and tested; later steps depend on earlier ones.

1. **Server scaffold.** Add a `server/` package: a FastAPI/Starlette app, `GET /health`, a WS
   endpoint stub that accepts a connection and echoes a `notice`. Add `fastapi`/`uvicorn`/`websockets`
   to deps (optional extra `[server]`, like `[tui]`). *DoD: `uvicorn server:app` runs; a WS client
   connects and gets a `notice`.*
2. **Network bus.** `ServerChannel(inbox_queue)` + `ServerOutput(hub)` implementing the v0 `Channel`/
   `Output` protocols; a `BroadcastHub` (subscribe/unsubscribe/broadcast, thread-safe). Contract tests
   pin them against the same expectations as `TuiChannel`/`TuiOutput`. *DoD: a fake engine drives the
   hub; events fan out to two subscribers; `user()` is a no-op.*
3. **AgentRuntime + AgentHost.** `AgentRuntime(agent_id)` starts `engine.run(channel=ServerChannel,
   output=ServerOutput, brain=LiveBrain)` on a thread; `AgentHost` is the `agent_id -> runtime`
   registry. Per-agent state/store paths (`.kiln/{agent_id}/…`). *DoD: the host starts one agent; it
   ticks with no client (status events flow to the hub).*
4. **WS event protocol + lifecycle.** Wire the WS endpoint: on attach, send `snapshot`; pump
   `user.message`/`command` → inbox; stream hub events → client; handle disconnect (agent lives on).
   Define the typed event (de)serialisers. *DoD: a test WS client attaches, sends a message, receives
   `agnika.message` + `usage`; a second client receives the same; disconnect doesn't stop ticks.*
5. **Non-blocking under load.** Verify a blocking `deep` call on one agent's thread doesn't stall the
   async layer (a second agent / a `/health` request stays responsive). Offload any accidental
   loop-thread blocking. *DoD: while agent A is mid-`deep` (mocked slow), `GET /health` and agent B
   keep responding.*
6. **HTTP helpers.** `GET /agents` (ids + status), `GET /agent/{id}/history?limit=N` from the store.
   *DoD: a fresh client renders recent scrollback on attach.*
7. **Second agent — Pashu (§12).** Author a minimal `state/pashu/` (its own `canon.md` + needs / mood
   / prompts) and register `AgentRuntime("pashu")` in the host at boot, beside Agnika. Both agents tick
   concurrently on separate threads with isolated `.kiln/{id}/` + `state/{id}/`; Pashu gets a narrower
   permission-scope field (enforced in v1.3). *DoD: the host runs Agnika + Pashu at once; a client
   attaches to `ws://…/agent/pashu` independently; a turn on one agent doesn't touch the other's
   needs/store.*
8. **Remote TUI client.** A `--remote ws://…/agent/{id}` mode for the Textual app (pick Agnika or
   Pashu): a `WsBridge` (or WS-backed `Channel`/`Output`) that feeds the existing `_render`; reuse
   `render.py` unchanged. *DoD: the TUI attached over WS to either agent holds a full turn and shows
   the status/needs panel, identical to local mode.*
9. **Docs + contracts.** Update `ARCHITECTURE.md` (promote the planned server/event-protocol bullets
   to "current"), pin the event protocol as a contract test. *DoD: ARCHITECTURE matches the shipped
   server; protocol contract test green.*

**Critical path:** 1 → 2 → 3 → 4 → 7 → 8 (5/6 land alongside). **Effort:** ~M each (the protocol, the
second agent, and the remote client are the bulk; the engine is untouched). **Model note:** every test
runs against
`MockBrain` — **zero paid calls**.

## 11. Risks & decisions

- **Thread vs async loop.** v1.1 deliberately keeps `run()` on a thread (the TUI's proven shape)
  rather than rewriting it async. The async/FSM rewrite is **v1.2** and can come without changing the
  protocol. *Decision: thread-per-agent for v1.1.*
- **One store, many writers.** Only the agent's own thread writes its store; clients never write.
  Multi-agent uses **separate** per-agent stores, so there is no cross-agent contention.
- **Echo-free invariant.** `ServerOutput.user()` must stay a no-op, or two clients double-render input.
  It is a contract test.
- **Backpressure.** A slow/dead client must not block the hub or the engine thread; broadcast is
  best-effort per connection (drop-and-disconnect a stuck socket), the engine never awaits a client.

## 12. Second agent: **Pashu** (in scope of v1.1)

v1.1 hosts **two** agents, not one: **Agnika** (`agent_id: "agnika"`, home / elevated) and **Pashu**
(`agent_id: "pashu"`, the first companion). Hosting a *real* second agent — not merely an
`agent_id`-scoped API — is **part of this version's DoD**: it is the proof that the host is a genuine
multi-agent hub. Both **developing** Pashu and **connecting** to it ship in v1.1.

**What it takes — and what it doesn't:**

- **Zero engine changes.** Pashu runs the *same* `engine.run()` on its *own* thread; the mind is
  identical code. Only its **data** differs. This is exactly what the `agent_id`-scoping of §8 buys.
- **Development (Pashu's data).** Its own `agent_id`-scoped paths: a `state/pashu/` with **its own**
  `canon.md` (Pashu's persona), `needs.json` (independent levels), `needs_model.yaml`, `mood.json`,
  `prompts.md`, and `.kiln/pashu/{store.json, usage-ledger.jsonl}`. v1.1 ships a **minimal Pashu canon**
  so a second agent genuinely runs (the full persona can deepen later); Agnika and Pashu never share
  state.
- **Connection (Pashu's bus + route).** A second `AgentRuntime("pashu")` runs beside
  `AgentRuntime("agnika")` (the host is just a `dict`); `ws://…/agent/pashu` exposes Pashu's own
  broadcast hub. A client attaches to Agnika **or** Pashu; both tick concurrently, and multiple clients
  on Pashu see Pashu's session exactly as for Agnika.
- **A narrower permission scope.** **Agnika = home / elevated**; **Pashu = a companion with a narrower
  scope**. The scope field is set per agent in v1.1; **enforcement** arrives with tools in **v1.3** —
  so Pashu's narrower scope only *bites* then.

**Still out of scope here (v2):** the operator **management UI** to add / start / stop / inspect agents
from a panel (§2.2 in the roadmap). In v1.1, Pashu is registered in **config** (a second `agent_id` +
its `state/pashu/` files, both started at server boot); v2 makes that operator-driven from the UI.

