import time
from dataclasses import dataclass


@dataclass
class _Session:
    running: bool = False
    waiting: bool = False
    last_seen: float = 0.0
    project: str = ""


class SessionStore:
    def __init__(self, stale_after: float = 300.0, max_entries: int = 6,
                 clock=time.monotonic):
        self._sessions: dict[str, _Session] = {}
        self._stale_after = stale_after
        self._max_entries = max_entries
        self._clock = clock
        self._recent: list[tuple[str, str]] = []   # (code, text), newest first
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

    def _push(self, code: str, text: str) -> None:
        self._recent.insert(0, (code, text))
        del self._recent[self._max_entries:]

    def prompt_submit(self, sid: str, project: str = "") -> None:
        s = self._touch(sid)
        s.running = True
        s.waiting = False
        if project:
            s.project = project
        # Show "thinking..." the instant a prompt is submitted, so the feed
        # isn't stuck on the previous turn's last command.
        self._push(s.project, "thinking...")

    def post_tool(self, sid: str, project: str = "") -> None:
        # No feed line — tool calls are noise. Just keep the session busy and
        # clear any waiting alert (you've approved and work resumed).
        s = self._touch(sid)
        s.running = True
        s.waiting = False
        if project:
            s.project = project

    def notification(self, sid: str, project: str = "") -> None:
        s = self._touch(sid)
        s.waiting = True
        if project:
            s.project = project

    def stop(self, sid: str, project: str = "") -> None:
        s = self._touch(sid)
        s.running = False
        s.waiting = False
        self._completed = True
        if project:
            s.project = project

    def push_message(self, project: str, text: str) -> None:
        # The buddy "speaks": pushed by the daemon once the transcript has the
        # turn's final assistant message.
        self._push(project, text)

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
        waiters = [s for s in self._sessions.values() if s.waiting]
        completed = self._completed
        self._completed = False

        # (code, text) oldest-first: the firmware treats the LAST entry as
        # newest (drawHUD highlights lines[n-1] and data.h checks
        # lines[n-1] == msg).
        combined = list(reversed(self._recent))
        # Pin one "needs you" per waiting project at the newest end so the alert
        # stays the most-prominent line while a prompt is pending. Recency
        # order, deduped by project.
        seen = set()
        for s in sorted(waiters, key=lambda x: x.last_seen):
            key = s.project or "\0"
            if key in seen:
                continue
            seen.add(key)
            combined.append((s.project, "needs you"))
        combined = combined[-self._max_entries:]

        # Tag with the project code only when the feed spans 2+ projects — a
        # single-project feed needs no disambiguation.
        multi = len({c for c, _ in combined if c}) >= 2
        entries = [f"[{c}] {t}" if (multi and c) else t for c, t in combined]

        msg = entries[-1] if entries else ("working" if running else "idle")
        return {
            "total": len(self._sessions),
            "running": running,
            "waiting": len(waiters),
            "msg": msg,
            "entries": entries,
            "completed": completed,
        }
