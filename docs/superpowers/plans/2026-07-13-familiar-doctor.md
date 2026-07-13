# `familiar doctor` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command that turns "my buddy is broken" into a named cause and the exact commands to fix it — instead of hours of hypothesis-hopping across `systemctl`, `bluetoothctl`, `journalctl -k`, and an HCI trace.

**Architecture:** `doctor.py` splits into a **pure** `diagnose(facts) -> list[Finding]` and a thin `collect(cfg)` that shells out. Every diagnosis is then unit-testable with no Bluetooth, no systemd, and no hardware — including the exact 2026-07-13 failure that cost hours. Same shape as `feed.h` (pure change-detection) and `archive.stats` (pure trend maths): the logic worth testing is isolated from the I/O that makes it untestable.

**Tech Stack:** Python 3.11+, stdlib only (`subprocess`, `dataclasses`, `re`, `argparse`). No new dependencies. pytest.

**Spec:** `docs/superpowers/specs/2026-07-13-familiar-doctor-design.md`

## Global Constraints

- **Diagnose only. Never mutate anything.** No `--fix`, no starting/stopping services, no `bluetoothctl` state changes. `doctor` is strictly read-only. The failure that motivated it *cannot* be auto-fixed — the firmware is `ESP_LE_AUTH_REQ_SC_MITM_BOND`, so pairing requires a human to type a 6-digit passkey off the stick. That is MITM protection working as intended. A `--fix` would succeed at the easy cases, look like it worked, and leave the user broken on the one that matters.
- **Exactly one new command.** Do NOT add `start`, `stop`, `restart`, `logs`, or `redeploy` — they are 1:1 wrappers over `systemctl`/`journalctl`, and the spec rejects them explicitly.
- **`collect()` must NEVER raise.** A missing `bluetoothctl`, absent systemd, denied journal access, an unparseable config — every one degrades to `None` for that fact. `diagnose` then reports *"couldn't determine X"* honestly. Guessing is what cost us the day.
- **`diagnose()` must be PURE** — no subprocess, no filesystem, no network. It takes a facts dict and returns findings. If it touches I/O, the tests become worthless.
- **An unknown fact must never produce a confident diagnosis.** `None` means "couldn't check", not "fine".
- No new dependencies. Stdlib only.
- Run tests from `linux-bridge/`: `uv run pytest -q`. The suite is currently **198 passing**.
- Imports belong in each file's top import block.

---

### Task 1: The pure diagnostic core

**Files:**
- Create: `linux-bridge/src/familiar/doctor.py`
- Test: `linux-bridge/tests/test_doctor.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (Task 2 depends on these exactly):
  ```python
  @dataclass
  class Finding:
      level: str            # "ok" | "warn" | "error"
      title: str
      why: str
      remedy: list[str]     # copy-pasteable command lines; may be empty

  def diagnose(facts: dict) -> list[Finding]    # PURE — no I/O
  ```
  The facts dict shape is fixed (Task 2's `collect()` must produce exactly this):
  ```python
  {
    "config":  {"parsed": bool, "mode": "ble"|"tidbyt"|"none", "address": str|None,
                "haiku": bool, "tidbyt": bool},
    "service": {"installed": bool|None, "active": bool|None, "manual_procs": int|None},
    "have_bluetoothctl": bool,
    "adapter": {"powered": bool|None, "pairable": bool|None},
    "device":  {"known": bool|None, "paired": bool|None, "bonded": bool|None,
                "trusted": bool|None, "connected": bool|None},
    "kernel_smp_errors": int|None,
    "log": {"discover_failures": int|None, "not_found": int|None,
            "phantom_clears": int|None, "connected_recently": bool|None},
  }
  ```

**Background — what this is for.** On 2026-07-13 the buddy stopped connecting. Diagnosis took hours and three confidently wrong hypotheses. The real cause: the M5 lost its side of the pairing bond while the laptop kept its own. Every connect went link-up → the M5 sends `SMP: Security Request` → BlueZ answers `Pairing Failed: Pairing not supported` → the M5 hangs up. Two things were missed for hours: the adapter was `Pairable: no` (so no re-pair could *ever* succeed), and the kernel had been logging `unexpected SMP command 0x0b` the whole time.

- [ ] **Step 1: Write the failing tests**

Create `linux-bridge/tests/test_doctor.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_doctor.py -q`

Expected: FAIL — `ImportError: cannot import name 'doctor' from 'familiar'`.

- [ ] **Step 3: Implement the pure core**

Create `linux-bridge/src/familiar/doctor.py`:

```python
"""`familiar doctor` — diagnose why the buddy isn't working, and say how to fix it.

READ-ONLY BY DESIGN. It never starts, stops, or repairs anything.

The failure that motivated this (2026-07-13) took hours and three wrong
hypotheses to find: the M5 had lost its side of the pairing bond while BlueZ
kept its own. It CANNOT be auto-fixed -- the firmware requires a human to type a
6-digit passkey off the stick (MITM protection, working as intended). So an
auto-fix would handle the easy cases, look like it worked, and leave the user
broken on the one that matters. We diagnose, and we print the exact commands.

diagnose() is PURE: facts in, findings out, no I/O. That is what makes every
scenario -- including the 2026-07-13 failure -- testable without hardware.
"""
from dataclasses import dataclass, field


