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
        "log": {"failures_since_connect": 0, "discover_since_connect": 0,
                "not_found_since_connect": 0, "flaps": 0,
                "recent_failures": 0},
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
        log={"failures_since_connect": 400, "discover_since_connect": 400,
             "recent_failures": 400},
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
    ))
    titles = [t.lower() for t in _titles(_errors(findings))]
    pairable_at = next(i for i, t in enumerate(titles) if "pairable" in t)
    bond_at = next(i for i, t in enumerate(titles)
                   if "bond" in t or "not paired" in t)
    assert pairable_at < bond_at, "fix the adapter before telling them to re-pair"


def test_phantom_link_is_diagnosed_and_the_remedy_is_a_disconnect():
    findings = doctor.diagnose(_facts(
        device={"connected": True},
        log={"failures_since_connect": 12, "not_found_since_connect": 12,
             "recent_failures": 12},
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
        log={"failures_since_connect": None, "discover_since_connect": None,
             "not_found_since_connect": None, "flaps": None,
             "recent_failures": None},
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
    assert facts["log"]["discover_since_connect"] == 5
    assert facts["log"]["failures_since_connect"] == 5
    assert facts["log"]["recent_failures"] == 5


def test_main_exits_1_on_an_error_and_prints_the_remedy(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "collect", lambda cfg: _facts(
        device={"paired": False, "connected": False}))
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
        log={"failures_since_connect": 1, "discover_since_connect": 1,
             "recent_failures": 1},
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
        log={"failures_since_connect": 400, "discover_since_connect": 400,
             "recent_failures": 400},
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


# --- round 2: an instantaneous sample must never veto windowed evidence ----


def test_the_real_failure_is_caught_even_if_bluetoothctl_samples_it_connected():
    # THE REGRESSION. During the real failure the daemon loops connect ->
    # discovery-fails -> disconnect, so bluetoothctl legitimately reports
    # Connected: yes for the seconds the link is up. An instantaneous sample must
    # NEVER veto 400 windowed failures and the kernel's SMP fingerprint.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},      # <- sampled mid-loop
        kernel_smp_errors=3,
        log={"failures_since_connect": 400, "discover_since_connect": 400,
             "recent_failures": 400},
    ))
    errs = [f for f in findings if f.level == "error"]
    assert errs, "400 failures + the SMP fingerprint must never read as healthy"
    assert "KeyboardOnly" in "\n".join(errs[0].remedy)
    assert not any(f.level == "ok" for f in findings)


def test_the_early_catch_corridor_fires_on_smp_corroboration():
    # 3-9 failures WITH the kernel fingerprint: catch it before 400 accumulate.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        kernel_smp_errors=2,
        log={"failures_since_connect": 4, "discover_since_connect": 4,
             "recent_failures": 4},
    ))
    errs = [f for f in findings if f.level == "error"]
    assert errs and "KeyboardOnly" in "\n".join(errs[0].remedy)


def test_log_volume_alone_fires_without_a_readable_kernel_log():
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        kernel_smp_errors=None,                 # journal unreadable
        log={"failures_since_connect": 12, "discover_since_connect": 12,
             "recent_failures": 12},
    ))
    assert [f for f in findings if f.level == "error"]


def test_a_flapping_link_never_claims_health():
    findings = doctor.diagnose(_facts(
        device={"connected": False},
        log={"failures_since_connect": 5, "discover_since_connect": 5,
             "recent_failures": 5},
    ))
    assert any(f.level == "warn" for f in findings)
    assert not any(f.level == "ok" for f in findings), \
        "a flapping link is not 'everything looks healthy'"


def test_an_unreachable_stick_is_reported_not_called_healthy():
    # Stick off / flat / out of range: BlueZ keeps the paired record forever, so
    # known=True and connected=False with "was not found" in the log. This used to
    # produce NO finding at all and print "healthy, not connected".
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        log={"failures_since_connect": 20, "not_found_since_connect": 20,
             "discover_since_connect": 0, "recent_failures": 20},
    ))
    assert any(f.level in ("warn", "error") for f in findings)
    assert not any(f.level == "ok" for f in findings)


