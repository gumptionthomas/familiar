from PIL import Image, ImageDraw
from familiar import font


def test_dimensions():
    assert font.CHAR_W == 4 and font.CHAR_H == 6


def test_known_glyph_loaded():
    # 'A' must exist and have some set pixels
    assert "A" in font.GLYPHS
    assert any(any(row) for row in font.GLYPHS["A"])


def test_text_width_is_monospace():
    assert font.text_width("abc") == 3 * font.CHAR_W


def test_draw_text_sets_pixels():
    im = Image.new("RGB", (32, 8), (0, 0, 0))
    d = ImageDraw.Draw(im)
    font.draw_text(d, (0, 0), "A", (255, 255, 255))
    colors = {c for _, c in im.getcolors()}
    assert (0, 0, 0) in colors          # background present (not entirely white)
    assert (255, 255, 255) in colors    # glyph pixels drawn


def test_known_glyphs_pin_bitmaps():
    # Pin exact bitmaps so a regression in _normalize's baseline math is caught.
    assert font.GLYPHS["A"] == [[0, 1, 0, 0], [1, 0, 1, 0], [1, 1, 1, 0],
                                [1, 0, 1, 0], [1, 0, 1, 0], [0, 0, 0, 0]]
    # 'g' has a descender: ink on the bottom rows, below the baseline.
    assert font.GLYPHS["g"] == [[0, 0, 0, 0], [0, 1, 1, 0], [1, 0, 1, 0],
                                [1, 1, 1, 0], [0, 0, 1, 0], [0, 1, 0, 0]]
