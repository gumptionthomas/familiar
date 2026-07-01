"""Tom Thumb 4x6 bitmap font, parsed from the vendored BDF, for the haiku render.

GLYPHS[char] is a list of CHAR_H rows, each a list of CHAR_W ints (1 = ink).
"""
import os

CHAR_W, CHAR_H = 4, 6
_BDF = os.path.join(os.path.dirname(__file__), "fonts", "tom-thumb.bdf")


def _parse_bdf(path):
    glyphs, cur, code, bbx = {}, None, None, None
    reading = False
    for line in open(path):
        p = line.split()
        if not p:
            continue
        if p[0] == "ENCODING":
            code = int(p[1])
        elif p[0] == "BBX":
            bbx = tuple(int(x) for x in p[1:5])          # w, h, xoff, yoff
        elif p[0] == "BITMAP":
            reading, cur = True, []
        elif p[0] == "ENDCHAR":
            reading = False
            if code is not None and 32 <= code < 127:
                glyphs[chr(code)] = _normalize(cur, bbx)
            cur = code = bbx = None
        elif reading:
            cur.append(int(p[0], 16))
    return glyphs


def _normalize(rows, bbx):
    # BDF rows are MSB-left hex; place into a CHAR_W x CHAR_H cell using the
    # glyph bbx offsets so every glyph shares one baseline/grid.
    w, h, xoff, yoff = bbx
    cell = [[0] * CHAR_W for _ in range(CHAR_H)]
    top = CHAR_H - h - (1 + yoff)                        # tom-thumb descent = 1
    for r, val in enumerate(rows):
        y = top + r
        if not (0 <= y < CHAR_H):
            continue
        for c in range(w):
            bit = (val >> (8 * ((w + 7) // 8) - 1 - c)) & 1
            x = xoff + c
            if 0 <= x < CHAR_W and bit:
                cell[y][x] = 1
    return cell


GLYPHS = _parse_bdf(_BDF)
_BLANK = [[0] * CHAR_W for _ in range(CHAR_H)]


def text_width(s: str) -> int:
    return len(s) * CHAR_W


def draw_text(draw, xy, s, fill):
    x0, y0 = xy
    for i, ch in enumerate(s):
        cell = GLYPHS.get(ch, _BLANK)
        for ry, row in enumerate(cell):
            for rx, on in enumerate(row):
                if on:
                    draw.point((x0 + i * CHAR_W + rx, y0 + ry), fill=fill)
