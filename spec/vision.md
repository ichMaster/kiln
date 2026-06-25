# kiln — Roadmap / TODO

Detailed plan. Legend: ✅ done · 🟡 partial · ⬜ planned.

## Positioning

**kiln is a distinct approach — not a Lumi rebuild.** It shares the persona
(Lili → Agnika) with [`lumi`](file:///Users/Vitalii_Bondarenko2/development/lumi),
but its own focus is a **cheap, always-on tick _server_**: a persona that lives on
cheap local ticks and spends the expensive model **rarely** (needs-gated
Haiku/Opus routing), exposed as a server clients attach to — so the tick server
(1.1) is the point, not a late add-on. Two guiding constraints: **cost** (cheap by
default, conserve Opus) and the **tick loop** (the engine keeps living between
turns). Lumi is the mature reference for the *persona pieces* (TUI, RAG, memory,
inner monologue) — port those; the cheap tick-server core is kiln's own.

**North star:** kiln grows into a **hub for agents** — one cheap tick-server
runtime hosting *many* agents (Lumi-like personas) as pluggable minds, not just
Agnika. The needs/tick/server core is the shared substrate; each agent brings its
own canon, memory, tools, and **permission scope**. Agents differ by role:
**Agnika is the _home agent_** — always connected, **elevated permissions** (broad
tools / system / home access); a Lumi-style companion runs at a lower scope. So
the server (1.1) is an **agent host** with **per-agent permissions**, and the later
items (multi-user, admin) generalize to **multi-agent**.

## Reference projects (`~/development/`)

Lumi already builds most persona features (table below) — prefer porting proven
pieces over inventing. kiln's own contribution is the cheap tick-server core.

| Project | Path | What it is | Used for |
|---|---|---|---|
| **lumi** | `~/development/lumi` | full persona engine; Textual TUI; `core` never depends on an interface | TUI (0.3/0.4), RAG (1.4), personality (2.x), client/server (1.1) |
| **clay** | `~/development/clay` | voxel-world *body* on fast ticks; swappable stub→Lili brain | Games (1.5) |
| **silt** | `~/development/silt` | always-on server, 2D life world (cellular automata → Lenia), web + API client | Games (1.5) + server pattern (1.1/3.x) |
| **checkers** | `~/development/claude-code-test/russian-checkers` | russian checkers (Node) | Games (1.5) |
| **mote**, **pip** | — | specced in `lumi/specification/features/ukrainian/games/` (`mote.md`, `pip.md`); no repo found | Games (1.5) |

Key principle to copy from Lumi: **the core is built first and never depends on
an interface.** Lumi's `tui/bridge.py` is an **echo-free file-bus** (inbox/outbox
FIFO, no Textual dependency) — that's both the fix for kiln's typed-text-echo
problem and the substrate for multiple clients (TUI, Telegram, voice).

## v0.0 — Prototype

### 0.1 Basic prototype — ✅
Tick loop, needs (drift/satiation), self-triggers (hysteresis+cooldown), two
brains (Haiku via SDK / Opus via `claude -p`), `chat|think|tools` routing, slash
commands, `memory.md` summaries, `history/*.json` transcripts, `.env` config,
canon persona, colored output. Modules: config/history/usage/memory/commands/engine.

### 0.2 Professional structure & tests (refactor) — ⬜
Turn the prototype into a maintainable, **tested** package before growing features
(so every later phase ships with tests + green CI).
- **Package** — flat modules → a `kiln/` package + `pyproject.toml` (metadata, deps,
  tool config) + a console entry. (Lumi: `pyproject.toml` + `uv`.)
