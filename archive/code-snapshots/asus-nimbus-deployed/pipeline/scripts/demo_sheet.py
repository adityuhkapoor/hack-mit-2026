"""Contact sheet for the demo: one row per sensor scenario, columns = as shot, Real, Sensed air, New world.

    uv run python scripts/demo_sheet.py ../docs/demo      # after the captures are downloaded there
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CELL_W, GAP, HEAD, LABEL = 560, 14, 96, 44
COLS = [("as_shot", 0, "As shot"), ("photo", 0, "Real"), ("photo", 1, "Sensed air"), ("photo", 2, "New world")]


def font(size):
    return ImageFont.load_default(size=size)


def readings_line(r):
    parts = [f"{r['temp_c']:.0f}°C", f"{r['rh']:.0f}% RH", f"{r['lux']:.0f} lux",
             f"wind {r['wind']:.1f} m/s", f"{r['db']:.0f} dB"]
    return "  ·  ".join(parts)


def main(folder: Path) -> None:
    scenarios = json.loads((folder / "scenarios.json").read_text())
    sample = Image.open(folder / f"{scenarios[0]['key']}_0_photo.jpg")
    cell_h = round(sample.height * CELL_W / sample.width)
    width = GAP + len(COLS) * (CELL_W + GAP)
    height = LABEL + len(scenarios) * (HEAD + cell_h + GAP) + GAP
    sheet = Image.new("RGB", (width, height), (14, 15, 18))
    d = ImageDraw.Draw(sheet)
    for i, (_, _, name) in enumerate(COLS):
        d.text((GAP + i * (CELL_W + GAP), 12), name, font=font(24), fill=(238, 240, 243))
    y = LABEL
    for s in scenarios:
        d.text((GAP, y + 12), f"{s['title']}, {s['note']}", font=font(26), fill=(238, 240, 243))
        d.text((GAP, y + 50), readings_line(s["readings"]), font=font(21), fill=(154, 163, 173))
        y += HEAD
        for i, (which, dial, _) in enumerate(COLS):
            im = Image.open(folder / f"{s['key']}_{dial}_{which}.jpg").convert("RGB").resize((CELL_W, cell_h), Image.LANCZOS)
            sheet.paste(im, (GAP + i * (CELL_W + GAP), y))
        y += cell_h + GAP
    sheet.save(folder / "sheet.jpg", quality=90)
    print(folder / "sheet.jpg", sheet.size)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "../docs/demo"))
