"""OLED frames as images, in the same colours as sim/src/main.rs."""

from PIL import Image

OFF = (8, 10, 14)
ON = (120, 200, 255)


def image(pixels, scale=4):
    img = Image.new("RGB", ((128 + 2) * scale, (32 + 2) * scale), OFF)
    px = img.load()
    for y in range(32):
        for x in range(128):
            if pixels[y][x]:
                for dy in range(scale - 1):
                    for dx in range(scale - 1):
                        px[(x + 1) * scale + dx, (y + 1) * scale + dy] = ON
    return img


def save_frame(pixels, path, scale=4):
    image(pixels, scale).save(path)


def save_gif(frames, path, scale=3):
    """frames: [(seconds, pixels)], shown for as long as they were on screen."""
    kept = []
    for t, p in frames:
        if not kept or kept[-1][1] != p:
            kept.append((t, p))
    if not kept:
        return
    images = [image(p, scale) for _, p in kept]
    durations = [max(40, int((b[0] - a[0]) * 1000)) for a, b in zip(kept, kept[1:])] + [1500]
    images[0].save(path, save_all=True, append_images=images[1:], duration=durations, loop=0)
