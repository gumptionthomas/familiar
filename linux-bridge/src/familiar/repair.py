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

    try:
        service.stop()
        done("stopped the daemon")
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
        done("restarted the daemon")

    if await service.wait_for_connect(connect_timeout):
        report.ok = True
        report.message = "the buddy is back"
    else:
        report.message = (
            f"paired, but the daemon did not connect within "
            f"{int(connect_timeout)}s — run `familiar doctor`")
    return report
