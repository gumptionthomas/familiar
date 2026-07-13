import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime

from . import archive, haiku, heartbeat, tidbyt, transcript
from .calendar_mood import calendar_mood
from .config import load
from .state import SessionStore
from .transport import NullTransport, StdoutTransport

_DISPATCH = {
    "session_start": lambda s, p: s.session_start(p["session_id"]),
    "prompt_submit": lambda s, p: s.prompt_submit(
        p["session_id"], p.get("project", ""), p.get("prompt", "")),
    "post_tool": lambda s, p: s.post_tool(
        p["session_id"], p.get("project", ""), p.get("tool", ""), p.get("file", "")),
    "notification": lambda s, p: s.notification(
        p["session_id"], p.get("project", "")),
    "stop": lambda s, p: s.stop(p["session_id"], p.get("project", "")),
    "session_end": lambda s, p: s.session_end(p["session_id"]),
}


def apply_event(store: SessionStore, payload: dict) -> None:
    fn = _DISPATCH.get(payload.get("event"))
    if fn and payload.get("session_id"):
        fn(store, payload)


class Bridge:
    def __init__(self, store, transport, socket_path,
                 debounce=0.2, keepalive=10.0, sweep_interval=60.0,
                 compose=None, haiku_periodic=90.0, tidbyt=None):
        self.store = store
        self.transport = transport
        self.socket_path = socket_path
        self.debounce = debounce
        self.keepalive = keepalive
        self.sweep_interval = sweep_interval
        self._compose = compose          # async fn(digest)->list[str]|None, or None
        self.haiku_periodic = haiku_periodic
        self._tidbyt = tidbyt            # dict for tidbyt.push(**), or None
        self._composing = False
        self._last_haiku = -1e9
        self._today_date = None
        # Tidbyt buddy orchestration: the slot shows a state-reflective bufo by
        # default; a new haiku takes it over for a few seconds, then reverts.
        self._tb_haiku_until = -1e9
        self._tb_celebrate_until = -1e9
        self._tb_heart_until = -1e9    # fast-completion "aww" pulse
        self._tb_active_at = None      # last time the pet was doing something
        self._tb_prompt_at = {}        # session_id -> prompt-submit time (turn timing)
        self._tb_current = None        # asset/marker currently in the slot
        self._tb_idle_idx = None
        self._tb_idle_at = -1e9
        # A haiku scrolls once in ~8s, so ~18s ≈ two full scroll passes before
        # reverting to the buddy.
        self.tb_haiku_secs = 18.0
        self.tb_celebrate_secs = 5.0
        self.tb_idle_refresh = 180.0
        self.tb_sleep_after = 300.0     # normal idle before the calendar personality engages
        self.tb_flourish_period = 300.0  # a mood flourish comes round every 5 min
        self.tb_flourish_secs = 20.0     # and shows for ~20s of each period
        self._wall_clock = datetime.now  # local wall clock; injectable for tests
        self.tb_heart_threshold = 5.0   # turns faster than this get heart, not confetti
        self._dirty = asyncio.Event()

    def _loop_time(self):
        return asyncio.get_running_loop().time()

    def _maybe_roll_today(self):
        d = date.today()
        if self._today_date is None:
            self._today_date = d
        elif d != self._today_date:
            self._today_date = d
            self.store.reset_today()

    async def handle_conn(self, reader, writer):
        try:
            data = await reader.readuntil(b"\n")
        except asyncio.IncompleteReadError as e:
            data = e.partial
        except Exception:
            writer.close()
            return
        for line in data.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            apply_event(self.store, payload)
            self._dirty.set()
            self._track_turn(payload)
            if payload.get("event") == "stop" and payload.get("transcript_path"):
                asyncio.create_task(self._on_stop(
                    payload.get("session_id", ""), payload.get("project", ""),
                    payload["transcript_path"]))
        writer.close()

    def _track_turn(self, payload):
        # Time each turn (prompt -> stop) so a fast finish gets heart instead of
        # confetti. Open the celebration window here (at the stop event) so it's
        # set before _on_stop's reply-poll/compose delay (see _tidbyt_haiku).
        ev, sid = payload.get("event"), payload.get("session_id", "")
        if ev == "prompt_submit" and sid:
            self._tb_prompt_at[sid] = self._loop_time()
        elif ev == "stop":
            now = self._loop_time()
            started = self._tb_prompt_at.pop(sid, None)
            if started is not None and now - started < self.tb_heart_threshold:
                self._tb_heart_until = now + self.tb_celebrate_secs
            else:
                self._tb_celebrate_until = now + self.tb_celebrate_secs

    async def _await_reply(self, path, tries=30, interval=0.15):
        # Poll for the turn's final reply (the closing assistant text), requiring
        # the same value twice so a mid-flush read can't win. Returns "" if none.
        loop = asyncio.get_running_loop()
        prev = None
        for _ in range(tries):
            try:
                text = await loop.run_in_executor(
                    None, transcript.last_reply, path, 200)
            except Exception:
                text = ""
            if text and text == prev:
                return text
            prev = text
            await asyncio.sleep(interval)
        return ""

    async def _on_stop(self, sid, project, path):
        # The celebrate/heart window was opened synchronously in _track_turn.
        reply = await self._await_reply(path)
        # Credit this turn's output tokens (feeds the pet's level).
        loop = asyncio.get_running_loop()
        try:
            toks = await loop.run_in_executor(
                None, transcript.turn_output_tokens, path)
        except Exception:
            toks = 0
        if toks:
            self._maybe_roll_today()
            self.store.add_tokens(toks)
            self._dirty.set()
        if self._compose is not None:               # haiku mode
            if reply:
                self.store.record_reply(sid, reply)
            await self._haiku_tick(sid, force=True)
        elif reply:                                  # reply-snippet mode (no key)
            self.store.push_message(project, reply[:80])
            self._dirty.set()

    async def _haiku_tick(self, focus_sid, force=False):
        # Compose an aggregate haiku. force=True (turn-end) bypasses the periodic
        # gate; the in-flight guard coalesces bursts either way.
        if self._compose is None or self._composing:
            return
        now = asyncio.get_running_loop().time()
        if not force and now - self._last_haiku < self.haiku_periodic:
            return
        self._composing = True
        try:
            digest = self.store.digest(focus_sid)
            if not digest:
                return
            lines = await self._compose(digest)
            if lines:
                self.store.set_haiku(lines)
                self._last_haiku = asyncio.get_running_loop().time()
                self._dirty.set()
                if self._tidbyt:
                    asyncio.create_task(self._tidbyt_haiku(lines))
        except Exception:
            pass
        finally:
            self._composing = False

    # --- Tidbyt buddy/haiku orchestration --------------------------------
    async def _tidbyt_haiku(self, lines):
        # Let an in-progress celebration/heart pulse finish before the haiku
        # takes the slot.
        wait = max(self._tb_celebrate_until, self._tb_heart_until) - self._loop_time()
        if wait > 0:
            await asyncio.sleep(wait)
        tb = self._tidbyt
        ok = await tidbyt.push(lines, device_id=tb["device_id"],
                               api_token=tb["api_token"])
        if ok:
            self._tb_haiku_until = self._loop_time() + self.tb_haiku_secs
            self._tb_current = "haiku"

    def _persona(self, snap, now):
        # Priority: needs-you > heart (fast finish) > celebrate > busy > idle,
        # and idle dozes into sleep after a long quiet stretch.
        if snap.get("waiting", 0) > 0:
            self._tb_active_at = now
            return "attention"
        # _track_turn opened a heart (fast) or celebrate window at the stop
        # event; fall back to celebrate on the completed pulse if neither is set.
        if snap.get("completed") and now >= self._tb_heart_until \
                and now >= self._tb_celebrate_until:
            self._tb_celebrate_until = now + self.tb_celebrate_secs
        if now < self._tb_heart_until:
            self._tb_active_at = now
            return "heart"
        if now < self._tb_celebrate_until:
            self._tb_active_at = now
            return "celebrate"
        if snap.get("running", 0) > 0:
            self._tb_active_at = now
            return "busy"
        if self._tb_active_at is None:
            self._tb_active_at = now
        if now - self._tb_active_at >= self.tb_sleep_after:
            return self._deep_idle_state(now)
        return "idle"

    def _deep_idle_state(self, now):
        # Past the grace window the idle pet takes on a time-of-day / day-of-week
        # personality (the M5's clock-mode moods). A calm baseline holds, with a
        # brief flourish for the first tb_flourish_secs of each tb_flourish_period.
        baseline, flourish = calendar_mood(self._wall_clock())
        if flourish and (now % self.tb_flourish_period) < self.tb_flourish_secs:
            return flourish
        return baseline

    def _tidbyt_decide(self, snap, now):
        """The buddy asset to show, or None to leave the slot unchanged."""
        if now < self._tb_haiku_until:
            return None                       # haiku event in progress
        persona = self._persona(snap, now)
        if persona != "idle":
            return persona
        # idle: ASCII pets ship a single animated idle; bufo rotates variants.
        idle = (self._tidbyt or {}).get("idle_assets") or ["idle"]
        if len(idle) <= 1:
            return idle[0]
        if self._tb_idle_idx is None or now - self._tb_idle_at >= self.tb_idle_refresh:
            self._tb_idle_idx = 0 if self._tb_idle_idx is None \
                else (self._tb_idle_idx + 1) % len(idle)
            self._tb_idle_at = now
        return idle[self._tb_idle_idx]

    async def _tidbyt_sync(self, snap):
        if not self._tidbyt:
            return
        asset = self._tidbyt_decide(snap, self._loop_time())
        if asset is None or asset == self._tb_current:
            return
        tb = self._tidbyt
        path = os.path.join(tb["asset_dir"], asset + ".webp")
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception:
            return
        # Only latch _tb_current on a confirmed push; a dropped frame (network
        # blip, non-200) must stay un-committed so the next sync retries it
        # rather than leaving the device stuck on a stale frame.
        if await tidbyt.push_image(data, device_id=tb["device_id"],
                                   api_token=tb["api_token"]):
            self._tb_current = asset

    async def serve(self):
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        return await asyncio.start_unix_server(self.handle_conn, self.socket_path)

    async def push(self):
        snap = self.store.snapshot()
        try:
            await self.transport.send(heartbeat.encode(snap))
        except Exception:
            pass
        return snap

    async def _push_loop(self):
        while True:
            try:
                await asyncio.wait_for(self._dirty.wait(), timeout=self.keepalive)
                await asyncio.sleep(self.debounce)  # collapse bursts
            except asyncio.TimeoutError:
                pass  # keepalive tick
            self._dirty.clear()
            self._maybe_roll_today()   # zero tokens_today at local midnight
            snap = await self.push()
            if self._tidbyt:
                try:
                    await self._tidbyt_sync(snap)
                except Exception:
                    pass

    async def _sweep_loop(self):
        while True:
            await asyncio.sleep(self.sweep_interval)
            self.store.sweep()
            self._dirty.set()

    async def _haiku_loop(self):
        # Refresh during sustained activity so a long turn isn't stale.
        while True:
            await asyncio.sleep(30.0)
            focus = self.store.latest_running()
            if focus:
                await self._haiku_tick(focus, force=False)

    async def run(self):
        server = await self.serve()
        loops = [self._push_loop(), self._sweep_loop()]
        if self._compose is not None:
            loops.append(self._haiku_loop())
        async with server:
            await asyncio.gather(*loops)