@dataclass
class Finding:
    level: str                              # "ok" | "warn" | "error"
    title: str
    why: str
    remedy: list[str] = field(default_factory=list)


_REPAIR = [
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


def _repair_steps(addr):
    a = addr or "<MAC>"
    return [line.replace("{addr}", a) for line in _REPAIR]


def diagnose(facts: dict) -> list[Finding]:
    """Facts in, findings out. PURE — never do I/O here."""
    out = []
    cfg = facts.get("config") or {}
    svc = facts.get("service") or {}
    adapter = facts.get("adapter") or {}
    dev = facts.get("device") or {}
    log = facts.get("log") or {}
    addr = cfg.get("address")

    # --- nothing configured -------------------------------------------------
    if cfg.get("mode") == "none":
        out.append(Finding(
            "error", "Nothing is configured",
            "No M5 address and no Tidbyt keys, so the daemon has nothing to drive.",
            ["familiar init"]))
        return out

    # --- service ------------------------------------------------------------
    if svc.get("active") is False:
        out.append(Finding(
            "error", "The service is not running",
            "familiar.service is installed but not active, so nothing is "
            "feeding the buddy.",
            ["systemctl --user start familiar"]
            if svc.get("installed") else ["familiar init --service"]))
    elif svc.get("active") is None:
        out.append(Finding(
            "warn", "Could not check the service",
            "systemctl did not answer. If you run the daemon by hand, this is "
            "expected.", []))

    if svc.get("active") and (svc.get("manual_procs") or 0) > 0:
        out.append(Finding(
            "error", "Two instances are running",
            "A manual `familiar run` is up alongside the service. Only ONE BLE "
            "connection to the stick is possible at a time, so the two fight and "
            "the symptoms look random.",
            ["# find it, then kill it BY PID:",
             "pgrep -af 'familiar run'",
             "kill <PID>",
             "# never `pkill -f familiar` — the pattern matches its own shell"]))

    # --- BLE (only when an M5 is actually configured) ------------------------
    if cfg.get("mode") == "ble" and addr:
        if not facts.get("have_bluetoothctl"):
            out.append(Finding(
                "warn", "Could not check Bluetooth",
                "bluetoothctl is not installed, so the pairing and adapter "
                "checks were skipped.", []))
        else:
            # The adapter first: a re-pair CANNOT work while it is not pairable,
            # so telling the user to re-pair before this is fixed sends them into
            # a loop that cannot terminate.
            if adapter.get("powered") is False:
                out.append(Finding(
                    "error", "The Bluetooth adapter is powered off",
                    "Nothing can connect until it is on.",
                    ["bluetoothctl power on"]))
            if adapter.get("pairable") is False:
                out.append(Finding(
                    "error", "The adapter is not pairable",
                    "BlueZ answers every pairing attempt with 'Pairing not "
                    "supported' while Pairable is no. GNOME leaves it off, and an "
                    "adapter power-cycle resets it. No re-pair can succeed until "
                    "this is fixed.",
                    ["bluetoothctl pairable on"]))

            connected = dev.get("connected")
            recently = log.get("connected_recently")
            not_found = log.get("not_found") or 0
            discover_fails = log.get("discover_failures") or 0

            # Phantom: BlueZ holds a link the daemon cannot use.
            if connected is True and recently is False and not_found > 0:
                out.append(Finding(
                    "error", "Phantom link",
                    "BlueZ reports the stick as connected, but the daemon cannot "
                    "find it — a connected peripheral stops advertising. Something "
                    "left a stale link behind.",
                    [f"bluetoothctl disconnect {addr}"]))

            # One-sided bond: the M5 lost its keys, we kept ours.
            elif dev.get("paired") is False or (
                    discover_fails > 0 and recently is False):
                out.append(Finding(
                    "error", "The M5 is not paired (a one-sided bond)",
                    "The stick has lost its pairing keys (its screen shows "
                    "'discover') while BlueZ still holds its own. Every connect "
                    "then goes: link up -> the M5 demands encryption -> BlueZ "
                    "answers 'Pairing not supported' -> the M5 hangs up. "
                    "`bluetoothctl disconnect` CANNOT help: it clears a stale "
                    "link, not stale keys. This needs a human — the firmware "
                    "requires a 6-digit passkey typed off the stick.",
                    _repair_steps(addr)))

            elif dev.get("known") is False:
                out.append(Finding(
                    "warn", "The stick is not advertising",
                    "BlueZ has never seen it. It may be asleep or flat.",
                    ["# press any button on the stick; check it is charged",
                     "# and that bluetooth is on in its settings menu"]))

            elif connected is None:
                out.append(Finding(
                    "warn", "Could not check the link",
                    "bluetoothctl did not answer for this device.", []))

    # Only claim health if we actually managed to CHECK. If some checks could
    # not run, the warnings above stand on their own -- announcing "everything
    # looks healthy" when we could not look is exactly the false confidence this
    # tool exists to prevent.
    if any(f.level in ("error", "warn") for f in out):
        return out

    bits = [f"mode={cfg.get('mode')}"]
    if cfg.get("haiku"):
        bits.append("haiku on")
    if cfg.get("tidbyt"):
        bits.append("tidbyt on")
    if cfg.get("mode") == "ble":
        bits.append("connected" if dev.get("connected") else "not connected")
    out.append(Finding("ok", "Everything looks healthy", ", ".join(bits), []))
    return out
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `cd linux-bridge && uv run pytest -q`

Expected: PASS — 208 (198 existing + 10 new). No existing test may break.

- [ ] **Step 5: Commit**

```bash
git add linux-bridge/src/familiar/doctor.py linux-bridge/tests/test_doctor.py
git commit -m "feat: the pure diagnostic core behind familiar doctor

diagnose() is a pure function from facts to findings, so every failure --
including the 2026-07-13 one-sided bond that took hours and three wrong
hypotheses to find -- is unit-testable with no Bluetooth and no hardware.

The one-sided-bond remedy includes the two steps everyone misses:
`pairable on` (no pairing can succeed without it) and `agent KeyboardOnly`
(the firmware demands a human-typed passkey; no daemon can self-heal it)."
```

---

### Task 2: Collect the facts, ship the command

**Files:**
- Modify: `linux-bridge/src/familiar/doctor.py` (add `collect`, `render`, `main`)
- Modify: `linux-bridge/src/familiar/cli.py` (dispatch + help)
- Modify: `linux-bridge/README.md`
- Test: `linux-bridge/tests/test_doctor.py`

**Interfaces:**
- Consumes (from Task 1): `doctor.Finding(level, title, why, remedy)` and
  `doctor.diagnose(facts) -> list[Finding]`, plus the fixed facts-dict shape above.
- Produces: `doctor.collect(cfg) -> dict` (the facts), `doctor.main(argv=None) -> int`.

**Background.** `collect()` is the only part that touches the outside world, and it must never
raise: a missing `bluetoothctl`, absent systemd, denied journal access, an unparseable config —
each degrades that fact to `None`, and `diagnose` (already written) reports "couldn't
determine X" rather than inventing a cause.

The daemon's own log strings are stable and are what the counts key off (`ble.py:220-253`):
`"failed to discover services"`, `"was not found"`, `"clearing a possible stale link"`,
`"connected "`.

- [ ] **Step 1: Write the failing tests**

Append to `linux-bridge/tests/test_doctor.py`:

```python
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
            return "unexpected SMP command 0x0b from f0:16\n" * 3
        if cmd[0] == "journalctl":
            return ("[familiar] disconnected: failed to discover services\n" * 5 +
                    "[familiar] clearing a possible stale link to AA:BB\n")
        if cmd[0] == "pgrep":
            return ""
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
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
```

Add `from familiar.config import Config` to the test file's top import block.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_doctor.py -q`

Expected: FAIL — `AttributeError: module 'familiar.doctor' has no attribute '_run'`.

- [ ] **Step 3: Implement `_run` and `collect`**

Add to `linux-bridge/src/familiar/doctor.py` (imports into the existing top block):

```python
import re
import subprocess

from .config import load as load_config


def _run(cmd, timeout=10) -> str:
    """Run a command, return stdout. Raises on any failure — callers catch."""
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, check=False).stdout


def _try(fn, default=None):
    """Best-effort: any failure means 'couldn't determine', never a crash."""
    try:
        return fn()
    except Exception:
        return default


def _yesno(text, field):
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith(field + ":"):
            v = line.split(":", 1)[1].strip().lower()
            return True if v == "yes" else (False if v == "no" else None)
    return None


def collect(cfg) -> dict:
    """Gather the facts. NEVER raises: an undeterminable fact is None."""
    have_btctl = _try(
        lambda: bool(_run(["bluetoothctl", "--version"])), False) or False

    adapter = {"powered": None, "pairable": None}
    if have_btctl:
        show = _try(lambda: _run(["bluetoothctl", "show"]), "") or ""
        adapter = {"powered": _yesno(show, "Powered"),
                   "pairable": _yesno(show, "Pairable")}

    device = {"known": None, "paired": None, "bonded": None,
              "trusted": None, "connected": None}
    if have_btctl and cfg.address:
        info = _try(lambda: _run(["bluetoothctl", "info", cfg.address]), "") or ""
        known = "Paired:" in info
        device = {"known": known if info else None,
                  "paired": _yesno(info, "Paired"),
                  "bonded": _yesno(info, "Bonded"),
                  "trusted": _yesno(info, "Trusted"),
                  "connected": _yesno(info, "Connected")}

    active = _try(
        lambda: _run(["systemctl", "--user", "is-active",
                      "familiar.service"]).strip() == "active")
    installed = _try(
        lambda: "familiar.service" in _run(
            ["systemctl", "--user", "list-unit-files", "familiar.service"]))
    manual = _try(
        lambda: len([l for l in _run(["pgrep", "-af", "familiar run"]).splitlines()
                     if l.strip()]))

    kern = _try(lambda: _run(
        ["journalctl", "-k", "--since", "-10min", "--no-pager"]), "") or ""
    smp = len(re.findall(r"unexpected SMP command", kern)) if kern else None

    jlog = _try(lambda: _run(
        ["journalctl", "--user", "-u", "familiar.service",
         "--since", "-10min", "--no-pager"]), "") or ""
    log = {"discover_failures": None, "not_found": None,
           "phantom_clears": None, "connected_recently": None}
    if jlog:
        log = {
            "discover_failures": jlog.count("failed to discover services"),
            "not_found": jlog.count("was not found"),
            "phantom_clears": jlog.count("clearing a possible stale link"),
            "connected_recently": "[familiar] connected " in jlog,
        }

    mode = ("ble" if cfg.address
            else "tidbyt" if (cfg.tidbyt_device_id and cfg.tidbyt_api_key)
            else "none")
    return {
        "config": {"parsed": True, "mode": mode, "address": cfg.address,
                   "haiku": bool(cfg.api_key),
                   "tidbyt": bool(cfg.tidbyt_device_id and cfg.tidbyt_api_key)},
        "service": {"installed": installed, "active": active,
                    "manual_procs": manual},
        "have_bluetoothctl": have_btctl,
        "adapter": adapter,
        "device": device,
        "kernel_smp_errors": smp,
        "log": log,
    }
```

- [ ] **Step 4: Implement `render` and `main`**

Append to `linux-bridge/src/familiar/doctor.py`:

```python
_MARK = {"ok": "OK  ", "warn": "??  ", "error": "!!  "}


def render(findings) -> str:
    lines = []
    for f in findings:
        lines.append(f"{_MARK.get(f.level, '    ')}{f.title}")
        if f.why:
            lines.append(f"      {f.why}")
        if f.remedy:
            lines.append("")
            lines.extend(f"      {r}" if r else "" for r in f.remedy)
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="familiar doctor",
        description="Diagnose why the buddy isn't working. Read-only: this "
                    "never starts, stops, or repairs anything.")
    ap.parse_args(argv)

    cfg = _try(load_config)
    if cfg is None:
        print("!!  Could not read the config\n"
              "      ~/.config/familiar/config.toml is missing or unparseable.\n\n"
              "      familiar init")
        return 1

    findings = diagnose(collect(cfg))
    print(render(findings))
    return 1 if any(f.level == "error" for f in findings) else 0
```

- [ ] **Step 5: Add the CLI dispatch**

In `linux-bridge/src/familiar/cli.py`: add `doctor` to the import
(`from . import archive, daemon, doctor, hook, init`), add a `_HELP` line after the
`haikus` one:

```
  familiar doctor              diagnose why the buddy isn't connecting
```

and a dispatch branch before the unknown-command fallback:

```python
    if cmd == "doctor":
        return doctor.main(rest)
```

- [ ] **Step 6: Document it in the bridge README**

In `linux-bridge/README.md`, immediately before the existing troubleshooting section (the one
containing "`disconnected: Device with address ... was not found`" at line ~137), insert:

```markdown
## Something's wrong?

```bash
familiar doctor
```

It checks the config, the service, the Bluetooth adapter, the pairing bond, and
the recent logs, then names the cause and prints the exact commands to fix it.
It is read-only — it never changes anything. Exit code 0 = healthy, 1 = a problem
was found.

The cases below are what it detects; you shouldn't normally need to work through
them by hand.
```

- [ ] **Step 7: Run the full suite**

Run: `cd linux-bridge && uv run pytest -q`

Expected: PASS — 212 (208 + 4 new).

- [ ] **Step 8: Commit**

```bash
git add linux-bridge/src/familiar/doctor.py linux-bridge/src/familiar/cli.py linux-bridge/README.md linux-bridge/tests/test_doctor.py
git commit -m "feat: familiar doctor — name the cause, print the fix

collect() shells out to systemctl/bluetoothctl/journalctl and NEVER raises:
every undeterminable fact becomes None, and diagnose() reports 'couldn't
check X' rather than inventing a cause. Exit 0 healthy, 1 on a problem."
```

---

### Task 3: Verify it live

**Files:** none. Run by the controller.

- [ ] **Step 1: Redeploy and run it healthy**

```bash
uv tool install --force --reinstall ./linux-bridge
familiar doctor; echo "exit=$?"
```

Expected: reports healthy (the buddy is currently connected), `exit=0`.

- [ ] **Step 2: Prove it catches a real fault**

Stop the service and re-run — this is the cheapest real fault to induce, and it must be
caught rather than reported as healthy:

```bash
systemctl --user stop familiar
familiar doctor; echo "exit=$?"
systemctl --user start familiar
```

Expected: the service finding, a `systemctl --user start familiar` remedy, and `exit=1`.

**If it still says "healthy", the collectors are not wired to the diagnoses** — the pure tests
would still pass, so this step is the only thing that catches that.

---

## Notes for the implementer

- **`diagnose()` must stay pure.** If you find yourself importing `subprocess` into it, stop:
  the tests are the whole value here, and they only work because it does no I/O.
- **`collect()` must never raise**, however tempting a bare `_run` looks. The person running
  this is already confused; a traceback from the diagnostic tool is the worst possible output.
- **Never suggest `pkill -f familiar`** in any remedy — the pattern matches its own shell and
  kills the caller. There is a test.
- **Do not add `--fix`, `start`, `stop`, `restart`, `logs`, or `redeploy`.** The spec rejects
  them explicitly, with reasons.
