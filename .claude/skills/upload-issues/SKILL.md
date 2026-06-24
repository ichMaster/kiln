---
name: upload-issues
description: Upload issues from a version issues file to GitHub one by one with proper labels and dependencies.
---

# Skill: Upload Version Issues to GitHub

Upload issues from a version issues file to GitHub one by one, with proper labels (prefixed by version) and dependencies.

## Usage

```
/upload-issues <version-issues-file>
```

Example: `/upload-issues @spec/roadmap/implementation/v1-issues.md`

A version issues file is the fine-grained breakdown of a ROADMAP version (v0–v3): each phase (`vA.B`) in [spec/ROADMAP.md](../../../spec/ROADMAP.md) is split into one or more `KILN-xxx` issues. If the file does not exist yet, derive it from the version's phases (each phase's Goal / Tasks / DoD) first, then run this skill.

## Instructions

### Step 1: Read the version issues file

Read the provided file (e.g., `spec/roadmap/implementation/v{N}-issues.md`).

Determine from the file:
- **Version number** (n): from the filename or heading (e.g., `v1-issues.md` -> n = `1`)
- **Label prefix**: `v{n}::` (e.g., `v1::`)

Parse the **Issues Summary Table** to extract for each issue:
- `ID` (e.g., KILN-001)
- `Title`
- `Size` (S, M, L)
- `Area` (the component: `engine`, `config`, `history`, `usage`, `memory`, `commands`, `server`, `tui`, `web`, `tools`, `rag`, `state`, `tests`)
- `Phase` (the ROADMAP phase it implements, e.g. `v1.2`)
- `Dependencies` (list of KILN-xxx IDs)

Then parse each **detailed issue section** (heading with KILN-xxx) to extract: `Description`, `What needs to be done`, `Dependencies`, `Expected result`, `Acceptance criteria` (should align with the phase DoD in ROADMAP.md).

### Step 2: Confirm with user

Show the user a summary of what will be created: number of issues, label prefix (e.g., `v1::`), the full list of labels, and ask for confirmation before proceeding.

### Step 3: Create labels (if they don't exist)

All labels MUST be prefixed with `v{n}::`. Label format: `v{n}::{category}:{value}`.

Version titles: **v0 — Prototype & TUI**; **v1 — Engine (tick-server & hub)**; **v2 — Personality (autonomous inner life)**; **v3 — Web & multi-agent hub**.

```bash
# Version label
gh label create "v1::version:1" --color "0E8A16" --description "Version v1 — Engine (tick-server & hub)" 2>/dev/null || true

# Size labels
gh label create "v1::size:S" --color "28A745" --description "Small (1-2 days)" 2>/dev/null || true
gh label create "v1::size:M" --color "FFC107" --description "Medium (3-5 days)" 2>/dev/null || true
gh label create "v1::size:L" --color "DC3545" --description "Large (5-8 days)" 2>/dev/null || true

# Area labels (one per component touched in this version)
gh label create "v1::area:engine" --color "6F42C1" 2>/dev/null || true
gh label create "v1::area:server" --color "1D76DB" 2>/dev/null || true
gh label create "v1::area:tui"    --color "0E8A16" 2>/dev/null || true
# ... memory / usage / tools / rag / web / state / tests as needed
```

### Step 4: Create issues ONE BY ONE

**IMPORTANT:** Issues must be created one at a time, sequentially. After creating each issue, show the user the result (issue number, URL) and proceed to the next immediately (do not wait for confirmation between issues).

For each issue (in order from the summary table):

1. Build the issue body in markdown:

```markdown
## Description
{description}

## What needs to be done
{full content}

## Dependencies
{dependency list, with references to already-created issue numbers}

## Expected result
{expected result}

## Acceptance criteria
{checklist}

---
**ID:** {KILN-xxx}
**Size:** {S/M/L}
**Version:** v{n}
**Area:** {engine/server/tui/web/tools/rag/memory/state/tests}
**Phase:** {vA.B from ROADMAP}
```

2. Create the issue with a single `gh issue create` command (one issue per command, never batch):

```bash
gh issue create \
  --title "KILN-xxx: {title}" \
  --label "v1::version:1,v1::size:{S/M/L},v1::area:{area}" \
  --body "$(cat <<'BODY'
{issue body}
BODY
)"
```

3. Record the mapping: KILN-xxx -> GitHub issue #number
4. Report to user: `Created KILN-xxx -> #{number}: {title}`
5. If the issue depends on already-created issues, add a comment:
   ```bash
   gh issue comment {issue-number} --body "Blocked by #{dep-issue-number} (KILN-xxx)"
   ```
6. Move to the next issue.

### Step 5: Generate report

After all issues are created, generate `spec/roadmap/implementation/v{N}-github-report.md`:

```markdown
# Version v{n} -- GitHub Issues Report

**Uploaded:** {date}
**Repository:** {github repo URL}
**Total issues:** {count}

## Issue Mapping

| KILN ID | GitHub # | Title | Phase | Labels | URL |
|---------|----------|-------|-------|--------|-----|
| KILN-001 | #5 | ... | v1.1 | v1::version:1, v1::size:S, v1::area:engine | {url} |

## Labels Created

- v{n}::version:{n}
- v{n}::size:S, v{n}::size:M, v{n}::size:L
- v{n}::area:{list}
```

### Step 6: Report to user

Show the user: total issues created, link to the GitHub issues page, path to the generated report file.

## Error Handling

- If `gh` is not authenticated, tell the user to run `gh auth login`
- If the repo has no GitHub remote yet, tell the user to create one (`gh repo create`) before uploading
- If an issue already exists with the same title, skip it and note in the report
- If label creation fails, continue (labels may already exist)
- On any failure, report what was created so far and what remains
