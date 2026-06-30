"""Convert bufo state GIFs -> 64x32 animated WebPs for the Tidbyt buddy.

One-time/build helper. Run from the repo root:
    uv run --with pillow python tools/build_tidbyt_buddy.py
"""
import glob
import os
from PIL import Image

SRC = "characters/bufo"
DST = "linux-bridge/src/claude_buddy/tidbyt_buddy"
W, H = 64, 32
STATES = ["idle_0", "idle_1", "idle_2", "idle_3", "idle_4", "idle_5", "idle_6",
          "idle_7", "idle_8", "busy", "attention", "celebrate"]


def convert(gif_path, out_path):
    im = Image.open(gif_path)
    n = getattr(im, "n_frames", 1)
    sh = H
    sw = max(1, round(im.size[0] * H / im.size[1]))   # fit to height, keep ratio
    if sw > W:
        sw, sh = W, max(1, round(im.size[1] * W / im.size[0]))
    ox, oy = (W - sw) // 2, (H - sh) // 2
    frames, durations = [], []
    for i in range(n):
        im.seek(i)
        fr = im.convert("RGBA").resize((sw, sh), Image.LANCZOS)
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        canvas.paste(fr, (ox, oy), fr)
        frames.append(canvas.convert("RGB"))
        durations.append(im.info.get("duration", 100))
    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, format="WEBP", lossless=True)
    return n, (sw, sh)


def main():
    os.makedirs(DST, exist_ok=True)
    for st in STATES:
        gif = os.path.join(SRC, st + ".gif")
        if not os.path.exists(gif):
            print("skip (missing):", gif); continue
        out = os.path.join(DST, st + ".webp")
        n, size = convert(gif, out)
        print(f"{st:12} -> {os.path.basename(out)}  {size[0]}x{size[1]}  {n}f  "
              f"{os.path.getsize(out)//1024}KB")


if __name__ == "__main__":
    main()
