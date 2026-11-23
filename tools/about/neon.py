"""Slime + neon + glitch renderer for the Adamas002 About section.

Everything is drawn with code (Pillow + NumPy): text from an open-licence font,
drips, glow and glitch are plain maths. No AI image tools, no anime artwork.

Palette (Rimuru slime blues):
  shine  #F7FCFC   highlight #CCE9F6   body #93B9E8   accent #3A71A4
Plate colour = AniList dark-theme card (#151F2E), so the plates vanish on the dark theme.
"""
import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import gaussian_filter, distance_transform_edt
from scipy.spatial import cKDTree

SS = 2  # supersampling factor (render at 2x, then shrink)

BG = np.array([21, 31, 46], np.float32)
SHINE = np.array([247, 252, 252], np.float32)
HI = np.array([204, 233, 246], np.float32)
BODY = np.array([147, 185, 232], np.float32)
ACC = np.array([58, 113, 164], np.float32)
NEAR = np.array([94, 151, 208], np.float32)
CYAN = np.array([96, 226, 255], np.float32)
VIOLET = np.array([122, 132, 255], np.float32)
DIM = ACC * 0.55 + BG * 0.45  # an unlit tube


def smooth(a, lo, hi):
    t = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def gooey(mask, sigma, edge=0.10):
    """Blur then re-threshold: rounds corners and melts shapes together like slime."""
    return smooth(gaussian_filter(mask, sigma), 0.5 - edge, 0.5 + edge)


def plate_alpha(W, H, radius):
    """Opaque area of a rounded plate, at output size (bool array)."""
    img = Image.new("L", (W * 4, H * 4), 0)
    ImageDraw.Draw(img).rounded_rectangle([0, 0, W * 4 - 1, H * 4 - 1], radius=radius * 4, fill=255)
    return np.asarray(img.resize((W, H), Image.LANCZOS)) >= 128


def vignette(Wss, Hss, margin):
    """1 inside, falling to 0 at the plate edge, so the glow never touches the border."""
    y = np.minimum(np.arange(Hss), Hss - 1 - np.arange(Hss))[:, None]
    x = np.minimum(np.arange(Wss), Wss - 1 - np.arange(Wss))[None, :]
    lo = 3 * SS  # the outer 3 output pixels stay exactly the plate colour
    return (smooth(y.astype(np.float32), lo, margin + lo) * smooth(x.astype(np.float32), lo, margin + lo)).astype(np.float32)


def far_glow(mask, bright):
    """The two wide glow layers. Computed from the still shape so they stay identical between
    frames, which keeps the GIFs small."""
    mb = mask * np.broadcast_to(bright, mask.shape).astype(np.float32)
    return gaussian_filter(mb, 9.0 * SS), gaussian_filter(mb, 22.0 * SS)


def shade(mask, bright, stroke, vig, glow=1.0, far=None):
    """Turn a 0..1 mask into a lit neon tube on the plate colour. Returns float RGB."""
    cb = gaussian_filter(mask, stroke * 0.25)
    inner = np.clip((cb - 0.5) / 0.42, 0.0, 1.0) * mask
    col = NEAR + (BODY - NEAR) * smooth(inner, 0.0, 0.45)[..., None]
    col = col + (HI - col) * (smooth(inner, 0.45, 0.90) * 0.90)[..., None]
    col = col + (SHINE - col) * (smooth(inner, 0.90, 1.0) * 0.50)[..., None]
    b = np.broadcast_to(bright, mask.shape).astype(np.float32)
    col = DIM + (col - DIM) * b[..., None]

    mb = mask * b
    g1 = gaussian_filter(mb, 3.0 * SS)
    g2, g3 = far if far is not None else far_glow(mask, b)
    add = (NEAR * 0.42) * (g1 * glow)[..., None] + (ACC * 0.62) * g2[..., None] + (ACC * 0.60) * g3[..., None]
    frame = BG + add * vig[..., None]
    m = mask[..., None]
    return frame * (1 - m) + col * m


