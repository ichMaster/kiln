# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`kiln` is a small multi-module Python prototype for a needs-driven chat engine (stdlib-only for
dry-run; `anthropic` SDK only for the live chat branch). It runs a loop of cheap local "ticks"; an
LLM ("the brain") is invoked only on a condition (user input, or a need crossing its threshold), and
each invocation is **routed** to one of two branches.

**Language convention:** code comments, docstrings, all documentation (`README.md`, `docs/`,
`spec/`), and operator-facing chrome (command output, `[exit]` notices, the TUI status/footer) are
in **English**. The only Ukrainian left is the **persona/conversation layer** — Agnika's voice and
anything that shapes it for the model: `DEFAULT_CANON` and `state/canon.md`, `state/prompts.md`, the
`summarize`/self-trigger prompts, the system-prompt memory intro and transcript labels
(`history.py`), and the `THINK_HINTS`/`TOOL_HINTS` that match Ukrainian user input. Keep that
Ukrainian; write everything else in English.

The modules live in the `kiln/` package and form a clean DAG —
`config`/`history`/`usage` (leaves) → `memory` → `commands` → `engine`:
`kiln/config.py` (paths, `.env`, tunables), `kiln/history.py` (session-transcript helpers),
`kiln/usage.py` (model token logging + chat colors), `kiln/memory.py` (cross-session memory,
prompts/canon, RAG transcripts), `kiln/commands.py` (slash commands), `kiln/engine.py`
(`State`, ticks, the two brains, the loop — **no `__main__`**). The console entry is
`kiln/__main__.py` (`main()`: live mode + the dry-run demo), wired as the `kiln` command.
**`engine.py` carries no `__main__`, so it can be imported freely** (incl. by tests) — that's
why constants live in `config.py` and `/ask` does its deep call back in `run()` rather than in
`commands.py`.

Human-facing docs live in [`docs/`](docs/) — [`docs/architecture.md`](docs/architecture.md) (design)
and [`docs/how-it-works.md`](docs/how-it-works.md) (runtime mechanics, tables). The root `README.md`
is usage-only; this file and `docs/` carry the internals.

## Commands

```bash
# one-time setup (venv execution model)
python3 -m venv .venv
source .venv/bin/activate           # then `python` == .venv/bin/python
pip install -e .[dev]               # editable install + dev tools (pytest, ruff)

kiln                                # dry-run: deterministic demo via ScriptedChannel (no API/CLI calls)
python -m kiln                      # same as `kiln`
KILN_LIVE=1 kiln                    # live: interactive StdinChannel; ticks run on their own, you type into the terminal

pytest                              # the test suite (runs against a mock brain — zero paid calls)
ruff check . && ruff format --check .   # lint + format gate
```

The `anthropic` dep (in `pyproject.toml`) is needed only by the **live chat branch**. Dry-run
and the live *deep* branch (which shells out to the `claude` CLI) are stdlib-only. Live mode also
needs the **Claude Code CLI** installed and logged in (the deep branch calls `claude -p`). The
test suite lives in `tests/` (pytest); the dry-run demo in `kiln/__main__.py` stays a smoke test.

### State files live in `state/`

All mutable state is under `STATE_DIR` (defined in `config.py`, = repo root `/state`): `state/needs.json` (seed need
levels, rewritten each run by `save_state`), `state/prompts.md` (self-trigger prompts),
`state/canon.md` (the **canon** — the persona/voice that becomes the system prompt of both
branches), `state/mood.json` (v0.9 — the need/biorhythm **bands** (thresholds + Ukrainian names) and
the behavioural **cues** for the `## Настрій` section; `mood.load_mood` → `DEFAULT_MOOD` fallback),
and `state/memory.md` (cross-session summaries, generated on exit — gitignored).
`run()` calls `STATE_DIR.mkdir(exist_ok=True)` before reading, so a fresh clone never crashes;
each loader falls back to a default if its file is missing (`load_canon` → `DEFAULT_CANON`,
`load_mood` → `DEFAULT_MOOD`, empty needs/prompts otherwise).

### Config via `.env`

`load_dotenv()` (a tiny stdlib-only `KEY=VALUE` parser in `config.py`, no dependency)
reads `.env` from the repo root **before** the config constants are defined, so these can be set
without touching code: `CHAT_MODEL`, `DEEP_MODEL`, `TICK_SECONDS`, `THINK_THRESHOLD`,
`SELF_COOLDOWN`, `REST_WAKE`, `THINKING_TOKENS`, `FACTS_DIGEST_LINES`, `MAX_FACTS`,
`FACTS_ENABLED`, `MEMORY_SUMMARIES`, `SUMMARY_SENTENCES`, `USAGE_REPORT`, `USER_LOCATION`,
`TIMEZONE`, `RECENT_MESSAGES`, `WORLD_AWARENESS`, `USER_NAME`, `AGENT_NAME` (plus `KILN_LIVE`,
`ANTHROPIC_API_KEY` for live mode). It uses `os.environ.setdefault`,
so a real environment variable always wins over `.env`. `.env` is gitignored; the structured dict
knobs (`DRIFT`/`SATIATION`/`NEED_TRIGGERS`) stay in code.

