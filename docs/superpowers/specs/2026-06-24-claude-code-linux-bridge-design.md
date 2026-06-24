# Claude Code → Hardware Buddy: Linux BLE Bridge

**Date:** 2026-06-24
**Status:** Approved design, pending implementation plan
**Branch:** `claude-code-linux` (off `main`)

## Problem

The M5StickC Plus runs the buddy firmware (upstream `main`), which displays
live Claude activity received over BLE. The official "bridge" that feeds it
lives inside the Claude **desktop app** (macOS/Windows only). On Linux there
is no desktop app with this feature, so the stick has no data source.

This project builds a **Linux bridge**: a host-side program that connects to
the stick over BLE and pushes the same heartbeat protocol the desktop app
speaks, sourced from **Claude Code** session activity via hooks.

The BLE wire protocol is documented in `REFERENCE.md` (Nordic UART Service +
newline-delimited JSON). Nothing about it is OS-specific.

## Scope (v1)

In scope:
- **Ambient display only** — one-way host → device. The stick reflects what
  Claude Code is doing; it does not send decisions back. (No `PreToolUse`
  approve/deny round-trip.)
- **All Claude Code sessions on the machine**, aggregated. Each session fires
  hooks tagged with its `session_id`; the bridge tracks them collectively.
- States driven by **activity**: idle / busy (running) / attention
  (permission waiting) / celebrate (task completed). Plus recent tool-call
  lines for the transcript screen.

Out of scope (v1), may revisit later:
- **Token tracking** — no transcript parsing; the tokens screen and the
  token-based level-up are not driven. (Celebrate is still driven, via task
  completion — see State Mapping.)
- **Device → Claude approve/deny.**
- **systemd service** — run as a foreground script first; add a `--user`
  unit once proven.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Interaction | Ambient display only | Simplest, robust; no blocking the session on a button |
| Sessions | All, aggregated | Matches desktop behavior; useful when multitasking |
| Tokens | Skipped in v1 | Avoids fragile transcript parsing |
| Lifecycle | Manual foreground script first | Verify before daemonizing |
| Hook → daemon IPC | Unix domain socket | Real-time, no network port, no file rotation |
| `busy` threshold | Firmware patch `running >= 1` | Single-session CLI workflow: `busy` should mean "Claude is working" (see below) |

### `busy` threshold note

Upstream firmware's `derive()` uses `running >= 3` for `busy`, because the
desktop app aggregates many sessions and something is almost always
generating — a low threshold would make `busy` constant and meaningless, so 3
marks genuine high concurrent load. Our data source is one (or a few) Claude
Code sessions, where the interesting signal is "is Claude working right now?"
So v1 patches the threshold to `running >= 1` (a one-line change in
`src/main.cpp`), repurposing `busy` to mean active work. Requires one reflash.

## Architecture

Four components. Host-side tool lives in `linux-bridge/`; the firmware tweak
is in `src/main.cpp`. Both on the `claude-code-linux` branch; `main` stays a
clean mirror of upstream.

### 1. `claude-buddy` daemon (Python, asyncio + bleak)

The only long-lived process. Three concurrent jobs:

- **BLE link** (`ble.py`): connect to the bonded stick (by configured
  address, else scan for `Claude-` name prefix), hold the connection,
  reconnect with backoff on drop. On connect, send time-sync and owner name.
- **State store** (`state.py`): aggregate of all sessions keyed by
  `session_id`; computes `total` / `running` / `waiting` and recent
  `entries`. Pure logic, no I/O — primary unit-test target.
- **IPC server** (`daemon.py`): asyncio Unix socket server; each hook event
  updates state and triggers a (debounced) heartbeat. Also runs a periodic
  stale-session sweep.

### 2. `claude-buddy-hook` thin client (Python, `hook.py`)

Invoked by Claude Code hooks. Reads the hook's stdin JSON, sends one compact
JSON line to the daemon's Unix socket, exits. **Never fails the hook**: short
connect timeout (~200 ms), and on any error exits 0 silently. It cannot slow
down or break a Claude Code session even if the daemon is down.

### 3. Hook configuration

A snippet (`hooks-settings.example.json`) merged into the **user-level**
`~/.claude/settings.json` so every Claude Code session machine-wide feeds the
buddy. Each entry runs `claude-buddy-hook <event>`.

### 4. One-time BLE pairing

The firmware requires LE Secure Connections with MITM + bonding
(`ESP_LE_AUTH_REQ_SC_MITM_BOND`, DisplayOnly); RX/TX characteristics are
encrypted-only. So a cold connection can't read/write them. Pairing is a
documented one-time manual `bluetoothctl` step (below). The daemon assumes
the bond exists and prints a clear "pair first" message if the connection is
rejected for lack of encryption.

