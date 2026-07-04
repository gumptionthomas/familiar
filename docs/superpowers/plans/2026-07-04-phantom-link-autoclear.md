# Phantom-Link Auto-Clear Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After 3 consecutive M5 connect failures, have the daemon best-effort `bluetoothctl disconnect <MAC>` to clear a stale BlueZ phantom link, then keep retrying — no manual intervention.

**Architecture:** Extend the existing `_ble_link_loop` reconnect loop in `ble.py` with a pure `_FailStreak` consecutive-failure counter and a best-effort `_bluetoothctl_disconnect` subprocess helper. On the signaling failure the loop clears the phantom, then continues its normal backoff-and-retry. No new files.

**Tech Stack:** Python 3.11+ asyncio, `asyncio.create_subprocess_exec`, pytest, bleak (BLE), bluetoothctl (BlueZ CLI).

## Global Constraints

- All changes are in `linux-bridge/src/familiar/ble.py` and `linux-bridge/tests/test_ble.py`. No new files.
- Tests run with `uv run pytest -q` from `linux-bridge/`. Full suite must stay green (158 tests + the new ones).
- The failure threshold is a hardcoded default `phantom_after=3`; the mechanism is `bluetoothctl disconnect <MAC>`, best-effort (all failures swallowed).
- Only connect failures **with a known address** count toward the streak; the scan-path / no-device-found case must NOT trigger a clear.
- `run_with_ble`'s signature and call site stay unchanged — the new `_ble_link_loop` params take defaults.
- `asyncio` is already imported at the top of `ble.py`; no new imports are needed.

---

### Task 1: `_FailStreak` consecutive-failure counter

**Files:**
- Modify: `linux-bridge/src/familiar/ble.py` (add the class near the top, after the module constants / before `class BleTransport`)
- Test: `linux-bridge/tests/test_ble.py` (append)

**Interfaces:**
- Produces: `class _FailStreak` with `__init__(self, threshold: int)`, `failure(self) -> bool` (returns True and resets when `threshold` consecutive failures are reached, else False), and `success(self) -> None` (resets the count to 0).

- [ ] **Step 1: Write the failing tests**

Append to `linux-bridge/tests/test_ble.py`:

```python
def test_fail_streak_signals_at_threshold():
    s = ble._FailStreak(3)
    assert s.failure() is False   # 1
    assert s.failure() is False   # 2
    assert s.failure() is True    # 3 -> signal and reset
    assert s.failure() is False   # streak reset -> 1 again
    assert s.failure() is False   # 2
    assert s.failure() is True    # 3


def test_fail_streak_success_resets():
    s = ble._FailStreak(3)
    assert s.failure() is False   # 1
    assert s.failure() is False   # 2
    s.success()                   # reset mid-streak
    assert s.failure() is False   # 1 again
    assert s.failure() is False   # 2
    assert s.failure() is True    # 3
```

