from pathlib import Path
from PIL import Image, ImageSequence

src = Path(__file__).parent / "assets" / "idle.gif"
out = Path(__file__).parent / "foamo.ico"

img = Image.open(src)
frame = next(ImageSequence.Iterator(img)).convert("RGBA")

w, h = frame.size
side = min(w, h)
left = (w - side) // 2
top = (h - side) // 2
frame = frame.crop((left, top, left + side, top + side))

sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
frame.save(out, format="ICO", sizes=sizes)
print(f"Saved: {out}")