def test_a_couldnt_check_warning_suppresses_the_health_summary():
    # If we could not look, we must not say "everything looks healthy".
    findings = doctor.diagnose(_facts(have_bluetoothctl=False))
    assert not any(f.level == "ok" for f in findings)


def test_bond_min_failures_is_pinned():
    # The reviewer showed BOND_MIN_FAILURES could be mutated to 999 and all 22
    # tests still passed. Pin the value AND pin a behavior that only holds at 3:
    # 4 discover_failures + the SMP fingerprint must catch the bond early, before
    # BOND_FAILURES_ALONE (10) would catch it on volume alone.
    assert doctor.BOND_MIN_FAILURES == 3
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        kernel_smp_errors=1,
        log={"failures_since_connect": 3, "discover_since_connect": 3,
             "recent_failures": 3},
    ))
    errs = [f for f in findings if f.level == "error"]
    assert errs and "KeyboardOnly" in "\n".join(errs[0].remedy)


def test_bond_failures_alone_is_pinned():
    # The reviewer showed BOND_FAILURES_ALONE could be mutated to 999 and all 22
    # tests still passed. Pin the value AND pin the behavior: log volume alone
    # (no kernel corroboration) must still catch it at exactly this threshold.
    assert doctor.BOND_FAILURES_ALONE == 10
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        kernel_smp_errors=None,
        log={"failures_since_connect": 10, "discover_since_connect": 10,
             "recent_failures": 10},
    ))
    errs = [f for f in findings if f.level == "error"]
    assert errs and "KeyboardOnly" in "\n".join(errs[0].remedy)


def test_blocks_health_gate_is_not_bypassable_by_deleting_the_check():
    # The reviewer showed that deleting `or f.blocks_health` from the health
    # gate passed all 22 tests -- because no warn set blocks_health except the
    # benign pairable one. `connected` is True here (not False) so the
    # separate "never claim health while disconnected" guard can't be what
    # saves this test -- only `blocks_health` on the flapping warn can.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"failures_since_connect": 5, "discover_since_connect": 5,
             "not_found_since_connect": 0, "recent_failures": 5},
    ))
    assert [f for f in findings if f.level == "error"] == []
    warns = [f for f in findings if f.level == "warn"]
    assert warns and any(f.blocks_health for f in warns), \
        "the flapping warn must set blocks_health"
    assert not any(f.level == "ok" for f in findings)


# --- round 3/4: an unknown fact is never read as "fine" (structural) -------


def test_an_unreadable_journal_never_reads_as_healthy():
    # THE BUG CLASS. `None` means "couldn't read the journal", NOT "it was clean".
    # A 5s journalctl timeout on a cold journal, plus a device sampled Connected:
    # yes mid-failure-loop, made doctor print "Everything looks healthy" during
    # the exact failure it exists to catch.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        kernel_smp_errors=None,
        log={"failures_since_connect": None, "discover_since_connect": None,
             "not_found_since_connect": None, "flaps": None,
             "recent_failures": None},
    ))
    assert not any(f.level == "ok" for f in findings), \
        "we could not read the log -- we must not claim health"
    assert any(f.level == "warn" and f.blocks_health for f in findings)


def test_diagnose_never_returns_an_empty_report():
    # A dead stick with a sub-threshold failure count produced ZERO findings:
    # a blank line and exit 0. A blank report is the worst output a diagnostic
    # can give.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        log={"failures_since_connect": 1, "not_found_since_connect": 1,
             "discover_since_connect": 0, "recent_failures": 1},
    ))
    assert findings, "a diagnostic must never print nothing"
    assert not any(f.level == "ok" for f in findings)


