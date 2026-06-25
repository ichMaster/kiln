# kiln

Simplified chat with Claude as the brain — state-aware Haiku/Opus routing.

A chat engine with "needs": it lives in a loop of short ticks, sometimes speaks
up first on its own, and calls a stronger model for substantive turns. Cheap
conversation runs through Haiku, more complex turns through Claude (Opus). The
persona of your conversation partner is configured by a file; the models and
behavior — via `.env`.

> How it works internally — see [`docs/`](docs/)
> ([architecture](docs/architecture.md), [how it works](docs/how-it-works.md)).

## Installation

Requires Python 3.10+. The first time, create a virtual environment and install
the package (the `anthropic` dependency is needed only for the live chat branch;
dry-run runs on the standard library alone):

    python3 -m venv .venv
    source .venv/bin/activate          # Windows: .venv\Scripts\activate
    pip install -e .                   # or `pip install -e .[dev]` for tests/lint

After that, just keep `.venv` activated in each new terminal session.

## Running

**Dry-run** — a deterministic demo scenario, with no model calls at all:

    kiln                # or: python -m kiln

**Live** — interactively: ticks run on their own, you type into the terminal,
and messages are picked up on the next tick:

    KILN_LIVE=1 kiln    # or: KILN_LIVE=1 python -m kiln

Live mode has two requirements (per branch):

- **Chat (Haiku)** — the `ANTHROPIC_API_KEY` key in `.env` or in the environment.
- **Reasoning / tools (Opus)** — an installed and logged-in **Claude Code CLI**
  (`claude`), because this branch calls `claude -p`.

`KILN_LIVE=1` can also be set in `.env`, so you don't have to type it every time.

## Configuration

A local `.env` file (not committed) sets the models and behavior — without
editing code. Real environment variables take priority over the file.

| Variable | What it sets | Default |
|---|---|---|
| `CHAT_MODEL` | chat branch model | `claude-haiku-4-5-20251001` |
| `DEEP_MODEL` | reasoning branch model | `claude-opus-4-8` |
| `TICK_SECONDS` | tick length (sec; live only) | `0.5` |
| `THINK_THRESHOLD` | how easily a turn goes to the stronger model | `0.45` |
| `SELF_COOLDOWN` | pause (ticks) before the engine speaks again | `5` |
| `KILN_LIVE` | `1` — enable live mode | — |
| `ANTHROPIC_API_KEY` | key for the chat branch | — |

**Persona.** The voice and character of your conversation partner live in
[`state/canon.md`](state/canon.md) — it's plain text, edit it freely. If the
file is missing, the built-in fallback canon is used.

## Talking

Just type into the terminal. The engine replies on its own and **may speak
first** when its internal "need" has built up.

Lines starting with `/` are commands (they don't go to the model):

| Command | Action |
|---|---|
| `/status` | how many turns, the hottest need, mode |
| `/needs` | current need levels |
| `/memory` | contents of long-term memory |
| `/history` | the last turns of this session |
| `/ask <text>` | force a query to Claude (the stronger model) |
| `/clear` | clear the session history |
| `/help` | list of commands |
| `/quit` | exit |

## Where things are stored

- `state/` — needs, persona (`canon.md`), prompts, long-term memory.
- `history/` — the full transcript of each session in JSON (saved on exit;
  local, not committed). Handy for downstream RAG.

On exit (including via `/quit` or Ctrl-C) the conversation is summarized into
long-term memory, and the raw transcript is written to `history/` — so on the
next run the engine "remembers" previous conversations.
