from claude_buddy.state import SessionStore


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_idle_when_empty():
    s = SessionStore(clock=FakeClock())
    snap = s.snapshot()
    assert snap["total"] == 0
    assert snap["running"] == 0
    assert snap["waiting"] == 0
    assert snap["msg"] == "idle"
    assert snap["completed"] is False


def test_prompt_submit_shows_thinking():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a", project="CDB")
    snap = s.snapshot()
    # single project -> no tag prefix
    assert snap["entries"][-1] == "thinking..."
    assert snap["msg"] == "thinking..."


def test_stop_pulses_completed_no_message():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a", project="CDB")   # pushes "thinking..."
    s.stop("a")          # the reply arrives later via push_message
    snap = s.snapshot()
    assert snap["completed"] is True
    assert snap["entries"] == ["thinking..."]


def test_push_message_single_project_untagged():
    s = SessionStore(clock=FakeClock())
    s.push_message("CDB", "Merged and live")
    snap = s.snapshot()
    assert snap["entries"][-1] == "Merged and live"
    assert snap["msg"] == "Merged and live"


def test_push_message_tagged_when_feed_spans_projects():
    s = SessionStore(clock=FakeClock())
    s.push_message("CDB", "merged it")
    s.push_message("weba", "tests pass")
    # feed spans 2 projects -> both lines get their code
    assert s.snapshot()["entries"] == ["[CDB] merged it", "[weba] tests pass"]


def test_running_after_prompt():
    s = SessionStore(clock=FakeClock())
    s.session_start("a")
    s.prompt_submit("a")
    snap = s.snapshot()
    assert snap["total"] == 1
    assert snap["running"] == 1


def test_post_tool_keeps_running_no_feed_line():
    s = SessionStore(clock=FakeClock())
    s.post_tool("a")
    snap = s.snapshot()
    assert snap["running"] == 1
    assert snap["entries"] == []      # tool calls no longer add a feed line
    assert snap["msg"] == "working"   # busy, but nothing to show


def test_notification_sets_waiting_cleared_by_activity():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a")
    s.notification("a")
    assert s.snapshot()["waiting"] == 1
    s.post_tool("a")      # a tool ran -> you've approved, alert clears
    assert s.snapshot()["waiting"] == 0


def test_waiting_pins_needs_you_as_newest():
    s = SessionStore(clock=FakeClock())
    s.notification("a", project="webapp")
    snap = s.snapshot()
    # single project -> alert is untagged; firmware highlights the last line
    assert snap["entries"][-1] == "needs you"
    assert snap["msg"] == "needs you"


def test_waiting_without_project():
    s = SessionStore(clock=FakeClock())
    s.notification("a")
    assert s.snapshot()["entries"][-1] == "needs you"


def test_waiting_multiple_sessions_each_get_a_line():
    clock = FakeClock()
    s = SessionStore(clock=clock)
    s.notification("a", project="webapp")
    clock.t += 1
    s.notification("b", project="docs")   # most recently waiting -> newest
    snap = s.snapshot()
    assert snap["waiting"] == 2
    # two projects waiting -> each alert is tagged
    assert snap["entries"][-1] == "[docs] needs you"
    assert "[webapp] needs you" in snap["entries"]


def test_waiting_alert_with_concurrent_busy_session():
    s = SessionStore(clock=FakeClock())
    s.notification("a", project="webapp")
    # a busy *other* session runs a tool while we're waiting — adds no feed line
    s.post_tool("b", project="api")
    snap = s.snapshot()
    # only webapp's alert is in the feed (b adds no line) -> single -> untagged
    assert snap["entries"] == ["needs you"]
    assert snap["running"] == 1
    assert snap["waiting"] == 1


def test_post_tool_project_remembered_for_later_waiting():
    s = SessionStore(clock=FakeClock())
    s.post_tool("a", project="webapp")   # remember a's project
    s.push_message("docs", "hi")          # a 2nd project in the feed -> tags on
    s.notification("a")                   # no project on this event
    # the alert is tagged with a's remembered project
    assert "[webapp] needs you" in s.snapshot()["entries"]


def test_repeat_notification_no_duplicate_alert():
    s = SessionStore(clock=FakeClock())
    s.notification("a", project="webapp")
    s.notification("a", project="webapp")   # already waiting
    entries = s.snapshot()["entries"]
    assert entries.count("needs you") == 1


