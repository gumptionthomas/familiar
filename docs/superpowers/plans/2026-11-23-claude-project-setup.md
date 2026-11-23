# Claude Project Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every Claude Code thread — cloud, local, or terminal — the same starting facts about Familiar, by writing a committed `CLAUDE.md` and a Project instructions document.

**Architecture:** Split by audience. `CLAUDE.md` (committed, public) carries what is true for anyone touching this code. `docs/project-instructions.md` (committed, but authored to be pasted into the Project's instructions field) carries routing and desk specifics. No code changes.

**Tech Stack:** Markdown. Verification is `grep`/`test` against the repo plus the existing suites.

**Spec:** `docs/superpowers/specs/2026-11-23-claude-project-setup-design.md`

## Global Constraints

- Project instructions must be **≤ 16,000 characters**.
- `CLAUDE.md` must not restate `README.md`. Where the README already says it (hardware list, flashing walkthrough, pets, project layout), link rather than copy.
- Every command, file path, and symbol named in either document must resolve in this repo as of this commit.
- Facts come from the design doc and are verified against source, not from recollection.
- Firmware changes stay isolated to `src/main.cpp` (fork discipline); the documents say so but change no code.
- PRs target the fork: `gh pr create --repo gumptionthomas/familiar --base main`.

## Review Focus

Failure modes the spec implies that no document "test" naturally exercises:

1. **A stale path.** A document naming `src/env.h` or `feed.h` after a later rename reads as authoritative and is wrong — Task 1 Step 3 resolves every path mechanically.
2. **A cloud thread claiming hardware verification.** The instructions must say it outright, not imply it — Task 2 Step 2 greps for the explicit sentence.
3. **The 16k cap breached silently.** The UI truncates; the thread never knows — Task 2 Step 3 asserts the count.
4. **Duplication with the README that then drifts.** Task 1 Step 4 diffs the two for copied sentences.
5. **A reader who does not know this is a fork** pushing to `upstream`. Both documents state the remote rule in their first section — Task 1 Step 3 checks for it.

---

### Task 1: `CLAUDE.md`

**Files:**
- Create: `CLAUDE.md`
- Reference (do not modify): `README.md`, `REFERENCE.md`, `CONTRIBUTING.md`, the design doc

**Interfaces:**
- Consumes: the nine-section outline in the design doc's *The how → 1*.
- Produces: the repo-level facts Task 2's instructions deliberately do **not** repeat (remotes, commands, hardware-facts-that-change-code, the don't-fix list).

- [ ] **Step 1: Verify each load-bearing fact against source before writing it**

```bash
grep -n '0x70\|0x56' src/env.h                 # QMP6988 address + fallback
grep -n 'G0\|G26\|Wire\b' src/env.h            # I2C0 pins
grep -n 'feedApplyEntries' src/feed.h
grep -n 'FW_VERSION' src/version.h tools/git_version.py
grep -n 'native' platformio.ini
grep -rn 'INSTALLATION_ID' linux-bridge/src/familiar/tidbyt.py
git remote -v
```
Expected: every grep matches. Any that does not is a fact to correct, not to carry over.

- [ ] **Step 2: Write `CLAUDE.md`**

Nine sections, in the design doc's order: what this is; where things live (link the README's *Project layout*); commands; git and PRs; workflow; hardware facts that change the code; lines not to cross; open questions and what not to "fix"; what cannot be verified without the stick. Prose in the repo's existing register — plain, specific, reasons included. No invented conventions: if the repo does not already do it, it does not go in.

- [ ] **Step 3: Resolve every path and command named in it**

```bash
grep -oE '`[a-zA-Z0-9_./-]+\.(h|cpp|py|md|ini|toml|jsonl)`' CLAUDE.md | tr -d '`' | sort -u | while read -r f; do test -e "$f" || echo "MISSING: $f"; done
grep -c 'upstream' CLAUDE.md
```
Expected: no `MISSING:` lines; the `upstream` rule present.

- [ ] **Step 4: Check it against the README for copied prose**

Read `README.md`'s *Hardware*, *Flashing*, *Project layout* and *Credits* sections alongside `CLAUDE.md`. Any sentence that says the same thing becomes a link. Expected: no duplicated claim that could drift.

- [ ] **Step 5: Confirm nothing in the repo changed behaviourally**

Run: `cd linux-bridge && uv run pytest -q` and `uvx --from platformio pio test -e native`
Expected: both pass, as they did before the commit.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/superpowers/specs/2026-11-23-claude-project-setup-design.md docs/superpowers/plans/2026-11-23-claude-project-setup.md
git commit -m "docs: add CLAUDE.md so any thread starts from the same facts"
```

---

### Task 2: Project instructions

**Files:**
- Create: `docs/project-instructions.md`
- Reference: `CLAUDE.md` from Task 1

**Interfaces:**
- Consumes: Task 1's `CLAUDE.md` — the instructions reference it rather than restating it.
- Produces: the text the user pastes into **Project settings → Memory → Project instructions**.

- [ ] **Step 1: Write `docs/project-instructions.md`**

Five sections, per the design doc's *The how → 2*: routing (cloud-eligible vs local-only, with both test commands); the device; operating it (`familiar doctor` first, `familiar repair` needs a real TTY and is handed to the user); the observation trap; PR etiquette — **threads stop at a plan and do not open a pull request until the user has seen it**. Open with one line saying the repo's `CLAUDE.md` carries the codebase facts and is not repeated here.

- [ ] **Step 2: Verify the two non-negotiable sentences are explicit**

```bash
grep -n 'cannot verify\|unflashed' docs/project-instructions.md   # cloud threads do not claim hardware verification
grep -ni 'tty' docs/project-instructions.md                        # familiar repair needs a real terminal
grep -ni 'pull request' docs/project-instructions.md               # plan-first
```
Expected: each matches at least once.

- [ ] **Step 3: Verify the character cap**

Run: `wc -c docs/project-instructions.md`
Expected: under 16000.

- [ ] **Step 4: Commit**

```bash
git add docs/project-instructions.md
git commit -m "docs: add the Project instructions for cloud/local thread routing"
```

---

### Task 3: Hand over the setup steps

**Files:** none

- [ ] **Step 1: Give the user the three steps they must click**

Claude GitHub App access to `gumptionthomas/familiar`; new Project with the repo added, `uv` confirmed in the cloud environment; `claude remote-control --spawn worktree` in this folder. Paste-ready: the contents of `docs/project-instructions.md`.

- [ ] **Step 2: Open the PR**

```bash
git push -u origin <branch>
gh pr create --repo gumptionthomas/familiar --base main
```
