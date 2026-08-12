# `familiar repair` + Discharge Measurement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-typed `bluetoothctl` re-pair with `familiar repair`, and measure the M5's discharge curve so `BatteryGuard`'s thresholds come from data.

**Architecture:** `repair.py` holds the sequence as pure policy with every side effect injected (three small protocols: a BlueZ backend, a passkey prompt, and service control), exactly as `doctor.py` splits I/O-bound `collect()` from pure `diagnose()`. The real D-Bus work lives in `bluez_agent.py` behind that protocol, so the sequence is fully unit-testable without a Bluetooth adapter. `tools/measure_discharge.py` is a standalone throwaway harness.

**Tech Stack:** Python 3.11+, `dbus-fast` (BlueZ D-Bus), `bleak` (measurement only), pytest.

## Global Constraints

- **Scope is Phase 1 + Phase 2 of the spec only.** Phase 3 (`BatteryGuard`) is gated on Phase 2's measured curve and gets its own plan. Do not invent thresholds.
- **No firmware security changes.** No static passkey, no Just Works. The human types six digits.
- **`doctor` stays diagnose-only.** It may gain a pointer line; it must never mutate state.
- **Declare `dbus-fast` explicitly** in `linux-bridge/pyproject.toml` dependencies. It is already installed as a `bleak` transitive dependency, so this installs nothing new — but importing a transitive dep without declaring it is fragile.
- **`tools/` is never shipped.** `pyproject.toml` packages only `src/familiar`. Keep it that way.
- Run tests from `linux-bridge/`: `uv run pytest`.
- Device MAC used in examples: `F0:16:1D:03:4C:FA`. Device name prefix: `Claude-`.

---

## File Structure

| File | Responsibility |
|---|---|
| `linux-bridge/src/familiar/repair.py` (create) | The repair *sequence* as pure policy + `main(argv)`. No D-Bus, no subprocess. |
| `linux-bridge/src/familiar/bluez_agent.py` (create) | The D-Bus implementation: KeyboardOnly agent, discovery, pair/trust/remove. |
| `linux-bridge/src/familiar/service_ctl.py` (create) | `systemctl --user` stop/start + journal wait-for-connect. |
| `linux-bridge/tests/test_repair.py` (create) | Sequence tests against fakes. The bulk of the coverage. |
| `linux-bridge/tests/test_bluez_agent.py` (create) | Tests for the pure helpers (path building). |
| `linux-bridge/src/familiar/cli.py` (modify) | Register the `repair` subcommand + help text. |
| `linux-bridge/src/familiar/doctor.py` (modify:115-135) | Lead `_REPAIR` with `familiar repair`. |
| `linux-bridge/pyproject.toml` (modify) | Declare `dbus-fast`. |
| `tools/measure_discharge.py` (create) | Throwaway discharge-curve harness. |
| `linux-bridge/README.md` (modify) | Document `familiar repair`. |

---

### Task 1: The repair sequence (pure policy)

**Files:**
- Create: `linux-bridge/src/familiar/repair.py`
- Test: `linux-bridge/tests/test_repair.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `repair.RepairReport(ok: bool, steps: list[tuple[str, str]], message: str)`
  - `async repair.run_repair(address, bluez, ui, service, *, discover_timeout=30.0, connect_timeout=30.0) -> RepairReport`
  - Injected protocol — **BlueZ backend** (all async): `ensure_pairable()`, `find_device(address: str | None, timeout: float) -> str | None` (returns a D-Bus object path), `remove_device(path)`, `disconnect(path)`, `pair(path, supply_passkey: Callable[[], str])`, `set_trusted(path)`
  - Injected protocol — **ui**: `ask_passkey() -> str`, `info(msg: str)`
  - Injected protocol — **service**: `stop()`, `start()`, `async wait_for_connect(timeout: float) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `linux-bridge/tests/test_repair.py`:

