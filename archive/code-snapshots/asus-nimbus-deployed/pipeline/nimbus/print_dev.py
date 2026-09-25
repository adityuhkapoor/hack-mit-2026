"""A small dev page for the printer: PRINT (the latest photo as a 3x4 polaroid) and PRINT LINES (a cutting guide).

Run it on the machine the printer is plugged into. It is separate from the main API, listens on localhost only, and
prints through nimbus.printing like everything else:

    NIMBUS_PRINTER=Epson_XP4200 NIMBUS_PRINT_MEDIA=PhotographicSemiGloss NIMBUS_CAPTURES=~/nimbus/captures \\
        .venv/bin/python -m uvicorn nimbus.print_dev:app --host 127.0.0.1 --port 8790

then open http://127.0.0.1:8790 (over an SSH tunnel: ssh -L 8790:127.0.0.1:8790 asus@...).

PRINT LINES prints one 4x6 sheet with a faint dotted line across its middle; cut along it and load the two 3x4 halves
for PRINT.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from . import polaroid, printing

COOLDOWN = 4.0            # seconds between prints: a double click must not spend two sheets
app = FastAPI(title="Nimbus print (dev)")
_lock = threading.Lock()
_last_print = 0.0
_preview: dict[str, bytes] = {}
Quality = Literal["draft", "normal", "high"]     # the page defaults to draft: the quickest the printer does


def captures_root() -> Path:
    return Path(os.environ.get("NIMBUS_CAPTURES", Path(__file__).resolve().parents[1] / "captures")).expanduser()


def latest_photo() -> Path | None:
    """The newest stored capture's photo.jpg (what the pipeline made), or None when there are none."""
    photos = [p for p in captures_root().glob("*/photo.jpg") if p.is_file()]
    return max(photos, key=lambda p: p.stat().st_mtime, default=None)


def _capture_info(photo: Path | None) -> dict | None:
    if photo is None:
        return None
    return {"id": photo.parent.name, "at": time.strftime("%H:%M:%S", time.localtime(photo.stat().st_mtime)),
            "version": f"{photo.parent.name}-{photo.stat().st_mtime_ns}"}


def _claim() -> None:
    """One print at a time, and not again within COOLDOWN."""
    global _last_print
    if not _lock.acquire(blocking=False):
        raise HTTPException(429, "a print is already being sent")
    if time.monotonic() - _last_print < COOLDOWN:
        _lock.release()
        raise HTTPException(429, "wait a moment: a sheet was just sent")


def _done(sent: bool) -> None:
    global _last_print
    if sent:
        _last_print = time.monotonic()
    _lock.release()


def _queued() -> int:
    """Jobs the queue still holds (waiting or printing)."""
    name = printing.queue()
    try:
        out = printing._run(["lpstat", "-o", name]).stdout if name else ""
    except printing.PrintError:
        return 0
    return sum(1 for line in out.splitlines() if line.startswith(f"{name}-"))


@app.get("/state")
def state():
    try:
        printer = printing.status()
    except printing.PrintError as e:
        printer = {"enabled": False, "reason": str(e)}
    return {"printer": printer, "queued": _queued(), "max_copies": printing.MAX_COPIES,
            "capture": _capture_info(latest_photo())}


