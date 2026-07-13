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


def _rec(lines, prompt="p1"):
    return {"ts": "2026-07-13T00:00:00+00:00", "lines": lines,
            "model": "m", "prompt": prompt}


def test_imagery_is_document_frequency_not_raw_count():
    # "silence" appears THREE times, but all inside ONE haiku out of ten.
    # That is one haiku using a word, not a rut -> 10%, NOT 30%.
    # A metric that can't tell these apart is worthless, which is the whole point.
    recs = [_rec(["silence silence", "silence falls", "dusk"])]
    recs += [_rec(["nothing here", "moving on", "quite fresh"]) for _ in range(9)]
    st = archive.stats(recs)
    imagery = dict((w, share) for w, _n, share in st["imagery"])
    assert imagery["silence"] == pytest.approx(0.1)


def test_imagery_drops_stopwords_and_ranks_by_share():
    recs = [_rec(["the quiet hum", "of the machine"]),
            _rec(["the quiet dusk", "a lantern"])]
    st = archive.stats(recs)
    words = [w for w, _n, _s in st["imagery"]]
    assert "the" not in words and "of" not in words and "a" not in words
    assert words[0] == "quiet"          # in both haikus -> ranked first


def test_repeated_lines_counted_across_haikus_not_within_one():
    recs = [_rec(["a silent hum", "a silent hum"]),   # twice in ONE haiku -> not repeat
            _rec(["a lantern sways"]),
            _rec(["a lantern sways"])]                # across TWO haikus -> a repeat
    st = archive.stats(recs)
    repeated = dict(st["repeated"])
    assert repeated == {"a lantern sways": 2}


def test_repeated_lines_normalise_case_and_whitespace():
    recs = [_rec(["The Lantern Sways"]), _rec(["  the lantern sways  "])]
    st = archive.stats(recs)
    assert dict(st["repeated"]) == {"the lantern sways": 2}


def test_tropes_flag_banned_imagery_case_insensitively():
    # The system prompt bans cursors and glowing screens. Measure whether it worked.
    recs = [_rec(["the Cursor blinks", "a glowing screen", "dusk"]),
            _rec(["a lantern sways", "nothing banned", "here"])]
    st = archive.stats(recs)
    tropes = dict((w, share) for w, _n, share in st["tropes"])
    assert tropes["cursor"] == pytest.approx(0.5)
    assert tropes["screen"] == pytest.approx(0.5)
    assert "keyboard" not in tropes          # never seen -> not reported


def test_stats_of_an_empty_archive_does_not_divide_by_zero():
    st = archive.stats([])
    assert st["count"] == 0
    assert st["imagery"] == [] and st["repeated"] == [] and st["tropes"] == []
    assert st["by_prompt"] == {}


def test_stats_group_by_prompt_version():
    recs = [_rec(["quiet dusk"], prompt="v1"), _rec(["quiet dawn"], prompt="v1"),
            _rec(["loud noon"], prompt="v2")]
    st = archive.stats(recs)
    assert st["by_prompt"]["v1"]["count"] == 2
    assert st["by_prompt"]["v2"]["count"] == 1
    v1 = dict((w, share) for w, _n, share in st["by_prompt"]["v1"]["imagery"])
    assert v1["quiet"] == pytest.approx(1.0)      # both v1 haikus -> 100%
