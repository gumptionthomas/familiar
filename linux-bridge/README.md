# familiar — Linux bridge for Claude Code

Feeds an M5StickC Plus running the buddy firmware and/or a Tidbyt display with
live Claude Code activity. Ambient display only (one-way).

## Install

```bash
cd linux-bridge
uv tool install .
```

This puts `familiar` on your PATH.

## Setup

```bash
familiar init
```

`familiar init` walks you through everything interactively:

- Prompts for your two Tidbyt keys (`tidbyt_device_id` and `tidbyt_api_key`) and
  the optional extras: M5 device address, Anthropic API key, and owner name.
- Writes `~/.config/familiar/config.toml`.
- Merges the six hooks into `~/.claude/settings.json` (backs it up first;
  idempotent — safe to re-run).
- Optionally installs and enables the `familiar.service` systemd user unit.

### 1. Pair the stick (M5 users only — one-time)

The firmware requires an encrypted, bonded link. Pair via bluetoothctl before
running `familiar init` so you have the MAC address ready:

```bash
bluetoothctl
  scan on                 # wait for "Claude-XXXX", note its MAC
  pair AA:BB:CC:DD:EE:FF  # type the 6-digit code shown on the stick
  trust AA:BB:CC:DD:EE:FF
  scan off
  exit
```

If you only have a Tidbyt (no M5 stick), leave the `address` field blank when
`familiar init` asks — Tidbyt-only mode works with just the two Tidbyt keys.

### 2. Run

After `familiar init`, start the daemon:

```bash
familiar run             # connects over BLE / pushes to Tidbyt
familiar run --stdout    # dry run: prints heartbeats, no BLE or Tidbyt
```

### 3. Run as a service (recommended)

`familiar init` can install the service for you. To install it manually:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/familiar.service <<'EOF'
[Unit]
Description=Claude Code -> M5StickC / Tidbyt familiar bridge
After=bluetooth.target
Wants=bluetooth.target

[Service]
ExecStart=%h/.local/bin/familiar run
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now familiar.service
```

Watch it / manage it:

```bash
journalctl --user -u familiar -f      # live logs (look for "connected <MAC>")
systemctl --user restart familiar     # after editing config
systemctl --user status familiar
```

(Optional: `loginctl enable-linger $USER` keeps the service running even when
you're not logged in.)

> **Don't run a manual `familiar run` and the service at the same time** — BLE
> allows only one connection to the stick.

## Migrating from claude-buddy

If you have an existing `claude-buddy` install, `familiar init` handles the
migration automatically:

- Copies `~/.config/claude-buddy/config.toml` → `~/.config/familiar/config.toml`
  (non-destructive; won't overwrite if the familiar config already exists).
- Rewrites the hook commands in `~/.claude/settings.json` from
  `claude-buddy-hook <event>` to `familiar hook <event>`.
- Swaps the running service from `claude-buddy.service` to `familiar.service`.

After `familiar init` completes and you've verified everything works:

```bash
uv tool uninstall claude-buddy
```

## Tidbyt companion

With `tidbyt_device_id` and `tidbyt_api_key` set, the daemon also drives a
[Tidbyt](https://tidbyt.com) 64×32 display. No extra software needed — Tidbyt
pushes go directly over the Tidbyt cloud API (or a self-hosted
[Tronbyt](https://github.com/tronbyt/tronbyt-server)). It shows a
state-reflective pet by default. If haiku mode is also on (an Anthropic
`api_key` is set), each finished turn scrolls its haiku past for a couple of
passes before returning to the pet — otherwise the Tidbyt just shows the pet
(the two Tidbyt keys alone are enough; the haiku is the only part that needs
the Anthropic key).

**Pick a species** with `tidbyt_pet` — one of eighteen ASCII pets, or `bufo`
(the bundled GIF character):

> `capybara` · `duck` · `goose` · `blob` · `cat` · `dragon` · `octopus` ·
> `owl` · `penguin` · `turtle` · `snail` · `ghost` · `axolotl` · `cactus` ·
> `robot` · `rabbit` · `mushroom` · `chonk` · `bufo`

Restart the service after changing it; an unknown name falls back to `bufo`.

**States**, mapped from your Claude Code activity:

| Pet | When |
|---|---|
| busy | a session is running — a loading pulse ticks down the side |
| needs you | a session is waiting on you — a **pulsing amber border** |
| celebrate | a turn finished — a confetti burst |
| heart | a turn finished **fast** (< ~5s) — rising hearts instead of confetti |
| idle | connected, nothing urgent |
| sleep | ~5 min with no activity — the pet dozes with `Zzz` |

The pet WebPs are pre-rendered and bundled with the package —
`tools/render_ascii_pet.py` builds the ASCII species from their firmware source,
`tools/build_tidbyt_buddy.py` builds bufo from GIFs.

This is the roughest part of the project to hand off: it renders and pushes from
your own machine rather than being a one-click Tidbyt community app.

## Troubleshooting

**`disconnected: Device with address ... was not found`, but `bluetoothctl
info` shows `Connected: yes`.** A previous client (e.g. a manually-run daemon
that was `kill`ed) left a stale connection that BlueZ still holds; a connected
peripheral stops advertising, so the new daemon can't find it. Clear it:

```bash
bluetoothctl disconnect AA:BB:CC:DD:EE:FF
systemctl --user restart familiar
```

**Pairing won't prompt for the passkey.** Set the agent capability before
`pair`: in `bluetoothctl`, run `agent KeyboardDisplay` then `default-agent`.

## How it maps

| Claude Code | Pet |
|---|---|
| actively working (running) | busy |
| permission prompt / notification | attention (LED blinks) + `needs you` |
| turn finished | celebrate; text refreshes (haiku, or reply snippet) |
| quiet | idle / sleep |

In haiku mode the text area shows one aggregate haiku blending all active
sessions, weighted to the turn that just ended; `needs you` alerts still pin
on top. Without an API key it shows the closing reply snippet instead.
