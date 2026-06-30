# Feature: FSM — the agent's behaviour as an explicit, declarative state machine

Status: **design**. The end concept that ties four roadmap phases together: **1.3** (FSM core), **1.5**
(Tools — the action vocabulary), **1.6** (Declarative FSM in YAML), and **1.7** (non-blocking
execution). Companion: [ROADMAP.md](../ROADMAP.md) §1.3/1.5/1.6/1.7,
[server-architecture.en.md](server-architecture.en.md) (the event queue / thread model),
[ARCHITECTURE.md](../ARCHITECTURE.md).

## The idea

Today an agent's behaviour lives **implicitly** inside `engine.run()`'s tick loop — a fixed `if/elif`
priority (input > reach-out > thought > idle) with a rest-gate. It works, but it is Python, it is one
machine for every agent, and its states/transitions can't be inspected or tested as a machine.

The goal is to make that behaviour an **explicit state machine**, and ultimately a **per-agent,
declarative** one: each agent's states and transitions live in a YAML file, the actions it fires are
**tools**, and the engine just interprets it. So an agent differs from another not only in its
*calibration* (needs / mood / persona) but in its **behavioural logic** — all in config, no Python.

It arrives in four layers, each a roadmap phase:

1. **FSM core (1.3)** — an explicit, **table-driven** state machine + a unified event queue. The
   current behaviour becomes the *default* transition table (still in Python). Behaviour-preserving.
2. **Tools = the action vocabulary (1.5)** — the tool registry is designed so that **every action the
   FSM can fire is a tool**. Built-ins (`chat`/`deep`/`reach_out`/`think`/`idle`/`rotate`) are built-in
   tools; user tools extend the set; the per-agent permission scope gates which an agent may fire.
3. **Declarative FSM (1.6)** — `state/{id}/fsm.yaml` defines an agent's states + transitions + actions;
   the 1.3 engine interprets it; a missing/broken file heals to the default table.
4. **Non-blocking execution (1.7)** — the model call runs on a worker so the machine stays in
   `thinking` and **keeps draining the queue** while it runs.

## What exists today (the implicit FSM)

The machine is already there, just not named. In `engine.run()`:

- **States** are emitted as the snapshot's `status_label`: `idle`, `responding`, `thinking` (inner
  monologue), `resting` (the rest-gate). (`cooling` is named in the old roadmap but isn't a real state
  yet — per-need cooldowns live in `TriggerBook.cooldown`, never surfaced as a status.)
- **Transitions** are a hardcoded `if/elif` with the fixed priority **input > reach-out > thought >
  idle**; `resting` preempts the self-triggers.
- **Events** come from three different mechanisms: user input via `channel.poll()` (one message per
  tick off the inbox queue), self-triggers *computed each tick* from need levels (not queued), and
  rotation results off a separate `rotate_results` queue.
- The **model call blocks the agent thread**. Per-agent threading means one agent's block doesn't
  freeze the others, but *within* an agent a long `deep` call stalls commands/status until it returns.

So the substance is present but **implicit, heterogeneous, and blocking-within-the-agent** — which is
exactly what the four phases make explicit, unified, and (optionally) non-blocking.

## The model — states, events, actions, guards, transitions

- **States** — `idle`, `thinking`, `responding`, `cooling`, `resting` (extensible per agent).
- **Events** — drained one at a time from a single typed **event queue**:
  `user.message`, `command`, `self_trigger:<need>` (connection / reflection / …), `tick`,
  `rotate.request`, `response.ready` (internal, from the worker in 1.7), and **reserved** for later
  phases: `peer.message` (1.8), `room.message` (1.10), `tool.result` (1.5).
- **Actions = tools** — a transition fires one action, and **every action is a tool** (see 1.5): the
  built-ins `chat` / `deep` / `tool:<name>` / `reach_out` / `think` / `idle` / `rotate`, plus any user
  tool the agent's scope allows.
- **Guards** — simple predicates over the agent's state: need levels and boolean flags
  (`rest >= 0.9`, `self_messages`, `connection >= 0.8`). **Never arbitrary code** — a constrained
  expression, so the machine stays inspectable and safe.
- **Transition** — `(state, event, guard) → (action, next_state)`.

## The transition table (the one key abstraction)

The 1.3 engine interprets a **transition table as data**, not hardcoded `if/elif`. The *default* table
— which reproduces today's behaviour exactly — lives in Python and ships with 1.3. A per-agent
`fsm.yaml` (1.6) is simply a different table loaded from a file. **This is the decision that makes the
whole vision cheap:** because 1.3 reads a table, the YAML phase is a thin loader, not a rewrite, and
the same engine, tracing, and tests serve both.