class Drip:
    def __init__(self, x, y, w, lmax, phase):
        self.x, self.y, self.w, self.lmax, self.phase = x, y, w, lmax, phase


class Title:
    """One animated section title."""

    def __init__(self, text, font_path, W=600, H=130, size=64, tracking=5.0, cy=0.42,
                 seed=1, n_drips=5, frames=40, flicker_letter=None, glitch_at=(22, 23, 33)):
        self.text_str = text
        self.Wo, self.Ho = W, H
        self.W, self.H = W * SS, H * SS
        self.N = frames
        self.rng = random.Random(seed)
        self.glitch_at = set(glitch_at)
        font = ImageFont.truetype(font_path, size * SS)
        tr = tracking * SS
        adv = [font.getlength(c) for c in text]
        total = sum(adv) + tr * (len(text) - 1)
        x = (self.W - total) / 2
        cap = -font.getbbox("H", anchor="ls")[1]
        base = self.H * cy + cap / 2
        img = Image.new("L", (self.W, self.H), 0)
        d = ImageDraw.Draw(img)
        self.spans = []
        for c, a in zip(text, adv):
            if c != " ":
                d.text((x, base), c, font=font, fill=255, anchor="ls")
                self.spans.append((int(x), int(x + a)))
            x += a + tr
        self.cap, self.base = cap, base
        self.text = np.asarray(img, np.float32) / 255.0
        ink = self.text > 0.5
        self.stroke = float(2 * np.percentile(distance_transform_edt(ink)[ink], 92))
        self.text_w = total
        self.vig = vignette(self.W, self.H, 14 * SS)
        self.alpha = plate_alpha(W, H, 16)
        self.flicker_letter = flicker_letter
        self.still = gooey(self.text, self.stroke * 0.10)
        self._far = {}
        self._place_drips(ink, n_drips)
        self._plan_glitch()

    def _place_drips(self, ink, n):
        cols = ink.any(0)
        low = np.where(cols, self.H - 1 - np.argmax(ink[::-1], axis=0), -1)
        floor = low.max()
        tol = max(2, int(self.cap * 0.05))
        good = low >= floor - tol
        runs, start = [], None
        for xi in range(self.W):
            if good[xi] and start is None:
                start = xi
            if (not good[xi] or xi == self.W - 1) and start is not None:
                runs.append((start, xi - 1))
                start = None
        min_w = self.stroke * 0.45
        spots = []
        for a, b in runs:
            if b - a < min_w:
                continue
            pad = self.stroke * 0.32
            if b - a > self.stroke * 1.6:  # a flat bottom bar: anywhere along it
                spots.append(self.rng.uniform(a + pad, b - pad))
            else:
                spots.append((a + b) / 2)
        self.rng.shuffle(spots)
        chosen = []
        gap = self.stroke * 2.2
        for s in spots:
            if all(abs(s - c) > gap for c in chosen):
                chosen.append(s)
            if len(chosen) == n:
                break
        self.drips = []
        k = len(chosen)
        for j, s in enumerate(sorted(chosen)):
            xi = int(round(s))
            w = self.stroke * self.rng.uniform(0.26, 0.34)
            lmax = self.cap * self.rng.uniform(0.34, 0.56)
            phase = (j * 0.618 + self.rng.uniform(0, 0.15)) % 1.0
            self.drips.append(Drip(s, float(low[xi]), w, lmax, phase))

    def _plan_glitch(self):
        r = self.rng
        top, bot = self.base - self.cap, self.base
        self.glitch = {}
        for f in sorted(self.glitch_at):
            bands = []
            for _ in range(r.choice([1, 2])):
                y0 = int(r.uniform(top, bot - self.cap * 0.25))
                h = int(r.uniform(self.cap * 0.12, self.cap * 0.24))
                dx = int(r.choice([-1, 1]) * r.uniform(3, 7) * SS)
                bands.append((y0, y0 + h, dx))
            self.glitch[f] = dict(bands=bands, split=int(r.uniform(2.0, 3.5) * SS),
                                  scan=int(r.uniform(top, bot)))

    def _drips(self, t):
        img = Image.new("L", (self.W, self.H), 0)
        d = ImageDraw.Draw(img)
        A, B, C = 0.50, 0.60, 0.92
        floor_y = self.H - 16 * SS
        for dr in self.drips:
            tau = (t + dr.phase) % 1.0
            l0, lr = 0.14 * dr.lmax, 0.42 * dr.lmax
            if tau < A:
                e = ease(tau / A)
                length, bulb = l0 + (dr.lmax - l0) * e, 1.0 + 0.40 * e
            elif tau < B:
                e = ease((tau - A) / (B - A))
                length, bulb = dr.lmax + (lr - dr.lmax) * e, 1.40 - 0.40 * e
            else:
                e = ease((tau - B) / (1 - B))
                length, bulb = lr + (l0 - lr) * e, 1.0
            x, w = dr.x, dr.w
            y0, y1 = dr.y - self.stroke * 0.5, dr.y + length
            d.rounded_rectangle([x - w, y0, x + w, y1], radius=w, fill=255)
            wb = w * bulb
            d.ellipse([x - wb, y1 - wb * 1.15, x + wb, y1 + wb * 0.85], fill=255)
            if A <= tau <= C:
                v = (tau - A) / (C - A)
                start = dr.y + dr.lmax
                yd = start + (floor_y - start) * v * v
                rd = w * 1.25 * (1 - float(smooth(np.float32(v), 0.55, 1.0)))
                if rd > 0.6:
                    d.ellipse([x - rd, yd - rd * (1.0 + 0.5 * v), x + rd, yd + rd], fill=255)
        return np.asarray(img, np.float32) / 255.0

    def frame(self, i):
        t = i / self.N
        mask = np.clip(self.text + self._drips(t), 0, 1)
        mask = gooey(mask, self.stroke * 0.10)
        bright = np.ones(self.W, np.float32)
        fl = {9: 0.5}.get(i % self.N, 1.0)
        bright *= fl
        lf = None
        if self.flicker_letter is not None:
            lf = {27: 0.15, 28: 0.7, 29: 0.2, 30: 0.85}.get(i % self.N)
            if lf is not None:
                a, b = self.spans[self.flicker_letter]
                xs = np.arange(self.W, dtype=np.float32)
                inside = smooth(xs, a - 4 * SS, a + 2 * SS) * (1 - smooth(xs, b - 2 * SS, b + 4 * SS))
                bright = bright * (1 - inside * (1 - lf))
        g = self.glitch.get(i)
        if g:
            for y0, y1, dx in g["bands"]:
                y0, y1 = max(0, y0), min(self.H, y1)
                mask[y0:y1] = np.roll(mask[y0:y1], dx, axis=1)
        if "still" not in self._far:  # the wide glow never changes: flickers stay local and cheap
            self._far["still"] = far_glow(self.still, np.float32(1.0))
        out = shade(mask, bright[None, :], self.stroke, self.vig, far=self._far["still"])
        if g:
            s = g["split"]
            keep = (1 - mask)[..., None]
            out += CYAN * 0.38 * (np.roll(mask, -s, axis=1)[..., None] * keep)
            out += VIOLET * 0.32 * (np.roll(mask, s, axis=1)[..., None] * keep)
            x0 = int((self.W - self.text_w) / 2 - 10 * SS)
            x1 = int((self.W + self.text_w) / 2 + 10 * SS)
            y = g["scan"]
            out[y:y + SS, x0:x1] = out[y:y + SS, x0:x1] * 0.6 + HI * 0.4
        return finish(out, self.Wo, self.Ho)


