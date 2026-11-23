# Familiar

A desk buddy for Claude Code, on an M5StickC Plus or a Tidbyt. Firmware at the
repo root, a Linux BLE bridge in `linux-bridge/`.

Read `README.md` for what it is and how it's set up, and `REFERENCE.md` for the
BLE protocol — that protocol is the stable surface, so treat it as fixed unless
the change *is* a protocol change.

## This is a fork

- `origin` is `gumptionthomas/familiar` (ours). `upstream` is
  `anthropics/claude-desktop-buddy` — **never push there**. Sync deliberately:
  `git fetch upstream && git merge upstream/main`.
- `main` tracks `origin/main`. `gh` defaults to upstream, so PRs need the repo
  named: `gh pr create --repo gumptionthomas/familiar --base main`.
- `CONTRIBUTING.md` is upstream's and describes what *they* accept. It doesn't
  govern this fork.
- The local working directory is still `claude-desktop-buddy`; only the GitHub
  repo was renamed.
- Fork changes to the firmware stay isolated to `src/main.cpp` where they can,
  so upstream merges stay cheap.

## Layout

`README.md` has the full map. In short: firmware in `src/`, bridge package in
`linux-bridge/src/familiar/`, host-testable firmware logic in headers
(`src/feed.h`), build and render helpers in `tools/`.

## Commands

| | |
|---|---|
| Build firmware | `pio run` |
| Flash | `pio run -t upload` — add `--upload-port` when more than one serial device is present |
| Firmware tests (host) | `uvx --from platformio pio test -e native` |
| Bridge tests | `cd linux-bridge && uv run pytest -q` |
| Redeploy the bridge | `uv tool install . --force --no-cache` from `linux-bridge/`, then `systemctl --user restart familiar` |

`--no-cache` is not optional: the version stays `0.1.0`, so plain `--force`
serves a cached wheel and silently reinstalls the old code. Reinstalling alone
changes nothing — the running service keeps the old process until it restarts.

The README's *Flashing* section tells you to `pio run -t erase` first on a
previously-flashed device. **Don't, on a paired stick.** `pio run -t upload`
writes only the app partition, so the pairing bond, pet name, owner and stats
in NVS survive; `erase` destroys all of it and costs a re-pair that needs a
human-typed passkey.

When the buddy misbehaves, run **`familiar doctor`** before forming any
hypothesis. It is read-only and names the cause. Exit 1 means it found an
error-level fault; exit 0 only means it found none — warnings exit 0 too, so
read the output rather than the status code.

Use `uv`, not `pip` or `pipx`. `git add` new files explicitly — `git commit -a`
skips untracked ones. `uv.lock` is committed.

## Workflow

Design, then plan, then tests, then code. Designs live in
`docs/superpowers/specs/`, plans in `docs/superpowers/plans/`; both are
committed, and the existing ones are worth reading before changing the area
they cover.

Pure firmware logic belongs in host-compilable headers that include only
`<stdint.h>`, `<string.h>` and `<ArduinoJson.h>` — anything touching
`<Arduino.h>` or the M5 libraries breaks the native build and is then
untestable.

## Hardware facts that change the code

- **I²C bus assignment is the reverse of the ESP32 default.** Read the header
  comment in `src/env.h` before touching any peripheral; getting it wrong kills
  the IMU, RTC and PMIC at once. The QMP6988's address depends on an SDO
  strap and varies by HAT batch: this unit answers on `0x70`
  (`SLAVE_ADDRESS_L`), `0x56` is the other strap, and `envInit` tries L then H.
  Keep both.
- **Offload blocking peripheral reads to a core-0 task**, don't throttle them in
  `loop()`. A 250 ms I²C read hitches the animation at any interval.
- **Feed change detection lives in `src/feed.h`.** Its comment says why; the
  short version is that comparing against a smaller buffer made every long line
  look new, and the buddy never slept for weeks.
- **The firmware stamps `git describe` as `FW_VERSION`** (`tools/git_version.py`,
  fallback in `src/version.h`); it shows on the DEVICE info page and in the
  `status` reply. **After a firmware PR is squash-merged, reflash from `main`** —
  the branch hash the stick reports no longer exists.
- **One BLE connection at a time.** Never run `familiar run` by hand while
  `familiar.service` is up.
- Flashing does not cost the pairing bond; `pio run -t upload` writes only the
  app partition and NVS survives.

## Lines not to cross

- **The haiku archive stores outputs only.** The digest contains the user's
  actual prompts and replies and is deliberately never written. Adding it is a
  decision for the user, not a convenience — a test pins the record's key set.
- The imagery statistic is **document frequency, not word count**. A test pins
  this; don't "simplify" it.
- The Tidbyt `INSTALLATION_ID` must stay alphanumeric (the API rejects hyphens),
  and renaming it orphans the old installation — delete that one *after* the
  daemon is redeployed, or the next push resurrects it.

## Already decided against

A thread will reasonably propose all of these. They were considered and
rejected; reopen one only if the user asks.

- **`familiar doctor --fix`, and `start`/`stop`/`logs` wrappers.** Doctor is
  diagnose-only on purpose: the failure it exists for needs a human-typed
  passkey, and a `--fix` would handle the easy cases and abandon the user on the
  one that matters.
- **Porting the firmware's per-species particles to the Tidbyt.** The Tidbyt
  pets deliberately use native particles designed for 64×32; only the poses are
  extracted faithfully.
- **A Plus2 port.** The branch was deleted — untestable without the hardware.
- **The 18650 battery base and a charge-current bump.** The base was DOA and
  isn't being replaced, and raising the AXP192 charge current would over-drive
  the bare stick's ~120 mAh cell.
- **`BatteryGuard`.** Cancelled: a brownout alone does not cost the pairing
  bond, which was its entire reason to exist.
- **Chasing the bond loss.** The cause is an unproven firmware hypothesis
  (a link dropping mid-encryption-setup). What the captured log does establish
  is that the trigger is the range boundary — a burst of connect-and-flap
  cycles — and not power, and not the Bluetooth toggle; a further suspicion,
  unproven, is that it also needs a recently reset stick or a recycled adapter.
  No recurrence has been recorded in the repo since 2026-08-14. Don't "fix" it
  without a fresh one and evidence.
- Known blind spot, unfixed: a link that holds ≥30 s and then drops for good
  logs only `link dropped after Ns`, which is counted by neither doctor's
  failure tally nor its flap tally. Doctor won't call that healthy, but it has
  nothing to say about it either.

## What cannot be verified without the device

The host tests cover pure logic only. Rendering, I²C, BLE, timing and every
idle-state behaviour need the stick, and so does the device build itself.

Note the observation trap: every tool call feeds the buddy and resets its idle
timer, so idle behaviour cannot be self-observed — hand that to the user. When a
firmware change hasn't been flashed, say so plainly rather than inferring from a
green test run.
