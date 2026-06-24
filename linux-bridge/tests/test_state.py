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
