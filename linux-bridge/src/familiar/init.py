"""`familiar init` — write config, wire Claude Code hooks, optional service.
Interactive by default."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from .config import _default_config_path

EVENTS = {"SessionStart": "session-start", "UserPromptSubmit": "prompt-submit",
          "PostToolUse": "post-tool", "Notification": "notification",
          "Stop": "stop", "SessionEnd": "session-end"}
_MATCHER = {"PostToolUse": "*"}


def _entry(evt, name):
    grp = {"hooks": [{"type": "command", "command": f"familiar hook {name}"}]}
    if evt in _MATCHER:
        grp["matcher"] = _MATCHER[evt]
    return grp


def merge_hooks(settings: dict) -> dict:
    out = dict(settings)
    hooks = {k: list(v) for k, v in out.get("hooks", {}).items()}
    for evt, name in EVENTS.items():
        groups = hooks.get(evt, [])
        # drop any prior familiar group for this event (idempotent re-runs)
        kept = []
        for grp in groups:
            cmds = [h.get("command", "") for h in grp.get("hooks", [])]
            if any(c.startswith("familiar hook ") for c in cmds):
                continue
            kept.append(grp)
        kept.append(_entry(evt, name))
        hooks[evt] = kept
    out["hooks"] = hooks
    return out


def _settings_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "settings.json"


def _write_hooks(settings_path: Path):
    if settings_path.exists():
        cur = json.loads(settings_path.read_text() or "{}")
        settings_path.with_suffix(f".json.bak.{int(time.time())}").write_text(
            settings_path.read_text())
    else:
        cur = {}
        settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(merge_hooks(cur), indent=2) + "\n")


def _prompt(label, default=""):
    v = input(f"{label}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def _write_config(cfg_path: Path, values: dict):
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    existing = cfg_path.read_text() if cfg_path.exists() else ""
    lines = [existing.rstrip()] if existing else []
    for k, v in values.items():
        if v and f"{k} " not in existing and f"{k}=" not in existing:
            lines.append(f'{k} = "{v}"')
    cfg_path.write_text("\n".join(l for l in lines if l) + "\n")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="familiar init")
    ap.add_argument("--yes", action="store_true", help="non-interactive")
    ap.add_argument("--tidbyt-device"); ap.add_argument("--tidbyt-key")
    ap.add_argument("--m5-address"); ap.add_argument("--anthropic-key")
    ap.add_argument("--owner"); ap.add_argument("--service", action="store_true")
    a = ap.parse_args(argv)

    cfg_path = _default_config_path()
    interactive = not (a.yes or a.tidbyt_device)
    values = {
        "tidbyt_device_id": a.tidbyt_device or (_prompt("Tidbyt device id") if interactive else ""),
        "tidbyt_api_key": a.tidbyt_key or (_prompt("Tidbyt API key") if interactive else ""),
        "address": a.m5_address or (_prompt("M5 BLE address (blank if none)") if interactive else ""),
        "api_key": a.anthropic_key or (_prompt("Anthropic API key (blank to skip haikus)") if interactive else ""),
        "owner": a.owner or (_prompt("Your name", os.environ.get("USER", "")) if interactive else ""),
    }
    _write_config(cfg_path, values)
    _write_hooks(_settings_path())
    print(f"Wrote {cfg_path} and merged hooks into {_settings_path()}.")
    if a.service or (interactive and _prompt("Install systemd service? (y/N)").lower() == "y"):
        _install_service()
    print("Done. Start with `familiar run` (or the service).")
    return 0


def _install_service():
    unit = Path.home() / ".config/systemd/user/familiar.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Unit]\nDescription=Familiar desk buddy\nAfter=bluetooth.target\n\n"
        "[Service]\nExecStart=%h/.local/bin/familiar run\nRestart=on-failure\n"
        "RestartSec=3\nEnvironment=PYTHONUNBUFFERED=1\n\n"
        "[Install]\nWantedBy=default.target\n")
    # Hardcoded string; no user input reaches this call, so no injection risk.
    # subprocess.run would still need shell=True for the && compound command.
    os.system("systemctl --user daemon-reload && "  # noqa: S605
              "systemctl --user enable --now familiar.service")
