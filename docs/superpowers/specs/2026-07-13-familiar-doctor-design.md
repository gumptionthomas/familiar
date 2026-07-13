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

### The structural rule: an unknown fact is never read as "fine"

Five Critical defects across four review rounds were all the **same bug wearing a different
hat**: a `None` fact — "we could not determine this" — silently treated as "clean" or "fine" at
some call site the author forgot to guard. The worst instance: `log.get("not_found") or 0`
turned "we could not read the journal" into "the journal was clean", so a `journalctl` timeout
on a cold journal (a 5-second subprocess timeout on a large journal is a normal, not exotic,
event) during the real 2026-07-13-class failure printed **"Everything looks healthy"**.

Re-implementing the "unknown must not be fine" rule by hand at every branch does not work — it
was tried three times and a site kept getting missed. The next attempt replaced per-branch
vigilance with a hand-maintained allowlist (`_UNKNOWN_LABELS` + a `checks` list, swept once
before any trigger logic) — and that failed too, for the same underlying reason: the allowlist
was still something a human had to remember to keep in sync with what `diagnose()` actually
reads. It forgot `service.manual_procs`, a fact `collect()` explicitly sets to `None` on a
`pgrep` failure/timeout — so `(svc.get("manual_procs") or 0) > 0` turned "we could not count the
manual processes" into "zero manual processes", and `doctor` printed **"Everything looks
healthy"** while two daemons fought over the one BLE connection. The vigilance had simply moved
from the trigger sites to the label map — instances closed, class still open.

**The rule is now derived from usage, not from memory.** `diagnose()` reads every fact through a
small accessor, `_Facts`:

```python
class _Facts:
    def __init__(self, facts):
        self._f = facts or {}
        self.unknown = []                    # labels of required facts we could not determine

    def need(self, section, key, label):
        """Read a REQUIRED fact. An unknown one auto-registers and suppresses health."""
        val = (self._f.get(section) or {}).get(key)
        if val is None and label not in self.unknown:
            self.unknown.append(label)
        return val

    def opt(self, section, key):
        """Read an OPTIONAL fact (corroboration only). Unknown is fine here."""
        return (self._f.get(section) or {}).get(key)

    def top(self, key, default=None):        # top-level, optional
        return self._f.get(key, default)
```

**Reading a fact through `.need()` IS declaring it required.** There is no separate list to
keep in sync, because the list no longer exists as a distinct artifact — it is exactly the set
of `.need()` calls `diagnose()` happens to make. Forgetting a fact is now impossible in the way
that mattered: forgetting would mean not reading the fact at all, and a fact `diagnose()` never
reads cannot influence any diagnosis in the first place, so there is nothing to forget to guard.
Conversely, a fact it *does* read to decide something cannot be read without also being
registered — the two actions are the same line of code.

- Every fact `diagnose()` uses to decide something is read via `.need(section, key, label)`. No
  bare `facts["x"]["y"]`, no `.get(...) or 0` anywhere in `diagnose()`.
