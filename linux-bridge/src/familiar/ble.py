import asyncio
import time

from bleak import BleakClient, BleakScanner

from .daemon import Bridge
from .transport import NullTransport

NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write to device
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notify from device
NAME_PREFIX = "Claude-"
CHUNK = 180
HOLD_MIN = 30.0              # a link must survive this long to count as a success
PHANTOM_MIN_INTERVAL = 300.0  # floor between bluetoothctl disconnect calls


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


async def _bluetoothctl_disconnect(address) -> None:
    # Clear a stale BlueZ link ("phantom") that leaves the device 'connected' at
    # the OS level while bleak can't reach it. Mirrors the manual
    # `bluetoothctl disconnect <MAC>` remedy. Best-effort: any failure (missing
    # binary, non-zero exit, timeout) is swallowed — we retry the connect either
    # way.
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "disconnect", address,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(), timeout=10)
    except Exception:
        # On timeout (or any error) don't leave a wedged child lingering.
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass


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
        await sleep(min(backoff, max_backoff))
        backoff = min(backoff * 2, max_backoff)


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
