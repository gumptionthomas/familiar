# Tidbyt Calendar Moods Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the idle Tidbyt the M5's time-of-day / day-of-week personality (weekend naps, noon hearts, Friday TGIF, late-night woozy) instead of flatly sleeping.

**Architecture:** A pure `calendar_mood(dt)` function returns a `(baseline, flourish)` pair ported from the M5's clock-mode table. The daemon's `_persona` deep-idle branch (past the existing ~300s grace window) consults it and shows the calm baseline, breaking into the flourish for ~20s every 5 min. Flourishes reuse existing per-species assets; the one new asset, `dizzy`, is generated as each species' idle pose plus a generic "woozy" particle overlay.

**Tech Stack:** Python 3.11+ (asyncio daemon), pytest, Pillow (bufo build), pixlet v0.34.0 (ASCII pet render, build-time only).

## Global Constraints

- Tests run with `uv run pytest -q` from `linux-bridge/`. Full suite must stay green (135 tests + the new ones).
- The Python package is `familiar` (under `linux-bridge/src/familiar/`); assets live at `linux-bridge/src/familiar/tidbyt_buddy/`. The pre-rename name `claude_buddy` is dead — never reintroduce it.
- `datetime` is already imported in `daemon.py` (`from datetime import date, datetime`).
- Weekday convention is Python's `datetime.weekday()`: Mon=0 … Sun=6, so `friday = wd == 4`, `weekend = wd >= 5`.
- Real activity always overrides idle moods: the `attention > heart > celebrate > busy` chain in `_persona` is unchanged.
- Every Tidbyt mood change is a network push, so the flourish cadence must stay frugal (~20s flourish per 5-min window).
- Reference weekdays for tests (verify once with `date -d <YYYY-MM-DD> +%A` if unsure): 2026-07-06 = Monday, 07-07 = Tuesday, 07-08 = Wednesday, 07-10 = Friday, 07-11 = Saturday, 07-12 = Sunday.
- No config knobs, no firmware changes, no new per-species pose art (generic particle only).

---

### Task 1: `calendar_mood` pure function

**Files:**
- Create: `linux-bridge/src/familiar/calendar_mood.py`
- Test: `linux-bridge/tests/test_calendar_mood.py`

**Interfaces:**
- Produces: `calendar_mood(dt: datetime) -> tuple[str, str | None]` returning `(baseline, flourish)`. `baseline` is always a state name (`"sleep"` or `"idle"`); `flourish` is a state name (`"heart"`, `"idle"`, `"celebrate"`, `"dizzy"`, `"sleep"`) or `None`.

- [ ] **Step 1: Write the failing tests**

Create `linux-bridge/tests/test_calendar_mood.py`:

```python
from datetime import datetime
from familiar.calendar_mood import calendar_mood


def test_deep_night_sleeps_no_flourish():
    assert calendar_mood(datetime(2026, 7, 8, 3, 0)) == ("sleep", None)     # Wed 3am


def test_deep_night_beats_weekend():
    assert calendar_mood(datetime(2026, 7, 12, 3, 0)) == ("sleep", None)    # Sun 3am


def test_weekend_naps_with_heart():
    assert calendar_mood(datetime(2026, 7, 11, 14, 0)) == ("sleep", "heart")  # Sat 2pm


def test_weekend_beats_noon():
    assert calendar_mood(datetime(2026, 7, 12, 12, 30)) == ("sleep", "heart") # Sun 12:30


def test_early_weekday_sleeps_with_idle():
    assert calendar_mood(datetime(2026, 7, 6, 8, 0)) == ("sleep", "idle")   # Mon 8am


def test_noon_weekday_hearts():
    assert calendar_mood(datetime(2026, 7, 6, 12, 15)) == ("idle", "heart") # Mon 12:15


def test_friday_afternoon_tgif():
    assert calendar_mood(datetime(2026, 7, 10, 16, 0)) == ("idle", "celebrate")  # Fri 4pm


def test_friday_before_3pm_is_normal_day():
    assert calendar_mood(datetime(2026, 7, 10, 14, 0)) == ("idle", "sleep")  # Fri 2pm


def test_late_night_dizzy():
    assert calendar_mood(datetime(2026, 7, 8, 23, 0)) == ("sleep", "dizzy") # Wed 11pm


def test_midnight_hour_dizzy():
    assert calendar_mood(datetime(2026, 7, 8, 0, 30)) == ("sleep", "dizzy") # Wed 00:30


def test_normal_daytime_idle():
    assert calendar_mood(datetime(2026, 7, 7, 14, 0)) == ("idle", "sleep")  # Tue 2pm
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_calendar_mood.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'familiar.calendar_mood'`.

