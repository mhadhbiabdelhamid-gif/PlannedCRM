#!/usr/bin/env python3
"""Rebuild the phone app icons in static/img/pwa from the logo.

Only needed if the logo changes:  python make_pwa_icons.py
Needs Pillow and numpy (pip install numpy). The icons are committed, so the
server itself needs neither.
"""
import sys
from PIL import Image, ImageChops, ImageDraw

src = sys.argv[1] if len(sys.argv) > 1 else "static/img/logo.jpg"
out = sys.argv[2] if len(sys.argv) > 2 else "static/img/pwa"
im = Image.open(src).convert("RGB")
s = im.size[0] / 400
# the P-and-roof mark, without the wordmark under and beside it
box = [int(v * s) for v in (0, 30, 300, 250)]
mark = im.crop(box)
d = ImageDraw.Draw(mark)
d.rectangle([int(142 * s), int(150 * s), mark.size[0], mark.size[1]], fill="white")
# Lift the mark off its white background: alpha from how far the darkest
# channel is from white, then un-blend the colour so the gold stays bright
# instead of keeping the white it was mixed with.
import numpy as np
px = np.asarray(mark).astype(np.float32)
a = np.clip((255 - px.min(axis=2)) / 200.0, 0, 1)
safe = np.maximum(a, 1e-3)[..., None]
rgb = np.clip((px - 255 * (1 - a[..., None])) / safe, 0, 255)
out_px = np.dstack([rgb, a * 255]).astype(np.uint8)
mark = Image.fromarray(out_px, "RGBA")
alpha = mark.getchannel("A")
mark = mark.crop(alpha.point(lambda v: 255 if v > 20 else 0).getbbox())

INK = (35, 32, 26, 255)

def icon(size, pad):
    canvas = Image.new("RGBA", (size, size), INK)
    inner = int(size * (1 - 2 * pad))
    m = mark.copy(); m.thumbnail((inner, inner), Image.LANCZOS)
    canvas.alpha_composite(m, ((size - m.size[0]) // 2, (size - m.size[1]) // 2))
    return canvas

icon(192, .10).save(f"{out}/icon-192.png")
icon(512, .10).save(f"{out}/icon-512.png")
icon(512, .20).save(f"{out}/icon-maskable-512.png")   # safe zone for round/squircle masks
icon(180, .12).convert("RGB").save(f"{out}/apple-touch-icon.png")
# Android status-bar badge: a white silhouette on transparent
b = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
m = mark.copy(); m.thumbnail((80, 80), Image.LANCZOS)
white = Image.new("RGBA", m.size, (255, 255, 255, 0)); white.putalpha(m.getchannel("A"))
b.alpha_composite(white, ((96 - m.size[0]) // 2, (96 - m.size[1]) // 2))
b.save(f"{out}/badge-96.png")
