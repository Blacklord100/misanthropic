"""Regenerate Misanthropic's visual assets from code (skull + Anthropic asterisk).

Run this whenever the mark changes; the outputs are committed so the build and
DMG scripts don't need Pillow. Requires Pillow:  pip install Pillow

    python packaging/icons/draw.py

Writes:
    src/misanthropic/resources/menubar.png        (44x44 menu-bar template, @1x)
    src/misanthropic/resources/menubar@2x.png     (88x88, @2x retina)
    packaging/icons/Misanthropic.iconset/*        (app icon sizes for iconutil)
    packaging/icons/dmg-background.png            (660x400 DMG window backdrop)
    packaging/icons/dmg-background@2x.png         (1320x800, retina)
    packaging/icons/preview.png                   (256x256 mark preview, for docs)

The app `.icns` is built from the iconset by packaging/build.sh via `iconutil`.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO / "src" / "misanthropic" / "resources"
ICON_DIR = REPO / "packaging" / "icons"
ICONSET_DIR = ICON_DIR / "Misanthropic.iconset"

# Brand colors
BONE = (237, 235, 228, 255)        # warm off-white skull
CLAY = (217, 119, 87, 255)         # Anthropic-ish coral, for the asterisk
BG_TOP = (38, 42, 54)              # slate
BG_BOTTOM = (12, 14, 18)           # near-black

# The skull is authored in a 44x44 grid, scaled up for crisp downsampling.
GRID = 44
S = 24
BASE = GRID * S


def _asterisk(draw, cx, cy, arm, width, fill):
    """A 4-arm Anthropic-style asterisk centered at (cx, cy)."""
    for i in range(4):
        a = math.pi * i / 4
        dx, dy = int(arm * math.cos(a)), int(arm * math.sin(a))
        draw.line((cx - dx, cy - dy, cx + dx, cy + dy), fill=fill, width=width)


def _skull_mask(asterisk_holes):
    """An L-mode mask: 255 = bone, 0 = hole/background. Optionally punch the
    asterisk as negative space (used for the menu-bar template image)."""
    mask = Image.new("L", (BASE, BASE), 0)
    d = ImageDraw.Draw(mask)
    s = S
    d.ellipse((4 * s, 3 * s, 40 * s, 32 * s), fill=255)            # cranium
    d.polygon([(10 * s, 28 * s), (34 * s, 28 * s),
               (32 * s, 38 * s), (12 * s, 38 * s)], fill=255)       # jaw
    d.rounded_rectangle((10 * s, 25 * s, 34 * s, 30 * s), radius=2 * s, fill=255)
    for x0 in (11, 25):                                            # eye sockets
        d.ellipse((x0 * s, 16 * s, (x0 + 8) * s, 24 * s), fill=0)
    d.polygon([(22 * s, 24 * s), (20 * s, 28 * s), (24 * s, 28 * s)], fill=0)  # nose
    for cx in (16, 22, 28):                                        # teeth
        d.rectangle((cx * s - 1 * s, 34 * s, cx * s + 1 * s, 38 * s + 1), fill=0)
    if asterisk_holes:
        _asterisk(d, 22 * s, 10 * s, int(3.2 * s), max(2, int(1.2 * s)), 0)
    return mask


def _vgradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line((0, y, w, y), fill=c)
    return img.convert("RGBA")


def _font(size):
    for p in ("/System/Library/Fonts/SFNSDisplay.ttf",
              "/System/Library/Fonts/SFNS.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


# ---- menu-bar template (black skull + asterisk holes on transparent) --------

def menubar_image():
    mask = _skull_mask(asterisk_holes=True)
    out = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    out.paste((0, 0, 0, 255), (0, 0, BASE, BASE), mask=mask)
    return out


# ---- colored app icon (rounded-rect gradient + bone skull + clay asterisk) ---

def app_icon(px=1024):
    # Rounded-rect gradient tile with macOS-style padding.
    margin = int(BASE * 0.085)
    radius = int(BASE * 0.225)
    rr = Image.new("L", (BASE, BASE), 0)
    ImageDraw.Draw(rr).rounded_rectangle(
        (margin, margin, BASE - margin, BASE - margin), radius=radius, fill=255)
    icon = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    icon.paste(_vgradient(BASE, BASE, BG_TOP, BG_BOTTOM), (0, 0), rr)

    # Bone skull with the clay asterisk baked in, then scaled into the tile.
    bone = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    bone.paste(BONE, (0, 0, BASE, BASE), mask=_skull_mask(asterisk_holes=False))
    _asterisk(ImageDraw.Draw(bone), 22 * S, 10 * S, int(3.2 * S), int(1.7 * S), CLAY)

    scale = 0.62
    sw = int(BASE * scale)
    bone = bone.resize((sw, sw), Image.LANCZOS)
    icon.alpha_composite(bone, ((BASE - sw) // 2, int((BASE - sw) * 0.46)))
    return icon.resize((px, px), Image.LANCZOS)


def write_iconset():
    """Emit the .iconset PNG sizes; build.sh turns them into appicon.icns."""
    ICONSET_DIR.mkdir(parents=True, exist_ok=True)
    master = app_icon(1024)
    for size in (16, 32, 128, 256, 512):
        master.resize((size, size), Image.LANCZOS).save(ICONSET_DIR / f"icon_{size}x{size}.png")
        master.resize((size * 2, size * 2), Image.LANCZOS).save(ICONSET_DIR / f"icon_{size}x{size}@2x.png")


# ---- DMG window backdrop (drag-to-Applications) -----------------------------

def dmg_background(scale=1):
    w, h = 660 * scale, 400 * scale
    img = _vgradient(w, h, BG_TOP, BG_BOTTOM)
    d = ImageDraw.Draw(img)

    # Headline
    title = _font(26 * scale)
    sub = _font(15 * scale)
    msg = "Drag Misanthropic into Applications"
    tw = d.textlength(msg, font=title)
    d.text(((w - tw) / 2, 54 * scale), msg, font=title, fill=(237, 239, 245, 255))
    sub_msg = "Anthropic charges you. Misanthropic charges no one."
    sw = d.textlength(sub_msg, font=sub)
    d.text(((w - sw) / 2, 92 * scale), sub_msg, font=sub, fill=(139, 147, 167, 255))

    # Arrow from the app spot (≈180,200) toward Applications (≈480,200).
    y = 200 * scale
    x0, x1 = 300 * scale, 372 * scale
    arrow = (120, 132, 156, 255)
    d.line((x0, y, x1, y), fill=arrow, width=max(2, 4 * scale))
    head = 12 * scale
    d.polygon([(x1, y - head), (x1 + head * 1.4, y), (x1, y + head)], fill=arrow)
    return img


def main():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    ICON_DIR.mkdir(parents=True, exist_ok=True)

    mb = menubar_image()
    mb.resize((44, 44), Image.LANCZOS).save(RUNTIME_DIR / "menubar.png")
    mb.resize((88, 88), Image.LANCZOS).save(RUNTIME_DIR / "menubar@2x.png")

    write_iconset()
    app_icon(256).save(ICON_DIR / "preview.png")
    dmg_background(1).save(ICON_DIR / "dmg-background.png")
    dmg_background(2).save(ICON_DIR / "dmg-background@2x.png")

    print(f"wrote menu-bar template -> {RUNTIME_DIR}/menubar.png (+@2x)")
    print(f"wrote app iconset       -> {ICONSET_DIR}/ (build.sh -> appicon.icns)")
    print(f"wrote DMG backdrop      -> {ICON_DIR}/dmg-background.png (+@2x)")
    print(f"wrote mark preview      -> {ICON_DIR}/preview.png")


if __name__ == "__main__":
    main()
