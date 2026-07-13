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
import subprocess
from dataclasses import dataclass, field

from .config import load as load_config


@dataclass
class Finding:
    level: str                              # "ok" | "warn" | "error"
    title: str
    why: str
    remedy: list[str] = field(default_factory=list)
    # True only for a "couldn't check / couldn't determine" warning. A warn
    # about a KNOWN state (e.g. pairable=no) must NOT block the health
    # summary -- only genuine gaps in what we could check should.
    blocks_health: bool = False


# `device.connected` (from `bluetoothctl info`, right now) is an INSTANTANEOUS
# sample. `log.discover_failures` / `log.not_found` / `kernel_smp_errors` are
# counts over the last 10 minutes -- a WINDOW. During the real 2026-07-13
# failure the daemon loops connect -> GATT discovery fails -> disconnect ->
# back off, so BlueZ genuinely reports Connected: yes for the seconds the link
# is up. A trigger gated on `connected is not True` can sample mid-loop, see
# "connected", and skip every diagnosis -- which is exactly how this command's
# namesake failure printed "healthy" with 400 discovery failures and the
# kernel's SMP fingerprint sitting unread. THE RULE: an instantaneous sample
# must never veto windowed evidence. The window alone decides whether the
# link is failing; the instant sample is consulted only to tell WHICH
# failure it is (see `failing` below).

# Minimum `discover_failures` in the window that counts as corroborating
# evidence for a one-sided bond WHEN the kernel log also shows SMP errors for
# our MAC. (A much higher count, with no kernel corroboration at all, is
# damning on its own -- see BOND_FAILURES_ALONE.)
BOND_MIN_FAILURES = 3
# `discover_failures` alone, with no kernel corroboration (e.g. an unreadable
# journal), that is damning on volume alone.
BOND_FAILURES_ALONE = 10
# Below this many discover_failures / not_found events in the window, treat
# the log as noise -- not evidence the link is actually failing.
LINK_FAILING_MIN = 3


def _meets(x, n):
    """True only if `x` is a KNOWN count that is >= n.

    An unknown (`None`) count never satisfies a threshold -- but that is not
    the same as treating it as 0/clean. `None` here just means this local
    trigger cannot fire from this signal; the structural guarantee that we
    never call the result "healthy" comes from `_Facts.need()` below, which
    independently records a `blocks_health` warning the moment the very same
    `None` is read. The two mechanisms are deliberately separate: this one
    decides whether a SPECIFIC diagnosis fires, that one decides whether
    we're allowed to claim things are fine.
    """
    return x is not None and x >= n


class _Facts:
    """Fact reader that records what it could not determine.

    Five Criticals across four review rounds were all the same bug: a `None`
    silently read as "fine" at a site the author forgot. The previous attempt
    replaced per-branch vigilance with a hand-maintained allowlist
    (`_UNKNOWN_LABELS` + a `checks` list, since removed) -- which just moved
    the forgetting one layer up: it, too, missed a key (`service.manual_procs`,
    which `collect()` explicitly sets to `None` on a `pgrep` failure/timeout;
    the allowlist let that `None` become "no second instance" -> "Everything
    looks healthy" while two daemons fought over the one BLE connection).

    So: reading a fact through `.need()` IS declaring it required. If it is
    unknown, that is recorded automatically and the health claim is
    suppressed. You cannot forget a fact you did not read, and you cannot
    read one without registering it.
    """

    def __init__(self, facts):
        self._f = facts or {}
        self.unknown = []                # labels of required facts we could not determine

    def need(self, section, key, label):
        """Read a REQUIRED fact. An unknown one auto-registers and suppresses health."""
        val = (self._f.get(section) or {}).get(key)
        if val is None and label not in self.unknown:
            self.unknown.append(label)
        return val

    def opt(self, section, key):
        """Read an OPTIONAL fact (corroboration, or remedy-text refinement
        only -- never gates whether a diagnosis fires). Unknown is fine here.
        """
        return (self._f.get(section) or {}).get(key)

    def top(self, key, default=None):        # top-level, optional
        return self._f.get(key, default)


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
    """Facts in, findings out. PURE — never do I/O here.

    This is a thin wrapper around `_diagnose` that enforces one structural
    guarantee: the result is NEVER empty. `_diagnose` returning `[]` means
    every trigger stayed silent AND we were not allowed to claim health
    either (see the early-return sites inside it) -- that combination used
    to print a blank line and exit 0 on a genuinely dead stick. A blank
    report is the worst output a diagnostic tool can give, so this is
    enforced here once, structurally, rather than by remembering to append a
    fallback at every return site inside `_diagnose`.
    """
    out = _diagnose(facts)
    if out:
        return out
    dev = (facts.get("device") or {})
    state = ("not connected" if dev.get("connected") is False
              else "in a state we could not classify")
    return [Finding(
        "warn", "No specific fault found, but this is not healthy",
        f"Nothing matched a known diagnosis, and the buddy is {state}, so "
        "this cannot be reported as healthy either. Try again in a minute, "
        "or check the service log directly:",
        ["journalctl --user -u familiar.service -n 50"],
        blocks_health=True)]


