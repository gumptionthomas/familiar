# `familiar doctor` — Design

**Goal:** Turn "my buddy is broken" into a named cause and the exact commands to fix it, in
one command and half a second — instead of hours of hypothesis-hopping across `systemctl`,
`bluetoothctl`, `journalctl -k`, and an HCI trace.

## Motivation

On 2026-07-13 the buddy stopped connecting. Diagnosing it took hours and **three confidently
wrong hypotheses** (a stale bond that had already been cleared, a wedged firmware BLE stack,
a wedged adapter). The actual cause — the M5 had lost its side of the pairing bond while the
laptop kept its own — was named only after an HCI trace (`btmon`, root) showed
`SMP: Pairing Failed, Reason: Pairing not supported`.

Every check on that path is a single shell command a program could run instantly:

| Question | Command | The thing that was missed |
| --- | --- | --- |
| Is the service up? | `systemctl --user is-active familiar` | — |
| Is the link connected? | `bluetoothctl info <MAC>` | — |
| Is the bond one-sided? | `bluetoothctl info` + the stick advertising | the real cause |
| **Is the adapter `Pairable`?** | `bluetoothctl show` | **missed for hours; no re-pair can work while it is `no`** |
| What does the kernel say? | `journalctl -k \| grep -i smp` | `unexpected SMP command 0x0b` — the fingerprint |

The knowledge already exists — as prose in `linux-bridge/README.md:137-144` that a human
must find, read, and translate to their situation. This turns it into a program.

## Scope: exactly one command

**`familiar doctor`. Nothing else.**

Explicitly rejected: `start`, `stop`, `restart`, `logs`, `redeploy`. Those are 1:1 wrappers
over `systemctl --user restart familiar` and `journalctl --user -u familiar -f` — already
short, already standard, already documented. Wrapping them adds a second source of truth and
hides the tool the user needs the moment anything misbehaves. `redeploy` is worse: a CLI
reinstalling itself. It belongs in dev docs.

**Diagnose only. No `--fix`.** The failure that actually cost us the day *cannot* be
auto-fixed: the firmware is `ESP_LE_AUTH_REQ_SC_MITM_BOND`, so pairing requires a human to
read a 6-digit passkey off the stick and type it. That is MITM protection working as
intended. A `--fix` flag would handle the easy cases, appear to succeed, and leave the user
exactly as broken on the one case that matters — the worst possible behaviour for a tool you
reach for when you are already confused.

`doctor` prints a health summary when everything is fine and a diagnosis when it isn't. One
command, both jobs — the user does not have to know which they need before running it.

## Architecture: a pure core with I/O at the edge

`linux-bridge/src/familiar/doctor.py`:

```python
def collect(cfg) -> dict          # shells out; NEVER raises; unknowns are None
def diagnose(facts: dict) -> list[Finding]   # PURE; no I/O
def main(argv=None) -> int        # collect -> diagnose -> render
```

`Finding` is a small dataclass: `level` (`"ok" | "warn" | "error"`), `title`, `why`,
`remedy: list[str]` (copy-pasteable command lines, possibly empty).

**Why the split matters:** `diagnose` is a pure function from facts to findings, so every
scenario is unit-testable with no Bluetooth, no systemd, no hardware — including the exact
2026-07-13 failure. That test *is* the regression test for the hours lost. It follows the
same shape as `feed.h` (pure change-detection) and `archive.stats` (pure trend maths): the
logic worth testing is isolated from the I/O that makes it untestable.

### The facts

`collect()` gathers, each field `None` when it cannot be determined:

