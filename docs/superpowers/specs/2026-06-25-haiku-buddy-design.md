# Haiku Buddy Design

**Goal:** The buddy "speaks" in haiku — an aggregate 5-7-5 haiku composed by Claude
Haiku that blends all active Claude Code sessions into one overall snapshot,
emphasizing the turn that just ended. Replaces the displayed reply snippet.

**Architecture:** The daemon buffers light per-session activity, and on each
turn-end (plus a periodic refresh during long activity) builds an aggregate
"digest" and asks Claude Haiku (Anthropic Messages API) to write one haiku, then
pushes the 3 lines to the buddy over BLE. Haiku generation is an async task off
the hook/critical path, mirroring the existing `_speak` reply flow.

**Tech stack:** Python asyncio daemon; raw HTTPS POST to `/v1/messages` (no SDK
dep); existing BLE/state/hook plumbing.

## Global Constraints
- No new heavy dependency: call the API with the stdlib / a tiny client, not the
  `anthropic` SDK. Network I/O runs in an executor or via an async client so it
  never blocks the event loop.
- Never crash or block a turn: every API path is best-effort and returns `None`
  on any error; the last haiku stays on screen.
- Opt-in: haiku mode is active only when an API key is available. With no key,
  behavior is exactly today's (reply snippet) — existing setups keep working.
- Display stays glanceable: a haiku is exactly 3 short lines; the `[GH] needs
  you` alert + LED blink are unchanged.

## Behavior

### Trigger / cadence (3a)
- **Turn-end (primary):** when a session's turn ends (Stop) and its reply is
  captured, build an aggregate digest with that session as the focus and compose
  a new haiku.
- **Periodic refresh:** while any session has been continuously active (running)
  with no turn-end, recompose at most every ~90s so a long turn isn't stale.
- **Debounce:** coalesce rapid triggers — at most one in-flight compose and no
  more than one compose per ~15s; the newest trigger wins.

### Material sent to Haiku (moderate)
Per **active** session (running or waiting), the digest includes:
- the project code (e.g. `GH`, `CDB`),
- recent tool activity as kinds + file basenames (e.g. `edited auth.py`, `ran a
  command`) — **no command text, no prompts**,
- a short gist of the latest assistant reply (~first 200 chars).

The focus session (the one that just ended) is marked so the model weights it.
Example digest:
```
Focus [GH]: edited auth.py, ran a command, committed; reply: "race fixed, tests green, merged"
Also [CDB]: editing README.md
```

### Output
Claude Haiku returns one 5-7-5 haiku, 3 lines, no preamble. The daemon validates
"3 non-empty lines," trims each to fit the HUD width, and pushes them as 3 feed
entries (fits the 7-line HUD). The haiku is **aggregate**, so its lines are
**untagged** — it already speaks for all sessions. Only `needs you` alerts carry
project codes (unchanged: codes only when 2+ projects are live).

### Errors / fallback
- No key → haiku mode off; current reply-snippet behavior unchanged.
- API error/timeout/malformed → `compose` returns `None`; keep the last haiku
  (do not blank, do not fall back to a raw reply).

## Components
- **`haiku.py` (new):** `async compose(digest: str, *, api_key, model, client=None)
  -> str | None`. Builds the request (system prompt: desk-pet poet, exactly one
  5-7-5 haiku, three lines, no extra text), POSTs to `/v1/messages`, extracts the
  text, validates 3 lines, returns the haiku or `None`. `client` is injectable for
  tests (no network).
- **`state.py`:** per-session ring buffer of `(tool_kind, file_basename)` (cap
  ~8) + latest reply gist; `digest(focus_sid) -> str`; a `set_haiku(lines)` that
  replaces the displayed text with the 3 lines; drop the `thinking...` push.
- **`hook.py`:** `post_tool` again carries `tool` (kind) + file basename (no
  command text); `prompt-submit` no longer needs to push thinking (still marks
  running).
- **`daemon.py`:** on Stop → capture reply (existing `_speak`/transcript), build
  digest, `await haiku.compose(...)`, `set_haiku`. Add the periodic refresh timer
  and debounce. Read key/model from config.
- **`config.py`:** `api_key` (default `$ANTHROPIC_API_KEY`), `model` (default
  `claude-haiku-4-5`).

## Testing
- `haiku.compose` with a fake client: success (parses 3 lines), malformed (→
  None), HTTP error/timeout (→ None), strips preamble/blank lines.
- `state.digest` single vs multi-session, focus emphasis, material redaction (no
  command text/prompts leak in).
- `state.set_haiku` replaces text; tagging only when multi-project.
- daemon wiring with a stub composer (no network): Stop → digest → set_haiku;
  debounce coalesces; no key → no compose.
