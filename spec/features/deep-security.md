# Deep-branch security — the `claude -p` inventory

Status: **ground truth (v1.3.x) + the 1.4 target**. This document is the call-site inventory for
[ROADMAP §1.4](../ROADMAP.md) (Deep-branch security): exactly when kiln shells out to `claude -p`,
what each call carries, and how the phase changes it. Task 1 of the phase (the CLI flag audit)
extends this file with the verified flag semantics.

## When `claude -p` runs — the five call sites

Every path below spawns one `claude` subprocess per invocation. Everything else in kiln (the chat
branch, inner thoughts, the session summary) stays on the Haiku SDK and never shells out — see
[What never goes through `claude -p`](#what-never-goes-through-claude--p).

| # | Call | Fires when | Frequency |
|---|------|------------|-----------|
| 1 | Deep **think** | A user turn carries a reasoning marker (`THINK_HINTS`: «чому», «поясни», «проаналізуй»…); or the state weight `0.55·intensity + 0.45·connection` reaches `think_threshold` (0.45); or intensity is over its trigger threshold (0.75) when a user turn arrives; or the intensity **self-trigger** reaches out on the deep branch; or the operator forces it with `/ask <text>` | Per qualifying turn |
| 2 | Deep **tools** | A user turn carries an action marker (`TOOL_HINTS`: «файл», «запусти», «збережи», «прочитай», «пошукай», «знайди», «пошук») | Per qualifying turn |
| 3 | **Sub-agent** (session-wiki) | The novelty need crosses its threshold (0.85) — as a self-trigger on an idle tick, or ambient when a user turn arrives with novelty already high | Per novelty crossing (hysteresis + cooldown capped) |
| 4 | **Facts extraction** | Every session close — normal exit or Ctrl-C (the `finally` path), `/rotate`, or the auto-rotation timer (`rotate_every_hours`); skipped in dry-run and when `facts_enabled` is off | Once per session close |
| 5 | **Facts digest** | Every session start, when the store holds facts (same `facts_enabled` gate; dry-run stubs it) | Once per session start |

## How each call is built — the command anatomy

All five run through `subprocess.run` with `capture_output=True` and `env=claude_env()`. The
columns below are the parts that differ.

| # | Code path | Model | Tools granted | Prompt input | System prompt | Timeout |
|---|-----------|-------|---------------|--------------|---------------|---------|
| 1 | `routing.respond` → `brain.LiveBrain.deep(with_tools=False)` | `deep_model` (Opus 4.8) | none | prior transcript + current turn, via **stdin** | canon + memory + facts + world + mood, via `--append-system-prompt` (argv) | 180 s |
| 2 | `routing.respond` → `brain.LiveBrain.deep(with_tools=True)` | `deep_model` (Opus 4.8) | `--allowedTools Read,Write,Bash` (`config.DEEP_TOOLS`) | same, via stdin | same, via argv | 180 s |
| 3 | `routing.respond` → `brain.LiveBrain.tool(agent)` | the agent file's frontmatter `model:` (session-wiki: sonnet) | the frontmatter `tools:` (session-wiki: Read, WebFetch, WebSearch) | transcript + the run-your-instructions preamble, via stdin | same, via argv | 180 s |
| 4 | close path (`engine` `finally` / rotation worker) → `memory.extract_facts` | `deep_model` (Opus 4.8) | none | the whole prompt (known facts + transcript) as a **positional argument** | none | 180 s |
| 5 | start path (`engine.run` boot) → `memory.digest_facts` | `deep_model` (Opus 4.8) | none | the whole prompt (all stored facts) as a **positional argument** | none | 180 s |

Notes on the anatomy:

- Calls 1–3 pass the prompt via **stdin** because `--allowedTools` is variadic — a trailing
  positional prompt would be swallowed as another tool name. Calls 4–5 predate that fix and still
  pass the prompt as a positional argument, which makes the user's facts and the session
  transcript briefly visible in the process list (`ps`); 1.4 moves them to stdin.
- The system prompt rides on `--append-system-prompt` **in argv** for calls 1–3 — canon, memory
  summaries, and user facts are likewise visible in the process list. Local-only exposure, but the
  1.4 flag audit should check whether a settings file or stdin channel can carry it instead.
- Every call requests `--output-format json`, so one response carries the reply text (`result`),
  the token `usage`, and the CLI's actual `total_cost_usd` — which feeds the usage ledger.

## Invariants shared by every call (today)

- **Billing:** `claude_env()` removes `ANTHROPIC_API_KEY` from the environment, so every
  `claude -p` call bills through the CLI's own login (the subscription). Opus is never billed via
  the API key; conversely the SDK/Haiku paths never shell out.
- **Extended thinking is always on:** `claude_env()` sets `MAX_THINKING_TOKENS` (default 8000,
  `thinking_tokens` in config).
- **Failure degrades, never crashes:** a nonzero exit or a timeout becomes a
  `(deep error: …)` / `(<agent> error: …)` reply string (calls 1–3) or an empty result plus a
  console notice (calls 4–5). The tick loop and the exit path survive every CLI failure.
- **The 1.4 problem — what every call inherits today:** the working directory is wherever kiln
  runs (the repo root — code, `state/` persona files, and `.env` in scope), the environment is
  the operator's full shell environment minus the one stripped key, and the operator's own
  `~/.claude` settings — permission allow-rules, hooks, MCP servers — apply to the headless call.

## What never goes through `claude -p`

For contrast, the model calls that stay on the Anthropic SDK (Haiku, API-key billed): the **chat
branch** (`LiveBrain.chat`), **inner thoughts** (v0.10, through `brain.chat`), and the **session
summary** on close (`memory.summarize`). The **classifier** (`routing.classify`) is pure local
code (keyword hints + need weights) and stays local through 1.4; by the phase's DoD it never
runs on `claude -p` — the CLI only ever executes a decision already made. (A meaning-based
router on the chat model is its own phase, ROADMAP §1.5 — routing, not security.) Under 1.4 the
**facts extraction/digest pair joins this SDK list** (Sonnet, the `facts_model` tunable) —
after the phase, the only `claude -p` calls left are the sub-agents themselves.

## The 1.4 target — the same calls under the profile

Under ROADMAP §1.4 the conversational calls are built by the one builder (`kiln/security.py`),
which resolves the calling agent's `security.yaml` profile — and the facts pair leaves the CLI
for the SDK, so **`claude -p` keeps exactly one shape: `--agent <name>`**. Per call:

| Call today | Under 1.4 |
|------------|-----------|
| 1 — deep think | **Becomes the `deep` sub-agent** — a new kiln-authored `deep.md` (no tools; the persona's `deep_model` injected at workspace materialization, so per-agent config stays authoritative). The `think` class and the intensity trigger — now `intensity: {threshold: 0.75, action: tool, agent: deep}` in `needs_model.yaml` — fire it through the sub-agent path; satiation is unchanged (an agent named `deep` maps to the existing `SATIATION["deep"]`); `brain.LiveBrain.deep` retires. **`/ask` is deprecated and removed entirely** (command, help entry, and its special-cased deep call inside `run()`) — routing already sends reasoning turns deep, and `THINK_THRESHOLD=0` remains the calibration escape hatch. Built like every call: workspace cwd, minimal env, generated settings + MCP allowlist. |
| 2 — deep tools | **Retired as a raw armed call.** The `tools` turn fires the `hands` sub-agent instead, granted its frontmatter ∩ the profile. The main prompt never holds tools again. (How a turn gets the `tools` label is routing, not security — the keyword heuristic today, the ROADMAP §1.5 model router later.) |
| 3 — sub-agent | Same shape, gated: the agent must be listed in the profile's `agents:`, its definition is materialized into the workspace (kiln-owned), and web/MCP access comes from the profile clamp. |
| 4/5 — facts extract & digest | **Leave `claude -p` entirely** — both move to the Anthropic SDK on a new `facts_model` tunable (default Sonnet), API-billed like the chat branch and the session summary. No subprocess, so the positional-argv exposure disappears; tool-less by construction; faster session close/start (no CLI spin-up, no thinking budget). The Opus-refusal guard extends to `facts_model` — Opus stays subscription-only, never on the API key. |

And across all of them: `--max-turns` from the profile, the generated `--mcp-config` +
`--strict-mcp-config` pair (only the profile's servers from the kiln-owned `state/mcp.yaml`
registry), and one audit line per spawn in `.kiln/{id}/claude-audit.jsonl`.
