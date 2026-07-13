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


def test_record_contains_only_the_four_allowed_fields(tmp_path):
    # THE privacy constraint: outputs only. The digest contains the user's prompt
    # and Claude's reply; none of it may ever reach this file. If you are adding a
    # field and this test fails, that is the test doing its job -- go and re-read
    # the design doc before changing it.
    p = tmp_path / "haikus.jsonl"
    archive.append(["a", "b", "c"], model="m", prompt="p", path=p)
    rec = archive.load(p)[0]
    assert set(rec.keys()) == {"ts", "lines", "model", "prompt"}


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


def test_load_survives_a_truncation_inside_a_multibyte_character(tmp_path):
    # append() writes ensure_ascii=False, and the model emits em-dashes and curly
    # quotes constantly. A power cut mid-write can truncate INSIDE a multi-byte
    # character, leaving invalid UTF-8. That must cost us the bad line -- not the
    # entire archive, which is what a naive `except: return []` does.
    p = tmp_path / "haikus.jsonl"
    archive.append(["dusk settles — quiet", "the sensor breathes", "night"],
                   model="m", prompt="p", path=p)
    archive.append(["a second haiku", "wholly intact", "here"],
                   model="m", prompt="p", path=p)

    raw = p.read_bytes()
    # Chop inside the em-dash's 3 UTF-8 bytes of a THIRD, partial record.
    p.write_bytes(raw + '{"ts":"t","lines":["cut —'.encode("utf-8")[:-1])

    recs = archive.load(p)
    assert len(recs) == 2                      # both good records survive
    assert recs[1]["lines"][0] == "a second haiku"


def test_load_limit_keeps_the_most_recent(tmp_path):
    p = tmp_path / "haikus.jsonl"
    for i in range(5):
        archive.append([str(i)], model="m", prompt="p", path=p)
    recs = archive.load(p, limit=2)
    assert [r["lines"] for r in recs] == [["3"], ["4"]]   # newest 2, oldest first


def test_load_limit_zero_means_no_limit(tmp_path):
    p = tmp_path / "h.jsonl"
    for i in range(3):
        archive.append([str(i)], model="m", prompt="p", path=p)
    assert archive.load(p, limit=0) == archive.load(p)      # 0 == no limit
    assert len(archive.load(p, limit=0)) == 3


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


def test_tropes_catch_possessives_and_contractions():
    # "the cursor's glow" is plausible model output. If the apostrophe keeps
    # cursor's as one token, the banned word is silently missed -- a false
    # negative in the metric the design calls most valuable.
    recs = [_rec(["the cursor's glow", "a screen's hum", "it's dusk"])]
    st = archive.stats(recs)
    tropes = dict((w, share) for w, _n, share in st["tropes"])
    assert tropes["cursor"] == pytest.approx(1.0)
    assert tropes["screen"] == pytest.approx(1.0)
    imagery = [w for w, _n, _s in st["imagery"]]
    assert "it's" not in imagery and "cursor's" not in imagery


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


import asyncio

from familiar import daemon
from familiar.config import Config


def test_compose_archives_a_successful_haiku(tmp_path, monkeypatch):
    async def fake_compose(digest, *, api_key, model, **kw):
        return ["one", "two", "three"]

    monkeypatch.setattr(haiku, "compose", fake_compose)
    written = []
    cfg = Config(api_key="k", model="m1", haiku_archive=True)
    compose = daemon._make_compose(
        cfg, append=lambda lines, **kw: written.append((lines, kw)))

    lines = asyncio.run(compose("some digest"))
    assert lines == ["one", "two", "three"]
    assert len(written) == 1
    got_lines, kw = written[0]
    assert got_lines == ["one", "two", "three"]
    assert kw["model"] == "m1"
    assert kw["prompt"] == archive.prompt_id(haiku.SYSTEM)


