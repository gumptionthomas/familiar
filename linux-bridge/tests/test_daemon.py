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


def test_maybe_roll_today_resets_on_date_change():
    import datetime
    s = SessionStore()
    b = daemon.Bridge(s, FakeTransport(), "/tmp/unused.sock")
    s.add_tokens(100)
    b._maybe_roll_today()                       # latch today, no reset
    assert s.snapshot()["tokens_today"] == 100
    b._today_date = datetime.date(2000, 1, 1)   # pretend last seen was long ago
    b._maybe_roll_today()                       # date rolled -> reset today
    snap = s.snapshot()
    assert snap["tokens_today"] == 0
    assert snap["tokens"] == 100                # cumulative preserved


def _bridge_tb(idle_assets=None):
    if idle_assets is None:
        idle_assets = ["idle_%d" % i for i in range(9)]   # bufo default
    tb = {"device_id": "d", "api_token": "t", "pixlet": "pixlet",
          "app_path": "/a.star", "asset_dir": "/assets", "idle_assets": idle_assets}
    return daemon.Bridge(SessionStore(), FakeTransport(), "/tmp/x.sock", tidbyt=tb)


def test_persona_mapping():
    b = _bridge_tb()
    assert b._persona({"waiting": 1, "running": 1}, 100.0) == "attention"
    assert b._persona({"waiting": 0, "running": 1}, 100.0) == "busy"
    assert b._persona({"waiting": 0, "running": 0}, 100.0) == "idle"


def test_persona_celebrate_pulse_then_expires():
    b = _bridge_tb()
    assert b._persona({"running": 1, "completed": True}, 100.0) == "celebrate"
    assert b._persona({"running": 1, "completed": False}, 103.0) == "celebrate"
    assert b._persona({"running": 1, "completed": False}, 106.0) == "busy"


def test_tidbyt_decide_haiku_event_blocks_buddy():
    b = _bridge_tb()
    b._tb_haiku_until = 200.0
    assert b._tidbyt_decide({"running": 1}, 150.0) is None
    assert b._tidbyt_decide({"running": 1}, 201.0) == "busy"


def test_tidbyt_decide_idle_rotates_sequentially():
    b = _bridge_tb()
    b.tb_idle_refresh = 10.0
    assert b._tidbyt_decide({}, 100.0) == "idle_0"
    assert b._tidbyt_decide({}, 105.0) == "idle_0"   # within window
    assert b._tidbyt_decide({}, 111.0) == "idle_1"   # advanced


def test_tidbyt_decide_single_idle_no_rotation():
    # ASCII pets ship one animated idle.webp -> always "idle", never rotate.
    b = _bridge_tb(idle_assets=["idle"])
    b.tb_idle_refresh = 10.0
    assert b._tidbyt_decide({}, 100.0) == "idle"
    assert b._tidbyt_decide({}, 130.0) == "idle"
    assert b._tb_idle_idx is None       # rotation state untouched


def test_tidbyt_assets_selects_pet_and_falls_back(tmp_path):
    root = tmp_path / "tidbyt_buddy"
    (root / "capybara").mkdir(parents=True)
    for n in ("idle_0", "idle_1", "busy"):       # bufo at root
        (root / (n + ".webp")).write_bytes(b"")
    (root / "capybara" / "idle.webp").write_bytes(b"")

    d, idle = daemon._tidbyt_assets(str(tmp_path), "capybara")
    assert d.endswith("/capybara") and idle == ["idle"]

    d, idle = daemon._tidbyt_assets(str(tmp_path), "bufo")
    assert d == str(root) and idle == ["idle_0", "idle_1"]

    d, idle = daemon._tidbyt_assets(str(tmp_path), "nonesuch")   # unknown -> bufo
    assert d == str(root) and idle == ["idle_0", "idle_1"]


def test_tidbyt_haiku_waits_for_celebration(monkeypatch):
    # A new haiku holds off until the celebration window closes, so the Tidbyt
    # confetti isn't cut off after <1s.
    b = _bridge_tb()
    pushed_at = []

    async def fake_push(lines, **kw):
        pushed_at.append(b._loop_time())
        return True

    monkeypatch.setattr(daemon.tidbyt, "push", fake_push)

    async def go():
        start = b._loop_time()
        b._tb_celebrate_until = start + 0.2
        await b._tidbyt_haiku(["a", "b", "c"])
        return start

    start = asyncio.run(go())
    assert pushed_at and pushed_at[0] - start >= 0.18      # waited the window out
    assert b._tb_haiku_until > 0 and b._tb_current == "haiku"


def test_tidbyt_haiku_no_wait_without_celebration(monkeypatch):
    b = _bridge_tb()
    pushed_at = []

    async def fake_push(lines, **kw):
        pushed_at.append(b._loop_time())
        return True

    monkeypatch.setattr(daemon.tidbyt, "push", fake_push)

    async def go():
        b._tb_celebrate_until = b._loop_time() - 5      # already elapsed
        start = b._loop_time()
        await b._tidbyt_haiku(["a", "b", "c"])
        return start

    start = asyncio.run(go())
    assert pushed_at and pushed_at[0] - start < 0.1      # pushed promptly
