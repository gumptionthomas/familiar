from familiar import doctor
from familiar.config import Config


def _facts(**over):
    """A healthy baseline; override individual facts per test."""
    base = {
        "config": {"parsed": True, "mode": "ble", "address": "AA:BB:CC:DD:EE:FF",
                   "haiku": True, "tidbyt": False},
        "service": {"installed": True, "active": True, "manual_procs": 0,
                    "manual_pids": []},
        "have_bluetoothctl": True,
        "adapter": {"powered": True, "pairable": True},
        "device": {"known": True, "paired": True, "bonded": True,
                   "trusted": True, "connected": True},
        "kernel_smp_errors": 0,
        "log": {"discover_failures": 0, "not_found": 0, "phantom_clears": 0,
                "connected_recently": True},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def _titles(findings):
    return [f.title for f in findings]


def _errors(findings):
    return [f for f in findings if f.level == "error"]


def test_healthy_reports_ok_and_nothing_else():
    findings = doctor.diagnose(_facts())
    assert _errors(findings) == []
    assert any(f.level == "ok" for f in findings)


def test_the_2026_07_13_failure_is_diagnosed_as_a_one_sided_bond():
    # THE regression test for the hours lost. The M5 lost its pairing keys while
    # BlueZ kept its own: still "paired" locally, never connects, the log fills
    # with "failed to discover services", and the kernel logs SMP errors.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        kernel_smp_errors=3,
        log={"discover_failures": 400, "connected_recently": False},
    ))
    errs = _errors(findings)
    assert errs, "a 400-failure discovery loop must be diagnosed, not shrugged at"
    bond = errs[0]
    assert "bond" in bond.title.lower() or "pair" in bond.title.lower()
    joined = "\n".join(bond.remedy)
    # The two steps everyone misses, and the reason the daemon cannot self-heal:
    assert "pairable on" in joined
    assert "KeyboardOnly" in joined


def test_an_unpaired_device_is_the_same_one_sided_bond_diagnosis():
    findings = doctor.diagnose(_facts(
        device={"paired": False, "bonded": False, "connected": False},
        log={"connected_recently": False},
    ))
    errs = _errors(findings)
    assert errs and "KeyboardOnly" in "\n".join(errs[0].remedy)


def test_a_non_pairable_adapter_is_reported_before_any_repair_advice():
    # A re-pair CANNOT succeed while the adapter is Pairable: no. If we told the
    # user to re-pair without fixing this first, we would be sending them into a
    # loop that cannot terminate.
    findings = doctor.diagnose(_facts(
        adapter={"pairable": False},
        device={"paired": False, "connected": False},
        log={"connected_recently": False},
    ))
    titles = [t.lower() for t in _titles(_errors(findings))]
    pairable_at = next(i for i, t in enumerate(titles) if "pairable" in t)
    bond_at = next(i for i, t in enumerate(titles)
                   if "bond" in t or "not paired" in t)
    assert pairable_at < bond_at, "fix the adapter before telling them to re-pair"


def test_phantom_link_is_diagnosed_and_the_remedy_is_a_disconnect():
    findings = doctor.diagnose(_facts(
        device={"connected": True},
        log={"not_found": 12, "connected_recently": False},
    ))
    errs = _errors(findings)
    assert errs and "phantom" in errs[0].title.lower()
    assert any("disconnect" in line for line in errs[0].remedy)


def test_two_instances_are_diagnosed_and_pkill_is_never_suggested():
    # `pkill -f familiar` matches its own shell and kills the caller.
    findings = doctor.diagnose(_facts(service={"active": True, "manual_procs": 1}))
    errs = _errors(findings)
    assert errs and "instance" in errs[0].title.lower()
    assert not any("pkill" in line for line in errs[0].remedy)


def test_service_not_running_is_diagnosed():
    findings = doctor.diagnose(_facts(service={"active": False}))
    errs = _errors(findings)
    assert errs and "service" in errs[0].title.lower()
    assert any("systemctl" in line for line in errs[0].remedy)


def test_nothing_configured_points_at_familiar_init():
    findings = doctor.diagnose(_facts(
        config={"mode": "none", "address": None, "haiku": False}))
    errs = _errors(findings)
    assert errs and any("familiar init" in line for line in errs[0].remedy)


