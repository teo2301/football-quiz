#!/usr/bin/env python3
"""docs/badge-96.png: the small monochrome notification badge (Android uses only its alpha).
A white football: outline ring, centre pentagon and the five seams. python3 tools/make_badge.py"""
import math
import os
from PIL import Image, ImageDraw

S, K = 96, 4                      # draw 4x and scale down for smooth edges
img = Image.new("RGBA", (S * K, S * K), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
c, r, w = S * K / 2, S * K * 0.44, S * K * 0.075
white = (255, 255, 255, 255)
d.ellipse([c - r, c - r, c + r, c + r], outline=white, width=int(w))
pr = r * 0.36
pts = [(c + pr * math.cos(math.radians(-90 + 72 * i)), c + pr * math.sin(math.radians(-90 + 72 * i))) for i in range(5)]
d.polygon(pts, fill=white)
for x, y in pts:
    a = math.atan2(y - c, x - c)
    d.line([(x, y), (c + (r - w / 2) * math.cos(a), c + (r - w / 2) * math.sin(a))], fill=white, width=int(w * 0.8))
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "badge-96.png")
img.resize((S, S), Image.LANCZOS).save(out, optimize=True)
print(out)
