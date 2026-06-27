import time
from dataclasses import dataclass, field

# The firmware's GLCD font is ASCII-only; haiku text often has typographic
# punctuation (em/en dashes, curly quotes, ellipsis) that would render as
# multi-byte garbage. Fold to ASCII, drop anything else.
_ASCII_SUBS = {
    "—": "-", "–": "-", "‒": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", " ": " ", "­": "",
}


def _ascii(s: str) -> str:
    for k, v in _ASCII_SUBS.items():
        s = s.replace(k, v)
    return s.encode("ascii", "ignore").decode("ascii")


@dataclass
class _Session:
    running: bool = False
    waiting: bool = False
    last_seen: float = 0.0
    project: str = ""
    activity: list[str] = field(default_factory=list)  # haiku material, this turn
    reply_gist: str = ""


def _activity_label(tool: str, file: str) -> str:
    if file:
        return f"{tool} {file}"
    if tool == "Bash":
        return "ran a command"
    return tool or "did something"


class SessionStore:
    def __init__(self, stale_after: float = 300.0, max_entries: int = 6,
                 clock=time.monotonic, haiku_mode: bool = False):
        self._sessions: dict[str, _Session] = {}
        self._stale_after = stale_after
        self._max_entries = max_entries
        self._clock = clock
        self._haiku_mode = haiku_mode
        self._haiku: list[str] = []            # haiku-mode display (3 lines)
        self._recent: list[tuple[str, str]] = []   # reply-mode display, newest first
        self._completed = False
        self._tokens = 0       # cumulative output tokens since start; feeds level
        self._tokens_today = 0  # output tokens since local midnight (reset_today)

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
        s.activity = []          # fresh turn material
        s.reply_gist = ""
        if not self._haiku_mode:
            self._push(s.project, "thinking...")

    def post_tool(self, sid: str, project: str = "",
                  tool: str = "", file: str = "") -> None:
        # No feed line — tool calls keep the session busy, clear the waiting
        # alert, and accrue material for the next haiku.
        s = self._touch(sid)
        s.running = True
        s.waiting = False
        if project:
            s.project = project
        label = _activity_label(tool, file)
        if label and (not s.activity or s.activity[-1] != label):
            s.activity.append(label)
            del s.activity[:-8]   # keep the last 8

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

    def record_reply(self, sid: str, gist: str) -> None:
        # The turn's reply, kept as haiku material (not displayed in haiku mode).
        self._touch(sid).reply_gist = " ".join(str(gist).split())[:200]

    def push_message(self, project: str, text: str) -> None:
        # Reply-mode (no api_key) display: the buddy "speaks" the reply snippet.
        self._push(project, text)

    def set_haiku(self, lines) -> None:
        self._haiku = [str(x) for x in lines][:3]

    def digest(self, focus_sid: str = "") -> str:
        parts = []
        foc = self._sessions.get(focus_sid)
        if foc:
            parts.append(self._session_digest("Focus", foc))
        for sid, s in self._sessions.items():
            if sid == focus_sid or not (s.running or s.waiting):
                continue
            parts.append(self._session_digest("Also", s))
        return "\n".join(p for p in parts if p)

    def _session_digest(self, prefix: str, s: _Session) -> str:
        bits = []
        if s.activity:
            bits.append(", ".join(s.activity))
        if s.reply_gist:
            bits.append(f'reply: "{s.reply_gist}"')
        if s.waiting:
            bits.append("waiting for permission")
        body = "; ".join(bits) if bits else "active"
        return f"{prefix} [{s.project or '?'}]: {body}"

    def add_tokens(self, n: int) -> None:
        # Cumulative output tokens; the firmware tracks deltas and levels the
        # pet every 50K (celebrating on level-up). Also feeds today's tally.
        if isinstance(n, int) and n > 0:
            self._tokens += n
            self._tokens_today += n

    def reset_today(self) -> None:
        self._tokens_today = 0

    def latest_running(self) -> str:
        cands = [(s.last_seen, sid) for sid, s in self._sessions.items()
                 if s.running]
        return max(cands)[1] if cands else ""

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

        # (code, text) oldest-first; firmware treats the LAST entry as newest.
        if self._haiku_mode:
            combined = [("", ln) for ln in self._haiku]   # haiku is aggregate -> untagged
        else:
            combined = list(reversed(self._recent))
        # Pin one "needs you" per waiting project at the newest end, deduped.
        seen = set()
        for s in sorted(waiters, key=lambda x: x.last_seen):
            key = s.project or "\0"
            if key in seen:
                continue
            seen.add(key)
            combined.append((s.project, "needs you"))
        combined = combined[-self._max_entries:]

        # Tag with the project code only when the displayed feed spans 2+
        # projects (haiku lines have no code, so they're always untagged).
        multi = len({c for c, _ in combined if c}) >= 2
        entries = [_ascii(f"[{c}] {t}" if (multi and c) else t)
                   for c, t in combined]

        msg = entries[-1] if entries else ("working" if running else "idle")
        return {
            "total": len(self._sessions),
            "running": running,
            "waiting": len(waiters),
            "msg": msg,
            "entries": entries,
            "completed": completed,
            "tokens": self._tokens,
            "tokens_today": self._tokens_today,
        }
