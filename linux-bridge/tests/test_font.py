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
