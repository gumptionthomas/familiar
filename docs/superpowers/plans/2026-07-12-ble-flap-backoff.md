# BLE Reconnect Flap Backoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the bridge hammering the radio when the M5 is out of range, and make an
unrecoverable pairing failure diagnosable from `journalctl` instead of an HCI trace.

**Architecture:** Two changes to one file, `linux-bridge/src/familiar/ble.py`. First, a
session only counts as a success if the link *held* (>= 30s), so the existing exponential
backoff — currently unreachable on the flap path — actually engages. Second, before
issuing a `bluetoothctl disconnect` ("phantom clear"), check whether the device is even
paired; an unpaired device is not a phantom and the clear cannot help it.

**Tech Stack:** Python 3.11+, asyncio, bleak, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-12-ble-flap-backoff-design.md`

## Global Constants

Copy these values verbatim. They are module-level in `ble.py`.

| Name | Value | Meaning |
| --- | --- | --- |
| `HOLD_MIN` | `30.0` | Seconds a link must survive to count as a success |
| `PHANTOM_MIN_INTERVAL` | `300.0` | Minimum seconds between `bluetoothctl disconnect` calls |
| `max_backoff` | `30.0` | Backoff ceiling — **unchanged**, do not alter |
| `phantom_after` | `3` | Consecutive failures before a clear — **unchanged** |

## Global Constraints

- **Never let a BLE failure escape `_ble_link_loop`.** Every scan/connect/link operation
  stays inside the `try`. If an exception escapes, it cancels the persistent `Bridge` and
  refreezes the Tidbyt — the exact bug PR #43 fixed. This is the single most important
  invariant in this file.
- **`bridge.transport` must be restored to `NullTransport()` on every exit path.**
- A resolve failure (address is `None`) must **never** count toward the failure streak and
  must **never** trigger a phantom-clear. An absent device is not a phantom.
- `clock`, `sleep`, and `link_state` are injected **only** for testability. Their
  production defaults must preserve current behavior exactly.
- The 7 existing tests in `linux-bridge/tests/test_ble.py` must keep passing. Prefer not
  to edit them; if one breaks, the change is probably wrong. In practice, commit
  `ee241be` had to inject a `link_state` stub into
  `test_ble_link_loop_clears_phantom_after_threshold` and
  `test_phantom_clear_rate_limited`, because adding `link_state` with a real-`bluetoothctl`
  default made them shell out to a subprocess mid-test. No assertion was changed.
- Run tests from the `linux-bridge/` directory.

---

### Task 1: Minimum-hold gate so backoff actually engages

**Files:**
- Modify: `linux-bridge/src/familiar/ble.py`
- Test: `linux-bridge/tests/test_ble.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `_ble_session(bridge, on_connect, owner, connect, address, clock=time.monotonic) -> float`
    — returns the number of seconds the link was up. The `on_connected` parameter is
    **removed**.
  - `_ble_link_loop(cfg, bridge, on_connect, connector=None, disconnect=None,
    phantom_after=3, clock=time.monotonic, sleep=asyncio.sleep)` — gains `clock` and
    `sleep`.
  - Module constants `HOLD_MIN = 30.0`, `PHANTOM_MIN_INTERVAL = 300.0`.

**Background:** `_ble_session` currently calls `on_connected()` the instant `connect()`
returns, which resets `backoff` to 1.0 before anyone knows the link will hold. Then the
loop does `await asyncio.sleep(1); continue`, skipping the backoff sleep entirely. Result:
a link that lives 2 seconds is retried 1 second later, forever. Observed live: ~20 connect
attempts/minute indefinitely while the user was away.

- [ ] **Step 1: Write the failing tests**

Add to `linux-bridge/tests/test_ble.py`. These use a fake clock and a sleep recorder so no
real time passes.

