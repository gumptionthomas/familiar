import json
import os
import re
import socket
import sys

from .config import load

# Strip a leading "cd <path>" statement so Bash detail shows the real command,
# not the directory change most commands start with. The separator may be
# &&, ;, or a newline (multi-line commands) — [ \t]* before it so a trailing
# newline isn't pre-consumed.
_CD_PREFIX = re.compile(
    r"^cd\s+(?:\"[^\"]*\"|'[^']*'|\S+)[ \t]*(?:&&|;|\n)[ \t\n]*")

_SIMPLE = {
    "session-start": "session_start",
    "session-end": "session_end",
}


def _detail(tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return ""
    if "command" in tool_input:
        cmd = _CD_PREFIX.sub("", str(tool_input["command"]).lstrip())
        # Collapse remaining whitespace/newlines so a multi-line command shows
        # as one readable line.
        return " ".join(cmd.split())[:40]
    if "file_path" in tool_input:
        return os.path.basename(str(tool_input["file_path"]))
    return ""


def _project(cwd) -> str:
    # Project label = the working directory's basename, capped so the tagged
    # activity line stays readable on the 135px screen. "" when cwd is absent.
    if not cwd:
        return ""
    return os.path.basename(str(cwd).rstrip("/"))[:12]


def map_event(event: str, data: dict) -> dict | None:
    sid = data.get("session_id")
    if not sid:
        return None
    if event == "post-tool":
        return {"event": "post_tool", "session_id": sid,
                "tool": data.get("tool_name", "tool"),
                "detail": _detail(data.get("tool_input", {})),
                "project": _project(data.get("cwd"))}
    if event == "notification":
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