- [ ] **Step 3: Write the implementation**

Create `linux-bridge/src/familiar/calendar_mood.py`:

```python
"""Time-of-day / day-of-week mood for the idle Tidbyt, ported from the M5's
clock-mode calendar (src/main.cpp). Pure function of the given datetime."""


def calendar_mood(dt):
    """Return (baseline, flourish | None) for local datetime `dt`.

    baseline is the calm mood held most of the time; flourish (if any) is the
    brief periodic accent. Both are Tidbyt state/asset names. Precedence
    matches the firmware: 1-7am beats weekend beats the rest.
    """
    h, wd = dt.hour, dt.weekday()          # weekday(): Mon=0 .. Sun=6
    weekend, friday = wd >= 5, wd == 4
    if 1 <= h < 7:          return ("sleep", None)
    if weekend:             return ("sleep", "heart")
    if h < 9:               return ("sleep", "idle")
    if h == 12:             return ("idle",  "heart")
    if friday and h >= 15:  return ("idle",  "celebrate")
    if h >= 22 or h == 0:   return ("sleep", "dizzy")
    return ("idle", "sleep")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd linux-bridge && uv run pytest tests/test_calendar_mood.py -q`
Expected: PASS — 11 passed.

- [ ] **Step 5: Commit**

```bash
git add linux-bridge/src/familiar/calendar_mood.py linux-bridge/tests/test_calendar_mood.py
git commit -m "feat: calendar_mood table for the Tidbyt idle personality"
```

---

### Task 2: Wire deep-idle moods into the daemon

**Files:**
- Modify: `linux-bridge/src/familiar/daemon.py` (imports; `Bridge.__init__` constants near line 63; new `_deep_idle_state`; `_persona` deep-idle branch near line 217)
- Test: `linux-bridge/tests/test_daemon.py` (append new tests)

**Interfaces:**
- Consumes: `calendar_mood(dt) -> (baseline, flourish)` from Task 1.
- Produces: `Bridge._deep_idle_state(now: float) -> str`; new attributes `Bridge.tb_flourish_period` (float, 300.0), `Bridge.tb_flourish_secs` (float, 20.0), `Bridge._wall_clock` (callable returning a `datetime`, defaults to `datetime.now`).

Reference — the current `_persona` tail to change (`daemon.py:215-219`):

```python
        if self._tb_active_at is None:
            self._tb_active_at = now
        if now - self._tb_active_at >= self.tb_sleep_after:
            return "sleep"
        return "idle"
```

- [ ] **Step 1: Write the failing tests**

Append to `linux-bridge/tests/test_daemon.py`. Add `from datetime import datetime` to the imports at the top of the file if not already present, then add these tests (the `_bridge_tb()` helper already exists in this file):

