# Architecture — kiln

## Overview

Two axes. **The agent's mind** grows: needs + two-brain routing → memory → RAG →
mood/personality → inner monologue. **The interface/host** grows separately: an
in-process print loop (today) → a Textual TUI → a **tick-server** that clients
attach to → a **multi-agent hub**. They are bound by the **core**, which never
depends on the interface — the terminal, the web, and (later) other agents are
just clients of one tick loop.

kiln's defining choice: the engine is a **cheap tick loop**. Most ticks are free
local state (needs drift); the model is called rarely and on the cheapest brain
that fits. The loop keeps living between turns — that is what lets the agent
self-trigger.

## Components

(current modules in `()`; planned ones noted)

- **Config** (`config.py`) — paths, `.env` loader, all tunables (drift, satiation,
  thresholds, models, hints). Imported by everything; never imports back.
- **Core state** (`engine.py`: `State`, `load_state`/`save_state`) — the needs
  vector (`state/needs.json`), drift, satiation.
- **Tick loop** (`engine.py`: `run`) — the always-on loop; one action per tick
  (input > self-trigger > idle); catch-up drift for real elapsed time.
- **Two brains** (`brain.py`: `LiveBrain` — `chat` Haiku/SDK + `deep` Opus/`claude
  -p`; `MockBrain` for dry-run/tests) behind the **`Brain` seam**, with cost-aware
  routing (`engine.py`: `classify`, `respond`). The core reaches the model **only**
  through the seam — never the SDK or CLI directly.
- **Self-triggers** (`engine.py`: `select_self_trigger`, `TriggerBook`) — the
  agent speaking first (hysteresis + cooldown).
- **History** (`history.py`) — shared session-transcript helpers.
- **Usage** (`usage.py`) — per-call model/token capture + chat rendering (colors,
  the tech line).
- **Memory** (`memory.py`) — long-term `memory.md` summaries, the canon loader,
  prompts, and raw `history/*.json` transcripts (the RAG corpus).
- **Commands** (`commands.py`) — slash commands, intercepted before routing; their
  output goes through the **`Output` seam** (`output.notice`), never `print`, so any
  client (console, TUI) renders it.
- **Channels** (`engine.py`: `ScriptedChannel`, `StdinChannel`) — async input
  behind `poll()`.
- **Output** (`output.py`: `Output` seam, `ConsoleOutput`) — the core writes every
  reply/usage/notice through this port, never `print` directly. Today it prints to
  the terminal; v0.3 plugs the echo-free TUI bus, v1.1/1.2 the event protocol — the
  engine is unchanged. The mirror image of the input `Channel`.
- **TUI bridge** (`tui/bridge.py`: `Bridge`) — the **echo-free** inbox/outbox bus
  between the engine's tick loop (a background thread) and the UI thread. Two
  thread-safe queues: `inbox` (UI→engine typed lines, read via a `TuiChannel`) and
  `outbox` (engine→UI render events, written via a `TuiOutput`). Input never leaks to
  the outbox, so the UI shows the typed line once and the engine writes only its own
  replies. The shared bus for later clients (web, server).
- **Server / agent host** (planned, v1.1) — wraps the core as a WS/HTTP server;
  hosts many agents keyed by `agent_id`, each with a permission scope.
- **Clients** (`tui/`: Textual app, planned web) — thin front-ends over the bridge/
  server. None hold agent logic; the engine never imports a client.

## The tick loop (the central abstraction)

`run()` iterates short ticks. Each tick:

1. **Catch-up drift** — measure real elapsed time (`time.monotonic`) and apply
   that many ticks of drift, so a blocking model call ages needs by the time it
   really took (live only; dry-run = exactly 1 tick).
2. **Poll input** (`channel.poll()`), non-blocking.
3. **Pick one action**, priority **input > self-trigger > idle**:
   input → slash command or `respond()`; else a self-trigger may fire →
   `respond(force=…)`; else idle → `apply_satiation("idle")`.

The loop runs with no client attached (the agent keeps living). Model calls are
**synchronous today** (they freeze the loop); v1.1/1.2 move them to tasks behind
an FSM so the loop never blocks.

## Two brains and cost routing

The cost discipline lives here: **the cheapest brain that fits.** Both branches sit
behind the **`Brain` seam** (`brain.py`), each method returning `(text, usage)`:

- **chat** → **Haiku** via the Anthropic Messages API (`LiveBrain.chat`, in-process
  SDK; `anthropic` imported lazily so dry-run is dependency-free). Cheap, fast,
  no tools.
- **deep** → **Opus** via `claude -p` (`LiveBrain.deep`, subprocess,
  `--output-format json` for text + token usage). Reasoning and tools
  (`--allowedTools`).
