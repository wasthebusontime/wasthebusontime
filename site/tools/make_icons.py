"""Draws the site icon (a clock face) as SVG, ICO and PNG.

Our own mark, drawn from basic shapes here, so anyone can see where it comes from:
a violet square (the header color), a white face, two violet hands. Not a bus, not
the agency's colors (docs/planning/topics/style-guide.md).

Run once after changing the shapes, with Pillow as a temporary tool (not a site
dependency), from site/:

    uv run --with pillow python tools/make_icons.py

Writes src/wbot_site/static/icons/: favicon.svg, favicon.ico (16 and 32 px) and
apple-touch-icon.png (180 px, square corners: phones round them themselves).
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "src" / "wbot_site" / "static" / "icons"
VIOLET = "#4e2281"
WHITE = "#ffffff"

# Geometry on a 64-unit square.
SIZE = 64
CORNER = 14
FACE = (32, 32, 22)  # center x, center y, radius
HANDS = [((32, 32), (32, 16)), ((32, 32), (42, 38))]  # minute hand up, hour hand toward four
HAND_WIDTH = 5
HUB = 3.5


def svg() -> str:
    cx, cy, r = FACE
    hands = " ".join(f"M{a[0]} {a[1]} L{b[0]} {b[1]}" for a, b in HANDS)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}">'
        f'<rect width="{SIZE}" height="{SIZE}" rx="{CORNER}" fill="{VIOLET}"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{WHITE}"/>'
        f'<path d="{hands}" stroke="{VIOLET}" stroke-width="{HAND_WIDTH}" stroke-linecap="round"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{HUB}" fill="{VIOLET}"/>'
        "</svg>\n"
    )


def raster(px: int, rounded: bool) -> Image.Image:
    """Draws at 8x and scales down, for smooth edges."""
    k = px * 8 / SIZE
    big = Image.new("RGBA", (px * 8, px * 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    d.rounded_rectangle([0, 0, px * 8 - 1, px * 8 - 1], radius=CORNER * k if rounded else 0, fill=VIOLET)
    cx, cy, r = FACE
    d.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=WHITE)
    w = HAND_WIDTH * k
    for (x0, y0), (x1, y1) in HANDS:
        d.line([x0 * k, y0 * k, x1 * k, y1 * k], fill=VIOLET, width=round(w))
        for x, y in ((x0, y0), (x1, y1)):  # round caps
            d.ellipse([x * k - w / 2, y * k - w / 2, x * k + w / 2, y * k + w / 2], fill=VIOLET)
    d.ellipse([(cx - HUB) * k, (cy - HUB) * k, (cx + HUB) * k, (cy + HUB) * k], fill=VIOLET)
    return big.resize((px, px), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "favicon.svg").write_text(svg(), encoding="utf-8", newline="\n")
    raster(32, rounded=True).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32)])
    raster(180, rounded=False).save(OUT / "apple-touch-icon.png", optimize=True)
    print(f"wrote favicon.svg, favicon.ico and apple-touch-icon.png to {OUT}")


if __name__ == "__main__":
    main()
