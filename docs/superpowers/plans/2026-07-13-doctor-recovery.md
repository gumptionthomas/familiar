# `familiar doctor` Recovery Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `familiar doctor` reporting a fault the daemon has already fixed. Count failures **since the last successful connect**, not failures in a flat time window.

**Architecture:** `collect()` already reads the daemon's journal in order and throws the ordering away. It will instead find the last `[familiar] connected` line and count only what happened *after* it. `diagnose()`'s `failing` is then computed from that, so recovery falls out of the data instead of being a special case. A separate window-wide flap count guards the over-correction (an unstable link sampled just after a connect).

**Tech Stack:** Python 3.11+, stdlib only. pytest.

**Spec:** `docs/superpowers/specs/2026-07-13-doctor-recovery-design.md`

## The bug this fixes (caught in production, minutes after PR #51 merged)

`familiar doctor` reported `!! Phantom link`, exit 1. The buddy was **fine**. The daemon's log, in order:

```
18:54:35  disconnected: ... was not found
18:55:06  disconnected: ... was not found
18:55:38  disconnected: ... was not found
18:55:38  clearing a possible stale link to F0:16:1D:03:4C:FA   <- the daemon's own auto-clear (PR #44)
18:55:46  connected F0:16:1D:03:4C:FA                           <- RECOVERED, and holding
```

A service restart left a phantom BlueZ link; the daemon self-healed it eight seconds later. But `doctor` counts substrings across a flat 10-minute window with **no notion of ordering**, so the three historical failures still satisfied `failing` and the phantom trigger fired.

**A service restart reproduces this every time.** `doctor` systematically miscalls the daemon's own successful recovery as a live fault, for ten minutes afterwards.

`failing` asked *"have there been failures recently?"* — always the wrong question. The right one is **"has anything gone wrong since the last time it worked?"**

## Global Constraints

- **`diagnose()` must stay PURE.** No subprocess, no filesystem, no network. Facts in, findings out.
- **`collect()` must NEVER raise.** Every undeterminable fact degrades to `None`.
- **`diagnose()` reads decision-relevant facts ONLY through `F.need(section, key, label)`.** Reading a fact IS declaring it required; an unknown one auto-registers a `blocks_health` warning. **This is why there have been seven defects in this function and this must be the last** — every one was evidence read as more certain than it was. `F.opt()` is reserved for genuinely optional facts that gate no decision (currently `kernel_smp_errors`, and the new `recent_failures`, which is used only in a summary string).
- **`diagnose()` never returns an empty list.**
- **`None` must never be coerced to a number.** No `x or 0`. `_meets(None, n)` is `False` — an unknown count can never fabricate a trigger, and `F.need()` has already registered the same `None` as required-but-unknown.
- Read-only. No `--fix`. Never suggest `pkill -f familiar`.
- No new dependencies. Stdlib only.
- Run tests from `linux-bridge/`: `uv run pytest -q`. The suite is currently **242 passing**.

---

### Task 1: Count failures since the last success

**Files:**
- Modify: `linux-bridge/src/familiar/doctor.py` (the `collect()` log block at ~539-554, and the `diagnose()` trigger block at ~255-310, and the health summary)
- Test: `linux-bridge/tests/test_doctor.py`

**Interfaces:**
- The `log` sub-dict of the facts contract changes. **Old keys are removed** (`discover_failures`, `not_found`, `phantom_clears`, `connected_recently`). New shape:
  ```python
  "log": {
      "failures_since_connect":  int|None,   # both kinds, AFTER the last successful connect
      "discover_since_connect":  int|None,   # 'failed to discover services', after it
      "not_found_since_connect": int|None,   # 'was not found', after it
      "flaps":                   int|None,   # 'link flapped after' — across the WHOLE window
      "recent_failures":         int|None,   # total failures in the window; summary text only
  }
  ```
- Everything else in the facts contract is unchanged.

