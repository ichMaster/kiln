# Mission — kiln

## In one sentence

kiln is a **cheap, always-on tick-server** that hosts living text agents — a
persona (currently **Agnika**) that lives on a loop of cheap local ticks, spends
the expensive model rarely, and grows into a **hub for many agents**.

## What we are building

A persona engine built **server-first and cheap-first**. Unlike a chat bot that
only runs while you type, kiln's agent **lives on a loop of cheap local ticks**:
needs drift on their own, and the model ("the brain") is invoked only on a
condition — you wrote something, or a need crossed its threshold. When it fires,
the turn is routed to the cheapest brain that fits: a fast **Haiku** chat for
small talk, the heavier **Opus** (`claude -p`) only for reasoning or tools. The
loop keeps running between turns, so the agent can think and reach out on its own.

The interface grows separately: a terminal **TUI** first, then the engine becomes
a **WebSocket/HTTP server** that thin clients (TUI, web) attach to. The end-state
is a **hub**: one tick-server runtime hosting *many* agents — Agnika and
Lumi-like personas — each with its own canon, memory, tools, and **permission
scope**.

kiln is a **distinct approach**, not a rebuild of **Lumi** (`~/development/lumi`):
Lumi is the mature persona reference we port pieces from (TUI, RAG, memory, inner
monologue); kiln's own contribution is the **cheap tick-server core** and the
**multi-agent host**.

## For whom

A private project for myself and a close circle — never an open public service.
**Agnika** is the **home agent**: always connected, with **elevated permissions**
(broad tools / system / home access). Other hosted agents (a Lumi-style
companion) run at a lower scope.

## Principles

- **Cheap by default.** The tick loop is free (pure local state); the model fires
  rarely and on the cheapest brain that fits. Conserving the expensive model
  (Opus) is a design constraint, not an afterthought.
- **The agent lives between turns.** The tick loop runs always; needs drift, and
  the agent can self-trigger. State is the substrate, not the conversation.
- **Server-first.** The engine is meant to be a server clients attach to; it keeps
  ticking with no client connected. Designed around an `agent_id` from the start.
- **Core independent of interface.** All of an agent's logic lives in the core;
  TUI/web are just clients (Lumi's principle; the bridge is an echo-free bus).
- **Which brain answered decides what closed.** Needs close from the event that
  served them — cheap chat gives contact, deep gives "filling-meal" satisfaction
  — so routing is meaningful, not cosmetic.
- **A hub of agents, with permissions.** One runtime hosts many agents; each
  carries a permission scope. Agnika (home) is privileged; companions are narrow.
  Multi-agent is additive, designed in from the start.
- **Port the persona, own the server.** Persona depth (TUI, RAG, memory, mood,
  inner monologue) is ported from Lumi; the cheap tick-server and the hub are kiln's.
- **One model to start, cheap-first.** Haiku for chat (Anthropic SDK), Opus via
  the `claude -p` CLI for deep — behind seams, switchable in `.env`.
- **Local and private.** Runs locally; the model is a cloud API
  (`ANTHROPIC_API_KEY` in `.env`). Private by design, not offline.

## Non-goals

- Not a TUI-first persona showcase (that's Lumi) — kiln's centre of gravity is the
  cheap tick *server* and the agent hub.
- No open public sign-up; the hub stays a closed, admin-managed circle.
- Not a virtual world or a body — those are the *games* (clay / silt / …), worlds
  a kiln brain can drive, not kiln itself.

## Glossary

- **Tick** — one iteration of the cheap local loop; needs drift each tick, the
  model is not called unless a condition fires.
- **Need** — a drive (`connection` / `rest` / `novelty` / `intensity`) in `0..1`
  that drifts up over time and is closed by events; gates when the agent acts.
- **Self-trigger** — the agent speaking first when a need crosses its threshold
  (hysteresis + cooldown).
- **Two brains** — the cheap **chat** branch (Haiku, Anthropic SDK) and the
  **deep** branch (Opus via `claude -p`), chosen by routing.
- **Canon** — the stable persona (Agnika): character, voice, natal seed; the
  system prompt.
- **Agent** — a hosted mind (canon + memory + tools + permission scope) running on
  the tick-server. Agnika is one; Lumi-like personas are others.
- **Agent host / hub** — the tick-server running many agents, keyed by `agent_id`.
- **Home agent** — Agnika: always-connected, elevated permissions (system/home access).
- **Permission scope** — the tools/access an agent is granted; per-agent.
- **Transcript** — the raw per-session `history/*.json` (the RAG corpus); distinct
  from the `memory.md` summaries.
- **Inner monologue** — a background cheap-model loop where the agent thinks
  between turns (planned; ported from Lumi).
