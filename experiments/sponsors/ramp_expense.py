"""Ramp MVP: file the camera's Visa purchase as an expense with the photo as the receipt.

Reads shop.json (the product, the offer and the Visa receipt the camera produced) and creates a Ramp
reimbursement with the photo attached, against Ramp's sandbox (demo-api.ramp.com, client-credentials
OAuth). --dry-run prints the exact request. A real run needs a Ramp sandbox app with the
`reimbursements:write` and `receipts:write` scopes.

    uv run --with httpx python ramp_expense.py <capture id> --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from _keys import get

PHOTOS = Path.home() / ".nimbus" / "camera" / "photos"
BASE = "https://demo-api.ramp.com/developer/v1"


def payload(capture: Path) -> dict:
    shop = json.loads((capture / "shop.json").read_text())
    r, p = shop.get("receipt"), shop["product"]
    if not r:
        sys.exit("this photo was never bought")
    return {"amount": r["amount"], "currency": r["currency"], "merchant": r["merchant"],
            "memo": f"{p['brand'] or ''} {p['name']} ({p.get('variant') or ''}) — Nimbus camera, Visa {r['network']} "
                    f"auth {r['auth_code']} txn {r['transaction_id']}".strip(),
            "transaction_date": r["when"][:10], "receipt": str(capture / "photo.jpg")}


def token(cid: str, secret: str) -> str:
    r = httpx.post(f"{BASE.rsplit('/developer', 1)[0]}/developer/v1/token", auth=(cid, secret),
                   data={"grant_type": "client_credentials", "scope": "reimbursements:write receipts:write"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    body = payload(PHOTOS / a.capture)
    if a.dry_run:
        print("POST", f"{BASE}/reimbursements"); print(json.dumps(body, indent=2)); return
    cid, sec = get("ramp-client-id", "RAMP_CLIENT_ID"), get("ramp-client-secret", "RAMP_CLIENT_SECRET")
    if not (cid and sec):
        sys.exit("no Ramp sandbox credentials (Keychain ramp-client-id / ramp-client-secret)")
    h = {"Authorization": f"Bearer {token(cid, sec)}"}
    receipt_path = body.pop("receipt")
    r = httpx.post(f"{BASE}/reimbursements", headers=h, json=body, timeout=30)
    r.raise_for_status()
    rid = r.json()["id"]
    up = httpx.post(f"{BASE}/receipts", headers=h, data={"reimbursement_id": rid},
                    files={"receipt": ("photo.jpg", Path(receipt_path).read_bytes(), "image/jpeg")}, timeout=60)
    up.raise_for_status()
    print("filed reimbursement", rid, "with receipt", up.json().get("id"))


if __name__ == "__main__":
    main()