```python
def test_persona_grace_window_returns_idle():
    # Within tb_sleep_after of the last activity, calendar moods don't apply.
    b = _bridge_tb()
    b._wall_clock = lambda: datetime(2026, 7, 10, 16, 0)     # Fri 4pm (would be TGIF)
    b._tb_active_at = 100.0
    assert b._persona({}, 300.0) == "idle"                   # 200s idle < 300s grace


def test_persona_deep_idle_returns_calendar_baseline():
    b = _bridge_tb()
    b._wall_clock = lambda: datetime(2026, 7, 10, 16, 0)     # Fri 4pm -> (idle, celebrate)
    b._tb_active_at = 0.0
    now = b.tb_sleep_after + b.tb_flourish_secs + 1.0        # deep idle, outside flourish
    assert b._persona({}, now) == "idle"                     # baseline


def test_persona_deep_idle_flourish_window_returns_flourish():
    b = _bridge_tb()
    b._wall_clock = lambda: datetime(2026, 7, 10, 16, 0)     # Fri 4pm -> flourish "celebrate"
    b._tb_active_at = 0.0
    now = b.tb_flourish_period * 3                           # phase 0 -> inside flourish window
    assert now >= b.tb_sleep_after
    assert b._persona({}, now) == "celebrate"


def test_persona_deep_idle_late_night_dizzy():
    b = _bridge_tb()
    b._wall_clock = lambda: datetime(2026, 7, 8, 23, 0)      # Wed 11pm -> (sleep, dizzy)
    b._tb_active_at = 0.0
    base_now = b.tb_flourish_period * 2 + b.tb_flourish_secs + 5   # outside flourish
    assert b._persona({}, base_now) == "sleep"
    assert b._persona({}, b.tb_flourish_period * 2) == "dizzy"     # inside flourish


def test_persona_real_activity_overrides_deep_idle():
    b = _bridge_tb()
    b._wall_clock = lambda: datetime(2026, 7, 10, 16, 0)
    b._tb_active_at = 0.0
    now = b.tb_sleep_after + 1.0
    assert b._persona({"waiting": 1}, now) == "attention"
    assert b._persona({"running": 1}, now) == "busy"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_daemon.py -q -k "grace_window or deep_idle or real_activity_overrides"`
Expected: FAIL — `AttributeError: 'Bridge' object has no attribute 'tb_flourish_period'` (and/or `_wall_clock`).

- [ ] **Step 3: Add the import**

In `linux-bridge/src/familiar/daemon.py`, add the import next to the existing package imports (after line 8, `from . import haiku, heartbeat, tidbyt, transcript`):

```python
from .calendar_mood import calendar_mood
```

- [ ] **Step 4: Add the constants and injectable clock**

In `Bridge.__init__`, replace the existing `tb_sleep_after` line (`daemon.py:63`):

```python
        self.tb_sleep_after = 300.0     # doze off after this long with no activity
```

with:

```python
        self.tb_sleep_after = 300.0     # normal idle before the calendar personality engages
        self.tb_flourish_period = 300.0  # a mood flourish comes round every 5 min
        self.tb_flourish_secs = 20.0     # and shows for ~20s of each period
        self._wall_clock = datetime.now  # local wall clock; injectable for tests
```

- [ ] **Step 5: Add `_deep_idle_state` and rewire `_persona`**

In `_persona`, replace the tail (`daemon.py:217-219`):

```python
        if now - self._tb_active_at >= self.tb_sleep_after:
            return "sleep"
        return "idle"
```

with:

```python
        if now - self._tb_active_at >= self.tb_sleep_after:
            return self._deep_idle_state(now)
        return "idle"
```

Then add this method immediately after `_persona` (before `_tidbyt_decide`):

