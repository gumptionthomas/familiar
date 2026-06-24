import json
import os
import socket
import sys

from .config import load

_SIMPLE = {
    "session-start": "session_start",
    "prompt-submit": "prompt_submit",
    "notification": "notification",
    "stop": "stop",
    "session-end": "session_end",
}


def _detail(tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return ""
    if "command" in tool_input:
        return str(tool_input["command"])[:40]
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
