import json


def encode(obj: dict) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def time_sync(now_epoch: int, tz_offset_sec: int) -> dict:
    return {"time": [now_epoch, tz_offset_sec]}


def owner_msg(name: str) -> dict:
    return {"cmd": "owner", "name": name}
