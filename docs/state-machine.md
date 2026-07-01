# The state machine — internals (triggers, events, transitions)

An engineering-level view of how the needs vector drives the FSM: the trigger layer, the event layer,
the transition matrix, and a worked example traced cell-by-cell against that matrix. For the
higher-level runtime overview see [how-it-works.md](how-it-works.md#the-state-machine); for the design
rationale and where this is headed (declarative per-agent FSMs, simulation) see
[../spec/features/fsm.md](../spec/features/fsm.md).

The short version: **needs vector → trigger (edge detector) → event (normalized FSM input) → arbiter
(pick one) → `advance()` (pure transition) → action → writes back to the needs.** The FSM never reads
the needs or the triggers directly; it consumes exactly one `event` per tick plus a read-only view
(`ctx`) of the needs. Two things reach the FSM from the needs, by two different routes — that is the
part most people miss, and it is spelled out in §3.

## 1. The pipeline (one tick)

```
     ┌──────────────────────────────────────────────────┐
     │  NEEDS VECTOR   x = {connection, rest, novelty…}  │◄──────────────┐
     │  continuous, x ∈ [0,1]^n ; drift() integrates it  │               │
     └────────┬──────────────────────────────┬──────────┘               │
              │ read levels                   │ read levels              │
      PATH A  │ (threshold crossing → event)  │ PATH B (direct, via ctx) │
              ▼                               ▼                          │
     ┌──────────────────┐              ┌──────────────┐                  │
     │ TRIGGER          │              │ ctx = view   │                  │
     │ edge detector    │              │ of x + cfg   │                  │
     │ + latch (armed)  │              └──────┬───────┘                  │
     │ + refractory     │                     │                         │
     └────────┬─────────┘                     │                         │
              │ need name | None              │                         │
              ▼                               │                         │
     ┌──────────────────┐  stdin, timer,      │                         │
     │ PRODUCERS        │  heartbeat also     │                         │
     │ → Event(kind,pl) │  produce events     │                         │
     └────────┬─────────┘                     │                         │
              │ queue.put(e)                  │                         │
              ▼                               │                         │
     ┌──────────────────┐  holds this tick's  │                         │
     │ EVENT QUEUE      │  0..N candidates;    │                         │
     │ drain_one() → 1  │  pops 1 by priority  │                         │
     └────────┬─────────┘                     │                         │
              │ exactly 1 event               │                         │
              ▼                               ▼                         │
     ┌───────────────────────────────────────────────┐                 │
     │ advance(fsm_state, event, ctx)                 │                 │
     │ PURE table lookup → (action, next_state)       │                 │
     └───────────────────┬───────────────────────────┘                 │
                         │ action name                                  │
                         ▼                                              │
     ┌───────────────────────────────┐                                 │
     │ registry.fire(action, …)       │  side effect:                   │
     │                               │  apply_satiation(x, event) ──────┘  (writes needs back)
     └───────────────────┬───────────┘
                         │ next_state → fsm_state   (persists 1 bit: the resting latch)
                         ▼
                     next tick
```

`Path A` and `Path B` are the two coupling routes from the needs into the FSM. `apply_satiation` is
the feedback edge that closes the loop, so the needs the next tick reads are the ones this tick's
action just changed.

The stage boundaries map straight onto the code: `drift` / `apply_satiation` update `state.needs`;
`select_self_trigger` / `select_thought_trigger` are the triggers; `input_event` /
`self_trigger_event` / `rotate_event` / `tick_event` are the producers; `EventQueue.drain_one` is the
arbiter; `advance` is the transition function; `registry.fire` runs the action.

### 1.1 The event queue (zoom on the arbiter)

The `EventQueue` (`kiln/fsm.py`, KILN-062) is implemented and unit-tested. Each tick the producers
append their events to it with `put()`; `drain_one()` then sorts by `EVENT_PRIORITY`, returns the
single winner, and clears the rest. It is a **one-tick candidate buffer**, not a long-lived backlog:
non-winners are simply recomputed next tick (a self-trigger that loses to input is re-derived next
tick). The only genuinely persistent buffers sit *upstream* of the producers — the `ServerChannel`
inbox (client messages) and the `rotate_results` queue (finished async rotations) — and the producers
pull from those.

```
 producers (each emits 0 or 1 event this tick):
   input_event(channel.poll())    → USER_MESSAGE / COMMAND / —
   self_trigger_event(reach,refl) → SELF_TRIGGER(need)     / —
   rotate_event(do_rotate)        → ROTATE                 / —
   tick_event()                   → TICK   (always present)
                       │
                       │ queue.put(e)  for each non-None
                       ▼
        ┌──────────────────────────────────────────────┐
        │  EventQueue — this tick's candidate set        │
        │  example: [ SELF_TRIGGER("connection"), TICK ] │
        └───────────────────────┬──────────────────────┘
                                │ drain_one():
                                │   1. sort by EVENT_PRIORITY (0..3)
                                │   2. pop the lowest number  (the winner)
                                │   3. clear the remainder
                                ▼
                     one Event  →  advance(state, event, ctx)
```

So the queue is where the `input > self-trigger > rotate > tick` priority is actually applied. With
`[SELF_TRIGGER("connection"), TICK]`, the self-trigger (priority 1) beats the tick (priority 3), so
`advance` receives the self-trigger and the tick is discarded. If you had also typed that tick, a
`USER_MESSAGE` (priority 0) would have been in the set too and would have won instead.

## 2. A trigger is an edge detector

A trigger is a **rising-edge detector on a threshold comparator, with a latch and a refractory
counter**. Its whole purpose is to turn a *level* signal (a need sitting above its threshold) into a
*single edge* (one event per crossing), so the FSM acts once per crossing instead of every tick.

```
connection(t):
 0.90                    ╱╲                            ╱────
 0.80 ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈╱┈┈╲┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈╱┈┈┈┈  threshold = 0.80
              ╱────────╱     ╲ (satiation drop)       ╱
 0.50 ───────╱                ╲─────────────────────╱
             t0      t1                              t3        → time (ticks)

(a) LEVEL comparator  (need ≥ 0.80):
        ________|▔▔▔▔▔▔|__________________|▔▔▔▔▔▔    TRUE for the whole span
                                                     → would fire EVERY tick (spam)

(b) TRIGGER output  (edge + latch):
        ________|▔|_______________________|▔|_____    ONE pulse per crossing (t1, t3)

    armed:  ▔▔▔▔▔|_|▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔|_|▔▔▔▔    disarms on fire,
                                                      re-arms only after level < 0.80
```

At `t1` the level crosses 0.80, the trigger emits one pulse carrying the string `"connection"`, and
the latch (`armed`) flips to `false`. The action that follows (a reach-out) calls `apply_satiation`,
which drops `connection`; that re-arms the latch. Nothing fires again until the next crossing at `t3`.
The `cooldown` counter is a second guard — a fixed number of silent ticks after a fire — for the case
where satiation barely moves the need. In control terms: `armed` is the latch, `cooldown` is a
refractory period; together they debounce the comparator. This lives in `TriggerBook` (fields
`armed: dict[str,bool]`, `cooldown: dict[str,int]`); `_crossing_trigger` is the detector,
`select_self_trigger` runs it on `connection`, `select_thought_trigger` on `reflection`. A fired
detector becomes `Event(SELF_TRIGGER, need_name)`.

## 3. Two routes from needs → FSM

Not every need→behaviour link goes through a trigger. Reach-out and inner-thought do (Path A); the
rest gate does not (Path B).

```
 PATH A  (reach-out, inner thought)          PATH B  (rest gate)
 ───────────────────────────────────         ─────────────────────────────
 need crosses threshold                       event is just a plain `tick`
     │                                            │
     ▼                                            ▼
 trigger fires → Event(SELF_TRIGGER, need)    guard reads ctx directly:
     │                                        _should_rest: ctx.rest ≥ 0.90
     ▼                                        _can_wake:    ctx.rest ≤ 0.85
 transition keyed on kind + payload               │
 (_is_reach_out / _is_reflect compare             ▼
  payload against ctx.reach_out_need)         transition fires with NO trigger
```

The reason the same "fire once per crossing" requirement has two implementations is whether the
**resulting mode is persistent**:

- `RESTING` is a persistent state, so hysteresis is free — two thresholds (enter ≥ 0.90, wake ≤ 0.85)
  plus the fact that you are *in* the state give you the latch for nothing. No `TriggerBook` needed;
  the state itself is the latch.
- `responding` / `thinking` are **transient** — they last one tick and fall back to `idle`. There is
  no persistent state to hold "I already fired for this crossing," so that memory is externalised into
  `TriggerBook.armed`. That is exactly why a separate trigger object exists for these but not for rest.

### Why two paths, not one

The two paths exist because reach-out/think and the rest gate have genuinely different requirements,
and neither mechanism serves both well.

**Path A (trigger → event)** is required when the coupling must (1) be *edge-detected* — fire once per
crossing, because the resulting mode (`responding`/`thinking`) is transient and cannot hold that
memory itself; (2) *carry identity* — which need crossed, so the transition can route `reach_out` vs
`think` from the payload; and (3) be *arbitrated as a first-class event* — a reach-out must lose to a
user message the same tick. A plain guard can do none of these: `connection ≥ 0.80` as a guard would
be true every tick (no edge), carries no routing information, and is not an event the arbiter can rank.

**Path B (direct guard)** is sufficient when the coupling is (1) a *pure level predicate* with no
identity to carry — "am I tired enough / recovered enough"; and (2) *already debounced by a persistent
state*, so no external latch is needed (`RESTING` is the latch). Wrapping it in a trigger + event
would only duplicate machinery the state already provides.

In one line: use a trigger+event when the outcome is a transient mode that must compete as an event;
read the level directly in a guard when the outcome is a persistent state that debounces itself.

### How the two paths are evaluated (they are not parallel)

They are **not** two concurrent machines. It is one synchronous tick on one thread. Both paths are
*live* every tick, but they are evaluated at different stages and they converge — and compete — at the
single event `drain_one` selects:

```
 tick:
   1. Path A runs (producer stage):   triggers fire → SELF_TRIGGER queued (or not)
   2. drain_one() picks ONE event by priority
   3. advance(state, event, ctx):
        winner = SELF_TRIGGER  → Path A drives the transition (via payload)
        winner = TICK          → Path B's guards (_should_rest / _can_wake) are read
        winner = USER_MESSAGE  → neither need-path drives it
```

So Path A and Path B never both drive a transition in the same tick. Path A's event is priority 1;
Path B's guards ride the `tick` event at priority 3 — so a live Path A **preempts the very tick** Path
B would have guarded. They "run together" only in the sense that both are recomputed each tick from
the current needs; the arbiter serialises which one actually moves the machine. And while `resting`,
the producer emits no self-triggers at all, so Path A goes silent and Path B alone governs waking.

(One corner this exposes for the KILN-064 rewrite: on an active tick where a need-crossing and
`rest ≥ 0.90` coincide, the `SELF_TRIGGER` wins the arbiter, so entering rest is deferred to the next
otherwise-idle tick. v1.2 resolves that tie the other way — it latches `resting` at the top of the
loop and skips the reach-out — so the behaviour-preserving rewire has to match v1.2 here, not the
naive arbiter order.)

## 4. States and events

**States** (`fsm.State`). `idle`, `responding`, `thinking`, and `cooling` are the **active** states —
the machine reacts to the next event identically from any of them; they differ only as a label of what
the last tick did. `resting` is the one genuinely distinct state (the rest gate). Each one in detail:

| State | What it is | Entered by | Left by | Persistence / how you see it |
|---|---|---|---|---|
| `idle` | The awake-but-quiet default: needs drift, nothing is said. | `idle` action (a quiet `tick`), `wake` from resting, and after `command` / `rotate`. | the next event — input, a self-trigger, or the rest-crossing. | transient; the resting-hand position of the machine. |
| `responding` | She produced a turn this tick — a user answer (`respond`) or a reach-out where she spoke first (`reach_out`). | `user.message`, or a reach-out `self_trigger`. | the next quiet `tick` (`idle`), or another turn. | transient (one tick); her reply appears. |
| `thinking` | She formed a private inner thought (`think`), discharging `reflection`. | a reflection `self_trigger`. | the next quiet `tick` (`idle`). | transient (one tick); usually nothing shown (~1/M surfaced). |
| `cooling` | A post-turn settling state where per-need cooldowns tick down before returning to `idle`. | (not yet — a turn currently returns straight to `idle`). | a `tick` once cooldowns clear. | **defined but unused until KILN-065**; behaves as an active state today. |
| `resting` | The rest gate: `rest` hit its enter threshold, so she stops engaging and only recovers. | `enter_rest` (a `tick` with `rest ≥ 0.90`). | `wake` (a `tick` with `rest ≤ 0.85`). | **persistent** — holds across ticks; a user line gets `rest_ack` (heard, no brain call), commands still work. |

The key split is transient vs persistent. `idle`/`responding`/`thinking`/`cooling` are transient
labels — each is just "what the last tick did," and the machine drops back toward `idle` on the next
quiet tick, so they share one row in the matrix (§5). `resting` is the only state that genuinely
*holds*: it persists across ticks and carries the hysteresis latch itself (enter at 0.90, wake at
0.85), which is why it needs no external trigger (see §3, Path B). `cooling` exists in the enum so the
post-turn cooldown window can become an observable state, but until KILN-065 lands it is never entered.

**Events** (`fsm.EventKind`). The live set is `user.message`, `command`, `self_trigger` (payload = the
need name), `tick`, and `rotate.request`. Three more are reserved and unused this phase: `peer.message`
(1.6), `room.message` (1.11), `tool.result` (1.5).

**Event priority** (`EVENT_PRIORITY`, used by the arbiter): the arbiter pops exactly one event per
tick, lowest number wins.

```
user.message = command  (0)   >   self_trigger  (1)   >   rotate.request  (2)   >   tick  (3)
```

## 5. The transition matrix

Rows are the current state, columns are the incoming event (guard-qualified where a guard splits the
outcome). Each cell is `action → next_state`. State abbreviations: `I` = idle, `RE` = responding,
`TH` = thinking, `RS` = resting. "active" is any of idle / responding / thinking / cooling.

| From \ Event | `user.message` | `command` | `self_trigger` (reach) | `self_trigger` (reflect) | `rotate.request` | `tick` [rest ≥ 0.90] | `tick` [rest ≤ 0.85] | `tick` [else] |
|---|---|---|---|---|---|---|---|---|
| **active** (I/RE/TH/cool) | `respond`→RE | `command`→I | `reach_out`→RE | `think`→TH | `rotate`→I | `enter_rest`→RS | `idle`→I | `idle`→I |
| **resting** (RS) | `rest_ack`→RS | `command`→RS | — (not produced) | — (not produced) | `rotate`→RS | `idle`→RS | `wake`→I | `idle`→RS |

Reading the matrix:

- **Priority is upstream of the matrix.** The arbiter has already reduced the tick to one event, so the
  matrix maps a single event — it does not re-encode priority. If you typed while a reach-out was due,
  the arbiter handed the matrix `user.message`, not `self_trigger`.
- **The `tick` column is split by guard** because the same `(state, tick)` pair has different outcomes
  depending on the rest level. For an **active** state the only guarded outcome is `rest ≥ 0.90 →
  enter_rest`; anything else is `idle`. For **resting** the only guarded outcome is `rest ≤ 0.85 →
  wake`; anything above 0.85 stays `resting`. That split (enter at 0.90, wake at 0.85) is the
  hysteresis band.
- **`self_trigger` is absent from the resting row** because the producer does not emit self-triggers
  while resting (`select_self_trigger` runs only when not resting). `advance` would fall through to its
  default `idle` if one ever arrived, but it never does.
- **Every active state shares one row** because idle / responding / thinking / cooling are
  behaviourally identical for the *next* event; the difference between them is only which action ran
  last tick.

## 6. Worked example, traced against the matrix

A single run that exercises most cells. Tracking `connection` (conn) and `rest`. Assume a tense agent,
so the reach-out and the `/ask`-style turn both route to the deep brain. Satiation used:
`deep` → conn −0.60, rest +0.40; `chat` → conn −0.30, rest +0.20; `idle` → rest −0.01; drift moves
`rest` −0.005 and `conn` +0.001 per tick. Numbers are representative, rounded for legibility.

| Tick | State before | Event (guard) | Matrix cell | Action | Needs after | State after |
|---|---|---|---|---|---|---|
| 1 | idle | `self_trigger`, need=`connection` (reach) | active × self_trigger(reach) | `reach_out` (deep) | conn 0.80→0.20, rest 0.30→0.70 | responding |
| 2 | responding | `tick` (rest 0.695 < 0.90) | active × tick[else] | `idle` | rest 0.695→0.685 | idle |
| 3 | idle | `user.message` "поясни рекурсію" | active × user.message | `respond` (deep) | rest 0.680→1.00 (clamped) | responding |
| 4 | responding | `tick` (rest 0.995 ≥ 0.90) | active × tick[rest ≥ 0.90] | `enter_rest` | rest 0.995→0.985 (says rest line once) | resting |
| 5 | resting | `tick` (rest 0.980 > 0.85) | resting × tick[else] | `idle` | rest 0.980→0.970 | resting |
| 6 | resting | `command` "/status" | resting × command | `command` | (status printed; rest drifts to 0.960) | resting |
| 7 | resting | `user.message` "привіт" | resting × user.message | `rest_ack` | rest 0.955→0.945 (heard, no brain call) | resting |
| 8–13 | resting | `tick` ×6 (0.85 < rest ≤ 0.90) | resting × tick[else] | `idle` | rest decays ≈0.015/tick: 0.945→0.855 | resting |
| 14 | resting | `tick` (rest 0.840 ≤ 0.85) | resting × tick[rest ≤ 0.85] | `wake` | rest recovering | idle |

Step by step, in matrix terms:

- **Tick 1** — `connection` drifted up to 0.80 and the trigger fired (Path A), so the arbiter had a
  `self_trigger(connection)` and a `tick`; `self_trigger` (priority 1) outranks `tick` (3). The active
  row, `self_trigger(reach)` column, gives `reach_out → RE`. Because the agent was tense,
  `reach_out_branch` picked the deep brain; its satiation dropped `connection` to 0.20 and, being work,
  raised `rest` to 0.70.
- **Tick 2** — quiet, and `connection` (0.20) is far below threshold, so the trigger produced nothing
  and re-armed. Only a `tick` reached the arbiter. `rest` is 0.695, below 0.90, so the active
  `tick[else]` cell gives `idle → I`. That is the whole reason the machine "falls back" to idle: it is
  the else-branch of the tick column.
- **Tick 3** — you typed. `user.message` (priority 0) is top, so the matrix sees `user.message`, not a
  tick. The active row gives `respond → RE`; `classify` routed "поясни…" to the deep brain, whose
  satiation pushed `rest` to the 1.0 clamp.
- **Tick 4** — quiet again, but now `rest` is 0.995. The active row's `tick` column is guard-split, and
  `rest ≥ 0.90` selects `enter_rest → RS`. This is Path B: no trigger was involved; the guard
  `_should_rest` read `ctx.rest` directly inside `advance`. The rest line is announced once here.
- **Ticks 5–13** — she is in the resting row now. Every `tick` while `rest` stays above 0.85 lands in
  the resting `tick[else]` cell, `idle → RS`: she recovers but stays resting. Tick 6 shows a `command`
  still works from resting (`command → RS`), and tick 7 shows a `user.message` from resting hitting
  `rest_ack → RS` — heard, echoed, but no brain call. Each idle/rest_ack tick discharges `rest` by
  ≈0.015 (idle satiation −0.01 plus drift −0.005).
- **Tick 14** — `rest` finally reaches 0.840, at or below the wake threshold. Now the resting
  `tick[rest ≤ 0.85]` cell selects `wake → I`, and she is back in the active idle state. Note the
  hysteresis: she entered resting at 0.90 (tick 4) but only leaves at 0.85 (tick 14); the 0.05 band is
  what stops her flipping in and out.

## 7. The same machine as a state diagram

```
                    user.message / respond
                  ┌───────────────────────────┐
                  │                           ▼
   self_trigger(reflect)/think          ┌────────────┐
       ┌───────────────────────────────►│ RESPONDING │
       │                                └─────┬──────┘
  ┌────┴────┐  self_trigger(reach)/reach_out       │ tick[else] / idle
  │  IDLE   │◄─────────────────────────────────────┘
  │         │
  │         │  tick[rest ≥ 0.90] / enter_rest        ┌──────────┐
  │         │───────────────────────────────────────►│ RESTING  │
  │         │                                        │          │  user.message / rest_ack (stays)
  │         │◄───────────────────────────────────────│          │  command       / command   (stays)
  └─────────┘  tick[rest ≤ 0.85] / wake              └──────────┘  tick[else]     / idle      (stays)
       ▲                                                              tick[rest>0.85] stays RESTING
       └──────── THINKING ── tick[else] / idle ───────┘
```

Each edge is exactly one non-trivial cell of the matrix in §5. `advance(state, event, ctx)` is the
table lookup that realises it — it reads only `state`, `event`, and `ctx`, never the raw needs vector
or the triggers.

## Status

`fsm.py` (the states, events, transition table, `advance`, the queue and producers) and the action
registry in `engine.py` already implement everything above and are unit-tested in isolation. The tick
loop in `run()` still uses the equivalent `if/elif`; wiring it to drive `advance()` / `registry.fire()`
is KILN-064, which is behaviour-preserving (the whole existing suite is the pin). `cooling` becomes a
live state in KILN-065, and per-transition tracing lands in KILN-066.
