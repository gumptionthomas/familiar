"""Push the buddy pet + haiku to a Tidbyt 64x32 over the HTTP API.

Best-effort: any failure (no config, network, non-200) is swallowed so it never
disturbs the M5 path. It is still *reported* — a silent push failure is
indistinguishable from a healthy one, which once hid a wrong installationID for
a whole session. We push every turn-end, so warn once per outage rather than
per attempt. `poster` is injectable for tests.
"""
import asyncio
import base64
import json
import sys
import urllib.request

from . import haiku_render

PUSH_URL = "https://api.tidbyt.com/v0/devices/%s/push"
# Must stay alphanumeric — the API 400s on hyphens, so no "claude-buddy" here.
INSTALLATION_ID = "familiar"


# True once an outage has been reported; reset by the next success so each
# fresh outage speaks up exactly once.
_warned = False


def reset_push_warning() -> None:
    global _warned
    _warned = False


def _warn(msg) -> None:
    global _warned
    if not _warned:
        print(f"[familiar] tidbyt push failed: {msg}", file=sys.stderr)
        _warned = True


def _note_success() -> None:
    global _warned
    if _warned:
        print("[familiar] tidbyt push succeeded again", file=sys.stderr)
        _warned = False


def _post(url, data, headers) -> int:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


async def push_image(webp_bytes, *, device_id, api_token,
                     installation_id=INSTALLATION_ID, poster=None) -> bool:
    if not (device_id and api_token and webp_bytes):
        return False
    post = poster or _post
    body = json.dumps({
        "image": base64.b64encode(webp_bytes).decode(),
        "installationID": installation_id,
        "background": False,
    }).encode()
    headers = {"Authorization": "Bearer " + api_token,
               "Content-Type": "application/json"}
    url = PUSH_URL % device_id
    try:
        status = await asyncio.get_running_loop().run_in_executor(
            None, post, url, body, headers)
    except Exception as e:
        _warn(e)
        return False
    if status != 200:
        _warn(f"HTTP {status} (installationID {installation_id!r}, "
              f"device {device_id!r})")
        return False
    _note_success()
    return True


async def push(lines, *, device_id, api_token, installation_id=INSTALLATION_ID,
               renderer=None, poster=None) -> bool:
    if not any(lines):
        return False
    render = renderer or haiku_render.render
    try:
        webp = render([str(x) for x in lines][:3])
    except Exception as e:
        _warn(f"render: {e}")
        return False
    return await push_image(webp, device_id=device_id, api_token=api_token,
                            installation_id=installation_id, poster=poster)
