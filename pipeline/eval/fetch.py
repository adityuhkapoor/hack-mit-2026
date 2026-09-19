"""Download the eval photo set from Lorem Picsum (images are from Unsplash, Unsplash License).

photos/ : natural-looking shots used for the synthetic ground-truth eval.
refs/   : already-stylized shots used as real "Instagram look" references in the contact sheet.
Attribution is written to credits.json next to the images.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
from pathlib import Path

import httpx

HERE = Path(__file__).parent
PHOTOS = [60, 63, 64, 74, 75, 76, 77, 85, 103, 106, 116, 118]
REFS = [65, 69, 82, 83, 89, 91, 95, 100, 101, 102, 111, 122]
WIDTH, HEIGHT = 1200, 800


def fetch(pid: int, dest: Path) -> dict:
    info = httpx.get(f"https://picsum.photos/id/{pid}/info", timeout=30).json()
    out = dest / f"{pid}.jpg"
    if not out.exists():
        r = httpx.get(f"https://picsum.photos/id/{pid}/{WIDTH}/{HEIGHT}", follow_redirects=True, timeout=60)
        r.raise_for_status()
        out.write_bytes(r.content)
    return {"id": pid, "file": str(out.relative_to(HERE)), "author": info["author"], "source": info["url"]}


def main() -> None:
    credits = []
    for name, ids in (("photos", PHOTOS), ("refs", REFS)):
        dest = HERE / name
        dest.mkdir(exist_ok=True)
        with cf.ThreadPoolExecutor(8) as ex:
            credits += list(ex.map(lambda i: fetch(i, dest), ids))
    (HERE / "credits.json").write_text(json.dumps(credits, indent=2))
    print(f"fetched {len(credits)} images")


if __name__ == "__main__":
    main()
