from familiar import doctor


def _facts(**over):
    """A healthy baseline; override individual facts per test."""
    base = {
        "config": {"parsed": True, "mode": "ble", "address": "AA:BB:CC:DD:EE:FF",
                   "haiku": True, "tidbyt": False},
        "service": {"installed": True, "active": True, "manual_procs": 0},
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
