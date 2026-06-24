# Claude Code Linux BLE Bridge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Linux host-side tool that feeds an M5StickC Plus buddy live Claude Code activity over BLE, so the pet reacts to your sessions (idle / busy / attention / celebrate).

**Architecture:** A long-lived asyncio daemon holds the BLE link and an aggregate of all Claude Code sessions. Thin hook clients — invoked by user-level Claude Code hooks — push events to the daemon over a Unix domain socket. The daemon recomputes aggregates and pushes the desktop-compatible heartbeat JSON to the stick. A one-line firmware change makes a single running session register as "busy".

**Tech Stack:** Python 3.11+, asyncio, `bleak` (BLE), `uv` (packaging/run), pytest. Firmware: PlatformIO/Arduino (existing).

## Global Constraints

- Python **3.11+** (uses `tomllib` from stdlib).
- Package + run via **uv** only (no pip/pipx). Run tests with `uv run pytest`.
- The hook client MUST **never fail a hook**: connect timeout ~200 ms, and on ANY error exit 0 silently.
- Heartbeat keepalive interval **10 s**; state-change pushes debounced **~200 ms**.
- Stale-session prune threshold **300 s**.
- BLE: Nordic UART Service. Service `6e400001-b5a3-f393-e0a9-e50e24dcca9e`, RX (write to device) `6e400002-...`, TX (notify from device) `6e400003-...`.
- RX writes chunked to **180 bytes** max per write (firmware reassembles on `\n`).
- Host tool lives under `linux-bridge/`. `main` stays a clean upstream mirror; all work on branch `claude-code-linux`.
- Heartbeat fields emitted in v1: `total`, `running`, `waiting`, `msg`, `entries`, `completed`. (No `tokens`/`prompt`.)
- Recent `entries` capped at **6**, newest first.

---

## File Structure

```
linux-bridge/
  pyproject.toml              # uv project; console scripts claude-buddy, claude-buddy-hook
  README.md                   # pairing + setup
  hooks-settings.example.json # snippet merged into ~/.claude/settings.json
  src/claude_buddy/
    __init__.py
    config.py     # load ~/.config/claude-buddy/config.toml
    state.py      # SessionStore: pure aggregation (main unit-test target)
    heartbeat.py  # encode() + connect messages (time sync, owner)
    hook.py       # thin client: stdin -> unix socket; claude-buddy-hook entrypoint
    transport.py  # Transport protocol + StdoutTransport (dry run)
    ble.py        # BleTransport: bleak connect/scan/reconnect/write
    daemon.py     # asyncio wiring; claude-buddy entrypoint
  tests/
    test_state.py
    test_heartbeat.py
    test_hook.py
    test_daemon.py
src/main.cpp                  # one-line derive() busy threshold change
```

---

## Task 1: Project scaffold + config

**Files:**
- Create: `linux-bridge/pyproject.toml`
- Create: `linux-bridge/src/claude_buddy/__init__.py`
- Create: `linux-bridge/src/claude_buddy/config.py`
- Test: `linux-bridge/tests/test_config.py`

**Interfaces:**
- Produces: `config.Config` dataclass with fields `address: str | None`, `owner: str`, `socket_path: str`; `config.load(path: Path | None = None) -> Config`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "claude-buddy"
version = "0.1.0"
description = "Linux BLE bridge: Claude Code activity -> M5StickC buddy"
requires-python = ">=3.11"
dependencies = ["bleak>=0.22"]

[project.scripts]
claude-buddy = "claude_buddy.daemon:main"
claude-buddy-hook = "claude_buddy.hook:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/claude_buddy"]

[dependency-groups]
dev = ["pytest>=8"]
```

- [ ] **Step 2: Create empty package marker**

`linux-bridge/src/claude_buddy/__init__.py`:
```python
__version__ = "0.1.0"
```

- [ ] **Step 3: Write the failing config test**

`linux-bridge/tests/test_config.py`:
```python
from pathlib import Path
from claude_buddy.config import load


