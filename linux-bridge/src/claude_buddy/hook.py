import json
import os
import re
import socket
import sys

from .config import load

_SIMPLE = {
    "session-start": "session_start",
    "session-end": "session_end",
}


def _project(cwd) -> str:
    # A short, glanceable project code for the [tag] prefix: initials of a
    # multi-word name (claude-desktop-buddy -> CDB), else the first 4 chars of a
    # single word (webapp -> weba). "" when cwd is absent.
    if not cwd:
        return ""
    name = os.path.basename(str(cwd).rstrip("/"))
    parts = [p for p in re.split(r"[-_. ]+", name) if p]
    if len(parts) >= 2:
        return "".join(p[0] for p in parts)[:4].upper()
    return name[:4]


def map_event(event: str, data: dict) -> dict | None:
    sid = data.get("session_id")
    if not sid:
        return None
    if event == "post-tool":
        # Tool calls don't add a feed line anymore (too noisy); they only keep
        # the session busy / clear the waiting alert. Just carry the project.
        return {"event": "post_tool", "session_id": sid,
                "project": _project(data.get("cwd"))}
    if event == "notification":
        # Claude Code fires Notification both for permission prompts and for the
        # ~60s "waiting for your input" idle nudge. Only the former is an alert;
        # ignore the idle one so the buddy doesn't false-trigger attention.
        if "waiting for your input" in str(data.get("message", "")).lower():
            return None
        return {"event": "notification", "session_id": sid,
                "project": _project(data.get("cwd"))}
    if event == "prompt-submit":
        return {"event": "prompt_submit", "session_id": sid,
                "project": _project(data.get("cwd"))}
    if event == "stop":
        # Send the transcript path; the daemon polls it for the final reply,
        # which may be flushed shortly AFTER this hook fires.
        return {"event": "stop", "session_id": sid,
                "project": _project(data.get("cwd")),
                "transcript_path": data.get("transcript_path", "")}
    name = _SIMPLE.get(event)
    if name is None:
        return None
    return {"event": name, "session_id": sid}


def send(payload: dict, socket_path: str, timeout: float = 0.2) -> None:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(socket_path)
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        s.close()
    except Exception:
        pass  # never disrupt a Claude Code hook


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv
    try:
        event = argv[1]
        raw = sys.stdin.read() or "{}"
        data = json.loads(raw) if raw.strip() else {}
        payload = map_event(event, data)
        if payload is not None:
            sock = os.environ.get("CLAUDE_BUDDY_SOCKET") or load().socket_path
            send(payload, sock)
    except Exception:
        pass
    return 0
