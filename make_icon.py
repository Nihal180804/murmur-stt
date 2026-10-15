"""Generate Murmur's logo: a gradient rounded tile with a white mic and sound
waves. Writes app.ico (multi-size) and assets/logo.png. Run at build time.
"""
import os
from PIL import Image, ImageDraw

# Brand gradient (top -> bottom)
TOP = (99, 102, 241)     # indigo-500
BOT = (168, 85, 247)     # purple-500
SS = 4                   # supersampling factor for smooth edges


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def render(size):
    s = size * SS
    # vertical gradient
    grad = Image.new("RGB", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        gd.line([(0, y), (s, y)], fill=_lerp(TOP, BOT, y / (s - 1)))

    # rounded-square alpha mask
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.23), fill=255)

    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    cx = s // 2
    white = (255, 255, 255, 255)

    def u(v):
        return int(v / 64 * s)

    # sound waves either side (semi-transparent)
    faint = (255, 255, 255, 110)
    for r, w in ((u(20), u(2.2)), (u(27), u(2.0))):
        d.arc([cx - r, u(32) - r, cx + r, u(32) + r], start=215, end=325, fill=faint, width=w)
        d.arc([cx - r, u(32) - r, cx + r, u(32) + r], start=35, end=145, fill=faint, width=w)

    # microphone capsule
    mw = u(13)
    d.rounded_rectangle([cx - mw // 2, u(13), cx + mw // 2, u(37)], radius=u(6.5), fill=white)
    # cradle arc
    cr = u(11)
    d.arc([cx - cr, u(27) - cr, cx + cr, u(27) + cr], start=0, end=180, fill=white, width=u(2.6))
    # stem + base
    d.line([cx, u(38), cx, u(46)], fill=white, width=u(2.6))
    d.rounded_rectangle([cx - u(7), u(46), cx + u(7), u(49)], radius=u(1.5), fill=white)

    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(here, "assets"), exist_ok=True)

    render(256).save(os.path.join(here, "assets", "logo.png"))

    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [render(z) for z in sizes]
    imgs[0].save(os.path.join(here, "app.ico"),
                 sizes=[(z, z) for z in sizes], append_images=imgs[1:])
    print("wrote app.ico and assets/logo.png")
