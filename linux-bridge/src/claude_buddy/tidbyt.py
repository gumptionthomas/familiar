"""Mirror the buddy's haiku to a Tidbyt 64x32 display via pixlet.

Best-effort: any failure (no config, pixlet missing, render/push error, no
network) is swallowed so it never disturbs the M5 path. `runner` is injectable
for tests so nothing actually shells out.
"""
import asyncio
import os
import tempfile

# The Tidbyt's tom-thumb font is ASCII; fold typographic punctuation like the
# M5 path does.
_SUBS = {
    "—": "-", "–": "-", "‒": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", " ": " ",
}


def _ascii(s: str) -> str:
    for k, v in _SUBS.items():
        s = s.replace(k, v)
    return s.encode("ascii", "ignore").decode("ascii")


def render_args(app_path, lines, out, pixlet="pixlet"):
    ln = [_ascii(str(x)) for x in lines][:3]
    ln += [""] * (3 - len(ln))
    return [pixlet, "render", app_path,
            "l1=" + ln[0], "l2=" + ln[1], "l3=" + ln[2], "-o", out]


def push_args(device_id, out, api_token, installation_id, pixlet="pixlet"):
    return [pixlet, "push", device_id, out,
            "-t", api_token, "-i", installation_id]


async def _run(args):
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return await proc.wait()


async def push(lines, *, device_id, api_token, app_path,
               installation_id="claudebuddy", pixlet="pixlet", runner=None):
    if not (device_id and api_token and app_path and any(lines)):
        return False
    run = runner or _run
    out = os.path.join(tempfile.gettempdir(),
                       "claude-buddy-tidbyt-%d.webp" % os.getpid())
    try:
        if await run(render_args(app_path, lines, out, pixlet)) != 0:
            return False
        return await run(push_args(device_id, out, api_token,
                                   installation_id, pixlet)) == 0
    except Exception:
        return False
