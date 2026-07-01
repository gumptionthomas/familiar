"""Render a buddy species' ASCII pose animations to Tidbyt WebPs.

Pulls poses with extract_buddies, lays each 5-row pose out in pixlet's
monospace `tom-thumb` font in the species body color, overlays a generic
per-state particle, and renders one animated 64x32 WebP per persona state to
`src/claude_buddy/tidbyt_buddy/<species>/<state>.webp`.

    uv run --with pillow python tools/render_ascii_pet.py src/buddies/capybara.cpp

Requires `pixlet` on PATH (or ~/.local/bin/pixlet).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import extract_buddies  # noqa: E402

PIXLET = shutil.which("pixlet") or os.path.expanduser("~/.local/bin/pixlet")
OUT_ROOT = os.path.join(os.path.dirname(__file__), os.pardir,
                        "linux-bridge", "src", "claude_buddy", "tidbyt_buddy")

POSE_X, POSE_Y = 8, 1          # center ~48x30 art on the 64x32 panel
FIRMWARE_TICK_MS = 200         # the M5 ticks the pet every 200ms (main.cpp)
MAX_ANIM_MS = 14500            # pixlet/Tidbyt hard-cap animations at 15s


_CONFETTI = ["#ffd000", "#ff5fa2", "#33ddff", "#ffffff", "#6bd968", "#ff8a3d"]


def _particle(state, i, n):
    """Tidbyt-native particles for frame i: list of (char, x, y, color).

    Designed for the 64x32 panel rather than ported from the M5 firmware.
    """
    if state == "busy":
        # A loading ellipsis breathing down the right edge (0->3 and back),
        # clear of the busy face animation in the center.
        count = [0, 1, 2, 3, 3, 2, 1][i % 7]
        ys = [2, 9, 16]
        return [(".", 59, ys[k], "#5bc8ff") for k in range(count)]
    if state == "sleep":
        # Zzz drifting up and to the right above the head.
        out = []
        for k in range(3):
            ph = (i - k * 4) % 12
            if ph < 7:
                out.append(("Z" if k == 2 else "z", 40 + ph, 8 - ph, "#8899bb"))
        return out
    if state == "heart":
        # A couple of little hearts rising and fading above the head.
        out = []
        for k in range(2):
            ph = (i + k * 6) % 12
            if ph < 8:
                out.append(("<3", 26 + k * 10, 8 - ph, "#ff5fa2"))
        return out
    # attention: no particle — the pulsing amber border (see _border) is the
    # "needs you" signal, and reads better from across the room than a small !.
    if state == "celebrate":
        # A full-width confetti rain: several colored bits falling at staggered
        # phases, multiple visible per frame.
        out = []
        for k in range(6):
            x = 3 + k * 10
            y = (i * 3 + k * 5) % 22
            ch = "*" if (i + k) % 2 else "."
            out.append((ch, x, y, _CONFETTI[k % len(_CONFETTI)]))
        return out
    # idle: no particle — the blink/chew/look-around poses carry the liveness.
    return []


def _border(state, i, n):
    """A pulsing amber panel frame for 'needs you' (attention), else None.

    The amber breathes bright<->dim over an 8-frame cycle so it reads as a
    deliberate pulse from across the room.
    """
    if state != "attention":
        return None
    phase = i % 8
    tri = phase if phase <= 4 else 8 - phase        # 0..4..0
    f = 0.2 + 0.8 * (tri / 4)                        # dim glow -> bright -> dim
    return "#%02x%02x00" % (round(255 * f), round(176 * f))


def _frame_star(rows, particles, color, border=None):
    pose = "render.Padding(pad=(%d, %d, 0, 0), child=render.Column(children=[%s]))" % (
        POSE_X, POSE_Y,
        ", ".join('render.Text(content=%s, font="tom-thumb", color=%s)'
                  % (json.dumps(r), json.dumps(color)) for r in rows))
    children = [pose]
    for ch, x, y, pcol in particles:
        children.append(
            'render.Padding(pad=(%d, %d, 0, 0), child=render.Text(content=%s, '
            'font="tom-thumb", color=%s))' % (x, y, json.dumps(ch), json.dumps(pcol)))
    if border:
        # A 2px frame as four edge boxes on top, leaving the center transparent.
        c = json.dumps(border)
        children += [
            'render.Box(width=64, height=2, color=%s)' % c,                         # top
            'render.Box(width=2, height=32, color=%s)' % c,                         # left
            'render.Padding(pad=(0, 30, 0, 0), child=render.Box(width=64, height=2, color=%s))' % c,   # bottom
            'render.Padding(pad=(62, 0, 0, 0), child=render.Box(width=2, height=32, color=%s))' % c,   # right
        ]
    return "render.Stack(children=[%s])" % ", ".join(children)


def _star(state, data):
    frames = data["frames"]
    color = data["color"]
    body = ",\n        ".join(
        _frame_star(frames[i], _particle(state, i, len(frames)), color,
                    _border(state, i, len(frames)))
        for i in range(len(frames)))
    # Match the M5 (each pose holds `divisor` ticks of 200ms), but speed up just
    # enough to keep every pose under the 15s animation cap.
    n = len(frames)
    delay = min(data.get("divisor", 5) * FIRMWARE_TICK_MS, MAX_ANIM_MS // max(n, 1))
    return (
        'load("render.star", "render")\n'
        "def main(config):\n"
        "    return render.Root(delay=%d, child=render.Animation(children=[\n"
        "        %s,\n"
        "    ]))\n" % (delay, body))


def render_species(cpp_path):
    name = os.path.splitext(os.path.basename(cpp_path))[0]
    states = extract_buddies.extract(cpp_path)
    out_dir = os.path.join(OUT_ROOT, name)
    os.makedirs(out_dir, exist_ok=True)
    for state, data in states.items():
        if not data["frames"]:
            continue
        # pixlet treats the .star's directory as the app bundle and globs
        # sibling *.star files, so give each render its own clean dir.
        tmp = tempfile.mkdtemp()
        star_path = os.path.join(tmp, "app.star")
        with open(star_path, "w") as f:
            f.write(_star(state, data))
        out = os.path.join(out_dir, state + ".webp")
        try:
            subprocess.run([PIXLET, "render", star_path, "-o", out],
                           check=True, capture_output=True, text=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        print("%-10s %2d frames -> %s" % (state, len(data["frames"]), out))
    return out_dir


if __name__ == "__main__":
    for cpp in sys.argv[1:]:
        render_species(cpp)
