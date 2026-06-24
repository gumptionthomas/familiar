import json
import socket
import threading
from claude_buddy import hook


def test_map_post_tool_extracts_detail():
    data = {"session_id": "a", "tool_name": "Bash",
            "tool_input": {"command": "git push"}}
    out = hook.map_event("post-tool", data)
    assert out == {"event": "post_tool", "session_id": "a",
                   "tool": "Bash", "detail": "git push", "project": ""}


def test_map_post_tool_file_path_detail():
    data = {"session_id": "a", "tool_name": "Read",
            "tool_input": {"file_path": "/x/main.cpp"}}
    out = hook.map_event("post-tool", data)
    assert out == {"event": "post_tool", "session_id": "a",
                   "tool": "Read", "detail": "main.cpp", "project": ""}


def test_map_post_tool_project_from_cwd():
    data = {"session_id": "a", "tool_name": "Bash", "tool_input": {"command": "ls"},
            "cwd": "/home/me/dev/webapp"}
    out = hook.map_event("post-tool", data)
    assert out["project"] == "webapp"


def test_map_post_tool_project_truncated_to_12():
    data = {"session_id": "a", "tool_name": "Edit", "tool_input": {},
            "cwd": "/home/me/claude-desktop-buddy"}
    out = hook.map_event("post-tool", data)
    assert out["project"] == "claude-deskt"
    assert len(out["project"]) <= 12


def test_map_post_tool_trailing_slash_cwd():
    data = {"session_id": "a", "tool_name": "Bash", "tool_input": {},
            "cwd": "/home/me/webapp/"}
    out = hook.map_event("post-tool", data)
    assert out["project"] == "webapp"


def test_map_simple_events():
    assert hook.map_event("stop", {"session_id": "a"}) == {
        "event": "stop", "session_id": "a"}
    assert hook.map_event("notification", {"session_id": "a"}) == {
        "event": "notification", "session_id": "a"}


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