def _diagnose(facts: dict) -> list[Finding]:
    out = []
    F = _Facts(facts)
    # `config` is never unknown -- `main()` already refused to call `diagnose`
    # at all if the config file itself could not be parsed (see `main`
    # below), so every field here is a determined value, not a gap to sweep.
    cfg = F.top("config") or {}
    addr = cfg.get("address")

    # --- nothing configured -------------------------------------------------
    if cfg.get("mode") == "none":
        out.append(Finding(
            "error", "Nothing is configured",
            "No M5 address and no Tidbyt keys, so the daemon has nothing to drive.",
            ["familiar init"]))
        return out

    # --- service ------------------------------------------------------------
    active = F.need("service", "active", "whether the service is running")
    if active is False:
        # `installed` only chooses WHICH remedy line to print -- the decision
        # to fire this finding was already made from `active` above, so an
        # unknown `installed` is not itself a gap worth its own warning.
        out.append(Finding(
            "error", "The service is not running",
            "familiar.service is installed but not active, so nothing is "
            "feeding the buddy.",
            ["systemctl --user start familiar"]
            if F.opt("service", "installed") else ["familiar init --service"]))
    # active is None: F.need() already registered it above -- no bare guard needed here.

    if active:
        manual_procs = F.need(
            "service", "manual_procs",
            "whether a second manual instance is running")
        # `collect()` sets manual_procs=None on a `pgrep` failure/timeout.
        # `_meets` treats that as "does not meet the threshold" (never as 0),
        # so it can never fabricate this finding -- the `.need()` call above
        # has already registered the gap, which is what stops an uncountable
        # manual process from silently reading as "no second instance".
        if _meets(manual_procs, 1):
            # `manual_pids` only chooses which remedy text to print (named
            # PIDs vs. the manual pgrep recipe); the fire decision was
            # already made from `manual_procs` above.
            pids = F.opt("service", "manual_pids")
            if pids:
                kill_lines = [f"kill {pid}" for pid in pids]
            else:
                # We know a manual instance exists but couldn't pin its PID down
                # (e.g. pgrep failed after the count was taken) -- fall back to
                # the manual recipe rather than guessing a PID.
                kill_lines = ["pgrep -af 'familiar run'   # exclude the service's "
                              "own PID (systemctl --user show familiar.service "
                              "-p MainPID)",
                              "kill <PID>"]
            out.append(Finding(
                "error", "Two instances are running",
                "A manual `familiar run` is up alongside the service. Only ONE BLE "
                "connection to the stick is possible at a time, so the two fight and "
                "the symptoms look random.",
                ["# kill the manual instance(s) below -- NEVER the service's own "
                 "PID, and never kill-by-name-match (it would also match the "
                 "shell running this very command):"] + kill_lines))

    # --- BLE (only when an M5 is actually configured) ------------------------
    # In `tidbyt`/`none` mode these facts are IRRELEVANT, not unknown -- so
    # this whole block, and every `F.need()` call inside it, is skipped. That
    # is what keeps a healthy Tidbyt-only setup from acquiring brand-new
    # false-positive warnings about facts that do not apply to it.
    connected = None
    if cfg.get("mode") == "ble" and addr:
        have_btctl = F.top("have_bluetoothctl", False)
        if not have_btctl:
            # Dedupe by cause: bluetoothctl itself is missing, so none of the
            # facts below can be read at all -- do not `.need()` any of them,
            # or a single missing binary would produce four warnings instead
            # of the one that actually explains it.
            out.append(Finding(
                "warn", "Could not check Bluetooth",
                "bluetoothctl is not installed, so the pairing and adapter "
                "checks were skipped.", [], blocks_health=True))
        else:
            connected = F.need(
                "device", "connected", "whether the stick is connected")
            paired = F.need("device", "paired", "whether the stick is paired")
            # NEVER coerce an unknown windowed count into a number: `None`
            # means "could not read the journal", not "it was clean". These
            # stay `None`-able all the way through; `_meets` treats an
            # unknown count as not meeting any threshold (so it cannot
            # fabricate a trigger), while `F.need()` above has ALREADY
            # registered the same `None` as a required-but-unknown fact -- so
            # a `None` window can never silently read as a healthy one.
            not_found = F.need("log", "not_found", "the daemon's recent log")
            discover_fails = F.need(
                "log", "discover_failures", "the daemon's recent log")
            # The ONE genuinely optional fact: corroboration only. An
            # unreadable kernel log is already covered by the log-volume
            # trigger (BOND_FAILURES_ALONE), so its absence does not need its
            # own warning -- it is never used on its own to justify health.
            smp = F.top("kernel_smp_errors")

            # The WINDOW decides whether the link is working. The
            # instantaneous `connected` sample must never veto it: during the
            # real failure the daemon loops connect -> discovery-fails ->
            # disconnect, so bluetoothctl legitimately says Connected: yes
            # for the seconds the link is up.
            failing = (_meets(discover_fails, LINK_FAILING_MIN)
                       or _meets(not_found, LINK_FAILING_MIN))

            # Grade the one-sided-bond evidence by confidence. NONE of these
            # branches consult `connected` -- that is the fix.
            bond_fires = (
                paired is False                                    # definitive
                or (failing and _meets(discover_fails, BOND_MIN_FAILURES)
                    and _meets(smp, 1))                            # fingerprint
                or (failing and _meets(discover_fails, BOND_FAILURES_ALONE)))

            # Only HERE does the instant sample matter, and only to
            # distinguish the failure type: a connected peripheral stops
            # advertising, so "connected" plus the daemon still reporting
            # "was not found" means BlueZ is holding a link the daemon can't
            # use.
            phantom_fires = (failing and connected is True
                              and _meets(not_found, 1))

            # A dead / flat / out-of-range stick: BlueZ keeps the paired
            # record forever, so this shows up as "was not found" climbing
            # with no confirmed connect -- not as a phantom (connected is not
            # True) and, below BOND_FAILURES_ALONE, not as a bond failure
            # either. Only reported when the bond finding didn't already
            # explain it.
            unreachable_fires = (
                failing and _meets(not_found, LINK_FAILING_MIN)
                and connected is not True and not bond_fires)

            # Failing, but none of the above -- the daemon's normal
            # backoff-and-retry. Never send someone to hand-pair on this: a
            # healthy long-lived link has NO "connected" line in the last 10
            # minutes either, since that is the steady state.
            flapping_fires = failing and not (
                bond_fires or phantom_fires or unreachable_fires)

            # The adapter first: a re-pair CANNOT work while it is not
            # pairable, so telling the user to re-pair before this is fixed
            # sends them into a loop that cannot terminate. But Pairable: no
            # is the GNOME DEFAULT and only blocks pairing a NEW device --
            # an existing bond connects fine, so it is only an error when a
            # re-pair is actually being advised.
            powered = F.need(
                "adapter", "powered", "whether the Bluetooth adapter is powered on")
            pairable = F.need(
                "adapter", "pairable", "whether the adapter is pairable")
            if powered is False:
                out.append(Finding(
                    "error", "The Bluetooth adapter is powered off",
                    "Nothing can connect until it is on.",
                    ["bluetoothctl power on"]))
            if pairable is False:
                if bond_fires:
                    out.append(Finding(
                        "error", "The adapter is not pairable",
                        "BlueZ answers every pairing attempt with 'Pairing "
                        "not supported' while Pairable is no. GNOME leaves it "
                        "off, and an adapter power-cycle resets it. No "
                        "re-pair can succeed until this is fixed first.",
                        ["bluetoothctl pairable on"]))
                else:
                    out.append(Finding(
                        "warn", "Bluetooth pairing is off (existing bonds "
                        "still work)",
                        "Pairable: no is the GNOME default. It only blocks "
                        "pairing a brand-new device -- an already-paired "
                        "stick like this one connects fine. Only turn this "
                        "on if you need to pair a new device.",
                        ["bluetoothctl pairable on"]))

            if phantom_fires:
                out.append(Finding(
                    "error", "Phantom link",
                    "BlueZ reports the stick as connected, but the daemon "
                    "cannot find it — a connected peripheral stops "
                    "advertising. Something left a stale link behind.",
                    [f"bluetoothctl disconnect {addr}"]))

            if bond_fires:
                out.append(Finding(
                    "error", "The M5 is not paired (a one-sided bond)",
                    "The stick has lost its pairing keys (its screen shows "
                    "'discover') while BlueZ still holds its own. Every "
                    "connect then goes: link up -> the M5 demands encryption "
                    "-> BlueZ answers 'Pairing not supported' -> the M5 hangs "
                    "up. `bluetoothctl disconnect` CANNOT help: it clears a "
                    "stale link, not stale keys. This needs a human — the "
                    "firmware requires a 6-digit passkey typed off the "
                    "stick.",
                    _repair_steps(addr)))

            if unreachable_fires:
                out.append(Finding(
                    "warn", "The stick is not reachable",
                    f"{not_found} recent 'was not found' with no confirmed "
                    "connect. The stick is off, flat, or out of range -- "
                    "BlueZ keeps a paired device's record forever, so this "
                    "isn't the same as losing the bond.",
                    ["# press any button on the stick; check it is charged",
                     "# and that bluetooth is on in its settings menu"],
                    blocks_health=True))

            elif flapping_fires:
                out.append(Finding(
                    "warn", "The link is flapping",
                    f"{discover_fails} recent 'failed to discover services' "
                    f"and {not_found} 'was not found', with no confirmed "
                    "reconnect yet. The daemon retries with backoff, and "
                    "this often clears on its own. Re-run `familiar doctor` "
                    "if it persists.", [], blocks_health=True))

            # connected is None (bluetoothctl didn't answer for this device)
            # was registered by F.need() above, not handled here.

    # Every fact read through F.need() above that turned out to be unknown is
    # now, automatically, a blocks_health warning -- not because someone
    # remembered to list it, but because reading it through .need() already
    # declared it required. This is what makes the class of bug closed: a
    # fact this function never reads cannot appear here, and a fact it DOES
    # read cannot be forgotten, because reading it is what registers it.
    for label in F.unknown:
        out.append(Finding(
            "warn", f"Could not determine {label}",
            "This could not be checked, so it cannot be read as fine -- an "
            "unknown fact is never evidence of health.", [],
            blocks_health=True))

    # Only claim health if we actually managed to CHECK. A warn about a KNOWN
    # state (e.g. pairable=no) is informative, not a gap -- it must not
    # suppress the summary. But every OTHER warn -- "couldn't check", "the
    # link is flapping", "the stick is not reachable" -- sets blocks_health,
    # and must suppress it: claiming "everything looks healthy" next to one
    # of those is exactly the false confidence this tool exists to prevent.
    if any(f.level == "error" or f.blocks_health for f in out):
        return out

    # Belt and suspenders: even with no error and no blocks_health finding
    # above, a BLE buddy that is sampled disconnected right now is not
    # "healthy" -- never print the ok summary next to "not connected". (If
    # `connected` were unknown here, F.unknown would already have forced the
    # `blocks_health` return above, so reaching this line in `ble` mode means
    # it is KNOWN -- True or False.)
    if cfg.get("mode") == "ble" and connected is False:
        return out

    bits = [f"mode={cfg.get('mode')}"]
    if cfg.get("haiku"):
        bits.append("haiku on")
    if cfg.get("tidbyt"):
        bits.append("tidbyt on")
    if cfg.get("mode") == "ble":
        bits.append("connected" if connected else "not connected")
    out.append(Finding("ok", "Everything looks healthy", ", ".join(bits), []))
    return out


