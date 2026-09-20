"""Shop: photograph a thing, the camera names it exactly, finds it for sale, and you buy it with Visa.

Three steps, each honest about what is real:

    identify(jpeg)   Muse Spark looks at the photo and names the product (brand, name, variant, size).
    find(product)    A live search: Open Food Facts for food and drink (the exact product, size and image)
                     and a web search for the rest; Muse turns the results into one to three offers with
                     merchant, price and link. No keys, nothing pre-canned.
    checkout(offer)  Payment. With Visa Developer sandbox credentials in the Keychain it is a real call to
                     the sandbox (Visa Direct pull-funds); otherwise `SimulatedVisa` approves it locally and
                     says so on the receipt. Nothing here ever moves real money.

Results are cached beside the photo (shop.json) so a second "what is this" costs nothing.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from . import keys, tagger

IDENTIFY_PROMPT = """Name the product in this photograph as precisely as a shop listing would.
Reply with JSON only:
{"name": "<product name as sold, e.g. 'Red Bull Energy Drink'>",
 "brand": "<brand or null>",
 "variant": "<flavour, colour, model or size, e.g. '8.4 fl oz can', or null>",
 "category": "<one of: food, drink, electronics, clothing, book, toy, sports, beauty, home, other>",
 "confidence": <0 to 1>,
 "search_query": "<the exact words to search a shop for it>"}
If there is no buyable product, use confidence 0 and describe what is there in name."""

OFFERS_PROMPT = """A shopper photographed: {product}.
Below are live search results. Pick the results that sell exactly this product (same brand and variant) and
turn them into offers. Reply with JSON only:
{{"offers": [{{"merchant": "<store name>", "title": "<listing title>", "price": <number>,
              "estimated": <true if the price is not in the result text>, "currency": "USD", "url": "<the result url>"}}]}}
At most 3 offers, best first (a listed price beats an estimate, a known US retailer beats a marketplace).
When the result gives no price, put your best estimate of the usual US price for that exact item and size
and set estimated to true. If nothing matches, return an empty list.

