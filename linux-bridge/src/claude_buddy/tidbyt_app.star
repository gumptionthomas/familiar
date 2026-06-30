# Renders the buddy's haiku on a Tidbyt (64x32): a small Claude coral sunburst
# leads the haiku, then the three lines (full-width, centered) scroll vertically
# so a long one shows in full. Lines l1/l2/l3 are passed as config vars (already
# ASCII-folded by the bridge). tom-thumb is the smallest built-in font (~4x6).
load("render.star", "render")
load("encoding/base64.star", "base64")

# 7x7 Claude coral sunburst (generated, see commit message).
ICON = base64.decode("iVBORw0KGgoAAAANSUhEUgAAAAcAAAAHCAYAAADEUlfTAAAANUlEQVR4nGO4WR7+nwEKYGxkMRRBZAkmBgYGBvXOlYwoqqEAqyBcEsMOqEkM6BJYHYQuCQMA5wwlJ5fGpPoAAAAASUVORK5CYII=")

def main(config):
    lines = [config.str("l1", ""), config.str("l2", ""), config.str("l3", "")]

    # Small Claude badge leads the haiku, then each line full-width + centered.
    rows = [render.Row(
        main_align = "center",
        children = [render.Image(src = ICON, width = 7, height = 7)],
    )]
    for t in lines:
        if not t:
            continue
        rows.append(render.Box(width = 1, height = 2))
        rows.append(render.WrappedText(
            content = t,
            font = "tom-thumb",
            color = "#ffffff",
            align = "center",
            width = 64,
        ))
    if len(rows) == 1:
        rows.append(render.Text(content = "buddy", font = "tom-thumb", color = "#556"))

    return render.Root(
        delay = 180,  # ms/frame — slow, readable scroll
        child = render.Marquee(
            height = 32,
            scroll_direction = "vertical",
            offset_start = 0,    # start with the badge + first line at the top
            offset_end = 32,     # scroll fully off before looping -> no mid-snap
            delay = 14,          # hold ~2.5s at the top before scrolling
            child = render.Column(cross_align = "center", children = rows),
        ),
    )