```python
class _FakeTime:
    """Deterministic clock; recorded sleeps advance it."""
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_flap_does_not_reset_backoff(tmp_path):
    # A link that connects and dies in 2s is a FLAP, not a success. Backoff must
    # grow (1, 2, 4, 8...) instead of pinning at 1s forever.
    async def run():
        store = SessionStore()
        bridge = Bridge(store, NullTransport(), str(tmp_path / "s.sock"))
        t = _FakeTime()

        async def short_session(*a, **k):
            t.now += 2.0          # link held only 2s
            return 2.0

        cfg = Config(address="AA:BB", owner="", socket_path=str(tmp_path / "s.sock"))

        async def on_connect(transport, owner):
            pass

        orig = ble._ble_session
        ble._ble_session = short_session
        try:
            task = asyncio.ensure_future(ble._ble_link_loop(
                cfg, bridge, on_connect, connector=lambda *a, **k: None,
                disconnect=lambda a: asyncio.sleep(0),
                clock=t.clock, sleep=t.sleep))
            for _ in range(400):
                await asyncio.sleep(0)
                if len(t.sleeps) >= 5:
                    break
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            ble._ble_session = orig

        assert t.sleeps[:5] == [1.0, 2.0, 4.0, 8.0, 16.0], \
            f"flap must back off exponentially, got {t.sleeps[:5]}"

    asyncio.run(run())


def test_held_link_resets_backoff(tmp_path):
    # A link that held >= HOLD_MIN is a real session: backoff resets to 1s.
    async def run():
        store = SessionStore()
        bridge = Bridge(store, NullTransport(), str(tmp_path / "s2.sock"))
        t = _FakeTime()
        calls = {"n": 0}

        async def sessions(*a, **k):
            calls["n"] += 1
            # First three are flaps (backoff climbs), the fourth holds.
            held = 2.0 if calls["n"] <= 3 else 60.0
            t.now += held
            return held

        cfg = Config(address="AA:BB", owner="", socket_path=str(tmp_path / "s2.sock"))

        async def on_connect(transport, owner):
            pass

        orig = ble._ble_session
        ble._ble_session = sessions
        try:
            task = asyncio.ensure_future(ble._ble_link_loop(
                cfg, bridge, on_connect, connector=lambda *a, **k: None,
                disconnect=lambda a: asyncio.sleep(0),
                clock=t.clock, sleep=t.sleep))
            for _ in range(600):
                await asyncio.sleep(0)
                if len(t.sleeps) >= 5:
                    break
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            ble._ble_session = orig

        # 3 flaps -> 1, 2, 4. Then a 60s hold resets -> 1 again.
        assert t.sleeps[:4] == [1.0, 2.0, 4.0, 1.0], \
            f"a held link must reset backoff, got {t.sleeps[:4]}"

    asyncio.run(run())


def test_phantom_clear_rate_limited(tmp_path):
    # With continuous failures the clear must respect PHANTOM_MIN_INTERVAL (300s
    # of fake clock), NOT fire once per 3 failures. In the 2026-07-12 incident it
    # fired ~every 90s for seven hours.
    async def run():
        store = SessionStore()
        bridge = Bridge(store, NullTransport(), str(tmp_path / "r.sock"))
        t = _FakeTime()
        cleared_at = []

        async def fake_disconnect(address):
            cleared_at.append(t.now)

        def connect(address, disconnected_callback=None):
            raise OSError("device not found")

        cfg = Config(address="AA:BB", owner="", socket_path=str(tmp_path / "r.sock"))

        async def on_connect(transport, owner):
            pass

        task = asyncio.ensure_future(ble._ble_link_loop(
            cfg, bridge, on_connect, connector=connect,
            disconnect=fake_disconnect, phantom_after=3,
            clock=t.clock, sleep=t.sleep))
        try:
            # Run well past 300s of fake time so a second clear becomes eligible.
            for _ in range(3000):
                await asyncio.sleep(0)
                if t.now > 900 or len(cleared_at) >= 3:
                    break
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert cleared_at, "expected at least one clear"
        gaps = [b - a for a, b in zip(cleared_at, cleared_at[1:])]
        assert all(g >= ble.PHANTOM_MIN_INTERVAL for g in gaps), \
            f"clears must be >= {ble.PHANTOM_MIN_INTERVAL}s apart, got gaps {gaps}"

    asyncio.run(run())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && python -m pytest tests/test_ble.py -k "backoff or rate_limited" -v`

Expected: FAIL — `_ble_link_loop() got an unexpected keyword argument 'clock'`.

- [ ] **Step 3: Add the constants and rewrite `_ble_session` to return its hold duration**

In `linux-bridge/src/familiar/ble.py`, add `import time` at the top with the other imports,
and these constants next to `CHUNK`:

```python
HOLD_MIN = 30.0              # a link must survive this long to count as a success
PHANTOM_MIN_INTERVAL = 300.0  # floor between bluetoothctl disconnect calls
```

Replace `_ble_session` entirely. Note `on_connected` is **gone** — the reset it drove was
the bug — and the function now returns how long the link was actually up:

