import io
import struct
from PIL import Image
from familiar import haiku_render


def _durations(b):
    durs, i = [], 12
    while i < len(b) - 8:
        tag = b[i:i+4]; size = struct.unpack("<I", b[i+4:i+8])[0]
        if tag == b"ANMF":
            p = b[i+8:]; durs.append(p[12] | (p[13] << 8) | (p[14] << 16))
        i += 8 + size + (size & 1)
    return durs


def test_render_returns_64x32_animation():
    out = haiku_render.render(["morning tokens flow",
                               "a capybara dreams in brown",
                               "the cursor blinks on"])
    im = Image.open(io.BytesIO(out))
    assert im.size == (64, 32)
    assert im.n_frames > 1


def test_render_under_15s():
    out = haiku_render.render(["one two three four five",
                               "six seven eight nine ten now",
                               "eleven twelve done"])
    assert sum(_durations(out)) <= 15000


def test_render_empty_lines_ok():
    out = haiku_render.render(["", "", ""])
    assert Image.open(io.BytesIO(out)).size == (64, 32)


def test_ascii_folds_typographic_punctuation():
    from familiar.haiku_render import _ascii
    assert _ascii("it’s “hi” — done…") == 'it\'s "hi" - done...'