```python
{
  "config":  {"parsed": bool, "mode": "ble"|"tidbyt"|"none", "address": str|None,
              "haiku": bool, "tidbyt": bool},
  "service": {"installed": bool|None, "active": bool|None, "manual_procs": int|None,
              "manual_pids": list[int]},   # the genuinely-manual PIDs (service's own excluded)
  "have_bluetoothctl": bool,
  "adapter": {"powered": bool|None, "pairable": bool|None},
  "device":  {"known": bool|None, "paired": bool|None, "bonded": bool|None,
              "trusted": bool|None, "connected": bool|None},
  "kernel_smp_errors": int|None,      # `unexpected SMP command` lines for OUR MAC, recent
                                       # (the kernel logs this for ANY peripheral, so lines
                                       # not naming our address don't count)
  "log": {"discover_failures": int|None,   # "failed to discover services"
          "not_found": int|None,           # "was not found"
          "phantom_clears": int|None,      # "clearing a possible stale link"
          "connected_recently": bool|None},
}
```

The log counts key off the daemon's own strings, which are stable and already used by the
in-daemon diagnostic (`ble.py:220-253`). The window is the last 10 minutes.

**`collect()` never raises.** A missing `bluetoothctl`, no systemd, no journal permissions,
an unparseable config — all degrade to `None`. `diagnose` then reports *"couldn't determine
X"* honestly rather than guessing. Guessing is what cost us the day.

## The diagnoses

Evaluated in a fixed order, and EVERY matching cause is reported (not just the first) --
findings that would otherwise be masked (e.g. the adapter being unpairable *and* a one-sided
bond) both need to reach the user, since fixing only one leaves the other blocking. The order
still matters for read sequence: prerequisite fixes (like adapter pairability) are listed
before the fixes that depend on them.

### 1. One-sided bond — `error` (the 2026-07-13 failure)

**Triggered by**, graded by confidence so a single transient blip can never trigger it (a
healthy long-lived link has no `[familiar] connected` line in the last 10 minutes either —
that is the normal steady state, not evidence of failure):

- **Definitive** — `device.paired is False`. BlueZ has no keys; nothing else is needed.
- **Strongly corroborated** — not connected, `discover_failures >= BOND_MIN_FAILURES` (3),
  **and** `kernel_smp_errors > 0` for *our* MAC. The kernel SMP error is the fingerprint.
- **Damning on log volume alone** — not connected and `discover_failures >= 10` (covers an
  unreadable kernel log, where `kernel_smp_errors` is `None`).

