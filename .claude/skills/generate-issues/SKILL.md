---
name: generate-issues
description: Decompose a roadmap phase into a per-phase GitHub-issues file (Lumi format) at spec/roadmap/implementation/, ready for /upload-issues.
---

# Skill: Generate Version Issues

Decompose one ROADMAP **phase** (`vA.B`) into a fine-grained, dependency-ordered
**issues file** in the Lumi format, written to `spec/roadmap/implementation/`. The
output is the input to `/upload-issues` (which pushes it to GitHub) and then
`/execute-issues` (which implements it).

## Usage

```
/generate-issues <phase>
```

- `/generate-issues 0.2` — decompose ROADMAP phase **v0.2** → `spec/roadmap/implementation/v0.2-issues.md`
- `/generate-issues v1.1` — phase **v1.1** → `…/v1.1-issues.md`

One file per **phase** (`vA.B`), matching Lumi's convention. IDs (`KILN-xxx`) are
**globally sequential** and continue across phase files.

## Instructions

### Step 0: Read inputs

1. Normalize the phase to `vA.B` (e.g. `0.2` → `v0.2`).
2. Read [spec/ROADMAP.md](../../../spec/ROADMAP.md) §`vA.B` — the phase's **Goal**,
   **Tasks**, and **DoD** (and the version heading it sits under).
3. Read [spec/ARCHITECTURE.md](../../../spec/ARCHITECTURE.md) for the contracts and
   components the phase touches, and [spec/MISSION.md](../../../spec/MISSION.md) for
   the principles (cheap-first, core-independent-of-interface, per-`agent_id` scope).
4. Read `CLAUDE.md` for code conventions and the current module map.
5. **Find the next free `KILN-xxx` id:** scan existing
   `spec/roadmap/implementation/v*-issues.md`; continue from the highest id used. If
   none exist yet, start at `KILN-001`.
6. If `…/v{A.B}-issues.md` already exists, ask whether to overwrite or append.

### Step 1: Decompose the phase

Turn the phase's **Tasks** into a small set of issues (typically **3–7**), each a
coherent, independently shippable slice:

- Size each **S** (1–2 d) / **M** (3–5 d) / **L** (5–8 d).
- Order by dependency; the first issue is usually the **gate** (the seam/structure
  everything else builds on).
- Map each issue to part of the phase Tasks; together they must satisfy the phase
  **DoD**.
- **Bake tests into every issue** (kiln runs against a mock brain — no paid calls):
  unit for pure logic, contract for any seam, an integration turn where relevant.
- A seam change (the `respond()` shape, the usage record, `needs.json`, the
  brain seam, the event protocol, per-`agent_id` isolation) carries an
  ARCHITECTURE update + its contract test in the **same** issue.
- Stay **within the phase** — don't pull later phases' scope in early.

### Step 2: Write the issues file

Write `spec/roadmap/implementation/v{A.B}-issues.md` using **exactly** this format:

````markdown
# v{A.B} — GitHub Issues

Issues for phase **v{A.B} — {phase title}** (version **v{A} — {version title}**),
derived from the per-phase Tasks in [ROADMAP.md](../../ROADMAP.md) (§v{A.B}) and the
contracts in [ARCHITECTURE.md](../../ARCHITECTURE.md) ({the relevant § sections}).
This file is scoped to a single phase; IDs continue from the previous phase
(KILN-{prev} → **KILN-{first}…{last}**).

{1–3 sentences: what the phase does, the seams it extends, why now.}

## Issues Summary Table

| # | ID | Title | Size | Phase | Dependencies |
|---|----|-------|------|-------|--------------|
| 1 | KILN-{first} | {title} | M | v{A.B} | -- |
| 2 | KILN-{…} | {title} | S | v{A.B} | KILN-{first} |
| … | … | … | … | … | … |

**Size legend:** S = 1–2 days, M = 3–5 days, L = 5–8 days

---

## Dependency Tree

```
KILN-{first} ({gate})
  |
  +-- KILN-{…} (…) --+
  |                  |
  +-- KILN-{…} (…) --+
                     |
            KILN-{…} (…)  => {phase DoD}
```

**Parallelization hints:** {which gate first; what runs in parallel after}.

---

## v{A.B} — {phase title}

### KILN-{id} — {Title}

**Description:**
{1–3 sentences. Note which module(s) it touches: engine/config/.../server/tui.}

**What needs to be done:**
- {bullet}
- {bullet}

**Dependencies:** {KILN-ids, or None}

**Expected result:**
{one sentence}

**Acceptance criteria:**
- [ ] {functional criterion}
- [ ] **Contract test:** {seam pinned} — *(only if a seam changes)*
- [ ] **Unit test:** {pure logic} against a **mock brain** (no paid call)
- [ ] {ties to the phase DoD}

---

{repeat the `### KILN-{id} …` block per issue}

## v{A.B} scope notes

**Total effort:** {rough estimate}.
**Critical path:** KILN-{…} → … → KILN-{…}.
**Phase DoD (ROADMAP §v{A.B}):** {restate the DoD}.
**Contracts pinned this phase:** {the seams + their tests}.
**Model note:** the model runs behind the brain seam; **no paid APIs** — a mock
brain returns canned replies for unit + integration tests.
**Companion documents:**
- [ROADMAP.md](../../ROADMAP.md) — version goals, per-phase Tasks and DoDs (§v{A.B}).
- [ARCHITECTURE.md](../../ARCHITECTURE.md) — {the relevant § sections}.
- Generated on upload: `v{A.B}-github-report.md` (KILN-xxx → GitHub #), then `v{A.B}-execution-report.md`.
````

### Step 3: Report

Show the user: the file path, the issue count, the `KILN-xxx` id range, and the
critical path. Suggest the next step:

```
/upload-issues @spec/roadmap/implementation/v{A.B}-issues.md
```

(Do **not** create GitHub issues here — that's `/upload-issues`. This skill only
writes the local issues file.)

## Important Rules

- **One file per phase** (`vA.B`) at `spec/roadmap/implementation/v{A.B}-issues.md`.
- **IDs are globally sequential** (`KILN-xxx`), continuing across phase files — never reset per phase.
- **Tests in every issue.** Acceptance criteria include the unit/contract/integration tests; the model is mocked, never a paid call.
- **Seam = ARCHITECTURE + test together.** Any contract change lands its `spec/ARCHITECTURE.md` update and contract test in the same issue.
- **Scope to the phase.** Map issues to the phase's Tasks/DoD; don't pull later phases in early (cheap-first, simplicity-first).
- **Honor the DoD.** The issues together must satisfy the phase DoD in ROADMAP §v{A.B}.
- **Ask on ambiguity.** If the phase's Tasks are unclear or under-specified, ask the user before inventing scope.
- **Don't touch GitHub.** This skill writes only the local file; `/upload-issues` pushes it.
