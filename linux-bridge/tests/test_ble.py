import asyncio
import os

from familiar import ble
from familiar.config import Config
from familiar.daemon import Bridge
from familiar.state import SessionStore
from familiar.transport import NullTransport


class _FakeClient:
    """Stands in for BleakClient as an async context manager."""
    def __init__(self, address, disconnected_callback=None):
        self.address = address
        self.cb = disconnected_callback

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def start_notify(self, *a):
        pass

    async def write_gatt_char(self, *a, **k):
        pass


def test_ble_session_swaps_transport_and_restores():
    # While the M5 is connected the bridge uses BleTransport; when the link
    # drops it must revert to NullTransport so the Tidbyt keeps being driven.
    async def run():
        store = SessionStore()
        bridge = Bridge(store, NullTransport(), "/tmp/unused_ble.sock")
        assert isinstance(bridge.transport, NullTransport)

        clients = []

        def connect(address, disconnected_callback=None):
            c = _FakeClient(address, disconnected_callback)
            clients.append(c)
            return c

        seen = {}

        async def on_connect(transport, owner):
            seen["transport"] = transport

        task = asyncio.ensure_future(
            ble._ble_session(bridge, on_connect, "owner", connect, "AA:BB"))

        # Let the session connect and attach its transport.
        for _ in range(100):
            await asyncio.sleep(0)
            if isinstance(bridge.transport, ble.BleTransport):
                break
        assert isinstance(bridge.transport, ble.BleTransport)  # attached while connected
        assert seen["transport"] is not None                   # on_connect was called

        # Same Bridge object throughout — not rebuilt per connection.
        clients[0].cb(None)                                    # fire disconnect
        await task
        assert isinstance(bridge.transport, NullTransport)     # restored on drop

    asyncio.run(run())


def test_run_with_ble_bridge_runs_when_ble_unavailable(tmp_path):
    # The Tidbyt-driving Bridge must run even if the M5 is never reachable.
    # Proof: the Bridge binds its Unix socket regardless of BLE state.
    async def run():
        sock = str(tmp_path / "familiar.sock")
        store = SessionStore()

        def connect(address, disconnected_callback=None):
            raise OSError("device not found")   # simulate M5 offline

        cfg = Config(address="AA:BB", owner="", socket_path=sock)

        async def on_connect(transport, owner):
            pass

        task = asyncio.ensure_future(
            ble.run_with_ble(cfg, store, on_connect, connector=connect))
        try:
            for _ in range(500):
                await asyncio.sleep(0)
                if os.path.exists(sock):
                    break
            assert os.path.exists(sock), \
                "Bridge (and its Tidbyt loop) must run even when the M5 is offline"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


def test_run_with_ble_survives_resolve_error(tmp_path, monkeypatch):
    # A scan/resolve failure must NOT propagate out and cancel the persistent
    # bridge — otherwise the decoupling is undone (Tidbyt refreezes).
    async def run():
        sock = str(tmp_path / "familiar.sock")
        store = SessionStore()
        calls = {"n": 0}

        async def boom_resolve(cfg):
            calls["n"] += 1
            raise RuntimeError("scanner exploded")

        monkeypatch.setattr(ble, "_resolve_address", boom_resolve)

        def connect(address, disconnected_callback=None):
            raise AssertionError("must not reach connect when resolve fails")

        cfg = Config(address=None, owner="", socket_path=sock)

        async def on_connect(transport, owner):
            pass

        task = asyncio.ensure_future(
            ble.run_with_ble(cfg, store, on_connect, connector=connect))
        try:
            for _ in range(500):
                await asyncio.sleep(0)
                if os.path.exists(sock) and calls["n"] >= 1:
                    break
            assert os.path.exists(sock)          # bridge still bound its socket
            assert calls["n"] >= 1               # resolve was attempted and threw
            assert not task.done()               # ...but it did NOT kill the daemon
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(run())


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


def test_ble_link_loop_clears_phantom_after_threshold(tmp_path):
    # After the failure threshold, the loop issues a best-effort disconnect for
    # the configured address (phantom_after=1 so the first failure triggers it).
    async def run():
        store = SessionStore()
        bridge = Bridge(store, NullTransport(), str(tmp_path / "s.sock"))
        cleared = []

        async def fake_disconnect(address):
            cleared.append(address)

        async def unknown_state(address):
            return {"powered": None, "pairable": None, "paired": None,
                    "bonded": None, "trusted": None}

        def connect(address, disconnected_callback=None):
            raise OSError("device not found")

        cfg = Config(address="AA:BB", owner="", socket_path=str(tmp_path / "s.sock"))

        async def on_connect(transport, owner):
            pass

        task = asyncio.ensure_future(ble._ble_link_loop(
            cfg, bridge, on_connect, connector=connect,
            disconnect=fake_disconnect, phantom_after=1, link_state=unknown_state))
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
        # A real yield to the event loop: no wall-clock delay (asyncio.sleep(0)
        # is a bare cooperative yield), but without it this coroutine has zero
        # suspension points and _ble_link_loop's `while True` never returns
        # control -- hanging the test forever instead of letting the polling
        # loop below observe progress and cancel the task.
        await asyncio.sleep(0)


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

        async def unknown_state(address):
            return {"powered": None, "pairable": None, "paired": None,
                    "bonded": None, "trusted": None}

        def connect(address, disconnected_callback=None):
            raise OSError("device not found")

        cfg = Config(address="AA:BB", owner="", socket_path=str(tmp_path / "r.sock"))

        async def on_connect(transport, owner):
            pass

        task = asyncio.ensure_future(ble._ble_link_loop(
            cfg, bridge, on_connect, connector=connect,
            disconnect=fake_disconnect, phantom_after=3,
            clock=t.clock, sleep=t.sleep, link_state=unknown_state))
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


def test_default_link_state_binding_is_used(tmp_path, monkeypatch):
    # probe_link = link_state or _link_state only resolves to the module-level
    # _link_state when no link_state= kwarg is passed. Every other test in this
    # file injects a stub, so that default-binding line has zero coverage
    # without this test -- a typo in it would ship silently.
    async def run():
        store = SessionStore()
        bridge = Bridge(store, NullTransport(), str(tmp_path / "d.sock"))
        cleared = []
        calls = {"n": 0}

        async def recording_link_state(address):
            calls["n"] += 1
            return {"powered": None, "pairable": None, "paired": None,
                    "bonded": None, "trusted": None}

        monkeypatch.setattr(ble, "_link_state", recording_link_state)

        async def fake_disconnect(address):
            cleared.append(address)

        def connect(address, disconnected_callback=None):
            raise OSError("device not found")

        cfg = Config(address="AA:BB", owner="", socket_path=str(tmp_path / "d.sock"))

        async def on_connect(transport, owner):
            pass

        t = _FakeTime()
        task = asyncio.ensure_future(ble._ble_link_loop(
            cfg, bridge, on_connect, connector=connect,
            disconnect=fake_disconnect, phantom_after=1,
            clock=t.clock, sleep=t.sleep))
        try:
            for _ in range(400):
                await asyncio.sleep(0)
                if cleared:
                    break
            assert calls["n"] >= 1, \
                "the default probe_link must resolve to the monkeypatched _link_state"
            assert cleared == ["AA:BB"], \
                "all-None state must fall back to today's behavior and clear"
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