```python
async def _ble_session(bridge, on_connect, owner, connect, address,
                       clock=time.monotonic) -> float:
    # One connect -> serve -> disconnect cycle. Attaches a live BleTransport to
    # the already-running bridge for the duration of the link, then restores
    # NullTransport so the bridge's other loops (Tidbyt, haiku, sweep) keep
    # running once the M5 goes away. The bridge itself is never torn down here.
    #
    # Returns how long the link was UP (seconds), measured from the moment the
    # connection is established -- excluding connect latency. The caller uses
    # this to tell a real session from a flap: a link that dies in 2s must not
    # be credited as a success, or the reconnect backoff can never engage.
    disconnected = asyncio.Event()
    async with connect(
        address,
        disconnected_callback=lambda _c: disconnected.set(),
    ) as client:
        up_at = clock()
        print(f"[familiar] connected {address}")
        # TX notify is encrypted-only; subscribing forces the encrypted link up
        # (and lets the device send acks).
        try:
            await client.start_notify(NUS_TX, lambda _c, _d: None)
        except Exception:
            pass
        transport = BleTransport(client)
        await on_connect(transport, owner)
        bridge.transport = transport
        try:
            # bleak fires disconnected_callback on battery death, unplug, or
            # out-of-range; hold the link until then. The heartbeat loop
            # swallows write errors, so it can't self-detect a dead link.
            await disconnected.wait()
        finally:
            bridge.transport = NullTransport()
        return clock() - up_at
```

- [ ] **Step 4: Rewrite `_ble_link_loop` so every path goes through the backoff sleep**

Replace `_ble_link_loop` in `linux-bridge/src/familiar/ble.py`:

```python
async def _ble_link_loop(cfg, bridge, on_connect, connector=None,
                         disconnect=None, phantom_after=3,
                         clock=time.monotonic, sleep=asyncio.sleep) -> None:
    connect = connector or BleakClient
    clear_phantom = disconnect or _bluetoothctl_disconnect
    backoff = 1.0
    max_backoff = 30.0
    streak = _FailStreak(phantom_after)
    last_clear = float("-inf")

    while True:
        # Everything BLE-related is inside the try: a scan/connect/link failure
        # must only back off and retry, never escape and cancel the persistent
        # bridge (which would refreeze the Tidbyt -- the bug #43 undoes).
        address = None
        try:
            address = await _resolve_address(cfg)
            if not address:
                # No address = a genuinely absent device, not a phantom, so the
                # failure streak / phantom-clear intentionally does not apply.
                print("[familiar] no Claude- device found; is it awake? "
                      "have you paired with bluetoothctl?")
            else:
                held = await _ble_session(bridge, on_connect, cfg.owner, connect,
                                          address, clock=clock)
                if held >= HOLD_MIN:
                    # A real session. Reconnect promptly.
                    print(f"[familiar] link dropped after {held:.0f}s; "
                          f"reconnecting {address}")
                    backoff = 1.0
                    streak.success()
                else:
                    # A flap -- the signature of a device at the edge of range.
                    # Treat it as a failure so the backoff below engages; a
                    # 2-second link credited as a success is what let this loop
                    # retry forever and hammer the radio.
                    print(f"[familiar] link flapped after {held:.1f}s; "
                          f"backing off {address}")
                    streak.failure()
        except Exception as e:
            bridge.transport = NullTransport()  # ensure detached on any failure
            print(f"[familiar] disconnected: {e}")
            # After repeated failures with a known address, a stale BlueZ link
            # ("phantom") is the likely cause; clear it and keep retrying. Rate
            # limited: an out-of-range device produces the same failures, and
            # clearing on every 3rd one just hammers the radio for nothing.
            if address and streak.failure() \
                    and clock() - last_clear >= PHANTOM_MIN_INTERVAL:
                last_clear = clock()
                print(f"[familiar] clearing a possible stale link to {address}")
                await clear_phantom(address)
        await sleep(min(backoff, max_backoff))
        backoff = min(backoff * 2, max_backoff)
```

Note what disappeared: the `await asyncio.sleep(1); continue` shortcut. There is now
exactly one sleep, at the bottom, on every path.

- [ ] **Step 5: Run the full suite**

Run: `cd linux-bridge && python -m pytest tests/ -v`

Expected: PASS — the 3 new tests plus all 7 pre-existing `test_ble.py` tests and the rest
of the suite (was 162 passing; expect 165).

If a pre-existing test fails, **stop** — the change is wrong; do not edit the test.