- **tool** → a **named Claude Code sub-agent** via `claude -p --agent <agent>`
  (`LiveBrain.tool`; the agent's `.claude/agents/<agent>.md` supplies its system prompt,
  model, and tools — kiln reads the frontmatter only for `--allowedTools` + usage labeling).
  A `NEED_TRIGGERS` entry with `action: "tool"` names the agent — **`session-wiki`**, chosen by
  `reach_out_branch` when a connection reach-out fires with high `novelty`. It reads the recent
  session, fetches an external Wikipedia fact, and returns one Ukrainian paragraph; satiation is
  **per-agent** (`SATIATION["session-wiki"]` drops `novelty`), falling back to `deep` otherwise.
- **mock** → `MockBrain` returns deterministic canned text + a synthetic usage
  record (no network, no subprocess); the dry-run demo and the whole test suite run
  on it — **zero paid calls**.

**Cost/auth invariant: Opus never touches the API key.** The API-key (SDK) path is the
cheap-Haiku `chat` branch only — `LiveBrain.chat` refuses to run an Opus model (`_is_opus`).
Everything Opus/Sonnet goes through `claude -p`, which is spawned with `ANTHROPIC_API_KEY`
**stripped from its environment** (`_claude_env`), so it bills via the CLI's own login
(subscription/OAuth), never the API key.

`classify(prompt, state)` → `(class, agent)`, class ∈ `chat | think | tools | tool`: explicit
markers (`TOOL_HINTS`/`THINK_HINTS`) win, then **ambient high needs pick the deeper brain like a
self-trigger** (`intensity ≥ thr` → `deep`, else `novelty ≥ thr` → `session-wiki`), then a state
weight (`0.55·intensity + 0.45·connection` vs `THINK_THRESHOLD`), else `chat`. `respond()` maps the
class to a branch and a satiation **event** (`chat | deep | idle | <agent>`). Both branches share one system
prompt (canon + long-term memory). A deep failure degrades to `(deep error: …)` —
never crashes the loop.

## Needs and self-triggers (motivational substrate)

Each need is `0..1`. Two forces: a slow **drift up** each tick (`DRIFT`) and
**closing by events** (`SATIATION`). Key idea: **which branch answered decides
what closed** — `deep` is the "filling meal" (closes novelty/rest/intensity hard),
`chat` mostly closes connection, `idle` slowly rests. So the agent accumulates
need → pushes to expensive `deep` → which discharges it → a long cheap stretch.
That cycle is what conserves Opus.

A **self-trigger** (proactive message) fires only when `REACH_OUT_NEED` (**connection** =
loneliness) crosses its threshold — guarded by **hysteresis** (one fire per upward crossing;
re-arms below) and a **cooldown** (`SELF_COOLDOWN` ticks). **Which brain answers** the reach-out
is shaped by her other needs at that moment (`reach_out_branch`): `intensity` over its threshold
→ deep/Opus, else `novelty` over its → `session-wiki`, else a light `chat`. So Opus and
`session-wiki` **never self-initiate** — `intensity` only otherwise routes *user* turns to Opus
via `turn_weight`. `rest` crossing drives the **rest gate** (sleep), not a message. Full
algorithm in [`docs/how-it-works.md`](../docs/how-it-works.md).

## Memory and transcripts (+ RAG)

All cross-session state lives in **one `.kiln/store.json`** (`store.py`, v0.5) — written
**atomically** (temp + `os.replace`) with a `.bak`, recovered on corruption — in four
Lumi-style sections:

- **`summaries`** — `{session_id, stamp, text}`, one per session: at exit the (pruned) session
  is summarized via the Anthropic Messages API (Haiku — cheap/fast) and appended; at start all summaries
  load into the system prompt of every branch (`build_system`). What the agent *remembers*.
- **`messages`** — `{session_id: [{role, text, at}]}`, the full turn list per session (each turn
  carries an `at` ISO timestamp, v0.8), written **before** the summary so a summary failure can't
  lose it. The **RAG corpus** (1.4).
- **`sessions`** — `{id, started_at, ended_at, mode, turns}`, the per-session index.
- **`facts`** (v0.6) — `{id, text, first_seen, last_seen, source_session}`, durable **facts about
  the user** (who they are, preferences, life), deduped by normalized text (`add_facts`).
  Extracted on close and digested into the system prompt (v0.6) — distinct from `summaries`
  (what was *discussed*); these are stable facts carried forward indefinitely.

(Legacy `state/memory.md` summaries + `history/session-*.json` transcripts are migrated into
the store and retired across v0.5; the `state/` knobs — canon/prompts/needs — stay put.)