def test_load_defaults_when_missing(tmp_path):
    cfg = load(tmp_path / "nope.toml")
    assert cfg.address is None
    assert cfg.owner == ""
    assert cfg.socket_path.endswith("claude-buddy.sock")


def test_load_reads_values(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('address = "AA:BB:CC:DD:EE:FF"\nowner = "Thomas"\n')
    cfg = load(p)
    assert cfg.address == "AA:BB:CC:DD:EE:FF"
    assert cfg.owner == "Thomas"
```

- [ ] **Step 4: Run test, verify it fails**

Run: `cd linux-bridge && uv run pytest tests/test_config.py -v`
Expected: FAIL (ModuleNotFoundError: claude_buddy.config)

- [ ] **Step 5: Implement `config.py`**

```python
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


def _default_socket() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return str(Path(base) / "claude-buddy.sock")


@dataclass
class Config:
    address: str | None = None
    owner: str = ""
    socket_path: str = ""


def _default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "claude-buddy" / "config.toml"


def load(path: Path | None = None) -> Config:
    path = path or _default_config_path()
    data = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
    return Config(
        address=data.get("address"),
        owner=data.get("owner", ""),
        socket_path=data.get("socket") or _default_socket(),
    )
```

- [ ] **Step 6: Run test, verify pass**

Run: `cd linux-bridge && uv run pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add linux-bridge/pyproject.toml linux-bridge/src/claude_buddy/__init__.py linux-bridge/src/claude_buddy/config.py linux-bridge/tests/test_config.py
git commit -m "feat(bridge): project scaffold + config loader"
```

---

## Task 2: SessionStore aggregation (core logic)

**Files:**
- Create: `linux-bridge/src/claude_buddy/state.py`
- Test: `linux-bridge/tests/test_state.py`

**Interfaces:**
- Produces: `state.SessionStore(stale_after=300.0, max_entries=6, clock=time.monotonic)` with methods:
  - `session_start(sid: str)`, `prompt_submit(sid: str)`, `post_tool(sid: str, tool: str, detail: str = "")`, `notification(sid: str)`, `stop(sid: str)`, `session_end(sid: str)`
  - `sweep() -> None` (prune sessions whose `last_seen` is older than `stale_after`)
  - `snapshot() -> dict` returning keys `total, running, waiting, msg, entries, completed`; the `completed` pulse is True only on the first snapshot after a `stop()` and resets to False afterward.
- Consumes: nothing.

- [ ] **Step 1: Write failing tests**

`linux-bridge/tests/test_state.py`:
```python
from claude_buddy.state import SessionStore


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_idle_when_empty():
    s = SessionStore(clock=FakeClock())
    snap = s.snapshot()
    assert snap["total"] == 0
    assert snap["running"] == 0
    assert snap["waiting"] == 0
    assert snap["completed"] is False


def test_running_after_prompt():
    s = SessionStore(clock=FakeClock())
    s.session_start("a")
    s.prompt_submit("a")
    snap = s.snapshot()
    assert snap["total"] == 1
    assert snap["running"] == 1


def test_post_tool_sets_activity_and_keeps_running():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a")
    s.post_tool("a", "Bash", "git push")
    snap = s.snapshot()
    assert snap["running"] == 1
    assert snap["msg"] == "Bash: git push"
    assert snap["entries"][0] == "Bash: git push"


def test_notification_sets_waiting_cleared_by_activity():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a")
    s.notification("a")
    assert s.snapshot()["waiting"] == 1
    s.post_tool("a", "Read", "main.cpp")
    assert s.snapshot()["waiting"] == 0


def test_stop_clears_running_and_pulses_completed_once():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a")
    s.stop("a")
    first = s.snapshot()
    assert first["running"] == 0
    assert first["completed"] is True
    second = s.snapshot()
    assert second["completed"] is False


def test_session_end_removes_session():
    s = SessionStore(clock=FakeClock())
    s.session_start("a")
    s.session_end("a")
    assert s.snapshot()["total"] == 0


def test_aggregate_two_sessions():
    s = SessionStore(clock=FakeClock())
    s.prompt_submit("a")
    s.prompt_submit("b")
    s.notification("b")
    snap = s.snapshot()
    assert snap["total"] == 2
    assert snap["running"] == 2
    assert snap["waiting"] == 1


def test_sweep_prunes_stale():
    clock = FakeClock()
    s = SessionStore(stale_after=300.0, clock=clock)
    s.prompt_submit("a")
    clock.t += 301
    s.sweep()
    assert s.snapshot()["total"] == 0


def test_entries_capped_newest_first():
    s = SessionStore(max_entries=2, clock=FakeClock())
    s.post_tool("a", "Read", "1")
    s.post_tool("a", "Read", "2")
    s.post_tool("a", "Read", "3")
    entries = s.snapshot()["entries"]
    assert entries == ["Read: 3", "Read: 2"]
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_state.py -v`
Expected: FAIL (ModuleNotFoundError: claude_buddy.state)

- [ ] **Step 3: Implement `state.py`**

```python
import time
from dataclasses import dataclass, field


@dataclass
class _Session:
    running: bool = False
    waiting: bool = False
    last_seen: float = 0.0


def _line(tool: str, detail: str) -> str:
    return f"{tool}: {detail}" if detail else tool


class SessionStore:
    def __init__(self, stale_after: float = 300.0, max_entries: int = 6,
                 clock=time.monotonic):
        self._sessions: dict[str, _Session] = {}
        self._stale_after = stale_after
        self._max_entries = max_entries
        self._clock = clock
        self._recent: list[str] = []   # newest first
        self._completed = False

    def _touch(self, sid: str) -> _Session:
        s = self._sessions.get(sid)
        if s is None:
            s = _Session()
            self._sessions[sid] = s
        s.last_seen = self._clock()
        return s

    def session_start(self, sid: str) -> None:
        self._touch(sid)

    def prompt_submit(self, sid: str) -> None:
        s = self._touch(sid)
        s.running = True
        s.waiting = False

    def post_tool(self, sid: str, tool: str, detail: str = "") -> None:
        s = self._touch(sid)
        s.running = True
        s.waiting = False
        self._recent.insert(0, _line(tool, detail))
        del self._recent[self._max_entries:]

    def notification(self, sid: str) -> None:
        s = self._touch(sid)
        s.waiting = True

    def stop(self, sid: str) -> None:
        s = self._touch(sid)
        s.running = False
        s.waiting = False
        self._completed = True

    def session_end(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    def sweep(self) -> None:
        now = self._clock()
        stale = [k for k, s in self._sessions.items()
                 if now - s.last_seen > self._stale_after]
        for k in stale:
            del self._sessions[k]

    def snapshot(self) -> dict:
        running = sum(1 for s in self._sessions.values() if s.running)
        waiting = sum(1 for s in self._sessions.values() if s.waiting)
        completed = self._completed
        self._completed = False
        msg = self._recent[0] if self._recent else ("working" if running else "idle")
        return {
            "total": len(self._sessions),
            "running": running,
            "waiting": waiting,
            "msg": msg,
            "entries": list(self._recent),
            "completed": completed,
        }
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd linux-bridge && uv run pytest tests/test_state.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add linux-bridge/src/claude_buddy/state.py linux-bridge/tests/test_state.py
git commit -m "feat(bridge): SessionStore aggregation logic"
```

---

## Task 3: Heartbeat encoding + connect messages

**Files:**
- Create: `linux-bridge/src/claude_buddy/heartbeat.py`
- Test: `linux-bridge/tests/test_heartbeat.py`

**Interfaces:**
- Consumes: `snapshot` dict from `SessionStore.snapshot()`.
- Produces:
  - `heartbeat.encode(obj: dict) -> bytes` — JSON + trailing `\n`, compact.
  - `heartbeat.time_sync(now_epoch: int, tz_offset_sec: int) -> dict` -> `{"time": [epoch, tz]}`.
  - `heartbeat.owner_msg(name: str) -> dict` -> `{"cmd": "owner", "name": name}`.

- [ ] **Step 1: Write failing tests**

`linux-bridge/tests/test_heartbeat.py`:
```python
import json
from claude_buddy import heartbeat


def test_encode_is_json_line():
    out = heartbeat.encode({"a": 1})
    assert out.endswith(b"\n")
    assert json.loads(out) == {"a": 1}
    assert b" " not in out  # compact separators


def test_time_sync_shape():
    assert heartbeat.time_sync(1775731234, -25200) == {"time": [1775731234, -25200]}


def test_owner_msg_shape():
    assert heartbeat.owner_msg("Thomas") == {"cmd": "owner", "name": "Thomas"}
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_heartbeat.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement `heartbeat.py`**

```python
import json


def encode(obj: dict) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def time_sync(now_epoch: int, tz_offset_sec: int) -> dict:
    return {"time": [now_epoch, tz_offset_sec]}


def owner_msg(name: str) -> dict:
    return {"cmd": "owner", "name": name}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd linux-bridge && uv run pytest tests/test_heartbeat.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add linux-bridge/src/claude_buddy/heartbeat.py linux-bridge/tests/test_heartbeat.py
git commit -m "feat(bridge): heartbeat encoding + connect messages"
```

---

## Task 4: Thin hook client

**Files:**
- Create: `linux-bridge/src/claude_buddy/hook.py`
- Test: `linux-bridge/tests/test_hook.py`

**Interfaces:**
- Consumes: Claude Code hook JSON on stdin (fields used: `session_id`, `tool_name`, `tool_input`).
- Produces:
  - `hook.map_event(event: str, data: dict) -> dict | None` — translate a hook event + stdin payload into a wire event `{"event","session_id",...}`. Returns None if it should be ignored.
  - `hook.send(payload: dict, socket_path: str, timeout: float = 0.2) -> None` — best-effort send; swallows all errors.
  - `hook.main(argv=None) -> int` — entrypoint; always returns 0.

Wire events produced (consumed by Task 5):
`{"event":"session_start","session_id":sid}`,
`{"event":"prompt_submit","session_id":sid}`,
`{"event":"post_tool","session_id":sid,"tool":str,"detail":str}`,
`{"event":"notification","session_id":sid}`,
`{"event":"stop","session_id":sid}`,
`{"event":"session_end","session_id":sid}`.

- [ ] **Step 1: Write failing tests**

`linux-bridge/tests/test_hook.py`:
```python
import json
import socket
import threading
from claude_buddy import hook


def test_map_post_tool_extracts_detail():
    data = {"session_id": "a", "tool_name": "Bash",
            "tool_input": {"command": "git push"}}
    out = hook.map_event("post-tool", data)
    assert out == {"event": "post_tool", "session_id": "a",
                   "tool": "Bash", "detail": "git push"}


def test_map_post_tool_file_path_detail():
    data = {"session_id": "a", "tool_name": "Read",
            "tool_input": {"file_path": "/x/main.cpp"}}
    out = hook.map_event("post-tool", data)
    assert out == {"event": "post_tool", "session_id": "a",
                   "tool": "Read", "detail": "main.cpp"}


def test_map_simple_events():
    assert hook.map_event("stop", {"session_id": "a"}) == {
        "event": "stop", "session_id": "a"}
    assert hook.map_event("notification", {"session_id": "a"}) == {
        "event": "notification", "session_id": "a"}


def test_map_ignores_no_session():
    assert hook.map_event("stop", {}) is None


def test_send_delivers_line(tmp_path):
    sock_path = str(tmp_path / "s.sock")
    received = []
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)

    def accept():
        conn, _ = srv.accept()
        received.append(conn.recv(1024))
        conn.close()

    t = threading.Thread(target=accept)
    t.start()
    hook.send({"event": "stop", "session_id": "a"}, sock_path)
    t.join(timeout=2)
    srv.close()
    assert json.loads(received[0]) == {"event": "stop", "session_id": "a"}


def test_send_swallows_errors_when_no_server(tmp_path):
    # No server listening -> must not raise.
    hook.send({"event": "stop", "session_id": "a"},
              str(tmp_path / "absent.sock"))


def test_main_always_returns_zero(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "a"})))
    monkeypatch.setenv("CLAUDE_BUDDY_SOCKET", str(tmp_path / "absent.sock"))
    assert hook.main(["claude-buddy-hook", "stop"]) == 0
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_hook.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement `hook.py`**

```python
import json
import os
import socket
import sys

from .config import load

_SIMPLE = {
    "session-start": "session_start",
    "prompt-submit": "prompt_submit",
    "notification": "notification",
    "stop": "stop",
    "session-end": "session_end",
}


def _detail(tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return ""
    if "command" in tool_input:
        return str(tool_input["command"])[:40]
    if "file_path" in tool_input:
        return os.path.basename(str(tool_input["file_path"]))
    return ""


def map_event(event: str, data: dict) -> dict | None:
    sid = data.get("session_id")
    if not sid:
        return None
    if event == "post-tool":
        return {"event": "post_tool", "session_id": sid,
                "tool": data.get("tool_name", "tool"),
                "detail": _detail(data.get("tool_input", {}))}
    name = _SIMPLE.get(event)
    if name is None:
        return None
    return {"event": name, "session_id": sid}


def send(payload: dict, socket_path: str, timeout: float = 0.2) -> None:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(socket_path)
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        s.close()
    except Exception:
        pass  # never disrupt a Claude Code hook


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv
    try:
        event = argv[1]
        raw = sys.stdin.read() or "{}"
        data = json.loads(raw) if raw.strip() else {}
        payload = map_event(event, data)
        if payload is not None:
            sock = os.environ.get("CLAUDE_BUDDY_SOCKET") or load().socket_path
            send(payload, sock)
    except Exception:
        pass
    return 0
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd linux-bridge && uv run pytest tests/test_hook.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add linux-bridge/src/claude_buddy/hook.py linux-bridge/tests/test_hook.py
git commit -m "feat(bridge): thin hook client (never fails a hook)"
```

---

## Task 5: Transport abstraction + stdout dry-run

**Files:**
- Create: `linux-bridge/src/claude_buddy/transport.py`
- Test: `linux-bridge/tests/test_transport.py`

**Interfaces:**
- Produces:
  - `transport.Transport` — typing.Protocol with `async def send(self, data: bytes) -> None`.
  - `transport.StdoutTransport` — prints each line to stdout (dry run); implements `send`.
  - `transport.FakeTransport` — records sent bytes in `.sent: list[bytes]` (for tests).

- [ ] **Step 1: Write failing test**

`linux-bridge/tests/test_transport.py`:
```python
import asyncio
from claude_buddy.transport import FakeTransport, StdoutTransport


def test_fake_records():
    t = FakeTransport()
    asyncio.run(t.send(b"hi\n"))
    assert t.sent == [b"hi\n"]


def test_stdout_prints(capsys):
    t = StdoutTransport()
    asyncio.run(t.send(b'{"a":1}\n'))
    assert '{"a":1}' in capsys.readouterr().out
```

- [ ] **Step 2: Run test, verify it fails**

Run: `cd linux-bridge && uv run pytest tests/test_transport.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement `transport.py`**

```python
import sys
from typing import Protocol


class Transport(Protocol):
    async def send(self, data: bytes) -> None: ...


class FakeTransport:
    def __init__(self):
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


class StdoutTransport:
    async def send(self, data: bytes) -> None:
        sys.stdout.write(data.decode("utf-8", "replace"))
        sys.stdout.flush()
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd linux-bridge && uv run pytest tests/test_transport.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add linux-bridge/src/claude_buddy/transport.py linux-bridge/tests/test_transport.py
git commit -m "feat(bridge): transport protocol + stdout/fake transports"
```

---

## Task 6: Daemon core — socket server, event apply, push scheduler

**Files:**
- Create: `linux-bridge/src/claude_buddy/daemon.py`
- Test: `linux-bridge/tests/test_daemon.py`

**Interfaces:**
- Consumes: `SessionStore` (Task 2), `heartbeat.encode` (Task 3), `transport.Transport` (Task 5).
- Produces:
  - `daemon.apply_event(store: SessionStore, payload: dict) -> None` — dispatch a wire event to the store.
  - `daemon.Bridge(store, transport, socket_path)` with `async def handle_conn(reader, writer)`, `async def push()` (snapshot -> encode -> transport.send), and `async def serve()` (start unix server). Pushes are the integration point; keepalive/sweep loops live in `run()`.
  - `daemon.main(argv=None) -> int` — entrypoint (parses `--stdout`, wires real BLE otherwise).

- [ ] **Step 1: Write failing tests**

`linux-bridge/tests/test_daemon.py`:
```python
import asyncio
import json
from claude_buddy.state import SessionStore
from claude_buddy.transport import FakeTransport
from claude_buddy import daemon


def test_apply_event_dispatches():
    s = SessionStore()
    daemon.apply_event(s, {"event": "prompt_submit", "session_id": "a"})
    daemon.apply_event(s, {"event": "post_tool", "session_id": "a",
                           "tool": "Bash", "detail": "ls"})
    snap = s.snapshot()
    assert snap["running"] == 1
    assert snap["entries"][0] == "Bash: ls"


def test_apply_event_ignores_unknown():
    s = SessionStore()
    daemon.apply_event(s, {"event": "bogus", "session_id": "a"})
    assert s.snapshot()["total"] == 0


def test_push_sends_encoded_snapshot():
    s = SessionStore()
    s.prompt_submit("a")
    t = FakeTransport()
    b = daemon.Bridge(s, t, "/tmp/unused.sock")
    asyncio.run(b.push())
    assert len(t.sent) == 1
    obj = json.loads(t.sent[0])
    assert obj["running"] == 1


def test_socket_event_reaches_store(tmp_path):
    async def scenario():
        s = SessionStore()
        t = FakeTransport()
        sock = str(tmp_path / "d.sock")
        b = daemon.Bridge(s, t, sock)
        server = await b.serve()
        reader, writer = await asyncio.open_unix_connection(sock)
        writer.write(json.dumps(
            {"event": "prompt_submit", "session_id": "a"}).encode() + b"\n")
        await writer.drain()
        writer.close()
        await asyncio.sleep(0.05)
        server.close()
        await server.wait_closed()
        return s.snapshot()

    snap = asyncio.run(scenario())
    assert snap["running"] == 1
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd linux-bridge && uv run pytest tests/test_daemon.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement `daemon.py`**

```python
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

from . import heartbeat
from .config import load
from .state import SessionStore
from .transport import StdoutTransport

_DISPATCH = {
    "session_start": lambda s, p: s.session_start(p["session_id"]),
    "prompt_submit": lambda s, p: s.prompt_submit(p["session_id"]),
    "post_tool": lambda s, p: s.post_tool(
        p["session_id"], p.get("tool", "tool"), p.get("detail", "")),
    "notification": lambda s, p: s.notification(p["session_id"]),
    "stop": lambda s, p: s.stop(p["session_id"]),
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
                apply_event(self.store, json.loads(line))
                self._dirty.set()
            except Exception:
                pass
        writer.close()

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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd linux-bridge && uv run pytest tests/test_daemon.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add linux-bridge/src/claude_buddy/daemon.py linux-bridge/tests/test_daemon.py
git commit -m "feat(bridge): daemon socket server + push scheduler"
```

---

## Task 7: BLE transport (bleak)

**Files:**
- Create: `linux-bridge/src/claude_buddy/ble.py`

**Interfaces:**
- Consumes: `Config` (Task 1), `SessionStore` (Task 2), `Bridge` (Task 6), `heartbeat` (Task 3).
- Produces:
  - `ble.BleTransport(client, rx_uuid)` implementing `async def send(self, data: bytes)` with 180-byte chunking.
  - `ble.run_with_ble(cfg, store, on_connect) -> None` — connect/scan/reconnect loop that builds a `Bridge` with a `BleTransport` once connected, calls `on_connect(transport, cfg.owner)`, and serves until disconnect, then retries.

Note: this module is exercised by the hardware smoke test (Task 9), not unit
tests — `bleak` requires a real adapter and a bonded device. Keep all pure
logic in earlier, tested modules.

- [ ] **Step 1: Implement `ble.py`**

```python
import asyncio

from bleak import BleakClient, BleakScanner

from .daemon import Bridge

NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # write to device
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # notify from device
NAME_PREFIX = "Claude-"
CHUNK = 180


class BleTransport:
    def __init__(self, client: BleakClient, rx_uuid: str = NUS_RX):
        self._client = client
        self._rx = rx_uuid

    async def send(self, data: bytes) -> None:
        for i in range(0, len(data), CHUNK):
            await self._client.write_gatt_char(
                self._rx, data[i:i + CHUNK], response=False)
            await asyncio.sleep(0.01)


async def _resolve_address(cfg) -> str | None:
    if cfg.address:
        return cfg.address
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or "").startswith(NAME_PREFIX), timeout=10.0)
    return dev.address if dev else None


async def run_with_ble(cfg, store, on_connect) -> None:
    backoff = 1.0
    while True:
        address = await _resolve_address(cfg)
        if not address:
            print("[claude-buddy] no Claude- device found; is it awake? "
                  "have you paired with bluetoothctl?")
            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)
            continue
        try:
            async with BleakClient(address) as client:
                print(f"[claude-buddy] connected {address}")
                backoff = 1.0
                transport = BleTransport(client)
                # TX notify is encrypted-only; subscribing forces the
                # encrypted link up (and lets the device send acks).
                try:
                    await client.start_notify(NUS_TX, lambda _c, _d: None)
                except Exception:
                    pass
                await on_connect(transport, cfg.owner)
                bridge = Bridge(store, transport, cfg.socket_path)
                await bridge.run()
        except Exception as e:
            print(f"[claude-buddy] disconnected: {e}")
            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)
