import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

from . import heartbeat, transcript
from .config import load
from .state import SessionStore
from .transport import StdoutTransport

_DISPATCH = {
    "session_start": lambda s, p: s.session_start(p["session_id"]),
    "prompt_submit": lambda s, p: s.prompt_submit(
        p["session_id"], p.get("project", "")),
    "post_tool": lambda s, p: s.post_tool(
        p["session_id"], p.get("tool", "tool"), p.get("detail", ""),
        p.get("project", "")),
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
                 debounce=0.2, keepalive=10.0, sweep_interval=60.0):
        self.store = store
        self.transport = transport
        self.socket_path = socket_path
        self.debounce = debounce
        self.keepalive = keepalive
        self.sweep_interval = sweep_interval
        self._dirty = asyncio.Event()

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
                # The final reply may land in the transcript just after Stop;
                # poll for it off the hook's path and push it when it appears.
                asyncio.create_task(self._speak(
                    payload.get("project", ""), payload["transcript_path"]))
        writer.close()

    async def _speak(self, project, path, tries=25, interval=0.1):
        loop = asyncio.get_event_loop()
        for _ in range(tries):
            try:
                text = await loop.run_in_executor(None, transcript.last_reply, path)
            except Exception:
                text = ""
            if text:
                self.store.push_message(project, text)
                self._dirty.set()
                return
            await asyncio.sleep(interval)

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
            try:
                await self.push()
            except Exception:
                pass

    async def _sweep_loop(self):
        while True:
            await asyncio.sleep(self.sweep_interval)
            self.store.sweep()
            self._dirty.set()

    async def run(self):
        server = await self.serve()
        async with server:
            await asyncio.gather(self._push_loop(), self._sweep_loop())


def _tz_offset() -> int:
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds()) if off else 0


async def _on_connect(transport, owner):
    import time
    await transport.send(heartbeat.encode(
        heartbeat.time_sync(int(time.time()), _tz_offset())))
    if owner:
        await transport.send(heartbeat.encode(heartbeat.owner_msg(owner)))


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="claude-buddy")
    ap.add_argument("--stdout", action="store_true",
                    help="print heartbeats instead of sending over BLE")
    args = ap.parse_args(argv)
    cfg = load()
    store = SessionStore()

    if args.stdout:
        transport = StdoutTransport()
        bridge = Bridge(store, transport, cfg.socket_path)
        print(f"[claude-buddy] dry-run; socket={cfg.socket_path}", file=sys.stderr)
        try:
            asyncio.run(bridge.run())
        except KeyboardInterrupt:
            pass
        return 0

    from .ble import run_with_ble
    try:
        asyncio.run(run_with_ble(cfg, store, _on_connect))
    except KeyboardInterrupt:
        pass
    return 0
