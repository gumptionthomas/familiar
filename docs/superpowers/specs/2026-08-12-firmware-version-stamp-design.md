# Firmware Version Stamp — Design

**Goal:** Make "what firmware is on the stick?" a question that can be answered, instead of one that requires archaeology.

## Why

Asked on 2026-08-12 what firmware the M5 was running, the honest answer was: *unknowable*. The firmware carries **no version identifier anywhere** — not in `src/`, not in the `status` reply (`xfer.h:112-139`), not on the DEVICE info page.

What could be established was only circumstantial:

- the newest commit touching `src/` or `platformio.ini` is `b894f94` (#49), 2026-07-13;
- the local build artifact `.pio/build/m5stickc-plus/firmware.bin` is dated 2026-07-13 11:06;
- therefore *if* the stick was flashed on or after that date, it is current.

That "if" is the whole problem. Nothing on the device can confirm it, and there is no OTA channel to interrogate — the partition table is `no_ota.csv`.

This will matter more, not less: the pending `BatteryGuard` work (see
`2026-08-11-repair-and-battery-guard-design.md`) would be the first firmware
change in a month, and knowing whether a given stick has it is exactly the
question this spec makes answerable.

## The version string

`git describe --always --dirty`, yielding `199ee58` or `199ee58-dirty`.

The repository has **no tags**, so `--tags` would add nothing today and would produce long
strings (`v1.2.0-3-g199ee58`) if tagging ever started — worse for a buffer that is already
tight. A bare short hash is unambiguous and stable in length.

The `-dirty` suffix earns its place: builds here are routinely flashed from a working tree
before the commit lands (the 2026-07-13 artifact predates its own merge commit by fifty
minutes). A version that silently claims to be a clean commit when it is not would be worse
than no version at all.

**A missing version must never fail the build.** If `git` is absent, or the source is a
downloaded zip rather than a checkout, the script falls back to `unknown`.

## Build injection

`extra_scripts = pre:tools/git_version.py`, on the `[env:m5stickc-plus]` environment **only**.
The script runs `git describe --always --dirty` and appends `-DFW_VERSION=\"…\"` to that
environment's flags.

`[env:native]` is deliberately untouched, so `pio test -e native` is unaffected — it already
sets its own `build_flags` and excludes `src/` via `build_src_filter`.

### The compile-time fallback

A `-D` flag that fails to arrive would otherwise be a compile error, so the fallback lives in
source. New committed header `src/version.h`:

```c
#pragma once
// FW_VERSION is injected at build time by tools/git_version.py. This fallback
// keeps the firmware compiling when the script did not run — no git, a source
// zip, or a build outside the PlatformIO device environment.
#ifndef FW_VERSION
#define FW_VERSION "unknown"
#endif
```

Included by both consumers, `xfer.h` and `main.cpp`. Note this is a small **committed** header
holding one fallback, not a generated file — the objection below is to generating source, not to
having a header.

Two alternatives were rejected:

- **An inline `!` shell command in `build_flags`.** One line and no new file, but embedding a
  quoted `-D` string through shell substitution inside `platformio.ini` is fragile, and a
  quoting slip surfaces as a confusing compile error rather than a clear failure.
- **A generated `src/version.h`.** Adds a build artifact inside the source tree that is easy to
  commit by accident — PR #58 exists precisely because generated and scratch files kept
  drifting toward the index.

## The status reply, and a latent bug it exposes

The `status` handler builds its whole reply with one `snprintf` into `char b[320]`
(`xfer.h:120`). That buffer is **already at its limit**, before any new field.

It carries two user-controlled strings — `_petName[24]` and `_ownerName[32]`
(`stats.h:213-214`) — so the worst case is roughly:

| Part | Bytes |
|---|---|
| `{"ack":"status",...,"data":{` scaffolding | ~40 |
| `name` + `owner` + `sec` (incl. 23 + 31 chars of user data) | ~87 |
| `bat` group | ~50 |
| `sys` group | ~83 |
| `stats` group | ~74 |
| closing `}}\n` | 3 |
| **total** | **~337** |

Those are estimates, not exact counts, and the realistic case is smaller — `heap` is ~6 digits
rather than 10, `fsTotal` ~7 — landing near 319. Inside 320 by a hair, and only by luck.

`snprintf` truncates rather than overflowing, so this is not memory-unsafe. It is worse in a
subtler way: **a truncated reply is malformed JSON**, and something now parses it —
`tools/measure_discharge.py` reads `status` to log the battery curve. A silently truncated
reply would show up as a harness that mysteriously records nothing.

So:

1. **Buffer `320 → 512`.** Ample headroom over the ~337 worst case plus the new field.
2. **Guard the result**, because a bigger buffer only moves the cliff:

```c
if (len < 0 || len >= (int)sizeof(b)) {
  // Truncated JSON is worse than no JSON: it parses as garbage rather than
  // failing honestly, and the caller cannot tell the difference.
  _xAck("status", false);
  return true;
}
```

3. **Add `fw` inside the `sys` group**, where system facts already live:

```
"sys":{"fw":"199ee58","up":1234,"heap":98765,"fsFree":...,"fsTotal":...}
```

Grouping under `sys` was chosen over a top-level `data.fw` for cohesion; the cost is one extra
level of nesting for a consumer, which is trivial in every language that will read it.

**The buffer fix is folded in deliberately.** It is a pre-existing defect, not one this feature
introduces — but the version field is what pushes the reply over the edge, so shipping the
field without the fix would convert a latent bug into a live one.

## The DEVICE info page

One line under `SYSTEM` in `main.cpp` (the `infoPage == 3` branch, around `:637-645`), beside
uptime and heap:

```c
ln("  fw       %s", FW_VERSION);
```

This is the path that needs no daemon, no script, and no BLE link — pick up the stick, click to
page 3, read it.

**Unverifiable from source:** whether the page has vertical room for a seventh `SYSTEM` line
without running off the 135×240 display. That must be checked on the actual device, and is
listed as a verification step rather than assumed.

## Testing

- `pio test -e native` must stay green. The change touches only the device environment; if
  native breaks, the `extra_scripts` scoping is wrong.
- **The fallback header compiles on its own:** building the device env with the `extra_scripts`
  line removed must still succeed, reporting `unknown` rather than failing to compile.
- **The fallback path is the part most likely to be wrong and least likely to be exercised.**
  Run `tools/git_version.py` outside a git checkout and confirm it yields `unknown` and exits
  zero rather than raising.
- **Truncation guard, by hand:** set a 23-character pet name and a 31-character owner — the
  current worst case — then request `status` and confirm the reply is valid JSON and comfortably
  inside 512. Without deliberately maximal strings this path is never reached.
- **On-device:** confirm the DEVICE page renders the new line within the screen, and that the
  hash shown matches `git describe --always --dirty` at flash time.

## Non-goals

- No tagging scheme, no semantic versioning, no build date. The hash identifies the source
  exactly; a date is redundant once the hash is lookup-able.
- No OTA version negotiation. `board_build.partitions = no_ota.csv` — there is no OTA here.
- No version reporting in the bridge's `familiar doctor`. Doctor diagnoses the *link*; firmware
  identity is a different question, and adding it would widen doctor's remit for no diagnostic
  gain.