- `.opt()` is used only where a fact is genuinely optional — corroboration
  (`kernel_smp_errors`, via `.top()` since it's a top-level scalar) or a remedy-text refinement
  that never gates whether a diagnosis fires (`service.installed` and `service.manual_pids`
  only choose *which* valid remedy line to print, after the firing decision was already made
  from `service.active` / `service.manual_procs`).
- **Mode-relevance is structural, not swept:** in `tidbyt` mode the BLE/device/adapter facts are
  simply never read — the whole BLE branch, and every `.need()` call inside it, is skipped — so
  they can never register as unknown. Sweeping an irrelevant fact would be a brand-new false
  positive on a healthy Tidbyt-only setup, not caution.
- **Dedupe by cause:** if `bluetoothctl` itself is missing, none of the downstream BLE facts are
  read at all (there is nothing to call `.need()` on), so one missing binary produces exactly
  one warning, not four.
- Downstream trigger logic (`failing`, `bond_fires`, `phantom_fires`, …) is free to treat an
  unknown windowed count as "does not meet this threshold" for the purpose of deciding whether a
  *specific* diagnosis fires (via the `_meets(x, n)` helper, which returns `False` for `None`
  rather than crashing or coercing to `0`) — this is safe only *because* `.need()` has already
  independently guaranteed the same `None` is in `F.unknown`. The two mechanisms are
  deliberately decoupled: one decides whether a specific cause fires, the other decides whether
  the tool is allowed to claim things are fine.
- At the end of `_diagnose()`, every label in `F.unknown` becomes a `warn(blocks_health=True)`
  finding — automatically, in one loop, with no per-fact code to write or forget.
- `diagnose()` itself is a thin wrapper around a pure `_diagnose()`: if `_diagnose()` returns an
  empty list — every trigger stayed silent AND the tool was not allowed to claim health either —
  the wrapper appends an explicit "no specific fault found, but this is not healthy" finding.
  **`diagnose()` must never return an empty list.** A blank report used to be produced by a dead
  stick with a sub-threshold failure count (`not_found=1`, below `LINK_FAILING_MIN`): zero
  findings, a blank line, exit 0. A blank report is the worst possible output of a diagnostic.

`kernel_smp_errors` is deliberately read via `.top()` rather than `.need()`: it is corroborating
evidence, not a required fact — the log-volume trigger (`BOND_FAILURES_ALONE`) already covers an
unreadable kernel log without needing its own warning, and treating its absence as "no
corroboration" (rather than "unhealthy") does not create false confidence, since it is never
used on its own to justify a health claim.

One more defect surfaced by the same review round: `_meets`'s `None` branch was unpinned by the
test suite. Inverting it to `return x is None or x >= n` passed all 237 tests that existed at
the time — a healthy machine with a `journalctl` timeout would then get `[error] Phantom link`
and `[error] The M5 is not paired`, telling the user to hand-pair with a passkey on the strength
of a log file that could not be read. `blocks_health` correctly suppresses the *health claim* in
that scenario, but nothing was pinning the absence of a false *error* — every existing
unknown-fact test asserted only "no `ok` finding", which the sweep/accessor guarantees on its
own regardless of `_meets`'s correctness. `test_an_unknown_journal_yields_no_error_findings`
closes that gap by asserting the error list is empty, not merely that the summary is withheld.

### The window-vs-instant model (read this before the trigger list)

Two of the facts `collect()` gathers look similar but are NOT the same kind of evidence:

- `device.connected` — from `bluetoothctl info <MAC>`, this is an **instantaneous sample**:
  what BlueZ says the link state is right now, this half-second.
- `log.discover_failures`, `log.not_found`, `kernel_smp_errors` — counts over the **last 10
  minutes**: a window.

Round 2 of review found a real regression caused by conflating the two: every trigger was
gated on `connected is not True`. But during the actual 2026-07-13 failure the daemon loops
**connect → GATT discovery fails → disconnect → back off**, so BlueZ genuinely reports
`Connected: yes` for the seconds the link is up. Sample it in that window and every
`connected`-gated trigger reads "connected" and skips — `doctor` printed "Everything looks
healthy" with 400 discovery failures and 3 kernel SMP errors for our own MAC sitting unread in
the facts. That is the exact failure this command exists to catch, reported as healthy.

**The rule: an instantaneous sample must never veto windowed evidence.** Concretely, `diagnose`
derives whether the link is failing from the window FIRST, before looking at `connected` at
all:

```python
failing = (discover_failures >= LINK_FAILING_MIN     # 3
           or not_found >= LINK_FAILING_MIN)
```

`connected` is then consulted only to tell WHICH failure is occurring — phantom link vs.
one-sided bond vs. stick unreachable — never to decide whether one is occurring.

Evaluated in a fixed order, and EVERY matching cause is reported (not just the first) --
findings that would otherwise be masked (e.g. the adapter being unpairable *and* a one-sided
bond) both need to reach the user, since fixing only one leaves the other blocking. The order
still matters for read sequence: prerequisite fixes (like adapter pairability) are listed
before the fixes that depend on them.

### 1. One-sided bond — `error` (the 2026-07-13 failure)

**Triggered by**, graded by confidence so a single transient blip can never trigger it (a
healthy long-lived link has no `[familiar] connected` line in the last 10 minutes either —
that is the normal steady state, not evidence of failure). **None of these three consult
`connected`** — that is the round-2 fix:

- **Definitive** — `device.paired is False`. BlueZ has no keys; nothing else is needed.
- **Strongly corroborated** — `failing`, `discover_failures >= BOND_MIN_FAILURES` (3), **and**
  `kernel_smp_errors > 0` for *our* MAC. The kernel SMP error is the fingerprint.
- **Damning on log volume alone** — `failing` and `discover_failures >= BOND_FAILURES_ALONE`
  (10) (covers an unreadable kernel log, where `kernel_smp_errors` is `None`).

Below all three, `failing` still routes to one of two other findings rather than being
dropped on the floor (see §3 and §4 below) — a `failing` window with no matching bond evidence
is never simply ignored.

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

**Triggered by:** `failing` **and** `device.connected is True` **and** `not_found > 0`.

This is the ONE place the instantaneous `connected` sample is consulted, and only to
distinguish this failure from the other two below — the window (`failing`, `not_found`) still
gates whether a finding fires at all.

**Why:** BlueZ holds a stale link the daemon cannot use. (The daemon self-heals this after 3
failures, rate-limited to once per 5 minutes — so this finding mostly explains what you are
already seeing in the log.)

**Remedy:** `bluetoothctl disconnect <MAC>`

### 3b. Stick not reachable — `warn`

**Triggered by:** `failing` **and** `not_found >= LINK_FAILING_MIN` **and**
`device.connected is not True` **and** the one-sided-bond finding (§1) did NOT fire.

**Why:** the stick is off, flat, or out of range. BlueZ keeps a paired device's record
forever, so this is distinct from losing the bond — `not_found` climbs with nothing else
corroborating it.

Before round 2, `not_found` was collected but read only by the phantom trigger above (which
requires `connected is True`). A dead stick — `connected` sampled `False`, `not_found` high —
matched no trigger at all, and `doctor` printed "Everything looks healthy ... not connected".
This finding closes that gap.

**Remedy:** press a button on the stick; check it is charged; confirm bluetooth is on in its
settings menu.

### 3c. Link flapping — `warn`

**Triggered by:** `failing`, and none of §1 / §3 / §3b fired.

**Why:** the daemon retries with backoff, and this often clears on its own. This finding never
prints the re-pair recipe: sending someone to stop the service and hand-pair with a passkey on
evidence too thin for §1 is worse than saying nothing.

**Remedy:** none — re-run `familiar doctor` if it persists.

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

### 7. Could not check the link — `warn`

**Triggered by:** `have_bluetoothctl` is true, but `device.connected is None` (bluetoothctl
didn't answer for this specific device) and no windowed finding (§1, §3, §3b, §3c) fired.
Superseded as the primary "stick is unreachable" diagnosis by §3b, which uses the windowed
`not_found` count instead of the instantaneous `known` sample — the same reasoning as the
window-vs-instant rule above: a stale device record from `bluetoothctl info` risked missing
the same class of failure.

### 8. Healthy — `ok`

Connected, service active, nothing above triggered. Print the summary: mode, haiku on/off,
Tidbyt on/off, archive size, link state.

**The health summary is suppressed** (not printed, even with zero `error` findings) whenever:

- any finding has `level == "error"`, OR
- any finding has `blocks_health == True`. Every `warn` finding sets `blocks_health` EXCEPT
  the benign "`Pairable: no`, existing bonds still work" one (§2) — that one describes a KNOWN,
  harmless state, not a gap in what could be checked or evidence the link is unhealthy. Round 2
  found that the flapping (§3c) and stick-not-reachable (§3b) warns were NOT setting
  `blocks_health`, so `doctor` could print `?? The link is flapping` immediately followed by
  `OK Everything looks healthy ... not connected` — an absurd, self-contradicting report.
- `cfg.mode == "ble"` and `device.connected is False`, as a final belt-and-suspenders check
  independent of the finding list: a BLE buddy sampled disconnected right now is never
  "healthy," even in the (currently unreachable in practice) case where no other finding fired.

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

**Round 2 additions — pin the window-vs-instant rule so it cannot regress:**

13. **The regression, encoded directly:** `device.connected is True` (sampled mid-loop) with
    400 `discover_failures` and `kernel_smp_errors > 0` → must STILL produce the one-sided-bond
    finding at `error`. An instantaneous sample must never veto windowed evidence.
14. `discover_failures` in the `BOND_MIN_FAILURES..BOND_FAILURES_ALONE` corridor (e.g. 4) with
    the kernel fingerprint → catches the bond early, before log volume alone would.
15. `discover_failures >= BOND_FAILURES_ALONE` with `kernel_smp_errors = None` (unreadable
    journal) → still fires, on volume alone.
16. A flapping link (`failing`, no bond/phantom/unreachable evidence) → a `warn` finding, and
    NO `ok` finding alongside it.
17. An unreachable stick (`not_found` high, `connected` sampled `False`, no bond evidence) →
    a `warn` or `error` finding, never silently produces zero findings.
18. `have_bluetoothctl = False` → the resulting warn suppresses the `ok` summary.
19. **Mutation pins**, added after the reviewer showed each mutation below passed the full
    round-1 suite unnoticed: `BOND_MIN_FAILURES == 3`, `BOND_FAILURES_ALONE == 10`, and a
    `warn` finding (not the benign pairable one) with `blocks_health = True` actually
    suppresses the `ok` summary — tested with `device.connected = True` so the separate
    connected-is-False guard can't be what saves the assertion.

**Round 3/4 additions — the structural "unknown is never fine" rule, pinned so it cannot regress
one branch at a time:**

20. An unreadable journal (`discover_failures`, `not_found`, and `kernel_smp_errors` all `None`)
    with the device sampled `connected: True` mid-loop → the sweep fires, `blocks_health` is set,
    and the `ok` summary is never printed. This is `test_an_unreadable_journal_never_reads_as_healthy`
    — the direct regression test for the "5-second `journalctl` timeout during the real failure"
    scenario.
21. A dead stick (`connected: False`, `not_found = 1`, below `LINK_FAILING_MIN`) → `diagnose()`
    still returns a non-empty finding list, and it is not an `ok` finding. This is
    `test_diagnose_never_returns_an_empty_report` — the direct regression test for a blank report
    on a genuinely dead buddy.
22. Each of `service.active`, `adapter.pairable`, `device.connected`, `device.paired` set to
    `None` in isolation (all other facts healthy) → the `ok` summary is suppressed every time.
    This is the sweep test: it proves the rule holds at every site the sweep covers, not just the
    two sites that happened to get hand-written checks before.
23. A Tidbyt-only setup with every BLE/device/adapter fact `None` → no errors, and the `ok`
    summary is STILL printed, because those facts are irrelevant in `tidbyt` mode and must not be
    swept. Proves the sweep is mode-aware, not a blanket "any None anywhere" check.
24. **Mutation pins for round 3/4:** `LINK_FAILING_MIN == 3` (a single blip, `discover_failures =
    1`, must not trigger the "link is flapping" warning); the phantom trigger's `connected is
    True` check (a dead stick with a high `not_found` count must never be diagnosed as a
    "phantom link" — the wrong remedy, `bluetoothctl disconnect`, would be printed); and the
    "stick not reachable" finding's `blocks_health` flag, asserted directly on the `Finding`
    object rather than inferred from the summary — the separate `connected is False`
    belt-and-suspenders guard would otherwise mask a flipped flag in that exact scenario.

## Out of scope

- Any `--fix` / auto-repair. Rejected above, on the grounds that the case that matters cannot
  be fixed without a human.
- `start` / `stop` / `restart` / `logs` / `redeploy` wrappers.
- Anything that changes BlueZ or systemd state. `doctor` is strictly read-only.
- Firmware diagnostics over serial (a separate concern, needs a data cable).
