# Feature: Server architecture & client/server separation (v1.1 + v1.2)

Status: **design**. Spans two phases: **v1.1** — *Tick-server (the engine becomes a WS/HTTP server;
one agent, Agnika)* — and **v1.2** — *Companion agent Pashu (a real second agent on the same host)*.
Companion: [ROADMAP §1.1–1.2](../ROADMAP.md), [ARCHITECTURE.md](../ARCHITECTURE.md). Ukrainian
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
  **FSM** that makes this explicit is **v1.3**; v1.1 only needs the thread + non-blocking transport.

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
  enforced** — enforcement is v1.5 (Tools). Designing the field now keeps v1.5 additive.

## 9. Scope: v1.1 (server) + v1.2 (Pashu), reviewed against v0

Because v0 finished the seams, both phases are tightly bounded. **v1.1** = the single-agent server;
**v1.2** = the second agent. Each item below is tagged with its phase.

**In scope — v1.1 (single-agent server)**
- Async server (FastAPI/Starlette + websockets); a `GET /health` + `GET /agents`.
- `AgentHost` + `AgentRuntime`, `agent_id`-scoped routes and persistence paths (the host is N-capable
  from the start, but v1.1 runs **one** agent — Agnika).
- `ServerChannel` + `ServerOutput` + a per-agent broadcast hub (the network `Bridge`).
- The WS **event protocol** above (serialise the existing `Output`/`status` events; echo-free).
- The tick loop runs server-side **with no client**; **non-blocking** model calls (per-agent thread).
- A **remote-mode TUI client** that connects over WS to a chosen `agent_id`, reusing `tui/render.py`
  (the in-process `Bridge` stays for local/dev).
- Tests against `MockBrain`: server ticks with no client; a client attaches and holds a turn; a
  **second client sees the same session**; zero paid calls.

**In scope — v1.2 (companion Pashu, §12)**
- A **second agent, Pashu**, hosted concurrently with Agnika — its own `AgentRuntime` (thread, bus,
  `state/pashu/`, `.kiln/pashu/`), a minimal authored Pashu canon, and `ws://…/agent/pashu`.
- **Agnika and Pashu tick concurrently with isolated state**; `agent_id` isolation is contract-tested.

**Out of scope (later phases)**
- The explicit event-queue **FSM** and `idle/thinking/responding/cooling` states → **v1.3**.
- **RAG** recall over transcripts → **v1.4**.
- A typed-argument **tool registry** + per-agent permission **enforcement** → **v1.5**.
- The **web** client and the operator **multi-agent management** UI (add/start/stop/inspect agents from
  a panel — v1.2 registers Pashu in config) → **v2**.
- Auth / TLS / multi-user accounts (single-operator localhost assumed for v1.x).

## 10. Implementation plan

Ordered, each step shippable and tested; later steps depend on earlier ones. Steps 1–8 are **v1.1**
(the single-agent server); step 9 is **v1.2** (the companion Pashu).

**v1.1 — single-agent server:**

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
7. **Remote TUI client.** A `--remote ws://…/agent/{id}` mode for the Textual app: a `WsBridge` (or
   WS-backed `Channel`/`Output`) that feeds the existing `_render`; reuse `render.py` unchanged. *DoD:
   the TUI attached over WS holds a full turn and shows the status/needs panel, identical to local
   mode.*
8. **Docs + contracts.** Update `ARCHITECTURE.md` (promote the planned server/event-protocol bullets
   to "current"), pin the event protocol as a contract test. *DoD: ARCHITECTURE matches the shipped
   server; protocol contract test green.*

**Then v1.2 — companion Pashu:**

9. **Second agent — Pashu (§12).** Author a minimal `state/pashu/` (its own `canon.md` + needs / mood
   / prompts) and register `AgentRuntime("pashu")` in the host at boot, beside Agnika. Both agents tick
   concurrently on separate threads with isolated `.kiln/{id}/` + `state/{id}/`; Pashu gets a narrower
   permission-scope field (enforced in v1.5). The remote TUI (step 7) can attach to either agent.
   *DoD: the host runs Agnika + Pashu at once; a client attaches to `ws://…/agent/pashu` independently;
   a turn on one agent doesn't touch the other's needs/store.*

**Critical path:** v1.1: 1 → 2 → 3 → 4 → 7 (5/6 land alongside, 8 last); then v1.2: 9 (Pashu). **Effort:**
v1.1 ~M (the protocol + remote client are the bulk; the engine is untouched), v1.2 ~S–M (the host is
already N-capable, so it's mostly authoring Pashu's data + concurrency tests). **Model note:** every
test runs against
`MockBrain` — **zero paid calls**.

## 11. Risks & decisions

