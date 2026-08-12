# Firmware Version Stamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "what firmware is on the stick?" answerable, by stamping the build with its git version and surfacing it over BLE and on screen.

**Architecture:** A PlatformIO pre-build script injects `git describe --always --dirty` as `-DFW_VERSION`. A committed `src/version.h` supplies an `"unknown"` fallback so a missing flag can never break the build. The version is then surfaced in two places: the `status` reply (for scripts) and the DEVICE info page (for a stick in hand). Adding a field to `status` forces a pre-existing latent bug to be fixed first — its 320-byte buffer is already at the edge.

**Tech Stack:** PlatformIO (SCons `extra_scripts`), Arduino/ESP32 C++, Python 3 for the build script.

## Global Constraints

- **Touch the device environment only.** `[env:native]` must keep working — `pio test -e native` is the existing host test suite and must stay green.
- **A missing version must never fail the build.** No git, a source zip, or a build outside the PlatformIO device env must yield `unknown`, not a compile error.
- **Version string:** `git describe --always --dirty` → e.g. `199ee58` or `199ee58-dirty`. No tags, no build date, no semver.
- **`snprintf` truncation must never produce malformed JSON.** A truncated reply parses as garbage rather than failing honestly; `tools/measure_discharge.py` now parses this reply.
- Status buffer: **320 → 512**.
- Do not add OTA version negotiation — `board_build.partitions = no_ota.csv`, there is no OTA here.

---

## File Structure

| File | Responsibility |
|---|---|
| `tools/git_version.py` (create) | PlatformIO pre-build hook: resolve the git version, inject `-DFW_VERSION`. |
| `src/version.h` (create) | One `#ifndef` fallback so the firmware compiles without the flag. |
| `platformio.ini` (modify) | Wire `extra_scripts` into the device env only. |
| `src/xfer.h` (modify:112-139) | Status reply: bigger buffer, truncation guard, new `fw` field. |
| `src/main.cpp` (modify:4, :636-645) | Include the header; add one line to the DEVICE info page. |

---

### Task 1: Build-time version injection

**Files:**
- Create: `tools/git_version.py`
- Create: `src/version.h`
- Modify: `platformio.ini:9-11`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: the preprocessor macro `FW_VERSION`, a C string literal, available to any translation unit that includes `src/version.h`. Later tasks use it as `FW_VERSION` directly (e.g. `printf("%s", FW_VERSION)`).

- [ ] **Step 1: Write the pre-build script**

Create `tools/git_version.py`:

```python
"""PlatformIO pre-build hook: stamp the firmware with its git version.

Injects -DFW_VERSION="<git describe --always --dirty>" into the device
environment. Wired up from platformio.ini via:

    extra_scripts = pre:tools/git_version.py

Never fails the build: if git is missing, or this is a source zip rather
than a checkout, the version is "unknown" and src/version.h's fallback
would cover it anyway.
"""
import subprocess

Import("env")  # noqa: F821 — injected by PlatformIO's SCons environment


def git_version() -> str:
    try:
        p = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        return "unknown"
    v = p.stdout.strip()
    return v if p.returncode == 0 and v else "unknown"


version = git_version()
print(f"[git_version] FW_VERSION={version}")
env.Append(CPPDEFINES=[("FW_VERSION", env.StringifyMacro(version))])  # noqa: F821
```

`env.StringifyMacro` is PlatformIO's own helper for quoting a macro value as a C string literal — use it rather than hand-rolling quotes, which is where the rejected inline-`!` approach goes wrong.

- [ ] **Step 2: Write the fallback header**

Create `src/version.h`:

```c
#pragma once

// FW_VERSION is injected at build time by tools/git_version.py. This fallback
// keeps the firmware compiling when that script did not run -- no git, a
// source zip, or a build outside the PlatformIO device environment. A missing
// version is an inconvenience; a firmware that will not compile is not.
#ifndef FW_VERSION
#define FW_VERSION "unknown"
#endif
```

- [ ] **Step 3: Wire it into the device environment only**

In `platformio.ini`, in the `[env:m5stickc-plus]` section, add an `extra_scripts` line after `build_flags`:

```ini
build_flags =
    -DCORE_DEBUG_LEVEL=0
extra_scripts = pre:tools/git_version.py
```

Do NOT add it to `[env:native]`. That environment sets its own `build_flags` and excludes `src/` via `build_src_filter`; adding the script there would run a device build hook during host tests.

- [ ] **Step 4: Verify the version resolves in a checkout**

Run: `python3 -c "import subprocess; p=subprocess.run(['git','describe','--always','--dirty'],capture_output=True,text=True); print(repr(p.stdout.strip()), p.returncode)"`
Expected: a short hash such as `'e95d012'` (or `'e95d012-dirty'`) and returncode `0`.

- [ ] **Step 5: Verify the fallback outside a checkout**

This is the path most likely to be wrong and least likely to be exercised, so test it explicitly. Run the same resolution logic from a directory that is not a git repository:

```bash
cd /tmp && python3 -c "
import subprocess
p = subprocess.run(['git','describe','--always','--dirty'],
                   capture_output=True, text=True, check=False)
v = p.stdout.strip()
print('resolved:', v if p.returncode == 0 and v else 'unknown')
"
```

Expected: `resolved: unknown` — and no traceback. If this prints a hash, you are still inside a git repository; move somewhere that is not.

- [ ] **Step 6: Verify the host test environment is unaffected**

Run: `pio test -e native`
Expected: PASS, exactly as before. If this fails, `extra_scripts` was added to the wrong environment.

(The "does it still compile with no version injected?" check belongs at the end of Task 3 — until the consumers exist, that build would succeed trivially and prove nothing.)

- [ ] **Step 7: Commit**

```bash
git add tools/git_version.py src/version.h platformio.ini
git commit -m "build: stamp the firmware with its git version"
```

---

### Task 2: Status reply — buffer, truncation guard, and the fw field

**Files:**
- Modify: `src/xfer.h:112-139`

**Interfaces:**
- Consumes: `FW_VERSION` from Task 1.
- Produces: the `status` reply gains `data.sys.fw`, a string. Full shape:
  `{"ack":"status","ok":true,"n":0,"data":{"name":…,"owner":…,"sec":…,"bat":{…},"sys":{"fw":"199ee58","up":…,"heap":…,"fsFree":…,"fsTotal":…},"stats":{…}}}`
  On a reply that would not fit, `status` instead answers `{"ack":"status","ok":false,"n":0}`.

- [ ] **Step 1: Include the version header**

At the top of `src/xfer.h`, add the include after the existing `"ble_bridge.h"` line:

```c
#include "ble_bridge.h"
#include "version.h"
```

- [ ] **Step 2: Enlarge the buffer, add the field, and guard the result**

In `src/xfer.h`, in the `status` handler, replace:

```c
    char b[320];
    int len = snprintf(b, sizeof(b),
      "{\"ack\":\"status\",\"ok\":true,\"n\":0,\"data\":{"
      "\"name\":\"%s\",\"owner\":\"%s\",\"sec\":%s,"
      "\"bat\":{\"pct\":%d,\"mV\":%d,\"mA\":%d,\"usb\":%s},"
      "\"sys\":{\"up\":%lu,\"heap\":%u,\"fsFree\":%lu,\"fsTotal\":%lu},"
      "\"stats\":{\"appr\":%u,\"deny\":%u,\"vel\":%u,\"nap\":%lu,\"lvl\":%u}"
      "}}\n",
      petName(), ownerName(), bleSecure() ? "true" : "false",
      pct, vBat, iBat, (vBus > 4000) ? "true" : "false",
      millis() / 1000, ESP.getFreeHeap(),
      (unsigned long)(LittleFS.totalBytes() - LittleFS.usedBytes()),
      (unsigned long)LittleFS.totalBytes(),
      stats().approvals, stats().denials, statsMedianVelocity(),
      (unsigned long)stats().napSeconds, stats().level
    );
    Serial.write(b, len);
    bleWrite((const uint8_t*)b, len);
    return true;
```

with:

```c
    // 512, not 320: this reply carries a 23-char pet name and a 31-char owner
    // (stats.h), so the worst case was already ~337 bytes and only fit by luck.
    char b[512];
    int len = snprintf(b, sizeof(b),
      "{\"ack\":\"status\",\"ok\":true,\"n\":0,\"data\":{"
      "\"name\":\"%s\",\"owner\":\"%s\",\"sec\":%s,"
      "\"bat\":{\"pct\":%d,\"mV\":%d,\"mA\":%d,\"usb\":%s},"
      "\"sys\":{\"fw\":\"%s\",\"up\":%lu,\"heap\":%u,\"fsFree\":%lu,\"fsTotal\":%lu},"
      "\"stats\":{\"appr\":%u,\"deny\":%u,\"vel\":%u,\"nap\":%lu,\"lvl\":%u}"
      "}}\n",
      petName(), ownerName(), bleSecure() ? "true" : "false",
      pct, vBat, iBat, (vBus > 4000) ? "true" : "false",
      FW_VERSION, millis() / 1000, ESP.getFreeHeap(),
      (unsigned long)(LittleFS.totalBytes() - LittleFS.usedBytes()),
      (unsigned long)LittleFS.totalBytes(),
      stats().approvals, stats().denials, statsMedianVelocity(),
      (unsigned long)stats().napSeconds, stats().level
    );
    // snprintf truncates rather than overflowing, so this is memory-safe -- but
    // a truncated reply is MALFORMED JSON, which parses as garbage instead of
    // failing honestly. tools/measure_discharge.py reads this reply; a silent
    // truncation there looks like a harness that mysteriously records nothing.
    if (len < 0 || len >= (int)sizeof(b)) {
      _xAck("status", false);
      return true;
    }
    Serial.write(b, len);
    bleWrite((const uint8_t*)b, len);
    return true;
```

