---
name: execute-issues
description: Execute GitHub issues for a version sequentially - implement, validate, commit, push, and generate a report.
---

# Skill: Execute GitHub Issues

Execute GitHub issues for a version sequentially: implement, validate, commit, push, and generate a report.

## Usage

```
/execute-issues <label> [--issue KILN-xxx] [--dry-run]
```

The `<label>` is the GitHub version label exactly as it appears (e.g., `v1::version:1`).

- `/execute-issues v1::version:1` -- execute all issues labeled `v1::version:1`
- `/execute-issues v1::version:1 --issue KILN-003` -- execute a single issue from that version
- `/execute-issues v1::version:1 --dry-run` -- show execution plan without making changes

## Instructions

### Step 0: Verify prerequisites

1. Confirm we are on the expected branch (e.g., `main` or the user's working branch)
2. Confirm working tree is clean (`git status`)
3. Confirm `gh` is authenticated
4. Parse the label to determine version: label `v1::version:1` -> version `n=1`
5. Fetch issues from GitHub:
   ```bash
   gh issue list --label "{label}" --state open --limit 100
   ```
6. Read the version issues file for detailed descriptions: `spec/roadmap/implementation/v{n}-issues.md`
7. If a GitHub report exists (`spec/roadmap/implementation/v{n}-github-report.md`), read the KILN-to-GitHub# mapping
8. Read [spec/ROADMAP.md](../../../spec/ROADMAP.md) for the version goal and the phase (`vA.B`) DoD, [spec/ARCHITECTURE.md](../../../spec/ARCHITECTURE.md) for the contracts the issue must honor, and [docs/how-it-works.md](../../../docs/how-it-works.md) for the runtime mechanics (tick loop, needs, routing).

### Step 1: Build execution queue

From the GitHub issue list, build an ordered queue based on dependencies:
- Parse KILN-xxx IDs from issue titles (format: `KILN-xxx: {title}`)
- Determine dependency order from the version issues file dependency tree
- Issues with no unmet dependencies go first
- Skip issues already closed on GitHub
- If `--issue KILN-xxx` is specified, execute only that issue (but verify its dependencies are closed)

Show the user the execution plan and ask for confirmation.

### Step 2: Execute each issue (loop)

For each issue in the queue:

#### 2a. Assign and announce

Print: `--- Starting KILN-xxx: {title} ---`

#### 2b. Read issue details

Read the full issue description from the version issues file (the detailed section for this KILN-xxx).

#### 2c. Implement

Execute the tasks described in the issue. Follow the conventions in `CLAUDE.md` and the principles in `spec/MISSION.md`. Route by component:

- **Core / engine** (`engine.py`, `config.py`): `State` + needs (drift/satiation), the tick loop (`run`), the two brains (`chat_reply` Haiku/SDK, `deep_reply` Opus/CLI) and cost routing (`classify`, `respond`), self-triggers. Keep the **cost discipline** (cheapest brain that fits; conserve Opus) and keep the core **interface-independent** — no module imports `engine` (it runs as `__main__`).
- **Modules** (`history.py`, `usage.py`, `memory.py`, `commands.py`): transcript helpers, model/token capture + chat rendering, long-term memory/canon/prompts/transcripts, slash commands.
- **Server / agent host** (planned `server/`): the always-on tick-server (WS/HTTP); host agents keyed by **`agent_id`** with a **per-agent permission scope**.
- **TUI / web clients** (planned `tui/`, `web/`): thin clients over the server bus (Textual; echo-free inbox/outbox) — no agent logic.
- **Tools / RAG** (planned `tools/`, `rag/`): the permission-scoped tool registry; semantic recall over `history/*.json`.
- **Contract changes:** any change to a stable seam (the `respond(...) → {class, route, reply, usage}` shape, the model-usage record, `state/needs.json`, the canon → system-prompt path, the event protocol, or per-`agent_id` isolation) updates `spec/ARCHITECTURE.md` **AND** its contract test, in the same commit.
- Follow existing style/patterns; keep each version self-contained (don't pull later-version concerns in early — "cheap-first, core-first").

#### 2d. Validate

Run validation checks (Python):

1. **Tests:** `pytest` for the changed packages (unit + the contract tests pinning the seams), where tests exist.
2. **Dry-run smoke:** `KILN_LIVE=0 python engine.py` runs clean (exercises routing, self-triggers, commands, memory, transcripts deterministically — no paid calls).
3. **Lint:** `ruff check {changed paths}` (if configured).
4. **Syntax/import:** `python3 -m py_compile {changed_py_files}` and an import check for changed modules.
5. **Contract consistency:** the touched seams match `spec/ARCHITECTURE.md` and their contract tests.
6. **Acceptance criteria:** go through each criterion from the issue and verify against the phase DoD in `spec/ROADMAP.md`.

Record pass/fail for each check. **Tests are part of the work.** No paid APIs in validation/CI: mock the model (Anthropic SDK + the `claude -p` CLI), never call live.

#### 2e. Commit

```bash
git add {specific files created/modified}
git commit -m "$(cat <<'EOF'
KILN-xxx: {title}

{1-2 sentence summary of what was implemented}

Closes #{github-issue-number}

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

#### 2f. Push

```bash
git push
```

#### 2g. Close issue with summary

```bash
gh issue close {issue-number} --comment "$(cat <<'EOF'
## Implementation Summary

**Commit:** {commit-hash}
**Files changed:** {count}

### What was done
{bullet list of key changes}

### Validation
{pass/fail status for each check}

### Acceptance criteria
{checklist with pass/fail}
EOF
)"
```

#### 2h. Log progress

Append to the in-memory execution log: issue ID + title, commit hash, files changed, validation results, status (success/partial/failed).

### Step 3: Handle failures

If implementation or validation fails for an issue:

1. Do NOT commit broken code
2. Revert changes: `git checkout -- .`
3. Add a comment to the GitHub issue explaining what failed
4. Log the failure
5. Ask the user: continue to next issue (if no dependency), or stop?

### Step 3b: Version bump on completion

**Do NOT bump the version automatically.** Never change the version (VERSION file, RELEASE.txt, or git tag) without explicit user confirmation. When a phase/version's issues are all done, report completion and let the user decide whether/when to release via `/release-version`.

Version notation `A.B.C`: `A` = roadmap version (v0→0 … v3→3), `B` = phase, `C` = post-release fix. Roadmap phase `vA.B` → semver `A.B.0` (e.g. v1.1 → `1.1.0`). If some issues failed or were skipped, do NOT bump — note in the report that the version is incomplete. (Delegate the release to `/release-version`.)

### Step 4: Generate execution report

After all issues are processed (or on stop), generate `spec/roadmap/implementation/v{n}-execution-report.md`:

```markdown
# Version v{n} -- Execution Report

**Date:** {date}
**Branch:** {branch name}
**Label:** {label}
**Target version:** {version}
**Executed by:** Claude Code

## Summary

| Status | Count |
|--------|-------|
| Completed | {n} |
| Failed | {n} |
| Skipped | {n} |
| Remaining | {n} |

## Issues

| # | KILN ID | Title | Phase | Status | Commit | Files | Tests |
|---|---------|-------|-------|--------|--------|-------|-------|
| 1 | KILN-001 | ... | v1.1 | completed | a1b2c3d | 4 | pass |

## Detailed Results

### KILN-001: ...
**Status:** completed · **Commit:** a1b2c3d
**Validation:** [x] tests · [x] dry-run · [x] ruff · [x] acceptance

## Next Steps
{remaining issues + dependencies}
```

Commit and push the report (`KILN`-style message, with the Co-Authored-By trailer).

## Important Rules

- **One issue at a time.** Never work on multiple issues simultaneously.
- **Dependency order.** Never start an issue whose dependencies are not closed.
- **Clean commits.** Each issue = one commit. No mixing work across issues.
- **No broken code.** Only commit code that passes validation (tests + dry-run + ruff).
- **Tests ship with the feature.** Mock the model (SDK + `claude -p`); never call paid APIs.
- **Core independent of interface.** Never leak client/server concerns into the core; no module imports `engine`.
- **Cost discipline.** Use the cheapest brain that fits; "which branch answered decides what closed." Don't make the loop spend Opus where Haiku suffices.
- **Per-agent scope (hub).** When the agent host lands, every record is keyed by `agent_id` and an agent acts only within its permission scope (Agnika = home/elevated; companions narrow).
- **Contracts stay stable.** A seam change updates `spec/ARCHITECTURE.md` and its contract test in the same commit.
- **Ask on ambiguity.** If an issue description is unclear, ask the user rather than guessing.
- **Progress updates.** Print a short status line after each issue completes.
