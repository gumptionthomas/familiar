# BLE Reconnect Flap Backoff — Design

**Goal:** Stop the bridge from hammering the radio when the M5 is out of range or at the
edge of it. A link that connects and immediately dies must count as a *failure*, so the
existing exponential backoff can engage.

## The bug

`_ble_link_loop` (`linux-bridge/src/familiar/ble.py`) has an exponential backoff that is
**structurally unreachable on the flap path**. Two lines cause it:

1. `_ble_session` calls `on_connected()` — i.e. `on_up()` — the instant `connect()`
   returns (`ble.py:65`). That resets `backoff = 1.0` and clears the failure streak,
   *before* anyone knows whether the link will hold.
2. When the session ends, the loop does `await asyncio.sleep(1); continue`
   (`ble.py:134-135`), which **skips the backoff sleep at the bottom of the loop
   entirely**.

So a link that lives two seconds is retried one second later, forever. Backoff only
engages when `connect()` raises outright.

Observed live (2026-07-12, laptop carried away from the desk):

```
15:05:17 connected            15:05:19 link dropped     (2s)
15:05:22 connected            15:05:49 link dropped
15:05:53 connected            15:05:56 link dropped     (3s)
15:05:59 connected            15:06:40 link dropped
...  ~20 connect attempts/minute, indefinitely
```

