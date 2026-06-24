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


def test_msg_working_fallback_when_running_no_activity():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a")
    assert s.snapshot()["msg"] == "working"


def test_running_after_prompt():
    s = SessionStore(clock=FakeClock())
    s.session_start("a")
    s.prompt_submit("a")
    snap = s.snapshot()
    assert snap["total"] == 1
    assert snap["running"] == 1


def test_post_tool_sets_activity_and_keeps_running():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a")
    s.post_tool("a", "Bash", "git push")
    snap = s.snapshot()
    assert snap["running"] == 1
    assert snap["msg"] == "Bash: git push"
    assert snap["entries"][0] == "Bash: git push"


def test_post_tool_tags_project():
    s = SessionStore(clock=FakeClock())
    s.post_tool("a", "Edit", "main.cpp", project="buddy")
    snap = s.snapshot()
    assert snap["entries"][0] == "[buddy] Edit: main.cpp"
    assert snap["msg"] == "[buddy] Edit: main.cpp"


def test_post_tool_no_project_unprefixed():
    s = SessionStore(clock=FakeClock())
    s.post_tool("a", "Bash", "ls")
    assert s.snapshot()["entries"][0] == "Bash: ls"


def test_notification_sets_waiting_cleared_by_activity():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a")
    s.notification("a")
    assert s.snapshot()["waiting"] == 1
    s.post_tool("a", "Read", "main.cpp")
    assert s.snapshot()["waiting"] == 0


def test_waiting_pushes_needs_you_entry_named_by_project():
    s = SessionStore(clock=FakeClock())
    s.notification("a", project="webapp")
    snap = s.snapshot()
    assert snap["entries"][0] == "webapp: needs you"   # shows in the HUD feed
    assert snap["msg"] == "webapp: needs you"           # newest entry


def test_waiting_without_project():
    s = SessionStore(clock=FakeClock())
    s.notification("a")
    assert s.snapshot()["entries"][0] == "needs you"


def test_waiting_multiple_sessions_each_get_a_line():
    clock = FakeClock()
    s = SessionStore(clock=clock)
    s.notification("a", project="webapp")
    clock.t += 1
    s.notification("b", project="docs")   # newest
    snap = s.snapshot()
    assert snap["waiting"] == 2
    assert snap["entries"][0] == "docs: needs you"
    assert "webapp: needs you" in snap["entries"]


def test_waiting_appears_above_activity():
    s = SessionStore(clock=FakeClock())
    s.post_tool("a", "Bash", "ls", project="webapp")
    s.notification("a", project="webapp")
    assert s.snapshot()["entries"][0] == "webapp: needs you"


def test_post_tool_project_remembered_for_later_waiting():
    s = SessionStore(clock=FakeClock())
    s.post_tool("a", "Bash", "ls", project="webapp")
    s.notification("a")   # no project on this event
    assert s.snapshot()["entries"][0] == "webapp: needs you"


def test_repeat_notification_no_duplicate_entry():
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


def test_entries_capped_newest_first():
    s = SessionStore(max_entries=2, clock=FakeClock())
    s.post_tool("a", "Read", "1")
    s.post_tool("a", "Read", "2")
    s.post_tool("a", "Read", "3")
    entries = s.snapshot()["entries"]
    assert entries == ["Read: 3", "Read: 2"]
