# `familiar repair` + `BatteryGuard` — Design

**Goal:** Stop the M5 losing its pairing bond, and when it still does, replace the manual
`bluetoothctl` dance with one command.

## The incident, 2026-08-11

After a laptop reboot the buddy would not connect. `familiar doctor` first reported the stick
unreachable, then — four minutes later, on the same unchanged fault — reported a one-sided
bond. The daemon log shows why the verdict moved:

```
22:04:40  disconnected: Device with address F0:16:1D:03:4C:FA was not found.
22:07:39  disconnected: Device with address F0:16:1D:03:4C:FA was not found.
22:08:37  disconnected: failed to discover services, device disconnected
   ...    (14 more, every 31s)
```

The failure mode changes at `22:08:37`. `was not found` means the stick is not advertising.
`failed to discover services, device disconnected` means the link comes **up** and then dies
during GATT discovery — the signature of a one-sided bond: BlueZ still holds keys
(`Paired: yes Bonded: yes Trusted: yes`), the M5 does not, so it demands encryption, BlueZ
answers "pairing not supported", and the M5 hangs up.

A manual re-pair fixed it. The `bluetoothctl` pairing session then left a phantom link — BlueZ
held the connection, so the stick stopped advertising and the daemon logged `was not found`
again — cleared with `bluetoothctl disconnect` plus a service restart. Two stacked faults, two
different remedies, which is why the first fix appeared not to work.

## Why the bond is lost

The user reports this "usually happens after I move the laptop out of range" — and, on
questioning, that the stick then runs on its bare internal battery until it dies, because the
18650C base is out of play.

**Going out of range cannot clear a bond.** A supervision timeout drops the link; both sides
keep their LTKs. That is what bonding is for. Nor should a flat battery matter: LTKs live in
NVS, which is flash.

What *can* lose them is an uncontrolled brownout. The firmware has **no low-battery handling at
all** — nothing reads `M5.Axp.GetBatVoltage()` to act on it, and `M5.Axp.PowerOff()` appears
only as a menu item (`main.cpp:313`). The stick runs until the AXP192 cuts the rail, at
whatever moment that lands. A battery sagging slowly toward zero can leave the NVS partition
mid-write, dropping recent entries. That fits the reported "usually, but not always."

**This is a theory, not a finding.** It is tested before any code is written against it — see
Phase 2.

## The hard constraint

Fully hands-off re-pairing is not available without weakening security. `ble_bridge.cpp:121-122`
sets `ESP_LE_AUTH_REQ_SC_MITM_BOND` with `ESP_IO_CAP_OUT` (DisplayOnly), so the ESP32 picks a
**random** 6-digit passkey per pairing and displays it. Nothing on the Linux side can know that
number in advance.

The alternatives — a static passkey (`ESP_BLE_SM_SET_STATIC_PASSKEY`) or Just Works — were
considered and **rejected**. Transcript snippets and tool-call hints flow over this link;
`REFERENCE.md:196-204` argues explicitly for LE Secure Connections bonding. The human types six
digits. That is the price, and it is worth paying.

Bonds are otherwise only erased deliberately: factory reset (`main.cpp:232`) and the `unpair`
command (`xfer.h:99`). Neither is in play here.

## Order of work

Root cause first in intent, but the measurement that proves the root cause **costs a bond** —
the stick must drain to death on battery, and if the theory holds, that destroys the pairing.
So the recovery tool is built first, to make the experiment cheap and repeatable rather than
punishing.

**`repair` → measure → `BatteryGuard`.**

## Phase 1 — `familiar repair`

New `linux-bridge/src/familiar/repair.py`, wired into `cli.py` beside `doctor`.

**Mechanism: an in-process BlueZ D-Bus agent.** Register an `org.bluez.Agent1` with
`KeyboardOnly` capability via `dbus-fast` — already installed as a bleak transitive dependency,
so no new requirement. `repair` owns the whole sequence:

1. `systemctl --user stop familiar`
2. Force `Adapter1.Pairable = true`. Doctor's own remedy leads with `pairable on` for a
   reason: while it is off, BlueZ answers every attempt with "Pairing not supported" and no
   amount of retrying can succeed.