def test_stop_clears_running_and_pulses_completed_once():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a")
    s.stop("a")
    first = s.snapshot()
    assert first["running"] == 0
    assert first["completed"] is True
    second = s.snapshot()
    assert second["completed"] is False


def test_session_end_removes_session():
    s = SessionStore(clock=FakeClock())
    s.session_start("a")
    s.session_end("a")
    assert s.snapshot()["total"] == 0


def test_aggregate_two_sessions():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a")
    s.prompt_submit("b")
    s.notification("b")
    snap = s.snapshot()
    assert snap["total"] == 2
    assert snap["running"] == 2
    assert snap["waiting"] == 1


def test_sweep_prunes_stale():
    clock = FakeClock()
    s = SessionStore(stale_after=300.0, clock=clock)
    s.prompt_submit("a")
    clock.t += 301
    s.sweep()
    assert s.snapshot()["total"] == 0


def test_entries_capped_oldest_first():
    s = SessionStore(max_entries=2, clock=FakeClock())
    s.push_message("", "1")
    s.push_message("", "2")
    s.push_message("", "3")
    entries = s.snapshot()["entries"]
    # capped to the 2 newest, emitted oldest-first (newest last)
    assert entries == ["2", "3"]


def test_haiku_mode_no_thinking():
    s = SessionStore(clock=FakeClock(), haiku_mode=True)
    s.prompt_submit("a", project="GH")
    assert s.snapshot()["entries"] == []   # no "thinking..." in haiku mode


def test_set_haiku_displays_three_lines():
    s = SessionStore(clock=FakeClock(), haiku_mode=True)
    s.set_haiku(["files mend", "a branch returns", "tests glow"])
    snap = s.snapshot()
    assert snap["entries"] == ["files mend", "a branch returns", "tests glow"]
    assert snap["msg"] == "tests glow"


def test_set_haiku_caps_three():
    s = SessionStore(clock=FakeClock(), haiku_mode=True)
    s.set_haiku(["1", "2", "3", "4"])
    assert s.snapshot()["entries"] == ["1", "2", "3"]


def test_haiku_with_waiting_alert_pins_untagged_single():
    s = SessionStore(clock=FakeClock(), haiku_mode=True)
    s.set_haiku(["a", "b", "c"])
    s.notification("x", project="GH")
    assert s.snapshot()["entries"][-1] == "needs you"   # single project -> untagged


def test_post_tool_records_activity_into_digest():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a", project="GH")
    s.post_tool("a", project="GH", tool="Edit", file="auth.py")
    s.post_tool("a", project="GH", tool="Bash")     # no file -> "ran a command"
    d = s.digest("a")
    assert "Focus [GH]" in d
    assert "Edit auth.py" in d
    assert "ran a command" in d


def test_digest_has_reply_gist_no_raw_commands():
    s = SessionStore(clock=FakeClock())
    s.post_tool("a", project="GH", tool="Bash")     # command text never reaches state
    s.record_reply("a", "race fixed, tests pass")
    d = s.digest("a")
    assert "ran a command" in d
    assert 'reply: "race fixed, tests pass"' in d


def test_digest_focus_then_also():
    s = SessionStore(clock=FakeClock())
    s.post_tool("a", project="GH", tool="Edit", file="auth.py")
    s.post_tool("b", project="CDB", tool="Edit", file="README.md")
    d = s.digest("a")
    assert d.startswith("Focus [GH]")
    assert "Also [CDB]" in d


def test_prompt_submit_clears_prior_activity():
    s = SessionStore(clock=FakeClock())
    s.post_tool("a", project="GH", tool="Edit", file="old.py")
    s.prompt_submit("a", project="GH")              # new turn
    s.post_tool("a", project="GH", tool="Edit", file="new.py")
    d = s.digest("a")
    assert "new.py" in d
    assert "old.py" not in d


def test_latest_running():
    clock = FakeClock()
    s = SessionStore(clock=clock)
    s.prompt_submit("a")
    clock.t += 1
    s.prompt_submit("b")
    assert s.latest_running() == "b"
    s.stop("b")
    assert s.latest_running() == "a"


def test_add_tokens_accumulates_in_snapshot():
    s = SessionStore(clock=FakeClock())
    assert s.snapshot()["tokens"] == 0
    s.add_tokens(1200)
    s.add_tokens(800)
    assert s.snapshot()["tokens"] == 2000


def test_add_tokens_ignores_nonpositive():
    s = SessionStore(clock=FakeClock())
    s.add_tokens(0)
    s.add_tokens(-5)
    assert s.snapshot()["tokens"] == 0