```python
import asyncio

import pytest

from familiar import repair


class FakeBluez:
    """Records the sequence. Every method is async, like the real backend."""

    # NOTE: `found` is consumed one entry PER find_device call, and the
    # sequence calls it twice -- once to spot a stale record, once after
    # removing it. So the healthy default needs the path twice.
    def __init__(self, found=("/org/bluez/hci0/dev_F0_16_1D_03_4C_FA",) * 2,
                 pair_error=None):
        self.calls = []
        self._found = list(found)
        self._pair_error = pair_error
        self.passkey_used = None

    async def ensure_pairable(self):
        self.calls.append(("ensure_pairable", None))

    async def find_device(self, address, timeout):
        self.calls.append(("find_device", address))
        return self._found.pop(0) if self._found else None

    async def remove_device(self, path):
        self.calls.append(("remove_device", path))

    async def disconnect(self, path):
        self.calls.append(("disconnect", path))

    async def pair(self, path, supply_passkey):
        self.calls.append(("pair", path))
        if self._pair_error:
            raise RuntimeError(self._pair_error)
        self.passkey_used = supply_passkey()

    async def set_trusted(self, path):
        self.calls.append(("set_trusted", path))


class FakeUi:
    def __init__(self, passkey="123456"):
        self._passkey = passkey
        self.messages = []

    def ask_passkey(self):
        return self._passkey

    def info(self, msg):
        self.messages.append(msg)


class FakeService:
    def __init__(self, connects=True):
        self.calls = []
        self._connects = connects

    def stop(self):
        self.calls.append("stop")

    def start(self):
        self.calls.append("start")

    async def wait_for_connect(self, timeout):
        self.calls.append("wait")
        return self._connects


def _run(**over):
    bluez = over.get("bluez") or FakeBluez()
    ui = over.get("ui") or FakeUi()
    service = over.get("service") or FakeService()
    report = asyncio.run(repair.run_repair(
        "F0:16:1D:03:4C:FA", bluez, ui, service))
    return report, bluez, ui, service


def _names(bluez):
    return [name for name, _ in bluez.calls]


def test_a_successful_repair_reports_ok():
    report, _, _, service = _run()
    assert report.ok is True
    assert service.calls == ["stop", "start", "wait"]


def test_the_hosts_stale_keys_are_removed_before_pairing():
    # In a one-sided bond BlueZ still believes it is paired, so Device1.Pair()
    # fails with AlreadyExists. The stale record MUST go first.
    _, bluez, _, _ = _run()
    names = _names(bluez)
    assert names.index("remove_device") < names.index("pair")


def test_the_adapter_is_made_pairable_before_pairing():
    # `pairable on` is the step everyone misses; without it pairing can never
    # succeed and the user loops forever.
    _, bluez, _, _ = _run()
    names = _names(bluez)
    assert names.index("ensure_pairable") < names.index("pair")


def test_the_pairing_link_is_cleared_after_trusting():
    # THE 2026-08-11 trap: the pairing session leaves a phantom link, a
    # connected peripheral stops advertising, and the daemon then logs
    # "was not found" -- making a SUCCESSFUL repair look like a failure.
    _, bluez, _, _ = _run()
    names = _names(bluez)
    assert names.index("disconnect") > names.index("set_trusted")


def test_the_typed_passkey_reaches_the_backend():
    _, bluez, _, _ = _run(ui=FakeUi(passkey="654321"))
    assert bluez.passkey_used == "654321"


def test_a_stick_that_never_appears_aborts_without_pairing():
    bluez = FakeBluez(found=(None, None))
    report, bluez, _, service = _run(bluez=bluez)
    assert report.ok is False
    assert "pair" not in _names(bluez)
    assert "charged" in report.message


def test_a_failed_pairing_still_restarts_the_daemon():
    # Leaving the daemon stopped would be worse than the fault we came to fix.
    bluez = FakeBluez(pair_error="org.bluez.Error.AuthenticationFailed")
    report, _, _, service = _run(bluez=bluez)
    assert report.ok is False
    assert "start" in service.calls


def test_a_stick_that_never_appears_still_restarts_the_daemon():
    report, _, _, service = _run(bluez=FakeBluez(found=(None, None)))
    assert "start" in service.calls


def test_pairing_that_does_not_lead_to_a_connect_is_not_called_ok():
    report, _, _, _ = _run(service=FakeService(connects=False))
    assert report.ok is False
    assert "doctor" in report.message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_repair.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'familiar.repair'`

- [ ] **Step 3: Write the minimal implementation**

Create `linux-bridge/src/familiar/repair.py`:

