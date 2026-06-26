"""Compose a haiku about coding activity via the Anthropic Messages API.

Best-effort and never raises: any failure returns None so the buddy simply keeps
its previous haiku. No SDK dependency — a plain HTTPS POST run in an executor.
"""
import asyncio
import json
import urllib.request

_URL = "https://api.anthropic.com/v1/messages"
_SYSTEM = (
    "You are a tiny desk pet who narrates a programmer's coding session ONLY in "
    "haiku. Given a short activity digest, reply with exactly one haiku: three "
    "lines of roughly 5-7-5 syllables, evocative and a little playful. Output "
    "only the three lines — no title, no commentary, no quotes, no extra text."
)


def _extract(raw: str) -> str:
    obj = json.loads(raw)
    parts = obj.get("content", []) if isinstance(obj, dict) else []
    return "\n".join(
        b.get("text", "") for b in parts
        if isinstance(b, dict) and b.get("type") == "text")


def to_haiku(text: str) -> list[str] | None:
    """Parse model output into up to 3 non-empty lines, or None."""
    text = (text or "").strip()
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) == 1 and "/" in lines[0]:          # "a / b / c" on one line
        lines = [p.strip() for p in lines[0].split("/") if p.strip()]
    return lines[:3] or None


def _post(api_key: str, model: str, digest: str, timeout: float) -> str:
    payload = {
        "model": model,
        "max_tokens": 120,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": digest}],
    }
    req = urllib.request.Request(
        _URL, data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _extract(resp.read().decode("utf-8"))


async def compose(digest, *, api_key, model="claude-haiku-4-5-20251001",
                  request=None, timeout=12.0) -> list[str] | None:
    """Return a haiku (list of up to 3 lines) for the digest, or None on any
    failure. `request` is an injectable async fn(digest)->text for tests."""
    if not api_key or not digest:
        return None
    try:
        if request is not None:
            text = await request(digest)
        else:
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(
                None, _post, api_key, model, digest, timeout)
    except Exception:
        return None
    return to_haiku(text)
