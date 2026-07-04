import asyncio

from bleak import BleakClient, BleakScanner

from .daemon import Bridge
from .transport import NullTransport

NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write to device
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notify from device
NAME_PREFIX = "Claude-"
CHUNK = 180


class BleTransport:
    def __init__(self, client: BleakClient, rx_uuid: str = NUS_RX):
        self._client = client
        self._rx = rx_uuid

    async def send(self, data: bytes) -> None:
        for i in range(0, len(data), CHUNK):
            await self._client.write_gatt_char(
                self._rx, data[i:i + CHUNK], response=False)
            await asyncio.sleep(0.01)


async def _resolve_address(cfg) -> str | None:
    if cfg.address:
        return cfg.address
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or "").startswith(NAME_PREFIX), timeout=10.0)
    return dev.address if dev else None


async def _ble_session(bridge, on_connect, owner, connect, address) -> None:
    # One connect -> serve -> disconnect cycle. Attaches a live BleTransport to
    # the already-running bridge for the duration of the link, then restores
    # NullTransport so the bridge's other loops (Tidbyt, haiku, sweep) keep
    # running once the M5 goes away. The bridge itself is never torn down here.
    disconnected = asyncio.Event()
    async with connect(
        address,
        disconnected_callback=lambda _c: disconnected.set(),
    ) as client:
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


async def _ble_link_loop(cfg, bridge, on_connect, connector=None) -> None:
    connect = connector or BleakClient
    backoff = 1.0
    while True:
        address = await _resolve_address(cfg)
        if not address:
            print("[familiar] no Claude- device found; is it awake? "
                  "have you paired with bluetoothctl?")
            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)
            continue
        try:
            await _ble_session(bridge, on_connect, cfg.owner, connect, address)
            print(f"[familiar] link dropped; reconnecting {address}")
            backoff = 1.0                      # clean drop -> retry promptly
        except Exception as e:
            bridge.transport = NullTransport()  # ensure detached on any failure
            print(f"[familiar] disconnected: {e}")
            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)


async def run_with_ble(cfg, store, on_connect, compose=None, tidbyt=None,
                       connector=None) -> None:
    # The Bridge (socket server + Tidbyt/haiku/sweep loops) runs persistently,
    # independent of the M5 link. The BLE layer only attaches/detaches the
    # transport, so an offline or flapping M5 never freezes the Tidbyt.
    bridge = Bridge(store, NullTransport(), cfg.socket_path,
                    compose=compose, tidbyt=tidbyt)
    bridge_task = asyncio.ensure_future(bridge.run())
    link_task = asyncio.ensure_future(
        _ble_link_loop(cfg, bridge, on_connect, connector))
    try:
        # Neither task returns normally; if either crashes, re-raise it.
        done, _ = await asyncio.wait(
            {bridge_task, link_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            t.result()
    finally:
        for t in (bridge_task, link_task):
            t.cancel()
        await asyncio.gather(bridge_task, link_task, return_exceptions=True)
