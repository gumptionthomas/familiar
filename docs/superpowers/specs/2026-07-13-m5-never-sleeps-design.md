# M5 Never Sleeps — Design

**Goal:** Make `lineGen` bump only when the feed lines actually change, so the buddy can
enter clock mode, let its screen sleep, and hold its transcript scroll position.

## The bug

`src/data.h:111` decides whether the feed changed by comparing the last line against `msg`:

```c
if (n != out->nLines || (n > 0 && strcmp(out->lines[n-1], out->msg) != 0)) {
    out->lineGen++;
}
```

`msg` is the wrong thing to compare against, for two independent reasons — either alone is
enough to break it:

1. **`msg` is a status string, not a copy of the newest line.** `REFERENCE.md:50` documents
   it as e.g. `"approve: Bash"`, alongside a separate `entries` array. The two are not
   meant to match.
2. **`msg` is truncated.** The struct is `char msg[24]` vs `char lines[8][92]`
   (`data.h:14,16`), and `data.h:101` does `strncpy(out->msg, m, sizeof(out->msg)-1)`. So
   **any feed line longer than 23 characters can never compare equal to `msg`**, even when
   the daemon sets them from the same string.

Our bridge does set them from the same string (`state.py:194`,
`msg = entries[-1] if entries else ...`), so reason 2 is what bites us: a feed line of 24+
characters makes `strcmp` mismatch on **every heartbeat**, and `lineGen` increments
forever.

### Why haiku mode exposes it

At true idle with no entries, `n == 0`, the `strcmp` branch is skipped, and the bug hides.
But **haiku lines persist in the store after sessions are swept** — `snapshot()` builds
`combined` from `self._haiku`, which nothing clears — so `entries` is never empty once a
haiku has been generated. Haiku lines routinely exceed 23 characters. The daemon's
keepalive is **10 seconds** (`daemon.py:35`), so `lineGen` ticks 6×/minute indefinitely.

### What that breaks

Three consumers hang off `lineGen`, and all three are broken:

| Consumer | Symptom |
| --- | --- |
| `main.cpp:1027` — `lastFeedChangeMs = now` | the 120s `CLOCK_IDLE_GRACE_MS` never elapses → **`clocking` is never true; the buddy never enters clock mode and never sleeps** |
| `main.cpp:932` — `wake()` | **the screen is woken every 10s** → it never powers off |
| `main.cpp:932` — `msgScroll = 0` | **the transcript scroll resets every 10s** → scrolling with button B snaps back |

Observed 2026-07-13: the buddy stayed awake all night, never showing its sleeping pet.

