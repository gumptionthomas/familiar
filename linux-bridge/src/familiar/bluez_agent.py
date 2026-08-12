"""BlueZ over D-Bus: a KeyboardOnly pairing agent and the operations
`repair.run_repair` needs.

Everything here is I/O against org.bluez and is deliberately thin -- the
decisions live in repair.py, which is where the tests are. `dbus-fast` is
the same D-Bus library bleak already uses on Linux.
"""
import asyncio

from dbus_fast import BusType
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method

BLUEZ = "org.bluez"
AGENT_PATH = "/org/gumptionthomas/familiar/agent"
NAME_PREFIX = "Claude-"


def device_path(adapter_path: str, address: str) -> str:
    """/org/bluez/hci0 + F0:16:1D:03:4C:FA -> .../dev_F0_16_1D_03_4C_FA"""
    return f"{adapter_path}/dev_" + address.upper().replace(":", "_")


async def connect_system_bus():
    return await MessageBus(bus_type=BusType.SYSTEM).connect()


class _Agent(ServiceInterface):
    """BlueZ calls RequestPasskey on us because we register as KeyboardOnly.

    The firmware is DisplayOnly, so it shows the number and we type it.
    """

    def __init__(self, supply):
        super().__init__("org.bluez.Agent1")
        self._supply = supply

    @method()
    def RequestPasskey(self, device: 'o') -> 'u':
        return int(self._supply())

    @method()
    def AuthorizeService(self, device: 'o', uuid: 's'):
        return

    @method()
    def RequestAuthorization(self, device: 'o'):
        return

    @method()
    def Cancel(self):
        return

    @method()
    def Release(self):
        return


class Bluez:
    def __init__(self, bus, adapter_path="/org/bluez/hci0"):
        self._bus = bus
        self._adapter_path = adapter_path
        self._supply = None
        self._agent = _Agent(lambda: self._supply())

    async def _iface(self, path, name):
        intro = await self._bus.introspect(BLUEZ, path)
        return self._bus.get_proxy_object(BLUEZ, path, intro).get_interface(name)

    async def register_agent(self):
        self._bus.export(AGENT_PATH, self._agent)
        mgr = await self._iface("/org/bluez", "org.bluez.AgentManager1")
        await mgr.call_register_agent(AGENT_PATH, "KeyboardOnly")
        await mgr.call_request_default_agent(AGENT_PATH)

    async def ensure_pairable(self):
        adapter = await self._iface(self._adapter_path, "org.bluez.Adapter1")
        await adapter.set_powered(True)
        await adapter.set_pairable(True)

    async def _match(self, address):
        intro = await self._bus.introspect(BLUEZ, "/")
        om = self._bus.get_proxy_object(BLUEZ, "/", intro).get_interface(
            "org.freedesktop.DBus.ObjectManager")
        for path, ifaces in (await om.call_get_managed_objects()).items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev:
                continue
            addr = dev["Address"].value if "Address" in dev else ""
            name = dev["Name"].value if "Name" in dev else ""
            if address and addr.upper() == address.upper():
                return path
            if not address and name.startswith(NAME_PREFIX):
                return path
        return None

    async def find_device(self, address, timeout):
        adapter = await self._iface(self._adapter_path, "org.bluez.Adapter1")
        # A device we already know is visible without scanning; only pay for
        # discovery if it is not.
        found = await self._match(address)
        if found:
            return found
        try:
            await adapter.call_start_discovery()
        except Exception:
            pass    # already discovering is fine
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                found = await self._match(address)
                if found:
                    return found
                await asyncio.sleep(1.0)
            return None
        finally:
            try:
                await adapter.call_stop_discovery()
            except Exception:
                pass

    async def remove_device(self, path):
        adapter = await self._iface(self._adapter_path, "org.bluez.Adapter1")
        await adapter.call_remove_device(path)

    async def disconnect(self, path):
        dev = await self._iface(path, "org.bluez.Device1")
        try:
            await dev.call_disconnect()
        except Exception:
            pass    # "not connected" is the outcome we wanted anyway

    async def pair(self, path, supply_passkey):
        self._supply = supply_passkey
        dev = await self._iface(path, "org.bluez.Device1")
        await dev.call_pair()

    async def set_trusted(self, path):
        dev = await self._iface(path, "org.bluez.Device1")
        await dev.set_trusted(True)