- [ ] **Step 6: Commit**

```bash
git add linux-bridge/src/familiar/ble.py linux-bridge/tests/test_ble.py
git commit -m "fix: only credit a BLE link as a success if it held

A connect that dies in 2s was resetting the reconnect backoff, and the
post-session path skipped the backoff sleep entirely -- so an M5 at the
edge of range was retried every ~1s forever, hammering the radio. Time
each session and treat anything under HOLD_MIN (30s) as a flap.

Also rate-limit the phantom-clear to PHANTOM_MIN_INTERVAL (300s); it was
firing every ~90s against out-of-range devices it cannot help."
```

---

### Task 2: Don't phantom-clear an unpaired device — say what's actually wrong

**Files:**
- Modify: `linux-bridge/src/familiar/ble.py`
- Test: `linux-bridge/tests/test_ble.py`

**Interfaces:**
- Consumes (from Task 1): `_ble_link_loop(..., clock=time.monotonic, sleep=asyncio.sleep)`,
  constants `HOLD_MIN`, `PHANTOM_MIN_INTERVAL`, the `last_clear` rate limit.
- Produces:
  - `async def _link_state(address) -> dict` — keys `powered`, `pairable`, `paired`,
    `bonded`, `trusted`; each value is `True`, `False`, or `None` (undetermined).
  - `_ble_link_loop(..., link_state=_link_state)` — one further injected parameter.

**Background (the 2026-07-12 incident):** The M5 silently lost its side of the pairing
bond while the laptop kept its own. Every connect went: link up → M5 sends
`SMP: Security Request` → laptop answers `Pairing Failed: Pairing not supported` → M5
terminates the link. Bleak reported only `failed to discover services, device
disconnected`. The phantom-clear fired ~280 times against it. **`bluetoothctl disconnect`
clears a stale link; it cannot restore stale keys.** An unpaired device is not a phantom.

The firmware (`src/ble_bridge.cpp:69-72`, `ESP_LE_AUTH_REQ_SC_MITM_BOND`) requires a
6-digit passkey displayed on the stick and typed by a human. **No daemon can complete that
pairing.** The correct behavior is to detect it, stop hammering, and print the remedy.

- [ ] **Step 1: Write the failing tests**

Add to `linux-bridge/tests/test_ble.py`:

```python
def test_no_phantom_clear_when_unpaired(tmp_path, capsys):
    # The 2026-07-12 incident: the M5 lost its bond, so every connect failed and
    # the phantom-clear fired ~280 times against a condition it cannot repair.
    # bluetoothctl disconnect clears a stale LINK, not stale KEYS.
    async def run():
        store = SessionStore()
        bridge = Bridge(store, NullTransport(), str(tmp_path / "u.sock"))
        cleared = []

        async def fake_disconnect(address):
            cleared.append(address)

        async def unpaired_state(address):
            return {"powered": True, "pairable": False, "paired": False,
                    "bonded": False, "trusted": False}

        def connect(address, disconnected_callback=None):
            raise OSError("failed to discover services, device disconnected")

        cfg = Config(address="AA:BB", owner="", socket_path=str(tmp_path / "u.sock"))

        async def on_connect(transport, owner):
            pass

        t = _FakeTime()
        task = asyncio.ensure_future(ble._ble_link_loop(
            cfg, bridge, on_connect, connector=connect,
            disconnect=fake_disconnect, phantom_after=1,
            clock=t.clock, sleep=t.sleep, link_state=unpaired_state))
        try:
            for _ in range(400):
                await asyncio.sleep(0)
                if len(t.sleeps) >= 6:
                    break
            assert cleared == [], \
                "an unpaired device is not a phantom -- must not disconnect"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())
    out = capsys.readouterr().out
    assert "NOT paired" in out                 # says what is wrong
    assert "KeyboardOnly" in out               # ...and how to fix it


def test_unknown_link_state_still_clears(tmp_path):
    # If bluetoothctl is missing/unparseable, paired is None. The diagnostic is
    # strictly ADDITIVE: an unknown state must never make us less able to
    # self-heal than we are today, so the phantom-clear still fires.
    async def run():
        store = SessionStore()
        bridge = Bridge(store, NullTransport(), str(tmp_path / "k.sock"))
        cleared = []

        async def fake_disconnect(address):
            cleared.append(address)

        async def unknown_state(address):
            return {"powered": None, "pairable": None, "paired": None,
                    "bonded": None, "trusted": None}

        def connect(address, disconnected_callback=None):
            raise OSError("device not found")

        cfg = Config(address="AA:BB", owner="", socket_path=str(tmp_path / "k.sock"))

        async def on_connect(transport, owner):
            pass

        t = _FakeTime()
        task = asyncio.ensure_future(ble._ble_link_loop(
            cfg, bridge, on_connect, connector=connect,
            disconnect=fake_disconnect, phantom_after=1,
            clock=t.clock, sleep=t.sleep, link_state=unknown_state))
        try:
            for _ in range(400):
                await asyncio.sleep(0)
                if cleared:
                    break
            assert cleared == ["AA:BB"], \
                "unknown pairing state must fall back to today's behavior"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


def test_link_state_parses_bluetoothctl_output(monkeypatch):
    # _link_state scrapes bluetoothctl; verify the parse, not the subprocess.
    async def run():
        show = "\tPowered: yes\n\tPairable: no\n"
        info = "\tPaired: no\n\tBonded: no\n\tTrusted: yes\n"

        async def fake_run(*args, **kwargs):
            return show if "show" in args else info

        monkeypatch.setattr(ble, "_bluetoothctl_output", fake_run)
        st = await ble._link_state("AA:BB")
        assert st["powered"] is True
        assert st["pairable"] is False
        assert st["paired"] is False
        assert st["trusted"] is True

    asyncio.run(run())


def test_link_state_unknown_when_bluetoothctl_missing(monkeypatch):
    # No bluetoothctl -> every field None, never an exception.
    async def run():
        async def boom(*args, **kwargs):
            raise FileNotFoundError("bluetoothctl")

        monkeypatch.setattr(ble, "_bluetoothctl_output", boom)
        st = await ble._link_state("AA:BB")
        assert st == {"powered": None, "pairable": None, "paired": None,
                      "bonded": None, "trusted": None}

    asyncio.run(run())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && python -m pytest tests/test_ble.py -k "unpaired or unknown or link_state" -v`