def test_every_unknown_fact_suppresses_the_health_claim():
    # The sweep: ANY relevant fact we could not determine must produce a
    # blocks_health warning, so the rule cannot be forgotten at a new site.
    for path in [("service", "active"), ("adapter", "pairable"),
                 ("adapter", "powered"),
                 ("device", "connected"), ("device", "paired")]:
        section, key = path
        findings = doctor.diagnose(_facts(**{section: {key: None}}))
        assert not any(f.level == "ok" for f in findings), \
            f"{section}.{key} unknown must suppress the health summary"


def test_tidbyt_only_mode_does_not_warn_about_irrelevant_ble_facts():
    # In Tidbyt-only mode the BLE facts are IRRELEVANT, not unknown. Warning
    # about them would be a new false positive.
    findings = doctor.diagnose(_facts(
        config={"mode": "tidbyt", "address": None, "tidbyt": True},
        adapter={"powered": None, "pairable": None},
        device={"known": None, "paired": None, "bonded": None,
                "trusted": None, "connected": None},
        log={"failures_since_connect": None, "discover_since_connect": None,
             "not_found_since_connect": None, "flaps": None,
             "recent_failures": None},
    ))
    assert [f for f in findings if f.level == "error"] == []
    assert any(f.level == "ok" for f in findings), \
        "a healthy Tidbyt-only setup is healthy"


def test_a_single_blip_is_not_flapping(monkeypatch):
    # Pins LINK_FAILING_MIN: dropping it to 1 would resurrect round 1's cry-wolf.
    findings = doctor.diagnose(_facts(
        device={"connected": True},
        log={"failures_since_connect": 1, "discover_since_connect": 1,
             "recent_failures": 1}))
    assert [f for f in findings if f.level == "error"] == []
    assert any(f.level == "ok" for f in findings)


def test_a_dead_stick_is_not_called_a_phantom_link():
    # Pins the phantom trigger's `connected is True` check -- the ONLY legitimate
    # use of the instantaneous sample. Without it, a dead stick gets the wrong
    # remedy (`bluetoothctl disconnect`).
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        log={"failures_since_connect": 20, "not_found_since_connect": 20,
             "discover_since_connect": 0, "recent_failures": 20},
    ))
    assert not any("phantom" in f.title.lower() for f in findings)


def test_an_unknown_journal_yields_no_error_findings():
    # The sweep suppresses the HEALTH CLAIM on an unknown fact -- but it must not
    # be the only thing standing between us and a false ERROR. An unreadable
    # journal must not produce a phantom-link or bond diagnosis: telling someone
    # to hand-pair with a passkey because we could not read a log file is round
    # one's cry-wolf all over again.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"failures_since_connect": None, "discover_since_connect": None,
             "not_found_since_connect": None, "flaps": None,
             "recent_failures": None},
    ))
    assert [f for f in findings if f.level == "error"] == []
    assert not any(f.level == "ok" for f in findings)      # but no health claim either


def test_an_uncountable_manual_process_never_reads_as_healthy():
    # collect() sets manual_procs=None when pgrep fails/times out. The old code
    # did `(svc.get("manual_procs") or 0) > 0` -> None became "no second
    # instance" -> "Everything looks healthy" while two daemons fought over the
    # one BLE connection. This is the fact the hand-maintained allowlist forgot.
    findings = doctor.diagnose(_facts(service={"active": True,
                                               "manual_procs": None,
                                               "manual_pids": None}))
    assert not any(f.level == "ok" for f in findings)
    assert any(f.level == "warn" and f.blocks_health for f in findings)


def test_a_powered_off_adapter_is_reported():
    # Deleting this branch passed the whole suite.
    findings = doctor.diagnose(_facts(adapter={"powered": False},
                                      device={"connected": False}))
    errs = [f for f in findings if f.level == "error"]
    assert errs and any("power on" in line for f in errs for line in f.remedy)


def test_only_one_warning_when_bluetoothctl_is_missing():
    # One cause, one warning. Four warnings for one missing binary is noise.
    findings = doctor.diagnose(_facts(have_bluetoothctl=False))
    warns = [f for f in findings if f.level == "warn"]
    assert len(warns) == 1
    assert not any(f.level == "ok" for f in findings)