{results}"""


@dataclass
class Product:
    name: str
    brand: str | None = None
    variant: str | None = None
    category: str = "other"
    confidence: float = 0.0
    search_query: str = ""
    image_url: str | None = None      # from Open Food Facts when it knows the product

    def label(self) -> str:
        name = self.name if not self.brand or self.brand.lower() in self.name.lower() else f"{self.brand} {self.name}"
        return f"{name}, {self.variant}" if self.variant and self.variant.lower() not in name.lower() else name


@dataclass
class Offer:
    merchant: str
    title: str
    url: str
    price: float | None = None
    currency: str = "USD"
    estimated: bool = False           # the price was not on the listing; Muse's guess at the usual one

    def price_text(self) -> str:
        if self.price is None:
            return "price on the page"
        return f"~${self.price:.2f}" if self.estimated else f"${self.price:.2f}"


@dataclass
class Receipt:
    approved: bool
    amount: float
    currency: str
    merchant: str
    last4: str
    auth_code: str
    transaction_id: str
    network: str                      # "Visa sandbox" or "simulated Visa"
    when: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    message: str = ""


# ---------------------------------------------------------------------------------------------
# identify


def identify(jpeg: bytes) -> Product:
    client = tagger._client()
    if client is None:
        raise RuntimeError("no Muse key: the camera cannot name the product")
    r = client.chat.completions.create(model=tagger.MODEL, max_tokens=1200, reasoning_effort=tagger.REASONING,
                                       messages=[{"role": "user", "content": [
                                           {"type": "text", "text": IDENTIFY_PROMPT},
                                           {"type": "image_url", "image_url": {"url": tagger._data_url(jpeg)}}]}])
    d = _json(r.choices[0].message.content or "")
    return Product(name=str(d.get("name") or "something")[:80], brand=_opt(d.get("brand")),
                   variant=_opt(d.get("variant")), category=str(d.get("category") or "other").lower(),
                   confidence=float(d.get("confidence") or 0), search_query=str(d.get("search_query") or "")[:120])


# ---------------------------------------------------------------------------------------------
# find


def open_food_facts(query: str) -> dict | None:
    """The exact packaged product, when it is food or drink: name, brand, quantity, image."""
    try:
        r = httpx.get("https://search.openfoodfacts.org/search",       # the old cgi/search.pl 503s
                      params={"q": query, "page_size": 3, "fields": "product_name,brands,quantity,image_front_url,code"},
                      headers={"User-Agent": "Nimbus camera (HackMIT 2026)"}, timeout=12, follow_redirects=True)
        r.raise_for_status()
        for p in r.json().get("hits", []):
            if p.get("product_name"):
                if isinstance(p.get("brands"), list):
                    p["brands"] = ", ".join(p["brands"])
                return p
    except Exception as e:
        print(f"[shop] Open Food Facts unavailable ({type(e).__name__})")
    return None


def web_search(query: str, n: int = 8) -> list[dict]:
    """Live web results (DuckDuckGo via ddgs, no key). Each: title, href, body."""
    try:
        from ddgs import DDGS
        return list(DDGS().text(f"{query} buy price", max_results=n))
    except Exception as e:
        print(f"[shop] web search unavailable ({type(e).__name__}: {e})")
        return []


def find(product: Product) -> list[Offer]:
    query = product.search_query or product.label()
    if product.category in ("food", "drink"):
        off = open_food_facts(query)
        if off:
            product.image_url = off.get("image_front_url")
            if off.get("quantity") and not product.variant:
                product.variant = off["quantity"]
    results = web_search(query)
    if not results:
        return []
    client = tagger._client()
    if client is None:
        return [Offer(merchant=_host(r["href"]), title=r["title"], url=r["href"]) for r in results[:3]]
    text = "\n".join(f"- {r['title']} | {r['href']} | {r.get('body', '')[:200]}" for r in results)
    r = client.chat.completions.create(model=tagger.MODEL, max_tokens=2500, reasoning_effort=tagger.REASONING,   # it reasons over the results first; 600 starved the answer
                                       messages=[{"role": "user", "content": OFFERS_PROMPT.format(
                                           product=product.label(), results=text)}])
    offers = []
    for o in _json(r.choices[0].message.content or "").get("offers", [])[:3]:
        try:
            price = float(o["price"]) if o.get("price") not in (None, "") else None
        except (TypeError, ValueError):
            price = None
        if o.get("url"):
            offers.append(Offer(merchant=str(o.get("merchant") or _host(o["url"]))[:40], title=str(o.get("title", ""))[:100],
                                url=str(o["url"]), price=price, currency=str(o.get("currency") or "USD")[:3],
                                estimated=bool(o.get("estimated", False))))
    return offers


# ---------------------------------------------------------------------------------------------
# checkout


class SimulatedVisa:
    """Approves locally. The receipt says "simulated", never pretends to be the network."""

    network = "simulated Visa"
    last4 = "4242"

    def charge(self, offer: Offer, amount: float) -> Receipt:
        return Receipt(approved=True, amount=amount, currency=offer.currency, merchant=offer.merchant, last4=self.last4,
                       auth_code=f"{random.randint(0, 999999):06d}", transaction_id=f"sim-{int(time.time())}",
                       network=self.network, message="Simulated approval; no money moved.")


class VisaSandbox:
    """Visa Developer Platform sandbox, Visa Direct pull funds. Needs a project there: its user id and
    password (Keychain `visa-sandbox-user` / `visa-sandbox-password`) and the mutual-TLS certificate and key
    it issued, at ~/.nimbus/visa/cert.pem and key.pem (NIMBUS_VISA_DIR to move them). Sandbox test card."""

    network = "Visa sandbox"
    URL = "https://sandbox.api.visa.com/visadirect/fundstransfer/v1/pullfundstransactions"
    TEST_PAN = "4895142232120006"     # Visa's published sandbox test card

    def __init__(self, user: str, password: str, cert_dir: Path):
        self.auth = (user, password)
        self.cert = (str(cert_dir / "cert.pem"), str(cert_dir / "key.pem"))

    @classmethod
    def available(cls) -> "VisaSandbox | None":
        user, pw = keys.get("visa_user"), keys.get("visa_password")
        d = Path(os.environ.get("NIMBUS_VISA_DIR", Path.home() / ".nimbus" / "visa"))
        if user and pw and (d / "cert.pem").exists() and (d / "key.pem").exists():
            return cls(user, pw, d)
        return None

    def charge(self, offer: Offer, amount: float) -> Receipt:
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        stan = random.randint(100000, 999999)
        body = {"systemsTraceAuditNumber": stan, "retrievalReferenceNumber": time.strftime("%y%j%H") + f"{stan:06d}",
                "localTransactionDateTime": now, "acquiringBin": 408999, "acquirerCountryCode": "840",
                "senderPrimaryAccountNumber": self.TEST_PAN, "senderCardExpiryDate": "2030-10",
                "senderCurrencyCode": "USD", "amount": f"{amount:.2f}", "businessApplicationId": "AA",
                "cardAcceptor": {"name": offer.merchant[:25], "terminalId": "NIMBUS01", "idCode": "NIMBUSCAM",
                                 "address": {"country": "USA", "zipCode": "02139", "state": "MA"}}}
        with httpx.Client(cert=self.cert, auth=self.auth, timeout=30) as c:
            r = c.post(self.URL, json=body, headers={"Accept": "application/json"})
        ok = r.status_code == 200
        d = r.json() if ok else {}
        return Receipt(approved=ok and str(d.get("actionCode")) == "00", amount=amount, currency="USD",
                       merchant=offer.merchant, last4=self.TEST_PAN[-4:], auth_code=str(d.get("approvalCode", "")),
                       transaction_id=str(d.get("transactionIdentifier", "")), network=self.network,
                       message="Visa sandbox approval (test card, no money moved)." if ok else f"Visa sandbox said {r.status_code}: {r.text[:120]}")


def payments():
    return VisaSandbox.available() or SimulatedVisa()


def checkout(offer: Offer) -> Receipt:
    amount = offer.price if offer.price is not None else 0.0
    return payments().charge(offer, amount)


# ---------------------------------------------------------------------------------------------
# cache and helpers


def load(photo_dir: Path) -> dict | None:
    f = photo_dir / "shop.json"
    return json.loads(f.read_text()) if f.exists() else None


def save(photo_dir: Path, product: Product, offers: list[Offer], receipt: Receipt | None = None) -> None:
    (photo_dir / "shop.json").write_text(json.dumps(
        {"product": asdict(product), "offers": [asdict(o) for o in offers],
         "receipt": asdict(receipt) if receipt else None}, indent=2))


def _json(text: str) -> dict:
    import re
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def _opt(v) -> str | None:
    return None if v in (None, "", "null") else str(v)[:60]


def _host(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.removeprefix("www.")