def test_tidbyt_only_mode_does_not_report_ble_failures():
    # No M5 configured -> the BLE checks are IRRELEVANT, not failing.
    findings = doctor.diagnose(_facts(
        config={"mode": "tidbyt", "address": None, "tidbyt": True},
        device={"known": None, "paired": None, "bonded": None,
                "trusted": None, "connected": None},
        log={"connected_recently": None},
    ))
    assert _errors(findings) == []


def test_unknown_facts_never_produce_a_confident_diagnosis():
    # Nothing could be determined. We must say so -- not invent a cause.
    # Guessing from incomplete evidence is exactly what cost us the day.
    findings = doctor.diagnose(_facts(
        service={"installed": None, "active": None, "manual_procs": None},
        have_bluetoothctl=False,
        adapter={"powered": None, "pairable": None},
        device={"known": None, "paired": None, "bonded": None,
                "trusted": None, "connected": None},
        kernel_smp_errors=None,
        log={"discover_failures": None, "not_found": None,
             "phantom_clears": None, "connected_recently": None},
    ))
    assert _errors(findings) == []                      # no invented cause
    assert any(f.level == "warn" for f in findings)     # but it says so


def test_collect_never_raises_when_nothing_is_available(monkeypatch):
    # No bluetoothctl, no systemd, no journal. collect() must return a facts dict
    # full of None -- never raise. The daemon's user is already confused; a
    # traceback from the DIAGNOSTIC tool is the last thing they need.
    def boom(*a, **k):
        raise FileNotFoundError("nothing is installed here")

    monkeypatch.setattr(doctor, "_run", boom)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
    facts = doctor.collect(cfg)

    assert facts["have_bluetoothctl"] is False
    assert facts["adapter"]["pairable"] is None
    assert facts["device"]["paired"] is None
    assert facts["service"]["active"] is None
    # ...and diagnose() must survive those facts without inventing a cause.
    assert [f for f in doctor.diagnose(facts) if f.level == "error"] == []


def test_collect_parses_bluetoothctl_and_the_log(monkeypatch):
    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"       # collect() probes this first
        if cmd[0] == "bluetoothctl" and cmd[1] == "show":
            return "\tPowered: yes\n\tPairable: no\n"
        if cmd[0] == "bluetoothctl" and cmd[1] == "info":
            return "\tPaired: yes\n\tBonded: yes\n\tTrusted: no\n\tConnected: no\n"
        if cmd[0] == "systemctl":
            return "active\n"
        if cmd[0] == "journalctl" and "-k" in cmd:
            # our configured MAC, lowercase as the kernel prints it
            return "unexpected SMP command 0x0b from f0:16:1d:03:4c:fa\n" * 3
        if cmd[0] == "journalctl":
            return ("[familiar] disconnected: failed to discover services\n" * 5 +
                    "[familiar] clearing a possible stale link to AA:BB\n")
        if cmd[0] == "pgrep":
            return ""
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="F0:16:1D:03:4C:FA", owner="", socket_path="/tmp/x.sock")
    facts = doctor.collect(cfg)

    assert facts["adapter"] == {"powered": True, "pairable": False}
    assert facts["device"]["paired"] is True
    assert facts["device"]["connected"] is False
    assert facts["service"]["active"] is True
    assert facts["kernel_smp_errors"] == 3
    assert facts["log"]["discover_failures"] == 5
    assert facts["log"]["phantom_clears"] == 1
    assert facts["log"]["connected_recently"] is False


def test_main_exits_1_on_an_error_and_prints_the_remedy(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "collect", lambda cfg: _facts(
        device={"paired": False, "connected": False},
        log={"connected_recently": False}))
    assert doctor.main([]) == 1
    out = capsys.readouterr().out
    assert "KeyboardOnly" in out          # the remedy is actually printed
    assert "pairable on" in out


def test_main_exits_0_when_healthy(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "collect", lambda cfg: _facts())
    assert doctor.main([]) == 0
    assert "healthy" in capsys.readouterr().out.lower()


def test_a_healthy_connected_buddy_is_never_reported_as_broken():
    # Pairable: no is the GNOME DEFAULT and only blocks NEW pairings -- an
    # existing bond connects fine. A working buddy must not produce an error, and
    # must still get its health summary. A diagnostic that cries wolf on a healthy
    # machine destroys trust in every finding it makes.
    findings = doctor.diagnose(_facts(adapter={"pairable": False}))
    assert [f for f in findings if f.level == "error"] == []
    assert any(f.level == "ok" for f in findings)


