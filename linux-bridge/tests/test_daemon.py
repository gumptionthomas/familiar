import asyncio
import json
from claude_buddy.state import SessionStore
from claude_buddy.transport import FakeTransport
from claude_buddy import daemon


def test_apply_event_dispatches():
    s = SessionStore()
    daemon.apply_event(s, {"event": "prompt_submit", "session_id": "a"})
    daemon.apply_event(s, {"event": "post_tool", "session_id": "a",
                           "tool": "Bash", "detail": "ls"})
    snap = s.snapshot()
    assert snap["running"] == 1
    assert snap["entries"][0] == "Bash: ls"


def test_apply_event_ignores_unknown():
    s = SessionStore()
    daemon.apply_event(s, {"event": "bogus", "session_id": "a"})
    assert s.snapshot()["total"] == 0


def test_push_sends_encoded_snapshot():
    s = SessionStore()
    s.prompt_submit("a")
    t = FakeTransport()
    b = daemon.Bridge(s, t, "/tmp/unused.sock")
    asyncio.run(b.push())
    assert len(t.sent) == 1
    obj = json.loads(t.sent[0])
    assert obj["running"] == 1


def test_socket_event_reaches_store(tmp_path):
    async def scenario():
        s = SessionStore()
        t = FakeTransport()
        sock = str(tmp_path / "d.sock")
        b = daemon.Bridge(s, t, sock)
        server = await b.serve()
        reader, writer = await asyncio.open_unix_connection(sock)
        writer.write(json.dumps(
            {"event": "prompt_submit", "session_id": "a"}).encode() + b"\n")
        await writer.drain()
        writer.close()
        await asyncio.sleep(0.05)
        server.close()
        await server.wait_closed()
        return s.snapshot()

    snap = asyncio.run(scenario())
    assert snap["running"] == 1
