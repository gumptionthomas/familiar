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
    def __init__(self, connects=True, stop_raises=False, start_raises=False):
        self.calls = []
        self._connects = connects
        self._stop_raises = stop_raises
        self._start_raises = start_raises

    def stop(self):
        self.calls.append("stop")
        if self._stop_raises:
            raise RuntimeError("systemctl stop failed")

    def start(self):
        self.calls.append("start")
        if self._start_raises:
            raise RuntimeError("systemctl start timed out")

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


def test_a_failing_stop_still_restarts_the_daemon():
    service = FakeService(stop_raises=True)
    report, _, _, service = _run(service=service)
    assert report.ok is False
    assert "start" in service.calls


def test_a_raising_restart_still_returns_a_report_naming_the_fix():
    # An exception inside `finally` would discard the pending `return report`
    # and propagate out of run_repair entirely -- leaving the daemon stopped
    # with no report saying so. It must come back as a normal report instead,
    # naming the one command that recovers.
    service = FakeService(start_raises=True)
    report, _, _, service = _run(service=service)
    assert isinstance(report, repair.RepairReport)
    assert "systemctl --user start familiar" in report.message


def test_a_raising_restart_on_an_otherwise_successful_repair_is_not_ok():
    # Pairing succeeded, but the daemon never actually came back up -- this
    # must never be reported as ok, no matter how clean the rest of the
    # sequence was.
    service = FakeService(start_raises=True)
    report, _, _, service = _run(service=service)
    assert report.ok is False


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


def test_saw_connect_requires_the_space_after_connected():
    # Without the trailing space in the match, "[familiar] connected" is a
    # substring of this line and it would read as a successful connect.
    assert service_ctl.saw_connect("[familiar] connectedish\n") is False


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