**RAG (planned, port from Lumi):** embed transcripts → vector store → recall
relevant past fragments per turn, alongside the summaries. Lumi has it built:
`core/embedder.py`, `chunking.py`, `memory.py`.

## Agent host (the hub)

The north star. The v1.1 server is an **agent host**: one runtime runs many agents,
each on its own tick loop, keyed by `agent_id`. A client attaches to a chosen
agent/session. Two cross-cutting properties designed in from the start so
multi-agent is additive, not a rewrite:

- **`agent_id`** scopes every record (state, memory, transcripts) and every event.
- **Permission scope** — each agent is granted a set of tools/access. **Agnika is
  the home agent**: always connected, broad system/home tools. A Lumi-style
  companion runs narrow. The scope gates the tool registry (1.3) and any system access.

## Contracts (stable seams)

- **Reply / route:** `respond(...) → {class, route, reply, usage}`.
- **Model usage:** `{model, input, output, cache_read, cache_write, total, cost_usd}` captured by
  `usage_record` (SDK `msg.usage` / CLI `data.usage` + `total_cost_usd`). `total` = input+output
  (cache tracked separately, Lumi-style); `cost_usd` = the CLI's actual cost, or `None` for the
  SDK path (estimated from the v0.7 price table).
- **Needs:** `state/needs.json` = `{need: level(0..1)}`.
- **Canon / system prompt:** `build_system(canon, memory, facts, world, mood, thoughts)` composes the
  system prompt of every branch — `canon` (`state/canon.md`, fallback `DEFAULT_CANON`) + the v0.5
  memory summaries + the v0.6 `## Facts about the user` digest + the v0.8 `world` block (`## Зараз` +
  `## Повідомлення з минулої сесії`, `world.world_block`) + the v0.9 `mood` block (`## Настрій` —
  every need's level + a Ukrainian band, then the day's biorhythm; `mood.mood_block`) + the v0.10
  `thoughts` block (`## Думки` — the last N cross-session inner thoughts, deduped vs the live turns;
  `memory.thoughts_block`). Each layer optional; `thoughts=""` is v0.9-equivalent. The world + mood
  + thoughts blocks are composed **per turn** so the clock, needs, and latest thoughts stay live; the
  world timeline is the **previous session's** tail (current-session turns already ride in the
  messages array / transcript) and the biorhythm is computed **once at session start** (static).
- **Store (v0.5–0.10):** `.kiln/store.json` = `{sessions: [{id, started_at, ended_at, mode, turns}],
  messages: {session_id: [{role, text, at}]}, summaries: [{session_id, stamp, text}],
  facts: [{id, text, first_seen, last_seen, source_session}],
  thoughts: [{id, text, at, session, shown}]}`, via `store.load_store`/`save_store` (atomic write +
  `.bak`; corrupt → recover from `.bak` or fresh; missing sections healed). `thoughts` (v0.10) is
  Agnika's inner monologue, appended by `store.add_thought`.
- **Usage ledger (v0.7):** `.kiln/usage-ledger.jsonl` — one append-only JSON line per closed
  session: `{session_id, model, started_at, ended_at, turns, input, output, cache_read,
  cache_write, cache_ttl, cost_usd, cli_calls, by_model}`, via `ledger.append_session`/`read_ledger`.
  `cost_usd` sums the CLI's actual cost (`claude -p`) + the price-table estimate (SDK); `cli_calls`
  counts `claude -p` executions (deep + tool); `by_model` is the per-model breakdown
  `{model: {calls, input, output, cache_read, cache_write, cost_usd}}`. The generated
  `.kiln/usage-report.md` (`report.py`) is regenerated from it.
- **Brain seam:** `Brain.chat(history, system)`, `Brain.deep(prompt, history, system,
  with_tools)`, and `Brain.tool(agent, history, system)` each return `(text, usage)`;
  `LiveBrain` (SDK + CLI + named sub-agents) and `MockBrain` implement it; model ids are
  config. `respond()` calls the model only through this seam (classes
  `chat | think | tools | tool`, the last delegating to a named `.claude/agents/<agent>`).
- **Output seam:** `Output` with `user(text)` / `agent(text, is_self, lead, model, is_thought)` /
  `usage(dict, latency?)` / `notice(text)` / `status(snapshot)`; the core emits through it,
  `ConsoleOutput` is the default sink (`status` a no-op). `is_thought` (v0.10) marks a surfaced
  inner thought (rendered dim / «думка:»); the TUI bridge `agent` event carries it. The method set
  foreshadows the event protocol below.
- **Status event (v0.4):** `run()` emits a `status(snapshot)` **every tick** —
  `{status, model, branch, tick, needs, thresholds, actions, hottest, cooldowns, stats}` where
  `stats = {turns, tokens_total, tokens_by_branch, last_tokens, last_latency,
  avg_latency}` (`SessionStats`). It's the live data the TUI status bar + needs panel
  render from; a precursor to the v1.1 WS `status` event.
