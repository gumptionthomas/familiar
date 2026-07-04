# Tidbyt Calendar Moods — Design

**Goal:** Give the idle Tidbyt companion the same time-of-day / day-of-week
personality the M5 already shows in clock mode, so it shares the M5's
TGIF/weekend character instead of flatly dozing off.

## Background

The M5 firmware, once it drops into clock/screensaver mode, overlays a
calendar-driven mood on the idle pet (`src/main.cpp:1186-1199`): it sleeps
overnight, naps on weekends with the occasional heart, gets hearts at noon,
**celebrates on Friday afternoons (TGIF)**, goes woozy (`dizzy`) late at night,
and otherwise idles.

The Tidbyt has no equivalent. Its host-side persona (`daemon.py:_persona`)
rolls `idle → sleep` after ~300s of inactivity and stops there. So on a Friday
afternoon the M5 celebrates while the Tidbyt just sleeps — technically correct,
but personality-less.

This adds a **calendar-mood layer** to the Tidbyt's deep-idle behavior,
mirroring the M5's table.

## Key constraints

- **Every Tidbyt mood change is a network push** (HTTP to the Tidbyt API),
  unlike the M5's free local re-render. Mood cadence must be push-frugal.
- Real activity always wins: the existing `attention > heart > celebrate >
  busy` priority is unchanged. Calendar moods live only in the idle tail.
- Reuse existing per-species assets (`idle`, `sleep`, `celebrate`, `heart`).
  The only new art is `dizzy`.
