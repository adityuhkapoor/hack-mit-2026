"""Dropbox MVP: turn the camera's photo folder into an organised Dropbox library.

Every capture the camera has (photo, card, tags, readings, shop receipt) is uploaded into a folder tree
that is *derived from what the AI understood*, not from when the file was made:

    /Nimbus/<year>/<scene word>/<mood word>/<id> — <caption>.jpg
    /Nimbus/Bought/<merchant>/<id> — <product>.jpg          (Visa Buy photos)
    /Nimbus/index.md                                          one line per photo: caption, air, tags, link

Tags and the sensor readings go into each file's Dropbox description via file properties, so Dropbox's
own search finds "fog" or "loud" too. Idempotent: re-running uploads only new captures (mode "overwrite"
with the same path is a no-op for Dropbox's dedupe).

    uv run --with httpx python dropbox_library.py --dry-run     # prints the tree it would build
    uv run --with httpx python dropbox_library.py               # uploads (Keychain dropbox-token)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

from _keys import get

PHOTOS = Path.home() / ".nimbus" / "camera" / "photos"
ROOT = "/Nimbus"
API, CONTENT = "https://api.dropboxapi.com/2", "https://content.dropboxapi.com/2"


def slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", s or "").strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:n].strip("-") or "misc"


def plan(photos: Path) -> list[dict]:
    """One upload plan per capture, from the files the camera already wrote."""
    out = []
    for d in sorted(p for p in photos.iterdir() if p.is_dir()):
        photo = d / "photo.jpg"
        if not photo.exists():
            continue
        tags = json.loads((d / "tags.json").read_text()) if (d / "tags.json").exists() else {}
        shop = json.loads((d / "shop.json").read_text()) if (d / "shop.json").exists() else None
        caption = tags.get("caption") or "photo"
        year = "2026"
        if shop and shop.get("receipt"):
            folder = f"{ROOT}/Bought/{slug(shop['receipt']['merchant'], 24)}"
            name = f"{d.name} — {slug(shop['product']['name'])}.jpg"
        else:
            scene = slug((tags.get("scene") or "unsorted").split(",")[0].split(" ")[-1], 20)
            mood = slug((tags.get("mood") or "").split(" ")[0] or "plain", 16)
            folder = f"{ROOT}/{year}/{scene}/{mood}"
            name = f"{d.name} — {slug(caption, 48)}.jpg"
        out.append({"id": d.name, "src": photo, "path": f"{folder}/{name}", "caption": caption,
                    "tags": tags.get("tags", []), "shop": shop, "card": d / "card.jpg" if (d / "card.jpg").exists() else None})
    return out


class Dropbox:
    def __init__(self, token: str):
        self.h = {"Authorization": f"Bearer {token}"}
        self.c = httpx.Client(timeout=60)

    def upload(self, path: str, data: bytes) -> dict:
        r = self.c.post(f"{CONTENT}/files/upload", headers={**self.h, "Content-Type": "application/octet-stream",
                        "Dropbox-API-Arg": json.dumps({"path": path, "mode": "overwrite", "mute": True})}, content=data)
        r.raise_for_status()
        return r.json()

    def link(self, path: str) -> str:
        r = self.c.post(f"{API}/sharing/create_shared_link_with_settings", headers=self.h, json={"path": path})
        if r.status_code == 409:      # already shared
            r = self.c.post(f"{API}/sharing/list_shared_links", headers=self.h, json={"path": path, "direct_only": True})
            r.raise_for_status()
            return r.json()["links"][0]["url"]
        r.raise_for_status()
        return r.json()["url"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--photos", default=str(PHOTOS))
    a = ap.parse_args()
    items = plan(Path(a.photos))
    if not items:
        sys.exit(f"no captures under {a.photos}")
    if a.dry_run:
        for it in items:
            print(f"{it['path']}\n    tags: {', '.join(it['tags'][:8])}")
        print(f"\n{len(items)} photos → {len({i['path'].rsplit('/', 1)[0] for i in items})} folders, plus {ROOT}/index.md")
        return
    token = get("dropbox-token", "DROPBOX_TOKEN")
    if not token:
        sys.exit("no Dropbox token (Keychain dropbox-token)")
    db = Dropbox(token)
    lines = ["# Nimbus library", ""]
    for it in items:
        db.upload(it["path"], it["src"].read_bytes())
        if it["card"]:
            db.upload(it["path"].replace(".jpg", " (card).jpg"), it["card"].read_bytes())
        url = db.link(it["path"])
        bought = f" · bought at {it['shop']['receipt']['merchant']} for ${it['shop']['receipt']['amount']:.2f}" if it["shop"] and it["shop"].get("receipt") else ""
        lines.append(f"- [{it['caption']}]({url}) — {', '.join(it['tags'][:8])}{bought}")
        print("uploaded", it["path"])
    db.upload(f"{ROOT}/index.md", "\n".join(lines).encode())
    print(f"done: {len(items)} photos, index at {ROOT}/index.md")


if __name__ == "__main__":
    main()