def test_a_single_transient_failure_does_not_advise_a_hand_repair():
    # A healthy long-lived link has NO "connected" line in the last 10 minutes --
    # that is the steady state, not a fault. One blip while the daemon backs off
    # must never send the user to stop the service and hand-pair with a passkey.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"discover_failures": 1, "connected_recently": False},
    ))
    assert [f for f in findings if f.level == "error"] == []
    assert "KeyboardOnly" not in "\n".join(
        line for f in findings for line in f.remedy)


def test_smp_errors_from_another_device_are_not_counted(monkeypatch):
    # The kernel logs SMP errors for ANY peripheral. Counting a mouse's errors
    # against our M5 would be a brand-new false positive.
    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "journalctl" and "-k" in cmd:
            return ("Bluetooth: hci0: unexpected SMP command 0x0b from "
                    "aa:aa:aa:aa:aa:aa\n")          # NOT our MAC
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="F0:16:1D:03:4C:FA", owner="", socket_path="/tmp/x.sock")
    assert doctor.collect(cfg)["kernel_smp_errors"] == 0


def test_smp_errors_from_our_device_are_counted(monkeypatch):
    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "journalctl" and "-k" in cmd:
            return ("Bluetooth: hci0: unexpected SMP command 0x0b from "
                    "f0:16:1d:03:4c:fa\n") * 3     # ours, lowercase in the kernel
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="F0:16:1D:03:4C:FA", owner="", socket_path="/tmp/x.sock")
    assert doctor.collect(cfg)["kernel_smp_errors"] == 3


def test_the_bond_diagnosis_still_fires_on_the_real_thing():
    # The 2026-07-13 failure must STILL be caught after all this tightening.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        kernel_smp_errors=3,
        log={"discover_failures": 400, "connected_recently": False},
    ))
    errs = [f for f in findings if f.level == "error"]
    assert errs and "KeyboardOnly" in "\n".join(errs[0].remedy)


def test_the_two_instances_remedy_names_the_actual_pid():
    findings = doctor.diagnose(_facts(
        service={"active": True, "manual_procs": 1, "manual_pids": [99999]}))
    errs = [f for f in findings if f.level == "error"]
    assert errs and any("99999" in line for line in errs[0].remedy)


def test_the_service_is_not_mistaken_for_a_manual_instance(monkeypatch):
    # The systemd unit's ExecStart IS `familiar run`, so a naive
    # `pgrep -f "familiar run"` matches the SERVICE ITSELF -- and doctor would
    # then report "two instances are running" to every user, every time.
    # A diagnostic that always invents a fault is worse than no diagnostic.
    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "bluetoothctl":
            return ""
        if cmd[0] == "systemctl" and "is-active" in cmd:
            return "active\n"
        if cmd[0] == "systemctl" and "MainPID" in " ".join(cmd):
            return "82408\n"                     # the service's own PID
        if cmd[0] == "systemctl":
            return "familiar.service enabled\n"
        if cmd[0] == "pgrep":
            return "82408\n"                     # ...which pgrep also matches
        if cmd[0] == "journalctl":
            return ""
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
    facts = doctor.collect(cfg)

    assert facts["service"]["manual_procs"] == 0, \
        "the service's own process must not be counted as a manual instance"
    assert not [f for f in doctor.diagnose(facts)
                if f.level == "error" and "instance" in f.title.lower()]


def test_a_genuine_manual_instance_is_still_detected(monkeypatch):
    # ...but a REAL second daemon must still be caught: only one BLE connection
    # to the stick is possible at a time, and two daemons fight over it.
    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "bluetoothctl":
            return ""
        if cmd[0] == "systemctl" and "is-active" in cmd:
            return "active\n"
        if cmd[0] == "systemctl" and "MainPID" in " ".join(cmd):
            return "82408\n"
        if cmd[0] == "systemctl":
            return "familiar.service enabled\n"
        if cmd[0] == "pgrep":
            return "82408\n99999\n"              # the service AND a real manual one
        if cmd[0] == "journalctl":
            return ""
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
    facts = doctor.collect(cfg)

    assert facts["service"]["manual_procs"] == 1
    assert [f for f in doctor.diagnose(facts)
            if f.level == "error" and "instance" in f.title.lower()]
