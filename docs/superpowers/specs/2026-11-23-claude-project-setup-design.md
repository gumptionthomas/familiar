# Design: moving Familiar into a claude.ai Project

**Date:** 2026-11-23
**Size:** small change (two documents + a setup checklist)

## Intent

Continue Familiar development from a claude.ai **Project** (the new
coordinator-plus-threads kind), used from the Claude Desktop app's Code tab.
Cloud threads do the software work and open PRs; a local thread on this
machine does anything that touches the stick.

The repo does not move. It stays at `gumptionthomas/familiar`; the Project
points at it.

**Why now:** the knowledge that makes this project tractable lives only in
this machine's Claude Code auto-memory — the one-sided-bond repair flow, the
QMP6988 sitting at 0x70 instead of the documented 0x56, reflashing from `main`
after a squash-merge, the deliberate refusal to archive the haiku digest, "run
`familiar doctor` first". None of it is in the repo, and there is no
`CLAUDE.md`. A thread started anywhere else relearns all of it the expensive
way — and this project has already demonstrated how expensive that is (seven
hours on the bond, six Criticals on one bug class in `doctor`).

## Goals

- Every thread — cloud, local, or a terminal session here — starts from the
  same facts about this codebase.
- A thread knows which work it can actually verify, and says so when it can't.
- The hard-won gotchas survive outside one machine's memory directory.

## Non-goals

- Moving or restructuring the repo.
- Changing firmware or bridge behaviour.
- Adding `muntin` to the Project (it has no GitHub remote yet).
- Replacing `README.md` or `REFERENCE.md`. `CLAUDE.md` points at them.
- Automating the pairing passkey. It is human-typed by design.

## Anti-goals

Things that would make this a failure even if the files exist:

- A `CLAUDE.md` that restates the README. Two copies drift, and the stale one
  is the one that gets believed.
- Instructions that leave a cloud thread believing it can verify hardware.
  "The tests pass" is not "the pet works".
- Anything that invites an unattended thread to flash firmware, restart
  `familiar.service`, or touch the bond.

## Constraints

From the Projects docs and from this repo:

- Cloud threads have no `/dev/ttyUSB0`, no BLE adapter, no `familiar.service`,
  no stick. They *can* run exactly what CI runs: `uv run pytest -q` in
  `linux-bridge/`, and `uvx --from platformio pio test -e native` at the root.
- Threads load `CLAUDE.md` from every repo at start. They do **not** load the
  repo's `.mcp.json`, hooks, permission rules, or `env`, and they inherit
  nothing from the Claude Code setup on this machine.
- Local threads start with the Project's instructions but **not** its memory
  files. So anything durable belongs in `CLAUDE.md`, not in Project memory.
- Project instructions cap at 16,000 characters.
- Two local threads in one folder overwrite each other —
  `claude remote-control --spawn worktree`.
