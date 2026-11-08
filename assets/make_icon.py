"""
Generate the Clicky BI AI app icon (assets/icon.ico) for the PyInstaller build.

The icon is a nón lá (Vietnamese conical hat) in the panel blue (#2563eb):
seen from the side it is a triangle, so it keeps the original Clicky shape
while reading as "from Vietnam". The on-screen pointer (ui/overlay.py) is
the same hat: one symbol everywhere.

Produces multiple sizes (16 … 256); Windows picks the right one for taskbar,
file explorer and installer.  Run:  python assets\\make_icon.py
"""

from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "icon.ico"
BLUE = (0x25, 0x63, 0xEB, 255)      # panel blue #2563eb
DEEP = (0x1E, 0x40, 0xAF, 255)      # strap / shade
RING = (0xCF, 0xE0, 0xFF, 255)      # woven rings
WHITE = (255, 255, 255, 255)
SS = 8                              # supersampling factor for smooth edges


def _brim(cx, cy, rx, ry, steps=24):
    """Lower half of an ellipse, right -> left (the curved hat brim)."""
    import math
    return [(cx + rx * math.cos(math.pi * i / steps), cy + ry * math.sin(math.pi * i / steps))
            for i in range(steps + 1)]


def make_frame(size: int) -> Image.Image:
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = S / 32.0                                   # 32-unit design grid
    apex = (16 * u, 2.5 * u)
    hat = [apex, (29.5 * u, 23 * u)] + _brim(16 * u, 23 * u, 13.5 * u, 3.2 * u) + [(2.5 * u, 23 * u)]
    outline = max(1, int(1.4 * u)) if size >= 32 else max(1, int(1.0 * u))
    d.polygon(hat, fill=BLUE, outline=WHITE, width=outline)
    if size >= 24:                                 # rings vanish at 16 px; keep it clean there
        w = max(1, int(1.3 * u))
        for y, half in ((10.5, 4.1), (16.5, 7.9)):
            d.line([(16 * u - half * u, y * u), (16 * u + half * u, y * u)], fill=RING, width=w)
    if size >= 32:                                 # chin strap (quai nón)
        d.arc([8 * u, 21 * u, 24 * u, 31 * u], 20, 160, fill=DEEP, width=max(1, int(1.4 * u)))
    return img.resize((size, size), Image.LANCZOS)


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [make_frame(s) for s in sizes]
    # Save from the LARGEST frame. Pillow's ICO writer only ever downscales
    # from the base image, so basing this on frames[0] (16px) would produce a
    # 16x16-only .ico. append_images keeps the purpose-drawn small frames.
    frames[-1].save(
        OUT,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[:-1],
    )
    print(f"Wrote {OUT} ({len(sizes)} sizes)")


if __name__ == "__main__":
    main()
