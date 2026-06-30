# Tidbyt Buddy Design

**Goal:** The Tidbyt shows an animated, state-reflective `bufo` buddy by default;
a new haiku interrupts as a ~45s event, then it returns to the buddy. Also put
`bufo` on the M5 so both displays show the same pet.

**Architecture:** The daemon already knows session state and when a haiku
arrives. It derives a "persona" and pushes the matching pre-rendered `bufo`
WebP to the Tidbyt's single installation slot; a new haiku pushes the haiku and
schedules a revert to the current persona. Best-effort/async — never disturbs
the M5 path.

## Persona (mirrors the firmware's `derive`)
From the snapshot's running/waiting/completed:
- `waiting > 0` → **attention**
- `completed` (turn just finished) → **celebrate** (brief, ~5s, then re-derive)
- `running > 0` → **busy**
- else → **idle**

`idle` rotates the 9 idle variants **sequentially** (idle_0…idle_8 → loop), like
the firmware: advance the index on each idle push, plus a periodic refresh
(~every few minutes while idle) for variety.

## Components
1. **Asset conversion (`tools/build_tidbyt_buddy.py`, build-time):** convert each
   `characters/bufo/<state>.gif` → a 64x32 animated WebP (fit-to-height, centered
   on black), committed under `src/claude_buddy/tidbyt_buddy/<state>.webp`
   (idle_0..8, busy, attention, celebrate). 96x100 → ~31x32 centered sprite.
   One-time, Pillow.
2. **`tidbyt.py`:** add `push_image(webp_path, *, device_id, api_token,
   installation_id, pixlet, runner)` — `pixlet push` a pre-made WebP directly
   (no `.star`). Keep `push(lines)` for haikus. Same installation id so buddy and
   haiku share the one slot.
3. **`state.py`:** add `persona()` → one of attention/celebrate/busy/idle from the
   current counts + completed pulse.
4. **`daemon.py`:** orchestrate the Tidbyt slot:
   - On each snapshot/state change, compute persona; if it changed (and no haiku
     event is active), push the persona WebP (idle advances the variant).
   - On new haiku: push the haiku, mark a haiku-event with a ~45s deadline.
   - A timer/loop: when the haiku-event deadline passes, revert to the current
     persona WebP. Celebrate auto-expires (~5s) back to the derived persona.
   - The buddy `tidbyt` config dict carries the asset dir + pixlet path.

## M5 bufo install (Phase 2)
Upload the `bufo` GIFs to the stick's LittleFS so the "ascii pet" setting offers
it: place `characters/bufo/` under the firmware's filesystem image (`data/`) and
`pio run -t uploadfs` (device connected). Verify the LittleFS partition has room
(~600KB of GIFs) first.

## Error handling
Best-effort: missing pixlet/asset/network → no-op; the M5 path is unaffected.
A haiku event always reverts even if a push fails (timer-driven).

## Testing
- `state.persona()` for each state combination (incl. celebrate pulse, idle).
- `tidbyt.push_image` arg construction; no-op without config.
- daemon: persona-change pushes the right asset; haiku event reverts after the
  deadline; idle advances variant. Injected runner + clock, no pixlet/network.
- Asset converter validated by inspecting output dims/animation (not in CI).
