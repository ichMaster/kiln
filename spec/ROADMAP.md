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

### 0.4 TUI enhancements (Lumi-like layout: status bar, needs panel, stats) — 🟡
**Goal:** bring the v0.3 TUI up to a **Lumi-like layout** — a top status bar, a live
**needs/thresholds panel** (kiln's analogue of Lumi's reasoning box), a scrollable
chat log, an input box, and a command/keybinding footer scoped to *implemented*
features. Lumi's header is the visual reference; kiln drops what it doesn't have yet
(sound, style, emotions/mood).
**Tasks:**
- **Status bar (top, Lumi-style header, minus sound/style/emotions).** Two lines,
  updated live each tick:
  - line 1 — `status:` (online / thinking / idle), current model + branch
    (haiku/opus), and the hottest need;
  - line 2 (`stats:`) — **connection statistics + tokens:** last-turn tokens +
    latency, session totals (turns, total tokens, by branch), average latency, and
    cache reads where available.
- **Needs / thresholds panel (replaces the "Thinking" box).** A live box, refreshed
  each tick, showing every need's current level vs its trigger threshold (a bar +
  number per need), the hottest need, and any active self-trigger cooldown. This is
  kiln's native internal-state view — where Lumi shows model reasoning, kiln shows the
  motivational substrate driving it.
- **Status/state event on the bus.** Extend the `Output`/bridge event protocol with a
  periodic `status` event (needs + thresholds + session stats snapshot) the engine
  emits each tick, so the status bar and needs panel update without the client reading
  engine state directly. (Foreshadows the v1.1 `status` WS event; ARCHITECTURE +
  contract test.)
- **Chat log polish.** `you` / `Agnika` / tech colors, self-trigger replies marked,
  and a per-turn token + latency line under each reply.
- **Input box + command hints — implemented only.** The fixed input with a hint line
  listing **only the slash commands kiln actually has** (`/status /needs /self /ask
  /clear /help /quit`); no Lumi commands kiln lacks (`/style /mood
  /model /biorhythm …`).
- **Keybinding footer — implemented only.** `Ctrl+Q` quit, `Ctrl+Y` copy last reply,
  `Ctrl+O` copy all, `Ctrl+L` clear screen. (Dictate / sound / palette / mouse-select
  deferred until they exist.)
- **Tests.** The status bar and needs panel render correctly from a given `State` +
  stats snapshot (mock brain); the `status` event carries needs/thresholds/stats; copy
  actions place reply text on the clipboard; the command-hint list matches the
  actually-registered commands. CI stays green.
**DoD:** the TUI shows a live **status bar** and a **needs/thresholds panel** that
update each tick; per-turn tokens + latency and session totals are visible; the
command-hint line and keybinding footer list **only implemented features**; copy
reply / copy all works. Full cost/`$` analytics stays in v0.7.

### 0.5 Short memory — unified `.kiln/store.json` — ⬜
**Goal:** replace the scattered, append-only persistence (`state/memory.md` +
`history/session-*.json`, no pruning) with a single **`.kiln/store.json`** — Lumi-style
(`sessions` / `messages` / `summaries`) — holding a **cleaned** session history and
**per-session summaries that load into the system prompt**. This is kiln's "short memory":
the recent conversation is kept tidy, and everything older is carried forward as compact
summaries instead of raw transcript.
**Tasks:**
- **Single-file store (`.kiln/store.json`).** One JSON owned by a small `store` module
  (atomic write + `.bak`), mirroring Lumi's `.lumi/store.json`: `sessions` (id, start/end,
  mode, turn count), `messages` (turns per session), `summaries`. Replaces the
  `state/memory.md` + `history/*.json` split; the `state/` knobs (canon/prompts/needs) stay.
- **Review & prune the current history.** Before a session is stored, review it and drop
  what isn't real conversation — slash-command echoes, empty / `/`-prefixed lines, the
  resting notice, accidental noise (and noise-only sessions) — so only meaningful turns
  persist. Rule-based first; may later use the model to judge relevance.
- **Migrate existing data.** A one-shot migration that folds the current `state/memory.md`
  summaries and all `history/session-*.json` transcripts into `.kiln/store.json` losslessly,
  and migrates the in-flight conversation on first run.
- **Summarize on every session close.** On exit (incl. Ctrl-C, via `finally`), summarize the
  *cleaned* session through `claude -p` on **Opus with extended thinking on**
  (`MAX_THINKING_TOKENS`; the API key is stripped → subscription billing) and append it to the
  store's `summaries` — every closed session yields exactly one summary.
- **Summaries → system prompt.** On start, load **all** prior `summaries` from the store into
  the system prompt of every branch (`build_system`), so each new session opens with compact
  memory of all previous ones.