- **Brain seam** — both brains behind one **mockable** interface (`chat_reply` /
  `deep_reply` become implementations) so tests run against a **mock brain** — no
  paid calls. (Lumi's `LLMClient` seam.)
- **Tests (`tests/`, pytest)** — unit (drift/satiation, `classify`,
  `select_self_trigger`, `needs.json` round-trip, usage parsing, catch-up drift),
  contract (the seams: `needs.json`, `respond()`, the usage dict), a mock-brain
  integration turn. Dry-run demo stays a smoke test.
- **ruff + CI** (`.github/workflows/ci.yml` on push; `main` green) + `VERSION` /
  `RELEASE.txt` for `/release-version`.

### 0.3 Implement TUI — ⬜
Replace `print` + `StdinChannel` with a real TUI. **Match Lumi's TUI.**
- **Framework: Textual** (Lumi pins `textual>=0.80`). Port from `lumi/tui/app.py`.
- Adopt Lumi's **bridge pattern** (`lumi/tui/bridge.py`): an echo-free inbox/outbox
  bus between the engine and the UI → fixes the typed-text-vs-output interleaving
  and lets Telegram/voice clients reuse the same bus later.
- Carry over speaker colors/labels (`you` / `Agnika` / tech line).

### 0.4 TUI enhancements — status, tokens, statistics, copy/paste — 🟡
- **status panel** — live needs, mode, hottest need (today only `/status`).
- **tokens** — per-turn counter (have the tech line) surfaced in the UI.
- **statistics** — per-session: total tokens, turns/branch, cost, avg latency.
- **copy/paste** — copy replies / code blocks. (Textual gives most of this.)

### 0.5 Tokens report — 🟡
Per-execution `· model · in→out tok (total)` done (`usage.py`). TODO: session
aggregate (sum, by branch, `$` from the CLI's `total_cost_usd`) + persist it.

## v1.0 — Engine

### 1.1 Client-server: engine = WebSocket/HTTP server, TUI = client — ⬜
Lumi's **v2 "server platform"** — specced there, not yet built, so this is mostly
greenfield (silt's always-on-server + web-canvas + API-client is a working example).
- Tick loop → **async server** (WS/SSE); TUI and the web UI (3.1) are both clients.
- Engine keeps living (ticks, self-triggers) with no client attached; multi-client.
- **Agent host (north star):** one server runs multiple agents on their own tick
  loops; a client attaches to a chosen agent/session. Design the server around an
  `agent_id` **and a per-agent permission scope** from the start so multi-agent is
  additive, not a rewrite.
- Event protocol mirrors the FSM (1.2). Offload blocking model calls to tasks so
  the loop never freezes (chat / `claude -p` are synchronous today).
- Stack: FastAPI/Starlette + websockets (or aiohttp, as silt uses).

### 1.2 State machine (FSM, events, queue) — ⬜
Procedural tick → **event-driven FSM**. States: idle / thinking / responding /
cooling. Events: `user_message`, `tick`, `need_crossed`, `model_done`, `command`.
One queue; FSM consumes one at a time (makes today's "input > self-trigger > idle"
explicit). Natural backend for 1.1 (events = WS messages). cf. `lumi/core/cycle.py`.

### 1.3 Tools — ⬜ *(was the duplicate "1.2")*
Agnika's own tools (function calling), separate from Claude Code's. The `tools`
branch already exists (deep + `--allowedTools`); add a typed-argument registry.
Tools are **permission-scoped per agent** — Agnika (home agent) gets broad
system/home access; companion agents a narrow set. cf. Lumi's file tool /
`imagetool.py` / `news.py`.

### 1.4 RAG — ⬜
Index the accumulated `history/*.json` and recall relevant fragments per turn
(precise quotes alongside the `memory.md` summaries). **Port from Lumi**, which
already has semantic recall in v0: `core/embedder.py`, `core/chunking.py`,
`core/memory.py`. Open: embedding model, store (sqlite-vss / chroma), chunking
granularity, when to inject.

### 1.5 Games — ⬜ *(was 1.4)*
Agnika's **core as a swappable brain** driving world-bodies / games (clay's
pattern: the body sends needs + surroundings, the brain returns an action; the
brain swaps to Lili/kiln with no body changes). Specs: `lumi/.../games/`.
- **checkers** (`claude-code-test/russian-checkers`) — first concrete game: board,
  moves, TUI render.
- **clay** (voxel world), **silt** (Lenia) — tick-worlds; integrate kiln as the brain.
- **mote**, **pip** — specced only; build later.

## v2.0 — Personality (autonomous inner life) — *Lumi v1*

### 2.1 Needs review — ⬜
Fix `rest` semantics (today `deep` *lowers* it though hard work should tire —
open). Recalibrate DRIFT/SATIATION/thresholds. Maybe more needs (boredom,
attachment) + a mood state. cf. Lumi `core/mood.py`, `emotion.py`, `biorhythm.py`.

### 2.2 Plans — ⬜
Agnika forms goals/intentions held across turns/sessions (intent, steps, status in
state/memory). FSM events: create/advance/complete; a self-trigger can push action.

### 2.3 Memories — ⬜
Structured memory beyond `memory.md`: episodic (what happened) + semantic
(facts/preferences). Written during conversation, recalled via RAG (1.4). Lumi has
a three-layer, **user-scoped** memory model (`core/memory.py`) worth adopting now
so the multi-user server (1.1) is additive, not a rewrite.

### 2.4 Inner monologue — ⬜
Background "inner voice": between turns Agnika thinks to herself (cheap-model loop
on input-free ticks → reflect, update plans/mood, decide whether to speak). Some
surfaces (debug/panel), some stays in state. **Port from Lumi** `core/inner_voice*`
+ `nudge.py`. Sits on the FSM (1.2) + self-triggers; cost control is key.

## v3.0 — Web interface — *Lumi v2.4*

### 3.1 Web UI — ⬜
Web client to the same WS/HTTP server (1.1); chat + status/stats panel, same API
as the TUI. silt's web canvas is a working reference.

### 3.2 Admin panel — ⬜
Manage the **hosted agents** (add / configure / start / stop), each with its own
canon, memory, and knobs. Live knob tuning (`.env`/calibration), inspect/edit
memory, browse transcripts/RAG, stats + cost per agent.