```python
    def _deep_idle_state(self, now):
        # Past the grace window the idle pet takes on a time-of-day / day-of-week
        # personality (the M5's clock-mode moods). A calm baseline holds, with a
        # brief flourish for the first tb_flourish_secs of each tb_flourish_period.
        baseline, flourish = calendar_mood(self._wall_clock())
        if flourish and (now % self.tb_flourish_period) < self.tb_flourish_secs:
            return flourish
        return baseline
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `cd linux-bridge && uv run pytest tests/test_daemon.py -q -k "grace_window or deep_idle or real_activity_overrides"`
Expected: PASS — 5 passed.

- [ ] **Step 7: Make the existing quiet-stretch test deterministic**

The existing `test_persona_sleeps_after_quiet_stretch` (`tests/test_daemon.py`, line 248) asserts `_persona(..., 1301.0) == "sleep"` on line 253. That call now reaches the deep-idle branch, so its result depends on the wall-clock mood — it would pass at night but fail during the day. Pin its clock to a deterministic `("sleep", None)` window by inserting one line right after `b = _bridge_tb()` at the top of that test:

```python
def test_persona_sleeps_after_quiet_stretch():
    b = _bridge_tb()
    b._wall_clock = lambda: datetime(2026, 7, 8, 3, 0)   # Wed 3am -> ("sleep", None)
    b.tb_sleep_after = 300.0
    assert b._persona({"running": 0, "waiting": 0}, 1000.0) == "idle"   # latches active_at
    assert b._persona({"running": 0, "waiting": 0}, 1200.0) == "idle"   # 200s < 300s
    assert b._persona({"running": 0, "waiting": 0}, 1301.0) == "sleep"  # 301s >= 300s
    # activity wakes it back up
    assert b._persona({"running": 1, "waiting": 0}, 1400.0) == "busy"
    assert b._persona({"running": 0, "waiting": 0}, 1450.0) == "idle"   # active_at reset
```

(No other existing test reaches the deep-idle branch — `test_persona_mapping`, the celebrate/heart tests, and the `_tidbyt_decide` tests all stay within the grace window or a real-activity branch.)

- [ ] **Step 8: Run the full suite**

Run: `cd linux-bridge && uv run pytest -q`
Expected: PASS — all tests green (135 existing + 11 from Task 1 + 5 new here = 151).

- [ ] **Step 9: Commit**

```bash
git add linux-bridge/src/familiar/daemon.py linux-bridge/tests/test_daemon.py
git commit -m "feat: idle Tidbyt takes on calendar moods past the grace window"
```

---

### Task 3: `dizzy` asset generation + build-tool path fixes

**Files:**
- Modify: `tools/render_ascii_pet.py` (fix `OUT_ROOT`; add a `dizzy` particle; refactor the per-state render into a helper; synthesize `dizzy` from idle)
- Modify: `tools/build_tidbyt_buddy.py` (fix `DST`; add `"dizzy"` to `STATES`)
- Create (build artifacts, committed): `linux-bridge/src/familiar/tidbyt_buddy/<species>/dizzy.webp` (18) and `linux-bridge/src/familiar/tidbyt_buddy/dizzy.webp` (bufo)

**Interfaces:**
- Consumes: nothing from Tasks 1-2. The daemon (Task 2) will request `dizzy.webp` at runtime for late-night flourishes, so these files must exist.

This task is a build + verification task, not pytest-TDD: the deliverable is 19 rendered `.webp` files plus the two path fixes that make regeneration land in the live asset directory. pixlet v0.34.0 must be on PATH (confirmed at `~/.local/bin/pixlet`). Run all commands from the repo root.

- [ ] **Step 1: Fix the stale output paths**

In `tools/render_ascii_pet.py`, change `OUT_ROOT` (line 23-24) from `"claude_buddy"` to `"familiar"`:

```python
OUT_ROOT = os.path.join(os.path.dirname(__file__), os.pardir,
                        "linux-bridge", "src", "familiar", "tidbyt_buddy")
```

In `tools/build_tidbyt_buddy.py`, change `DST` (line 11):

```python
DST = "linux-bridge/src/familiar/tidbyt_buddy"
```

- [ ] **Step 2: Add the `dizzy` particle**

In `tools/render_ascii_pet.py`, inside `_particle`, add this branch immediately before the final `# idle: no particle` comment / `return []`:

```python
    if state == "dizzy":
        # Two little stars orbiting above the head — a woozy spin. Positions are
        # a 4-point loop at head height; the two stars sit opposite each other.
        orbit = [(30, 1), (36, 3), (34, 6), (28, 4)]
        out = []
        for k in range(2):
            x, y = orbit[(i + k * 2) % len(orbit)]
            out.append(("*", x, y, "#c9a0ff"))
        return out
```