Below all three, but still failing and not connected, is reported as a `warn` ("the link is
flapping") instead — the daemon retries with backoff and this often clears on its own. That
warning never prints the re-pair recipe: sending someone to stop the service and hand-pair
with a passkey on thin evidence is worse than saying nothing.

**Why:** the M5 lost its pairing keys (it then advertises as pairable — its screen shows
"discover") while BlueZ still holds its own. Every connect: link up → the M5 sends
`SMP: Security Request` → BlueZ answers `Pairing Failed: Pairing not supported` → the M5
hangs up. **`bluetoothctl disconnect` cannot help — it clears a stale *link*, not stale
*keys*.**

**Remedy** (the whole recipe, including the two steps people miss):

```
systemctl --user stop familiar
bluetoothctl
  pairable on                       # ← without this, no pairing can EVER succeed
  agent KeyboardOnly                # ← the firmware needs a 6-digit passkey typed
  default-agent
  scan on                           (wait for Claude-XXXX)
  scan off
  pair <MAC>                        # type the code shown ON THE STICK
  trust <MAC>
  quit
systemctl --user start familiar
```

Note it must be **one interactive session** — `bluetoothctl`'s one-shot form tears down
discovery between invocations, so a later `pair` reports "Device not available".

### 2. Adapter not pairable — `error` only when it actually blocks a fix, else `warn`

**Triggered by:** `adapter.pairable is False`.

**Why:** `Pairable: no` is the GNOME *default*, and it only blocks pairing a brand-new device —
an existing bond connects fine. So this is a `warn` ("Bluetooth pairing is off (existing bonds
still work)") unless the one-sided-bond finding (§1) is ALSO firing, in which case a re-pair is
about to be advised and `Pairable: no` genuinely blocks it — GNOME leaves it off, an adapter
power-cycle resets it, and while it is `no` BlueZ answers every pairing attempt with "Pairing
not supported". It is then an `error`, and still reported **before** the bond finding, since
the re-pair recipe cannot succeed until this is fixed first.

**Remedy:** `bluetoothctl pairable on`

### 3. Phantom link — `error`

**Triggered by:** `device.connected is True` **and** the log shows `was not found` with no
recent connect.

**Why:** BlueZ holds a stale link the daemon cannot use. (The daemon self-heals this after 3
failures, rate-limited to once per 5 minutes — so this finding mostly explains what you are
already seeing in the log.)

**Remedy:** `bluetoothctl disconnect <MAC>`

### 4. Two instances — `error`

**Triggered by:** the service is active **and** `manual_procs > 0`.

**Why:** only one BLE connection to the stick is possible at a time. A manual `familiar run`
alongside the service produces baffling, intermittent symptoms.

**Remedy:** kill the manual process (by PID; **never `pkill -f familiar`** — the pattern
matches its own shell and kills the caller). `collect()` already knows which PIDs are the
service's own (excluded) versus genuinely manual (`service.manual_pids`), so the remedy names
the actual PID to kill — never the service's own, and never a placeholder the user has to
disambiguate by hand.

### 5. Service not running — `error`

**Remedy:** `systemctl --user start familiar` (or `familiar init --service` if not installed).

### 6. Nothing configured — `error`

**Triggered by:** `config.mode == "none"`. **Remedy:** `familiar init`.

### 7. Device not advertising — `warn`

**Triggered by:** an address is configured, the device is not connected, and BlueZ does not
know it. **Why:** the stick may be asleep or flat. **Remedy:** press a button; check it is
charged; confirm bluetooth is on in its settings menu.

### 8. Healthy — `ok`

Connected, service active, nothing above triggered. Print the summary: mode, haiku on/off,
Tidbyt on/off, archive size, link state.

## Output

Human-readable, grouped, with the remedy indented under each finding. Exit **0** when there
are no `error` findings, **1** when there are — so it is scriptable and usable in CI or a
hook.

Warnings do not fail the exit code.

## Error handling

- Any collector failing → that fact is `None` → `diagnose` emits a `warn` finding naming what
  it could not check, and continues. It never silently pretends a check passed.
- No `bluetoothctl` → all Bluetooth facts `None`; the BLE diagnoses are skipped with a
  `warn`, and the rest still run.
- No configured address (Tidbyt-only mode) → the BLE checks are skipped entirely, not failed.

## Testing

`diagnose` is pure, so every scenario is a table test over a facts dict — **no hardware, no
subprocess, no BLE**.

1. **The 2026-07-13 failure, encoded:** paired, not connected, many `discover_failures`,
   `kernel_smp_errors > 0` → produces the one-sided-bond finding, at `error`, whose remedy
   contains `KeyboardOnly` and `pairable on`.
2. Device not paired at all → same one-sided-bond finding.
3. `adapter.pairable is False` → its own finding, and it must appear **before** the re-pair
   advice (a re-pair cannot work until it is fixed).
4. Connected + `not_found` in the log → phantom-link finding, remedy is `bluetoothctl disconnect`.
5. Service active + `manual_procs == 1` → two-instances finding; the remedy must **not**
   suggest `pkill -f familiar`.
6. Service inactive → service finding.
7. `mode == "none"` → configure finding, remedy `familiar init`.
8. All healthy → exactly one `ok` finding, and `main` exits 0.
9. Any `error` finding → `main` exits 1.
10. Facts full of `None` (nothing could be determined) → no crash, no false diagnosis; emits
    `warn` findings naming what could not be checked.
11. Tidbyt-only mode (no address) → BLE diagnoses skipped, not reported as failures.
12. `collect()` with `bluetoothctl` absent → returns a dict with `None` Bluetooth facts and
    does not raise.

## Out of scope

- Any `--fix` / auto-repair. Rejected above, on the grounds that the case that matters cannot
  be fixed without a human.
- `start` / `stop` / `restart` / `logs` / `redeploy` wrappers.
- Anything that changes BlueZ or systemd state. `doctor` is strictly read-only.
- Firmware diagnostics over serial (a separate concern, needs a data cable).