- The repo is a public fork. `CONTRIBUTING.md` (upstream's) takes almost
  nothing, so PRs go to the fork:
  `gh pr create --repo gumptionthomas/familiar --base main`.
- `origin` is the fork, `upstream` is `anthropics/claude-desktop-buddy`, never
  pushed to. The local working directory is still named
  `claude-desktop-buddy`.

## Approaches

**A — Repo-first.** Everything in a committed `CLAUDE.md`, thin instructions.
One source of truth, versioned, works in the terminal too. Rejected because
desk-specific facts (which stick, which serial port, the systemd unit) are
noise in a public fork that anyone may clone.

**B — Project-first.** Everything in the Project instructions, memory left to
accumulate. Matches the user's three existing Projects. Rejected: invisible to
the terminal here, not in git, and local threads start without memory loaded,
so the two descriptions drift.

**C — Split by audience. Chosen.** `CLAUDE.md` carries what is true for anyone
touching this code; Project instructions carry routing and desk specifics.

The device MAC is already throughout the public repo as test fixtures, so the
split is about audience, not secrecy.

## The how

### 1. `CLAUDE.md` at the repo root (committed)

Audience: any agent or person working on this code, on any machine.

1. **What this is** — the fork, the two remotes, never push `upstream`, sync
   it deliberately; local directory name differs from the repo name.
2. **Where things live** — points at the README's *Project layout*; firmware in
   `src/`, bridge in `linux-bridge/src/familiar/`, pure logic in host-testable
   headers.
3. **Commands** — `pio run`, `pio run -t upload --upload-port /dev/ttyUSB0`,
   the two test commands, and the bridge redeploy that bites:
   `uv tool install . --force --no-cache` (plain `--force` serves a stale
   cached wheel). `uv`, never `pip`.
4. **Git and PRs** — `gh pr create --repo gumptionthomas/familiar`, `main`
   tracks `origin/main`, `git add` new files (`commit -a` skips untracked).
5. **Workflow** — designs and plans under `docs/superpowers/`, design before
   code, tests before implementation.
6. **Hardware facts that change the code** — ENV HAT on `Wire`/I2C0, G0/G26,
   QMP6988 at **0x70** (the docs' 0x56 is wrong); `Wire1` is the internal bus,
   remapping it kills the IMU, RTC and AXP; blocking peripheral reads go on a
   core-0 task, not throttled in `loop()`; native tests may include only
   `<stdint.h>`, `<string.h>`, `<ArduinoJson.h>`; the firmware stamps its git
   hash, so **reflash from `main` after a squash-merge**; one BLE connection at
   a time; feed-change detection lives in `src/feed.h` and why.
7. **Lines not to cross** — the haiku archive stores outputs only; the digest
   is deliberately never stored, and adding it is a decision for the user, not
   a convenience. Imagery is document frequency, not word count.
8. **Open questions, and what not to "fix"** — `doctor`'s known blind spot (a
   link that holds ≥30s then drops forever is counted by nothing); the
   bond-loss hypothesis is a firmware investigation, unproven, and six days of
   ordinary use since suggest not touching it without a fresh recurrence;
   `BatteryGuard` was cancelled, do not resurrect it from the spec.
9. **What cannot be verified without the stick** — rendering, I²C, BLE,
   timing, and all idle behaviour. Say so rather than inferring it.

### 2. Project instructions (in the Project, ≤16k chars)

Audience: threads working on *this desk's* Familiar.

- **Routing.** Cloud-eligible: bridge code and tests, Tidbyt rendering, pure
  firmware logic plus its native tests, docs, review. Local-only: flashing,
  serial, BLE, `familiar.service`, the bond, anything observed on the screen.
  A cloud thread that changes firmware says plainly that it is unflashed.
- **The device.** M5StickC Plus v1.1, `Claude-4CFA`, `F0:16:1D:03:4C:FA`,
  `/dev/ttyUSB0`, a real data USB-C cable — the magnetic mount cable is
  power-only.
- **Operating it.** `familiar.service` as a user unit,
  `journalctl --user -u familiar -f`, config at
  `~/.config/familiar/config.toml`. **`familiar doctor` first**, before any
  hypothesis. `familiar repair` fixes a one-sided bond but needs a real TTY —
  hand it to the user, it cannot be run from a tool call.
- **The observation trap.** Every tool call feeds the buddy and resets its idle
  timer, so idle-state behaviour cannot be self-verified. Hand that observation
  to the user.
- **PR etiquette** — the user's call on whether threads open PRs unprompted.

### 3. Setup, which is the user's to click

- Grant the **Claude GitHub App** access to `gumptionthomas/familiar`
  ([github.com/settings/installations](https://github.com/settings/installations)).
  A `/web-setup` token is explicitly not enough for Project threads.
- New Project, add the repo, confirm `uv` is available in the cloud
  environment (both test commands need it).
- Expose this folder: `claude remote-control --spawn worktree`, or the desktop
  app's Remote Control switch.

## Left to the builder

Exact wording and ordering; how much of the bond-and-doctor history to carry
versus link; and trimming anything that merely restates the README.