**Note on the test file:** the existing tests use the old log keys (in `_facts()`'s baseline and in `log={...}` overrides). You MUST update those keys so the tests exercise the new contract. **Do not change any assertion** — only the fact keys feeding them. If an assertion has to change to keep a test passing, stop: that means the behaviour regressed.

- [ ] **Step 1: Write the failing tests**

Add to `linux-bridge/tests/test_doctor.py`:

```python
def test_a_fault_the_daemon_already_fixed_is_not_reported(capsys):
    # THE REGRESSION (caught in production minutes after PR #51 merged). A
    # service restart leaves a phantom BlueZ link; the daemon's own auto-clear
    # (PR #44) fixes it seconds later. doctor counted the historical failures
    # and reported "!! Phantom link" on a perfectly healthy, connected buddy --
    # every time anyone restarted the service.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"failures_since_connect": 0,      # it RECOVERED after them
             "discover_since_connect": 0,
             "not_found_since_connect": 0,
             "flaps": 0,
             "recent_failures": 3},            # ...but they did happen
    ))
    assert [f for f in findings if f.level == "error"] == []
    assert any(f.level == "ok" for f in findings)
    # ...and say so, rather than silently claiming perfection.
    summary = next(f for f in findings if f.level == "ok")
    assert "recovered" in summary.why


def test_the_2026_07_13_failure_still_fires():
    # No successful connect in the window at all -> every failure counts, and
    # the bond diagnosis must still fire even though bluetoothctl sampled the
    # device as Connected: yes mid-failure-loop.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        kernel_smp_errors=3,
        log={"failures_since_connect": 400,
             "discover_since_connect": 400,
             "not_found_since_connect": 0,
             "flaps": 0,
             "recent_failures": 400},
    ))
    errs = [f for f in findings if f.level == "error"]
    assert errs and "KeyboardOnly" in "\n".join(errs[0].remedy)
    assert not any(f.level == "ok" for f in findings)


def test_a_live_phantom_link_still_fires():
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"failures_since_connect": 5,
             "discover_since_connect": 0,
             "not_found_since_connect": 5,     # still failing NOW
             "flaps": 0,
             "recent_failures": 5},
    ))
    errs = [f for f in findings if f.level == "error"]
    assert errs and "phantom" in errs[0].title.lower()
    assert any("disconnect" in line for line in errs[0].remedy)


def test_an_unstable_link_is_never_called_healthy():
    # THE OVER-CORRECTION GUARD. A link that connects and drops repeatedly will
    # often be sampled just after a connect -- failures_since_connect ~ 0 -- so
    # counting only "since the last success" would call it healthy. The daemon
    # logs "link flapped after N.Ns" distinctly; that count is what catches it.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"failures_since_connect": 0,
             "discover_since_connect": 0,
             "not_found_since_connect": 0,
             "flaps": 5,                       # up right now, but not trustworthy
             "recent_failures": 0},
    ))
    assert any(f.level == "warn" and f.blocks_health for f in findings)
    assert not any(f.level == "ok" for f in findings)


def test_one_or_two_flaps_is_not_instability():
    # Pins LINK_FAILING_MIN: dropping it to 1 resurrects the cry-wolf class.
    findings = doctor.diagnose(_facts(
        device={"paired": True, "connected": True},
        log={"failures_since_connect": 0, "discover_since_connect": 0,
             "not_found_since_connect": 0, "flaps": 2, "recent_failures": 0},
    ))
    assert [f for f in findings if f.level == "error"] == []
    assert any(f.level == "ok" for f in findings)


def test_collect_counts_only_failures_after_the_last_connect(monkeypatch):
    # The whole fix, at the collect() layer: ordering, not counting.
    journal = "\n".join([
        "[familiar] disconnected: Device with address AA:BB was not found.",
        "[familiar] disconnected: Device with address AA:BB was not found.",
        "[familiar] disconnected: Device with address AA:BB was not found.",
        "[familiar] clearing a possible stale link to AA:BB",
        "[familiar] connected AA:BB",              # <- RECOVERED here
    ])

    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "journalctl" and "-k" in cmd:
            return ""
        if cmd[0] == "journalctl":
            return journal
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
    log = doctor.collect(cfg)["log"]

    assert log["failures_since_connect"] == 0     # all three preceded the connect
    assert log["not_found_since_connect"] == 0
    assert log["recent_failures"] == 3            # ...but they are still visible


def test_collect_counts_failures_that_followed_the_last_connect(monkeypatch):
    journal = "\n".join([
        "[familiar] connected AA:BB",
        "[familiar] disconnected: Device with address AA:BB was not found.",
        "[familiar] disconnected: failed to discover services, device disconnected",
    ])

    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "journalctl" and "-k" in cmd:
            return ""
        if cmd[0] == "journalctl":
            return journal
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
    log = doctor.collect(cfg)["log"]

    assert log["failures_since_connect"] == 2
    assert log["not_found_since_connect"] == 1
    assert log["discover_since_connect"] == 1


def test_collect_counts_everything_when_there_was_never_a_connect(monkeypatch):
    # No successful connect in the window: the link has not worked within living
    # memory, so every failure counts. This is the 2026-07-13 shape.
    journal = "\n".join([
        "[familiar] disconnected: failed to discover services, device disconnected",
    ] * 7)

    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "journalctl" and "-k" in cmd:
            return ""
        if cmd[0] == "journalctl":
            return journal
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
    log = doctor.collect(cfg)["log"]

    assert log["failures_since_connect"] == 7
    assert log["discover_since_connect"] == 7


def test_collect_counts_flaps_across_the_whole_window(monkeypatch):
    # Flaps are repetition ACROSS connects, so "since the last connect" is the
    # wrong denominator -- it would be ~0 by construction.
    journal = "\n".join([
        "[familiar] connected AA:BB",
        "[familiar] link flapped after 2.1s; backing off AA:BB",
        "[familiar] connected AA:BB",
        "[familiar] link flapped after 1.8s; backing off AA:BB",
        "[familiar] connected AA:BB",
        "[familiar] link flapped after 3.0s; backing off AA:BB",
        "[familiar] connected AA:BB",
    ])

    def fake_run(cmd, **kw):
        if cmd[0] == "bluetoothctl" and cmd[1] == "--version":
            return "bluetoothctl: 5.66\n"
        if cmd[0] == "journalctl" and "-k" in cmd:
            return ""
        if cmd[0] == "journalctl":
            return journal
        return ""

    monkeypatch.setattr(doctor, "_run", fake_run)
    cfg = Config(address="AA:BB", owner="", socket_path="/tmp/x.sock")
    log = doctor.collect(cfg)["log"]

    assert log["failures_since_connect"] == 0     # sampled right after a connect
    assert log["flaps"] == 3                      # ...but it is plainly unstable
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_doctor.py -q`

Expected: FAIL. The `diagnose()` tests fail because the new log keys are unknown to `_facts()`; the `collect()` tests fail because the new keys don't exist.

**Confirm `test_a_fault_the_daemon_already_fixed_is_not_reported` fails specifically on the phantom error being present** — that is the production bug, and a test that has never been seen to catch it is not a regression test.

- [ ] **Step 3: Rewrite `collect()`'s journal parsing**

In `linux-bridge/src/familiar/doctor.py`, replace the `log = {...}` block in `collect()` (currently ~lines 546-554):

```python
    log = {"failures_since_connect": None, "discover_since_connect": None,
           "not_found_since_connect": None, "flaps": None,
           "recent_failures": None}
    if jlog:
        lines = jlog.splitlines()

        # ORDERING, not counting. "Have there been failures recently?" was the
        # wrong question -- a service restart leaves a phantom link that the
        # daemon's own auto-clear fixes seconds later, and counting the window
        # flat made doctor report that healed fault for ten minutes afterwards.
        # The right question is "has anything gone wrong SINCE the last time it
        # worked?" If there is no successful connect in the window at all, the
        # link has not worked within living memory and every failure counts.
        last_ok = -1
        for i, line in enumerate(lines):
            if "[familiar] connected " in line:
                last_ok = i
        after = lines[last_ok + 1:]

        def _count(where, needle):
            return sum(1 for line in where if needle in line)

        nf = _count(after, "was not found")
        df = _count(after, "failed to discover services")
        log = {
            "failures_since_connect": nf + df,
            "discover_since_connect": df,
            "not_found_since_connect": nf,
            # Flaps are counted across the WHOLE window on purpose: flapping is
            # repetition ACROSS connects, so "since the last connect" would be
            # ~0 by construction and would miss it entirely.
            "flaps": _count(lines, "link flapped after"),
            # Summary text only -- never gates a decision.
            "recent_failures": (_count(lines, "was not found")
                                + _count(lines, "failed to discover services")),
        }
```

- [ ] **Step 4: Rewrite `diagnose()`'s trigger block**

Replace the fact reads and the trigger derivations (currently ~lines 261-310) with:

```python
            # Counted SINCE THE LAST SUCCESSFUL CONNECT, not across a flat
            # window. A fault the daemon already healed is history, not a live
            # problem -- reporting it for ten minutes afterwards is how doctor
            # cried wolf on a healthy buddy after every service restart.
            failures = F.need(
                "log", "failures_since_connect", "the daemon's recent log")
            discover_fails = F.need(
                "log", "discover_since_connect", "the daemon's recent log")
            not_found = F.need(
                "log", "not_found_since_connect", "the daemon's recent log")
            flaps = F.need("log", "flaps", "the daemon's recent log")
            # The ONE genuinely optional fact: corroboration only. An unreadable
            # kernel log is already covered by the log-volume trigger
            # (BOND_FAILURES_ALONE), so its absence needs no warning of its own.
            smp = F.top("kernel_smp_errors")

            failing = _meets(failures, LINK_FAILING_MIN)

            # Grade the one-sided-bond evidence by confidence. NONE of these
            # branches consult `connected`: during the real failure the daemon
            # loops connect -> discovery-fails -> disconnect, so bluetoothctl
            # legitimately says Connected: yes for the seconds the link is up.
            bond_fires = (
                paired is False                                    # definitive
                or (failing and _meets(discover_fails, BOND_MIN_FAILURES)
                    and _meets(smp, 1))                            # fingerprint
                or (failing and _meets(discover_fails, BOND_FAILURES_ALONE)))

            # Only HERE does the instant sample matter, and only to distinguish
            # the failure type: a connected peripheral stops advertising, so
            # "connected" plus the daemon STILL reporting "was not found" means
            # BlueZ is holding a link the daemon cannot use.
            phantom_fires = (failing and connected is True
                             and _meets(not_found, 1))

            # A dead / flat / out-of-range stick: BlueZ keeps the paired record
            # forever, so this shows up as "was not found" climbing with no
            # confirmed connect.
            unreachable_fires = (
                failing and _meets(not_found, LINK_FAILING_MIN)
                and connected is not True and not bond_fires)

            # Failing, but none of the above -- the daemon's normal
            # backoff-and-retry.
            flapping_fires = failing and not (
                bond_fires or phantom_fires or unreachable_fires)

            # THE OVER-CORRECTION GUARD. Counting only "since the last success"
            # means a link that connects and drops repeatedly reads as healthy
            # whenever we happen to sample just after a connect. The daemon logs
            # "link flapped after N.Ns" separately from a held link ending, so
            # the flap count catches it even while we are currently connected.
            unstable_fires = _meets(flaps, LINK_FAILING_MIN)
```

- [ ] **Step 5: Emit the `unstable` finding**

Add this finding where the other link findings are emitted (alongside `flapping_fires`), keeping the existing ordering conventions:

```python
            if unstable_fires:
                out.append(Finding(
                    "warn", "The link keeps flapping",
                    f"The daemon has logged {flaps} short-lived connections in "
                    f"the last 10 minutes. The link is up right now, but it is "
                    f"not holding — the stick may be at the edge of range, or "
                    f"low on battery.",
                    ["# move the stick closer, or check its charge",
                     "journalctl --user -u familiar -f   # watch it live"],
                    blocks_health=True))
```

- [ ] **Step 6: Make the health summary honest**

The summary block currently reads (`doctor.py:417-427`):

```python
    if mode == "ble" and connected is False:
        return out

    bits = [f"mode={mode}"]
    if cfg.get("haiku"):
        bits.append("haiku on")
    if cfg.get("tidbyt"):
        bits.append("tidbyt on")
    if mode == "ble":
        bits.append("connected" if connected else "not connected")
    out.append(Finding("ok", "Everything looks healthy", ", ".join(bits), []))
```

Replace **only** the `if mode == "ble":` branch with:

```python
    if mode == "ble":
        # Reaching here in `ble` mode means `connected` is True: an unknown one
        # already returned via blocks_health, and a False one returned above.
        recent = F.opt("log", "recent_failures")   # gates nothing; text only
        if recent:
            bits.append(f"connected (recovered from {recent} failures "
                        f"in the last 10 min)")
        else:
            bits.append("connected")
```

A link that recovered from recent trouble IS healthy — but saying so silently overstates it. One line tells the user their link had a wobble and healed, which on the night this bug was found was the exact truth.

- [ ] **Step 7: Update the existing tests' fact keys**

The `_facts()` helper's `log` baseline and every `log={...}` override in `linux-bridge/tests/test_doctor.py` still use the removed keys. Update them to the new contract.

The baseline becomes:

```python
        "log": {"failures_since_connect": 0, "discover_since_connect": 0,
                "not_found_since_connect": 0, "flaps": 0,
                "recent_failures": 0},
```

Then fix each override. Mapping:
- a test that meant "N discovery failures, still failing" → `failures_since_connect: N, discover_since_connect: N, recent_failures: N`
- a test that meant "N 'was not found', still failing" → `failures_since_connect: N, not_found_since_connect: N, recent_failures: N`
- `connected_recently: False` → drop it (the new counts carry that meaning)
- an all-`None` log (the "couldn't read the journal" tests) → set all five new keys to `None`

**Change NO assertion.** If a test only passes after you weaken an assertion, the behaviour regressed — stop and report it.

- [ ] **Step 8: Run the full suite**

Run: `cd linux-bridge && uv run pytest -q`

Expected: PASS — 251 (242 existing + 9 new). Every pre-existing assertion must still hold.

- [ ] **Step 9: Mutation-check the new invariants**

Confirm each of these BREAKS the suite (if one doesn't, add a test until it does, and report which):

1. `unstable_fires = _meets(flaps, LINK_FAILING_MIN)` → `False` (disables the over-correction guard)
2. In `collect()`, `after = lines[last_ok + 1:]` → `after = lines` (reverts to the flat window — the production bug)
3. The `unstable` finding's `blocks_health=True` → `False`
4. `F.need("log", "flaps", ...)` → `F.opt("log", "flaps")`

- [ ] **Step 10: Commit**

```bash
git add linux-bridge/src/familiar/doctor.py linux-bridge/tests/test_doctor.py
git commit -m "fix: doctor reported faults the daemon had already healed

Caught in production minutes after #51 merged: a service restart leaves a
phantom BlueZ link, the daemon's own auto-clear (#44) fixes it seconds
later -- and doctor reported '!! Phantom link', exit 1, on a healthy
connected buddy. It counted failures across a flat 10-minute window with
no notion of ordering, so a healed fault kept firing for ten minutes.

'Have there been failures recently?' was the wrong question. Count
failures SINCE THE LAST SUCCESSFUL CONNECT, and recovery falls out of the
data instead of being a special case someone has to remember.

Guard the over-correction: a link that connects and drops repeatedly would
sample as ~0 failures-since-connect. The daemon logs 'link flapped after
N.Ns' distinctly, so count flaps across the window -- three or more warns,
even while currently connected."
```

---

### Task 2: Verify on the live machine

**Files:** none. Run by the controller.

- [ ] **Step 1: Redeploy**

```bash
uv tool install --force --reinstall ./linux-bridge
```

- [ ] **Step 2: Reproduce the exact production scenario**

Restarting the service is what triggered the bug — it leaves a phantom link that the daemon then self-heals.

```bash
systemctl --user restart familiar.service
# wait for the daemon to reconnect (watch for "[familiar] connected")
journalctl --user -u familiar.service -n 5 --no-pager
familiar doctor; echo "exit=$?"
```

Expected: once the daemon has reconnected, **exit 0**, a health summary, and **no phantom error** — with the summary noting the recovery, e.g. `connected (recovered from 3 failures in the last 10 min)`.

Before this fix, this sequence reliably produced `!! Phantom link` and exit 1.

- [ ] **Step 3: Confirm a real fault is still caught**

```bash
systemctl --user stop familiar
familiar doctor > /tmp/doc.txt 2>&1; echo "exit=$?"
head -4 /tmp/doc.txt
systemctl --user start familiar
```

Expected: the service finding, its `systemctl --user start familiar` remedy, and **exit 1**.

**Note:** capture the exit code from `familiar doctor` directly — `$?` after a pipe reports the *last* command in the pipeline, not `doctor`.

---

## Notes for the implementer

- **Do not add a "was it recently connected?" boolean back.** `connected_recently` was order-blind — it could not distinguish "connected, then failed" from "failed, then connected" — and it is the direct cause of this bug.
- **Do not coerce `None` to `0`.** No `x or 0` anywhere. `_meets(None, n)` is `False`, and `F.need()` has already registered the `None` as required-but-unknown. Six earlier Criticals in this function were all an unknown silently read as "fine".
- **Do not read a decision-relevant fact with `F.opt()`.** Only `kernel_smp_errors` and `recent_failures` qualify, and `recent_failures` only because it gates nothing — it enriches a summary string.
- **Do not weaken an existing assertion to make it pass.** Step 7 changes fact *keys*, never assertions.
