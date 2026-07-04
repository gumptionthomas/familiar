# Phantom-Link Auto-Clear — Design

**Goal:** When the M5 can't be reached because of a stale BlueZ "phantom" link,
have the daemon clear it automatically after a few failed connects, instead of
requiring a manual `bluetoothctl disconnect`.

## Background

BlueZ sometimes keeps a device marked `Connected: yes` at the OS level while
bleak can no longer establish its own connection — bleak raises
`Device with address F0:16:1D:03:4C:FA was not found`. The daemon then spins in
its reconnect loop indefinitely; the M5 stays dark until someone runs
`bluetoothctl disconnect <MAC>` and the daemon reconnects.

Observed live this session: after a service restart the M5 showed
`Connected: yes` to BlueZ but bleak reported "not found" on a loop; the manual
`bluetoothctl disconnect F0:16:1D:03:4C:FA` + retry fixed it immediately.

The recent decoupling fix means the **Tidbyt** no longer freezes during this
(the bridge runs independent of BLE), so this feature only affects the M5's own
reconnection resilience.

## Decisions (from brainstorming)

1. **Trigger:** after **N = 3 consecutive** connect failures with a known
   address, issue one clear, then keep retrying. Not per-failure (too
   aggressive on ordinary transient drops) and not phantom-signature detection
   (extra BlueZ state reads for no real gain — a disconnect on a genuinely
   absent device is a harmless no-op).
2. **Mechanism:** shell out to `bluetoothctl disconnect <MAC>`, best-effort.
   It is the exact manual remedy that works, and the project already assumes
   `bluetoothctl` is present (the pairing hint message references it).
3. **Threshold is a hardcoded constant (3)** for now — matches how the other
   tuning knobs live; liftable to config later (YAGNI).

## Architecture

All changes live in `linux-bridge/src/familiar/ble.py`, extending the
`_ble_link_loop` reconnect loop introduced by the decouple fix. No new files.

### `_FailStreak` — consecutive-failure counter (pure, testable)

```python
class _FailStreak:
    """Counts consecutive connect failures; signals when to clear the link."""
    def __init__(self, threshold: int):
        self.threshold = threshold
        self.count = 0

    def failure(self) -> bool:
        # Return True (and reset) once `threshold` consecutive failures are seen.
        self.count += 1
        if self.count >= self.threshold:
            self.count = 0
            return True
        return False

    def success(self) -> None:
        self.count = 0
```

- Only connect failures **with a known address** call `.failure()`. The
  scan-path / no-device-found case does not — that's a genuinely absent device,
  not a phantom.
- A successful connect calls `.success()` (wired through the existing
  `on_connected` hook that already resets the backoff), so the streak never
  accumulates across a good session.

### `_bluetoothctl_disconnect` — best-effort clear

```python
async def _bluetoothctl_disconnect(address) -> None:
    # Clear a stale BlueZ link ("phantom") that leaves the device 'connected'
    # at the OS level while bleak can't reach it. Mirrors the manual
    # `bluetoothctl disconnect <MAC>` remedy. Best-effort: any failure (missing
    # binary, non-zero exit, timeout) is swallowed — we retry the connect either
    # way.
    try:
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "disconnect", address,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception:
        pass
```

### `_ble_link_loop` integration

The loop gains two injectable parameters (for testing) with production
defaults: `disconnect=None` (→ `_bluetoothctl_disconnect`) and
`phantom_after=3`. On a connect failure with a known address, it records the
failure and, when the streak signals, clears the phantom before continuing the
normal backoff-and-retry:

```python
async def _ble_link_loop(cfg, bridge, on_connect, connector=None,
                         disconnect=None, phantom_after=3) -> None:
    connect = connector or BleakClient
    clear_phantom = disconnect or _bluetoothctl_disconnect
    backoff = 1.0
    streak = _FailStreak(phantom_after)

    def on_up():
        nonlocal backoff
        backoff = 1.0
        streak.success()

    while True:
        address = None
        try:
            address = await _resolve_address(cfg)
            if not address:
                print("[familiar] no Claude- device found; is it awake? "
                      "have you paired with bluetoothctl?")
            else:
                await _ble_session(bridge, on_connect, cfg.owner, connect,
                                   address, on_connected=on_up)
                print(f"[familiar] link dropped; reconnecting {address}")
                await asyncio.sleep(1)
                continue
        except Exception as e:
            bridge.transport = NullTransport()
            print(f"[familiar] disconnected: {e}")
            if address and streak.failure():
                print(f"[familiar] clearing a possible stale link to {address}")
                await clear_phantom(address)
        await asyncio.sleep(min(backoff, 30))
        backoff = min(backoff * 2, 30)
```

`run_with_ble` is unchanged: it calls `_ble_link_loop(cfg, bridge, on_connect,
connector)` and the new parameters take their defaults.

## Data flow

```
connect attempt (known address) fails
  -> bridge.transport = NullTransport()   (already the case)
  -> streak.failure()
       count < 3 -> False -> normal backoff + retry
       count == 3 -> True (reset) -> bluetoothctl disconnect <MAC> -> backoff + retry
successful connect -> on_up() -> streak.success() (+ backoff reset)
```

## Error handling

- The disconnect is best-effort and never blocks or crashes the loop; a failed
  clear (missing binary, timeout, non-zero exit) falls through to the normal
  backoff-and-retry.
- A `_resolve_address` exception (scan glitch) still backs off and retries; it
  does not count toward the streak (address is `None`), so it never triggers a
  clear.

## Testing

- **Unit — `_FailStreak`:** `.failure()` returns `False, False, True` at
  threshold 3 and resets (next cycle again `False, False, True`); `.success()`
  resets a partial streak.
- **Integration — `_ble_link_loop` clears on the address path:** with an
  always-raising `connector`, an injected recording `disconnect`, and
  `phantom_after=1`, assert `disconnect` is called with the configured address
  on the first failure.
- **Integration — no clear without an address:** with `_resolve_address`
  monkeypatched to raise (or `cfg.address` unset and the scanner returning
  `None`) and an injected recording `disconnect`, assert `disconnect` is
  **never** called — a genuinely absent device is not a phantom.
- **`_bluetoothctl_disconnect`** is thin best-effort glue (subprocess +
  swallow) and is left untested, consistent with the other best-effort IO in
  this module; running the real subprocess in CI would be flaky.

## Out of scope (YAGNI)

- No configurable threshold (hardcoded `3`).
- No BlueZ phantom-signature detection (query `Connected` state) — the blind
  best-effort disconnect is simpler and equally effective.
- No D-Bus `Device1.Disconnect` path — `bluetoothctl` is present and matches the
  proven manual remedy.
- No change to the Tidbyt path (already decoupled).