- [ ] **Step 3: Refactor the render step into a helper and synthesize `dizzy`**

In `tools/render_ascii_pet.py`, replace the whole `render_species` function (lines 132-153) with a helper plus a slimmer `render_species` that also emits `dizzy`:

```python
def _render_one(out_dir, state, data):
    # pixlet treats the .star's directory as the app bundle and globs sibling
    # *.star files, so give each render its own clean dir.
    tmp = tempfile.mkdtemp()
    star_path = os.path.join(tmp, "app.star")
    with open(star_path, "w") as f:
        f.write(_star(state, data))
    out = os.path.join(out_dir, state + ".webp")
    try:
        subprocess.run([PIXLET, "render", star_path, "-o", out],
                       check=True, capture_output=True, text=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%-10s %2d frames -> %s" % (state, len(data["frames"]), out))


def render_species(cpp_path):
    name = os.path.splitext(os.path.basename(cpp_path))[0]
    states = extract_buddies.extract(cpp_path)
    out_dir = os.path.join(OUT_ROOT, name)
    os.makedirs(out_dir, exist_ok=True)
    for state, data in states.items():
        if data["frames"]:
            _render_one(out_dir, state, data)
    # dizzy has no pose art of its own; synthesize it from the idle pose plus
    # the woozy particle so every species has a late-night mood.
    idle = states.get("idle")
    if idle and idle["frames"]:
        _render_one(out_dir, "dizzy", idle)
    return out_dir
```

- [ ] **Step 4: Add `dizzy` to the bufo build**

In `tools/build_tidbyt_buddy.py`, append `"dizzy"` to `STATES` (line 18-19). bufo already has `characters/bufo/dizzy.gif`, so no other change is needed:

```python
STATES = ["idle_0", "idle_1", "idle_2", "idle_3", "idle_4", "idle_5", "idle_6",
          "idle_7", "idle_8", "busy", "attention", "celebrate", "sleep", "heart",
          "dizzy"]
```

- [ ] **Step 5: Regenerate the assets**

```bash
uv run python tools/render_ascii_pet.py src/buddies/*.cpp
uv run --with pillow python tools/build_tidbyt_buddy.py
```

Expected: each species prints a `dizzy … -> …/dizzy.webp` line; bufo prints a `dizzy -> dizzy.webp` line.

- [ ] **Step 6: Verify the assets exist and the diff is dizzy-only**

```bash
ls linux-bridge/src/familiar/tidbyt_buddy/*/dizzy.webp | wc -l   # expect 18
ls -l linux-bridge/src/familiar/tidbyt_buddy/dizzy.webp          # bufo, exists
git status --short linux-bridge/src/familiar/tidbyt_buddy/
```

Expected: the only changes are new `dizzy.webp` files. If existing (non-dizzy) webps show as **modified** — that is pixlet-version drift, not part of this change. Revert them so the PR stays scoped to dizzy:

```bash
git diff --name-only linux-bridge/src/familiar/tidbyt_buddy/ | grep -v '/dizzy\.webp$' | xargs -r git checkout --
```

- [ ] **Step 7: Commit**

```bash
git add tools/render_ascii_pet.py tools/build_tidbyt_buddy.py linux-bridge/src/familiar/tidbyt_buddy/
git commit -m "feat: generate a woozy dizzy asset for every Tidbyt pet; fix build paths"
```

---

## Final verification (after all tasks)

- [ ] Full suite green: `cd linux-bridge && uv run pytest -q`
- [ ] 19 dizzy assets present: `ls linux-bridge/src/familiar/tidbyt_buddy/*/dizzy.webp linux-bridge/src/familiar/tidbyt_buddy/dizzy.webp | wc -l` → 19
- [ ] Hardware check (manual, owner): with the daemon running against the live Tidbyt, confirm the deep-idle pet shows the right baseline for the current time and that a flourish (and dizzy at night) reads correctly. Nudge the `dizzy` particle `orbit` coordinates in `_particle` and re-render if the stars sit awkwardly on the head.