## The declarative language (1.6)

`state/{id}/fsm.yaml` — states, the events each reacts to, an optional guard, the action (a tool) to
fire, and the next state. A sketch (the default machine, written out):

```yaml
initial: idle
states:
  idle:
    on:
      user.message:            { action: classify_and_reply, to: responding }
      self_trigger:connection: { guard: "self_messages and connection >= 0.8",
                                 action: reach_out, to: responding }
      self_trigger:reflection: { guard: "reflection >= 0.6", action: think, to: thinking }
      tick:                    { guard: "rest >= 0.9", action: enter_rest, to: resting }
  responding:
    on: { response.ready: { action: emit, to: cooling } }
  thinking:
    on: { response.ready: { action: store_thought, to: cooling } }
  cooling:
    on: { tick: { to: idle } }          # cooldowns tick down; back to idle
  resting:
    on: { tick: { guard: "rest <= 0.85", action: wake, to: idle } }
```

- **Actions reference tools by name** (`reach_out`, `think`, `classify_and_reply`, …). Unknown actions
  fail validation at load.
- **Guards** are the small predicate expression over needs + flags.
- A missing or invalid `fsm.yaml` **heals to the default table** (like every other per-agent config) —
  a broken file never crashes the agent.

## Tools as the action vocabulary (1.5 is designed for the FSM)

The Tools phase is built **for** the FSM, not beside it. Its registry is the action vocabulary the
machine fires; the design constraints follow from that:

- Every built-in behaviour is registered as a **built-in tool** with a typed signature, so the FSM
  fires `chat` / `deep` / `reach_out` / `think` / `idle` / `rotate` through the same call path as a
  user tool.
- User-registered tools (the 1.5 headline) join the *same* vocabulary — so an agent's `fsm.yaml` can
  fire them without any engine change.
- The **per-agent permission scope** (set in v1.2, enforced in 1.5) gates which tools an agent's FSM
  may fire — a narrow companion's machine simply can't name a tool outside its scope. The FSM and the
  permission model meet at the registry.

## Non-blocking execution (1.7 — the "C" piece)

A long `deep` call shouldn't freeze the machine. kiln already does exactly this for rotation
(`_finalize_async` computes off the agent thread and hands the result back through a queue). 1.7
generalises it: an action that calls the model runs on a **worker**, the machine sits in `thinking` /
`responding`, and the loop **keeps draining the event queue**; the reply returns as a `response.ready`
event that drives the next transition. Then a `/status` (or a queued peer message) is handled *during*
a long call, not after it. It's an optimization of the runtime — a separate version, slotted after the
declarative phase — and the social phases (1.8+) inherit it for free.

## Why a state machine (vs. the current loop)

- **Explicit + testable** — transitions become `advance(state, event) -> state`, unit-testable, instead
  of buried `if/elif`.
- **Unified** — one event queue replaces "a queue for input, polling for triggers, a third queue for
  rotation"; priority becomes a queue policy.
- **Extensible** — every later capability is just a new event type on the queue: peer messages (1.8),
  room messages (1.10), tool results (1.5). The social phases plug in instead of bolting on.
- **Per-agent + declarative** — an agent's behaviour is config (`fsm.yaml`), not code; Pashu can think
  and reach out on her own logic, not Agnika's.

## Guardrails (so the DSL stays sane)

1. **A constrained statechart, not a programming language.** Guards are predicates over needs/flags,
   never arbitrary Python — keep it inspectable and out of the security surface.
2. **Tracing is mandatory.** A YAML-driven machine is opaque without a log of every
   `state → event → guard → action → state`; ship it with the engine.
3. **Validate at load.** Catch unreachable states, missing transitions, unknown actions/tools; heal a
   broken file to the default table — never crash the agent.
4. **The DSL gets its own short spec** before 1.6 implements it (the exact event names, the guard
   grammar, action parameters).

## Relation to the rest of the system

- **Event queue ⇄ the network bus.** The inbox (`ServerChannel`) that the WS layer feeds today becomes
  one producer on the FSM's event queue; commands, self-triggers, and (later) peer/room messages are
  siblings on it. The thread-per-agent model is unchanged — the server-architecture doc already notes
  "the async/FSM rewrite can come without changing the protocol".
- **Inter-agent (1.8) / group chats (1.10).** §14 of the server-architecture doc says the host "drops a
  peer message onto the other agent's inbox" — that inbox *is* this queue, and a peer/room message is
  just another event type the FSM reacts to.
