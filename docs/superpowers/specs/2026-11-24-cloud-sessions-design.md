# Design: feeding the buddy from cloud sessions and claude.ai

**Date:** 2026-11-24
**Status:** Parked 2026-11-24. Thomas leans toward the state-only relay (B,
no content opt-in); relay host not yet chosen. No code or plan yet.
**Size:** medium (one new bridge module, one stdlib-only hook script, an
environment setup step)

## Problem

The buddy only knows what happens on `calvin`. Its one signal path is:

```
Claude Code on this machine ──~/.claude/settings.json hooks──> familiar hook
  ──Unix socket──> familiar daemon ──BLE──> stick
```

(`linux-bridge/src/familiar/hook.py`, `daemon.py:handle_conn`). Everything
about that path is local: the hooks live in the *user-level* settings file, the
IPC is a Unix socket, and the `stop` event hands the daemon a
`transcript_path` that it reads off local disk for the reply snippet and the
turn's output tokens (`transcript.last_reply`, `transcript.turn_output_tokens`).

Work has moved. Familiar itself is now developed from a claude.ai Project whose
threads mostly run in Anthropic's cloud containers (see
`2026-11-23-claude-project-setup-design.md`), and claude.ai chats never touched
the buddy at all. While a cloud thread works for an hour, the stick sits idle
and then sleeps.

## Goals

- The buddy goes **busy** while a cloud Claude Code session (a Project thread,
  or a plain claude.ai/code session) is working, **attention** when one needs
  Thomas, and **celebrate** when a turn finishes — the same states, from the
  same `SessionStore`, as local sessions.
- Local and cloud sessions aggregate together, exactly as several local
  sessions do today.
- Nothing about the local path changes. The BLE protocol (`REFERENCE.md`) does
  not change. The firmware does not change.

## Non-goals

- Approve/deny from the stick for cloud sessions. Local sessions don't have it
  either.
- Haiku material from cloud sessions in v1 (see *Privacy*).
- Anything that needs Anthropic to ship a feature first.

## Anti-goals

- **A cloud hook that can slow down or break a cloud session.** Same contract
  as `hook.py`: short timeout, every error swallowed, exit 0.
- **Prompts or replies leaving the container by default.** The digest is never
  stored locally (CLAUDE.md, *Lines not to cross*); shipping it through a
  third-party relay would be a bigger version of the same line. That is
  Thomas's call, not a default.
- **An inbound port on `calvin` opened to the internet** as the only way in.
- **Reusing Thomas's claude.ai login token** to scrape an undocumented API.

## What the cloud can actually expose

Checked from inside this thread's container on 2026-11-24:

