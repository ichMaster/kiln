# Roadmap — kiln

Versions built in order, complexity added per version, **cheap-first** and
**core-first** (the tick core never depends on an interface). Each phase lists a
**Goal**, **Tasks**, and a **Definition of Done (DoD)**. Legend: ✅ done · 🟡
partial · ⬜ planned. Lumi (`~/development/lumi`) is the reference to port persona
pieces from; the cheap tick-server and the agent hub are kiln's own.

## v0 — Prototype & TUI

### 0.1 Basic prototype — ✅
**Goal:** a living persona on a cheap tick loop in the terminal.
**Done:** tick loop + needs (drift/satiation), self-triggers (hysteresis+cooldown),
two brains (Haiku/SDK, Opus/`claude -p`) with cost routing, slash commands,
`memory.md` summaries, `history/*.json` transcripts, `.env` config, canon (Agnika),
colored output. Modules: config/history/usage/memory/commands/engine.
**DoD:** `python engine.py` holds a live dialogue and runs a deterministic dry-run
demo; the agent self-triggers; state persists across runs.

### 0.2 Professional structure & tests (refactor) — ⬜
**Goal:** turn the working prototype into a maintainable, **tested** Python package
before growing features — so every later phase ships with tests and a green CI.
**Tasks:**
- **Package layout** — move the flat modules into a package (`kiln/`: `kiln/config.py`,
  `kiln/engine.py`, …) with a thin console entry (`kiln = kiln.__main__:main`).
  Promote `requirements.txt` into a `pyproject.toml` (project metadata, runtime +
  dev deps, tool config). Lumi's `pyproject.toml` + `uv` is the model.
- **Brain seam (mockable)** — put both brains behind one thin interface
  (`Brain` / `LLMClient` seam); `chat_reply` (Haiku/SDK) and `deep_reply` (Opus/CLI)
  become implementations the core uses **through the seam, never the SDK/CLI
  directly**. The key unlock: tests run against a **mock brain** — zero paid calls.
  Mirrors Lumi's `LLMClient` seam.
- **Tests (`tests/`, pytest).** Unit: drift/satiation, `classify`/`turn_weight`,
  `select_self_trigger` (hysteresis/cooldown), `needs.json` load/save round-trip,
  usage token parsing, catch-up-drift math, `_cli_error_detail`. Contract: the seams
  (`needs.json` shape, `respond()` return, the usage dict). Integration: a full turn
  `respond()` against the mock brain. The dry-run demo stays a smoke test.
- **Lint / format / types** — ruff (lint + format) in `pyproject`; optional mypy on
  the typed seams.
- **CI** — `.github/workflows/ci.yml`: ruff + pytest on every push/PR; `main` stays
  green. Model mocked — never a paid API in CI.
- **Hygiene** — move the `__main__` demo out of `engine.py`; a thin output seam toward
  the TUI bus (0.3); add `VERSION` + `RELEASE.txt` so `/release-version` works.
**DoD:** `pip install -e .` installs the `kiln` package and the `kiln` command runs;
`pytest` is green against a mock brain (zero paid calls); `ruff check` is clean; CI
runs on push; the core depends only on the brain seam.

### 0.3 Implement TUI — ⬜
**Goal:** a real terminal UI, matching Lumi's.
**Tasks:**
- **`tui/` client package + launcher** — create the `tui/` dir (a new dir as the
  version begins; the client holds **no agent logic**); add a way to start it
  (`kiln --tui` flag or a `tui` console entry) while keeping the plain console mode.
