"""Turn the rendered lookbook into the static site's gallery.

Reads Photos/looks/manifest.json, writes web-sized JPEGs (a thumbnail and a full view for each
render) plus gallery.json into web/public/gallery, and copies the figures used on the page.

    uv run python scripts/build_site.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

Image.MAX_IMAGE_PIXELS = None
ROOT = Path(__file__).resolve().parents[2]
LOOKS = ROOT / "Photos" / "looks"
OUT = ROOT / "web" / "public" / "gallery"
FIGURES = ROOT / "web" / "public" / "figures"
FULL, THUMB = 1200, 420
TITLES = {
    "DSC00272-Enhanced-NR": "Grand Canyon, sunrise",
    "DSC06684-Enhanced-NR": "Egret over the reeds",
    "IMG_0223": "Campus lawn at dusk",
    "_DSC7022-Enhanced-NR": "Lavaux vineyards, Lake Geneva",
    "_DSC8307-Enhanced-NR": "Jungfrau in cloud",
    "_DSC8705-Enhanced-NR": "Brienz marina",
}


def save(src: Path, dest: Path, long_side: int, quality: int) -> dict:
    im = Image.open(src)
    im.draft("RGB", (long_side * 2, long_side * 2))
    im = im.convert("RGB")
    im.thumbnail((long_side, long_side), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, quality=quality, optimize=True, progressive=True)
    return {"w": im.size[0], "h": im.size[1]}


def main() -> None:
    manifest = json.loads((LOOKS / "manifest.json").read_text())
    if OUT.exists():
        shutil.rmtree(OUT)
    photos = []
    for rec in manifest:
        stem = Path(rec["photo"]).stem
        renders = []
        for r in rec["renders"]:
            src = LOOKS / r["file"]
            if not src.exists():
                continue
            name = Path(r["file"]).name
            size = save(src, OUT / stem / name, FULL, 74)
            save(src, OUT / stem / "thumb" / name, THUMB, 70)
            renders.append({"id": r["id"], "name": r["name"], "family": r["family"],
                            "description": r["description"], "file": f"{stem}/{name}", **size})
        photos.append({"id": stem, "title": TITLES.get(stem, stem), "renders": renders})
        print(f"{stem}: {len(renders)} renders")
    (OUT / "gallery.json").write_text(json.dumps({"photos": photos}, indent=1))

    FIGURES.mkdir(parents=True, exist_ok=True)
    scratch = Path("/private/tmp/claude-501/-Users-akvaithi-Developer-hackMIT/e5dda82e-7d2e-4415-bba8-b325d03be6a3/scratchpad")
    for src, dest in [(scratch / "faces2.jpg", "faces.jpg"), (scratch / "rt" / "rt_sheet3.jpg", "realtime.jpg")]:
        if src.exists():
            save(src, FIGURES / dest, 1600, 78)
            print("figure", dest)
    total = sum(f.stat().st_size for f in OUT.rglob("*.jpg"))
    print(f"gallery: {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
