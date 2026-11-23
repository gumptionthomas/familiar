# Project instructions — Familiar

*Paste the body of this file into **Project settings → Memory → Project
instructions**. It covers only what's true of this particular desk; the repo's
`CLAUDE.md` carries the codebase facts and isn't repeated here.*

---

## What runs where

Cloud threads have no device: no BLE adapter, no serial port, no
`familiar.service`, no stick. They can run exactly what CI runs, and should:

- `cd linux-bridge && uv run pytest -q`
- `uvx --from platformio pio test -e native`

**Cloud-eligible:** bridge code and its tests, Tidbyt rendering, pure firmware
logic and its host tests, docs, review, and reading logs I paste in.

**Local-only, needs a thread on my machine:** flashing, the serial console,
anything over BLE, `familiar.service`, the pairing bond, and anything judged by
looking at the screen. Ask for a local thread rather than approximating it.

A cloud thread that changes firmware says plainly that the change is unflashed
and unverified on hardware. Green native tests are not "it works" — the host
harness reaches none of the rendering, I²C, BLE or timing.

## The device and the machine

- Original M5StickC Plus v1.1. Advertises as `Claude-4CFA`, MAC
  `F0:16:1D:03:4C:FA`.
- Serial is `/dev/ttyUSB0` (FTDI). It needs a real data USB-C cable — the
  magnetic mount cable is power-only, so there's no serial through it.
- `pio` is at `~/.local/bin/pio`. Flash with
  `pio run -t upload --upload-port /dev/ttyUSB0` — this does not cost the
  pairing bond, but `pio run -t erase` would.
- The firmware answers `status` over serial without BLE: open `/dev/ttyUSB0` at
  115200, wait ~2.5 s for the DTR reset, then write `{"cmd":"status"}`. Most
  firmware verification is automatable this way; only the screen needs eyes.

## Running the bridge

- systemd **user** service `familiar.service`. Logs:
  `journalctl --user -u familiar -f`. Restart:
  `systemctl --user restart familiar`.
- Config at `~/.config/familiar/config.toml`; haiku archive at
  `~/.local/share/familiar/haikus.jsonl`.
- After editing bridge source, redeploy with
  `uv tool install . --force --no-cache` from `linux-bridge/` — then restart the
  service.
- Only one BLE connection exists at a time. Never run `familiar run` by hand
  while the service is up.
- `pkill -f familiar` self-matches (the paths contain "familiar") and kills its
  own shell. Kill by PID.

## When the buddy misbehaves

- **Run `familiar doctor` first**, before any hypothesis. It's read-only and
  names the cause. Exit 1 means an error-level fault; exit 0 only means it found
  none — `??` warnings exit 0 as well, and a disconnected stick returns early
  without a summary. Read the output, not the status code.
- A loop of `failed to discover services` is a one-sided bond, and the daemon
  can never self-heal it. `familiar repair` does the whole dance, but it needs a
  real terminal — the passkey only exists on the stick's screen — so it cannot
  be run from a tool call. Hand me the command and I'll run it.
- Don't chase BLE faults by guessing. `familiar doctor` and `journalctl` are the
  evidence; power-cycling and `bluetoothctl disconnect` are not.

## The observation trap

Every tool call you make feeds the buddy and resets its idle timer, so you
cannot verify idle, sleep or clock-mode behaviour yourself. Hand me the
observation and wait for it.

And don't explain idle weirdness away as "an active session is keeping it busy".
That explanation is plausible, it was wrong once, and it masked a real bug for
weeks. Get evidence first.

## How I want work to arrive

- **Show me the plan before opening a pull request.** Don't open one unprompted,
  even for a concrete fix, and don't start auto-fixing CI on a PR I haven't
  seen yet.
- PRs go to the fork: `gh pr create --repo gumptionthomas/familiar --base main`.
- Designs and plans go under `docs/superpowers/`, matching what's already there.
- If a task needs the hardware, say so and stop. A guess about the device costs
  more than the wait.
