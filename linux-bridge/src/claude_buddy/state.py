import time
from dataclasses import dataclass


@dataclass
class _Session:
    running: bool = False
    waiting: bool = False
    last_seen: float = 0.0
    project: str = ""


def _line(tool: str, detail: str, project: str = "") -> str:
    core = f"{tool}: {detail}" if detail else tool
    return f"[{project}] {core}" if project else core


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

    def post_tool(self, sid: str, tool: str, detail: str = "",
                  project: str = "") -> None:
        s = self._touch(sid)
        s.running = True
        s.waiting = False
        if project:
            s.project = project
        self._recent.insert(0, _line(tool, detail, project))
        del self._recent[self._max_entries:]

    def notification(self, sid: str, project: str = "") -> None:
        s = self._touch(sid)
        s.waiting = True
        if project:
            s.project = project

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
        waiters = [s for s in self._sessions.values() if s.waiting]
        completed = self._completed
        self._completed = False

        # Activity oldest-first: the firmware treats the LAST entry as newest
        # (drawHUD highlights lines[n-1] and data.h checks lines[n-1] == msg).
        entries = list(reversed(self._recent))
        # Pin a "needs you" line per waiting session at the newest end so the
        # alert stays the most-prominent line while a prompt is pending,
        # instead of being buried by concurrent activity. Recency order, deduped.
        for s in sorted(waiters, key=lambda x: x.last_seen):
            alert = f"{s.project}: needs you" if s.project else "needs you"
            if alert in entries:
                entries.remove(alert)
            entries.append(alert)
        entries = entries[-self._max_entries:]

        msg = entries[-1] if entries else ("working" if running else "idle")
        return {
            "total": len(self._sessions),
            "running": running,
            "waiting": len(waiters),
            "msg": msg,
            "entries": entries,
            "completed": completed,
        }