def _tz_offset() -> int:
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds()) if off else 0


async def _on_connect(transport, owner):
    import time
    await transport.send(heartbeat.encode(
        heartbeat.time_sync(int(time.time()), _tz_offset())))
    if owner:
        await transport.send(heartbeat.encode(heartbeat.owner_msg(owner)))


def _make_compose(cfg, append=None):
    if not cfg.api_key:
        return None
    write = append or archive.append

    async def compose(digest):
        lines = await haiku.compose(digest, api_key=cfg.api_key, model=cfg.model)
        # Archive here, not in Bridge: this is the single point every successful
        # compose passes through, and cfg (the model id, the opt-out) is already
        # in scope. Bridge stays ignorant that an archive exists.
        if lines and cfg.haiku_archive:
            write(lines, model=cfg.model, prompt=archive.prompt_id(haiku.SYSTEM))
        return lines

    return compose


def _tidbyt_assets(here, pet):
    """(asset_dir, idle_assets) for the configured pet, falling back to bufo.

    bufo lives at tidbyt_buddy/ with idle_0..N variants; ASCII species live at
    tidbyt_buddy/<pet>/ with a single idle.webp. An unknown/empty pet -> bufo.
    """
    root = os.path.join(here, "tidbyt_buddy")

    def idles(d):
        if not os.path.isdir(d):
            return []
        if os.path.exists(os.path.join(d, "idle.webp")):
            return ["idle"]
        return sorted((f[:-5] for f in os.listdir(d)
                       if f.startswith("idle_") and f.endswith(".webp")),
                      key=lambda s: int(s.rsplit("_", 1)[1]))

    cand = root if (not pet or pet == "bufo") else os.path.join(root, pet)
    idle = idles(cand)
    if not idle:
        cand, idle = root, idles(root)
    return cand, idle


