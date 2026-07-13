# Haiku Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a local append-only record of every haiku the bridge composes, and surface the trends in it (recurring imagery, repeated lines, banned-trope violations) via `familiar haikus --stats`.

**Architecture:** One new self-contained module, `linux-bridge/src/familiar/archive.py`, holding the record format, a best-effort writer, a tolerant reader, the trend statistics, and the `familiar haikus` subcommand. The writer is wired in as a decorator around `_make_compose` in `daemon.py` — so `Bridge` gains no new parameter, no new state, and no knowledge that an archive exists.

**Tech Stack:** Python 3.11+ (stdlib only — `json`, `hashlib`, `pathlib`, `collections`, `datetime`, `argparse`). No new dependencies. pytest.

**Spec:** `docs/superpowers/specs/2026-07-13-haiku-archive-design.md`

## Global Constraints

- **Outputs only. Never store the digest, the user's prompt, Claude's reply, the project name, or file paths.** The digest (`state.py:129-138`) contains `asked: "<the user's prompt>"` and `reply: "<Claude's reply>"` — a record of real work. This is a privacy line drawn on purpose; do not "helpfully" add fields to make debugging easier.
- **`archive.append` must NEVER raise.** It is called from the daemon's compose path. An unwritable path, a full disk, or a permission error must be swallowed and return `False`. The buddy must never break because a log file failed. This mirrors `haiku.py`'s existing contract ("Best-effort and never raises").
- **`archive.load` must tolerate a corrupt or truncated line** — skip it and return the good records. A crash mid-write must not render the log permanently unreadable.
- **`Bridge` must not change.** No new constructor parameter, no new attribute. The existing `Bridge` tests must keep passing untouched — that is the proof the archive is decoupled.
- No new dependencies. Stdlib only.
- Run tests from `linux-bridge/`: `uv run pytest -q`. The suite is currently **170 passing**.
- **Imports go in the file's top import block.** Later tasks show new code as "append to
  `archive.py`" / "append to `test_archive.py`" — that means the *functions*, not the
  `import` lines. Every `import` in those snippets belongs merged into the existing block at
  the top of the file, in the usual stdlib-then-local order. Do not leave imports stranded
  mid-file.

## Record format (binding)

One JSON object per line in `$XDG_DATA_HOME/familiar/haikus.jsonl` (default `~/.local/share/familiar/haikus.jsonl`):

```json
{"ts":"2026-07-13T11:42:03-05:00","lines":["...","...","..."],"model":"claude-haiku-4-5-20251001","prompt":"a3f1c802"}
```

`prompt` is `sha256(SYSTEM).hexdigest()[:8]` — the system-prompt version, so a prompt edit is a visible boundary in the trends instead of silent noise.

---

### Task 1: `archive.py` — record, write, read

**Files:**
- Create: `linux-bridge/src/familiar/archive.py`
- Modify: `linux-bridge/src/familiar/haiku.py` (rename `_SYSTEM` → `SYSTEM`)
- Test: `linux-bridge/tests/test_archive.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (Tasks 2 and 3 depend on these exact signatures):
  - `archive.DEFAULT_PATH: Path`
  - `archive.prompt_id(system: str) -> str` — 8 lowercase hex chars
  - `archive.append(lines: list[str], *, model: str, prompt: str, path: Path = DEFAULT_PATH, now=None) -> bool` — `now` is an injectable `datetime` for tests; returns `True` on write, `False` on any failure, **never raises**
  - `archive.load(path: Path = DEFAULT_PATH, limit: int | None = None) -> list[dict]` — oldest-first; `limit` keeps the **most recent** N
  - `haiku.SYSTEM: str` — the system prompt, now public

- [ ] **Step 1: Write the failing tests**

Create `linux-bridge/tests/test_archive.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_archive.py -q`

Expected: FAIL — `ImportError: cannot import name 'archive' from 'familiar'`.

- [ ] **Step 3: Make the system prompt public**

In `linux-bridge/src/familiar/haiku.py`, rename the module constant `_SYSTEM` to `SYSTEM`
(the definition at line 11 and its single use in `_post`'s payload, `"system": _SYSTEM`).
It is a module constant with no external users; making it public lets `archive.prompt_id`
hash it without reaching into a private.

- [ ] **Step 4: Write `archive.py` (record, write, read)**

Create `linux-bridge/src/familiar/archive.py`:

```python
"""A local, append-only archive of the haikus the buddy has composed.

Outputs ONLY. The model's input (the digest) contains the user's prompts, their
file activity, and Claude's replies -- a record of real work -- and is
deliberately never stored here. See the design doc: this archive can tell you
THAT the haikus are drifting (repetition, recycled imagery, banned tropes), never
WHY. Diagnosis stays a manual investigation.

Best-effort throughout: a failed write is swallowed. The buddy must never break
because a log file failed.
"""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path