**Correcting the record:** an earlier occurrence ("it hasn't gone to clock or sleep after
15 mins idle") was misdiagnosed as "the active Claude Code session is keeping it busy." The
overnight observation disproves that — there was no active session for eight hours. This
was the cause.

## Design

### 1. Extract the change detection into a pure, host-testable unit

The buggy logic is a function of data alone — "given the stored lines and an incoming
`entries` array, did the feed change?" — but it currently sits inside `data.h`, which
includes `<Arduino.h>` and calls `millis()`, `M5.Rtc.SetTime()`, and into `ble_bridge.h` /
`xfer.h`. That entanglement is the only reason it can't be tested.

New file **`src/feed.h`** — no Arduino, no M5, no I/O. ArduinoJson compiles natively, so
this is host-compilable:

```c
#pragma once
// Pure feed-line state: no Arduino, no M5, no I/O, so the change detection can
// be unit-tested on the host (test/test_feed).
#include <stdint.h>
#include <string.h>
#include <ArduinoJson.h>

#define FEED_MAX_LINES 8
#define FEED_LINE_CAP  92          // 91 chars + NUL

// Copy `entries` into `lines`; return true if the feed actually CHANGED (the
// count differs, or any line differs). The caller bumps lineGen on true.
//
// Do NOT reintroduce the old shortcut of comparing lines[n-1] against `msg`.
// `msg` is a short *status* field (REFERENCE.md) truncated to 23 chars, so any
// line longer than that mismatched on EVERY heartbeat -- lineGen ticked forever,
// which woke the screen every 10s and stopped the buddy ever reaching clock mode.
inline bool feedApplyEntries(char lines[FEED_MAX_LINES][FEED_LINE_CAP],
                             uint8_t* nLines, JsonArrayConst la) {
  uint8_t n = 0;
  bool changed = false;
  for (JsonVariantConst v : la) {
    if (n >= FEED_MAX_LINES) break;
    const char* s = v.as<const char*>();
    if (!s) s = "";
    if (strncmp(lines[n], s, FEED_LINE_CAP - 1) != 0) changed = true;
    strncpy(lines[n], s, FEED_LINE_CAP - 1);
    lines[n][FEED_LINE_CAP - 1] = 0;
    n++;
  }
  if (n != *nLines) changed = true;
  *nLines = n;
  return changed;
}
```

`src/data.h` then reduces to a call, and `lineGen` finally means what its comment
(`data.h:18`) always claimed — *"bumps when lines change"*:

```c
JsonArrayConst la = doc["entries"];
if (!la.isNull()) {
  if (feedApplyEntries(out->lines, &out->nLines, la)) out->lineGen++;
}
```

The comparison bound (`FEED_LINE_CAP - 1`) is the same bound used for the copy, so a line is
compared over exactly the range that is stored. Lines beyond `n` are left stale in the array
but are unreachable, guarded by `nLines`, exactly as today.

### 2. A native C++ test harness

This is the first automated firmware test in the project. It exists because this bug class —
pure state logic, no hardware — is precisely what on-device eyeballing keeps missing, and
what a host test catches instantly. There is direct precedent: the same calendar-mood
precedence, once ported to Python for the Tidbyt, had its midnight-vs-TGIF ordering bug
caught immediately by unit tests.

**`platformio.ini`** gains a native environment. `build_src_filter = -<*>` keeps it from
trying to compile the Arduino firmware; only the test file and ArduinoJson are built:

```ini
[env:native]
platform = native
test_framework = unity
build_flags = -std=gnu++17
build_src_filter = -<*>        ; don't compile the Arduino firmware natively
lib_compat_mode = off          ; ArduinoJson is header-only; allow the native platform
lib_deps =
    bblanchon/ArduinoJson @ ^7.0.0
```

**`test/test_feed/test_feed.cpp`** — Unity tests over `feedApplyEntries`, including the
regression that names this bug:

- first apply of N lines → **changed**, `nLines == N`
- identical re-apply → **not changed** ← the core fix
- identical re-apply of a line **longer than 23 characters** → **not changed** ← *the
  regression*: this is the exact case the `msg` proxy got wrong, and it fails against the
  old code
- one line differs → **changed**
- line count differs → **changed**
- more than `FEED_MAX_LINES` entries → capped at 8, no overflow
- a null array element → coerced to `""`, no crash

**CI** gains a `firmware tests (native)` job running `pio test -e native`, alongside the
existing bridge job. Pin the action SHAs, matching the existing workflow's convention.

**What this does NOT cover, stated plainly:** rendering, I²C, BLE, timing, and everything
else in `main.cpp` that touches hardware. Those still require the stick and human eyes. This
harness buys regression safety on parsing and pure state logic only. It is not a substitute
for the hardware verification below.

### 3. Relabel the AXP192 temperature

`main.cpp:645` prints the AXP192 power-management chip's die temperature as `temp %dC` on
the DEVICE info page. Two info pages one button-press apart now each show a line labelled
`temp` — one in °C (the chip) and one in °F (the room, on the ENV page) — and the DEVICE
one has no humidity beside it. That is a trap; it caught the project's own author.

Change it to `cpu %dC`.

## Scope

**Firmware + firmware test infrastructure.** The bridge is not changed.

The daemon setting `msg = entries[-1]` *is* a deviation from the documented protocol
(`msg` should be a short status). It is deliberately left alone:

- The firmware must be robust to any `msg` value regardless — per `REFERENCE.md`, `msg`
  differing from the newest entry is the **normal** case for upstream's desktop app.
- Fixing only the daemon would paper over the latent firmware defect, which would resurface
  the moment `msg` and the last entry legitimately differ.
- With the change-detector fixed, the daemon's behavior is harmless. `msg` is only rendered
  when `nLines == 0` (`main.cpp:937`), where the bridge sends the short `"idle"` /
  `"working"` strings that fit the 24-byte field.

## Error handling

No new failure modes. A null or absent `entries` array leaves the block untouched, as today.
A null string element is coerced to `""` (as today). `n` is still capped at 8, the array
bound.

## Verification

**Automated (new):** `pio test -e native` — the Unity tests above. `test_long_line_reapply`
must FAIL against the old `msg`-proxy code and PASS after the fix; that is the proof the
test has teeth. Runs in CI on every PR.

**On-device (still required — the automated tests reach none of this):**

1. `pio run` builds clean; flash over `/dev/ttyUSB0`.
2. **The bug, reproduced then fixed:** with a haiku loaded (so `entries` is non-empty with a
   >23-char line), leave the stick idle on USB. Before: the screen never sleeps and the
   clock never appears. After: within ~2 minutes (`CLOCK_IDLE_GRACE_MS`) it enters clock
   mode, and the pet sleeps overnight per the calendar moods.
3. **Scroll holds:** open the transcript and scroll with button B. It must stay where you
   put it instead of snapping to the top every 10 seconds.
4. **A real change still wakes it:** trigger activity and confirm the screen wakes and the
   new line appears — i.e. `lineGen` still bumps when it should. This guards against
   over-correcting into a feed that never updates.
5. DEVICE info page reads `cpu  NNC`; the ENV page still reads `temp NN F` with humidity.

## Out of scope

- Changing the wire protocol or what the bridge puts in `msg`.
- Clearing stale haiku lines from the store at idle (they are *correct* to persist — the
  Tidbyt and the M5 both show the last haiku; the bug was never that `entries` is
  non-empty).
- Landscape clock flicker (pre-existing, separately parked).
