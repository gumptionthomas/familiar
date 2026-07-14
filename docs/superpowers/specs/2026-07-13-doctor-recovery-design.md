# `familiar doctor` — Recovery Awareness — Design

**Goal:** Stop `doctor` reporting a fault the daemon has already fixed. Count failures **since
the last successful connect**, not failures in a time window.

## The bug, caught in production within the hour

Minutes after PR #51 merged, `familiar doctor` reported:

```
!!  Phantom link
      BlueZ reports the stick as connected, but the daemon cannot find it...
      bluetoothctl disconnect F0:16:1D:03:4C:FA
exit=1
```

The buddy was **fine**. The daemon's log, in order:

```
18:54:35  disconnected: ... was not found
18:55:06  disconnected: ... was not found
18:55:38  disconnected: ... was not found
18:55:38  clearing a possible stale link to F0:16:1D:03:4C:FA     ← PR #44 self-heal
18:55:46  connected F0:16:1D:03:4C:FA                             ← RECOVERED, and holding
```

A service restart left a phantom BlueZ link; the daemon's own auto-clear (PR #44) fixed it
eight seconds later. But `doctor` counts substrings across a flat 10-minute window
(`doctor.py:543-547`) and has **no notion of ordering**, so the three historical failures still
satisfied `failing`, and the phantom trigger fired.

**This is not an edge case: a service restart reproduces it every time.** `doctor`
systematically miscalls the daemon's own successful recovery as a live fault — and it does so
for the full ten minutes afterwards.

### Why the existing model was wrong

`failing` asked *"have there been failures recently?"* That was always the wrong question. The
right one is **"has anything gone wrong since the last time it worked?"**

`connected_recently` (`"[familiar] connected " in <window>`) was an order-blind boolean — it
could not distinguish "connected, then failed" from "failed, then connected". It is the direct
cause of this bug and is deleted.

**Lineage:** this is the seventh defect in `diagnose()`, and it is the same family as the
previous six — evidence being misread as more certain than it is. The first six were *unknown
read as fine*; this one is *stale read as current*. The fix is the same shape: make the correct
reading structural rather than remembered.

## Design

### 1. Count failures since the last success

`collect()` already reads the journal in order and throws the ordering away. Instead, find the
**last `[familiar] connected` line** and count only what appears **after** it:

```python
"log": {
    "failures_since_connect": int|None,   # 'was not found' + 'failed to discover services'
                                          # occurring AFTER the last successful connect
    "not_found_since_connect": int|None,  # the subset that were 'was not found'
    "flaps": int|None,                    # 'link flapped after' — counted across the window
    "recent_failures": int|None,          # total failures in the window, for the summary line
}
```

If there is **no** `connected` line in the window, every failure in it counts — which is
exactly right: the link has not worked within living memory.

`failing` becomes:

```python
failing = _meets(failures_since_connect, LINK_FAILING_MIN)
```

- **Tonight's log** → the last event is `connected`, so `failures_since_connect == 0` →
  not failing → **no error, exit 0.**
- **The 2026-07-13 failure** → no `connected` line anywhere in the window → all 400 failures
  count → **the bond diagnosis still fires.** Unchanged.

The phantom and unreachable triggers consume `not_found_since_connect` on the same basis.

`connected_recently` is removed entirely.

### 2. Guard the over-correction: flapping

Every fix in this function has created the opposite failure, and here the risk is obvious: a
link that connects and drops repeatedly will often be sampled just after a connect, giving
`failures_since_connect ≈ 0` — so a genuinely unstable link would read as healthy.

The daemon already distinguishes the cases for us (PR #48): `link flapped after N.Ns` is logged
separately from `link dropped after Ns` (a held link ending normally). So:

- **`flaps` is counted across the whole window**, not since the last connect. Flapping is
  inherently about repetition *across* connects, so "since last connect" is the wrong
  denominator for it — it would always be ≈0 by construction.
- **`flaps >= LINK_FAILING_MIN` (3) produces a `warn`**, even while currently connected, with
  `blocks_health=True`. The link is up but not trustworthy, and saying "healthy" would be a lie.

### 3. An honest health summary

A link that recovered from recent trouble is healthy, but saying so *silently* overstates it.
When `recent_failures > 0` and we are not failing, the summary says so:

```
OK  Everything looks healthy
      mode=ble, haiku on, connected (recovered from 3 failures in the last 10 min)
```

One line. It tells the user their link had a wobble and healed — which, on the night this bug
was found, was the exact truth.

## Constraints (unchanged, and binding)

- **`diagnose()` stays PURE.** No I/O. Facts in, findings out.
- **`collect()` must never raise.** Every undeterminable fact degrades to `None`.
- **`diagnose()` reads facts ONLY through `_Facts.need()`.** Reading a fact IS declaring it
  required; an unknown one auto-registers a `blocks_health` warning. The new log facts are read
  the same way — that is what stops this becoming the eighth defect. `.opt()` remains reserved
  for genuinely optional corroboration (`kernel_smp_errors`).
- **`diagnose()` never returns an empty list.**
- Read-only. No `--fix`. Never suggest `pkill -f familiar`.

## Testing

`diagnose()` is pure, so every case is a table test with no Bluetooth and no hardware.

1. **Tonight's incident, encoded:** failures then a connect, `device.connected=True`,
   `failures_since_connect=0`, `recent_failures=3` → **no error**, an `ok` finding, and the
   summary mentions the recovery. *This is the regression test for the false positive.*
2. **The 2026-07-13 failure still fires:** no connect in the window, so
   `failures_since_connect=400`, `kernel_smp_errors=3`, sampled `connected=True` (mid-loop) →
   the bond error, with the `KeyboardOnly` remedy.
3. **A live phantom still fires:** `failures_since_connect >= 3`, `not_found_since_connect > 0`,
   `connected=True` → the phantom error with the `bluetoothctl disconnect` remedy.
4. **Flapping is caught while connected:** `flaps=5`, `failures_since_connect=0`,
   `connected=True` → a `warn` with `blocks_health=True`, and **no** `ok` finding. (Without
   this, the fix over-corrects into calling an unstable link healthy.)
5. **One or two flaps is not flapping:** `flaps=2` → healthy. (Pins `LINK_FAILING_MIN`.)
6. **`collect()` parses ordering correctly:** given a journal with failures *before* a connect,
   `failures_since_connect == 0`; with failures *after* the last connect, it counts only those.
7. **No connect line at all** → every failure in the window counts.
8. **An unknown log** (`None`) still suppresses the health claim and produces no error.
9. `diagnose()` never returns an empty list.

## Out of scope

- Widening or narrowing the 10-minute journal window.
- Any new diagnosis. This corrects the evidence model behind the existing ones.
- `--fix` / auto-repair (still, and permanently, rejected).