def _default_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "familiar" / "haikus.jsonl"


DEFAULT_PATH = _default_path()


def prompt_id(system: str) -> str:
    """Short, stable id for a system-prompt version.

    Recorded on every haiku so that rewording the prompt shows up as a boundary
    in the stats instead of silently mixing two eras into one trend line.
    """
    return hashlib.sha256((system or "").encode("utf-8")).hexdigest()[:8]


def append(lines, *, model: str, prompt: str,
           path: Path = DEFAULT_PATH, now=None) -> bool:
    """Append one haiku. Returns True on success, False on ANY failure.

    NEVER raises: this runs on the daemon's compose path, and an archive is a
    nicety while the pet is the product.
    """
    try:
        when = now or datetime.now().astimezone()
        rec = {"ts": when.isoformat(), "lines": list(lines),
               "model": model, "prompt": prompt}
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def load(path: Path = DEFAULT_PATH, limit: int | None = None) -> list[dict]:
    """Read the archive oldest-first. `limit` keeps the most recent N.

    A corrupt or truncated line (e.g. a power cut mid-write) is skipped, not
    fatal -- an append-only log a crash can render unreadable is a bad log.
    """
    recs = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue                    # skip the bad line, keep going
                if isinstance(rec, dict) and isinstance(rec.get("lines"), list):
                    recs.append(rec)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return recs[-limit:] if limit else recs
```

- [ ] **Step 5: Run the tests and the full suite**

Run: `cd linux-bridge && uv run pytest -q`

Expected: PASS — 178 (170 existing + 8 new). No existing test may break.

- [ ] **Step 6: Commit**

```bash
git add linux-bridge/src/familiar/archive.py linux-bridge/src/familiar/haiku.py linux-bridge/tests/test_archive.py
git commit -m "feat: archive each composed haiku to a local JSONL log

Outputs only -- the digest contains the user's prompts and Claude's
replies and is deliberately never stored. The writer never raises and the
reader tolerates a truncated line, because the buddy must never break
because a log file failed."
```

---

### Task 2: The trend statistics

**Files:**
- Modify: `linux-bridge/src/familiar/archive.py`
- Test: `linux-bridge/tests/test_archive.py`

**Interfaces:**
- Consumes (from Task 1): `archive.load(path, limit) -> list[dict]`, records shaped
  `{"ts": str, "lines": list[str], "model": str, "prompt": str}`.
- Produces (Task 3 renders this):
  ```python
  archive.stats(records: list[dict]) -> dict
  # {
  #   "count": int,
  #   "imagery":  [(word, n_haikus, share_float), ...],   # desc, stopwords removed
  #   "repeated": [(line, n), ...],                       # n >= 2, desc
  #   "tropes":   [(word, n_haikus, share_float), ...],   # only words actually seen
  #   "by_prompt": {prompt_id: {"count": int, "imagery": [...], "tropes": [...]}},
  # }
  ```
  `share_float` is 0.0–1.0. `archive.TROPES: list[str]` and `archive.STOPWORDS: set[str]`
  are module constants.

**Background — the metric that matters.** The obvious implementation is a word count. It is
the wrong metric. A word used twice inside ONE haiku is just a haiku; a word appearing in 40%
of ALL haikus is a rut. So `imagery` measures **document frequency** — the share of haikus
containing a word at least once — not raw occurrences.

`tropes` measures whether the model is obeying its own system prompt, which explicitly bans
*"keyboards, keys, clicking, typing fingers, glowing screens, or blinking cursors"*
(`haiku.py:17-18`). If `cursor` shows up in 15% of haikus, the prompt is not working — and
nobody would ever notice that reading them one at a time on a 64×32 display.

- [ ] **Step 1: Write the failing tests**

Append to `linux-bridge/tests/test_archive.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_archive.py -q`

Expected: FAIL — `AttributeError: module 'familiar.archive' has no attribute 'stats'`.

- [ ] **Step 3: Implement `stats`**

Append to `linux-bridge/src/familiar/archive.py`:

```python
import re
from collections import Counter

