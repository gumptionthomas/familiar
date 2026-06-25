"""Read the assistant's final reply from a Claude Code JSONL transcript.

The Stop hook can fire *before* the final assistant message is flushed to the
transcript, so callers should poll `last_reply` until it returns non-empty.
"""
import json

_TAIL = 65536  # only read the end of the file; transcripts can be large


def _entries(path):
    with open(path, "rb") as f:
        try:
            f.seek(-_TAIL, 2)
        except OSError:
            f.seek(0)
        blob = f.read().decode("utf-8", "replace")
    items = []  # (role, text, has_tool_use)
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        role = obj.get("type")
        if role not in ("user", "assistant"):
            continue
        msg = obj.get("message")
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        has_tool = False
        if isinstance(content, list):
            text = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text")
            has_tool = any(
                isinstance(b, dict) and b.get("type") == "tool_use"
                for b in content)
        else:
            text = str(content)
        items.append((role, " ".join(text.split()), has_tool))
    return items


def last_reply(path, cap=48):
    """The turn's closing assistant text after the most recent human prompt.

    Capped to ~48 chars so the snippet fits the firmware HUD's 3-line window
    (it shows only the last few wrapped rows); a longer snippet would scroll
    its own opening off the top.

    Claude Code writes a turn as multiple entries — intermediate text, separate
    tool_use entries, tool results, then the final text. The closing reply is
    the last assistant text with NO assistant tool_use entry after it; an
    intermediate "let me do X" always has a tool_use after it, so it's excluded.
    Returns "" if the turn hasn't closed with a text reply yet (so a poller
    keeps waiting) or on any error — never raises.
    """
    if not path:
        return ""
    try:
        items = _entries(path)
    except Exception:
        return ""
    last_human = -1
    for i, (role, text, _tool) in enumerate(items):
        if role == "user" and text:          # human prompt (tool results have no text)
            last_human = i
    last_text = last_tool = -1
    for i in range(last_human + 1, len(items)):
        role, text, has_tool = items[i]
        if role != "assistant":
            continue
        if text:
            last_text = i
        if has_tool:
            last_tool = i
    if last_text > last_tool:                 # a text reply with no tool_use after it
        return items[last_text][1][:cap]
    return ""