def test_compose_archives_nothing_when_the_haiku_fails(tmp_path, monkeypatch):
    async def fake_compose(digest, *, api_key, model, **kw):
        return None                       # API down / unparseable

    monkeypatch.setattr(haiku, "compose", fake_compose)
    written = []
    cfg = Config(api_key="k", model="m1", haiku_archive=True)
    compose = daemon._make_compose(cfg, append=lambda *a, **kw: written.append(a))
    assert asyncio.run(compose("d")) is None
    assert written == []


def test_compose_archives_nothing_when_disabled(monkeypatch):
    async def fake_compose(digest, *, api_key, model, **kw):
        return ["one", "two", "three"]

    monkeypatch.setattr(haiku, "compose", fake_compose)
    written = []
    cfg = Config(api_key="k", model="m1", haiku_archive=False)
    compose = daemon._make_compose(cfg, append=lambda *a, **kw: written.append(a))
    assert asyncio.run(compose("d")) == ["one", "two", "three"]
    assert written == []                  # opted out


def test_haikus_cli_lists_recent(tmp_path, capsys):
    p = tmp_path / "h.jsonl"
    archive.append(["alpha one", "beta two", "gamma"], model="m", prompt="p", path=p)
    assert archive.main(["--path", str(p)]) == 0
    out = capsys.readouterr().out
    assert "alpha one" in out and "gamma" in out


def test_haikus_cli_stats_reports_tropes(tmp_path, capsys):
    p = tmp_path / "h.jsonl"
    archive.append(["the cursor blinks", "a glowing screen", "dusk"],
                   model="m", prompt="p", path=p)
    assert archive.main(["--stats", "--path", str(p)]) == 0
    out = capsys.readouterr().out.lower()
    assert "cursor" in out
    assert "trope" in out


def test_haikus_cli_on_an_empty_archive_is_friendly_not_an_error(tmp_path, capsys):
    assert archive.main(["--path", str(tmp_path / "nope.jsonl")]) == 0
    assert "no haikus" in capsys.readouterr().out.lower()


def test_haikus_cli_limit_zero_means_all_in_both_modes(tmp_path, capsys):
    # "limit 0" = no limit, in BOTH modes. The same flag value must never mean
    # "none" in one mode and "all" in the other.
    p = tmp_path / "h.jsonl"
    for i in range(3):
        archive.append([f"line {i}"], model="m", prompt="p", path=p)

    assert archive.main(["--path", str(p), "--limit", "0"]) == 0
    out = capsys.readouterr().out
    assert "line 0" in out and "line 2" in out       # all three, not none

    assert archive.main(["--path", str(p), "--stats", "--limit", "0"]) == 0
    assert "3 haikus" in capsys.readouterr().out


def test_haikus_cli_stats_defaults_to_the_whole_corpus_not_20(tmp_path, capsys):
    # --stats must analyse ALL records by default -- trends computed over only
    # the most-recent-20 default (the plain listing's default) would be noise,
    # which defeats the point of a trend report. Nothing else pins this: a
    # refactor that quietly restored the 20-record default here would make the
    # stats silently meaningless again.
    p = tmp_path / "h.jsonl"
    for i in range(25):
        archive.append([f"haiku {i}"], model="m", prompt="p", path=p)

    assert archive.main(["--stats", "--path", str(p)]) == 0
    out = capsys.readouterr().out
    assert "25 haikus" in out          # all 25, not truncated to 20

    # The split cuts both ways: the plain listing still defaults to the most
    # recent 20, not the whole archive.
    assert archive.main(["--path", str(p)]) == 0
    out = capsys.readouterr().out
    assert "haiku 24" in out and "haiku 5" in out     # newest 20 (5..24)
    assert "haiku 0" not in out and "haiku 4" not in out  # oldest 5 dropped


def test_haikus_cli_rejects_a_negative_limit(tmp_path):
    # Before the fix, --limit -1 sliced recs[1:] and silently dropped the
    # OLDEST record with no error. It must now be rejected outright via
    # argparse rather than returning 0 with a silently truncated list.
    p = tmp_path / "h.jsonl"
    archive.append(["a"], model="m", prompt="p", path=p)
    with pytest.raises(SystemExit):
        archive.main(["--path", str(p), "--limit", "-1"])