| Fact | Evidence |
|---|---|
| Hooks run in cloud sessions. | This container runs `SessionStart`, `UserPromptSubmit`, `PostToolUse` and `Stop` hooks from `~/.claude/launcher-settings.json`. |
| Repo `.claude/settings.json` hooks reach **plain** cloud sessions (they're part of the clone). | Claude Code on the web docs. |
| Whether repo hooks reach **Project threads** is unconfirmed. The 2026-11-23 setup design says threads don't load them; nothing here confirms or refutes it. | Needs one test thread (Phase 1, step 1). |
| User-level `~/.claude/settings.json` from `calvin` does **not** reach cloud sessions. | Docs. |
| Each environment has a **setup script** that runs when the container is built, for every session and every repo in that environment. | The `session-start-setup-script-failure.sh` hook here reports on it. |
| Outbound network is policy-limited. A test `GET https://ntfy.sh/` from this container was refused by the proxy (403). | Any relay host must be added under the environment's *Allowed domains*. |
| The session knows who it is: `CLAUDE_CODE_REMOTE=true`, `CLAUDE_CODE_REMOTE_SESSION_ID`. | Container env. |
| `transcript_path` in the `Stop` payload points into the **container's** disk. | The daemon on `calvin` can't read it. |

And claude.ai chats: no hooks, no transcript files, no API that lists a user's
conversations or their streaming state.

## Approaches

### A — Run Project threads locally. *Zero code.*

A Project thread can run on `calvin` instead of in the cloud (a local thread /
Remote Control). It's then a Claude Code process on this machine, and the
user-level hooks should fire as for any terminal session.

- **For:** works today, no relay, full fidelity (tokens, replies, haiku).
- **Against:** only covers threads Thomas chooses to run locally; `calvin` has
  to be on.
- **Verified 2026-11-24:** Thomas ran a Project thread on `calvin` through
  Remote Control and the stick reacted. Local Project threads already work.

### B — Cloud hooks → relay → bridge. *Recommended for cloud threads.*

```
cloud container                              calvin
───────────────                              ──────
Claude Code hook ─HTTPS POST─> relay <─HTTPS stream─ familiar daemon
 (familiar-cloud-hook,           (topic)        (remote.py, outbound only)
  stdlib only, 1 s timeout)                          │
                                         apply_event → SessionStore → BLE
```

1. **Getting the hook into the container.** The environment's setup script
   fetches one stdlib-only script from this repo and writes a
   `~/.claude/settings.json` that wires it to the same six events `hook.py`
   uses. Doing it in the setup script, not the repo, means every session in
   the environment reports — whichever repo it's on — and it doesn't depend on
   whether Project threads read repo hooks. (A repo `.claude/settings.json` is
   the fallback if the user-level file turns out to be ignored there.)
2. **Payload.** Same JSON shapes `hook.py:map_event` already produces, so the
   daemon's `apply_event` takes them unchanged, plus:
   - `session_id` → `"cloud:" + CLAUDE_CODE_REMOTE_SESSION_ID` (no collision
     with local ids);
   - `project` → the same `_project()` code, from the repo dir name;
   - `stop` carries `output_tokens` read from the transcript *inside the
     container* (the token count is not content), and no `transcript_path`;
   - `prompt-submit` sends `prompt: ""` and `post-tool` drops `file` unless
     the opt-in below is on;
   - `ts` and an HMAC-SHA256 over the body with a shared secret, so a stranger
     who learns the topic can't drive the buddy. Secret and topic come from
     environment variables set in the cloud environment, never from the repo.
3. **Relay.** Anything with "POST a message" in and "stream messages" out.
   [ntfy](https://ntfy.sh) is the reference: `POST /<topic>` from the hook,
   `GET /<topic>/json` (a long-lived line stream) from the daemon. Its public
   server works with nothing to host; it's also a single binary that could be
   self-hosted. The bridge only needs a URL.
4. **Bridge side.** New `remote.py`: an asyncio task that holds the stream,
   reconnects with backoff, verifies HMAC and a freshness window, drops
   replays, and hands each event to the existing `apply_event` + `_dirty.set()`
   path — the same thing `handle_conn` does for a socket line. `stop` with
   `output_tokens` calls `store.add_tokens` directly instead of reading a
   transcript. Off unless `remote_url` is set in `config.toml`.
5. **Stale sessions.** Containers get reclaimed without a `SessionEnd`. The
   existing stale sweep already prunes sessions not seen for five minutes;
   that covers it.
6. **`attention`.** Cloud sessions in auto mode rarely raise permission
   prompts, so `Notification` will fire less than locally. A Project thread
   waiting on Thomas (a question or a decision card) is the more useful
   "needs you" and isn't a Claude Code hook event. v1 takes `Notification` as
   is; mapping "blocked on the user" is an open question.

- **For:** covers every cloud session, from any repo, with `calvin` asleep or
  awake (events just don't land while it's off). No inbound port. Reuses
  `SessionStore`, the heartbeat and the stale sweep untouched.
- **Against:** a relay host to allowlist and a secret to set in the
  environment; a third party sees event metadata (event kind, a session id,
  a 4-letter project code, timing). Latency is one relay hop, which an
  ambient display won't notice.

### B′ — The project chat (the coordinator). *A gap B doesn't close.*

Quick questions answered in the project chat itself never start a thread, so
neither A nor B sees them. The coordinator is a different kind of session:

| | Threads | Coordinator |
|---|---|---|
| Session kind (from the session record) | `anthropic_cloud`, with the project's cloud `environment_id` and the repo cloned | `ccs`, **no** `environment_id`, no repo |
| Environment | The project's cloud environment | `cloud_default` (confirmed from its env) |
| Runs the environment's setup script | Yes | No: it isn't in that environment |
| Network policy | The environment's allowlist | `cloud_default`'s; `ntfy.sh` refused (403), and the project's allowlist doesn't apply |
| Hooks | Run (B's mechanism) | None: no `launcher-settings.json`, no `settings.json`, `remote-settings.json` is `{}` |

So the setup-script hook in B **will not fire for the coordinator**, and
nothing in the repo or environment settings reaches it either. (Confirmed by
the coordinator from its own shell on 2026-11-24.)

What's left, in order of preference:

1. **Accept the gap for v1. Chosen (Thomas, 2026-11-24).** Coordinator turns are short (seconds), so the
   buddy missing them costs the least. Threads are where the hour-long work is.
2. **The session list, revisited.** The coordinator does show in the
   claude.ai/code session list with a `working` status while it answers —
   that's the only signal it emits that anything off-box can see. This is
   Approach C, rejected below for reasons that still hold; it becomes worth
   reopening only if Anthropic documents that endpoint or ships a supported
   way to read it (e.g. a CLI command on `calvin` that lists remote sessions
   with their status). Then a 10 s poll from the bridge would cover the
   coordinator *and* every cloud thread, and B could be retired.
3. **Not:** asking the coordinator to curl the relay itself on every reply.
   That's F again — it depends on the model remembering, and it costs a tool
   call on every quick answer.

### C — Poll Anthropic's session list. *Rejected.*

The claude.ai/code session list does carry exactly the right field (a
`working` / `blocked` / `completed` status bucket per session). But the only
way to it from the bridge is the claude.ai login token against an endpoint
that isn't a public API (the public `/v1/sessions` is the managed-agents
product, a different thing). It would break silently, and borrowing the login
token is the wrong kind of clever.

### D — The desktop app's own Hardware Buddy. *Ruled out.*

**Checked 2026-11-24:** the Linux desktop app on `calvin` has no Developer
menu or Developer Mode option at all, so this route doesn't exist on Linux.


Upstream's BLE bridge lives in the Claude desktop app's Developer menu and
feeds Cowork and Code activity. It's documented for macOS and Windows only.
`calvin` runs a desktop app build on Linux; whether that build has
**Developer → Open Hardware Buddy…** is a thirty-second look. Even if it does,
it would hold the stick's one BLE connection and fight `familiar.service`, so
it's an alternative to the bridge, not an addition.

### E — claude.ai chats via a browser extension. *Possible, deferred.*

The only observable signal for a chat is the page itself: a response is
streaming, or it has finished. A small extension (or userscript) on
`claude.ai` could watch for that and POST `prompt_submit` / `stop` to a
loopback HTTP listener the bridge would add (127.0.0.1 only, token-checked).

- **For:** the only route to chats at all.
- **Against:** it sees only chats in that browser — not chats in the desktop
  app, which is where Thomas mostly is. It's DOM-scraping, so any claude.ai
  redesign breaks it. And a chat gives only busy → celebrate: no tools, no
  permission prompts. Not worth building until A/B are in and Thomas still
  wants chats.

### F — A remote MCP connector the model calls. *Rejected.*

The model would have to decide to call "I'm busy" each turn. That's not
ambient, it costs tokens, and it fails exactly when the model forgets.

## Privacy

Default payloads are **state only**: event kind, session id, project code,
token count, timestamp. A config flag in the cloud hook
(`FAMILIAR_CLOUD_CONTENT=1`) would add the prompt gist, tool-and-file labels
and reply gist, which is what haiku mode needs. That flag sends prompt and
reply text through the relay, so it stays off unless Thomas turns it on, and
the plan pins the default key set with a test (as the archive's is).

## Verification — what a cloud thread can and can't prove

- **Cloud-verifiable:** the hook script's payload mapping and HMAC (host
  tests), `remote.py`'s parsing, verification, replay rejection and
  reconnect logic (pytest with a fake stream), and — once the relay host is
  allowlisted — that a cloud thread's hooks really do POST to the relay.
- **Needs `calvin`:** the daemon holding the stream through
  `familiar.service`, and the stick actually going busy while a cloud thread
  works. That's a local-thread check plus Thomas's eyes on the screen.

## Phasing

0. **Two observations, no code.** ~~A local Project thread moves the stick~~
   (done 2026-11-24: it does), and ~~the desktop app has Hardware Buddy~~
   (done 2026-11-24: no Developer menu on Linux).
1. **Cloud ingest (B).**
   1. Settle whether Project threads pick up a user-level
      `~/.claude/settings.json` written by the setup script (one test thread
      with a hook that writes a marker file).
   2. `linux-bridge/cloud_hook.py` (stdlib only, mirrors `hook.py:map_event`)
      and its tests.
   3. `remote.py` + config keys (`remote_url`, `remote_secret`) + tests.
   4. Setup-script snippet and README section; allowlist the relay host.
   5. Redeploy the bridge on `calvin` and watch it end to end (local thread).
2. **Optional:** content opt-in for haiku material; mapping "thread blocked on
   you" to attention.
3. **Optional:** chats (E), only if still wanted.

## Open questions for Thomas

1. **Relay:** public ntfy.sh (nothing to run; a third party sees event
   metadata), self-hosted ntfy (e.g. on Fly), or a tunnel straight into
   `calvin` (Tailscale Funnel / Cloudflare Tunnel; no third party, but an
   inbound path). Recommendation: public ntfy.sh with HMAC, state-only, to
   start; the bridge takes any URL, so moving later is a config change.
2. **Content:** state-only forever, or build the haiku opt-in in Phase 2?
3. ~~**Project chat:**~~ Settled 2026-11-24: the v1 gap is accepted.
4. **Chats:** do they matter enough to build E, given it would only see the
   browser and not the desktop app?