Note the argument order: `FW_VERSION` goes immediately before `millis() / 1000`, matching the new `"fw"` placeholder's position at the head of the `sys` group. Getting this wrong compiles cleanly and produces nonsense at runtime.

- [ ] **Step 3: Build for the device**

Run: `pio run -e m5stickc-plus`
Expected: SUCCESS, and the build log contains a line like `[git_version] FW_VERSION=e95d012`.

- [ ] **Step 4: Prove the truncation guard actually fires**

The guard cannot be reached with a 512-byte buffer, so force it. Temporarily change `char b[512]` to `char b[64]`, then:

Run: `pio run -e m5stickc-plus`
Expected: SUCCESS (this is a runtime path, not a compile-time one).

Flash and request `status`. Expected: `{"ack":"status","ok":false,"n":0}` — a valid, honest failure — rather than a truncated fragment.

**Then change it back to `char b[512]` and rebuild.** Do not commit the 64-byte version.

- [ ] **Step 5: Verify the real reply with maximal strings**

Set the longest values the fields allow — a 23-character pet name and a 31-character owner (`_petName[24]` / `_ownerName[32]`, `stats.h:213-214`) — then request `status`.

Expected: one complete line of valid JSON, containing `"fw":"<hash>"` inside `sys`, well inside 512 bytes. This is the case that used to sit at ~337 of 320.

- [ ] **Step 6: Commit**

```bash
git add src/xfer.h
git commit -m "fix: the status reply could truncate into malformed JSON"
```

---

### Task 3: DEVICE info page line

**Files:**
- Modify: `src/main.cpp:4` (include), `src/main.cpp:636-645` (SYSTEM block)

**Interfaces:**
- Consumes: `FW_VERSION` from Task 1.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Include the version header**

In `src/main.cpp`, add the include after the existing `"ble_bridge.h"` line (line 4):

```c
#include "ble_bridge.h"
#include "version.h"
```

- [ ] **Step 2: Add the line to the DEVICE page**

In the `infoPage == 3` branch, in the `SYSTEM` block, add the `fw` line as the first entry — it is static identity, so it belongs above the live values. Change:

```c
    spr.setTextColor(p.text, p.bg);
    ln("SYSTEM");
    spr.setTextColor(p.textDim, p.bg);
    if (ownerName()[0]) ln("  owner    %s", ownerName());
    uint32_t up = millis() / 1000;
```

to:

```c
    spr.setTextColor(p.text, p.bg);
    ln("SYSTEM");
    spr.setTextColor(p.textDim, p.bg);
    ln("  fw       %s", FW_VERSION);
    if (ownerName()[0]) ln("  owner    %s", ownerName());
    uint32_t up = millis() / 1000;
```

`ln` is the varargs lambda defined at `main.cpp:552` and is in scope here.

- [ ] **Step 3: Build**

Run: `pio run -e m5stickc-plus`
Expected: SUCCESS.

- [ ] **Step 4: Verify on the device**

Flash, then navigate to the DEVICE info page (page 3) on the stick.

Expected: a `fw` line showing the same hash as `git describe --always --dirty` produces at flash time.

**Check it fits.** The `SYSTEM` block now has up to seven lines (fw, owner, uptime, heap, bright, bt, cpu) on a 135×240 display. This cannot be verified from source. If the last line runs off the screen, move `fw` to the end of the block instead of the start, so that the value pushed off is `cpu` — a live reading you can get elsewhere — rather than the identity this feature exists to show.

- [ ] **Step 5: Verify the firmware still compiles with no version injected**

Only now is this meaningful: both consumers (`xfer.h`, `main.cpp`) exist, so this actually
exercises whether `src/version.h`'s fallback reaches all of them.

Temporarily comment out the `extra_scripts` line in `platformio.ini`, then:

Run: `pio run -e m5stickc-plus`
Expected: SUCCESS, with no `[git_version]` line in the build log. `FW_VERSION` resolves to
`"unknown"` through `src/version.h`. A compile error here means some consumer is missing the
include — which is exactly the failure a downloaded source zip would hit.

**Then uncomment the line and rebuild** before committing.

- [ ] **Step 6: Commit**

```bash
git add src/main.cpp
git commit -m "feat: show the firmware version on the DEVICE page"
```

---

## Notes on testing

There are no automated tests in this plan, and that is a deliberate consequence of what the code is, not an oversight:

- `tools/git_version.py` is a build hook whose only real behaviour is shelling out to git. Its two paths — a checkout and not-a-checkout — are verified by the runnable commands in Task 1 Steps 4 and 5.
- The status handler lives in `xfer.h`, which pulls in `Arduino.h`, `LittleFS.h`, and `M5StickCPlus.h`. It cannot compile under `[env:native]`, so the existing Unity host suite cannot reach it. The truncation guard is instead proven by deliberately shrinking the buffer (Task 2 Step 4) — a stronger check than a mock, because it exercises the real `snprintf`.
- The DEVICE page is pixels on a physical screen.

`pio test -e native` must still pass throughout; it is the regression net for everything this plan does not touch.