Expected: FAIL — `module 'familiar.ble' has no attribute '_link_state'`.

- [ ] **Step 3: Implement `_bluetoothctl_output` and `_link_state`**

Add to `linux-bridge/src/familiar/ble.py`, below `_bluetoothctl_disconnect`:

```python
async def _bluetoothctl_output(*args) -> str:
    # Best-effort `bluetoothctl <args>` capture. Raises on a missing binary or
    # timeout; callers treat any failure as "state unknown".
    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    except Exception:
        if proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        raise
    return out.decode("utf-8", "replace")


def _yesno(text: str, field: str):
    # Scrape "\tField: yes" out of bluetoothctl output. None = couldn't tell.
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(field + ":"):
            value = line.split(":", 1)[1].strip().lower()
            if value == "yes":
                return True
            if value == "no":
                return False
    return None


async def _link_state(address) -> dict:
    # What BlueZ thinks of the adapter and the device. Every field is True,
    # False, or None (undetermined). NEVER raises: an undeterminable state must
    # leave the caller's behavior exactly as it was.
    state = {"powered": None, "pairable": None, "paired": None,
             "bonded": None, "trusted": None}
    try:
        show = await _bluetoothctl_output("show")
        state["powered"] = _yesno(show, "Powered")
        state["pairable"] = _yesno(show, "Pairable")
    except Exception:
        pass
    try:
        info = await _bluetoothctl_output("info", address)
        state["paired"] = _yesno(info, "Paired")
        state["bonded"] = _yesno(info, "Bonded")
        state["trusted"] = _yesno(info, "Trusted")
    except Exception:
        pass
    return state
```

- [ ] **Step 4: Wire the diagnostic into `_ble_link_loop`**

Two edits to `_ble_link_loop` in `linux-bridge/src/familiar/ble.py`.

First, the signature — add `link_state=_link_state` and bind it:

```python
async def _ble_link_loop(cfg, bridge, on_connect, connector=None,
                         disconnect=None, phantom_after=3,
                         clock=time.monotonic, sleep=asyncio.sleep,
                         link_state=None) -> None:
    connect = connector or BleakClient
    clear_phantom = disconnect or _bluetoothctl_disconnect
    probe_link = link_state or _link_state
    backoff = 1.0
    max_backoff = 30.0
    streak = _FailStreak(phantom_after)
    last_clear = float("-inf")
```

Second, replace the phantom-clear block inside `except Exception as e:` with a diagnose-
then-decide block:

```python
        except Exception as e:
            bridge.transport = NullTransport()  # ensure detached on any failure
            print(f"[familiar] disconnected: {e}")
            if address and streak.failure() \
                    and clock() - last_clear >= PHANTOM_MIN_INTERVAL:
                last_clear = clock()
                st = await probe_link(address)
                print(f"[familiar] repeated failures — adapter: "
                      f"powered={st['powered']} pairable={st['pairable']} | "
                      f"device {address}: paired={st['paired']} "
                      f"bonded={st['bonded']} trusted={st['trusted']}")
                if st["paired"] is False:
                    # NOT a phantom. bluetoothctl disconnect clears a stale LINK;
                    # it cannot restore stale KEYS. The firmware requires LE
                    # Secure Connections + MITM (src/ble_bridge.cpp), so pairing
                    # needs a 6-digit passkey typed by a human -- this daemon
                    # cannot repair it, and must say so instead of hammering.
                    print(f"[familiar] the M5 is NOT paired. Re-pair it in a "
                          f"terminal:\n"
                          f"    bluetoothctl\n"
                          f"    agent KeyboardOnly\n"
                          f"    default-agent\n"
                          f"    scan on          (wait for {address})\n"
                          f"    pair {address}   (type the code on the stick)\n"
                          f"    trust {address}")
                    print("[familiar] skipping the stale-link clear: an "
                          "unpaired device is not a phantom.")
                else:
                    # Paired (or undeterminable) -> a phantom is plausible, so
                    # clear it. Falling back to the clear on an unknown state
                    # keeps this strictly additive.
                    print(f"[familiar] clearing a possible stale link to {address}")
                    await clear_phantom(address)
```

- [ ] **Step 5: Run the full suite**

Run: `cd linux-bridge && python -m pytest tests/ -v`

Expected: PASS — 4 new tests on top of Task 1's 3 and the 7 pre-existing (expect 169).

- [ ] **Step 6: Commit**

```bash
git add linux-bridge/src/familiar/ble.py linux-bridge/tests/test_ble.py
git commit -m "fix: don't phantom-clear an unpaired M5; log the real problem

bluetoothctl disconnect clears a stale LINK, not stale KEYS. When the M5
loses its side of the bond, the clear cannot help -- it fired ~280 times
in one incident while bleak reported only 'failed to discover services'.
Probe the pairing state before clearing; if unpaired, skip the clear and
print the passkey re-pair steps. An undeterminable state falls back to
today's behavior."
```

---

### Task 3: Redeploy and verify on hardware

**Files:** none (deploy + observe)

- [ ] **Step 1: Reinstall the bridge**

The installed tool is a *copy*; editing the repo does not change what
`familiar.service` runs. A plain `--force` reuses a cached build of the same version, so
`--reinstall` is required:

```bash
uv tool install --force --reinstall ./linux-bridge
systemctl --user restart familiar.service
systemctl --user is-active familiar.service
```

Expected: `active`.

- [ ] **Step 2: Confirm a healthy link is unaffected**

```bash
journalctl --user -u familiar.service -n 5 --no-pager
```

Expected: `[familiar] connected F0:16:1D:03:4C:FA`, and the link stays up. Confirm the M5
leaves its "discover" screen and shows the pet.

- [ ] **Step 3: Verify the backoff engages when away**

Carry the laptop out of range of the M5 for ~3 minutes, then:

```bash
journalctl --user -u familiar.service --since "-3min" --no-pager
```

Expected: attempts decay (`1, 2, 4, 8, 16, 30, 30…`) rather than a continuous
connect/drop churn, with `link flapped after N.Ns; backing off` lines. **Roughly 2 attempts
per minute, not ~20.** The Bluetooth indicator should visibly settle.

- [ ] **Step 4: Verify reconnect on return**

Return to the desk. Expected: reconnects within ~30s and the log shows
`connected F0:16:1D:03:4C:FA` followed by no drops.

- [ ] **Step 5: Confirm the Tidbyt never froze**

The Tidbyt must have kept animating through the entire out-of-range period — that is the
PR #43 invariant, and the global constraint most easily broken by this change.

---

## Notes for the implementer

- **Do not "fix" the flap by shortening `HOLD_MIN`.** 30s is chosen because observed flaps
  live 2–7s and healthy links live minutes to hours; nothing sits near the boundary.
- **Do not add a retry inside `_ble_session`.** The loop is the only retry authority.
- **Do not make `_link_state` raise.** Every failure mode must degrade to `None`, which
  degrades to today's behavior.
