"""Draw the animated titles and divider for my About section.

Run from the repo root:
    python3 tools/about/build.py          writes about/*.gif
    python3 tools/about/build.py check    also decodes every GIF and compares it with what was drawn

Needs Pillow, NumPy and SciPy (the check also uses ffmpeg when it is installed).
Everything is drawn with code. The font is Fredoka Bold (SIL Open Font License, see fonts/OFL.txt).
No AI image tools, no anime artwork, and nothing here talks to AniList.

To change a title: edit TITLES below and run it again.
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from PIL import Image, ImageSequence

import neon

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FONT = HERE + "/fonts/Fredoka-Bold.ttf"
OUT = REPO + "/about"
W, H, SIZE, TRACK, FRAMES, DUR = 640, 130, 68, 5.0, 40, 70

TITLES = [
    # file name, text, seed, flickering letter (index among the letters), glitch frames
    ("title_status", "STATUS", 11, 3, (21, 22, 34)),
    ("title_top_isekai", "TOP ISEKAI", 3, 4, (22, 23, 33)),
    ("title_now_playing", "NOW PLAYING", 5, 6, (20, 21, 35)),
    ("title_visitors", "VISITORS", 8, 2, (23, 24, 32)),
]


def pieces():
    """Yield (name, frames, opaque-area mask) for every animated piece."""
    for name, text, seed, fl, gl in TITLES:
        t = neon.Title(text, FONT, W, H, size=SIZE, tracking=TRACK, seed=seed,
                       flicker_letter=fl, glitch_at=gl, frames=FRAMES)
        yield name, [t.frame(i) for i in range(t.N)], t.alpha
    d = neon.Divider()
    yield "divider", [d.frame(i) for i in range(d.N)], d.alpha


def check(path, want, alpha):
    """Decode the GIF and compare it with the frames that were drawn."""
    im = Image.open(path)
    got = [np.asarray(f.convert("RGBA")) for f in ImageSequence.Iterator(im)]
    ok = len(got) == len(want)
    worst = 0
    for g, w in zip(got, want):
        ok &= bool((g[..., 3][alpha] == 255).all() and (g[..., 3][~alpha] == 0).all())
        worst = max(worst, int(np.abs(g[..., :3].astype(int) - w.astype(int))[alpha].max()))
    ring = alpha.copy()  # the outermost 2 pixels of the plate must be exactly the plate colour
    ring[2:-2, 2:-2] = False
    ok &= all((g[..., :3][ring] == tuple(int(v) for v in neon.BG)).all() for g in got)
    ok &= worst <= 64
    second = "skipped (no ffmpeg)"
    if shutil.which("ffmpeg"):
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-fps_mode", "passthrough",
                            "-pix_fmt", "rgba", td + "/f%03d.png"], check=True)
            ff = [np.asarray(Image.open(p).convert("RGBA")) for p in sorted(glob.glob(td + "/f*.png"))]
        same = len(ff) == len(got) and all((a[..., :3][alpha] == b[..., :3][alpha]).all() for a, b in zip(ff, got))
        ok &= same
        second = "ok" if same else "MISMATCH"
    print(f"  check: {len(got)} frames, worst colour error {worst}, second decoder {second} -> {'ok' if ok else 'PROBLEM'}")
    return ok


def main():
    do_check = len(sys.argv) > 1 and sys.argv[1] == "check"
    os.makedirs(OUT, exist_ok=True)
    good = True
    for name, frames, alpha in pieces():
        path = f"{OUT}/{name}.gif"
        neon.save_gif(frames, alpha, path, DUR)
        print(f"{name}.gif  {os.path.getsize(path) // 1024} KB")
        if do_check:
            good &= check(path, frames, alpha)
    if do_check:
        print("ALL OK" if good else "PROBLEMS FOUND")
        sys.exit(0 if good else 1)


if __name__ == "__main__":
    main()
