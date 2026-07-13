"""A local, append-only archive of the haikus the buddy has composed.

Outputs ONLY. The model's input (the digest) contains the user's prompts, their
file activity, and Claude's replies -- a record of real work -- and is
deliberately never stored here. See the design doc: this archive can tell you
THAT the haikus are drifting (repetition, recycled imagery, banned tropes), never
WHY. Diagnosis stays a manual investigation.

Best-effort throughout: a failed write is swallowed. The buddy must never break
because a log file failed.
"""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path


def _default_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "familiar" / "haikus.jsonl"


DEFAULT_PATH = _default_path()


def prompt_id(system: str) -> str:
    """Short, stable id for a system-prompt version.

    Recorded on every haiku so that rewording the prompt shows up as a boundary
    in the stats instead of silently mixing two eras into one trend line.
    """
    return hashlib.sha256((system or "").encode("utf-8")).hexdigest()[:8]


def append(lines, *, model: str, prompt: str,
           path: Path = DEFAULT_PATH, now=None) -> bool:
    """Append one haiku. Returns True on success, False on ANY failure.

    NEVER raises: this runs on the daemon's compose path, and an archive is a
    nicety while the pet is the product.
    """
    try:
        when = now or datetime.now().astimezone()
        rec = {"ts": when.isoformat(), "lines": list(lines),
               "model": model, "prompt": prompt}
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def load(path: Path = DEFAULT_PATH, limit: int | None = None) -> list[dict]:
    """Read the archive oldest-first. `limit` keeps the most recent N.

    A corrupt or truncated line (e.g. a power cut mid-write) is skipped, not
    fatal -- an append-only log a crash can render unreadable is a bad log.
    """
    recs = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue                    # skip the bad line, keep going
                if isinstance(rec, dict) and isinstance(rec.get("lines"), list):
                    recs.append(rec)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return recs[-limit:] if limit else recs