# Small and deliberate: enough to stop "the" topping every report, no more.
STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "and", "to", "is", "it", "its",
    "with", "at", "by", "for", "from", "as", "into",
}

# Imagery the system prompt explicitly BANS ("Avoid the tired tropes: no
# keyboards, keys, clicking, typing fingers, glowing screens, or blinking
# cursors"). Counting these measures whether the model obeys its own prompt --
# the one statistic most likely to change what you do about it.
TROPES = [
    "keyboard", "keyboards", "key", "keys", "click", "clicking", "clicks",
    "type", "typing", "typed", "finger", "fingers", "screen", "screens",
    "glow", "glowing", "cursor", "cursors", "blink", "blinking",
]

_WORD = re.compile(r"[a-z']+")


def _words(rec) -> set[str]:
    """The distinct lowercase words in one haiku (a SET: we measure how many
    haikus contain a word, not how many times it occurs)."""
    text = " ".join(rec.get("lines") or []).lower()
    return set(_WORD.findall(text))


def _doc_freq(records, vocabulary=None):
    """(word, n_haikus, share) desc — the share of haikus containing the word."""
    total = len(records)
    if not total:
        return []
    seen = Counter()
    for rec in records:
        for w in _words(rec):
            if vocabulary is not None and w not in vocabulary:
                continue
            if vocabulary is None and (w in STOPWORDS or len(w) < 3):
                continue
            seen[w] += 1
    return [(w, n, n / total)
            for w, n in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))]


def _repeated_lines(records):
    """Lines emitted verbatim in two or more DIFFERENT haikus, desc."""
    counts = Counter()
    for rec in records:
        # dedupe within one haiku: repeating a line inside a poem is a choice,
        # reusing it across poems is staleness.
        for line in {" ".join((ln or "").lower().split())
                     for ln in (rec.get("lines") or []) if ln.strip()}:
            counts[line] += 1
    return sorted(((ln, n) for ln, n in counts.items() if n >= 2),
                  key=lambda kv: (-kv[1], kv[0]))


def stats(records) -> dict:
    records = list(records)
    by_prompt = {}
    for pid in sorted({r.get("prompt", "") for r in records}):
        group = [r for r in records if r.get("prompt", "") == pid]
        by_prompt[pid] = {
            "count": len(group),
            "imagery": _doc_freq(group)[:10],
            "tropes": _doc_freq(group, vocabulary=set(TROPES)),
        }
    return {
        "count": len(records),
        "imagery": _doc_freq(records)[:20],
        "repeated": _repeated_lines(records),
        "tropes": _doc_freq(records, vocabulary=set(TROPES)),
        "by_prompt": by_prompt,
    }
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `cd linux-bridge && uv run pytest -q`

Expected: PASS — 185 (178 + 7 new).

- [ ] **Step 5: Commit**

```bash
git add linux-bridge/src/familiar/archive.py linux-bridge/tests/test_archive.py
git commit -m "feat: haiku trend stats — document frequency, repeats, tropes

Imagery is measured as the SHARE OF HAIKUS containing a word, not raw
occurrences: a word used twice in one haiku is a haiku, a word in 40% of
all haikus is a rut. Tropes counts the imagery the system prompt
explicitly bans, so we can see whether the model is obeying it."
```

---

### Task 3: Wire the writer in, and ship `familiar haikus`

**Files:**
- Modify: `linux-bridge/src/familiar/archive.py` (add `main`)
- Modify: `linux-bridge/src/familiar/config.py:13` (add `haiku_archive`), and the `load()` builder
- Modify: `linux-bridge/src/familiar/daemon.py:336-343` (`_make_compose`)
- Modify: `linux-bridge/src/familiar/cli.py` (dispatch + help)
- Modify: `README.md`
- Test: `linux-bridge/tests/test_archive.py`

