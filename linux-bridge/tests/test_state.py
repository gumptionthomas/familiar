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
    s.prompt_submit("a", project="buddy")
    snap = s.snapshot()
    assert snap["entries"][-1] == "[buddy] thinking..."
    assert snap["msg"] == "[buddy] thinking..."


def test_stop_pulses_completed_no_message():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a", project="buddy")   # pushes "[buddy] thinking..."
    s.stop("a")          # the reply arrives later via push_message
    snap = s.snapshot()
    assert snap["completed"] is True
    assert snap["entries"] == ["[buddy] thinking..."]


def test_push_message_speaks_tagged_reply():
    s = SessionStore(clock=FakeClock())
    s.push_message("buddy", "Merged and live")
    snap = s.snapshot()
    assert snap["entries"][-1] == "[buddy] Merged and live"
    assert snap["msg"] == "[buddy] Merged and live"


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


def test_waiting_pins_needs_you_as_newest_named_by_project():
    s = SessionStore(clock=FakeClock())
    s.notification("a", project="webapp")
    snap = s.snapshot()
    # newest entry is last; the firmware highlights it and msg mirrors it
    assert snap["entries"][-1] == "webapp: needs you"
    assert snap["msg"] == "webapp: needs you"


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
    assert snap["entries"][-1] == "docs: needs you"
    assert "webapp: needs you" in snap["entries"]


def test_waiting_alert_with_concurrent_busy_session():
    s = SessionStore(clock=FakeClock())
    s.notification("a", project="webapp")
    # a busy *other* session runs a tool while we're waiting — adds no feed line
    s.post_tool("b", project="api")
    snap = s.snapshot()
    assert snap["entries"] == ["webapp: needs you"]   # only the alert shows
    assert snap["running"] == 1
    assert snap["waiting"] == 1


def test_post_tool_project_remembered_for_later_waiting():
    s = SessionStore(clock=FakeClock())
    s.post_tool("a", project="webapp")
    s.notification("a")   # no project on this event
    assert s.snapshot()["entries"][-1] == "webapp: needs you"


def test_repeat_notification_no_duplicate_alert():
    s = SessionStore(clock=FakeClock())
    s.notification("a", project="webapp")
    s.notification("a", project="webapp")   # already waiting
    entries = s.snapshot()["entries"]
    assert entries.count("webapp: needs you") == 1


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