## Data flow

```
Claude Code session ──hook──> claude-buddy-hook ──unix socket──> daemon
                                                                    │
                                            update SessionStore ────┤
                                            recompute aggregates    │
                                            build heartbeat ────────┤
                                                                    ▼
                                                  BLE notify (RX char) → stick
```

Heartbeat is pushed on state change (debounced ~200 ms) and as a keepalive
every 10 s (the firmware treats >30 s of silence as a dead link).

### Hooks wired

| Claude Code hook | Effect on session state |
|---|---|
| `SessionStart` | register session → `total++` |
| `UserPromptSubmit` | mark session **running** (turn started) |
| `PostToolUse` | set activity line (e.g. `"Bash: git push"`), keep running, clear waiting |
| `Notification` | permission needed / idle → mark **waiting** |
| `Stop` | turn done → running off; pulse **completed** once |
| `SessionEnd` | remove session → `total--` |

`waiting` is cleared on the next `PostToolUse` / `Stop` / `UserPromptSubmit`
for that session.

### State → animation (firmware `derive()`, unchanged except threshold)

- `waiting > 0` → **attention** (alert + LED blink) — fires on any permission prompt, even single-session
- `completed` pulse on `Stop` → **celebrate** — ambient reward on task completion
- `running >= 1` → **busy** (patched threshold)
- otherwise → **idle** → **sleep** when quiet

### Heartbeat payload (subset of REFERENCE.md the bridge emits)

```json
{
  "total": 2,
  "running": 1,
  "waiting": 0,
  "msg": "Bash: git push",
  "entries": ["10:42 Bash: git push", "10:41 Edit: main.cpp"],
  "completed": false
}
```
On connect, also: `{"time":[epoch, tz_offset_sec]}` and
`{"cmd":"owner","name":"<owner>"}`. `tokens` / `tokens_today` / `prompt` are
omitted in v1.

## Configuration

`~/.config/claude-buddy/config.toml`:
```toml
address = "AA:BB:CC:DD:EE:FF"   # optional; if absent, scan for "Claude-" prefix
owner   = "Thomas"
# socket = "$XDG_RUNTIME_DIR/claude-buddy.sock"  # optional override
```

## Resilience / error handling

- **Daemon BLE**: reconnect loop with backoff; clear log on connect failure
  (pair first / device asleep). The socket server keeps running and
  accumulating state regardless of BLE state; pushes are best-effort.
- **Hook client**: ~200 ms timeout; any error → exit 0 silently.
- **Stale sessions**: periodic sweep prunes sessions not seen in a few
  minutes (covers a crash with no `SessionEnd`) so counts don't get stuck.
- **Debounce**: collapse bursts of events into one heartbeat (~200 ms).

## Repo layout

```
linux-bridge/
  pyproject.toml              # uv; console scripts: claude-buddy, claude-buddy-hook
  README.md                   # pairing + setup
  src/claude_buddy/
    __init__.py
    state.py        # pure SessionStore aggregation  ← main unit-test target
    heartbeat.py    # build heartbeat dict from state
    ble.py          # bleak connection mgmt
    daemon.py       # asyncio: BLE + socket server + stale sweep
    hook.py         # thin client entrypoint
    config.py
  tests/
    test_state.py
    test_heartbeat.py
  hooks-settings.example.json # snippet to merge into ~/.claude/settings.json

src/main.cpp                  # one-line derive() busy threshold change
```

Install: `uv tool install ./linux-bridge` so `claude-buddy` and
`claude-buddy-hook` are on PATH (hooks call the latter by name).

## Testing

- **Unit** (no hardware): `state.py` and `heartbeat.py` are pure logic —
  apply an event sequence, assert the aggregate heartbeat dict. Includes
  waiting set/clear, stale pruning, completed pulse, multi-session totals.
- **Dry run**: daemon `--stdout` mode prints heartbeats instead of sending
  over BLE, with manual event injection — verify output without the stick.
- **Hardware smoke test**: pair once, run the daemon, drive real Claude Code
  activity, watch the pet transition idle → busy → (permission) attention →
  celebrate → idle.

## One-time pairing (documented in linux-bridge/README.md)

```
bluetoothctl
  scan on                 # wait for Claude-XXXX to appear, note its MAC
  pair AA:BB:CC:DD:EE:FF  # enter the 6-digit code shown on the stick
  trust AA:BB:CC:DD:EE:FF
  scan off
  exit
```
Then put the MAC in `config.toml`.

## Future (not v1)

- Token tracking (transcript usage parsing) → tokens screen + token level-up.
- Device → Claude approve/deny via a blocking `PreToolUse` hook.
- systemd `--user` service for autostart + restart.
