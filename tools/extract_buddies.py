"""Extract ASCII pose animations from a buddy species .cpp into JSON.

For each persona state (idle/busy/attention/celebrate) pulls the 5-row pose
arrays, the P[] order, the SEQ[] beats, and the body color, then expands the
SEQ into a flat frame list. Particles/offsets are intentionally ignored (the
Tidbyt renderer adds generic per-state particles).

    uv run python tools/extract_buddies.py src/buddies/capybara.cpp
"""
import json
import re
import sys

STATES = ["Idle", "Busy", "Attention", "Celebrate"]


def _unescape(s):
    return s.encode("utf-8").decode("unicode_escape")


def _state_body(src, state):
    m = re.search(r"void do%s\(uint32_t t\)\s*\{" % state, src)
    if not m:
        return None
    i = m.end()
    depth = 1
    quote = ""          # skip braces inside string/char literals (pose art has { })
    while i < len(src) and depth:
        c = src[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = ""
        elif c in '"\'':
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[m.end():i - 1]


def _poses(body):
    out = {}
    for m in re.finditer(r"const char\*\s*const\s+(\w+)\[5\]\s*=\s*\{(.*?)\};",
                         body, re.S):
        name = m.group(1)
        strs = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(2))
        out[name] = [_unescape(s) for s in strs[:5]]
    return out


def extract(path):
    src = open(path).read()
    result = {}
    for st in STATES:
        body = _state_body(src, st)
        if not body:
            continue
        poses = _poses(body)
        pm = re.search(r"(?<![A-Za-z0-9_])P\[\d+\]\s*=\s*\{(.*?)\};", body, re.S)
        order = [x.strip() for x in pm.group(1).split(",") if x.strip()] if pm else list(poses)
        sm = re.search(r"SEQ\[\]\s*=\s*\{(.*?)\};", body, re.S)
        seq = [int(x) for x in re.findall(r"\d+", sm.group(1))] if sm else list(range(len(order)))
        cm = re.search(r"buddyPrintSprite\([^;]*?0x([0-9A-Fa-f]{4})", body)
        rgb565 = int(cm.group(1), 16) if cm else 0xFFFF
        r = ((rgb565 >> 11) & 0x1F) << 3
        g = ((rgb565 >> 5) & 0x3F) << 2
        b = (rgb565 & 0x1F) << 3
        # `beat = (t / N)` -> each pose holds N firmware ticks; the renderer
        # turns this into the per-frame delay so the Tidbyt matches the M5.
        dm = re.search(r"beat\s*=\s*\(t\s*/\s*(\d+)\)", body)
        divisor = int(dm.group(1)) if dm else 5
        frames = [poses[order[i]] for i in seq if i < len(order) and order[i] in poses]
        result[st.lower()] = {"color": "#%02x%02x%02x" % (r, g, b),
                              "frames": frames, "divisor": divisor}
    return result


if __name__ == "__main__":
    data = extract(sys.argv[1])
    for st, d in data.items():
        print(f"{st}: color={d['color']}  {len(d['frames'])} frames")
    # show idle frame 0 to eyeball
    print("\n--- idle frame 0 ---")
    for row in data["idle"]["frames"][0]:
        print(repr(row))
