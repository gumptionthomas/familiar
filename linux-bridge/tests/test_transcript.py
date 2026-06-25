from claude_buddy import transcript


def _w(tmp_path, *lines):
    p = tmp_path / "t.jsonl"
    p.write_text("".join(line + "\n" for line in lines))
    return str(p)


def _assistant(text, tool=False):
    blocks = [{"type": "text", "text": text}]
    if tool:
        blocks.append({"type": "tool_use", "name": "Bash", "input": {}})
    import json
    return json.dumps({"type": "assistant",
                       "message": {"role": "assistant", "content": blocks}})


def _user(text):
    import json
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


def _tool_result():
    import json
    return json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}})


def _tool_only():
    # Claude Code writes the tool_use as its own assistant entry (no text)
    import json
    return json.dumps({"type": "assistant", "message": {"role": "assistant",
        "content": [{"type": "tool_use", "name": "Bash", "input": {}}]}})


def _assistant_text(text):
    import json
    return json.dumps({"type": "assistant", "message": {"role": "assistant",
        "content": [{"type": "text", "text": text}]}})


def test_reply_after_human(tmp_path):
    p = _w(tmp_path, _user("merge it"), _assistant("Merged and live"))
    assert transcript.last_reply(p) == "Merged and live"


def test_not_ready_returns_empty(tmp_path):
    # human prompt present, but the final assistant reply not yet flushed
    p = _w(tmp_path, _user("do a thing"))
    assert transcript.last_reply(p) == ""


def test_ignores_intermediate_tool_message(tmp_path):
    # the only assistant-after-human still has a tool_use -> reply not final yet
    p = _w(tmp_path, _user("go"), _assistant("let me check", tool=True))
    assert transcript.last_reply(p) == ""


def test_tool_results_are_not_human(tmp_path):
    # a tool_result (user role, no text) must not anchor the "after human" cut;
    # the final reply after the real human prompt is returned
    p = _w(tmp_path,
           _user("ship it"),
           _assistant("working", tool=True),
           _tool_result(),
           _assistant("Done, shipped"))
    assert transcript.last_reply(p) == "Done, shipped"


def test_only_previous_turn_reply_not_returned(tmp_path):
    # previous turn's reply is BEFORE the latest human prompt -> not returned
    p = _w(tmp_path,
           _user("first"), _assistant("first reply"),
           _user("second"))            # second turn not answered yet
    assert transcript.last_reply(p) == ""


def test_collapses_and_caps(tmp_path):
    long = " ".join(["word"] * 60)
    p = _w(tmp_path, _user("go"), _assistant(long))
    out = transcript.last_reply(p)
    assert len(out) <= 48
    assert "\n" not in out


def test_split_intermediate_text_then_tool_not_final(tmp_path):
    # real shape: intermediate text + SEPARATE tool_use entry, final not yet written
    p = _w(tmp_path,
           _user("go"),
           _assistant_text("Both fixes are on main"),   # intermediate (text-only entry)
           _tool_only())                                 # tool_use AFTER it -> not final
    assert transcript.last_reply(p) == ""


def test_split_final_reply_after_tool(tmp_path):
    # the bug case: must return the closing text, not the intermediate one
    p = _w(tmp_path,
           _user("go"),
           _assistant_text("Both fixes are on main"),   # intermediate
           _tool_only(),
           _tool_result(),
           _assistant_text("All clean"))                 # closing reply
    assert transcript.last_reply(p) == "All clean"


def test_missing_file():
    assert transcript.last_reply("/no/such/file.jsonl") == ""


def test_empty_path():
    assert transcript.last_reply("") == ""
