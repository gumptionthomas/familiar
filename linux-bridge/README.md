# claude-buddy — Linux BLE bridge for Claude Code

Feeds an M5StickC Plus running the buddy firmware with live Claude Code
activity over BLE. Ambient display only (one-way).

## Install

```bash
cd linux-bridge
uv tool install .
```
This puts `claude-buddy` and `claude-buddy-hook` on your PATH.

## 1. Pair the stick (one-time)

The firmware requires an encrypted, bonded link. Pair via bluetoothctl:

```bash
bluetoothctl
  scan on                 # wait for "Claude-XXXX", note its MAC
  pair AA:BB:CC:DD:EE:FF  # type the 6-digit code shown on the stick
  trust AA:BB:CC:DD:EE:FF
  scan off
  exit
```

## 2. Configure

`~/.config/claude-buddy/config.toml`:
```toml
address = "AA:BB:CC:DD:EE:FF"
owner   = "YourName"

# Optional — haiku mode: the buddy narrates activity as a haiku written by
# Claude Haiku (one aggregate haiku across active sessions, refreshed each
# turn). Falls back to $ANTHROPIC_API_KEY if api_key is omitted. Without a key,
# the buddy shows the plain reply snippet instead.
# api_key = "sk-ant-..."
# model   = "claude-haiku-4-5-20251001"

# Optional — drive a Tidbyt 64x32 display: a state-reflective pet, plus the
# haiku scrolling past when a turn ends. Needs `pixlet` on PATH
# (https://github.com/tronbyt/pixlet). Get the device id + API key from the
# Tidbyt app ("Get API key"). Both required; api key falls back to $TIDBYT_API_KEY.
# tidbyt_device_id = "your-device-id"
# tidbyt_api_key   = "your-api-key"
# tidbyt_pet       = "capybara"   # any species in the list below, or "bufo"
```

See [Tidbyt companion](#tidbyt-companion) for what it shows and how to pick a
species. It's best-effort — a missing `pixlet` or network blip never disturbs
the M5 stick.

## 3. Install the hooks

Merge `hooks-settings.example.json` into `~/.claude/settings.json` (user
scope, so all Claude Code sessions feed the buddy).

## 4. Run

```bash
claude-buddy            # connects over BLE
claude-buddy --stdout   # dry run: prints heartbeats, no BLE
```

## 5. Run as a service (recommended)

Run the daemon as a systemd **user** service so it autostarts on login and
reconnects automatically:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/claude-buddy.service <<'EOF'
[Unit]
Description=Claude Code -> M5StickC buddy BLE bridge
After=bluetooth.target
Wants=bluetooth.target

[Service]
ExecStart=%h/.local/bin/claude-buddy
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now claude-buddy.service
```

Watch it / manage it:

```bash
journalctl --user -u claude-buddy -f      # live logs (look for "connected <MAC>")
systemctl --user restart claude-buddy     # after editing config
systemctl --user status claude-buddy
```

(Optional: `loginctl enable-linger $USER` keeps the service running even when
you're not logged in.)

> **Don't run a manual `claude-buddy` and the service at the same time** — BLE
> allows only one connection to the stick.

## Tidbyt companion

With `tidbyt_device_id`, `tidbyt_api_key`, and `pixlet` set, the daemon also
drives a [Tidbyt](https://tidbyt.com) 64×32 display. It shows a state-reflective
pet by default. If haiku mode is also on (an Anthropic `api_key` is set), each
finished turn scrolls its haiku past for a couple of passes before returning to
the pet — otherwise the Tidbyt just shows the pet (the two Tidbyt keys alone are
enough; the haiku is the only part that needs the Anthropic key).

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
`tools/build_tidbyt_buddy.py` builds bufo from GIFs. Pushes go over the Tidbyt
cloud API; if that ever sunsets, point `pixlet` at a self-hosted
[Tronbyt](https://github.com/tronbyt/tronbyt-server) server instead.

This is the roughest part of the project to hand off: it renders and pushes from
your own machine rather than being a one-click Tidbyt community app.

## Troubleshooting

**`disconnected: Device with address ... was not found`, but `bluetoothctl
info` shows `Connected: yes`.** A previous client (e.g. a manually-run daemon
that was `kill`ed) left a stale connection that BlueZ still holds; a connected
peripheral stops advertising, so the new daemon can't find it. Clear it:

```bash
bluetoothctl disconnect AA:BB:CC:DD:EE:FF
systemctl --user restart claude-buddy
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
