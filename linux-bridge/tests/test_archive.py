import json
from datetime import datetime, timezone

import pytest

from familiar import archive, haiku


def test_prompt_id_is_stable_and_changes_with_the_prompt():
    a = archive.prompt_id("you are a desk pet")
    assert a == archive.prompt_id("you are a desk pet")   # stable
    assert len(a) == 8 and a == a.lower()
    assert a != archive.prompt_id("you are a desk pet.")  # one char -> different
    # The real prompt must be hashable without reaching into a private.
    assert len(archive.prompt_id(haiku.SYSTEM)) == 8


def test_append_then_load_roundtrip(tmp_path):
    p = tmp_path / "haikus.jsonl"
    when = datetime(2026, 7, 13, 11, 42, 3, tzinfo=timezone.utc)
    assert archive.append(["one", "two", "three"], model="m1", prompt="abc12345",
                          path=p, now=when) is True

    recs = archive.load(p)
    assert len(recs) == 1
    assert recs[0]["lines"] == ["one", "two", "three"]
    assert recs[0]["model"] == "m1"
    assert recs[0]["prompt"] == "abc12345"
    assert recs[0]["ts"].startswith("2026-07-13T11:42:03")


def test_append_appends_rather_than_overwriting(tmp_path):
    p = tmp_path / "haikus.jsonl"
    archive.append(["a"], model="m", prompt="p", path=p)
    archive.append(["b"], model="m", prompt="p", path=p)
    recs = archive.load(p)
    assert [r["lines"] for r in recs] == [["a"], ["b"]]   # oldest first


def test_append_creates_the_parent_directory(tmp_path):
    p = tmp_path / "nested" / "deeper" / "haikus.jsonl"
    assert archive.append(["a"], model="m", prompt="p", path=p) is True
    assert p.exists()


def test_append_never_raises_on_an_unwritable_path(tmp_path):
    # A directory where the file should be: writing must FAIL, not explode.
    # The buddy must never break because a log file failed.
    p = tmp_path / "haikus.jsonl"
    p.mkdir()
    assert archive.append(["a"], model="m", prompt="p", path=p) is False


def test_load_of_a_missing_file_is_empty_not_an_error(tmp_path):
    assert archive.load(tmp_path / "nope.jsonl") == []


def test_load_skips_a_corrupt_line_and_keeps_the_rest(tmp_path):
    # A power cut mid-write must not make the whole log unreadable.
    p = tmp_path / "haikus.jsonl"
    good = json.dumps({"ts": "t", "lines": ["ok"], "model": "m", "prompt": "p"})
    p.write_text(good + "\n" + '{"ts": "t", "lines": ["trunc' + "\n" + good + "\n")
    recs = archive.load(p)
    assert len(recs) == 2
    assert all(r["lines"] == ["ok"] for r in recs)


def test_load_limit_keeps_the_most_recent(tmp_path):
    p = tmp_path / "haikus.jsonl"
    for i in range(5):
        archive.append([str(i)], model="m", prompt="p", path=p)
    recs = archive.load(p, limit=2)
    assert [r["lines"] for r in recs] == [["3"], ["4"]]   # newest 2, oldest first
