"""Draws the MDI icon and writes every size the app and extension need.

    python tools/make_icon.py        (needs Pillow; only for regenerating)

Design: a cartoon orange globe with a chubby, smiling blue arrow diving
into it, on a navy rounded square, with thick ink outlines. It's an
original take on the classic "globe + arrow" download-manager look, with
the colours swapped and the arrow on the left.
"""

import os

from PIL import Image, ImageDraw

S = 1024                      # draw big, then downscale for smooth edges
INK = (27, 27, 47)
NAVY = (36, 52, 110)
NAVY_HI = (52, 74, 150)
ORANGE = (255, 159, 28)
ORANGE_DK = (230, 118, 10)
ORANGE_HI = (255, 200, 110)
BLUE = (58, 160, 255)
BLUE_HI = (160, 215, 255)
WHITE = (255, 255, 255)
PINK = (255, 120, 150)
OUT = 34                      # outline width at 1024 px

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def draw() -> Image.Image:
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    # Background tile with a lighter top half for a bit of depth.
    d.rounded_rectangle((24, 24, S - 24, S - 24), radius=210, fill=NAVY,
                        outline=INK, width=OUT)
    d.rounded_rectangle((70, 70, S - 70, S // 2), radius=170, fill=NAVY_HI)

    # Globe (right side), drawn on its own layer and clipped to the circle
    # so the grid lines never poke out past the outline.
    cx, cy, r = 590, 560, 330
    box = (cx - r, cy - r, cx + r, cy + r)
    globe = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(globe)
    g.ellipse(box, fill=ORANGE)
    for k in (0.42, 0.78):                       # meridians
        w = r * k
        g.ellipse((cx - w, cy - r - 40, cx + w, cy + r + 40),
                  outline=ORANGE_DK, width=22)
    g.line((cx, cy - r, cx, cy + r), fill=ORANGE_DK, width=22)
    for dy in (-0.5, 0.0, 0.5):                  # parallels
        y = cy + dy * r
        g.line((cx - r, y, cx + r, y), fill=ORANGE_DK, width=22)
    g.ellipse((cx + r * 0.18, cy - r * 0.80, cx + r * 0.62, cy - r * 0.46),
              fill=ORANGE_HI)                    # shine
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    im.paste(globe, (0, 0), mask)
    d.ellipse(box, outline=INK, width=OUT)

    # Chubby arrow (left side), pointing down into the globe, with a
    # cartoon drop shadow.
    arrow = [(225, 140), (475, 140), (475, 480), (600, 480),
             (350, 800), (100, 480), (225, 480)]
    d.polygon([(x + 26, y + 26) for x, y in arrow], fill=(15, 18, 40))
    d.polygon(arrow, fill=BLUE, outline=INK, width=OUT)
    d.rounded_rectangle((262, 180, 307, 420), radius=22, fill=BLUE_HI)

    # Face: eyes, cheeks, grin.
    for ex in (300, 400):
        d.ellipse((ex - 38, 300, ex + 38, 400), fill=WHITE, outline=INK,
                  width=14)
        d.ellipse((ex - 14, 340, ex + 22, 392), fill=INK)
        d.ellipse((ex - 4, 346, ex + 10, 362), fill=WHITE)
    d.ellipse((236, 420, 280, 452), fill=PINK)
    d.ellipse((420, 420, 464, 452), fill=PINK)
    d.arc((290, 460, 410, 580), start=20, end=160, fill=INK, width=16)
    return im


def main() -> None:
    big = draw()
    sizes = [16, 24, 32, 48, 64, 128, 256]
    assets = os.path.join(ROOT, "mdi", "assets")
    icons = os.path.join(ROOT, "extension", "icons")
    os.makedirs(assets, exist_ok=True)
    os.makedirs(icons, exist_ok=True)

    big.resize((256, 256), Image.LANCZOS).save(
        os.path.join(assets, "icon.png"))
    big.resize((256, 256), Image.LANCZOS).save(
        os.path.join(assets, "icon.ico"), sizes=[(s, s) for s in sizes])
    for s in (16, 32, 48, 128):
        big.resize((s, s), Image.LANCZOS).save(
            os.path.join(icons, f"icon{s}.png"))
    big.resize((512, 512), Image.LANCZOS).save(
        os.path.join(ROOT, "tools", "icon_preview.png"))
    print("icons written")


if __name__ == "__main__":
    main()
