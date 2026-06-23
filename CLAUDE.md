# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`kiln` is a single-file Python prototype (`engine.py`, stdlib only) for a needs-driven chat engine.
It runs a loop of cheap local "ticks"; an LLM ("the brain") is invoked only on a condition
(user input, or a need crossing its threshold), and each invocation is **routed** to one of two
branches. The README, code comments, and conversational prompts are all in **Ukrainian** — keep
that voice when editing prompts or user-facing strings.

## Commands

```bash
# one-time setup (venv execution model)
python3 -m venv .venv
source .venv/bin/activate           # then `python` == .venv/bin/python
pip install -r requirements.txt

python engine.py                    # dry-run: deterministic demo via ScriptedChannel (no API/CLI calls)
KILN_LIVE=1 python engine.py        # live: interactive StdinChannel; ticks run on their own, you type into the terminal
```

The venv + `requirements.txt` exist only for the **live chat branch** (`anthropic` SDK). Dry-run
and the live *deep* branch (which shells out to the `claude` CLI) are stdlib-only, so
`python3 engine.py` works without the venv too. Live mode also needs the **Claude Code CLI**
installed and logged in (the deep branch calls `claude -p`). There is **no test suite** — the
dry-run demo at the bottom of `engine.py` (`if __name__ == "__main__"`) is the de-facto smoke test.

### State files live in `state/`

All mutable state is under `STATE_DIR = engine.py's dir / "state"`: `state/needs.md` (seed need
levels, rewritten each run by `save_state`), `state/prompts.md` (self-trigger prompts),
`state/canon.md` (the **canon** — the persona/voice that becomes the system prompt of both
branches), and `state/memory.md` (cross-session summaries, generated on exit — gitignored).
`run()` calls `STATE_DIR.mkdir(exist_ok=True)` before reading, so a fresh clone never crashes;
each loader falls back to a default if its file is missing (`load_canon` → `DEFAULT_CANON`,
empty needs/prompts otherwise).

### Config via `.env`

`load_dotenv()` (a tiny stdlib-only `KEY=VALUE` parser at the top of `engine.py`, no dependency)
reads `.env` from the repo root **before** the config constants are defined, so these can be set
without touching code: `CHAT_MODEL`, `DEEP_MODEL`, `TICK_SECONDS`, `THINK_THRESHOLD`,
`SELF_COOLDOWN` (plus `KILN_LIVE`, `ANTHROPIC_API_KEY` for live mode). It uses `os.environ.setdefault`,
so a real environment variable always wins over `.env`. `.env` is gitignored; the structured dict
knobs (`DRIFT`/`SATIATION`/`NEED_TRIGGERS`) stay in code.

## Architecture (one file, several layered concerns)

The whole engine is `engine.py`; section banners (`=== ... ===`) divide it. The big-picture flow:

**Tick loop** (`run`) — drifts needs upward each tick (`drift`), then picks exactly one action
per tick with this priority: **user input > self-trigger > idle**. Input always wins; a pending
self-trigger waits for the next tick.

**Two brains** (the core idea — *which branch answers decides which needs close*):
- **Chat** (`chat_reply`, `CHAT_MODEL` Haiku): cheap/fast small talk via the Anthropic Messages
  API. Whole session `history` goes in as a `messages` array. **Currently a stub** — live mode
  raises `NotImplementedError`; wire the real `anthropic` SDK call here (the docstring shows the
  shape).
- **Deep** (`deep_reply`, `DEEP_MODEL` Opus): reasoning/tools via the `claude -p` subprocess.
  Since the subprocess holds no session, prior history is flattened into the prompt as a text
  transcript (`to_transcript`); long-term memory rides on `--append-system-prompt`; `tools` class
  adds `--allowedTools`.

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
classification, so they never reach a brain (`/status`, `/needs`, `/memory`, `/history`,
`/ask <text>` forces deep, `/clear`, `/help`, `/quit`).

**Cross-session memory** — on exit (incl. Ctrl-C, via `finally`) the session is summarized through
`claude -p` (`summarize`) and appended to `state/memory.md` with a datestamp (`save_summary`). On
start, all past summaries load into the system prompt of *both* branches (`load_memory` →
`build_system`). `history` and `memory.md` are append-only — no trimming/windowing yet.

## Calibration

All tuning lives in module-level constants near the top of `engine.py`: `TICK_SECONDS`, `DRIFT`,
`SATIATION`, `NEED_TRIGGERS`, `SELF_COOLDOWN`, `THINK_THRESHOLD`, `CHAT_MODEL`/`DEEP_MODEL`,
`DEEP_TOOLS`/`DEEP_SKILLS`, `THINK_HINTS`/`TOOL_HINTS`, and the weights in `turn_weight`. The
scalar ones (models, `TICK_SECONDS`, `THINK_THRESHOLD`, `SELF_COOLDOWN`) are overridable from
`.env` (see above); the persona/canon lives in `state/canon.md`.