```

- [ ] **Step 2: Verify it imports**

Run: `cd linux-bridge && uv run python -c "import claude_buddy.ble"`
Expected: no output, exit 0 (bleak installed, module imports).

- [ ] **Step 3: Verify full test suite still green**

Run: `cd linux-bridge && uv run pytest -v`
Expected: PASS (all prior tests)

- [ ] **Step 4: Commit**

```bash
git add linux-bridge/src/claude_buddy/ble.py
git commit -m "feat(bridge): bleak BLE transport + reconnect loop"
```

---

## Task 8: Firmware busy-threshold patch

**Files:**
- Modify: `src/main.cpp` (the `derive()` function)

**Interfaces:** none (firmware behavior change only).

- [ ] **Step 1: Edit `derive()` threshold**

In `src/main.cpp`, find:
```cpp
  if (s.sessionsRunning >= 3)  return P_BUSY;
```
Replace with:
```cpp
  if (s.sessionsRunning >= 1)  return P_BUSY;   // single Claude Code session = busy (Linux bridge)
```

- [ ] **Step 2: Build firmware**

Run: `cd /home/gumptionthomas/Development/claude-desktop-buddy && pio run`
Expected: `[SUCCESS]`

- [ ] **Step 3: Commit**

```bash
git add src/main.cpp
git commit -m "feat(firmware): busy at running>=1 for single-session Claude Code"
```

---

## Task 9: Hooks config, README, install + hardware smoke test

**Files:**
- Create: `linux-bridge/hooks-settings.example.json`
- Create: `linux-bridge/README.md`

**Interfaces:** none (docs/config).

- [ ] **Step 1: Create `hooks-settings.example.json`**

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "claude-buddy-hook session-start" } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "claude-buddy-hook prompt-submit" } ] }
    ],
    "PostToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command", "command": "claude-buddy-hook post-tool" } ] }
    ],
    "Notification": [
      { "hooks": [ { "type": "command", "command": "claude-buddy-hook notification" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "claude-buddy-hook stop" } ] }
    ],
    "SessionEnd": [
      { "hooks": [ { "type": "command", "command": "claude-buddy-hook session-end" } ] }
    ]
  }
}
```