A second, milder symptom: out-of-range failures are indistinguishable from a stale BlueZ
phantom, so the phantom-clear (`bluetoothctl disconnect`, added in PR #44) re-fires every
3 failures — roughly every 90 seconds — clearing a link that is not stale, merely absent.

The user-visible effect is a Bluetooth indicator that flashes constantly whenever they are
away from the buddy.

## Design

### 1. A session succeeds only if the link held

`_ble_session` records the time the link came up and **returns how long it stayed up**
(seconds, float). Connect latency is excluded — the clock starts at the point where
`on_connected` fires today.

`_ble_link_loop` then branches on the returned duration:

- **held >= `HOLD_MIN` (30.0s)** — a real session. Reset `backoff` to 1.0 and call
  `streak.success()`. The next reconnect attempt happens after a 1s sleep, exactly as
  today.
- **held < `HOLD_MIN`** — a flap. Do **not** reset. Fall through to the normal backoff
  sleep like any other failure.

This removes the `on_connected` / `on_up` callback entirely (its only purpose was the
premature reset) and removes the `sleep(1); continue` shortcut. The loop ends with a
single backoff sleep on every path.

**Why 30 seconds:** in the observed logs, flaps live 2–7s and healthy links live minutes to
hours. Nothing sits near the boundary, so the threshold is not sensitive.

### 2. Rate-limit the phantom-clear

Keep `phantom_after = 3` consecutive failures, but add a floor:
`PHANTOM_MIN_INTERVAL = 300.0` seconds between clears. A clear is issued only if at least
that long has passed since the previous one; the first clear after startup fires
immediately (last-clear initialised to `-inf`).

**Why a rate limit rather than arm/disarm-on-held-link:** a one-shot arm that only re-arms
after a link that held could *permanently* disable the phantom-clear — if a genuine
phantom appears and no good link ever follows, nothing would ever clear it. A rate limit
keeps the remedy available forever while stopping the every-90s hammering.

### 3. Backoff ceiling unchanged

`max_backoff` stays at **30s** (user's explicit choice). Steady state while away is 2
connect attempts/minute, down from ~20. Worst-case reconnect latency on return is 30s.

### 4. Diagnose before clearing, and log what is actually wrong

**Motivating incident (2026-07-12).** The M5 silently lost its side of the pairing bond
while the laptop kept its own. Every connect then went: link up → M5 sends
`SMP: Security Request` → laptop answers `Pairing Failed: Pairing not supported` → M5
terminates the link (`Reason: Remote User Terminated (0x13)`). Bleak surfaced only
`failed to discover services, device disconnected`, which describes the *aftermath* and
names none of that. Diagnosis required `btmon` and root. Meanwhile the phantom-clear fired
~280 times against a condition it fundamentally cannot repair.

Two lessons, both cheap to encode:

**(a) An unpaired device is not a phantom.** `bluetoothctl disconnect` clears a stale
*link*; it cannot restore *keys*. Before clearing, check whether the device is still
paired. If it is not, skip the clear entirely — it is pure radio noise — and log the
actual remedy instead.

**(b) Log the diagnosable state, not a reason code we cannot obtain.** Bleak raises a
generic error and does not expose the HCI disconnect reason, so this spec does **not**
promise one. What it logs instead is the state that *explains* the failure, gathered from
`bluetoothctl` (same best-effort subprocess pattern as `_bluetoothctl_disconnect`):

```
[familiar] 3 consecutive failures — adapter: powered=yes pairable=no |
           device F0:16:1D:03:4C:FA: paired=no bonded=no trusted=no
[familiar] the M5 is NOT paired. Its firmware requires LE Secure Connections + MITM
           (see src/ble_bridge.cpp), so pairing needs a 6-digit passkey typed by a human
           and CANNOT be repaired by this daemon. In a terminal:
             bluetoothctl
             agent KeyboardOnly / default-agent / scan on
             pair F0:16:1D:03:4C:FA     (type the code shown on the stick)
             trust F0:16:1D:03:4C:FA
[familiar] skipping the stale-link clear: an unpaired device is not a phantom.
```

This runs on the existing trigger (`phantom_after` consecutive failures) and obeys the
same `PHANTOM_MIN_INTERVAL` rate limit, so it cannot become a new source of log spam.

The passkey requirement is the crux: because pairing is `ESP_LE_AUTH_REQ_SC_MITM_BOND`
with the stick as DisplayOnly, **no headless agent can ever complete it**. The daemon's
correct behavior is therefore to detect the condition, stop hammering, and tell the human
exactly what to do — not to keep retrying forever.

## Constants

| Name | Value | Meaning |
| --- | --- | --- |
| `HOLD_MIN` | `30.0` s | Minimum link lifetime to count as a success |
| `PHANTOM_MIN_INTERVAL` | `300.0` s | Floor between `bluetoothctl disconnect` calls |
| `max_backoff` | `30.0` s | Unchanged |
| `phantom_after` | `3` | Unchanged |

## Interfaces

- `_ble_session(bridge, on_connect, owner, connect, address, clock=time.monotonic) -> float`
  Returns seconds the link was up. The `on_connected` parameter is **removed**.
- `_link_state(address) -> dict` — **new.** Best-effort `bluetoothctl show` / `info`
  scrape returning `{"powered", "pairable", "paired", "bonded", "trusted"}` as bools, with
  `None` for any field it could not determine (missing binary, parse failure). Never
  raises; a fully-unknown result must not change behavior.
- `_ble_link_loop(cfg, bridge, on_connect, connector=None, disconnect=None,
  phantom_after=3, clock=time.monotonic, sleep=asyncio.sleep, link_state=_link_state)`
  Gains `clock`, `sleep`, and `link_state` injection.

`clock`, `sleep`, and `link_state` are injected purely for testability; the production
defaults preserve current behavior exactly.

**Fallback rule:** if `_link_state` cannot determine `paired` (returns `None`), the loop
falls back to today's behavior and *does* issue the phantom-clear. Only a confident
`paired=False` suppresses it. An unknown state must never make us less capable of
self-healing than we are today.

## Error handling

Unchanged in kind: every BLE operation stays inside the `try`, so a scan/connect/link
failure only backs off and retries and can never escape to cancel the persistent Bridge
(the PR #43 decoupling). A connect that raises yields held = 0 by construction and is
handled on the existing exception path. `bridge.transport` is still restored to
`NullTransport` on every exit path.

A resolve failure (address `None`) still does **not** count toward the failure streak and
never triggers a phantom-clear — an absent device is not a phantom.

## Testing

Existing tests in `linux-bridge/tests/test_ble.py` (7) must keep passing, which also
proves the production defaults are behavior-preserving. In practice, commit `ee241be` had
to inject a `link_state` stub into `test_ble_link_loop_clears_phantom_after_threshold` and
`test_phantom_clear_rate_limited` — adding `link_state` with a real-`bluetoothctl` default
made them shell out to a subprocess mid-test. No assertion in either test changed.

New tests, using an injected fake clock and a sleep-recorder so no real time passes:

1. **`test_flap_does_not_reset_backoff`** — sessions that hold 2s repeatedly; assert the
   recorded sleeps grow `1, 2, 4, 8, …` rather than staying at 1.
2. **`test_held_link_resets_backoff`** — after backoff has grown, one session holding 60s
   must reset the next sleep to 1s.
3. **`test_phantom_clear_rate_limited`** — with continuous failures, assert clears are
   spaced at least `PHANTOM_MIN_INTERVAL` apart on the fake clock (i.e. not once per 3
   failures).
4. **`test_no_phantom_clear_when_unpaired`** — with `link_state` reporting `paired=False`,
   the loop must issue **zero** `bluetoothctl disconnect` calls, however many failures
   accumulate. This is the 2026-07-12 incident, encoded.
5. The same condition must emit the passkey/`KeyboardOnly` remedy text, so the next
   person sees the fix in `journalctl` rather than needing an HCI trace. This is not a
   separate test: it's folded into `test_no_phantom_clear_when_unpaired`'s `capsys`
   assertions.
6. **`test_unknown_link_state_still_clears`** — with `link_state` returning `paired=None`
   (e.g. `bluetoothctl` absent), the phantom-clear still fires as it does today. Proves the
   diagnostic is strictly additive and can't regress self-healing.

## Verification (hardware)

Redeploy the bridge, then walk the laptop out of range and watch
`journalctl --user -u familiar.service -f`. Expect connect attempts to decay to one every
30s instead of a continuous connect/drop churn, and the Bluetooth indicator to settle.
Returning to the desk must reconnect within ~30s.

## Out of scope

- RSSI/proximity-aware suppression (would require scanning, which is the radio activity we
  are trying to avoid).
- Any change to the Tidbyt path, the haiku path, or the firmware.
- Reducing the per-attempt log line volume (2/min is acceptable and useful).
- **Surfacing the HCI disconnect reason code** (`0x13`, SMP failure, …). Bleak does not
  expose it; obtaining it requires `btmon` and root. Section 4 logs the pairing *state*
  instead, which is what actually explains these failures.
- **Automatic re-pairing.** The firmware requires a human-typed passkey by design; a daemon
  cannot and should not attempt to work around MITM protection.