3. Drop BlueZ's stale record with `Adapter1.RemoveDevice`. This is **mandatory**, not
   housekeeping: in a one-sided bond BlueZ still believes it is paired, so `Device1.Pair()`
   fails outright with `org.bluez.Error.AlreadyExists`. Removing the record also tears down
   any link the device is holding, which subsumes the plain disconnect.
4. `Adapter1.StartDiscovery`, then wait for the target to appear. Target selection mirrors
   `_resolve_address` (`ble.py:47-52`): use `cfg.address` when set, otherwise the first device
   whose name starts with `Claude-`
5. `Device1.Pair()` → BlueZ calls our agent's `RequestPasskey` → prompt the user for the six
   digits shown on the stick → return it
6. Set `Device1.Trusted = true`
7. `Device1.Disconnect` — the pairing session's own phantom link, the exact fault that made
   tonight's re-pair look like it had failed
8. `systemctl --user start familiar`
9. Verify: wait up to 30s for `[familiar] connected` in the journal

Holding discovery for the whole operation is what makes this work at all. The documented manual
remedy warns that one-shot `bluetoothctl` invocations tear discovery down between calls, so a
later `pair` reports "not available"; that is why approach C (shelling out to one-shot
commands) is rejected, and why the manual instructions insist on a single interactive session.

Driving an interactive `bluetoothctl` in a pty (approach B) was also rejected: it means
scraping a REPL whose output wording varies by BlueZ version, and it is near-untestable. The
existing code deliberately confines itself to simple one-shot `bluetoothctl` calls
(`ble.py:93`).

**Failure behaviour:** any step that errors aborts and prints the manual steps, so the user is
never worse off than today. The service is restarted even on the failure path — `repair` must
not leave the daemon stopped.

**Doctor stays diagnose-only.** It gains one line — `run: familiar repair` — and continues to
change nothing. The guarantee that "you can always ask what's wrong without risk" is worth more
than saving a keystroke, and tonight's misdiagnosis is the argument: a doctor that auto-repaired
would have triggered a pointless re-pair.

## Phase 2 — measure the discharge curve

`tools/measure_discharge.py`, a development harness. **Not shipped in the wheel** —
`pyproject.toml` packages only `src/familiar`, so `tools/` is already outside the distribution.

The `status` command already returns everything needed (`xfer.h:112-134`): `mV`, `mA`, `usb`.
The harness stops the daemon (only one client can hold the link), connects with bleak,
subscribes to TX notify, sends `{"cmd":"status"}` every 60s, and appends
`iso_time,mV,mA,usb` to a CSV until the link dies.

Serial is not an option: plugging in USB charges the stick and destroys the measurement.

**Procedure:** start from a full charge, unplug, run unattended (~1-2h on the internal cell).

**This experiment is the hypothesis test**, and both outcomes are useful:

- **Bond lost** → theory confirmed. Read warn/shutdown thresholds off the knee of the curve
  and proceed to Phase 3.
- **Bond survived** → theory dead. `BatteryGuard` would have fixed nothing. Regroup on why
  bonds vanish, having spent no effort on the wrong fix.

Phase 3 does not begin until this data exists.

### RESULT (2026-08-12): the theory is dead

Run on 2026-08-12, 15:15 → 17:05. Data: `discharge-2026-08-12.csv` (110 samples, 60s
interval). The stick drained from 4152 mV to 3187 mV over 107 minutes at a steady ~-65 mA,
died, and sat dead for two hours.

**The bond survived.** On reconnecting power the daemon logged
`[familiar] connected F0:16:1D:03:4C:FA` seven seconds after boot; `bluetoothctl info` shows
`Paired/Bonded/Trusted: yes` and `familiar doctor` exits 0.

So an uncontrolled brownout does **not** cost the M5 its pairing keys, and **`BatteryGuard`
must not be written** — its entire justification was protecting a bond that turns out not to
need protecting. Phase 3 is cancelled, not deferred.

**The curve, recorded because it was expensive to obtain:**

| Phase | Behaviour |
|---|---|
| minutes 0-50 | 4152 → 3817 mV, decaying slope |
| minutes 50-100 | **plateau, ~-3.5 mV/min**, 3817 → 3615 mV |
| minutes 100-107 | **collapse, ~-60 mV/min**, 3615 → 3187 mV, then death |