- The daemon runs on Linux with the correct local timezone, so `datetime.now()`
  gives the right wall-clock hour/weekday directly (no time-sync plumbing like
  the M5's RTC needs).

## Design decisions (from brainstorming)

1. **Cadence — "occasional flourish."** Each time window has a calm *baseline*
   mood plus, for some windows, a *flourish* that appears briefly and
   periodically. Not the M5's ~4s flicker (too many pushes); not a single held
   mood (too static). ~20s flourish every 5 min.
2. **Engage timing — grace period first.** The normal idle animation plays for
   the existing ~300s window after activity stops; only then does the calendar
   personality take over. Mirrors the M5, whose moods appear only once it's in
   clock/screensaver mode. The pet won't nod off the instant you stop typing.
   The late-night `dizzy` window is hours 22, 23, 0 (10pm–12:59am); the deep
   1–7am sleep branch covers 1am onward, so the two don't overlap.
3. **`dizzy` — build the asset.** A generic Tidbyt-native dizzy (each species'
   idle pose + an orbiting "woozy" particle), rendered for all 18 ASCII species
   and bufo. bufo already ships a `dizzy.gif` source.

## The mood table

A pure function `calendar_mood(dt) -> (baseline, flourish | None)`, a direct
port of the M5's precedence:

| When (idle) | baseline | flourish | Source (M5) |
|---|---|---|---|
| 1am–6:59am | `sleep` | — | `h>=1 && h<7` |
| Weekend (any other hour) | `sleep` | `heart` | `weekend` |
| Before 9am (weekday) | `sleep` | `idle` | `h<9` |
| Noon (12:00–12:59) | `idle` | `heart` | `h==12` |
| **Friday ≥ 3pm** | `idle` | `celebrate` | `friday && h>=15` |
| 10pm–12:59am (hours 22, 23, 0) | `sleep` | `dizzy` | `h>=22 || h==0` |
| Otherwise (daytime) | `idle` | `sleep` | else |

Precedence is top-to-bottom and matters: 1–7am beats everything (including
weekends); the weekend row beats noon / TGIF / late-night. `weekday()` is
Python's Mon=0…Sun=6, so `friday = wd == 4` and `weekend = wd >= 5`.

```python
def calendar_mood(dt):
    h, wd = dt.hour, dt.weekday()
    weekend, friday = wd >= 5, wd == 4
    if 1 <= h < 7:          return ("sleep", None)
    if weekend:             return ("sleep", "heart")
    if h < 9:               return ("sleep", "idle")
    if h == 12:             return ("idle",  "heart")
    if friday and h >= 15:  return ("idle",  "celebrate")
    if h >= 22 or h == 0:   return ("sleep", "dizzy")
    return ("idle", "sleep")
```

## Architecture

### Module: `linux-bridge/src/familiar/calendar_mood.py` (new)

Holds only the pure `calendar_mood(dt)` above. No IO, no clock reads — the
caller passes the datetime. This keeps it exhaustively unit-testable.

### `daemon.py` — deep-idle wiring

`_persona(snap, now)` keeps its real-activity chain unchanged
(`attention > heart > celebrate > busy`). Only the idle tail changes:

- **Grace window** — `now - self._tb_active_at < self.tb_sleep_after`
  (the existing ~300s): return `"idle"`, exactly as today.
- **Deep idle** — past the grace window: return `self._deep_idle_state(now)`
  instead of a flat `"sleep"`.

New helper and constants on `Bridge`:

```python
self.tb_flourish_period = 300.0   # a flourish comes round every 5 min
self.tb_flourish_secs   = 20.0    # and shows for ~20s
self._wall_clock = datetime.now   # injectable for tests

def _deep_idle_state(self, now):          # now = monotonic loop clock
    baseline, flourish = calendar_mood(self._wall_clock())
    if flourish and (now % self.tb_flourish_period) < self.tb_flourish_secs:
        return flourish
    return baseline
```

The flourish schedule is derived deterministically from the monotonic loop
clock (`now`), so it is testable without wall-clock mocking of the schedule
itself; only the *mood selection* consults `_wall_clock`. The existing
`_push_loop` keepalive (~10s) drives re-evaluation, so flourish transitions
reach the device within ~10s — no new timer needed.

Because a real completion refreshes `self._tb_active_at` (via the `celebrate`/
`heart`/`busy` branches), finishing a turn resets the grace window: the pet
returns to normal idle for another ~300s before the calendar personality
resumes.

`tb_sleep_after` keeps its name (churn-minimizing) but its meaning broadens
from "time until sleep" to "time until the calendar personality engages." A
one-line comment will note this.

### Data flow

```
_push_loop tick (dirty event, or ~10s keepalive)
  -> _tidbyt_sync(snap)
       -> _tidbyt_decide(snap, loop_now)
            -> _persona(snap, loop_now)
                 attention/heart/celebrate/busy (real activity)  -> that state
                 within grace window                             -> "idle"
                 deep idle -> _deep_idle_state(loop_now)
                                -> calendar_mood(wall_now) -> (baseline, flourish)
                                -> flourish if in flourish window else baseline
       -> push <state>.webp if it differs from _tb_current
          (dedup + retry-on-failed-push, per the PR #40 fix)
```

Flourish states reuse existing assets, so `_tidbyt_sync` needs no changes; it
just receives `"dizzy"` as a possible asset name and pushes
`<asset_dir>/dizzy.webp`.

## The `dizzy` asset

### ASCII species (18) — `tools/render_ascii_pet.py`

`dizzy` has no pose art in the species `.cpp` files (it's inconsistently
defined and the extractor ignores it), so it is **synthesized** from the idle
pose, matching the established "Tidbyt-native generic particle" approach:

- Add a `_particle("dizzy", i, n)` branch: a couple of small stars (`*`)
  orbiting above the head across the frame cycle (a simple rotation in x/y over
  `i`), in a woozy blue/violet. Distinct from the plain idle frames.
- After rendering the extracted states, synthesize a `dizzy` render by reusing
  the already-extracted **idle** state's `frames`/`color`/`divisor`, but
  rendering with `state="dizzy"` so `_particle` and the frame delay come out
  right. Written to `tidbyt_buddy/<species>/dizzy.webp`.
- If a species has no idle state extracted (shouldn't happen — all ship idle),
  skip dizzy for it rather than crash.

### bufo — `tools/build_tidbyt_buddy.py`

Add `"dizzy"` to `STATES`. bufo already has `characters/bufo/dizzy.gif`, so the
existing `convert()` path produces `tidbyt_buddy/dizzy.webp` with no new code.

### Rename-fallout fix (prerequisite)

Both build tools still write to the pre-rename path
`linux-bridge/src/claude_buddy/tidbyt_buddy`, but assets now live under
`.../familiar/tidbyt_buddy`. Regenerating any asset today would silently write
into a dead directory. Fix both:

- `render_ascii_pet.py` `OUT_ROOT`: `claude_buddy` → `familiar`
- `build_tidbyt_buddy.py` `DST`: `claude_buddy` → `familiar`

## Testing

**`tests/test_calendar_mood.py` (new)** — exhaustive, using fixed
`datetime(...)` values (no mocking needed, pure function):
- Each branch returns the expected `(baseline, flourish)`.
- Precedence: a Saturday at noon → `(sleep, heart)` (weekend beats noon); a
  Sunday at 3am → `(sleep, None)` (1–7am beats weekend); a Friday at 4pm →
  `(idle, celebrate)`; a Wednesday at 11pm → `(sleep, dizzy)`; a Tuesday at
  2pm → `(idle, sleep)`.

**`tests/test_daemon.py` (extend)** — with injected `_wall_clock` and explicit
loop-`now` values:
- Grace window (`now - _tb_active_at < tb_sleep_after`) → `_persona` returns
  `"idle"` regardless of calendar.
- Deep idle, outside a flourish sub-window → returns the calendar baseline.
- Deep idle, inside a flourish sub-window → returns the flourish.
- A `dizzy` deep-idle result at a late-night wall time.
- Real activity (waiting / running / celebrate window) still overrides deep
  idle.

**Asset generation** is a build step, not a pytest: run
`render_ascii_pet.py` over all species and `build_tidbyt_buddy.py`, confirm 19
`dizzy.webp` files appear under `familiar/tidbyt_buddy/`, then verify on the
live Tidbyt (dizzy reads as woozy; TGIF/weekend moods appear at the right
times). pixlet v0.34.0 is confirmed available.

## Out of scope (YAGNI)

- No new dizzy *pose art* per species (generic particle over idle instead).
- No per-species mood customization — one shared table.
- No config knobs for the mood table or flourish cadence (constants for now;
  can be lifted to config later if wanted).
- No changes to the M5 firmware — it already has this behavior.
