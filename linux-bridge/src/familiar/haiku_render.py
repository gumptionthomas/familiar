"""Render a haiku to a scrolling 64x32 WebP (replaces the Pixlet .star)."""
import base64
import io

from PIL import Image, ImageDraw

from . import font

W, H = 64, 32
FRAME_MS = 180          # matches the old Root(delay=180)
TOP_HOLD_FRAMES = 14    # ~2.5s hold at the top (old Marquee delay=14)
MAX_MS = 14500          # under the 15s device cap
GAP = 2                 # blank rows between lines

# 7x7 Claude coral sunburst (same asset the old .star used).
BADGE = Image.open(io.BytesIO(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAcAAAAHCAYAAADEUlfTAAAANUlEQVR4nGO4WR7+n"
    "wEKYGxkMRRBZAkmBgYGBvXOlYwoqqEAqyBcEsMOqEkM6BJYHYQuCQMA5wwlJ5fGpP"
    "oAAAAASUVORK5CYII="))).convert("RGBA")


# Fold typographic punctuation the tom-thumb (ASCII-only) glyphs can't draw.
_SUBS = {
    "\u2014": "-", "\u2013": "-", "\u2012": "-",   # em / en / figure dash
    "\u2018": "'", "\u2019": "'",                   # curly single quotes
    "\u201c": '"', "\u201d": '"',                   # curly double quotes
    "\u2026": "...",                                # ellipsis
    "\u00a0": " ",                                  # non-breaking space
}


def _ascii(s):
    for k, v in _SUBS.items():
        s = s.replace(k, v)
    return s.encode("ascii", "ignore").decode("ascii")


def _wrap(s, max_chars):
    words, lines, cur = s.split(), [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if font.text_width(cand) > max_chars * font.CHAR_W and cur:
            lines.append(cur); cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines or [""]


def _strip(lines):
    """Tall RGB image: centered badge, then each wrapped line centered."""
    max_chars = W // font.CHAR_W
    rendered = [row for ln in lines if ln for row in _wrap(ln, max_chars)]
    height = 7 + GAP + sum(font.CHAR_H + GAP for _ in rendered) + H  # trailing pad = one screen
    img = Image.new("RGB", (W, max(height, H)), (0, 0, 0))
    d = ImageDraw.Draw(img)
    img.paste(BADGE, ((W - 7) // 2, 0), BADGE)
    y = 7 + GAP
    for row in rendered:
        x = (W - font.text_width(row)) // 2
        font.draw_text(d, (x, y), row, (255, 255, 255))
        y += font.CHAR_H + GAP
    return img


def render(lines) -> bytes:
    strip = _strip([_ascii(str(x)) for x in lines])
    travel = max(0, strip.height - H)                  # px to scroll
    steps = [0] * TOP_HOLD_FRAMES + list(range(1, travel + 1))
    if len(steps) * FRAME_MS > MAX_MS:                 # fit the device cap
        steps = steps[:max(1, MAX_MS // FRAME_MS)]
    frames = [strip.crop((0, off, W, off + H)) for off in steps]
    buf = io.BytesIO()
    frames[0].save(buf, format="WEBP", save_all=True, append_images=frames[1:],
                   duration=FRAME_MS, loop=0, lossless=True)
    return buf.getvalue()