- **Textual app** (port `lumi/tui/app.py`) — fixed input line at the bottom, a
  scrollable reply log above; carry over the `you`/`Agnika`/tech colors as Textual
  styles (today's ANSI from `usage.py`).
- **`TuiOutput` sink** — implement the v0.2 **`Output` seam** (`user`/`agent`/
  `usage`/`notice`) to push into the scrollable log instead of `print`, and pass it
  to `run(output=…)`. Route the remaining slash-command output (`commands.py`, still
  on `print`) through the same bus so the UI owns all rendering.
- **TUI input channel** — a `Channel` (`poll()`) backed by the input widget (the UI
  mirror of `StdinChannel`), so the tick loop reads typed lines from the UI.
- **Echo-free bridge** (port `lumi/tui/bridge.py`) — an inbox/outbox bus decoupling
  the engine's tick loop from the Textual UI thread; fixes typed-text-vs-output
  interleaving and is the **shared bus for later clients** (web, server). The engine
  writes only its own replies (no echo of the user's typed line).
- **Loop alongside the UI** — drive the tick loop concurrently with the Textual
  event loop (background thread or asyncio task) so the agent keeps ticking and can
  **self-trigger** while the UI renders; model calls must not freeze the UI.
- **Tests** — contract tests for `TuiOutput` and the TUI `Channel` against the v0.2
  seams (mock brain, **zero paid calls**); a Textual pilot smoke if practical. CI
  stays green.
**DoD:** chat in a Textual TUI with no input/output interleaving; the engine writes
only its own replies to the bus (echo-free); the tick loop keeps running (self-
triggers still fire) while the UI is open.

### 0.4 TUI enhancements (status, tokens, statistics, copy/paste) — 🟡
**Goal:** status, tokens, statistics, copy/paste in the UI.
**Tasks:** live status panel (needs, mode, hottest need); per-turn token line
surfaced; per-session stats (total tokens, turns/branch, cost, avg latency); copy
replies/code.
**DoD:** the panel updates live; stats are visible; replies copy.

### 0.5 Tokens report — 🟡
**Goal:** per-turn and per-session cost visibility.
**Done:** per-execution `· model · in→out tok (total)` (`usage.py`).
**Tasks:** session aggregate (sum, by branch, `$` from the CLI's `total_cost_usd`);
persist for analytics.
**DoD:** a session shows total tokens + cost by branch.

## v1 — Engine (the tick-server & hub foundation)

### 1.1 Tick-server: engine = WS/HTTP server, clients attach — ⬜
**Goal:** the engine is an always-on server; TUI and (later) web are clients; the
foundation of the agent **hub**.
**Tasks:** async server (WS/SSE); the tick loop runs server-side with no client
attached; offload blocking model calls to tasks so the loop never freezes; design
around an **`agent_id` + per-agent permission scope** from the start (multi-agent
additive); event protocol mirrors the FSM (1.2). Stack: FastAPI/Starlette +
websockets (silt is a working server example).
**DoD:** the server ticks with no client connected; a TUI client attaches over WS
and holds a turn; a second client sees the same session; the API is `agent_id`-scoped.

### 1.2 State machine (FSM, events, queue) — ⬜
**Goal:** an event-driven core behind the server.
**Tasks:** states (idle/thinking/responding/cooling); one event queue (input,
ticks, self-triggers) consumed one at a time; makes "input > self-trigger > idle"
explicit; events = WS messages. cf. `lumi/core/cycle.py`.
**DoD:** the loop is an FSM driven by a queue; the server emits the same events.

### 1.3 Tools — ⬜
**Goal:** Agnika's own permission-scoped tools.
**Tasks:** a typed-argument tool registry, separate from Claude Code's; **per-agent
permission scope** (Agnika = broad system/home; companions narrow); e.g.
time/notes/RAG-search/start-a-game. cf. Lumi's file/imagetool/news.
**DoD:** Agnika calls a registered tool within her scope; a companion agent is
denied an out-of-scope tool.

### 1.4 RAG — ⬜
**Goal:** exact recall over past conversations.
**Tasks:** embed `history/*.json` → vector store; recall top-K relevant fragments
into the turn, deduped against the window, capped. Port from Lumi
(`core/embedder.py`, `chunking.py`, `memory.py`). Decide embedder (local?), store
(sqlite-vss/chroma), chunking, when to inject.
**DoD:** `/recall` returns relevant past lines; automatic RAG injects them per turn.

### 1.5 Games — ⬜
**Goal:** kiln's core as a swappable brain driving world-bodies / games.
**Tasks:** a brain↔body interface (clay's pattern: body sends needs + surroundings,
brain returns an action); first concrete game — **checkers**
(`claude-code-test/russian-checkers`): board, moves, TUI render; then integrate
**clay** (voxel) / **silt** (Lenia). Specs in `lumi/.../games/`.
**DoD:** Agnika plays a full checkers game in the TUI; the same brain drives one
world-body.

## v2 — Personality (autonomous inner life)

### 2.1 Needs review — ⬜
**Goal:** a coherent, calibrated needs model.
**Tasks:** fix `rest` semantics (deep should tire, not rest — open); recalibrate
drift/satiation/thresholds on real dialogue; maybe more needs (boredom, attachment)
+ a mood state. cf. Lumi `mood/emotion/biorhythm`.
**DoD:** needs behave intuitively over a long session; the `rest` inconsistency resolved.

### 2.2 Plans — ⬜
**Goal:** the agent forms and holds goals.
**Tasks:** plan structure (intent/steps/status) in state/memory; FSM events
create/advance/complete; a self-trigger can push action on a plan.
**DoD:** Agnika sets a goal, references it across turns/sessions, and acts on it.

### 2.3 Memories — ⬜
**Goal:** structured, durable memory beyond summaries.
**Tasks:** episodic (what happened) + semantic (facts/preferences), written during
conversation, recalled via RAG. Adopt Lumi's three-layer, **agent/user-scoped**
memory now so the hub is additive.
**DoD:** the agent recalls durable facts and impressions, scoped per agent/user.

### 2.4 Inner monologue — ⬜
**Goal:** the agent thinks between turns.
**Tasks:** a quiet cheap-model loop on input-free ticks (reflect, update
plans/mood, decide whether to speak); some surfaces (debug/panel), some stays in
state. Port from Lumi (`core/inner_voice*`, `nudge.py`); sits on the FSM +
self-triggers; cost-capped.
**DoD:** between turns Agnika produces internal thoughts that update her state and
occasionally prompt a self-initiated message.

## v3 — Web & multi-agent hub

### 3.1 Web UI — ⬜
**Goal:** a browser client to the same server.
**Tasks:** web client over the WS/HTTP API (chat + status/stats), same API as the
TUI; silt's web canvas is a reference.
**DoD:** chat with an agent in the browser, with the status panel.

### 3.2 Admin panel & multi-agent — ⬜
**Goal:** manage the hosted agents.
**Tasks:** add/configure/start/stop agents, each with its own
canon/memory/tools/**permission scope**; live knob tuning; inspect/edit memory;
browse transcripts/RAG; per-agent stats + cost. Agnika (home, elevated) and
companion agents coexist.
**DoD:** run two agents at once (Agnika + a companion) on one hub; manage each from
the panel.
