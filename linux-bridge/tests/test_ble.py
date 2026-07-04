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
