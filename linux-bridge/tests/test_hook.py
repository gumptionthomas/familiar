import json
import socket
import threading
from claude_buddy import hook


def test_map_post_tool_carries_tool_and_no_command():
    # tool kind + project, but NO command text (Bash has no file_path)
    data = {"session_id": "a", "tool_name": "Bash",
            "tool_input": {"command": "git push"}, "cwd": "/home/me/dev/webapp"}
    out = hook.map_event("post-tool", data)
    assert out == {"event": "post_tool", "session_id": "a", "project": "weba",
                   "tool": "Bash", "file": ""}


def test_map_post_tool_file_basename_only():
    data = {"session_id": "a", "tool_name": "Edit",
            "tool_input": {"file_path": "/home/me/dev/webapp/src/auth.py"}}
    out = hook.map_event("post-tool", data)
    assert out["tool"] == "Edit"
    assert out["file"] == "auth.py"   # basename only, no path


def test_map_post_tool_project_code_initials():
    # multi-word dir -> uppercase initials (capped at 4)
    out = hook.map_event("post-tool",
                         {"session_id": "a", "cwd": "/home/me/claude-desktop-buddy"})
    assert out["project"] == "CDB"


def test_map_post_tool_project_code_single_word():
    # single word -> first 4 chars, as-is
    out = hook.map_event("post-tool", {"session_id": "a", "cwd": "/home/me/dashboard"})
    assert out["project"] == "dash"


def test_map_post_tool_trailing_slash_cwd():
    out = hook.map_event("post-tool", {"session_id": "a", "cwd": "/home/me/webapp/"})
    assert out["project"] == "weba"


def test_map_post_tool_no_cwd():
    out = hook.map_event("post-tool", {"session_id": "a"})
    assert out == {"event": "post_tool", "session_id": "a", "project": "",
                   "tool": "tool", "file": ""}


def test_map_simple_events():
    assert hook.map_event("session-start", {"session_id": "a"}) == {
        "event": "session_start", "session_id": "a"}
    assert hook.map_event("session-end", {"session_id": "a"}) == {
        "event": "session_end", "session_id": "a"}


def test_map_prompt_submit_includes_project_and_prompt():
    out = hook.map_event("prompt-submit", {
        "session_id": "a", "cwd": "/x/webapp",
        "prompt": "  fix the   dash\n glyph  "})
    assert out == {"event": "prompt_submit", "session_id": "a",
                   "project": "weba", "prompt": "fix the dash glyph"}


def test_map_prompt_submit_no_prompt_empty():
    out = hook.map_event("prompt-submit", {"session_id": "a", "cwd": "/x/webapp"})
    assert out == {"event": "prompt_submit", "session_id": "a",
                   "project": "weba", "prompt": ""}


def test_map_stop_passes_transcript_path(tmp_path):
    out = hook.map_event("stop", {"session_id": "a", "cwd": "/x/webapp",
                                  "transcript_path": "/x/t.jsonl"})
    assert out == {"event": "stop", "session_id": "a",
                   "project": "weba", "transcript_path": "/x/t.jsonl"}


def test_map_notification_permission_alerts():
    out = hook.map_event("notification", {
        "session_id": "a", "cwd": "/x/webapp",
        "message": "Claude needs your permission to use Bash"})
    assert out == {"event": "notification", "session_id": "a", "project": "weba"}


def test_map_notification_idle_ignored():
    out = hook.map_event("notification", {
        "session_id": "a", "message": "Claude is waiting for your input"})
    assert out is None


def test_map_notification_includes_project():
    out = hook.map_event("notification", {"session_id": "a", "cwd": "/x/webapp"})
    assert out == {"event": "notification", "session_id": "a", "project": "weba"}


def test_map_notification_no_cwd_empty_project():
    out = hook.map_event("notification", {"session_id": "a"})
    assert out == {"event": "notification", "session_id": "a", "project": ""}


def test_map_ignores_no_session():
    assert hook.map_event("stop", {}) is None


def test_send_delivers_line(tmp_path):
    sock_path = str(tmp_path / "s.sock")
    received = []
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)

    def accept():
        conn, _ = srv.accept()
        received.append(conn.recv(1024))
        conn.close()

    t = threading.Thread(target=accept)
    t.start()
    hook.send({"event": "stop", "session_id": "a"}, sock_path)
    t.join(timeout=2)
    srv.close()
    assert json.loads(received[0]) == {"event": "stop", "session_id": "a"}


def test_send_swallows_errors_when_no_server(tmp_path):
    # No server listening -> must not raise.
    hook.send({"event": "stop", "session_id": "a"},
              str(tmp_path / "absent.sock"))


def test_main_always_returns_zero(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "a"})))
    monkeypatch.setenv("CLAUDE_BUDDY_SOCKET", str(tmp_path / "absent.sock"))
    assert hook.main(["claude-buddy-hook", "stop"]) == 0