- **Thread vs async loop.** v1.1 deliberately keeps `run()` on a thread (the TUI's proven shape)
  rather than rewriting it async. The async/FSM rewrite is **v1.3** and can come without changing the
  protocol. *Decision: thread-per-agent for v1.1.*
- **One store, many writers.** Only the agent's own thread writes its store; clients never write.
  Multi-agent uses **separate** per-agent stores, so there is no cross-agent contention.
- **Echo-free invariant.** `ServerOutput.user()` must stay a no-op, or two clients double-render input.
  It is a contract test.
- **Backpressure.** A slow/dead client must not block the hub or the engine thread; broadcast is
  best-effort per connection (drop-and-disconnect a stuck socket), the engine never awaits a client.

## 12. Second agent: **Pashu** (v1.2 — the phase right after the server)

**v1.2** adds **Pashu** (`agent_id: "pashu"`, the first companion) **beside** Agnika (`agent_id:
"agnika"`, home / elevated), so the host runs **two** agents. Hosting a *real* second agent — not
merely an `agent_id`-scoped API — is **v1.2's DoD**: the proof that the host is a genuine multi-agent
hub. Both **developing** Pashu and **connecting** to it ship in v1.2 (the immediately following phase),
on top of the v1.1 single-agent server.

**What it takes — and what it doesn't:**

- **Zero engine changes.** Pashu runs the *same* `engine.run()` on its *own* thread; the mind is
  identical code. Only its **data** differs. This is exactly what the `agent_id`-scoping of §8 buys —
  v1.1 builds the N-capable host, v1.2 fills the second slot.
- **Development (Pashu's data).** Its own `agent_id`-scoped paths: a `state/pashu/` with **its own**
  `canon.md` (Pashu's persona), `needs.json` (independent levels), `needs_model.yaml`, `mood.json`,
  `prompts.md`, and `.kiln/pashu/{store.json, usage-ledger.jsonl}`. v1.2 ships a **minimal Pashu canon**
  so a second agent genuinely runs (the full persona can deepen later); Agnika and Pashu never share
  state.
- **Connection (Pashu's bus + route).** A second `AgentRuntime("pashu")` runs beside
  `AgentRuntime("agnika")` (the host is just a `dict`); `ws://…/agent/pashu` exposes Pashu's own
  broadcast hub. A client attaches to Agnika **or** Pashu; both tick concurrently, and multiple clients
  on Pashu see Pashu's session exactly as for Agnika.
- **A narrower permission scope.** **Agnika = home / elevated**; **Pashu = a companion with a narrower
  scope**. The scope field is set per agent in v1.2; **enforcement** arrives with tools in **v1.5** —
  so Pashu's narrower scope only *bites* then.

**Still out of scope here (v2):** the operator **management UI** to add / start / stop / inspect agents
from a panel (§2.2 in the roadmap). In v1.2, Pashu is registered in **config** (a second `agent_id` +
its `state/pashu/` files, both started at server boot); v2 makes that operator-driven from the UI.

## 13. Shared vs isolated: the multi-agent boundary

One process, N agents. Rule of thumb: **the agent's *mind* and *memory* are isolated per `agent_id`;
*process infrastructure* and *operator credentials* are shared.** A turn or self-trigger on one agent
must never touch another's state — that is v1.2's contract-tested DoD.

**Isolated — one per `agent_id`** (its `AgentRuntime` owns all of it; a turn on one never moves another's):

| Isolated | What | Where |
|---|---|---|
| Thread | own `engine.run()` loop — ticks, drifts, self-triggers independently | in-memory |
| Need **levels** | live `State` (drift/satiation) + `TriggerBook` (hysteresis/cooldowns) | `.kiln/{id}/needs.json` (+ runtime) |
| Memory / store | sessions, messages, summaries, facts, thoughts; session id + rotation | `.kiln/{id}/store.json` |
| Usage | token ledger + report (per-agent cost) | `.kiln/{id}/usage-*` |
| Persona | canon (voice) + self-trigger prompts | `state/{id}/canon.md`, `prompts.md` |
| Calibration | the need **model** (drift/satiation/triggers) + mood bands/cues | `state/{id}/needs_model.yaml`, `mood.json` |
| Bus | inbox queue + broadcast hub (its own attached clients / session) | in-memory |
| Permission scope | the field that gates tools (Agnika broad, Pashu narrow) | per-agent |

**Shared — one per server process** (by design):

| Shared | What | Why it's safe to share |
|---|---|---|
| Process + event loop | the single Python process; the async FastAPI/uvicorn layer | stateless re: any agent — routes by `agent_id`, pumps each agent's bus |
| Host registry | `AgentHost` (`agent_id -> AgentRuntime` dict) | the broker; holds runtimes, owns no agent state — it's also the switchboard of §14 |
| Credentials | `ANTHROPIC_API_KEY` + the logged-in `claude` CLI | **one key/CLI for all agents → shared billing**; keys never leave the server |
| Launch flags | `KILN_SERVE`, `server.yaml` host/port | process-level, not a property of any agent |

**The real v1.2 work — de-globalizing config.** The per-agent *files* above are reachable by path
(`AgentPaths`), but v1.1's code loads persona/calibration/tunables as **module-level constants at
import** — `CHAT_MODEL`, `DRIFT`/`SATIATION`/`NEED_TRIGGERS`, the mood bands. Today **canon, prompts,
store, need levels, and the ledger are already `agent_id`-scoped; the need-model, mood, and the
`config.yaml` tunables are still global.** So v1.2's substantive task isn't authoring Pashu's files
(trivial) — it's **moving those loads from module globals to per-`AgentRuntime`** (an `AgentConfig`
resolved when the runtime is built and carried into `engine.run`). Until that lands, a second agent
would silently inherit Agnika's need-model/mood/models. The §1.2 isolation test (a turn on one agent
never moves the other's needs) is exactly what proves the de-globalization is complete.

> **Open decision (v1.2):** the operational `config.yaml` tunables (models, `tick_seconds`,
> `think_threshold`) — keep **shared** process-wide, or scope them **per-agent**
> (`state/{id}/config.yaml`)? Per-agent is the cleaner story (Pashu could run a cheaper model or a
> slower tick); shared is less to wire. The persona/calibration (canon, prompts, need-model, mood) is
> **per-agent regardless.**

## 14. Inter-agent communication

In version 1.2 each agent lives on its own. Agnika and Pashu have no channel between them at all, and
that is deliberate: the whole point of the phase is to prove that they don't interfere with each other.
Even so, the architecture is already ready for agents to talk to one another whenever we decide to allow
it. It won't take much, because the same machinery the user uses to talk to an agent works just as well
between two agents. The capability itself arrives later — it gets its own phase, 1.6, built on the tool
system from 1.5 — since "send a message to another agent" is an action, and actions are governed by
permissions. (That same phase also grows the TUI so one window can hold several agents at once.)

The key idea is that the host always sits in the middle. The host is the component that holds all the
agents together, so it is the one that knows how to find any of them and hand a message across. There
are three ways agents can communicate, from the simplest and safest to the most involved.

**The first way is a direct message through the host.** Suppose Agnika wants to say something to Pashu.
She doesn't reach into Pashu directly; she hands the message to the host and says who it is for. The
host drops that message into Pashu's ordinary inbox — the very same queue the user's messages arrive in
— and simply marks that it came from another agent rather than from a person. Pashu then handles it like
any normal turn, but knows it came from a peer. This is the safest way, because the agents share
nothing: the message is passed as a copy, so one agent cannot corrupt another's state. And because
sending a message to another agent is its own action, it will be offered as a tool, which means it can
be allowed or denied per agent — granted to Agnika, who has broad permissions, and withheld from a
locked-down companion. This arrives in phase 1.6.

**The second way is observation.** Instead of writing to a specific agent, one agent can simply listen
to what another says out loud. The host subscribes Pashu to Agnika's stream of messages, and Pashu
hears everything she says, as if they were in the same room. This is one-way and read-only — nobody
changes anyone else's state. It is useful when you want one agent to react in the background to another.
This is also part of phase 1.6.

**The third way is shared memory, and it is deferred.** Here both agents don't just read but also write
into one shared store — for example a shared picture of the world that they update together. This is the
only way that brings back the problem we discussed earlier: when two agents write into the same file at
once, they overwrite each other's changes. So this way has to wait for a proper database (PostgreSQL,
from phase 1.7) that can manage simultaneous writes correctly, rather than a shared JSON file.

Why do we prefer passing messages over sharing memory? Because in the first two ways each agent stays
the only one writing to its own state, and the agents only ever exchange copies through the host. That
keeps the agents isolated, keeps concurrent access simple, and means no agent can corrupt another's
data. The third way gives up that safety, which is exactly why it depends on the database. So the
natural order is: in 1.2 the agents are isolated; in 1.6 direct messages and observation appear, as a
permission-controlled tool, and one TUI can hold several agents at once; shared memory comes only after
the move to PostgreSQL in 1.7; and that shared substrate then powers the group-chat rooms in 1.8, where
several agents and the user share one conversation and everyone can answer to all.

One last point — guarding against endless loops. Finding the right agent to message is easy, since each
one already has its own name. The real worry is a runaway exchange: if Agnika writes to Pashu, who
replies to Agnika, who writes back again, the two could in principle loop forever — and every one of
those messages is a real, paid call to the model. The reassuring part is that kiln needs no special
anti-loop machinery for this; two mechanisms the agent already has take care of it.

The first is the response speed, which is set in the config. An agent never replies instantly — it
speaks on the beat of its tick loop, and how fast that beat runs is a config setting. So even a brisk
back-and-forth unfolds at that deliberate, throttled pace rather than at machine speed.

The second is the agent's need for rest. kiln already treats rest as one of the agent's needs: the more
it does — writing messages included — the more that need builds up, and once it climbs high enough the
engine makes the agent fall quiet and rest instead of producing more. An agent drawn into a long
exchange therefore simply tires, exactly as it would during any other busy stretch. This isn't a rule
invented for agent-to-agent chat; it's the same needs model that governs all of the agent's behaviour,
doing its job here too.

