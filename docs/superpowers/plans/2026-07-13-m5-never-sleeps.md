# M5 Never Sleeps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `lineGen` bump only when the feed lines actually change, so the buddy can enter clock mode, let its screen sleep, and hold its transcript scroll — and establish the project's first automated firmware tests so this bug class can't return silently.

**Architecture:** Extract the "did the feed change?" logic out of `src/data.h` (which is welded to `<Arduino.h>`, `millis()`, and `M5.Rtc`) into a new pure header `src/feed.h` that depends only on `stdint`, `string.h`, and ArduinoJson — and is therefore host-compilable. Add a PlatformIO `native` environment with Unity to test it, then wire `data.h` to call it.

**Tech Stack:** C++17, PlatformIO, Unity (PlatformIO's bundled test framework), ArduinoJson 7.x, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-13-m5-never-sleeps-design.md`

## Global Constraints

- **`src/feed.h` must NOT include `<Arduino.h>`, any `M5*` header, `ble_bridge.h`, or `xfer.h`, and must not call `millis()` or any I/O.** If it does, it stops compiling on the host and the entire harness is pointless. Its only includes are `<stdint.h>`, `<string.h>`, `<ArduinoJson.h>`.
- **Never reintroduce comparing a feed line against `msg`.** `msg` is a short *status* field (`REFERENCE.md:50`) truncated to 23 chars by `data.h`; comparing a 91-char line to it mismatches forever. That IS the bug.
- Exact values are binding: `FEED_MAX_LINES = 8`, `FEED_LINE_CAP = 92` (91 chars + NUL). These must match the existing `char lines[8][92]` in `TamaState` (`src/data.h:16`) — a mismatch is a buffer overflow.
- The firmware build must keep working: `pio run` (env `m5stickc-plus`) must compile clean after every task.
- CI actions must be **pinned to a SHA**, matching the existing `.github/workflows/ci.yml` convention. Do not add an unpinned action.
- Do not change the bridge (`linux-bridge/`) or the wire protocol.

---

### Task 1: Native test harness + the pure `feed.h`

**Files:**
- Create: `src/feed.h`
- Create: `test/test_feed/test_feed.cpp`
- Modify: `platformio.ini`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `src/feed.h` defining `FEED_MAX_LINES` (8), `FEED_LINE_CAP` (92), and
    ```c
    inline bool feedApplyEntries(char lines[FEED_MAX_LINES][FEED_LINE_CAP],
                                 uint8_t* nLines, JsonArrayConst la);
    ```
    Copies `la` into `lines`, sets `*nLines`, and **returns `true` if the feed actually changed** (count differs, or any line differs). Task 2 calls this and bumps `lineGen` on `true`.

**Background:** `src/data.h:111` currently decides "did the feed change?" with
`strcmp(out->lines[n-1], out->msg)`. `msg` is `char[24]` and lines are `char[92]`, so any
line longer than 23 characters can never compare equal — `lineGen` increments on *every*
heartbeat (every 10s). That calls `wake()` and resets the clock-mode idle timer, so the
buddy never sleeps. This task builds the test bed and the correct logic; Task 2 wires it in.

- [ ] **Step 1: Add the native test environment**

Append to `platformio.ini` (leave the existing `[env:m5stickc-plus]` block untouched):

```ini
[env:native]
platform = native
test_framework = unity
build_flags = -std=gnu++17
build_src_filter = -<*>        ; don't compile the Arduino firmware on the host
lib_compat_mode = off          ; ArduinoJson is header-only; allow the native platform
lib_deps =
    bblanchon/ArduinoJson @ ^7.0.0
```

- [ ] **Step 2: Write the failing tests**

Create `test/test_feed/test_feed.cpp`:

```cpp
#include <unity.h>
#include <ArduinoJson.h>
#include "../../src/feed.h"

static char    lines[FEED_MAX_LINES][FEED_LINE_CAP];
static uint8_t nLines;

void setUp(void)    { memset(lines, 0, sizeof(lines)); nLines = 0; }
void tearDown(void) {}

// Parse `json` and apply its "entries" array. Returns whether the feed changed.
static bool apply(const char* json) {
  JsonDocument doc;
  deserializeJson(doc, json);
  return feedApplyEntries(lines, &nLines, doc["entries"].as<JsonArrayConst>());
}

void test_first_apply_is_a_change(void) {
  TEST_ASSERT_TRUE(apply("{\"entries\":[\"alpha\",\"beta\"]}"));
  TEST_ASSERT_EQUAL_UINT8(2, nLines);
  TEST_ASSERT_EQUAL_STRING("alpha", lines[0]);
  TEST_ASSERT_EQUAL_STRING("beta",  lines[1]);
}

void test_identical_reapply_is_not_a_change(void) {
  apply("{\"entries\":[\"alpha\",\"beta\"]}");
  TEST_ASSERT_FALSE(apply("{\"entries\":[\"alpha\",\"beta\"]}"));
}

// THE REGRESSION. msg is char[24], lines are char[92]. The old code compared the
// newest line against the truncated msg, so any line longer than 23 chars could
// never compare equal -> lineGen ticked on EVERY heartbeat -> the buddy never
// slept. This line is 38 chars. It must still be recognised as unchanged.
void test_long_line_reapply_is_not_a_change(void) {
  const char* j = "{\"entries\":[\"the cursor blinks in the quiet dark\"]}";
  TEST_ASSERT_TRUE(apply(j));    // first time: a real change
  TEST_ASSERT_FALSE(apply(j));   // second time: identical -> NOT a change
}

void test_changed_line_is_a_change(void) {
  apply("{\"entries\":[\"alpha\",\"beta\"]}");
  TEST_ASSERT_TRUE(apply("{\"entries\":[\"alpha\",\"gamma\"]}"));
  TEST_ASSERT_EQUAL_STRING("gamma", lines[1]);
}

void test_changed_count_is_a_change(void) {
  apply("{\"entries\":[\"alpha\",\"beta\"]}");
  TEST_ASSERT_TRUE(apply("{\"entries\":[\"alpha\"]}"));
  TEST_ASSERT_EQUAL_UINT8(1, nLines);
}

void test_empty_entries_after_content_is_a_change(void) {
  apply("{\"entries\":[\"alpha\"]}");
  TEST_ASSERT_TRUE(apply("{\"entries\":[]}"));
  TEST_ASSERT_EQUAL_UINT8(0, nLines);
  // ...and staying empty is then stable (this is the idle steady state).
  TEST_ASSERT_FALSE(apply("{\"entries\":[]}"));
}

void test_caps_at_max_lines(void) {
  TEST_ASSERT_TRUE(apply(
    "{\"entries\":[\"1\",\"2\",\"3\",\"4\",\"5\",\"6\",\"7\",\"8\",\"9\",\"10\"]}"));
  TEST_ASSERT_EQUAL_UINT8(FEED_MAX_LINES, nLines);
  TEST_ASSERT_EQUAL_STRING("8", lines[7]);
}

void test_null_element_becomes_empty_string(void) {
  TEST_ASSERT_TRUE(apply("{\"entries\":[null,\"beta\"]}"));
  TEST_ASSERT_EQUAL_UINT8(2, nLines);
  TEST_ASSERT_EQUAL_STRING("", lines[0]);
}

void test_overlong_line_is_truncated_not_overflowed(void) {
  // 200 'x' chars; must be stored truncated to FEED_LINE_CAP-1 and NUL-terminated.
  char buf[256];
  snprintf(buf, sizeof(buf), "{\"entries\":[\"%.*s\"]}", 200,
           "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
           "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
           "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
           "xxxxxxxx");
  apply(buf);
  TEST_ASSERT_EQUAL_UINT8(FEED_LINE_CAP - 1, (uint8_t)strlen(lines[0]));
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_first_apply_is_a_change);
  RUN_TEST(test_identical_reapply_is_not_a_change);
  RUN_TEST(test_long_line_reapply_is_not_a_change);
  RUN_TEST(test_changed_line_is_a_change);
  RUN_TEST(test_changed_count_is_a_change);
  RUN_TEST(test_empty_entries_after_content_is_a_change);
  RUN_TEST(test_caps_at_max_lines);
  RUN_TEST(test_null_element_becomes_empty_string);
  RUN_TEST(test_overlong_line_is_truncated_not_overflowed);
  return UNITY_END();
}
```

- [ ] **Step 3: Write a DELIBERATELY BUGGY `feed.h` to prove the tests have teeth**

The old code's *effect* was "always report a change" (for any line over 23 chars). Reproduce
that effect first, so the tests go red for the right reason — not merely because a symbol is
missing.

Create `src/feed.h`:

```cpp
#pragma once
#include <stdint.h>
#include <string.h>
#include <ArduinoJson.h>

#define FEED_MAX_LINES 8
#define FEED_LINE_CAP  92          // 91 chars + NUL

inline bool feedApplyEntries(char lines[FEED_MAX_LINES][FEED_LINE_CAP],
                             uint8_t* nLines, JsonArrayConst la) {
  uint8_t n = 0;
  for (JsonVariantConst v : la) {
    if (n >= FEED_MAX_LINES) break;
    const char* s = v.as<const char*>();
    if (!s) s = "";
    strncpy(lines[n], s, FEED_LINE_CAP - 1);
    lines[n][FEED_LINE_CAP - 1] = 0;
    n++;
  }
  *nLines = n;
  return true;   // TEMPORARY: the old bug's effect -- always "changed"
}
```

- [ ] **Step 4: Run the tests and confirm they FAIL for the right reason**

Run: `pio test -e native`

Expected: **FAIL** — specifically `test_identical_reapply_is_not_a_change`,
`test_long_line_reapply_is_not_a_change`, and `test_empty_entries_after_content_is_a_change`
fail on their `TEST_ASSERT_FALSE`, because the stub always reports a change. The other tests
pass. This is the proof the tests actually detect the bug.

If they all pass, the tests are worthless — stop and fix them.

- [ ] **Step 5: Implement the real change detection**

Replace the body of `feedApplyEntries` in `src/feed.h` (keep the includes and the two
`#define`s):

```cpp
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

- [ ] **Step 6: Run the tests and confirm they all PASS**

Run: `pio test -e native`

Expected: **PASS**, 9/9.

- [ ] **Step 7: Confirm the firmware still builds**

Run: `pio run -e m5stickc-plus`

Expected: SUCCESS. (`feed.h` isn't included by anything yet — this just proves the new
`platformio.ini` didn't break the device build.)

- [ ] **Step 8: Add the CI job**

In `.github/workflows/ci.yml`, add a second job under `jobs:` (keep the existing `test` job
exactly as it is). Reuse the already-pinned actions rather than introducing a new unpinned
one — `uvx` runs PlatformIO without a PATH dance:

```yaml
  firmware:
    name: firmware tests (native)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5  # v4
      - name: Set up uv + Python
        uses: astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86  # v5.4.2
        with:
          python-version: "3.13"
          enable-cache: true
      - name: Run the native unit tests
        run: uvx --from platformio pio test -e native
```

- [ ] **Step 9: Commit**

```bash
git add src/feed.h test/test_feed/test_feed.cpp platformio.ini .github/workflows/ci.yml
git commit -m "test: add a native C++ harness and a pure feed-change unit

The 'did the feed change?' logic was welded to Arduino/M5 headers and so
could never be tested. Extract it into a pure src/feed.h (stdint, string.h,
ArduinoJson only) and add a PlatformIO native env with Unity, plus a CI job.

feedApplyEntries compares the incoming lines against the stored ones. The
regression test locks the actual bug: re-applying an IDENTICAL line longer
than 23 chars must report 'not changed'."
```

---

### Task 2: Wire `data.h` to `feed.h` and relabel the CPU temperature

**Files:**
- Modify: `src/data.h:102-115`
- Modify: `src/main.cpp:645`

**Interfaces:**
- Consumes (from Task 1): `src/feed.h`, providing
  `inline bool feedApplyEntries(char lines[FEED_MAX_LINES][FEED_LINE_CAP], uint8_t* nLines, JsonArrayConst la)`
  — returns `true` when the feed changed. `FEED_MAX_LINES` is 8, `FEED_LINE_CAP` is 92,
  matching `TamaState::lines[8][92]`.
- Produces: nothing consumed by later tasks.

**Background:** this is the actual bugfix. `TamaState::lineGen` drives three things in
`main.cpp` — `wake()` and `msgScroll = 0` (line 932) and `lastFeedChangeMs = now` (line
1027, which gates the 120s `CLOCK_IDLE_GRACE_MS` before clock mode). Because `lineGen`
currently ticks on every 10s heartbeat, the screen is re-woken constantly, the transcript
scroll snaps back, and clock mode is unreachable.

- [ ] **Step 1: Include `feed.h` in `data.h`**

In `src/data.h`, add to the include block at the top (after `#include <ArduinoJson.h>`):

```cpp
#include "feed.h"
```

- [ ] **Step 2: Replace the buggy block**

In `src/data.h`, replace exactly this (currently lines 102-115):

```cpp
  JsonArray la = doc["entries"];
  if (!la.isNull()) {
    uint8_t n = 0;
    for (JsonVariant v : la) {
      if (n >= 8) break;
      const char* s = v.as<const char*>();
      strncpy(out->lines[n], s ? s : "", 91); out->lines[n][91]=0;
      n++;
    }
    if (n != out->nLines || (n > 0 && strcmp(out->lines[n-1], out->msg) != 0)) {
      out->lineGen++;
    }
    out->nLines = n;
  }
```

with:

```cpp
  JsonArrayConst la = doc["entries"];
  if (!la.isNull()) {
    // lineGen must bump ONLY on a real change. It used to be inferred by
    // comparing the newest line against `msg` -- but `msg` is a 23-char status
    // field, so any longer line mismatched every heartbeat and lineGen ticked
    // forever, waking the screen every 10s and blocking clock mode entirely.
    if (feedApplyEntries(out->lines, &out->nLines, la)) out->lineGen++;
  }
```

If `JsonArrayConst la = doc["entries"];` does not compile under ArduinoJson 7.x, use the
explicit cast instead — the semantics are identical:

```cpp
  JsonArrayConst la = doc["entries"].as<JsonArrayConst>();
```

- [ ] **Step 3: Relabel the AXP192 die temperature**

In `src/main.cpp:645`, on the DEVICE info page, change:

```cpp
    ln("  temp     %dC", (int)M5.Axp.GetTempInAXP192());
```

to:

```cpp
    ln("  cpu      %dC", (int)M5.Axp.GetTempInAXP192());
```

This is the power-management chip's die temperature, not the room. It sat one button-press
from the ENV page's ambient `temp NN F`, with no humidity beside it — and it fooled the
project's own author into reporting the ENV sensor as broken.

- [ ] **Step 4: Confirm the native tests still pass**

Run: `pio test -e native`

Expected: PASS, 9/9. (`data.h` isn't compiled natively, but this guards against an
accidental edit to `feed.h`.)

- [ ] **Step 5: Confirm the firmware builds**

Run: `pio run -e m5stickc-plus`

Expected: SUCCESS. A failure here most likely means `feed.h` picked up an Arduino/M5
dependency, or `FEED_LINE_CAP` no longer matches `TamaState::lines`.

- [ ] **Step 6: Commit**

```bash
git add src/data.h src/main.cpp
git commit -m "fix: only bump lineGen when the feed actually changes

lineGen was inferred by comparing the newest feed line against `msg`, but
`msg` is a 23-char status field while lines are 91 chars -- so any longer
line mismatched on EVERY 10s heartbeat. lineGen ticked forever, which
called wake() (screen never slept), reset msgScroll (transcript scroll
snapped back), and reset lastFeedChangeMs (clock mode unreachable). The
buddy stayed awake all night. Haiku mode exposed it: haiku lines persist
at idle and are routinely longer than 23 chars.

Also relabel the DEVICE page's AXP192 die temperature as `cpu`, so it
stops reading like a second, contradictory room temperature."
```

---

### Task 3: Flash and verify on hardware

**Files:** none (deploy + observe). Run by the controller, not a subagent.

The native tests reach none of the rendering, timing, or hardware. This task is where the
fix is actually proven.

- [ ] **Step 1: Flash**

```bash
pio run -e m5stickc-plus -t upload --upload-port /dev/ttyUSB0
```

Expected: SUCCESS. (Needs a real data USB-C cable; the magnetic mount cable is power-only.)

- [ ] **Step 2: Confirm a real change still wakes the screen**

This guards against over-correcting into a feed that never updates. Trigger some Claude Code
activity and confirm the M5 wakes, and the new line appears in the feed.

**If this fails, the fix is wrong** — `lineGen` is now never bumping.

- [ ] **Step 3: Confirm the screen finally sleeps**

Leave the stick idle (a haiku will be loaded from the last turn, so `entries` is non-empty
with a >23-char line — the exact condition that used to keep it awake).

Expected: the screen powers off, and within ~2 minutes (`CLOCK_IDLE_GRACE_MS = 120000`) the
buddy enters clock mode instead of staying on the pet screen. Overnight it should show a
sleeping pet per the calendar moods.

- [ ] **Step 4: Confirm the transcript scroll holds**

Open the transcript and scroll with button B. It must stay where you put it, instead of
snapping back to the top every 10 seconds.

- [ ] **Step 5: Confirm the info pages**

DEVICE page reads `cpu  NNC`. The ENV page (one side-button press further) still reads
`temp NN F` with humidity and pressure below it.

---

## Notes for the implementer

- **Do not make `feed.h` include `<Arduino.h>`** to get `uint8_t` or `strncpy` — use
  `<stdint.h>` and `<string.h>`. Pulling in Arduino breaks the host build and defeats the
  entire harness.
- **Do not "simplify" the comparison to `strcmp`.** The bound (`FEED_LINE_CAP - 1`) must
  match the copy bound, or an unterminated 91-char line reads out of bounds.
- **Do not delete the deliberately-buggy stub step (Task 1, Step 3).** A test that has never
  been seen to fail is not a test.
