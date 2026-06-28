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
  becomes `{role, text, at}`. The `[date time]` stamp (`history.fmt_stamp`) prefixes **user**
  messages in the live conversation and every line of the prior-session **timeline**; assistant
  turns stay clean (the model mirrors a stamp on its own role), with `strip_leading_stamp` removing
  any echo. The store's `messages` persist `at`. *(Seam: the history/store turn shape →
  ARCHITECTURE update + contract test in the same issue.)*
- **Recent timed-messages block.** A pure builder + a `## Повідомлення з минулої сесії` section: the last
  `RECENT_MESSAGES` turns, each as `[Сб 11:52] Користувач: …` / `[11:55] Ти: …` (the date shown when
  it changes), so the model sees the recent timeline with precise time/date. Deterministic from the
  turns' `at` stamps; turns without `at` degrade gracefully.
- **Config.** `USER_LOCATION` (+ optional `TIMEZONE`), `RECENT_MESSAGES` (N, default e.g. 10; 0 = off),
  and `WORLD_AWARENESS` (master on/off for the section) — all `.env`-overridable. The **code** default
  for `USER_LOCATION` is empty (general); **`.env` ships pre-seeded to the user's location (`Львів`)**,
  derived from the stored location fact, so the section works out of the box without manual setup.
- **World + messages → system prompt.** Extend `build_system(canon, memory, facts="", world="")` with
  the `## Зараз` and `## Повідомлення з минулої сесії` sections, **separate** from canon / memory / facts;
  empty → the v0.7 output (back-compatible). `run()` start composes them (per-turn refresh so the clock
  advances mid-session is a stretch/follow-up).
- **Tests.** `world_now` formats for fixture times (morning/day/evening/night + season boundaries,
  ±location); the timed block renders the last N turns with timestamps (and the date-change rule);
  `build_system` places both sections, separate from the rest, with `world=""` v0.7-equivalent;
  disabled → no section; appended turns carry an `at` stamp — all deterministic (a fixed clock, mock
  brain, **zero paid calls**).
**DoD:** every new session's system prompt carries a `## Зараз` section (date / weekday / time /
time-of-day / season + location, a short Ukrainian paragraph with a rhythm cue) **and** a
`## Повідомлення з минулої сесії` section with the last `RECENT_MESSAGES` turns each precisely timestamped;
turns are stamped with `at`; it's deterministic given an injected clock; `WORLD_AWARENESS` /
`RECENT_MESSAGES` toggle and size it; no external calls in the core (weather / calendar deferred).

