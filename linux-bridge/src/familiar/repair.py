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

    restart_failed = False
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
        # An exception raised inside `finally` DISCARDS the pending `return
        # report` above and propagates out of run_repair -- past main()'s
        # generic handler, which would print "repair could not run" with no
        # mention that the daemon is now stopped. `service.start()` is a
        # subprocess call and CAN raise (TimeoutExpired, OSError), especially
        # right after a slow `stop()` (systemd may still be tearing the unit
        # down). The one invariant this whole command promises is that the
        # daemon always comes back, so a failure here must become a reported
        # outcome, never an escaping exception.
        try:
            service.start()
            done("restarted the daemon")
        except Exception as e:
            restart_failed = True
            report.message = (
                (report.message + " | " if report.message else "")
                + f"could not restart the daemon: {type(e).__name__}: {e} — "
                "run: systemctl --user start familiar")

    # If the restart itself failed, the daemon is NOT running -- do not ask
    # it whether it connected (that would either hang on a dead service or,
    # worse, overwrite the restart-failure message above with a connect-
    # timeout message that hides the real, more urgent problem).
    if restart_failed:
        return report

    if await service.wait_for_connect(connect_timeout):
        report.ok = True
        report.message = "the buddy is back"
    else:
        report.message = (
            f"paired, but the daemon did not connect within "
            f"{int(connect_timeout)}s — run `familiar doctor`")
    return report


def adapter_state(run=None):
    """(powered, blocked) for the Bluetooth adapter. None means 'couldn't tell'.

    Read-only, and best-effort by design: failing to READ the state must never
    be a reason to refuse a repair.
    """
    import subprocess

    def _run(cmd):
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=5, check=False).stdout
    run = _run if run is None else run

    powered = blocked = None
    try:
        for line in run(["bluetoothctl", "show"]).splitlines():
            if "Powered:" in line:
                powered = line.strip().endswith("yes")
    except Exception:
        pass
    try:
        for line in run(["rfkill", "list", "bluetooth"]).splitlines():
            if "Soft blocked:" in line:
                blocked = line.strip().endswith("yes")
    except Exception:
        pass
    return powered, blocked


def preflight_error(stdin=None, powered=None, blocked=None):
    """Why repair cannot possibly work -- checked BEFORE anything is mutated.

    Both entries here were paid for on 2026-08-14, hours apart, by the same
    mistake: mutating state and only then discovering a precondition that was
    knowable up front.

    First it ran without a terminal. It stopped the daemon and REMOVED BlueZ's
    pairing record before reaching the prompt, then raised EOFError inside a
    D-Bus callback, surfacing as a misleading "Authentication Failed". The
    passkey exists only on the stick's screen, so no terminal means no repair.

    Then it ran against an rfkill-blocked radio. It stopped the daemon and
    failed inside ensure_pairable with a bare "DBusError: Failed". A blocked
    adapter cannot be powered on, and `bluetoothctl power on` fails with the
    identical error -- so the cause has to be named, not guessed at.

    `None` for powered/blocked means "couldn't tell", and never blocks a run:
    failing to read the state is not evidence that the state is bad.
    """
    import sys
    stdin = sys.stdin if stdin is None else stdin
    if not stdin.isatty():
        return ("familiar repair needs an interactive terminal — the stick "
                "displays a 6-digit passkey that has to be typed. Run it "
                "directly, not through a pipe or a non-interactive shell.")
    if blocked is True:
        return ("Bluetooth is soft blocked by rfkill, so the adapter cannot be "
                "powered on and pairing cannot start. Run: rfkill unblock "
                "bluetooth")
    if powered is False:
        return ("The Bluetooth adapter is powered off, so pairing cannot "
                "start. Run: bluetoothctl power on")
    return None


def read_passkey(stream, timeout=55.0):
    """Read one line, giving up after `timeout` seconds.

    BlueZ's agent request expires on its own (~60s) and reports an opaque
    pairing failure, so time out just before that and say what really
    happened: nobody typed the code.
    """
    import select
    ready, _, _ = select.select([stream], [], [], timeout)
    if not ready:
        raise TimeoutError(
            f"no passkey typed within {int(timeout)}s")
    return stream.readline().strip()


class _Console:
    def ask_passkey(self):
        import sys
        print("      passkey shown ON THE STICK: ", end="", flush=True)
        return read_passkey(sys.stdin)

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

    # FIRST, before the daemon is stopped or BlueZ's pairing record is dropped.
    # Everything below this line mutates state that is expensive to restore.
    powered, blocked = adapter_state()
    problem = preflight_error(powered=powered, blocked=blocked)
    if problem:
        print(f"!!  {problem}")
        return 2

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