**Interfaces:**
- Consumes (from Tasks 1-2): `archive.append(lines, *, model, prompt, path, now) -> bool`,
  `archive.load(path, limit) -> list[dict]`, `archive.stats(records) -> dict`,
  `archive.prompt_id(system) -> str`, `haiku.SYSTEM`.
- Produces: `archive.main(argv) -> int`; `Config.haiku_archive: bool = True`.

**Background.** The writer is wired as a decorator inside `_make_compose` — NOT inside
`Bridge`. `_make_compose` already closes over `cfg` (where the model id and the archive flag
live) and every successful compose passes through it exactly once. `Bridge` therefore gains no
new parameter, no new state, and no knowledge that an archive exists — and every existing
`Bridge` test keeps passing untouched, which is the proof it is decoupled.

- [ ] **Step 1: Write the failing tests**

Append to `linux-bridge/tests/test_archive.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_archive.py -q`

Expected: FAIL — `TypeError: Config.__init__() got an unexpected keyword argument 'haiku_archive'`.

- [ ] **Step 3: Add the config flag**

In `linux-bridge/src/familiar/config.py`, add a field to the `Config` dataclass (after
`tidbyt_pet`):

```python
    haiku_archive: bool = True
```

and in `load()`, add to the `Config(...)` construction (after the `tidbyt_pet=` line):

```python
        # The archive writes composed haikus to $XDG_DATA_HOME/familiar/haikus.jsonl.
        # Outputs only -- never the digest. Set false to opt out entirely.
        haiku_archive=bool(data.get("haiku_archive", True)),
```

- [ ] **Step 4: Wire the writer into `_make_compose`**

In `linux-bridge/src/familiar/daemon.py`, add `archive` to the existing import line
(`from . import haiku, heartbeat, tidbyt, transcript` → include `archive`), then replace
`_make_compose` (lines 336-343):

```python
def _make_compose(cfg, append=None):
    if not cfg.api_key:
        return None
    write = append or archive.append

    async def compose(digest):
        lines = await haiku.compose(digest, api_key=cfg.api_key, model=cfg.model)
        # Archive here, not in Bridge: this is the single point every successful
        # compose passes through, and cfg (the model id, the opt-out) is already
        # in scope. Bridge stays ignorant that an archive exists.
        if lines and cfg.haiku_archive:
            write(lines, model=cfg.model, prompt=archive.prompt_id(haiku.SYSTEM))
        return lines

    return compose
```

- [ ] **Step 5: Add `archive.main` (the `familiar haikus` subcommand)**

Append to `linux-bridge/src/familiar/archive.py`:

```python
import argparse


def _fmt_share(n: int, share: float) -> str:
    return f"{share * 100:4.0f}%  ({n})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="familiar haikus")
    ap.add_argument("--stats", action="store_true",
                    help="show trends instead of the haikus themselves")
    ap.add_argument("--limit", type=int, default=20,
                    help="how many of the most recent haikus to use (default 20; "
                         "with --stats, 0 means all)")
    ap.add_argument("--path", default=str(DEFAULT_PATH),
                    help=argparse.SUPPRESS)     # tests only
    args = ap.parse_args(argv)

    limit = None if (args.stats and args.limit == 0) else args.limit
    records = load(Path(args.path), limit=limit)
    if not records:
        print("no haikus archived yet — the buddy writes one each time it "
              "composes (haiku mode must be on)")
        return 0

    if not args.stats:
        for rec in records:
            print(f"  {rec.get('ts', '')[:16].replace('T', ' ')}")
            for line in rec.get("lines") or []:
                print(f"    {line}")
            print()
        return 0

    st = stats(records)
    print(f"{st['count']} haikus, {len(st['by_prompt'])} prompt version(s)\n")

    print("RECURRING IMAGERY (share of haikus containing the word)")
    for w, n, share in st["imagery"][:10]:
        print(f"  {w:<14}{_fmt_share(n, share)}")

    print("\nREPEATED LINES")
    if st["repeated"]:
        for line, n in st["repeated"][:10]:
            print(f"  {n}x  \"{line}\"")
    else:
        print("  (none — every line so far is unique)")

    print("\nTROPE VIOLATIONS (imagery the system prompt bans)")
    if st["tropes"]:
        for w, n, share in st["tropes"]:
            print(f"  {w:<14}{_fmt_share(n, share)}")
    else:
        print("  (none — the prompt is holding)")

    if len(st["by_prompt"]) > 1:
        print("\nBY PROMPT VERSION")
        for pid, g in st["by_prompt"].items():
            top = g["imagery"][0][0] if g["imagery"] else "-"
            tropes = sum(n for _w, n, _s in g["tropes"])
            print(f"  {pid}  {g['count']:>4} haikus   top: {top:<12} "
                  f"trope hits: {tropes}")
    return 0
```