- **Bridge bus (v0.3+):** `Bridge` carries typed-dict render events on the outbox
  (`{"kind": "user"|"agent"|"usage"|"notice"|"status", …}`, mirroring the `Output`
  methods) and typed lines on the inbox; both drained non-blocking. Echo-free: input
  never appears on the outbox. A precursor to the WS event protocol below.
- **Event protocol (planned, 1.1/1.2):** server↔client events (`user.message`,
  `agnika.message`, `status`, `usage`, `tick`, `command`) mirror the FSM.

## Data model

- `state/needs.json` — `{connection, rest, novelty, intensity}` in `0..1` (seed; rewritten each run).
- `state/canon.md` — authored persona (system prompt).
- `state/prompts.md` — self-trigger prompts per need.
- `state/memory.md` — datestamped cross-session summaries (generated; gitignored).
- `history/session-*.json` — `{session, started_at, ended_at, mode, turns, history[]}` (generated; gitignored; RAG corpus).
- `.env` — models + scalar knobs + `ANTHROPIC_API_KEY` (gitignored).
- Planned: per-`agent_id` scoping of all the above; structured memory (facts/impressions), plans, vector store.

## Configuration and secrets

All tunables in `config.py`; `load_dotenv()` reads `.env` before the constants,
`os.environ.setdefault` so a real env var wins. Scalar knobs (`CHAT_MODEL`,
`DEEP_MODEL`, `TICK_SECONDS`, `THINK_THRESHOLD`, `SELF_COOLDOWN`) are
`.env`-overridable; structured dicts (`DRIFT`/`SATIATION`/`NEED_TRIGGERS`) stay in
code. Secrets (`ANTHROPIC_API_KEY`) live only in `.env`.

## Security and permissions

- **Per-agent permission scope** (from the hub design): an agent can only use the
  tools/access its scope grants. Agnika (home) is elevated; companions narrow.
- **Closed hub** (later): multi-agent/multi-user stays an admin-managed allowlist;
  no open sign-up.
- **Untrusted inputs** (later, with tools/RAG/web): tool/web/file content is data,
  never instructions.
- `.env` (with the key) is gitignored; never logged.

## Repository layout

```
pyproject.toml      # project metadata, deps (anthropic; dev: pytest/ruff), console entry, tool config
VERSION             # single source of truth for the version (pyproject + kiln.__version__ read it)
kiln/               # the package (installed via `pip install -e .`)
  __init__.py       # __version__ (reads VERSION)
  __main__.py       # console entry main(): live mode + the dry-run demo (the smoke test)
  config.py         # paths (PROJECT_ROOT = package parent, override KILN_HOME), .env, tunables
  history.py        # session transcript helpers
  usage.py          # model/token capture + chat colors
  memory.py         # long-term memory, canon, prompts, transcripts
  commands.py       # slash commands
  engine.py         # State, ticks, two brains, channels, respond, run (no __main__)
tui/                # Textual client (v0.3): bridge bus, TuiOutput/TuiChannel, app — no agent logic
  bridge.py         # echo-free inbox/outbox Bridge between the loop and the UI thread
tests/              # pytest: unit + contract (seams) + integration on a mock brain
state/              # needs.json, canon.md, prompts.md, memory.md (generated)
history/            # session-*.json transcripts (generated; RAG corpus)
docs/               # how-it-works, architecture (internals)
spec/               # MISSION.md, ARCHITECTURE.md, ROADMAP.md, vision.md, roadmap/implementation/
# planned: server/ (agent host), web/, tools/, rag/
```

The console entry lives in `kiln/__main__.py` (the `kiln` command / `python -m
kiln`), so **`engine.py` carries no `__main__` and can be imported freely** (incl.
by tests): constants live in `config.py`, and `/ask` does its deep call back in
`run()` so `commands.py` doesn't depend on the core. Run-time state resolves to the
project root (the package's parent dir, or `KILN_HOME`), not the package dir. Build
each new dir as its roadmap version begins; the core comes first and never depends
on a client.

## Testing

Today the dry-run demo (`if __name__ == "__main__"`) is the smoke test:
`KILN_LIVE=0 python engine.py` exercises routing, self-triggers, commands, memory,
and transcripts deterministically (stub brains, no paid calls). Planned (with the
server/FSM): unit tests for drift/satiation/trigger logic, contract tests for the
seams (needs.json, usage, event protocol, per-`agent_id` isolation), and a
mock-brain integration turn — the model is mocked, never a paid call in CI.