def _run(cmd, timeout=5) -> str:
    """Run a command and return its stdout (possibly empty).

    Uses `check=False`, so a non-zero exit does NOT raise -- callers that
    care about the return code (e.g. the unknown-MAC path, which relies on
    rc=1 with populated stdout) must inspect stdout themselves. This can
    still raise for other reasons (command not found, timeout); callers
    that want a fully best-effort call should wrap it in `_try`.
    """
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


def _manual_daemon_pids(service_main_pid) -> list[int]:
    """PIDs of `familiar run` processes that are NOT the service itself.

    The systemd unit's ExecStart IS `familiar run`, so pgrep matches the
    service's own process too. Exclude it (by PID, not name -- a name match
    can't tell service and manual apart), and exclude this process and its
    parent so `familiar doctor` never counts itself. Returning the actual
    PIDs (not just a count) lets the remedy name the process to kill instead
    of sending the user to `pgrep` themselves, where the service's own PID
    is indistinguishable from a real second instance.
    """
    pids = [l.strip() for l in _run(["pgrep", "-f", "familiar run"]).splitlines()
            if l.strip()]
    exclude = {str(os.getpid()), str(os.getppid())}
    if service_main_pid:
        exclude.add(str(service_main_pid))
    return [int(p) for p in pids if p not in exclude]


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
    manual_pids = _try(lambda: _manual_daemon_pids(service_main_pid))
    manual = len(manual_pids) if manual_pids is not None else None

    # The kernel logs SMP errors for ANY BLE peripheral (a mouse, a headset),
    # so counting them unfiltered would be a brand-new false positive. Count
    # only lines naming OUR device's MAC -- the kernel prints it lowercase,
    # the config stores it uppercase, so compare case-insensitively.
    kern = _try(lambda: _run(
        ["journalctl", "-k", "--since", "-10min", "--no-pager"]), "") or ""
    addr_lower = (cfg.address or "").lower()
    if not kern or not addr_lower:
        smp = None
    else:
        smp = sum(1 for line in kern.splitlines()
                  if "unexpected SMP command" in line
                  and addr_lower in line.lower())

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
                    "manual_procs": manual,
                    "manual_pids": manual_pids or []},
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
