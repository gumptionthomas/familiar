from familiar import init

EVENTS = {"SessionStart": "session-start", "UserPromptSubmit": "prompt-submit",
          "PostToolUse": "post-tool", "Notification": "notification",
          "Stop": "stop", "SessionEnd": "session-end"}


def test_merge_hooks_adds_all_six_events():
    out = init.merge_hooks({})
    for evt, name in EVENTS.items():
        cmds = [h["command"] for grp in out["hooks"][evt] for h in grp["hooks"]]
        assert f"familiar hook {name}" in cmds


def test_merge_hooks_is_idempotent():
    once = init.merge_hooks({})
    twice = init.merge_hooks(once)
    assert once == twice


def test_merge_hooks_preserves_foreign_hooks():
    existing = {"hooks": {"Stop": [{"hooks": [{"type": "command",
                                               "command": "other-tool ping"}]}]}}
    out = init.merge_hooks(existing)
    cmds = [h["command"] for grp in out["hooks"]["Stop"] for h in grp["hooks"]]
    assert "other-tool ping" in cmds and "familiar hook stop" in cmds


def test_merge_hooks_sets_posttooluse_matcher():
    out = init.merge_hooks({})
    assert out["hooks"]["PostToolUse"][0].get("matcher") == "*"
