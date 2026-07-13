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


# --- round 2: an instantaneous sample must never veto windowed evidence ----


def test_the_real_failure_is_caught_even_if_bluetoothctl_samples_it_connected():
    # THE REGRESSION. During the real failure the daemon loops connect ->
    # discovery-fails -> disconnect, so bluetoothctl legitimately reports
    # Connected: yes for the seconds the link is up. An instantaneous sample must
    # NEVER veto 400 windowed failures and the kernel's SMP fingerprint.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},      # <- sampled mid-loop
        kernel_smp_errors=3,
        log={"discover_failures": 400, "connected_recently": False},
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
        log={"discover_failures": 4, "connected_recently": False},
    ))
    errs = [f for f in findings if f.level == "error"]
    assert errs and "KeyboardOnly" in "\n".join(errs[0].remedy)


def test_log_volume_alone_fires_without_a_readable_kernel_log():
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        kernel_smp_errors=None,                 # journal unreadable
        log={"discover_failures": 12, "connected_recently": False},
    ))
    assert [f for f in findings if f.level == "error"]


def test_a_flapping_link_never_claims_health():
    findings = doctor.diagnose(_facts(
        device={"connected": False},
        log={"discover_failures": 5, "connected_recently": False},
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
        log={"not_found": 20, "discover_failures": 0, "connected_recently": False},
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
        log={"discover_failures": 3, "connected_recently": False},
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
        log={"discover_failures": 10, "connected_recently": False},
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
        log={"discover_failures": 5, "not_found": 0, "connected_recently": False},
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
        log={"discover_failures": None, "not_found": None,
             "phantom_clears": None, "connected_recently": None},
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
        log={"not_found": 1, "discover_failures": 0, "connected_recently": False},
    ))
    assert findings, "a diagnostic must never print nothing"
    assert not any(f.level == "ok" for f in findings)


def test_every_unknown_fact_suppresses_the_health_claim():
    # The sweep: ANY relevant fact we could not determine must produce a
    # blocks_health warning, so the rule cannot be forgotten at a new site.
    for path in [("service", "active"), ("adapter", "pairable"),
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
        log={"discover_failures": None, "not_found": None,
             "phantom_clears": None, "connected_recently": None},
    ))
    assert [f for f in findings if f.level == "error"] == []
    assert any(f.level == "ok" for f in findings), \
        "a healthy Tidbyt-only setup is healthy"


def test_a_single_blip_is_not_flapping(monkeypatch):
    # Pins LINK_FAILING_MIN: dropping it to 1 would resurrect round 1's cry-wolf.
    findings = doctor.diagnose(_facts(
        device={"connected": True},
        log={"discover_failures": 1, "connected_recently": False}))
    assert [f for f in findings if f.level == "error"] == []
    assert any(f.level == "ok" for f in findings)


def test_a_dead_stick_is_not_called_a_phantom_link():
    # Pins the phantom trigger's `connected is True` check -- the ONLY legitimate
    # use of the instantaneous sample. Without it, a dead stick gets the wrong
    # remedy (`bluetoothctl disconnect`).
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        log={"not_found": 20, "discover_failures": 0, "connected_recently": False},
    ))
    assert not any("phantom" in f.title.lower() for f in findings)


def test_unreachable_findings_blocks_health_flag_is_pinned():
    # The reviewer flipped the "stick not reachable" finding's blocks_health
    # from True to False and nothing caught it -- the separate "never claim
    # health while cfg.mode==ble and connected is False" guard happens to
    # rescue the *summary* in this exact scenario regardless of the flag, so
    # this asserts the flag directly rather than relying on that guard.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": False},
        log={"not_found": 20, "discover_failures": 0, "connected_recently": False},
    ))
    unreachable = next(f for f in findings if "not reachable" in f.title.lower())
    assert unreachable.blocks_health is True
