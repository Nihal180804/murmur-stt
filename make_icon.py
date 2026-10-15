"""Generate app.ico from the same mic drawing the tray uses. Run at build time."""
from PIL import Image, ImageDraw


def mic(size, bg=(40, 44, 52)):
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # rounded background tile
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=bg)
    # scale mic drawing to the tile
    def sc(v):
        return int(v / 64 * s)
    d.rounded_rectangle([sc(26), sc(12), sc(38), sc(40)], radius=sc(6), fill="white")
    d.arc([sc(20), sc(30), sc(44), sc(50)], start=0, end=180, fill="white", width=max(2, sc(3)))
    d.line([sc(32), sc(50), sc(32), sc(56)], fill="white", width=max(2, sc(3)))
    d.line([sc(24), sc(56), sc(40), sc(56)], fill="white", width=max(2, sc(3)))
    return img


if __name__ == "__main__":
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [mic(s) for s in sizes]
    imgs[0].save("app.ico", sizes=[(s, s) for s in sizes], append_images=imgs[1:])
    print("wrote app.ico")
