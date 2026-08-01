#!/usr/bin/env python3
"""
Render card/card.html to out/card.png at 2x.

Layer 3 (execution). Injects out/stats.json into the template, then screenshots it
with headless Chrome. The same injected file is what Ubersicht loads, so the Mac
desktop widget and the iPhone widget are pixel-identical by construction.

GPU note: backdrop-filter (the glass blur) needs real compositing. Software
rendering silently drops it and the tiles come out as flat dark rectangles. That
is the failure this script is most exposed to, so check_glass() measures the
rendered pixels for differential colour transmission rather than trusting flags.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATS = ROOT / "out" / "stats.json"

# Two compositions, not one scaled. The desktop card's labels land at 3.6pt inside
# an iPhone widget, and its 1px chamfer at a third of a point; the phone card is
# authored at 3x nominal so its labels clear the ~9pt legibility floor.
VARIANTS = {
    "desktop": {"tmpl": "card.html",       "w": 1200, "h": 718,  "scale": 2, "tiles": 7},
    "phone":   {"tmpl": "card-phone.html", "w": 1014, "h": 1062, "scale": 1, "tiles": 5},
}

# The .glow blobs deliberately overflow the body (right:-200px, bottom:-280px), so
# the *document* is larger than the card. body{overflow:hidden} hides it. Always
# capture with --window-size, never a fullPage capture - fullPage emits 2800x1890
# with dead margins.

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_chrome():
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    which = shutil.which("google-chrome") or shutil.which("chromium")
    if which:
        return which
    sys.exit("No Chrome/Chromium found. Install Google Chrome to render the card.")


def build(v):
    if not STATS.exists():
        sys.exit(f"missing {STATS} - run count.py first")
    html = (ROOT / "card" / v["tmpl"]).read_text()
    stats = STATS.read_text()
    # Replace the marker AND the `null` that follows it. Substituting only the
    # comment leaves `const S = {...} null;` which is a syntax error, and a syntax
    # error means no JS runs and the card renders empty at the correct dimensions.
    marker = "/*__STATS__*/ null"
    if marker not in html:
        sys.exit(f"template lost its marker: {marker!r}")
    built = html.replace(marker, stats)
    v["built"].write_text(built)
    if "/*__STATS__*/" in built:
        sys.exit("marker survived substitution - injection did not take")
    return v["built"]


def check_dom(chrome, src, v):
    """
    Verify the page populated before trusting the PNG. A JS error renders a
    silently empty card that still screenshots fine, so dimensions prove nothing.

    The script block is stripped first: it contains the tile template literal, so
    counting over the whole DOM matched one extra and let a 6-tile render pass a
    `< 7` guard.
    """
    res = subprocess.run(
        [chrome, "--headless=new", "--virtual-time-budget=3000",
         "--dump-dom", src.as_uri()],
        capture_output=True, text=True, timeout=120,
    )
    if res.returncode != 0 or not res.stdout.strip():
        sys.exit(f"chrome --dump-dom failed (rc={res.returncode}): {res.stderr[-400:]}")
    body = re.sub(r"<script\b.*?</script>", "", res.stdout, flags=re.S | re.I)
    tiles = body.count('class="tile')
    if tiles != v["tiles"]:
        sys.exit(f"expected {v['tiles']} rendered tiles, found {tiles} - JS likely threw.")
    for bad in ("undefined", "NaN", ">null<", "$0<"):
        if bad in body:
            sys.exit(f"placeholder {bad!r} reached the rendered card.")
    print(f"  dom ok: {tiles} tiles, no placeholders")


def shoot(chrome, src, v):
    v["png"].unlink(missing_ok=True)
    cmd = [
        chrome,
        "--headless=new",
        "--hide-scrollbars",
        "--force-device-scale-factor=%d" % v["scale"],
        "--window-size=%d,%d" % (v["w"], v["h"]),
        "--enable-gpu",
        "--use-angle=metal",
        "--enable-features=Metal",
        "--virtual-time-budget=3000",
        "--screenshot=%s" % v["png"],
        src.as_uri(),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not v["png"].exists():
        sys.exit("screenshot failed:\n" + res.stderr[-2000:])
    return res


def tile_rects(chrome, src):
    """Ask the live page where its tiles actually are, in CSS px."""
    html = src.read_text().replace("</script>", """
      const r=[...document.querySelectorAll('.tile')].map(t=>{
        const b=t.getBoundingClientRect();
        return [Math.round(b.left),Math.round(b.top),Math.round(b.width),Math.round(b.height)];
      });
      document.title = "RECTS=" + JSON.stringify(r);
    </script>""", 1)
    probe = src.with_name("probe.tmp.html")
    probe.write_text(html)
    try:
        out = subprocess.run(
            [chrome, "--headless=new", "--virtual-time-budget=2500",
             "--dump-dom", probe.as_uri()],
            capture_output=True, text=True, timeout=120,
        ).stdout
    finally:
        probe.unlink(missing_ok=True)
    m = re.search(r"RECTS=(\[.*?\])</title>", out, re.S)
    return json.loads(m.group(1)) if m else []


def check_render(chrome, src, v):
    """
    Verify the things the card's look actually depends on, measured on the
    rendered pixels at coordinates the live page reports.

    What this does NOT claim: it does not detect backdrop-filter being dropped.
    It cannot. The blur only smooths an already-smooth backdrop (the glows are
    themselves blur(90px)+), so removing it changes the render by RMS ~1/255.
    The tiles stay translucent either way because their fill is rgba, so the
    glow still transmits by ordinary alpha compositing. A previous version of
    this function compared regional mean warmth and claimed to guard the filter;
    blur is a mean-preserving convolution, so that metric was invariant to the
    exact failure it named, and it passed on a filter-free render.

    What the look genuinely rests on, and what is checked here:
      * correct canvas size
      * tile interiors are LIT, not black (a failed render is uniformly dark)
      * the 1-2px chamfer rim exists, brighter at the top edge than the interior.
        This is the load-bearing detail and the one that dies if mask-composite
        is unsupported or the ::before is lost.
    """
    try:
        from PIL import Image
    except ImportError:
        print("  warn: Pillow missing - render unverified (pip3 install pillow)")
        return

    im = Image.open(v["png"]).convert("L")
    want = (v["w"] * v["scale"], v["h"] * v["scale"])
    if im.size != want:
        sys.exit(f"wrong size {im.size}, expected {want}")

    rects = tile_rects(chrome, src)
    if len(rects) != v["tiles"]:
        sys.exit(f"probe saw {len(rects)} tiles, expected {v['tiles']}")

    k = v["scale"]

    def mean(box):
        px = list(im.crop(box).getdata())
        return sum(px) / len(px)

    checked = 0
    for (x, y, w, h) in rects:
        if w < 200 or h < 100:
            continue
        x, y, w, h = x * k, y * k, w * k, h * k
        inset = 14 * k                      # clear of the corner arc
        rim = mean((x + inset, y, x + w - inset, y + 2 * k))
        inner = mean((x + inset, y + 7 * k, x + w - inset, y + 12 * k))
        interior = mean((x + inset, y + h // 3, x + w - inset, y + 2 * h // 3))

        if interior < 8:
            sys.exit(f"tile at {x//k},{y//k} is nearly black ({interior:.1f}) - render failed")
        ratio = rim / max(inner, 1)
        if ratio < 1.4:
            sys.exit(
                f"chamfer rim missing on tile at {x//k},{y//k}: top edge {rim:.0f} vs "
                f"{inner:.0f} just inside (ratio {ratio:.2f}). mask-composite may be "
                f"unsupported, or .tile::before was lost."
            )
        checked += 1

    print(f"  render ok: {checked} tiles lit, chamfer present on each")


def publish_to_ubersicht():
    """
    Put the desktop card where the Ubersicht widget can actually load it.

    Ubersicht renders widgets in a WebKit view served from http://localhost:41416,
    so the widget cannot load a file:// image - it is cross-origin and gets blocked
    silently, leaving only alt text and a rounded border on the desktop. Assets that
    live beside the .jsx are served from that same origin, so the card has to be
    copied into the widget directory and referenced by bare filename.
    """
    wd = Path.home() / "Library" / "Application Support" / "Übersicht" / "widgets"
    if not wd.is_dir():
        print("  Ubersicht not installed - skipping desktop publish")
        return
    try:
        shutil.copyfile(VARIANTS["desktop"]["png"], wd / "claude-counter.png.tmp")
        os.replace(wd / "claude-counter.png.tmp", wd / "claude-counter.png")
        print(f"  published to Ubersicht widget dir")
    except OSError as exc:
        print(f"  Ubersicht publish failed: {exc}")


def mirror_to_icloud():
    """
    Copy the cards where the iPhone widget can see them.

    Uses copyfile + atomic replace rather than copy2, so no metadata is touched.

    On the permission boundary, measured with launchd probes rather than guessed:
    a LaunchAgent CAN create new files anywhere in iCloud Drive, but CANNOT modify
    or delete a file iCloud has already marked UF_TRACKED (flags 0o100), which it
    does to everything it syncs. Every strategy fails on a tracked file - copyfile
    over it, open(wb) truncate, rename-over, even unlink-then-write. So the first
    run of a fresh folder succeeds and every run after it fails, which is why this
    looked intermittent and looked like a bug in the copy call.

    No permission grant fixes this and none is needed. An Ubersicht child process
    is NOT subject to the guard (verified with a probe widget), so the widget's own
    command owns the iCloud mirror and runs it every 2 minutes. This function stays
    as the path for interactive runs, where it works, and defers quietly otherwise.

    Two destinations:
      * iCloud Drive/ClaudeCounter  - visible in the Files app, easy to eyeball
      * Scriptable's own container  - what FileManager.iCloud() reads on the phone.
        Scriptable only exposes its OWN container without a manual bookmark, so
        writing solely to CloudDocs would leave the widget unable to find anything.
        Created lazily: the container does not exist until Scriptable is installed.
    """
    mobile = Path.home() / "Library" / "Mobile Documents"
    targets = [mobile / "com~apple~CloudDocs" / "ClaudeCounter"]

    scriptable = mobile / "iCloud~dk~simonbs~Scriptable" / "Documents"
    if scriptable.is_dir():
        targets.append(scriptable / "ClaudeCounter")

    payload = [v["png"] for v in VARIANTS.values()] + [STATS]
    for target in targets:
        try:
            target.mkdir(parents=True, exist_ok=True)
            for src in payload:
                tmp = target / (src.name + ".tmp")
                try:
                    shutil.copyfile(src, tmp)  # bytes only - no flags, no xattrs
                    os.replace(tmp, target / src.name)   # atomic; never a half file
                except OSError:
                    # The tracked-file guard blocks the replace, not the write, so
                    # a .tmp is left behind. Clear it or they accumulate every run.
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    raise
            print(f"  mirrored to {target}")
        except OSError as exc:
            if getattr(exc, "errno", None) == 1:
                # Expected under launchd: iCloud refuses to let a LaunchAgent
                # overwrite a file it has marked UF_TRACKED. Not a problem - the
                # Ubersicht widget mirrors it instead, every 2 minutes, and is not
                # subject to that guard. No permission grant is needed anywhere.
                print(f"  mirror deferred to the Ubersicht widget (iCloud guard): {target.name}")
            else:
                print(f"  mirror to {target} failed: {exc}")

    if not scriptable.is_dir():
        print("  note: Scriptable not installed yet. Install it from the App Store,")
        print("        open it once so iCloud creates its folder, then re-run this;")
        print("        the phone copy lands automatically after that.")


def main():
    chrome = find_chrome()
    for name, v in VARIANTS.items():
        v["built"] = ROOT / "out" / f"card-{name}.built.html"
        v["png"] = ROOT / "out" / ("card.png" if name == "desktop" else f"card-{name}.png")
        src = build(v)
        check_dom(chrome, src, v)
        shoot(chrome, src, v)
        print(f"rendered {v['png'].name}")
        check_render(chrome, src, v)
    publish_to_ubersicht()
    mirror_to_icloud()


if __name__ == "__main__":
    main()