- [ ] **Step 2: Create `linux-bridge/README.md`**

````markdown
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
```

## 3. Install the hooks

Merge `hooks-settings.example.json` into `~/.claude/settings.json` (user
scope, so all Claude Code sessions feed the buddy).

## 4. Run

```bash
claude-buddy            # connects over BLE
claude-buddy --stdout   # dry run: prints heartbeats, no BLE
```

## How it maps

| Claude Code | Pet |
|---|---|
| actively working (running) | busy |
| permission prompt / notification | attention (LED blinks) |
| turn finished | celebrate |
| quiet | idle / sleep |
````

- [ ] **Step 3: Dry-run smoke test (no hardware)**

Run (terminal A): `cd linux-bridge && uv run claude-buddy --stdout`
Run (terminal B):
```bash
printf '{"session_id":"a","tool_name":"Bash","tool_input":{"command":"git push"}}' \
  | CLAUDE_BUDDY_SOCKET="${XDG_RUNTIME_DIR:-/tmp}/claude-buddy.sock" uv run claude-buddy-hook post-tool
```
Expected (terminal A): a JSON line with `"running":1` and `"msg":"Bash: git push"`.

- [ ] **Step 4: Hardware smoke test**

1. Pair via bluetoothctl (above). Put MAC in config.
2. `claude-buddy` — expect `connected <MAC>`.
3. In another terminal, run a real Claude Code session; do work that calls tools, hits a permission prompt, and finishes.
4. Watch the stick: idle → **busy** (running) → **attention** on a permission prompt → **celebrate** on finish → idle.

- [ ] **Step 5: Commit**

```bash
git add linux-bridge/hooks-settings.example.json linux-bridge/README.md
git commit -m "docs(bridge): hooks config, README, smoke tests"
```

---

## Self-Review

**Spec coverage:**
- Ambient display only → Tasks 2/6 (one-way; no PreToolUse) ✅
- All sessions aggregated → Task 2 (SessionStore keyed by session_id) ✅
- No tokens → omitted from heartbeat (Tasks 2/3) ✅
- Unix socket IPC → Tasks 5/6 ✅
- Hook never fails session → Task 4 ✅
- Daemon BLE reconnect/backoff → Task 7 ✅
- Stale sweep → Tasks 2/6 ✅
- `--stdout` dry run → Tasks 5/6 ✅
- One-time bluetoothctl pairing → Task 9 README ✅
- `running >= 1` firmware patch → Task 8 ✅
- uv packaging, console scripts → Task 1 ✅
- State→animation (busy/attention/celebrate/idle) → Tasks 2/8 + Task 9 smoke test ✅

**Placeholder scan:** none — all steps carry concrete code/commands.

**Type consistency:** wire event names (`session_start`, `prompt_submit`, `post_tool`, `notification`, `stop`, `session_end`) match across `hook.map_event` (Task 4), `_DISPATCH` (Task 6), and `SessionStore` methods (Task 2). `Transport.send` signature consistent across Tasks 5/6/7. `Bridge(store, transport, socket_path)` consistent Tasks 6/7. Heartbeat keys (`total/running/waiting/msg/entries/completed`) consistent Tasks 2/3/6.
