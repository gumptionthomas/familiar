import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime

from . import haiku, heartbeat, transcript
from .config import load
from .state import SessionStore
from .transport import StdoutTransport

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
                 compose=None, haiku_periodic=90.0):
        self.store = store
        self.transport = transport
        self.socket_path = socket_path
        self.debounce = debounce
        self.keepalive = keepalive
        self.sweep_interval = sweep_interval
        self._compose = compose          # async fn(digest)->list[str]|None, or None
        self.haiku_periodic = haiku_periodic
        self._composing = False
        self._last_haiku = -1e9
        self._today_date = None
        self._dirty = asyncio.Event()

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
            if payload.get("event") == "stop" and payload.get("transcript_path"):
                asyncio.create_task(self._on_stop(
                    payload.get("session_id", ""), payload.get("project", ""),
                    payload["transcript_path"]))
        writer.close()

    async def _await_reply(self, path, tries=30, interval=0.15):
        # Poll for the turn's final reply (the closing assistant text), requiring
        # the same value twice so a mid-flush read can't win. Returns "" if none.
        loop = asyncio.get_event_loop()
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
        reply = await self._await_reply(path)
        # Credit this turn's output tokens (feeds the pet's level).
        loop = asyncio.get_event_loop()
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
        now = asyncio.get_event_loop().time()
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
                self._last_haiku = asyncio.get_event_loop().time()
                self._dirty.set()
        except Exception:
            pass
        finally:
            self._composing = False

    async def serve(self):
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        return await asyncio.start_unix_server(self.handle_conn, self.socket_path)

    async def push(self):
        await self.transport.send(heartbeat.encode(self.store.snapshot()))

    async def _push_loop(self):
        while True:
            try:
                await asyncio.wait_for(self._dirty.wait(), timeout=self.keepalive)
                await asyncio.sleep(self.debounce)  # collapse bursts
            except asyncio.TimeoutError:
                pass  # keepalive tick
            self._dirty.clear()
            self._maybe_roll_today()   # zero tokens_today at local midnight
            try:
                await self.push()
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


def _make_compose(cfg):
    if not cfg.api_key:
        return None

    async def compose(digest):
        return await haiku.compose(digest, api_key=cfg.api_key, model=cfg.model)

    return compose


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="claude-buddy")
    ap.add_argument("--stdout", action="store_true",
                    help="print heartbeats instead of sending over BLE")
    args = ap.parse_args(argv)
    cfg = load()
    compose = _make_compose(cfg)
    store = SessionStore(haiku_mode=compose is not None)
    if compose is not None:
        print("[claude-buddy] haiku mode on", file=sys.stderr)

    if args.stdout:
        transport = StdoutTransport()
        bridge = Bridge(store, transport, cfg.socket_path, compose=compose)
        print(f"[claude-buddy] dry-run; socket={cfg.socket_path}", file=sys.stderr)
        try:
            asyncio.run(bridge.run())
        except KeyboardInterrupt:
            pass
        return 0

    from .ble import run_with_ble
    try:
        asyncio.run(run_with_ble(cfg, store, _on_connect, compose=compose))
    except KeyboardInterrupt:
        pass
    return 0
