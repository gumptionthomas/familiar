import asyncio
import json
from claude_buddy.state import SessionStore
from claude_buddy.transport import FakeTransport
from claude_buddy import daemon


def test_apply_event_dispatches():
    s = SessionStore()
    daemon.apply_event(s, {"event": "prompt_submit", "session_id": "a"})
    daemon.apply_event(s, {"event": "post_tool", "session_id": "a",
                           "project": "buddy"})
    snap = s.snapshot()
    assert snap["running"] == 1
    # post_tool adds no feed line; only "thinking..." from prompt_submit shows
    assert snap["entries"] == ["thinking..."]


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


def test_speak_pushes_reply_from_transcript(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(
        json.dumps({"type": "user",
                    "message": {"role": "user", "content": "go"}}) + "\n" +
        json.dumps({"type": "assistant",
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": "All done"}]}}) + "\n")

    async def scenario():
        s = SessionStore()   # reply mode (no compose)
        b = daemon.Bridge(s, FakeTransport(), "/tmp/unused.sock")
        await b._on_stop("s1", "buddy", str(t))
        return s.snapshot()

    snap = asyncio.run(scenario())
    # single project in the feed -> untagged
    assert snap["entries"][-1] == "All done"


def test_haiku_tick_composes_and_sets(tmp_path):
    captured = {}

    async def compose(digest):
        captured["digest"] = digest
        return ["files mend now", "a branch returns home", "the tests all pass"]

    async def scenario():
        s = SessionStore(haiku_mode=True)
        s.prompt_submit("s1", project="GH")
        s.post_tool("s1", project="GH", tool="Edit", file="auth.py")
        s.stop("s1", project="GH")
        b = daemon.Bridge(s, FakeTransport(), "/tmp/unused.sock", compose=compose)
        await b._haiku_tick("s1", force=True)
        return s.snapshot()

    snap = asyncio.run(scenario())
    assert snap["entries"] == ["files mend now", "a branch returns home", "the tests all pass"]
    assert "auth.py" in captured["digest"]


def test_haiku_tick_no_compose_is_noop():
    async def scenario():
        s = SessionStore(haiku_mode=True)
        b = daemon.Bridge(s, FakeTransport(), "/tmp/unused.sock", compose=None)
        await b._haiku_tick("s1", force=True)
        return s.snapshot()

    assert asyncio.run(scenario())["entries"] == []


def test_on_stop_credits_output_tokens(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(
        json.dumps({"type": "user",
                    "message": {"role": "user", "content": "go"}}) + "\n" +
        json.dumps({"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "All done"}],
            "usage": {"output_tokens": 321}}}) + "\n")

    async def scenario():
        s = SessionStore()   # reply mode (no compose)
        b = daemon.Bridge(s, FakeTransport(), "/tmp/unused.sock")
        await b._on_stop("s1", "buddy", str(t))
        return s.snapshot()

    assert asyncio.run(scenario())["tokens"] == 321