def finish(arr, Wo, Ho):
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    return np.asarray(img.resize((Wo, Ho), Image.LANCZOS))


class Divider:
    """Animated divider: a tapered light line with a slime drop in the middle."""

    def __init__(self, W=640, H=48, frames=30, seed=7):
        self.Wo, self.Ho = W, H
        self.W, self.H = W * SS, H * SS
        self.N = frames
        self.rng = random.Random(seed)
        self.vig = vignette(self.W, self.H, 8 * SS)
        self.alpha = plate_alpha(W, H, H // 2)
        self.stroke = 3.2 * SS
        xs = np.arange(self.W, dtype=np.float32) / self.W
        self.taper = smooth(np.minimum(xs, 1 - xs), 0.07, 0.30)
        self.glitch = {7: (int(-7 * SS), 0.30, 0.42), 8: (int(6 * SS), 0.58, 0.70), 21: (int(8 * SS), 0.36, 0.46)}
        self._far = {}

    def _still(self):
        """The resting shape (line, beads, drop) used for the wide glow."""
        cy, cx = self.H / 2, self.W / 2
        yy = np.abs(np.arange(self.H, dtype=np.float32) - cy)[:, None]
        xs = np.abs(np.arange(self.W, dtype=np.float32) - cx)[None, :]
        half, gap = 1.9 * SS, 30 * SS
        line = (1 - smooth(yy, half - 0.6 * SS, half + 0.6 * SS)) * self.taper[None, :]
        line = line * smooth(xs, gap - 5 * SS, gap + 5 * SS)
        return np.maximum(line, self._drop(cx, cy + 1.5 * SS, 7.6 * SS))

    def _drop(self, cx, cy, r):
        img = Image.new("L", (self.W, self.H), 0)
        d = ImageDraw.Draw(img)
        d.ellipse([cx - r, cy - r * 0.55, cx + r, cy + r * 1.45], fill=255)
        d.polygon([(cx, cy - r * 2.05), (cx + r * 0.80, cy - r * 0.05), (cx - r * 0.80, cy - r * 0.05)], fill=255)
        return gooey(np.asarray(img, np.float32) / 255.0, r * 0.16)

    def frame(self, i):
        p = i / self.N
        cy, cx = self.H / 2, self.W / 2
        pulse = (1 - math.cos(2 * math.pi * p)) / 2
        yy = np.abs(np.arange(self.H, dtype=np.float32) - cy)[:, None]
        half = 1.9 * SS
        line = (1 - smooth(yy, half - 0.6 * SS, half + 0.6 * SS)) * self.taper[None, :]
        gap = 30 * SS  # the line stops short of the drop
        xs = np.abs(np.arange(self.W, dtype=np.float32) - cx)[None, :]
        line = line * smooth(xs, gap - 5 * SS, gap + 5 * SS)
        g = self.glitch.get(i)
        if g:
            dx, a, b = g
            xa, xb = int(a * self.W), int(b * self.W)
            seg = line[:, xa:xb].copy()
            line[:, xa:xb] = 0
            dy = int(3 * SS if dx > 0 else -3 * SS)
            line[:, xa:xb] = np.maximum(line[:, xa:xb], np.roll(seg, dy, axis=0))
        mask = line.copy()
        for sx in (-gap + 9 * SS, gap - 9 * SS):  # two small beads beside the drop
            r = 2.7 * SS
            bead = np.sqrt((np.arange(self.W, dtype=np.float32)[None, :] - (cx + sx)) ** 2 +
                           (np.arange(self.H, dtype=np.float32)[:, None] - cy) ** 2)
            mask = np.maximum(mask, 1 - smooth(bead, r - 0.7 * SS, r + 0.7 * SS))
        r = (7.4 + 0.5 * pulse) * SS
        mask = np.maximum(mask, self._drop(cx, cy + 1.5 * SS, r))
        # light running outward from the drop along both arms
        pos = gap + (self.W / 2 - gap) * p
        run = np.exp(-((xs - pos) / (26 * SS)) ** 2)
        bright = 0.74 + 0.26 * run
        bright = np.maximum(bright, 1 - smooth(xs, gap * 0.9, gap * 1.3)) * (0.9 + 0.1 * pulse)
        fl = {12: 0.5}.get(i, 1.0)
        if "still" not in self._far:
            self._far["still"] = far_glow(self._still(), np.float32(0.8))
        g2, g3 = self._far["still"]
        out = shade(mask, np.broadcast_to(bright * fl, (1, self.W)), self.stroke, self.vig,
                    glow=0.75 + 0.25 * pulse, far=(g2 * 0.7, g3 * 0.7))
        if g:
            keep = (1 - mask)[..., None]
            out += CYAN * 0.5 * (np.roll(mask, -4 * SS, axis=1)[..., None] * keep)
        return finish(out, self.Wo, self.Ho)


def save_gif(frames, alpha, path, duration=70):
    """Write an animated GIF with one shared 255-colour palette and transparent plate corners.

    The plate colour is kept exact, and unchanged pixels are skipped between frames.
    """
    stack = np.concatenate(frames, axis=0)
    pimg = Image.fromarray(stack, "RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT,
                                                 dither=Image.Dither.NONE)
    pal = np.array(pimg.getpalette()[:255 * 3], np.float32).reshape(-1, 3)
    bg_i = int(np.argmin(((pal - BG) ** 2).sum(1)))
    pal[bg_i] = BG
    tree = cKDTree(pal)
    full = [tree.query(f.reshape(-1, 3).astype(np.float32))[1].reshape(f.shape[:2]).astype(np.uint8)
            for f in frames]
    T = 255
    out = []
    for k, idx in enumerate(full):
        cur = idx.copy()
        if k == 0:
            cur[~alpha] = T
        else:
            cur[idx == full[k - 1]] = T
            cur[~alpha] = T
        out.append(cur)
    flat = [int(v) for v in pal.astype(np.uint8).reshape(-1)] + [255, 0, 255]
    ims = []
    for cur in out:
        im = Image.fromarray(cur, "P")
        im.putpalette(flat)
        ims.append(im)
    ims[0].save(path, save_all=True, append_images=ims[1:], duration=duration, loop=0,
                transparency=T, disposal=1, optimize=False)
    strip_local_palettes(path)
    return pal.astype(np.uint8), full


def strip_local_palettes(path):
    """Pillow repeats the palette inside every frame. When a frame's copy is identical to the
    shared one, drop it (about 0.75 KB saved per frame). Frames with a different palette are kept."""
    b = open(path, "rb").read()
    flags = b[10]
    assert flags & 0x80, "no global palette"
    gsize = 3 * (2 ** ((flags & 7) + 1))
    gpal = b[13:13 + gsize]
    out = bytearray(b[:13 + gsize])
    p = 13 + gsize
    while p < len(b):
        c = b[p]
        if c == 0x3B:
            out.append(c)
            break
        if c == 0x21:
            q = p + 2
            while b[q]:
                q += b[q] + 1
            out += b[p:q + 1]
            p = q + 1
        elif c == 0x2C:
            fl = b[p + 9]
            q = p + 10
            head = bytearray(b[p:p + 10])
            if fl & 0x80:
                lsize = 3 * (2 ** ((fl & 7) + 1))
                lpal = b[q:q + lsize]
                q += lsize
                if lpal == gpal[:lsize] and lsize == gsize:
                    head[9] = fl & 0x60
                    out += head
                else:
                    out += head + lpal
            else:
                out += head
            start = q
            q += 1
            while b[q]:
                q += b[q] + 1
            out += b[start:q + 1]
            p = q + 1
        else:
            raise ValueError("unexpected GIF block")
    open(path, "wb").write(bytes(out))
