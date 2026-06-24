import json
from claude_buddy import heartbeat


def test_encode_is_json_line():
    out = heartbeat.encode({"a": 1})
    assert out.endswith(b"\n")
    assert json.loads(out) == {"a": 1}
    assert b" " not in out  # compact separators


def test_time_sync_shape():
    assert heartbeat.time_sync(1775731234, -25200) == {"time": [1775731234, -25200]}


def test_owner_msg_shape():
    assert heartbeat.owner_msg("Thomas") == {"cmd": "owner", "name": "Thomas"}
