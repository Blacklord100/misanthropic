"""Regenerate the menu-bar icon (skull silhouette + Anthropic-style asterisk).

The output is a black-on-transparent PNG used as a macOS *template image* — when
loaded with `template=True`, macOS tints it to match the menu-bar foreground
color (so it works in light and dark mode automatically).

    python packaging/icons/draw.py

writes:
    src/breakthrough/resources/menubar.png       (44x44, @1x)
    src/breakthrough/resources/menubar@2x.png    (88x88, @2x retina)
    packaging/icons/preview.png                  (256x256, for docs)

Requires Pillow:  pip install Pillow
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO / "src" / "breakthrough" / "resources"
PREVIEW = REPO / "packaging" / "icons" / "preview.png"

# Draw at @8x for crispness, then downsample to the target sizes.
SCALE = 8
W = 44 * SCALE
H = 44 * SCALE


def draw():
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    s = SCALE

    # Cranium: wide rounded top
    d.ellipse((4 * s, 3 * s, 40 * s, 32 * s), fill=255)
    # Jaw: narrower trapezoid below
    d.polygon(
        [(10 * s, 28 * s), (34 * s, 28 * s), (32 * s, 38 * s), (12 * s, 38 * s)],
        fill=255,
    )
    # Smooth the cranium/jaw junction
    d.rounded_rectangle((10 * s, 25 * s, 34 * s, 30 * s), radius=2 * s, fill=255)

    # Eye sockets (subtract = transparent)
    for x0 in (11, 25):
        d.ellipse((x0 * s, 16 * s, (x0 + 8) * s, 24 * s), fill=0)

    # Nose (small inverted triangle)
    d.polygon([(22 * s, 24 * s), (20 * s, 28 * s), (24 * s, 28 * s)], fill=0)

    # Teeth: notches cut into the jaw bottom edge
    for cx in (16, 22, 28):
        d.rectangle((cx * s - 1 * s, 34 * s, cx * s + 1 * s, 38 * s + 1), fill=0)

    # Anthropic-style 4-arm asterisk on the forehead (negative space)
    cx, cy, arm = 22 * s, 10 * s, int(3.2 * s)
    width = max(2, int(1.2 * s))
    for i in range(4):
        a = math.pi * i / 4
        dx, dy = int(arm * math.cos(a)), int(arm * math.sin(a))
        d.line((cx - dx, cy - dy, cx + dx, cy + dy), fill=0, width=width)

    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste((0, 0, 0, 255), (0, 0, W, H), mask=mask)
    return out


def main():
    icon = draw()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    icon.resize((44, 44), Image.LANCZOS).save(RUNTIME_DIR / "menubar.png")
    icon.resize((88, 88), Image.LANCZOS).save(RUNTIME_DIR / "menubar@2x.png")
    icon.resize((256, 256), Image.LANCZOS).save(PREVIEW)
    print(f"wrote {RUNTIME_DIR}/menubar.png (44x44)")
    print(f"wrote {RUNTIME_DIR}/menubar@2x.png (88x88)")
    print(f"wrote {PREVIEW} (256x256 preview)")


if __name__ == "__main__":
    main()
