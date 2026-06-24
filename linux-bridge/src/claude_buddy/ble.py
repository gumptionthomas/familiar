import asyncio

from bleak import BleakClient, BleakScanner

from .daemon import Bridge

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


async def run_with_ble(cfg, store, on_connect) -> None:
    backoff = 1.0
    while True:
        address = await _resolve_address(cfg)
        if not address:
            print("[claude-buddy] no Claude- device found; is it awake? "
                  "have you paired with bluetoothctl?")
            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)
            continue
        try:
            async with BleakClient(address) as client:
                print(f"[claude-buddy] connected {address}")
                backoff = 1.0
                transport = BleTransport(client)
                # TX notify is encrypted-only; subscribing forces the
                # encrypted link up (and lets the device send acks).
                try:
                    await client.start_notify(NUS_TX, lambda _c, _d: None)
                except Exception:
                    pass
                await on_connect(transport, cfg.owner)
                bridge = Bridge(store, transport, cfg.socket_path)
                await bridge.run()
        except Exception as e:
            print(f"[claude-buddy] disconnected: {e}")
            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)