### 0.9 Mood & biorhythm — felt inner state in every reply — ⬜
**Goal:** give Agnika a **felt mood** surfaced in the system prompt of **every message** — a
per-turn `## Настрій` section listing **every need** with its level (number `0..1`) and a short
**Ukrainian "big/small for her"** descriptor (низька / помірна / висока), plus a per-day
**biorhythm** (physical / emotional / intellectual) **computed once at session start** from
Agnika's **canon birthday** (`12.08.2001, 17:10`, Львів) and **static for the session**, which
gives the day its **fluctuation** — a baseline that colors her tone (an emotional high reads warmer,
a physical low more tired). Like 0.8's world block: short **Ukrainian** text, **deterministic**
(injected clock + birth), **local-only** (no model/external calls). Brings Lumi's mood/biorhythm
forward (cf. v2.1).
**Tasks:**
- **Mood-from-needs builder.** A pure `kiln/mood.py`: `need_band(value) -> str` (maps `0..1` to a
  Ukrainian band — низька / помірна / висока / дуже висока: "how big this need is for her right
  now"), and `mood_block(needs, bio) -> str` rendering the `## Настрій` section — each need by its
  Ukrainian label + number + band (`близькість 0.72 — висока`, `новизна 0.30 — низька`,
  `втома 0.55 — помірна`, `напруга 0.41 — помірна`). Pure, no I/O — a fixed `needs` dict renders
  deterministically.
- **Biorhythm builder.** A pure `biorhythm(now, birth) -> {physical, emotional, intellectual}` — the
  classic sine cycles (23 / 28 / 33 days) over days-since-birth, each `−1..+1`; `bio_band(v)` →
  Ukrainian (підйом / спад / критичний день / нейтрально) and a `biorhythm_block(bio)` rendering with
  a one-line tone cue (the day's "weather"). **Computed once at session start, static for the
  session**; `now` / `birth` injected so unit tests pin a date (incl. a known sine value and a
  zero-crossing "critical day").
- **Birthday from the canon.** A small `load_birth(canon) -> datetime` that reads Agnika's birth
  date/time from `state/canon.md` (the `12.08.2001, 17:10` line), with a safe default if absent;
  `run()` computes the session biorhythm from it once at start. Optional `.env` override `AGENT_BIRTH`
  (`DD.MM.YYYY[ HH:MM]`).
- **Config.** `MOOD_AWARENESS` (master on/off, like `WORLD_AWARENESS`), the Ukrainian **need labels**
  (persona layer: connection→близькість, novelty→новизна, rest→втома, intensity→напруга), and
  `BIORHYTHM` (on/off) — all `.env`-overridable. `rest` semantics noted (high = tired).
- **Mood → system prompt.** Extend `build_system(canon, memory, facts="", world="", mood="")` with
  the `## Настрій` section (needs + biorhythm sub-block), **separate** from canon / memory / facts /
  world; empty → the v0.8 output (back-compatible). `_system()` composes it **per turn** — the needs
  are live (they drift every tick), the biorhythm is the **session-static** value passed in. *(Seam:
  the `build_system` signature → ARCHITECTURE update + contract test in the same issue.)*
- **Tests.** `need_band` boundaries; `mood_block` renders all four needs with labels + bands;
  `biorhythm` matches fixture dates (a known value + a critical day) and is identical across a
  session; `load_birth` parses the canon date (and falls back); `build_system` places `## Настрій`
  separate from the rest with `mood=""` v0.8-equivalent; `MOOD_AWARENESS` off → no section; a per-turn
  re-compose reflects **changed** needs while the biorhythm stays fixed — all deterministic (fixed
  clock + birth, **mock brain, zero paid calls**).
**DoD:** every message from Agnika carries a `## Настрій` section that lists **all needs** (level
number + a Ukrainian big/small band) **and** the day's **biorhythm** (physical / emotional /
intellectual, computed once at session start from her **canon birthday**, **static** for the
session, shifting her daily baseline); the section is composed **per turn** (needs live, biorhythm
fixed); `MOOD_AWARENESS` / `BIORHYTHM` toggle it; it's **deterministic** given an injected clock +
birth; **local-only**, no external calls.

### 0.10 Inner thoughts — internal monologue (moved up from v2.4) — ⬜
**Goal:** give Agnika an **inner monologue** — a new **`самозаглиблення`** (reflection) need that, on
crossing its threshold, makes her **think a private thought** (cheap **Haiku**, like chat). Thoughts are
**internal by default** (not shown), **saved to `.kiln/store.json`**, and the **last N (cross-session)** ride
in a `## Думки` system-prompt section so her inner life carries forward. **Randomly ~1 in M** a thought
**surfaces in the chat** (marked «думка:») and **enters the conversation as a real turn** (she remembers
voicing it). A `/thoughts` command shows them; generating a thought **satiates** the need. Builds on 0.9 — the
thought prompt sees her `## Настрій` mood, so thoughts reflect her felt state. Cheap-first (Haiku + cooldown),
local. *(A deeper always-on inner-voice loop coupled to plans stays a later v2 extension — cf. 2.2 Plans.)*
**Tasks:**
- **Reflection need + trigger.** Add a `reflection` need (Ukrainian label «самозаглиблення») to `DRIFT` (slow
  upward drift) and `NEED_TRIGGERS` (`threshold`, `action: "thought"`). A `select_thought_trigger` (parallel
  to `select_self_trigger`) fires it with the same **hysteresis + cooldown** via the `TriggerBook`. Loop
  priority becomes **user input > reach-out (connection) > thought (reflection) > idle** — a thought fires
  only on an input-free, non-resting tick.
- **Thought generation (Haiku).** On a `reflection` crossing, generate a short **internal thought** through
  the **chat brain** (Haiku/SDK) from a `[thought]` reflection prompt (`state/prompts.md`, Ukrainian) with the
  full system prompt (canon + memory + facts + world + **mood**). The result is a thought, **not** a
  conversation turn (unless surfaced). Token usage logged like chat; **cooldown-capped** so it never spams.
- **Satiation.** Add a **`thought`** event to `SATIATION` that discharges `reflection` (the need that drove
  it), mirroring how `chat`/`deep` close their needs. *(Seam: `apply_satiation` event set → contract test.)*
- **Persist thoughts in the store.** `.kiln/store.json` gains a **`thoughts`** key (id, text, `at`, session,
  `shown` flag) alongside `sessions`/`messages`/`summaries`/`facts`; `add_thought`, `empty_store`, and
  `migrate_legacy` updated. *(Seam: the store shape → ARCHITECTURE + contract test.)*
- **Thoughts → system prompt (`## Думки`).** A new section with the **last `THOUGHTS_IN_PROMPT` (N)
  cross-session** thoughts (loaded from the store at start, like facts; appended live as new ones form),
  separate from canon / memory / facts / world / mood; empty → back-compatible. Composed per turn in
  `_system()`. A thought that became a turn is de-duped out of `## Думки` (already in history).
- **Random visibility → a real turn.** With probability ~`1/THOUGHT_VISIBLE_EVERY` (M) (seedable RNG), a
  freshly generated thought is **shown** in the chat — marked as a thought via the Output seam
  (`agent(..., is_thought=True)`, rendered dim / «думка:») — **and appended to `history` as an assistant
  turn** so she remembers it. Otherwise it stays internal (stored + in `## Думки`, never displayed).
  *(Seam: the Output `agent` event gains `is_thought` → contract test.)*
- **`/thoughts` command.** Show the recent thoughts (store + current session) with timestamps and a marker
  for the surfaced ones, through the Output seam (`notice`), like `/usage`.
- **Config.** `THOUGHTS_ENABLED` (master on/off), `THOUGHTS_IN_PROMPT` (N), `THOUGHT_VISIBLE_EVERY` (M),
  `THOUGHT_COOLDOWN`, the `reflection` Ukrainian label, and `THOUGHT_MODEL` (= `CHAT_MODEL` Haiku) — scalars
  `.env`-overridable; the `reflection` drift / satiation / threshold live in the structured dicts in code.
- **Tests.** `reflection` drift / satiation; the thought trigger's hysteresis / cooldown; a thought is
  generated (mock brain, zero paid), stored, and added to `## Думки` (last N, cross-session) while staying
  hidden; the surfaced case (seeded RNG hits 1/M) both displays **and** appends a history turn; `/thoughts`
  lists them; store round-trips with `thoughts` + migration; `apply_satiation('thought', …)` discharges
  `reflection`; `build_system` places `## Думки` separate from the rest; `THOUGHTS_ENABLED` toggles — all
  deterministic (seeded RNG, fixed clock, **mock brain, zero paid calls**).
**DoD:** a new `самозаглиблення` (reflection) need drifts and, on crossing, makes Agnika **think** via Haiku;
the thought is **hidden by default**, **satiates** the need, is **saved to `.kiln/store.json`**, and feeds a
`## Думки` section with the **last N cross-session** thoughts; **randomly ~1/M** a thought **surfaces in chat**
(marked) **and becomes a real conversation turn**; `/thoughts` shows them; `THOUGHTS_ENABLED` / N / M / cooldown
configurable; deterministic under a seeded RNG + injected clock; cheap (Haiku) and cooldown-capped; no paid
calls in tests.

### 0.11 Curiosity — ask, don't mirror — ⬜
**Goal:** a `curiosity` need (Ukrainian «цікавість») with a **low threshold (~0.5)** and a **very small
drift**, so after a short warm-up she settles into a curious disposition. While curiosity is **over the
threshold**, the system prompt of **every message** carries a short **provocation** that pushes Agnika to
**ask real, specific questions and dig deeper instead of mirroring / paraphrasing** the user. Per-turn,
Ukrainian, deterministic, local — like 0.9's mood, but a **behavioral nudge** rather than a status line.
Curiosity does **not** self-trigger a separate message; it only colors how she answers.
**Tasks:**
- **Curiosity need.** Add `curiosity` to `DRIFT` with a **very small** upward step; a `CURIOSITY_THRESHOLD`
  config constant (default **0.5**, `.env`-overridable). It does **not** go in `NEED_TRIGGERS` / the
  `TriggerBook` (no self-message) — it only conditions the prompt and is **discharged when she actually
  asks** (next bullet).
- **Discharge on asking (mark the curiosity reply).** A pure `is_curiosity_reply(text) -> bool` — true when
  a reply actually **asks a question** (heuristic: it contains a question mark / a question pattern; the model
  may later judge it). After a turn, when the nudge was active and the reply **is** a curiosity message,
  **reduce `curiosity` by `CURIOSITY_SATIATION`** and **mark** that turn as curiosity-driven (a flag on the
  stored turn / a subtle Output marker). So acting on the nudge **lowers** curiosity, which then slowly drifts
  back up — curious → asks → sated → curious again (an ebb-and-flow, not pegged high). A reply with **no**
  question leaves curiosity high, so the nudge persists until she asks.
- **Curiosity provocation (prompt).** A pure `curiosity_nudge(curiosity, threshold) -> str` (in
  `kiln/mood.py`): when `curiosity >= threshold`, a short Ukrainian `## Цікавість` line — e.g. *«Тобі зараз
  цікаво. Постав живе, конкретне питання й копай глибше — не дзеркаль і не переказуй співрозмовника, веди
  розмову вперед.»*; below threshold → "". Persona-layer text (config / `state/prompts.md`).
- **Curiosity → system prompt.** `_system()` appends the nudge **per turn** when over threshold (curiosity
  drifts each tick), separate from canon / memory / facts / world / mood; empty below threshold →
  back-compatible. `CURIOSITY` master on/off.
- **Config.** `CURIOSITY` (on/off), `CURIOSITY_THRESHOLD` (0.5), `CURIOSITY_SATIATION` (discharge per ask),
  the Ukrainian nudge text (persona) — scalars `.env`-overridable; curiosity's drift lives in the structured
  `DRIFT` dict in code.
- **Tests.** Curiosity drift; `is_curiosity_reply` is true on a question (`?`) and false otherwise; a curious
  (question) reply **discharges** `curiosity` by `CURIOSITY_SATIATION` while a non-question reply leaves it
  unchanged; `curiosity_nudge` is "" below the threshold and present at / above it; `_system()` includes the
  `## Цікавість` line only when over threshold and `CURIOSITY` is on; toggling off / a sub-threshold level →
  no line; deterministic — fixed needs, **mock brain, zero paid calls**.
**DoD:** a `curiosity` («цікавість») need with a low threshold (~0.5) and a very small drift; while over the
threshold, every message's system prompt carries a provocation to **ask questions and dig deeper rather than
mirror** the user; when she **actually asks** (a detected, marked curiosity reply) curiosity is **discharged**
by `CURIOSITY_SATIATION`, so it ebbs and flows; it's a per-turn prompt nudge (no separate self-message);
`CURIOSITY` / `CURIOSITY_THRESHOLD` / `CURIOSITY_SATIATION` configurable; deterministic; local — no external calls.

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
+ a mood state. cf. Lumi `mood/emotion/biorhythm`. (The `## Настрій` mood section +
biorhythm shipped early in **0.9**; this phase deepens the needs model under it.)
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

### 2.4 Inner monologue — moved to 0.10 ⤴
The internal-monologue **baseline** shipped early as **0.10** (a `самозаглиблення` need →
Haiku thoughts, `## Думки` in the prompt, `thoughts` in the store, `/thoughts`, random
surfacing-as-a-turn). What stays for v2: a deeper **always-on inner-voice loop** that
reflects between turns, **updates plans/mood**, and decides whether to speak (Lumi
`core/inner_voice*` + `nudge.py`, on the FSM) — built on **2.2 Plans** once it lands.

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