- **Tests.** Store round-trips (save→load, atomic + `.bak`); the prune step removes
  non-conversation entries; migration folds legacy files in losslessly; a session close
  writes exactly one summary; the system prompt includes prior summaries — all on a **mock
  brain** (zero paid calls).
**DoD:** all session state lives in one `.kiln/store.json`; the kept history is pruned to
real conversation; legacy `state/memory.md` + `history/*.json` are migrated in; closing a
session writes one summary; and every new session's system prompt carries all prior summaries.

### 0.6 Long memory — user facts (`store.json` `facts`) — ⬜
**Goal:** a durable **facts-about-the-user** layer on top of 0.5's store. On session close,
extract facts from the session via Opus; persist them in `.kiln/store.json` (`facts`); and on
each start, digest **all** facts down to N lines (Opus) into a dedicated **system-prompt
section**. Distinct from 0.5's per-session *summaries* (what was discussed) — these are stable
facts about the user (who they are, preferences, life), carried forward indefinitely. Both
Opus calls run with **extended thinking on**, the API key stripped (subscription).
**Tasks:**
- **Extract facts on session close.** On exit, send the (cleaned) session history to
  `claude -p` on **Opus + extended thinking** (`MAX_THINKING_TOKENS`; key stripped) to extract
  durable **facts about the user**, deduped against the facts already stored.
- **Persist facts in the store.** Save them to `.kiln/store.json` under a **`facts`** key
  (id, text, first/last-seen, source session) — alongside 0.5's
  `sessions`/`messages`/`summaries`. Mirrors Lumi's `facts`.
- **Digest facts on session start.** On start, send **all** stored facts to `claude -p`
  (Opus + thinking) to condense them to **N lines** (`FACTS_DIGEST_LINES`) — a compact, current
  view of who the user is (Lumi's `facts_digests`).
- **Facts → system prompt (new section).** Inject that N-line digest into the system prompt of
  every branch under a dedicated **`## Facts about the user`** section, separate from the canon
  and the 0.5 memory summaries (`build_system`).
- **Tests.** Fact extraction is invoked on close; facts round-trip in the store and dedupe; the
  start-time digest condenses to ≤ N lines; the system prompt carries the facts section — all on
  a **mock brain** (zero paid calls). Both Opus calls strip the API key (asserted on the
  captured subprocess env).
**DoD:** closing a session extracts user facts (Opus + thinking) into `.kiln/store.json`;
starting one digests all facts to N lines (Opus + thinking) and injects them as a dedicated
system-prompt section; both Opus calls bill via the subscription, never the API key.

### 0.7 Tokens & cost report (Lumi-style) — 🟡
**Goal:** full cost visibility at three levels — **per turn** (live), **per session**
(totals + `$`), and **cross-session** (a persistent ledger + a generated Markdown report).
Modeled on Lumi's `.lumi/usage-ledger.jsonl` + `.lumi/usage-report.md`: an append-only
per-session ledger and a regenerated report with an overall cost summary, a per-bucket
breakdown (input / output / cache read / cache write with rates + cache-savings), and
rollups by month / ISO week / day plus a recent-sessions table.
**Done:** per-turn `· model · in→out tok (total)` line (`usage.py`); the `SessionStats`
accumulator (turns / total / by-branch / last / avg-latency) on the status bar (v0.4).
**Tasks:**
- **Capture cache tokens.** Extend `usage_record` to also carry `cache_read` /
  `cache_write` (the SDK `usage` has `cache_read_input_tokens` /
  `cache_creation_input_tokens`; the `claude -p` JSON `usage` has the same) — today only
  input/output are kept. Per-turn line gains a `· cache r/w` segment where present.
- **Per-session ledger (`.kiln/usage-ledger.jsonl`).** On session close, append **one JSON
  line per session**: `session_id`, `model`(s), `started_at`/`ended_at`, `turns`, `input`,
  `output`, `cache_read`, `cache_write`, `cache_ttl`, and `cost_usd` — using the CLI's actual
  `total_cost_usd` for `claude -p` turns, an estimate for the SDK/chat turns. Append-only,
  mirrors Lumi's ledger. (Establishes the `.kiln/` dir shared with 0.5's `store.json`.)
- **Cost estimation.** A small per-model price table (input, output, cache read = 10 % of
  input, cache write = 1.25×/2× of input by TTL); estimate `$` per bucket and per session.
  Prefer the CLI's actual `total_cost_usd` where available; clearly label estimates as
  estimates (not a billing source of truth), as Lumi does.