def test_an_unknown_mode_never_reads_as_healthy():
    # `mode` gates the entire BLE block and the health summary. Read through a
    # bare .get(), an absent or None mode printed "Everything looks healthy".
    # collect() cannot produce that today -- but config.parsed sits in the facts
    # unused, inviting exactly the future change that would resurrect it.
    for facts in ({}, {"config": {"mode": None}}):
        findings = doctor.diagnose(facts)
        assert findings, "a diagnostic must never print nothing"
        assert not any(f.level == "ok" for f in findings), \
            "we do not know what this buddy is configured to drive"


def test_unreachable_findings_blocks_health_flag_is_pinned():
    # The reviewer flipped the "stick not reachable" finding's blocks_health
    # from True to False and nothing caught it -- the separate "never claim
    # health while cfg.mode==ble and connected is False" guard happens to
    # rescue the *summary* in this exact scenario regardless of the flag, so
    # this asserts the flag directly rather than relying on that guard.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        log={"failures_since_connect": 20, "not_found_since_connect": 20,
             "discover_since_connect": 0, "recent_failures": 20},
    ))
    unreachable = next(f for f in findings if "not reachable" in f.title.lower())
    assert unreachable.blocks_health is True


# --- round 5: count failures SINCE THE LAST SUCCESSFUL CONNECT -------------


def test_a_fault_the_daemon_already_fixed_is_not_reported(capsys):
    # THE REGRESSION (caught in production minutes after PR #51 merged). A
    # service restart leaves a phantom BlueZ link; the daemon's own auto-clear
    # (PR #44) fixes it seconds later. doctor counted the historical failures
    # and reported "!! Phantom link" on a perfectly healthy, connected buddy --
    # every time anyone restarted the service.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"failures_since_connect": 0,      # it RECOVERED after them
             "discover_since_connect": 0,
             "not_found_since_connect": 0,
             "flaps": 0,
             "recent_failures": 3},            # ...but they did happen
    ))
    assert [f for f in findings if f.level == "error"] == []
    assert any(f.level == "ok" for f in findings)
    # ...and say so, rather than silently claiming perfection.
    summary = next(f for f in findings if f.level == "ok")
    assert "recovered" in summary.why


def test_the_2026_07_13_failure_still_fires():
    # No successful connect in the window at all -> every failure counts, and
    # the bond diagnosis must still fire even though bluetoothctl sampled the
    # device as Connected: yes mid-failure-loop.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        kernel_smp_errors=3,
        log={"failures_since_connect": 400,
             "discover_since_connect": 400,
             "not_found_since_connect": 0,
             "flaps": 0,
             "recent_failures": 400},
    ))
    errs = [f for f in findings if f.level == "error"]
    assert errs and "KeyboardOnly" in "\n".join(errs[0].remedy)
    assert not any(f.level == "ok" for f in findings)


def test_a_live_phantom_link_still_fires():
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"failures_since_connect": 5,
             "discover_since_connect": 0,
             "not_found_since_connect": 5,     # still failing NOW
             "flaps": 0,
             "recent_failures": 5},
    ))
    errs = [f for f in findings if f.level == "error"]
    assert errs and "phantom" in errs[0].title.lower()
    assert any("disconnect" in line for line in errs[0].remedy)


def test_an_unstable_link_is_never_called_healthy():
    # THE OVER-CORRECTION GUARD. A link that connects and drops repeatedly will
    # often be sampled just after a connect -- failures_since_connect ~ 0 -- so
    # counting only "since the last success" would call it healthy. The daemon
    # logs "link flapped after N.Ns" distinctly; that count is what catches it.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"failures_since_connect": 0,
             "discover_since_connect": 0,
             "not_found_since_connect": 0,
             "flaps": 5,                       # up right now, but not trustworthy
             "recent_failures": 0},
    ))
    assert any(f.level == "warn" and f.blocks_health for f in findings)
    assert not any(f.level == "ok" for f in findings)


