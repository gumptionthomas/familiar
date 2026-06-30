# Renders the buddy's haiku on a Tidbyt (64x32). Three lines passed as config
# vars l1/l2/l3 (already ASCII-folded by the bridge). tom-thumb is the smallest
# built-in font (~4x6), so a haiku line fits in ~15 chars and wraps if longer.
load("render.star", "render")

def main(config):
    lines = [config.str("l1", ""), config.str("l2", ""), config.str("l3", "")]
    children = [
        render.WrappedText(
            content = t,
            font = "tom-thumb",
            color = "#7cf",
            align = "center",
        )
        for t in lines
        if t
    ]
    if not children:
        children = [render.Text(content = "buddy", font = "tom-thumb", color = "#445")]

    return render.Root(
        child = render.Box(
            padding = 1,
            child = render.Column(
                expanded = True,
                main_align = "space_evenly",
                cross_align = "center",
                children = children,
            ),
        ),
    )
