# M5 ENV-III HAT → Info Page — Design

**Goal:** Read the mounted M5StickC ENV-III HAT (SHT30 temp/humidity + QMP6988 pressure) and show ambient temperature (°F), humidity, and pressure on a new INFO sub-page — a first, minimal step to confirm the sensor works at all.

## Scope

Just the sensor read + an info-page readout. **No changes to the avatar rendering** (the M5's hand-drawn per-species art stays as-is; environment-reactive moods are explicitly out of scope for now). No Tidbyt/bridge involvement (that would need the M5→bridge reverse channel — a separate, larger project).

## Hardware facts (confirmed)

- ENV-III HAT: **SHT30 @ 0x44**, **QMP6988 @ 0x56** (per M5 docs), on the HAT header pins **SDA = GPIO0, SCL = GPIO26**.
- This is a **separate I²C bus** from the M5's internal one (G21/G22), which carries the AXP192, IMU (MPU6886), and RTC. The internal bus must not be disturbed.
- GPIO0 is the boot-strapping pin — the HAT's pull-up keeps boot happy, but the "won't scan on 0/26" community reports make an explicit found/not-found check worthwhile.
- Device flashes over **/dev/ttyUSB0** (FTDI FT232; world-writable via a udev rule).

## Architecture

### New module `src/env.h` (header-only inline, matching `data.h` / `stats.h`)

- Depends on the **`m5stack/M5Unit-ENV`** library (added to `platformio.ini` `lib_deps`), which provides the `SHT3X` and `QMP6988` classes and the QMP6988 pressure-compensation math.
- Uses a **second I²C instance `Wire1`** on pins (SDA=0, SCL=26). Never calls `Wire.begin()` / never touches the default internal bus.
- API:
  - `void envInit()` — `Wire1.begin(0, 26)`; a brief **I²C scan on `Wire1` printed to Serial** (bring-up diagnostic — shows what actually answers on 0/26); then `sht3x.begin(&Wire1, 0x44, 0, 26)` and `qmp.begin(&Wire1, <addr>, 0, 26)`. Sets `_envOk` = both began.
  - `void envPoll()` — throttled to ~2s (`millis()` gate). On each poll: `sht3x.update()` (caches `cTemp`, `humidity`) and `qmp.update()` (caches `pressure`). Keeps a `_envReadOk` flag from the latest read. Also prints readings to Serial during bring-up.
  - Accessors: `bool envPresent()` (init + last read ok), `float envTempF()` (SHT30 cTemp → °F), `int envHumidityPct()`, `int envPressureHpa()` (Pa → hPa).
- The exact `M5Unit-ENV` begin()/read API and the QMP6988 address constant are confirmed against the installed library header during implementation (the library has had two API generations).

### `src/main.cpp` wiring

- `#include "env.h"`.
- Call `envInit()` in `setup()` after `M5.begin()` (so the internal bus is already up).
- Call `envPoll()` once per main-loop iteration (self-throttled).
- Add a new **ENV** page to `drawInfo()`, inserted between the DEVICE and BLUETOOTH pages; bump the info-page count so the header reads e.g. `ENV  4/5` and the cycle reaches it. Layout:
  ```
  ENV  4/5
    temp      72.3 F
    humidity  41 %
    pressure  1013 hPa
    sensor    ok            (or: not found)
  ```
  When `!envPresent()`, temp/humidity/pressure show `--` and the sensor line reads `not found`.

## Data flow

```
setup(): M5.begin() -> envInit() (Wire1 up, I2C scan to Serial, sensors begin)
loop():  envPoll() (every ~2s: read SHT30 + QMP6988 into cache; Serial debug)
drawInfo() ENV page: reads cached envTempF()/envHumidityPct()/envPressureHpa()/envPresent()
```

## Error handling

- Missing/unresponsive HAT → `envInit`/`envPoll` set the flags false; the page shows `not found` / `--`. Never blocks the loop.
- Wire1 is a distinct bus on distinct pins, so a sensor failure or a floating GPIO0 cannot disturb the IMU/RTC/AXP on the internal bus.
- All sensor calls are best-effort; no exceptions in Arduino C++, but failed `begin()`/`update()` just leave the cache stale and the flag false.

## Verification (hardware, not unit tests)

Firmware has no automated test harness; verify on the device:
1. `pio run` builds clean with the new lib.
2. `pio run -t upload` over `/dev/ttyUSB0`.
3. `pio device monitor` — confirm the boot I²C scan lists **0x44 and 0x56** on Wire1, and the periodic env readings look sane (room temp in the 60s–70s °F, humidity a plausible %, pressure ~1000–1020 hPa).
4. On-device: cycle to the **ENV** info page; **breathe on the HAT** — temp and humidity should visibly rise, confirming a live sensor, not a stuck value.

If the I²C scan finds nothing on 0/26: the HAT/pins/bus is the problem (not the display code) — that's the diagnostic this design is built to surface.

## Out of scope (YAGNI)

- Environment-reactive avatar moods (deferred; and no avatar-render changes at all).
- Tidbyt display of ENV data (needs the M5→bridge reverse channel).
- Trends / history / comfort index / alerts.
- Configurable units (hardcoded °F per the owner; pressure hPa).