(`ble` is already imported at the top of `test_ble.py` as `from familiar import ble`.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_ble.py -q -k fail_streak`
Expected: FAIL — `AttributeError: module 'familiar.ble' has no attribute '_FailStreak'`.

- [ ] **Step 3: Write the implementation**

In `linux-bridge/src/familiar/ble.py`, add this class immediately after the module-level constants (the `NUS_RX`/`NUS_TX`/`NAME_PREFIX`/`CHUNK` block) and before `class BleTransport:`:

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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd linux-bridge && uv run pytest tests/test_ble.py -q -k fail_streak`
Expected: PASS — 2 passed.

- [ ] **Step 5: Commit**

```bash
git add linux-bridge/src/familiar/ble.py linux-bridge/tests/test_ble.py
git commit -m "feat: _FailStreak consecutive-failure counter for BLE link"
```

---

### Task 2: Phantom clear helper + link-loop wiring

**Files:**
- Modify: `linux-bridge/src/familiar/ble.py` (add `_bluetoothctl_disconnect`; rewrite `_ble_link_loop`)
- Test: `linux-bridge/tests/test_ble.py` (append)

**Interfaces:**
- Consumes: `_FailStreak(threshold)` from Task 1.
- Produces: `async def _bluetoothctl_disconnect(address) -> None` (best-effort). `_ble_link_loop` gains two optional params: `disconnect=None` (defaults to `_bluetoothctl_disconnect`) and `phantom_after=3`.

- [ ] **Step 1: Write the failing tests**

Append to `linux-bridge/tests/test_ble.py`:

```python
def test_ble_link_loop_clears_phantom_after_threshold(tmp_path):
    # After the failure threshold, the loop issues a best-effort disconnect for
    # the configured address (phantom_after=1 so the first failure triggers it).
    async def run():
        store = SessionStore()
        bridge = Bridge(store, NullTransport(), str(tmp_path / "s.sock"))
        cleared = []

        async def fake_disconnect(address):
            cleared.append(address)

        def connect(address, disconnected_callback=None):
            raise OSError("device not found")

        cfg = Config(address="AA:BB", owner="", socket_path=str(tmp_path / "s.sock"))

        async def on_connect(transport, owner):
            pass

        task = asyncio.ensure_future(ble._ble_link_loop(
            cfg, bridge, on_connect, connector=connect,
            disconnect=fake_disconnect, phantom_after=1))
        try:
            for _ in range(200):
                await asyncio.sleep(0)
                if cleared:
                    break
            assert cleared == ["AA:BB"]     # phantom clear issued for the M5 address
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


def test_ble_link_loop_no_clear_without_address(tmp_path, monkeypatch):
    # A genuinely absent device (resolve fails -> address is None) is NOT a
    # phantom: the loop must never issue a disconnect.
    async def run():
        store = SessionStore()
        bridge = Bridge(store, NullTransport(), str(tmp_path / "s.sock"))
        cleared = []

        async def fake_disconnect(address):
            cleared.append(address)

        async def boom_resolve(cfg):
            raise RuntimeError("scanner exploded")

        monkeypatch.setattr(ble, "_resolve_address", boom_resolve)

        def connect(address, disconnected_callback=None):
            raise AssertionError("must not connect when resolve fails")

        cfg = Config(address=None, owner="", socket_path=str(tmp_path / "s.sock"))

        async def on_connect(transport, owner):
            pass

        task = asyncio.ensure_future(ble._ble_link_loop(
            cfg, bridge, on_connect, connector=connect,
            disconnect=fake_disconnect, phantom_after=1))
        try:
            for _ in range(200):
                await asyncio.sleep(0)
            assert cleared == []            # absent device != phantom, no disconnect
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_ble.py -q -k "clears_phantom or no_clear_without_address"`
Expected: FAIL — `TypeError: _ble_link_loop() got an unexpected keyword argument 'disconnect'`.

- [ ] **Step 3: Add the `_bluetoothctl_disconnect` helper**

In `linux-bridge/src/familiar/ble.py`, add this function immediately before `async def _ble_link_loop(...)`:

```python
async def _bluetoothctl_disconnect(address) -> None:
    # Clear a stale BlueZ link ("phantom") that leaves the device 'connected' at
    # the OS level while bleak can't reach it. Mirrors the manual
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

- [ ] **Step 4: Rewrite `_ble_link_loop` to track failures and clear the phantom**

In `linux-bridge/src/familiar/ble.py`, replace the entire current `_ble_link_loop` function:

```python
async def _ble_link_loop(cfg, bridge, on_connect, connector=None) -> None:
    connect = connector or BleakClient
    backoff = 1.0

    def reset_backoff():
        nonlocal backoff
        backoff = 1.0

    while True:
        # Everything BLE-related is inside the try: a scan/connect/link failure
        # must only back off and retry, never escape and cancel the persistent
        # bridge (which would refreeze the Tidbyt — the bug this fix undoes).
        try:
            address = await _resolve_address(cfg)
            if not address:
                print("[familiar] no Claude- device found; is it awake? "
                      "have you paired with bluetoothctl?")
            else:
                await _ble_session(bridge, on_connect, cfg.owner, connect,
                                   address, on_connected=reset_backoff)
                print(f"[familiar] link dropped; reconnecting {address}")
                await asyncio.sleep(1)         # brief settle, guard against flap
                continue                       # backoff already reset on connect
        except Exception as e:
            bridge.transport = NullTransport()  # ensure detached on any failure
            print(f"[familiar] disconnected: {e}")
        await asyncio.sleep(min(backoff, 30))
        backoff = min(backoff * 2, 30)
```

with:

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
        # Everything BLE-related is inside the try: a scan/connect/link failure
        # must only back off and retry, never escape and cancel the persistent
        # bridge (which would refreeze the Tidbyt — the bug the decouple undoes).
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
                await asyncio.sleep(1)         # brief settle, guard against flap
                continue                       # backoff/streak reset on connect
        except Exception as e:
            bridge.transport = NullTransport()  # ensure detached on any failure
            print(f"[familiar] disconnected: {e}")
            # After repeated failures with a known address, a stale BlueZ link
            # ("phantom") is the likely cause; clear it and keep retrying.
            if address and streak.failure():
                print(f"[familiar] clearing a possible stale link to {address}")
                await clear_phantom(address)
        await asyncio.sleep(min(backoff, 30))
        backoff = min(backoff * 2, 30)
```

Note the two behavioral additions beyond the phantom clear: `address = None` is now set before the `try` (so the `except` can safely reference it even if `_resolve_address` raises), and the `on_connected` hook is renamed `on_up` and now resets the failure streak as well as the backoff.

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `cd linux-bridge && uv run pytest tests/test_ble.py -q -k "clears_phantom or no_clear_without_address"`
Expected: PASS — 2 passed.

- [ ] **Step 6: Run the full suite**

Run: `cd linux-bridge && uv run pytest -q`
Expected: PASS — all green (158 prior + 2 from Task 1 + 2 here = 162).

- [ ] **Step 7: Commit**

```bash
git add linux-bridge/src/familiar/ble.py linux-bridge/tests/test_ble.py
git commit -m "feat: auto-clear a stale BlueZ phantom link after repeated M5 connect failures"
```

---

## Final verification (after all tasks)

- [ ] Full suite green: `cd linux-bridge && uv run pytest -q` → 162 passed.
- [ ] `run_with_ble` still calls `_ble_link_loop(cfg, bridge, on_connect, connector)` with no extra args (new params defaulted): `grep -n "_ble_link_loop(" linux-bridge/src/familiar/ble.py`.
- [ ] Hardware check (manual, owner, deferred to redeploy): with the daemon running and the M5 in a phantom state, confirm the daemon logs "clearing a possible stale link to …" after ~3 failed connects and then reconnects on its own — no manual `bluetoothctl disconnect` needed.