## Architecture (the big-picture flow)

Split across the modules listed above; the runtime flow ties them together:

**Tick loop** (`run`) — drifts needs upward each tick (`drift`), then picks exactly one action
per tick with this priority: **user input > self-trigger > idle**. Input always wins; a pending
self-trigger waits for the next tick.

**Two brains** (the core idea — *which branch answers decides which needs close*):
- **Chat** (`chat_reply` in `engine.py`, `CHAT_MODEL` Haiku): cheap/fast small talk via a real
  Anthropic Messages API call (`anthropic` imported lazily so dry-run stays dependency-free). Whole
  session `history` goes in as a `messages` array; token usage is captured via `log_model` (`usage.py`).
- **Deep** (`deep_reply`, `DEEP_MODEL` Opus): reasoning/tools via the `claude -p` subprocess with
  `--output-format json` (so it returns both the text and token `usage`). The subprocess holds no
  session, so prior history is flattened into the prompt as a text transcript (`to_transcript`);
  long-term memory rides on `--append-system-prompt`; `tools` class adds `--allowedTools`. On a
  nonzero exit it degrades to a `(deep error: …)` string instead of crashing the loop.

**Routing** — when the brain fires on user input, `classify(prompt, state)` returns
`chat | think | tools` from message markers (`TOOL_HINTS`/`THINK_HINTS`) **and** a state weight
(`turn_weight = 0.55*intensity + 0.45*connection` vs `THINK_THRESHOLD`). `think`/`tools` → deep,
else → chat. `respond` maps the class to a branch and a satiation event (`chat` | `deep` | `idle`).

**Needs model** (`State`, `DRIFT`, `SATIATION`) — each need is `0..1`. `drift` raises every need
each tick; `apply_satiation(state, event)` lowers needs per the event that occurred. `deep` is the
"filling meal" (closes `novelty`/`rest`/`intensity` hard); `chat` mostly closes `connection`;
`idle` slowly rests. This creates the intended cycle: needs accumulate → push to expensive `deep`
→ deep discharges them → long stretch of cheap chat/idle. That cycle is what conserves Opus.

**Self-triggers** (`NEED_TRIGGERS`, `select_self_trigger`, `TriggerBook`) — when a need crosses its
own threshold the engine speaks first, on its own branch. Two anti-spam guards, both runtime-only
(not persisted): **hysteresis** (fires only on an upward crossing; re-arms only after the need
falls back below threshold) and **cooldown** (`SELF_COOLDOWN` silent ticks after firing). The
self-trigger's prompt text is picked at random from `state/prompts.md` (`[need]` sections).

**Input channels** — abstracted behind `poll() -> str | None`: `ScriptedChannel` (tick→text map,
deterministic, for the demo) and `StdinChannel` (a daemon thread reads stdin into a queue so the
non-blocking `poll()` never stalls the tick loop).

**Slash commands** (`handle_command`) — lines starting with `/` are intercepted **before**
classification, so they never reach a brain (`/status`, `/needs`, `/mood` shows the `## Настрій`
block from the prompt, `/self`, `/prompt`, `/usage`, `/report`, `/ask <text>` forces deep, `/clear`,
`/help`, `/quit`).

**Cross-session memory** — on exit (incl. Ctrl-C, via `finally`) the session is summarized through
the Anthropic Messages API (Haiku — cheap/fast, like the chat branch, not `claude -p`/Opus;
`summarize`) and written, with the pruned turns, into `.kiln/store.json` (`save_store`). On
start, all stored summaries load into the system prompt of *both* branches (`load_memory` →
`build_system`). The store is append-only — no trimming/windowing yet.

**Raw session transcripts (for RAG)** — also on exit, `save_session` writes the full turn list plus
metadata (start/end time, `live`/`dry` mode, turn count) to `history/session-<stamp>.json`
(`HISTORY_DIR`, gitignored), one file per session, `ensure_ascii=False` so Ukrainian stays readable.
It runs **before** `summarize` so a summary failure can't lose the transcript, and a same-second
collision guard appends `-2`, `-3`, … rather than overwriting. This is the unsummarized corpus
intended for downstream retrieval, distinct from the `memory.md` summaries.

## Calibration

All tuning lives in module-level constants in `config.py`: `TICK_SECONDS`, `DRIFT`,
`SATIATION`, `NEED_TRIGGERS`, `SELF_COOLDOWN`, `THINK_THRESHOLD`, `CHAT_MODEL`/`DEEP_MODEL`,
`DEEP_TOOLS`/`DEEP_SKILLS`, `THINK_HINTS`/`TOOL_HINTS`, and the weights in `turn_weight`. The
scalar ones (models, `TICK_SECONDS`, `THINK_THRESHOLD`, `SELF_COOLDOWN`) are overridable from
`.env` (see above); the persona/canon lives in `state/canon.md`.