def test_an_unknown_flap_count_never_reads_as_healthy():
    # `flaps` must be read through F.need(), not F.opt(): it gates the
    # over-correction guard's decision (unstable_fires), so an unreadable
    # count is exactly the "unknown read as fine" bug class this module keeps
    # tripping over. Isolate it as the ONLY unknown log fact -- if the other
    # three were also None, the blocks_health warning could come from any of
    # them and this wouldn't pin `flaps` specifically.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"failures_since_connect": 0, "discover_since_connect": 0,
             "not_found_since_connect": 0, "flaps": None,
             "recent_failures": 0},
    ))
    assert not any(f.level == "ok" for f in findings)
    assert any(f.level == "warn" and f.blocks_health for f in findings)


def test_one_or_two_flaps_is_not_instability():
    # Pins LINK_FAILING_MIN: dropping it to 1 resurrects the cry-wolf class.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"failures_since_connect": 0, "discover_since_connect": 0,
             "not_found_since_connect": 0, "flaps": 2, "recent_failures": 0},
    ))
    assert [f for f in findings if f.level == "error"] == []
    assert any(f.level == "ok" for f in findings)


def test_collect_counts_only_failures_after_the_last_connect(monkeypatch):
    # The whole fix, at the collect() layer: ordering, not counting.
    journal = "\n".join([
        "[familiar] disconnected: Device with address AA:BB was not found.",
        "[familiar] disconnected: Device with address AA:BB was not found.",
        "[familiar] disconnected: Device with address AA:BB was not found.",
        "[familiar] clearing a possible stale link to AA:BB",
        "[familiar] connected AA:BB",              # <- RECOVERED here
    ])

    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "journalctl" and "-k" in cmd:
            return ""
        if cmd[0] == "journalctl":
            return journal
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
    log = doctor.collect(cfg)["log"]

    assert log["failures_since_connect"] == 0     # all three preceded the connect
    assert log["not_found_since_connect"] == 0
    assert log["recent_failures"] == 3            # ...but they are still visible


def test_collect_counts_failures_that_followed_the_last_connect(monkeypatch):
    journal = "\n".join([
        "[familiar] connected AA:BB",
        "[familiar] disconnected: Device with address AA:BB was not found.",
        "[familiar] disconnected: failed to discover services, device disconnected",
    ])

    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "journalctl" and "-k" in cmd:
            return ""
        if cmd[0] == "journalctl":
            return journal
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
    log = doctor.collect(cfg)["log"]

    assert log["failures_since_connect"] == 2
    assert log["not_found_since_connect"] == 1
    assert log["discover_since_connect"] == 1


def test_collect_counts_everything_when_there_was_never_a_connect(monkeypatch):
    # No successful connect in the window: the link has not worked within living
    # memory, so every failure counts. This is the 2026-07-13 shape.
    journal = "\n".join([
        "[familiar] disconnected: failed to discover services, device disconnected",
    ] * 7)

    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "journalctl" and "-k" in cmd:
            return ""
        if cmd[0] == "journalctl":
            return journal
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
    log = doctor.collect(cfg)["log"]

    assert log["failures_since_connect"] == 7
    assert log["discover_since_connect"] == 7


def test_collect_counts_flaps_across_the_whole_window(monkeypatch):
    # Flaps are repetition ACROSS connects, so "since the last connect" is the
    # wrong denominator -- it would be ~0 by construction.
    journal = "\n".join([
        "[familiar] connected AA:BB",
        "[familiar] link flapped after 2.1s; backing off AA:BB",
        "[familiar] connected AA:BB",
        "[familiar] link flapped after 1.8s; backing off AA:BB",
        "[familiar] connected AA:BB",
        "[familiar] link flapped after 3.0s; backing off AA:BB",
        "[familiar] connected AA:BB",
    ])

    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "journalctl" and "-k" in cmd:
            return ""
        if cmd[0] == "journalctl":
            return journal
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
    log = doctor.collect(cfg)["log"]

    assert log["failures_since_connect"] == 0     # sampled right after a connect
    assert log["flaps"] == 3                      # ...but it is plainly unstable