The knee is at **~3600 mV**, and past it there are only ~6 minutes left. Worth recording that
the thresholds this spec originally guessed — warn 3400, shutdown 3300 — were badly wrong:
3400 mV is reached two minutes before death and 3300 mV about one. A guard built on the guess
would have fired far too late to do anything. Had it been written first, as originally
proposed, it would have been useless *and* aimed at a non-existent problem.

It died at 3187 mV, just under the 3200 the firmware's `pct` maths treats as empty
(`xfer.h:118`), so the percentage reading stays honest to zero.

**What this does not settle.** The experiment drained the stick while it was *connected* over
BLE the whole time. The reported real-world trigger is the laptop going *out of range*, where
the stick instead dies while advertising and unconnected. That variable was not controlled, so
"dies while advertising" remains untested — but the mechanism this spec proposed (a brownout
corrupting NVS) is disproven, since NVS demonstrably survived death.

**Next time it recurs, capture evidence rather than assuming:** check whether the stick's
screen actually reads `discover`, and run `journalctl -k -b | grep -i smp` while it is
failing. A new theory needs a real observation, not another plausible story.

## Phase 3 — `BatteryGuard` (conditional on Phase 2)

New `src/battery_guard.h`: a pure state machine with no M5 library calls, so it runs under the
existing host test environment (`pio test -e native`).

```cpp
enum class BattAction { None, Warn, Shutdown };
BattAction update(int vbat_mV, bool onUsb, uint32_t now_ms);
```

Rules:

- **On USB, never fire.** Reset to normal. This is why plugging in recovers.
- **Warn** below the measured warn threshold; **shut down** below the measured shutdown
  threshold. Numbers come from Phase 2. For scale, the firmware's own display maths already
  treats 3200 mV as 0% (`xfer.h:118`).
- **Debounce:** require consecutive samples over roughly 5s. Radio TX bursts sag the rail
  momentarily, and a single ADC dip must never power the buddy off. This is the rule most
  likely to be wrong, so it carries the most tests.
- **Hysteresis:** clear the warn state only above a higher recovery threshold, so a stick
  hovering at the boundary does not flicker.

`main.cpp` samples `GetBatVoltage()` / `GetVBusVoltage()` on the existing heartbeat tick and
acts on the returned action:

- `Warn` → show a low-battery state on screen.
- `Shutdown` → flush stats to NVS, then `M5.Axp.PowerOff()`.

**The NVS flush before a clean power-off is the actual fix.** It guarantees flash is quiescent
rather than possibly mid-write when the rail collapses.

### Explicitly cut

A device→desktop "going down" notice was designed and **dropped**. It was the only piece
touching the wire protocol, and its payoff was merely better log wording. Cutting it leaves
`ble.py` untouched.

Worth recording for whoever needs it later: the daemon currently subscribes to TX notify with a
no-op callback (`ble.py:76`), so **every ack the device sends is discarded**. Any future
device-initiated message needs a real handler there first.

## Testing

- **`repair`** — unit tests with a mocked D-Bus object: passkey supplied, pairing rejected,
  device never appears, systemctl failure. The verification step is tested against a fake
  journal reader.
- **`BatteryGuard`** — host tests under `pio test -e native`: USB override, debounce against a
  single-sample dip, hysteresis, and the warn→shutdown progression.
- **`measure_discharge.py`** — no tests; a throwaway harness whose output is read by a human.

## Non-goals

- No static passkey, no Just Works, no weakening of the encrypted link.
- No auto-repair from the daemon or from `doctor`. A one-sided bond needs a human by
  construction.
- No attempt to keep the stick alive longer (no base is in play, and charge-current changes
  were shelved as unsafe for the bare stick).

## Related, not in this spec

`doctor` ranks a stale historical failure count above live link state: `bond_fires` is computed
from discover-failures over a 10-minute window that a successful re-pair does not reset, and it
is checked before `phantom_fires` (`doctor.py:278-302`, `:327`, `:353`). Tonight that made
doctor demand a re-pair the user had just completed, when the live evidence — `Connected: yes`
plus the daemon still reporting `was not found` — said phantom link. Tracked separately.
