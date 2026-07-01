"""Push the buddy pet + haiku to a Tidbyt 64x32 over the HTTP API.

Best-effort: any failure (no config, network, non-200) is swallowed so it never
disturbs the M5 path. `poster` is injectable for tests.
"""
import asyncio
import base64
import json
import urllib.request

PUSH_URL = "https://api.tidbyt.com/v0/devices/%s/push"


def _post(url, data, headers) -> int:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


async def push_image(webp_bytes, *, device_id, api_token,
                     installation_id="claudebuddy", poster=None) -> bool:
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
        status = await asyncio.get_event_loop().run_in_executor(
            None, post, url, body, headers)
        return status == 200
    except Exception:
        return False


async def push(lines, *, device_id, api_token, app_path,
               installation_id="claudebuddy"):
    # replaced in Task 5
    return False
