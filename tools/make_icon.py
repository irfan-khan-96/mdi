"""Draws MDI's icon and toolbar glyphs and writes every size the app needs.

    python tools/make_icon.py        (needs Pillow; only for regenerating)

The icon: a friendly blue arrow diving into an orange globe, on an indigo
tile with a soft gradient. Small sizes are drawn separately rather than
just shrunk: below 48 px the face goes, and below 32 px the globe loses its
grid and the outlines get thicker, so it still reads in the taskbar.

The toolbar glyphs are simple line icons in the same colours.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter

S = 1024                      # draw big, then downscale for smooth edges
INK = (20, 20, 40)
TILE_TOP, TILE_BOTTOM = (64, 94, 200), (26, 34, 92)
GLOBE_LIGHT, GLOBE_DARK, GLOBE_LINE = (255, 200, 90), (244, 124, 18), (206, 96, 10)
ARROW_TOP, ARROW_BOTTOM = (130, 210, 255), (36, 136, 244)
SLATE = (58, 64, 86)
BRAND = (36, 136, 244)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "mdi", "assets")


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vertical_gradient(top, bottom, size=S):
    g = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(g)
    for y in range(size):
        d.line((0, y, size, y), fill=lerp(top, bottom, y / size) + (255,))
    return g


def solid_mask(draw_fn, soften=0):
    m = Image.new("L", (S, S), 0)
    draw_fn(ImageDraw.Draw(m))
    if soften:
        m = m.filter(ImageFilter.GaussianBlur(soften)).point(
            lambda v: 255 if v > 128 else 0)
    return m


def drop_shadow(mask, offset, blur, alpha):
    sh = Image.new("L", (S, S), 0)
    sh.paste(mask, offset)
    sh = sh.filter(ImageFilter.GaussianBlur(blur)).point(
        lambda v: v * alpha // 255)
    layer = Image.new("RGBA", (S, S), (8, 10, 30, 255))
    layer.putalpha(sh)
    return layer


def outline(mask, width):
    """A ring of ``width`` px around the outside of ``mask``."""
    grown = mask.filter(ImageFilter.MaxFilter(width * 2 + 1))
    return Image.eval(Image.merge("L", [grown]), lambda v: v).point(
        lambda v: v) if False else Image.composite(
            grown, Image.new("L", (S, S), 0), grown)


def draw(detail: str) -> Image.Image:
    """detail: "full" (face), "medium" (no face) or "tiny" (no grid,
    thick outlines)."""
    tiny = detail == "tiny"
    line = 54 if tiny else 30          # outline width at 1024 px
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Tile: gradient, rounded, with a soft sheen across the top.
    inset = 28 if tiny else 40
    tile_mask = solid_mask(lambda d: d.rounded_rectangle(
        (inset, inset, S - inset, S - inset), radius=230, fill=255))
    im.paste(vertical_gradient(TILE_TOP, TILE_BOTTOM), (0, 0), tile_mask)
    if not tiny:
        sheen = solid_mask(lambda d: d.ellipse((-200, -640, S + 200, 420),
                                               fill=255))
        sheen = Image.composite(sheen, Image.new("L", (S, S), 0), tile_mask)
        im.paste((255, 255, 255, 255), (0, 0), sheen.point(lambda v: v * 40 // 255))

    # Globe (right), shaded from a light top-left to a deep bottom-right.
    cx, cy, r = (600, 610, 320) if tiny else (610, 600, 300)
    globe_mask = solid_mask(lambda d: d.ellipse((cx - r, cy - r, cx + r, cy + r),
                                                fill=255))
    im.alpha_composite(drop_shadow(globe_mask, (0, 26), 26, 100))
    globe = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(globe)
    for i in range(r, 0, -2):
        t = i / r
        off = int((1 - t) * r * 0.35)        # light spot up and to the left
        g.ellipse((cx - i - off, cy - i - off, cx + i - off, cy + i - off),
                  fill=lerp(GLOBE_LIGHT, GLOBE_DARK, t) + (255,))
    if not tiny:
        w = 0.55 * r
        g.ellipse((cx - w, cy - r - 30, cx + w, cy + r + 30),
                  outline=GLOBE_LINE, width=26)
        g.line((cx - r, cy, cx + r, cy), fill=GLOBE_LINE, width=26)
    else:
        g.line((cx - r, cy, cx + r, cy), fill=GLOBE_LINE, width=46)
    im.paste(globe, (0, 0), globe_mask)
    im.paste(INK + (255,), (0, 0), Image.composite(
        globe_mask.filter(ImageFilter.MaxFilter(line * 2 + 1)),
        Image.new("L", (S, S), 0),
        Image.eval(globe_mask, lambda v: 255 - v)))

    # Arrow (left), diving down into the globe.
    if tiny:
        pts = [(230, 120), (500, 120), (500, 470), (650, 470), (365, 860),
               (80, 470), (230, 470)]
    else:
        pts = [(240, 150), (480, 150), (480, 500), (610, 500), (360, 840),
               (110, 500), (240, 500)]
    arrow_mask = solid_mask(lambda d: d.polygon(pts, fill=255), soften=14)
    im.alpha_composite(drop_shadow(arrow_mask, (18, 30), 22, 120))
    ring = Image.composite(arrow_mask.filter(ImageFilter.MaxFilter(line * 2 + 1)),
                           Image.new("L", (S, S), 0),
                           Image.eval(arrow_mask, lambda v: 255 - v))
    im.paste(INK + (255,), (0, 0), ring)
    im.paste(vertical_gradient(ARROW_TOP, ARROW_BOTTOM), (0, 0), arrow_mask)
    if not tiny:                                # glossy streak on the shaft
        streak = solid_mask(lambda d: d.rounded_rectangle(
            (274, 190, 316, 450), radius=21, fill=255))
        im.paste((255, 255, 255, 255), (0, 0), streak.point(lambda v: v * 110 // 255))

    if detail == "full":                        # the face
        d = ImageDraw.Draw(im)
        for ex in (318, 414):
            d.ellipse((ex - 36, 296, ex + 36, 388), fill=(255, 255, 255),
                      outline=INK, width=14)
            d.ellipse((ex - 10, 332, ex + 24, 380), fill=INK)
            d.ellipse((ex + 2, 338, ex + 14, 352), fill=(255, 255, 255))
        d.ellipse((252, 410, 292, 438), fill=(255, 128, 160))
        d.ellipse((440, 410, 480, 438), fill=(255, 128, 160))
        d.arc((306, 410, 426, 520), 20, 160, fill=INK, width=18)
    return im


# ------------------------------------------------------------ toolbar glyphs

def glyph(name: str, px: int) -> Image.Image:
    """A line icon drawn at 8x and shrunk to ``px``."""
    k = 8
    n = px * k
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = max(2, round(n * 0.11))             # stroke width
    c = SLATE + (255,)
    m = n * 0.16                            # margin

    def line(*pts, colour=c, width=w):
        d.line(pts, fill=colour, width=width, joint="curve")
        for x, y in (pts[0], pts[-1]):      # round caps
            d.ellipse((x - width / 2, y - width / 2, x + width / 2,
                       y + width / 2), fill=colour)

    if name == "add":                        # arrow into a tray, in brand blue
        b = BRAND + (255,)
        line((n / 2, m), (n / 2, n * 0.62), colour=b)
        line((n * 0.3, n * 0.42), (n / 2, n * 0.62), (n * 0.7, n * 0.42), colour=b)
        line((m, n * 0.66), (m, n - m), (n - m, n - m), (n - m, n * 0.66), colour=b)
    elif name == "resume":                   # play
        d.polygon([(n * 0.3, m), (n * 0.82, n / 2), (n * 0.3, n - m)], fill=c)
    elif name == "pause":
        for x in (n * 0.3, n * 0.6):
            d.rounded_rectangle((x, m, x + n * 0.14, n - m), radius=n * 0.05, fill=c)
    elif name == "remove":                   # bin
        line((m, n * 0.27), (n - m, n * 0.27))
        line((n * 0.4, m), (n * 0.6, m))
        d.rounded_rectangle((n * 0.24, n * 0.34, n * 0.76, n - m), radius=n * 0.06,
                            outline=c, width=w)
    elif name == "folder":
        d.rounded_rectangle((m, n * 0.32, n - m, n - m), radius=n * 0.08,
                            outline=c, width=w)
        line((m, n * 0.34), (m, n * 0.22), (n * 0.42, n * 0.22), (n * 0.5, n * 0.32))
    elif name == "settings":                 # gear
        import math
        cx = cy = n / 2
        for i in range(8):
            a = i * math.pi / 4
            line((cx + math.cos(a) * n * 0.22, cy + math.sin(a) * n * 0.22),
                 (cx + math.cos(a) * n * 0.36, cy + math.sin(a) * n * 0.36),
                 width=round(w * 1.3))
        d.ellipse((cx - n * 0.26, cy - n * 0.26, cx + n * 0.26, cy + n * 0.26),
                  outline=c, width=w)
        d.ellipse((cx - n * 0.08, cy - n * 0.08, cx + n * 0.08, cy + n * 0.08),
                  fill=c)
    elif name == "about":                    # i in a circle
        d.ellipse((m, m, n - m, n - m), outline=c, width=w)
        d.ellipse((n / 2 - w * 0.7, n * 0.28 - w * 0.7, n / 2 + w * 0.7,
                   n * 0.28 + w * 0.7), fill=c)
        line((n / 2, n * 0.45), (n / 2, n * 0.7))
    return im.resize((px, px), Image.LANCZOS)


GLYPHS = ("add", "resume", "pause", "remove", "folder", "settings", "about")


def main() -> None:
    full, medium, tiny = draw("full"), draw("medium"), draw("tiny")

    def at(px):
        src = full if px >= 48 else medium if px >= 32 else tiny
        return src.resize((px, px), Image.LANCZOS)

    os.makedirs(ASSETS, exist_ok=True)
    at(256).save(os.path.join(ASSETS, "icon.png"))
    sizes = [256, 128, 64, 48, 32, 24, 20, 16]
    at(256).save(os.path.join(ASSETS, "icon.ico"),
                 sizes=[(s, s) for s in sizes],
                 append_images=[at(s) for s in sizes[1:]])
    icons = os.path.join(ROOT, "extension", "icons")
    if os.path.isdir(icons):
        for s in (16, 32, 48, 128):
            at(s).save(os.path.join(icons, f"icon{s}.png"))
    for name in GLYPHS:
        for px in (20, 32):
            glyph(name, px).save(os.path.join(ASSETS, f"tb_{name}_{px}.png"))
    at(512).save(os.path.join(ROOT, "tools", "icon_preview.png"))
    print("icons written")


if __name__ == "__main__":
    main()