- **Generated report (`.kiln/usage-report.md`).** (Re)generate from the ledger on session
  close (and on demand via a command): **Overall** (est. cost, total tokens with the four
  buckets, sessions, turns); a **cost-breakdown** table (bucket / tokens / rate / cost /
  share) with a cache-savings note; rollups **by month / ISO week / day**; and a
  **recent-sessions** table (started, session, model, turns, buckets, total, est. cost) —
  matching Lumi's section layout.
- **`/usage` command.** Show the session-so-far totals + `$` and the path to the report
  (TUI + console, through the Output seam); `/report` regenerates the Markdown.
- **Config knob.** `USAGE_REPORT` (on by default) to enable/disable ledger + report writing,
  like Lumi's `usage_report` flag.
- **Tests.** Ledger appends exactly one line per session; cost-estimation math (incl. the
  cache rates) is correct; the report regenerates from a fixture ledger with right overall
  totals and by-day/week/month aggregation — all on fixtures/mock (zero paid calls).
**DoD:** each session appends a `.kiln/usage-ledger.jsonl` line; `.kiln/usage-report.md`
regenerates with the overall summary, per-bucket cost breakdown (cache-aware), and
by-month/week/day + recent-sessions tables (Lumi layout); per-turn and per-session token +
`$` stay visible live; `/usage` shows the session cost and report path.

### 0.8 World & temporal awareness — time / place + recent timed messages — ⬜
**Goal:** give Agnika awareness of the real world and the conversation's tempo — the current
**time, date, weekday, time-of-day, season, and the user's location**, plus the **last N exchanges,
each tagged with a precise timestamp** — injected into the system prompt of every branch, refreshed
each session start (alongside the facts digest). A "clock" so her replies fit the moment (brighter
in the morning, quieter and warmer late at night), a sense of place, and a feel for how recently
things were said (gaps, a late-night ping). The timeline shows the **previous session's** tail —
the current session's own turns already ride in the messages array / transcript, so they aren't
repeated. Computed **locally** — no external calls; weather / calendar / news are a later,
tool-backed layer (out of scope here). The sections are short **Ukrainian** text with a rhythm cue.
**Tasks:**
- **World-now builder.** A pure `world_now(now, location) -> str` (new `kiln/world.py`): a short
  Ukrainian paragraph — weekday + date, time + time-of-day (ранок/день/вечір/ніч), season, location,
  and a one-line rhythm cue. **Deterministic** — takes an injected `now`, so unit tests pass a fixed
  clock (no real `datetime.now()` in the pure function).
- **Timestamp each turn.** When a turn is appended to `history`, attach an ISO timestamp — the item
  becomes `{role, text, at}`. `to_messages`/`to_transcript` ignore `at` (back-compatible); the store's
  `messages` persist it (a richer transcript for RAG). *(Seam: the history/store turn shape →
  ARCHITECTURE update + contract test in the same issue.)*
- **Recent timed-messages block.** A pure builder + a `## Останні повідомлення` section: the last
  `RECENT_MESSAGES` turns, each as `[Сб 11:52] Користувач: …` / `[11:55] Ти: …` (the date shown when
  it changes), so the model sees the recent timeline with precise time/date. Deterministic from the
  turns' `at` stamps; turns without `at` degrade gracefully.
- **Config.** `USER_LOCATION` (+ optional `TIMEZONE`), `RECENT_MESSAGES` (N, default e.g. 10; 0 = off),
  and `WORLD_AWARENESS` (master on/off for the section) — all `.env`-overridable. The **code** default
  for `USER_LOCATION` is empty (general); **`.env` ships pre-seeded to the user's location (`Львів`)**,
  derived from the stored location fact, so the section works out of the box without manual setup.
- **World + messages → system prompt.** Extend `build_system(canon, memory, facts="", world="")` with
  the `## Зараз` and `## Останні повідомлення` sections, **separate** from canon / memory / facts;
  empty → the v0.7 output (back-compatible). `run()` start composes them (per-turn refresh so the clock
  advances mid-session is a stretch/follow-up).
- **Tests.** `world_now` formats for fixture times (morning/day/evening/night + season boundaries,
  ±location); the timed block renders the last N turns with timestamps (and the date-change rule);
  `build_system` places both sections, separate from the rest, with `world=""` v0.7-equivalent;
  disabled → no section; appended turns carry an `at` stamp — all deterministic (a fixed clock, mock
  brain, **zero paid calls**).
**DoD:** every new session's system prompt carries a `## Зараз` section (date / weekday / time /
time-of-day / season + location, a short Ukrainian paragraph with a rhythm cue) **and** a
`## Останні повідомлення` section with the last `RECENT_MESSAGES` turns each precisely timestamped;
turns are stamped with `at`; it's deterministic given an injected clock; `WORLD_AWARENESS` /
`RECENT_MESSAGES` toggle and size it; no external calls in the core (weather / calendar deferred).

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
