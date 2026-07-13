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
import os
import re
import subprocess
from dataclasses import dataclass, field

from .config import load as load_config


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
             "# do not kill-by-name-match here — the pattern would also match",
             "# the shell running this very command"]))

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


def _service_main_pid():
    """The systemd unit's own MainPID, or None if inactive/unknown.

    `systemctl show -p MainPID --value` prints "0" (or nothing) when the
    unit is not running, and the numeric PID otherwise.
    """
    out = _run(["systemctl", "--user", "show", "familiar.service",
                "-p", "MainPID", "--value"]).strip()
    pid = int(out) if out.isdigit() else 0
    return pid or None


def _manual_daemon_count(service_main_pid) -> int:
    """Count `familiar run` processes that are NOT the service itself.

    The systemd unit's ExecStart IS `familiar run`, so pgrep matches the
    service's own process too. Exclude it (by PID, not name -- a name match
    can't tell service and manual apart), and exclude this process and its
    parent so `familiar doctor` never counts itself.
    """
    pids = [l.strip() for l in _run(["pgrep", "-f", "familiar run"]).splitlines()
            if l.strip()]
    exclude = {str(os.getpid()), str(os.getppid())}
    if service_main_pid:
        exclude.add(str(service_main_pid))
    return len([p for p in pids if p not in exclude])


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

    # The systemd unit's own ExecStart IS `familiar run`, so a naive
    # `pgrep -f "familiar run"` also matches the service's own process. Find
    # that PID so we can exclude it -- otherwise doctor reports "Two
    # instances are running" to every user, every time the service is up.
    service_main_pid = _try(_service_main_pid)
    manual = _try(lambda: _manual_daemon_count(service_main_pid))

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
        "manual_procs": manual,
        "have_bluetoothctl": have_btctl,
        "adapter": adapter,
        "device": device,
        "kernel_smp_errors": smp,
        "log": log,
    }


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
