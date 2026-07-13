"""A local, append-only archive of the haikus the buddy has composed.

Outputs ONLY. The model's input (the digest) contains the user's prompts, their
file activity, and Claude's replies -- a record of real work -- and is
deliberately never stored here. See the design doc: this archive can tell you
THAT the haikus are drifting (repetition, recycled imagery, banned tropes), never
WHY. Diagnosis stays a manual investigation.

Best-effort throughout: a failed write is swallowed. The buddy must never break
because a log file failed.
"""
import argparse
import hashlib
import json
import os
import re
from collections import Counter
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

    `limit=0` and `limit=None` both mean "no limit" -- this is intentional.
    Do not "fix" `recs[-limit:] if limit else recs` below to special-case 0;
    doing so would silently break the CLI's "0 = all" contract.

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


# Small and deliberate: enough to stop "the" topping every report, no more.
STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "and", "to", "is", "it", "its",
    "with", "at", "by", "for", "from", "as", "into",
}

# Imagery the system prompt explicitly BANS ("Avoid the tired tropes: no
# keyboards, keys, clicking, typing fingers, glowing screens, or blinking
# cursors"). Counting these measures whether the model obeys its own prompt --
# the one statistic most likely to change what you do about it.
TROPES = [
    "keyboard", "keyboards", "key", "keys", "click", "clicking", "clicks",
    "type", "typing", "typed", "finger", "fingers", "screen", "screens",
    "glow", "glowing", "cursor", "cursors", "blink", "blinking",
]

_WORD = re.compile(r"[a-z']+")


def _words(rec) -> set[str]:
    """The distinct lowercase words in one haiku (a SET: we measure how many
    haikus contain a word, not how many times it occurs)."""
    text = " ".join(rec.get("lines") or []).lower()
    return set(_WORD.findall(text))


def _doc_freq(records, vocabulary=None):
    """(word, n_haikus, share) desc -- the share of haikus containing the word."""
    total = len(records)
    if not total:
        return []
    seen = Counter()
    for rec in records:
        for w in _words(rec):
            if vocabulary is not None and w not in vocabulary:
                continue
            if vocabulary is None and (w in STOPWORDS or len(w) < 3):
                continue
            seen[w] += 1
    return [(w, n, n / total)
            for w, n in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))]


def _repeated_lines(records):
    """Lines emitted verbatim in two or more DIFFERENT haikus, desc."""
    counts = Counter()
    for rec in records:
        # dedupe within one haiku: repeating a line inside a poem is a choice,
        # reusing it across poems is staleness.
        for line in {" ".join((ln or "").lower().split())
                     for ln in (rec.get("lines") or []) if ln.strip()}:
            counts[line] += 1
    return sorted(((ln, n) for ln, n in counts.items() if n >= 2),
                  key=lambda kv: (-kv[1], kv[0]))


def stats(records) -> dict:
    records = list(records)
    by_prompt = {}
    for pid in sorted({r.get("prompt", "") for r in records}):
        group = [r for r in records if r.get("prompt", "") == pid]
        by_prompt[pid] = {
            "count": len(group),
            "imagery": _doc_freq(group)[:10],
            "tropes": _doc_freq(group, vocabulary=set(TROPES)),
        }
    return {
        "count": len(records),
        "imagery": _doc_freq(records)[:20],
        "repeated": _repeated_lines(records),
        "tropes": _doc_freq(records, vocabulary=set(TROPES)),
        "by_prompt": by_prompt,
    }


def _fmt_share(n: int, share: float) -> str:
    return f"{share * 100:4.0f}%  ({n})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="familiar haikus")
    ap.add_argument("--stats", action="store_true",
                    help="show trends instead of the haikus themselves")
    ap.add_argument("--limit", type=int, default=20,
                    help="how many of the most recent haikus to use "
                         "(default 20; 0 = all)")
    ap.add_argument("--path", default=str(DEFAULT_PATH),
                    help=argparse.SUPPRESS)     # tests only
    args = ap.parse_args(argv)

    limit = None if args.limit == 0 else args.limit
    records = load(Path(args.path), limit=limit)
    if not records:
        print("no haikus archived yet — the buddy writes one each time it "
              "composes (haiku mode must be on)")
        return 0

    if not args.stats:
        for rec in records:
            print(f"  {rec.get('ts', '')[:16].replace('T', ' ')}")
            for line in rec.get("lines") or []:
                print(f"    {line}")
            print()
        return 0

    st = stats(records)
    print(f"{st['count']} haikus, {len(st['by_prompt'])} prompt version(s)\n")

    print("RECURRING IMAGERY (share of haikus containing the word)")
    for w, n, share in st["imagery"][:10]:
        print(f"  {w:<14}{_fmt_share(n, share)}")

    print("\nREPEATED LINES")
    if st["repeated"]:
        for line, n in st["repeated"][:10]:
            print(f"  {n}x  \"{line}\"")
    else:
        print("  (none — every line so far is unique)")

    print("\nTROPE VIOLATIONS (imagery the system prompt bans)")
    if st["tropes"]:
        for w, n, share in st["tropes"]:
            print(f"  {w:<14}{_fmt_share(n, share)}")
    else:
        print("  (none — the prompt is holding)")

    if len(st["by_prompt"]) > 1:
        print("\nBY PROMPT VERSION")
        for pid, g in st["by_prompt"].items():
            top = g["imagery"][0][0] if g["imagery"] else "-"
            tropes = sum(n for _w, n, _s in g["tropes"])
            print(f"  {pid}  {g['count']:>4} haikus   top: {top:<12} "
                  f"trope hits: {tropes}")
    return 0
