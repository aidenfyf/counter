#!/usr/bin/env python3
"""
Bake the phone widget's card background and embed it in the widget script.

Layer 3 (execution). Writes out/card-bg.png and injects it as base64 into
widgets/ClaudeCounter.js between its TEXTURE_B64 markers.

WHY THIS EXISTS AT ALL

SwiftUI's LinearGradient does not dither. A ramp subtle enough to read as a lit
surface spans only ~26 of 256 levels, so across a widget each level occupies a
band tens of pixels wide, plainly visible as stripes. Raising the contrast makes
the bands brighter, not fewer; lowering it removes the lighting. No value fixes
it, so the ramp is baked here with dithering instead.

WHY IT IS APPLIED TO THE WIDGET, NOT TO EACH TILE

  widget.backgroundImage (ListWidget)  - renders inside a widget extension
  stack.backgroundImage  (WidgetStack) - does NOT; paints blank white over the
                                         text, while looking correct in-app

So this image carries no features, just a ramp. Nothing to line up with, so it
cannot fall out of register on a different widget size. The tiles are drawn on
top as semi-transparent fills and let the ramp show through, which is where their
texture and their lighting come from.

WHY ORDERED DITHERING RATHER THAN NOISE

Random noise is incompressible by definition, so a native-resolution background
came to ~647 KB - far too much to embed, which forced generating it small and
letting iOS upscale it. That upscaling is what made the grain look coarse and
stretched. An ordered (Bayer) dither is periodic, so PNG's deflate matches it
across the image: the same picture costs ~35 KB, a 19x saving. That pays for
generating at native resolution, where nothing is stretched and the dither sits
at one pixel.
"""

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
JS = ROOT / "widgets" / "ClaudeCounter.js"

START = "// TEXTURE_B64_START"
END = "// TEXTURE_B64_END"

# Widget ground colour, matching BG in ClaudeCounter.js
BASE = (0x0a, 0x0a, 0x0d)

# A large widget at @3x. Slightly different on other devices, but only by a few
# percent, so the dither stays sub-pixel rather than becoming visible grain.
WIDTH, HEIGHT = 1092, 1146

TOP_LIFT = 26.0     # luminance added at the top-left corner, fading to 0
DITHER = 2.2        # dither amplitude in levels; 2.2 measured a 2px flat run

BAYER8 = [
    [0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21],
]


def bake():
    import numpy as np
    from PIL import Image

    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
    # long diagonal, weighted toward vertical: the ramp is spread over the
    # greatest distance available, which is half of not banding
    t = (xs * 0.55 + ys) / float(WIDTH * 0.55 + HEIGHT)
    lift = TOP_LIFT * (1.0 - t)

    bayer = np.array(BAYER8, dtype=float)
    tiled = np.tile(bayer, (HEIGHT // 8 + 1, WIDTH // 8 + 1))[:HEIGHT, :WIDTH]
    dither = ((tiled + 0.5) / 64.0 - 0.5) * DITHER

    out = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for i, c in enumerate(BASE):
        out[..., i] = np.clip(c + lift + dither, 0, 255).astype(np.uint8)

    img = Image.fromarray(out, "RGB")
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / "card-bg.png"
    img.save(dest, optimize=True)
    return dest, img


def longest_flat_run(img):
    """Bands show up as long runs of a single value along a scanline."""
    import numpy as np
    row = np.array(img.convert("L"), dtype=float)[HEIGHT // 2]
    runs, cur = [], 1
    for i in range(1, len(row)):
        if row[i] == row[i - 1]:
            cur += 1
        else:
            runs.append(cur)
            cur = 1
    runs.append(cur)
    return max(runs)


def inject(b64):
    s = JS.read_text()
    if START not in s or END not in s:
        print("  warn: markers missing in ClaudeCounter.js, not injecting")
        return
    head, tail = s.split(START)[0], s.split(END)[1]
    JS.write_text(f'{head}{START}\nconst TEX_B64 =\n  "{b64}";\n{END}{tail}')
    print(f"  injected {len(b64) / 1024:.0f} KB of base64 into ClaudeCounter.js")


def main():
    try:
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        sys.exit("textures.py needs pillow and numpy:  pip3 install pillow numpy")

    dest, img = bake()
    run = longest_flat_run(img)
    print(f"  card-bg.png: {WIDTH}x{HEIGHT} native  "
          f"{dest.stat().st_size / 1024:.0f} KB  longest flat run {run}px")
    if run > 12:
        print(f"  WARNING: {run}px runs will read as banding - raise DITHER")
    inject(base64.b64encode(dest.read_bytes()).decode())


if __name__ == "__main__":
    main()