- [ ] **Step 6: Add the CLI dispatch**

In `linux-bridge/src/familiar/cli.py`, add `archive` to the import
(`from . import archive, daemon, hook, init`), add a line to `_HELP` after the `hook` line:

```
  familiar haikus [--stats]    browse the archived haikus, or their trends
```

and add a branch before the unknown-command fallback:

```python
    if cmd == "haikus":
        return archive.main(rest)
```

- [ ] **Step 7: Document it in the README**

In `README.md`, in the "Haiku firmware" bullet of the intro list (the one beginning
"**Haiku firmware.**"), append this sentence to the end of that bullet:

```
  Every composed haiku is archived locally to
  `~/.local/share/familiar/haikus.jsonl`; `familiar haikus --stats` surfaces the
  trends (recurring imagery, repeated lines, and whether the model is obeying the
  prompt's ban on tired tropes). Outputs only — the model's input is never stored.
  Set `haiku_archive = false` to opt out.
```

- [ ] **Step 8: Run the full suite**

Run: `cd linux-bridge && uv run pytest -q`

Expected: PASS — 191 (185 + 6 new). **Every pre-existing `Bridge` test must still pass
untouched** — that is the proof the archive is decoupled from the daemon's hot path. If a
`Bridge` test broke, the wiring went in the wrong place.

- [ ] **Step 9: Commit**

```bash
git add linux-bridge/src/familiar/archive.py linux-bridge/src/familiar/config.py linux-bridge/src/familiar/daemon.py linux-bridge/src/familiar/cli.py linux-bridge/tests/test_archive.py README.md
git commit -m "feat: familiar haikus — browse the archive and its trends

Wire the writer as a decorator inside _make_compose rather than into
Bridge: cfg (the model id, the opt-out) is already in scope there, every
successful compose passes through it once, and Bridge stays ignorant that
an archive exists. haiku_archive = false opts out."
```

---

### Task 4: Verify it live

**Files:** none. Run by the controller.

- [ ] **Step 1: Redeploy**

```bash
uv tool install --force --reinstall ./linux-bridge
systemctl --user restart familiar.service
```

`--reinstall` is required: a plain `--force` serves a cached wheel for the unchanged version
`0.1.0` and silently reinstalls stale code.

- [ ] **Step 2: Trigger a haiku and confirm it lands**

Do some Claude Code activity to end a turn (haiku mode is on), then:

```bash
cat ~/.local/share/familiar/haikus.jsonl
```

Expected: at least one JSON line with `ts`, `lines`, `model`, `prompt`. **Confirm by eye that
it contains NO prompt text, NO reply text, and NO file paths** — that is the privacy line, and
it should be verified on real data rather than trusted.

- [ ] **Step 3: Read it back**

```bash
familiar haikus
familiar haikus --stats --limit 0
```

Expected: the haikus print; the stats print without crashing on a tiny sample.

---

## Notes for the implementer

- **Do not add the digest, the prompt, the reply, or the project name to the record**, however
  convenient it would make debugging. That exclusion is the whole reason this design was
  approved.
- **Do not let `append` raise.** If you find yourself adding a `raise` or removing the
  `except Exception`, stop — the daemon calls this on its compose path.
- **Do not "fix" `imagery` into a raw word count.** Document frequency is the metric on
  purpose; `test_imagery_is_document_frequency_not_raw_count` exists to stop exactly that.
- **Do not touch `Bridge`.** If a `Bridge` test breaks, the wiring went in the wrong place.