def _make_tidbyt(cfg):
    if not (cfg.tidbyt_device_id and cfg.tidbyt_api_key):
        return None
    here = os.path.dirname(__file__)
    asset_dir, idle_assets = _tidbyt_assets(here, cfg.tidbyt_pet)
    return {"device_id": cfg.tidbyt_device_id, "api_token": cfg.tidbyt_api_key,
            "asset_dir": asset_dir, "idle_assets": idle_assets}


def _run_mode(cfg) -> str:
    if cfg.address:
        return "ble"
    if cfg.tidbyt_device_id and cfg.tidbyt_api_key:
        return "tidbyt"
    return "none"


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="familiar run")
    ap.add_argument("--stdout", action="store_true",
                    help="dry run: print heartbeats to stdout, drive no device")
    args = ap.parse_args(argv)
    cfg = load()
    compose = _make_compose(cfg)
    tidbyt_cfg = _make_tidbyt(cfg)
    store = SessionStore(haiku_mode=compose is not None)
    mode = _run_mode(cfg)
    if mode == "none":
        print("[familiar] nothing configured — set an M5 `address` and/or "
              "Tidbyt keys (try `familiar init`)", file=sys.stderr)
        return 1
    if compose is not None:
        print("[familiar] haiku mode on", file=sys.stderr)
    if tidbyt_cfg is not None:
        print("[familiar] tidbyt on", file=sys.stderr)

    if mode == "tidbyt" or args.stdout:
        transport = StdoutTransport() if args.stdout else NullTransport()
        bridge = Bridge(store, transport, cfg.socket_path,
                        compose=compose, tidbyt=tidbyt_cfg)
        print(f"[familiar] {'dry-run' if args.stdout else 'tidbyt-only'}; "
              f"socket={cfg.socket_path}", file=sys.stderr)
        try:
            asyncio.run(bridge.run())
        except KeyboardInterrupt:
            pass
        return 0

    from .ble import run_with_ble
    try:
        asyncio.run(run_with_ble(cfg, store, _on_connect,
                                 compose=compose, tidbyt=tidbyt_cfg))
    except KeyboardInterrupt:
        pass
    return 0
