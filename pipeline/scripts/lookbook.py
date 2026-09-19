"""Render every style (and a few stolen reference looks) on a folder of photos, for choosing a look.

Photos are first resized to what the camera's 12 MP sensor would capture, then rendered exactly as
the pipeline would, and saved at review size with a contact sheet and an index.html per run.

    uv run python scripts/lookbook.py ../Photos --out ../Photos/looks [--styles anime,digicam] [--long 3000]
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nimbus import effects, imageio, look  # noqa: E402
from nimbus.backends import Backends  # noqa: E402
from nimbus.styles import BY_ID, STYLES, StyleUnavailable, render_style  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
SENSOR_MP = 12.0
REFERENCE_LOOKS = {  # eval refs: already-stylized Unsplash photos standing in for Instagram posts
    "ref_crossprocess": "eval/refs/82.jpg",
    "ref_faded_green": "eval/refs/101.jpg",
    "ref_sepia": "eval/refs/111.jpg",
}


def to_sensor(img: np.ndarray, mp: float = SENSOR_MP) -> np.ndarray:
    h, w = img.shape[:2]
    s = (mp * 1e6 / (h * w)) ** 0.5
    return img if s >= 1 else effects._resize(img, round(w * s), round(h * s))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("photos", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--styles", default=",".join(s.id for s in STYLES))
    ap.add_argument("--refs", default=",".join(REFERENCE_LOOKS), help="stolen reference looks; '' for none")
    ap.add_argument("--only", default="", help="comma-separated photo stems")
    ap.add_argument("--long", type=int, default=3000, help="long side of saved images")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    paths = sorted(p for p in args.photos.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if args.only:
        paths = [p for p in paths if p.stem in args.only.split(",")]
    styles = [BY_ID[s] for s in args.styles.split(",") if s]
    comfy = Backends(timeout=240).pick() if any(s.needs_gpu for s in styles) else None
    print("GPU backend:", comfy.url if comfy else "none", flush=True)

    ref_luts = {}
    for key in filter(None, args.refs.split(",")):
        ref = imageio.load(root / REFERENCE_LOOKS[key])
        ref_luts[key] = (look.estimate_grade(ref, "neutral_fit"), root / REFERENCE_LOOKS[key])

    args.out.mkdir(parents=True, exist_ok=True)
    # Stable file numbering from the full catalog, so re-rendering a few styles replaces their files
    # and keeps the rest of an existing lookbook.
    order = {"original": 0, **{s.id: i + 1 for i, s in enumerate(STYLES)},
             **{k: len(STYLES) + 1 + i for i, k in enumerate(REFERENCE_LOOKS)}}
    manifest_path = args.out / "manifest.json"
    previous = {r["photo"]: r for r in json.loads(manifest_path.read_text())} if manifest_path.exists() else {}
    manifest = []
    for p in paths:
        t0 = time.perf_counter()
        photo = to_sensor(imageio.load(p))
        d = args.out / p.stem
        d.mkdir(exist_ok=True)
        entries = [("original", "Original (12 MP)", "original", "", photo if max(photo.shape[:2]) <= args.long
                    else effects._scale_to_long(photo, args.long), {})]
        for st in styles:
            try:
                img, tm = render_style(photo, st, comfy, seed=args.seed, out_long_side=args.long)
            except (StyleUnavailable, Exception) as e:  # keep the batch going; note what failed
                print(f"  {p.stem} {st.id}: FAILED {e}", flush=True)
                continue
            entries.append((st.id, st.name, st.family, st.description, img, tm))
            print(f"  {p.stem} {st.id}: {tm}", flush=True)
        for key, (lut, ref_path) in ref_luts.items():
            small = effects._scale_to_long(photo, args.long)
            img = look.grade.apply_lut(small, lut)
            entries.append((key, f"Stolen look ({ref_path.name})", "stolen", f"Grade estimated from {ref_path.name}, full strength", img, {}))

        renders = {r["id"]: r for r in previous.get(p.name, {}).get("renders", [])}
        for sid, name, fam, desc, img, tm in entries:
            fn = f"{order[sid]:02d}_{sid}.jpg"
            imageio.save(img, d / fn, quality=90)
            renders[sid] = {"file": f"{p.stem}/{fn}", "id": sid, "name": name, "family": fam,
                            "description": desc, "timings": tm}
        manifest.append({"photo": p.name, "renders": sorted(renders.values(), key=lambda r: order.get(r["id"], 99))})
        previous.pop(p.name, None)
        print(f"{p.name}: {len(entries)} images in {time.perf_counter() - t0:.0f}s", flush=True)

    manifest += list(previous.values())  # photos not re-rendered this run
    manifest.sort(key=lambda r: r["photo"])
    refs_html = "".join(
        f'<figure><img src="{html.escape(str((root / r).resolve().as_uri()))}"><figcaption>{html.escape(k)}</figcaption></figure>'
        for k, r in REFERENCE_LOOKS.items() if any(x["id"] == k for rec in manifest for x in rec["renders"]))
    blocks = []
    for rec in manifest:
        figs = "".join(
            f'<figure><a href="{html.escape(r["file"])}"><img loading="lazy" src="{html.escape(r["file"])}"></a>'
            f'<figcaption><b>{html.escape(r["name"])}</b> <span class="fam">{r["family"]}</span><br>'
            f'{html.escape(r["description"])}</figcaption></figure>' for r in rec["renders"])
        blocks.append(f'<h2>{html.escape(rec["photo"])}</h2><div class="grid">{figs}</div>')
    (args.out / "index.html").write_text(f"""<!doctype html><meta charset="utf-8"><title>Lookbook</title>
<style>
body{{font:14px/1.4 system-ui;margin:24px;background:#111;color:#eee}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:14px}}
figure{{margin:0}} img{{width:100%;border-radius:6px;display:block}}
figcaption{{color:#bbb;margin-top:4px}} .fam{{font-size:11px;padding:1px 6px;border-radius:8px;background:#333;color:#9cf}}
.refs figure{{max-width:240px;display:inline-block;margin-right:10px}}
</style>
<h1>Lookbook</h1><p>Photos resized to a 12 MP sensor, rendered by the Nimbus pipeline, saved at {args.long}px.
Families: <b>grade</b> = LUT only · <b>camera</b> = grade + procedural optics/sensor artifacts · <b>reimagine</b> =
FLUX.2 klein diffusion (+ ESRGAN, + procedural finishing) · <b>stolen</b> = grade reverse-engineered from a reference photo.</p>
<div class="refs">{refs_html}</div>
{''.join(blocks)}""")
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote", args.out / "index.html")


if __name__ == "__main__":
    main()