```python
"""`familiar repair` -- re-pair the M5 after it loses its side of the bond.

Split like doctor.py: the SEQUENCE below is pure policy with every side
effect injected, so it is testable without a Bluetooth adapter. The D-Bus
work lives in bluez_agent.py; systemctl/journal in service_ctl.py.

Why a human is unavoidable: the firmware advertises DisplayOnly IO with
ESP_LE_AUTH_REQ_SC_MITM_BOND (ble_bridge.cpp:121-122), so the ESP32 picks a
RANDOM 6-digit passkey per pairing. Nothing here can know it in advance.
"""
from dataclasses import dataclass, field


@dataclass
class RepairReport:
    ok: bool = False
    steps: list = field(default_factory=list)   # (label, "ok")
    message: str = ""


class Aborted(Exception):
    """A step failed. Stop, but ALWAYS restart the daemon on the way out."""


async def run_repair(address, bluez, ui, service, *,
                     discover_timeout=30.0, connect_timeout=30.0):
    report = RepairReport()

    def done(label):
        report.steps.append((label, "ok"))
        ui.info(f"  {label}")

    service.stop()
    done("stopped the daemon")
    try:
        # Without this, BlueZ answers every pairing attempt with "Pairing not
        # supported" and no amount of retrying can succeed.
        await bluez.ensure_pairable()
        done("made the adapter pairable")

        # BlueZ still holds keys for a one-sided bond, and Device1.Pair() on an
        # already-known device fails with AlreadyExists. Drop OUR half first.
        stale = await bluez.find_device(address, timeout=5.0)
        if stale is not None:
            await bluez.remove_device(stale)
            done("dropped the stale pairing record")

        target = await bluez.find_device(address, timeout=discover_timeout)
        if target is None:
            raise Aborted("the stick never appeared — press a button on it, "
                          "check it is charged, and that Bluetooth is on in "
                          "its settings menu")
        done("found the stick")

        await bluez.pair(target, ui.ask_passkey)
        done("paired")

        await bluez.set_trusted(target)
        done("trusted")

        # The pairing session leaves a link behind, and a connected peripheral
        # stops advertising -- so the daemon would log "was not found" and the
        # repair would look like it had failed (2026-08-11).
        await bluez.disconnect(target)
        done("cleared the pairing link")
    except Exception as e:
        report.message = str(e) if isinstance(e, Aborted) else \
            f"{type(e).__name__}: {e}"
        return report
    finally:
        service.start()
        report.steps.append(("restarted the daemon", "ok"))

    if await service.wait_for_connect(connect_timeout):
        report.ok = True
        report.message = "the buddy is back"
    else:
        report.message = (
            f"paired, but the daemon did not connect within "
            f"{int(connect_timeout)}s — run `familiar doctor`")
    return report
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd linux-bridge && uv run pytest tests/test_repair.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Run the whole suite**

Run: `cd linux-bridge && uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add linux-bridge/src/familiar/repair.py linux-bridge/tests/test_repair.py
git commit -m "feat(repair): the re-pair sequence, as testable policy"
```

---

### Task 2: The BlueZ D-Bus backend

**Files:**
- Create: `linux-bridge/src/familiar/bluez_agent.py`
- Create: `linux-bridge/tests/test_bluez_agent.py`
- Modify: `linux-bridge/pyproject.toml`

**Interfaces:**
- Consumes: the backend protocol defined in Task 1.
- Produces: `bluez_agent.device_path(adapter_path, address) -> str`, `async bluez_agent.connect_system_bus()`, `bluez_agent.Bluez(bus, adapter_path="/org/bluez/hci0")` implementing Task 1's backend protocol, plus `async Bluez.register_agent()`.

- [ ] **Step 1: Write the failing test**

Create `linux-bridge/tests/test_bluez_agent.py`:

```python
from familiar import bluez_agent


def test_device_path_is_built_the_way_bluez_names_objects():
    assert bluez_agent.device_path("/org/bluez/hci0", "F0:16:1D:03:4C:FA") == \
        "/org/bluez/hci0/dev_F0_16_1D_03_4C_FA"