@app.get("/preview.jpg")
def preview():
    """What PRINT will put on the page: the latest photo in the full-bleed 3x4 polaroid."""
    photo = latest_photo()
    if photo is None:
        raise HTTPException(404, "no captures yet")
    key = _capture_info(photo)["version"]
    if key not in _preview:
        _preview.clear()
        _preview[key] = polaroid.sheet_jpeg(photo.read_bytes(), "polaroid1full")
    return Response(_preview[key], media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/print")
def print_photo(copies: int = 1, quality: Quality = "draft"):
    photo = latest_photo()
    if photo is None:
        raise HTTPException(404, "no captures yet")
    _claim()
    sent = False
    try:
        job = printing.submit(photo, title=f"nimbus-{photo.parent.name}", layout="polaroid1full",
                             copies=copies, quality=quality)
        sent = True
    except printing.PrintError as e:
        raise HTTPException(e.status, str(e))
    finally:
        _done(sent)
    return {**job, "what": "polaroid", "capture": photo.parent.name}


@app.post("/print-lines")
def print_lines(copies: int = 1, quality: Quality = "draft"):
    _claim()
    sent = False
    sheet = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", prefix="nimbus-cut-", delete=False) as f:
            sheet = Path(f.name)
            f.write(polaroid.cut_sheet_jpeg())
        job = printing.submit(sheet, title="nimbus-cut-lines", layout="single", size="4x6", borderless=True,
                             copies=copies, quality=quality)
        sent = True
    except printing.PrintError as e:
        raise HTTPException(e.status, str(e))
    finally:
        if sheet is not None:
            sheet.unlink(missing_ok=True)
        _done(sent)
    return {**job, "what": "cut lines"}


@app.get("/", response_class=HTMLResponse)
def page():
    return PAGE


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nimbus print</title>
<style>
:root{--bg:#f6f6f4;--card:#fff;--ink:#1b2b32;--mute:#66757c;--line:#dfe3e4;--go:#1b2b32;--go-ink:#fff;--ok:#2e7d4f;--bad:#b3392f}
@media (prefers-color-scheme:dark){:root{--bg:#12191c;--card:#1a2327;--ink:#e8eef0;--mute:#8fa0a7;--line:#2b3940;--go:#e8eef0;--go-ink:#12191c;--ok:#5cc286;--bad:#ef7b70}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 48px}
header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px}
h1{font-size:20px;margin:0}
.state{display:flex;align-items:center;gap:8px;color:var(--mute);font-size:14px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--mute)}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}
.grid{display:grid;grid-template-columns:minmax(0,300px) 1fr;gap:20px;align-items:start}
@media (max-width:640px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.shot{width:100%;aspect-ratio:3/4;object-fit:contain;border-radius:6px;background:#fff;border:1px solid var(--line);display:block}
.cap{color:var(--mute);font-size:13px;margin:8px 2px 0}
.actions{display:grid;gap:14px}
.opts{display:flex;flex-wrap:wrap;gap:18px 28px;align-items:center;margin-bottom:16px}
.opts label,.opts .lab{font-size:13px;color:var(--mute);display:block;margin-bottom:6px}
.seg,.step{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--card)}
.seg button,.step button{width:auto;padding:9px 14px;border:0;border-radius:0;font-weight:500;background:transparent;text-align:center}
.seg button+button,.step button+button,.step output+button{border-left:1px solid var(--line)}
.seg button[aria-pressed=true]{background:var(--go);color:var(--go-ink)}
.step output{min-width:44px;display:flex;align-items:center;justify-content:center;font-weight:600;border-left:1px solid var(--line)}
button{width:100%;font:inherit;font-weight:600;padding:16px 18px;border-radius:12px;border:1px solid var(--line);
 background:var(--card);color:var(--ink);cursor:pointer;text-align:left}
button.go{background:var(--go);color:var(--go-ink);border-color:var(--go)}
button:disabled{opacity:.5;cursor:wait}
button small{display:block;font-weight:400;opacity:.8;margin-top:2px;font-size:13px}
#log{margin-top:16px;font-size:14px;min-height:1.4em}
#log.ok{color:var(--ok)}#log.bad{color:var(--bad)}
</style></head>
<body><main>
<header><h1>Nimbus print <span style="color:var(--mute);font-weight:400">dev</span></h1>
<div class="state"><span class="dot" id="dot"></span><span id="pstate">checking printer…</span></div></header>
<div class="grid">
 <div class="card"><img class="shot" id="shot" alt="preview of the next print" hidden>
  <p class="cap" id="cap">No captures yet.</p></div>
 <div>
  <div class="opts">
   <div><span class="lab">Copies</span>
    <div class="step"><button id="minus" aria-label="fewer copies">&minus;</button><output id="copies">1</output><button id="plus" aria-label="more copies">+</button></div></div>
   <div><span class="lab">Speed</span>
    <div class="seg" id="speed"><button data-q="draft">Fast</button><button data-q="normal">Normal</button><button data-q="high">Best</button></div></div>
  </div>
  <div class="actions">
   <button class="go" id="print"><span>Print</span><small>The latest photo as a full 3 &times; 4 in polaroid. Load a 3 &times; 4 page.</small></button>
   <button id="lines"><span>Print lines</span><small>A faint dotted cut line across the middle of a 4 &times; 6 in sheet. Load a 4 &times; 6 sheet, cut along the line, and use the halves for Print.</small></button>
  </div>
  <div id="log" role="status" aria-live="polite"></div>
 </div>
</div>
</main>
<script>
const $=id=>document.getElementById(id);
let version=null,copies=1,maxCopies=10,quality="draft";
try{copies=+localStorage.copies||1;quality=localStorage.quality||"draft"}catch(e){}
function paint(){
 $("copies").textContent=copies;
 document.querySelectorAll("#speed button").forEach(b=>b.setAttribute("aria-pressed",b.dataset.q===quality));
 try{localStorage.copies=copies;localStorage.quality=quality}catch(e){}
}
$("minus").onclick=()=>{copies=Math.max(1,copies-1);paint()};
$("plus").onclick=()=>{copies=Math.min(maxCopies,copies+1);paint()};
document.querySelectorAll("#speed button").forEach(b=>b.onclick=()=>{quality=b.dataset.q;paint()});
paint();
function say(text,cls){const l=$("log");l.textContent=text;l.className=cls||""}
async function refresh(){
 try{
  const s=await (await fetch("state",{cache:"no-store"})).json();
  const p=s.printer;maxCopies=s.max_copies||10;
  $("dot").className="dot "+(p.enabled&&p.ready?"ok":"bad");
  $("pstate").textContent=!p.enabled?"printing is off ("+(p.reason||"")+")":!p.ready?(p.reason||"printer not ready"):"printer "+(p.state||"ready")+" · "+p.queue+(s.queued?" · "+s.queued+" in queue":"");
  const c=s.capture;
  if(!c){$("shot").hidden=true;$("cap").textContent="No captures yet.";version=null;return}
  $("cap").textContent="Latest capture "+c.id+" · "+c.at;
  if(c.version!==version){version=c.version;$("shot").src="preview.jpg?v="+encodeURIComponent(c.version);$("shot").hidden=false}
 }catch(e){$("dot").className="dot bad";$("pstate").textContent="server not reachable"}
}
async function send(path,button,label){
 const buttons=[$("print"),$("lines")];buttons.forEach(b=>b.disabled=true);say("Sending "+label+"…");
 try{
  const r=await fetch(path+"?copies="+copies+"&quality="+quality,{method:"POST"});const j=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(j.detail||("error "+r.status));
  say("Sent "+(copies>1?copies+" copies of ":"")+label+" · job "+(j.job||"?"),"ok");
 }catch(e){say(e.message,"bad")}
 setTimeout(()=>{buttons.forEach(b=>b.disabled=false)},4000);refresh();
}
$("print").onclick=()=>send("print",$("print"),"the polaroid");
$("lines").onclick=()=>send("print-lines",$("lines"),"the cut lines");
refresh();setInterval(refresh,5000);
</script></body></html>
"""
