# ENV Readout on the Clock Face — Design

**Goal:** Add a glanceable ambient **temp + humidity** line (e.g. `72F  41%`) to the
**portrait** clock face, so the room's conditions show alongside the time when the
stick is idle on its mount.

## Scope

- **Portrait clock only.** Landscape is deferred: it has pre-existing rendering
  issues (a direct-to-LCD per-tick wipe → CRT-style flicker; pet drawn small at
  1× in the upper-left) that deserve their own fix pass, not to be entangled with
  this.
- Temp + humidity only (pressure stays on the ENV info page — it's the least
  glanceable value).
- Display-only. **No change to the ENV sensor read** (still the core-0 task); the
  clock just reads the already-exposed cached accessors.

## Design

`drawClock()` portrait branch (`src/main.cpp:423-435`) currently paints the bottom
strip (y198–240) with `HH:MM` (size 2) and `Mon DD` (size 1). Rebalance it to fit a
third line when the HAT is present:

```
   (pet fills the top, unchanged)
   ─────────── bottom strip (y198–240) ───────────
      12:34        HH:MM   size 2   (p.text)
      Jul 10       date    size 1   (p.textDim)
      72F  41%     ENV     size 1   (p.textDim)   [only if envPresent()]
```

- When `envPresent()`: draw all three lines at rebalanced y-positions so they fit
  the 42px strip (e.g. time y≈208, date y≈224, env y≈236 — final values eyeballed
  on hardware).
- When `!envPresent()`: keep the current two-line layout exactly as it is today
  (no empty gap), so a stick without the HAT looks unchanged.
- The ENV string is built with the existing `envTempF()` / `envHumidityPct()`
  accessors (`env.h`), e.g. `snprintf(buf, "%dF  %d%%", envTempF(), envHumidityPct())`.

## Data flow / refresh

The portrait clock's bottom strip is redrawn into the sprite each frame while
clocking, so the ENV line reads the latest cached values every frame and tracks
the ~5s sensor updates automatically. No new timers or state.

## Error handling

- No HAT → `envPresent()` false → line omitted, two-line layout retained.
- Values are cached ints from the core-0 task; a stale/absent read just shows the
  last good value (or 0 before the first read, which resolves within ~5s of boot).

## Verification (hardware — no automated tests)

1. `pio run` builds clean; flash over `/dev/ttyUSB0`.
2. Let the stick settle into the portrait clock (idle, on USB); confirm the
   `72F  41%` line appears under the date, aligned and legible, and that humidity
   ticks up on a breath (tracks the sensor).
3. Confirm no layout regression to the time/date, and the pet above is unaffected.

## Out of scope

- Landscape clock (its ENV line **and** its flicker/pet-size fixes) — separate.
- Pressure on the clock; configurable units (hardcoded °F, matching the ENV page).
- Any change to the sensor read path.