def test_device_path_upcases_a_lowercase_mac():
    # bluetoothctl prints lowercase in some places; BlueZ object paths are upper.
    assert bluez_agent.device_path("/org/bluez/hci0", "f0:16:1d:03:4c:fa") == \
        "/org/bluez/hci0/dev_F0_16_1D_03_4C_FA"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd linux-bridge && uv run pytest tests/test_bluez_agent.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'familiar.bluez_agent'`

- [ ] **Step 3: Write the implementation**

Create `linux-bridge/src/familiar/bluez_agent.py`:

```python
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
```

- [ ] **Step 4: Declare the dependency**

In `linux-bridge/pyproject.toml`, change:

```toml
dependencies = ["bleak>=0.22", "pillow>=10"]
```

to:

```toml
dependencies = ["bleak>=0.22", "pillow>=10", "dbus-fast>=2.0"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd linux-bridge && uv run pytest tests/test_bluez_agent.py -q`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add linux-bridge/src/familiar/bluez_agent.py linux-bridge/tests/test_bluez_agent.py linux-bridge/pyproject.toml
git commit -m "feat(repair): a KeyboardOnly BlueZ agent over D-Bus"
```

---

### Task 3: Service control

**Files:**
- Create: `linux-bridge/src/familiar/service_ctl.py`
- Modify: `linux-bridge/tests/test_repair.py` (append)

**Interfaces:**
- Consumes: the service protocol from Task 1.
- Produces: `service_ctl.Service(unit="familiar")` with `stop()`, `start()`, `async wait_for_connect(timeout)`, and the pure helper `service_ctl.saw_connect(journal_text) -> bool`.

- [ ] **Step 1: Write the failing test**

Append to `linux-bridge/tests/test_repair.py`:

```python
from familiar import service_ctl


def test_saw_connect_recognises_the_daemons_connect_line():
    assert service_ctl.saw_connect(
        "Aug 11 22:18:31 calvin familiar[12941]: "
        "[familiar] connected F0:16:1D:03:4C:FA\n") is True


def test_saw_connect_is_not_fooled_by_a_disconnect():
    # "disconnected:" contains "connected" as a substring. The daemon logs
    # far more disconnects than connects, so this would invert the result.
    assert service_ctl.saw_connect(
        "[familiar] disconnected: failed to discover services\n") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_repair.py -q -k saw_connect`
Expected: FAIL — `ModuleNotFoundError: No module named 'familiar.service_ctl'`

- [ ] **Step 3: Write the implementation**

Create `linux-bridge/src/familiar/service_ctl.py`:

```python
"""systemctl --user + journal reads for `familiar repair`.

Kept apart from repair.py so the sequence stays pure and testable.
"""
import asyncio
import subprocess


def saw_connect(journal_text: str) -> bool:
    """True if the daemon logged a SUCCESSFUL connect.

    Matches the trailing space in "connected <MAC>" -- doctor.py does the
    same. Without it "disconnected:" matches as a substring and every
    failure reads as a success.
    """
    return "[familiar] connected " in journal_text


class Service:
    def __init__(self, unit="familiar"):
        self._unit = unit

    def _systemctl(self, verb):
        subprocess.run(["systemctl", "--user", verb, self._unit],
                       capture_output=True, text=True, timeout=30, check=False)

    def stop(self):
        self._systemctl("stop")

    def start(self):
        self._systemctl("start")

    async def wait_for_connect(self, timeout):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            out = subprocess.run(
                ["journalctl", "--user", "-u", f"{self._unit}.service",
                 "--since", "-2min", "--no-pager"],
                capture_output=True, text=True, timeout=10, check=False).stdout
            if saw_connect(out):
                return True
            await asyncio.sleep(2.0)
        return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd linux-bridge && uv run pytest tests/test_repair.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add linux-bridge/src/familiar/service_ctl.py linux-bridge/tests/test_repair.py
git commit -m "feat(repair): systemctl + journal verification"
```

---

### Task 4: Wire up `familiar repair` and point doctor at it

**Files:**
- Modify: `linux-bridge/src/familiar/repair.py` (append `main`)
- Modify: `linux-bridge/src/familiar/cli.py:6-14,32-33`
- Modify: `linux-bridge/src/familiar/doctor.py:115-130`
- Modify: `linux-bridge/tests/test_repair.py` (append)
- Modify: `linux-bridge/README.md:135-163`

**Interfaces:**
- Consumes: `repair.run_repair` (Task 1), `bluez_agent.Bluez` / `connect_system_bus` (Task 2), `service_ctl.Service` (Task 3).
- Produces: `repair.main(argv) -> int`, `familiar repair` on the CLI.

- [ ] **Step 1: Write the failing test**

Append to `linux-bridge/tests/test_repair.py`:

```python
from familiar import doctor


def test_doctor_points_at_the_repair_command():
    steps = doctor._repair_steps("F0:16:1D:03:4C:FA")
    assert any("familiar repair" in line for line in steps)


def test_doctor_still_prints_the_manual_fallback():
    # The command can fail (no D-Bus, an adapter that will not power on). The
    # hand steps must remain, or a failed repair leaves the user with nothing.
    joined = "\n".join(doctor._repair_steps("F0:16:1D:03:4C:FA"))
    assert "KeyboardOnly" in joined
    assert "pairable on" in joined


def test_the_cli_knows_the_repair_command():
    from familiar import cli
    assert "repair" in cli._HELP
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_repair.py -q -k "doctor_points or cli_knows"`
Expected: FAIL — `assert any(...)` is False; `"repair" in cli._HELP` is False.

- [ ] **Step 3: Add `main` to `repair.py`**

Append to `linux-bridge/src/familiar/repair.py`:

```python
class _Console:
    def ask_passkey(self):
        return input("      passkey shown ON THE STICK: ").strip()

    def info(self, msg):
        print(msg)


def main(argv=None) -> int:
    import argparse
    import asyncio

    from .bluez_agent import Bluez, connect_system_bus
    from .config import load as load_config   # doctor.py aliases it the same way
    from .service_ctl import Service

    ap = argparse.ArgumentParser(
        prog="familiar repair",
        description="Re-pair the M5 after it loses its side of the bond. "
                    "Stops the daemon, re-pairs, restarts, and verifies.")
    args = ap.parse_args(argv)

    cfg = load_config()
    print("familiar repair — the stick will show a 6-digit code\n")

    async def go():
        bus = await connect_system_bus()
        bluez = Bluez(bus)
        await bluez.register_agent()
        return await run_repair(cfg.address, bluez, _Console(), Service())

    try:
        report = asyncio.run(go())
    except Exception as e:
        print(f"\n!!  repair could not run: {type(e).__name__}: {e}\n")
        report = None

    if report is not None and report.ok:
        print(f"\nOK  {report.message}")
        return 0

    if report is not None:
        print(f"\n!!  {report.message}")
    print("\n    the manual steps, if you need them:\n")
    from .doctor import _repair_steps
    for line in _repair_steps(cfg.address):
        print(f"      {line}")
    return 1
```

- [ ] **Step 4: Register the subcommand in `cli.py`**

In `linux-bridge/src/familiar/cli.py`, add `repair` to the import on line 4:

```python
from . import archive, daemon, doctor, hook, init, repair
```

Add to `_HELP` after the `doctor` line:

```
  familiar repair              re-pair the M5 after it loses its bond
```

Add the dispatch after the `doctor` branch:

```python
    if cmd == "repair":
        return repair.main(rest)
```

- [ ] **Step 5: Lead doctor's remedy with the command**

In `linux-bridge/src/familiar/doctor.py`, change `_REPAIR` (line 115) to:

```python
_REPAIR = [
    "familiar repair          # does everything below, for you",
    "",
    "# or by hand:",
    "systemctl --user stop familiar",
    "bluetoothctl",
    "  pairable on          # without this, pairing can NEVER succeed",
    "  agent KeyboardOnly   # the firmware needs a 6-digit passkey typed",
    "  default-agent",
    "  scan on              # wait for Claude-XXXX to appear",
    "  scan off",
    "  pair {addr}          # type the code shown ON THE STICK",
    "  trust {addr}",
    "  quit",
    "systemctl --user start familiar",
    "",
    "(It must be ONE interactive bluetoothctl session: the one-shot form tears",
    " down discovery between invocations, so a later `pair` says 'not available'.)",
]
```

- [ ] **Step 6: Run the full suite**

Run: `cd linux-bridge && uv run pytest -q`
Expected: PASS. `doctor` keeps its read-only guarantee — nothing in this task calls into `repair` from `doctor`.

- [ ] **Step 7: Document it in the README**

In `linux-bridge/README.md`, under "Something's wrong?" (line 135), after the `familiar doctor` block, add:

```markdown
Doctor never changes anything. When it names a one-sided bond, this fixes it:

```bash
familiar repair
```

It stops the daemon, drops the stale pairing record, re-pairs (you type the
6-digit code the stick displays), clears the link the pairing session leaves
behind, restarts the daemon, and waits for it to reconnect. The firmware picks
a random passkey per pairing, so the six digits are the one part that cannot be
automated.
```

- [ ] **Step 8: Commit**

```bash
git add linux-bridge/src/familiar/repair.py linux-bridge/src/familiar/cli.py linux-bridge/src/familiar/doctor.py linux-bridge/tests/test_repair.py linux-bridge/README.md
git commit -m "feat: familiar repair — one command instead of the bluetoothctl dance"
```

- [ ] **Step 9: Verify against the real stick**

This is the only end-to-end proof; the tests cannot cover D-Bus.

```bash
familiar repair
```

Expected: it prompts for the passkey the stick displays, then prints `OK the buddy is back`, and `familiar doctor` exits 0.

If the bond is currently healthy there is nothing to repair — `repair` will re-pair anyway, which is a safe way to exercise the path.

---

### Task 5: The discharge measurement harness

**Files:**
- Create: `tools/measure_discharge.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (standalone).
- Produces: a CSV the `BatteryGuard` plan will read. No importable API.

- [ ] **Step 1: Write the harness**

There is no test for this — it is a throwaway harness whose output a human reads, and it cannot run without a real stick. Create `tools/measure_discharge.py`:

```python
#!/usr/bin/env python3
"""Log the M5's battery discharge curve to CSV until the stick dies.

Phase 2 of docs/superpowers/specs/2026-08-11-repair-and-battery-guard-design.md:
BatteryGuard's thresholds must come from THIS data, not from a guess.

    systemctl --user stop familiar          # only one client can hold the link
    uv run --with bleak python tools/measure_discharge.py \
        --address F0:16:1D:03:4C:FA --out discharge.csv

Start from a FULL charge, then UNPLUG. Plugging in charges the stick and
destroys the measurement -- which is also why serial is not an option here.
Expect 1-2 hours on the bare internal cell.

When it ends, check whether the bond survived:

    bluetoothctl info F0:16:1D:03:4C:FA     # then: familiar doctor

That answer is the point of the exercise. A lost bond confirms the brownout
theory; a surviving bond kills it, and BatteryGuard would have fixed nothing.
"""
import argparse
import asyncio
import csv
import json
import sys
import time

from bleak import BleakClient

NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # write to device
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # notify from device


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--out", default="discharge.csv")
    ap.add_argument("--interval", type=float, default=60.0)
    args = ap.parse_args()

    rows = asyncio.Queue()

    def on_notify(_c, data):
        for line in data.decode("utf-8", "replace").splitlines():
            if not line.strip().startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("ack") == "status":
                rows.put_nowait(msg.get("data", {}).get("bat", {}))

    with open(args.out, "w", newline="") as fh:
        out = csv.writer(fh)
        out.writerow(["iso_time", "mV", "mA", "usb", "pct"])
        fh.flush()

        async with BleakClient(args.address) as client:
            await client.start_notify(NUS_TX, on_notify)
            print(f"connected; logging to {args.out} every {args.interval}s")
            print("UNPLUG THE STICK NOW if it is still on USB.")
            while True:
                await client.write_gatt_char(
                    NUS_RX, b'{"cmd":"status"}\n', response=False)
                try:
                    bat = await asyncio.wait_for(rows.get(), timeout=15)
                except asyncio.TimeoutError:
                    print("no status reply — the stick may have died")
                    break
                stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
                out.writerow([stamp, bat.get("mV"), bat.get("mA"),
                              bat.get("usb"), bat.get("pct")])
                fh.flush()      # the run ends with a power cut; never buffer
                print(f"{stamp}  {bat.get('mV')} mV  {bat.get('mA')} mA  "
                      f"usb={bat.get('usb')}")
                await asyncio.sleep(args.interval)

    print(f"\nlink ended. curve saved to {args.out}")
    print("now check the bond:  bluetoothctl info <MAC> && familiar doctor")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
```

- [ ] **Step 2: Commit**

```bash
git add tools/measure_discharge.py
git commit -m "tools: log the M5 discharge curve until the stick dies"
```

- [ ] **Step 3: Run the experiment**

```bash
systemctl --user stop familiar
uv run --with bleak python tools/measure_discharge.py \
    --address F0:16:1D:03:4C:FA --out discharge.csv
# unplug the stick; leave it 1-2 hours
```

Then, after it dies:

```bash
systemctl --user start familiar
bluetoothctl info F0:16:1D:03:4C:FA
familiar doctor
```

- [ ] **Step 4: Record the outcome**

Add the result to the spec's Phase 2 section — the CSV path, whether the bond survived, and the voltage where the curve turns down. **This is the input to the `BatteryGuard` plan.** If the bond survived, stop: the brownout theory is dead and `BatteryGuard` should not be written until there is a new theory.

---

## Out of scope for this plan

**Phase 3, `BatteryGuard`** (`src/battery_guard.h` + `main.cpp` wiring), deliberately. Its warn and shutdown thresholds are the whole point of Task 5, and writing them now would mean inventing numbers the spec explicitly says must be measured. It gets its own plan once `discharge.csv` exists.
