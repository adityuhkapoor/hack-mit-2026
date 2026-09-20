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
from jwcrypto.common import json_encode

from . import diag, keys, tagger

log = diag.get("shop")

IDENTIFY_PROMPT = """Name the thing in this photograph as precisely as a shop listing would, and suggest what a
shopper who wants it might also buy.
Reply with JSON only:
{"name": "<product name as sold, e.g. 'Red Bull Energy Drink'; for a prepared dish its name, e.g. 'chicken pad thai'>",
 "brand": "<brand or null>",
 "variant": "<flavour, colour, model or size, e.g. '8.4 fl oz can', or null>",
 "category": "<one of: dish, food, drink, electronics, clothing, book, toy, sports, beauty, home, other>",
 "confidence": <0 to 1>,
 "search_query": "<the exact words to search a shop for it>",
 "related": [{"name": "<a related product>", "search_query": "<words to search for it>",
              "why": "<'alternative' | 'goes with' | 'ingredient' | 'make it at home'>"}]}
category "dish" is cooked food on a plate or in a bowl (order it for delivery); "food" is a packaged
grocery item. Give 2 or 3 related products: for a dish, a way to order it and its key ingredient or a kit
to make it; for a drink, its other flavour and a snack; for a gadget, its accessory.
If there is no buyable product, use confidence 0, describe what is there in name, and related = []."""

OFFERS_PROMPT = """A shopper photographed: {product}. They may also want the related items listed below.
For each item there are live search results. Pick the results that sell exactly that item and turn them
into offers. Reply with JSON only:
{{"offers": [{{"item": <item number>, "merchant": "<store name>", "title": "<listing title>", "price": <number>,
              "estimated": <true if the price is not in the result text>, "currency": "USD", "url": "<the result url>"}}]}}
Up to 3 offers for item 0 and 1 or 2 for each other item, best first within an item (a single unit beats a
case or multipack, a listed price beats an estimate, a known US retailer or delivery service beats a
marketplace). If a listing is a multipack, say so in the title ("24-pack") and give the pack price. When the result gives no price, put
your best estimate of the usual US price for that item and set estimated to true. Skip an item with no match.

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
    related: list = field(default_factory=list)    # [{"name", "search_query", "why"}]

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
    item: str = ""                    # what this offer is for: the product itself or a related item
    why: str = "this"                 # "this" | "alternative" | "goes with" | "ingredient" | "make it at home"
    image_url: str | None = None      # a catalogue picture of the item

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
    related = [{"name": str(x.get("name", ""))[:60], "search_query": str(x.get("search_query") or x.get("name", ""))[:120],
                "why": str(x.get("why") or "goes with")[:20]}
               for x in (d.get("related") or []) if isinstance(x, dict) and x.get("name")][:3]
    return Product(name=str(d.get("name") or "something")[:80], brand=_opt(d.get("brand")),
                   variant=_opt(d.get("variant")), category=str(d.get("category") or "other").lower(),
                   confidence=float(d.get("confidence") or 0), search_query=str(d.get("search_query") or "")[:120],
                   related=related)


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
        diag.caught(log, "Open Food Facts unavailable", e)
    return None


def web_search(query: str, n: int = 8) -> list[dict]:
    """Live web results (DuckDuckGo via ddgs, no key). Each: title, href, body."""
    try:
        from ddgs import DDGS
        return list(DDGS().text(f"{query} buy price", max_results=n))
    except Exception as e:
        diag.caught(log, "web search unavailable", e)
        return []


def product_image(query: str) -> str | None:
    """A catalogue-style picture of the product (web image search, no key)."""
    try:
        from ddgs import DDGS
        for r in DDGS().images(f"{query} product", max_results=5):
            if r.get("image", "").startswith("http"):
                return r["image"]
    except Exception as e:
        diag.caught(log, "image search unavailable", e)
    return None


def fetch_image(url: str, dest: Path) -> Path | None:
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 Nimbus camera"})
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest
    except Exception:
        return None


DELIVERY_NEAR = os.environ.get("NIMBUS_DELIVERY_NEAR", "Cambridge MA")


def find(product: Product) -> list[Offer]:
    """Offers for the product and for its related items, the product's first. One web search per item
    (in parallel) and one Muse call to turn all the results into offers."""
    from concurrent.futures import ThreadPoolExecutor
    query = product.search_query or product.label()
    if product.category in ("food", "drink"):
        off = open_food_facts(query)
        if off:
            product.image_url = off.get("image_front_url")
            if off.get("quantity") and not product.variant:
                product.variant = off["quantity"]
    items = [{"name": product.label(), "why": "this",
              "search_query": f"order {query} delivery near {DELIVERY_NEAR}" if product.category == "dish" else query}]
    items += [{"name": x["name"], "why": x["why"], "search_query": x["search_query"]} for x in product.related]
    with ThreadPoolExecutor(max_workers=4) as pool:
        searches = list(pool.map(lambda it: web_search(it["search_query"], 8 if it["why"] == "this" else 4), items))
        if not product.image_url:
            product.image_url = product_image(query)
    if not any(searches):
        return []
    client = tagger._client()
    if client is None:
        return [Offer(merchant=_host(r["href"]), title=r["title"], url=r["href"], item=items[0]["name"]) for r in searches[0][:3]]
    text = "\n\n".join(f"item {k}: {it['name']} ({it['why']})\n" +
                       "\n".join(f"- {r['title']} | {r['href']} | {r.get('body', '')[:160]}" for r in res)
                       for k, (it, res) in enumerate(zip(items, searches)) if res)
    r = client.chat.completions.create(model=tagger.MODEL, max_tokens=3500, reasoning_effort=tagger.REASONING,   # it reasons over the results first; 600 starved the answer
                                       messages=[{"role": "user", "content": OFFERS_PROMPT.format(
                                           product=product.label(), results=text)}])
    offers = []
    for o in _json(r.choices[0].message.content or "").get("offers", [])[:9]:
        try:
            price = float(o["price"]) if o.get("price") not in (None, "") else None
        except (TypeError, ValueError):
            price = None
        try:
            k = int(o.get("item", 0))
        except (TypeError, ValueError):
            k = 0
        it = items[k] if 0 <= k < len(items) else items[0]
        if o.get("url"):
            offers.append(Offer(merchant=str(o.get("merchant") or _host(o["url"]))[:40], title=str(o.get("title", ""))[:100],
                                url=str(o["url"]), price=price, currency=str(o.get("currency") or "USD")[:3],
                                estimated=bool(o.get("estimated", False)), item=it["name"], why=it["why"]))
    offers.sort(key=lambda o: 0 if o.why == "this" else 1)       # stable: the product first, then related
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
        # Message Level Encryption: mandatory on the payment APIs (without it: 9125 "expected input
        # credential was not present"). Visa's public cert encrypts our body; our MLE key decrypts theirs.
        self.mle_key_id = (cert_dir / "mle_key_id.txt").read_text().strip() if (cert_dir / "mle_key_id.txt").exists() else None
        self.mle_server_cert = next(iter(sorted(cert_dir.glob("server_cert*.pem"))), None)
        self.mle_private_key = next(iter(sorted(cert_dir.glob("mle_key*.pem"))), None)

    @property
    def mle(self) -> bool:
        return bool(self.mle_key_id and self.mle_server_cert and self.mle_private_key)

    def _encrypt(self, body: dict) -> dict:
        from jwcrypto import jwe, jwk
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        cert = x509.load_pem_x509_certificate(self.mle_server_cert.read_bytes())
        pub = jwk.JWK.from_pem(cert.public_key().public_bytes(serialization.Encoding.PEM,
                                                              serialization.PublicFormat.SubjectPublicKeyInfo))
        token = jwe.JWE(json.dumps(body).encode(), json_encode({"alg": "RSA-OAEP-256", "enc": "A128GCM",
                                                                  "kid": self.mle_key_id, "iat": int(time.time() * 1000)}))
        token.add_recipient(pub)
        return {"encData": token.serialize(compact=True)}

    def _decrypt(self, text: str) -> dict:
        from jwcrypto import jwe, jwk
        d = json.loads(text)
        if "encData" not in d:
            return d
        key = jwk.JWK.from_pem(self.mle_private_key.read_bytes())
        token = jwe.JWE()
        token.deserialize(d["encData"], key=key)
        return json.loads(token.payload)

    def post(self, url: str, body: dict) -> tuple[int, dict]:
        headers = {"Accept": "application/json"}
        if self.mle:
            headers["keyId"] = self.mle_key_id
            body = self._encrypt(body)
        with httpx.Client(cert=self.cert, auth=self.auth, timeout=30) as c:
            r = c.post(url, json=body, headers=headers)
        try:
            data = self._decrypt(r.text) if self.mle else r.json()
        except Exception:
            data = {"raw": r.text[:300]}
        return r.status_code, data

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
        # RRN is exactly 12 digits: ydddhh + the 6-digit STAN
        body = {"systemsTraceAuditNumber": stan, "retrievalReferenceNumber": time.strftime("%y%j%H")[1:] + f"{stan:06d}",
                "localTransactionDateTime": now, "acquiringBin": 408999, "acquirerCountryCode": "840",
                "senderPrimaryAccountNumber": self.TEST_PAN, "senderCardExpiryDate": "2030-10",
                "senderCurrencyCode": "USD", "amount": f"{amount:.2f}", "businessApplicationId": "AA",
                "cardAcceptor": {"name": offer.merchant[:25], "terminalId": "NIMBUS01", "idCode": "NIMBUSCAM",
                                 "address": {"country": "USA", "zipCode": "02139", "state": "MA"}}}
        status, d = self.post(self.URL, body)
        ok = status == 200
        err = (d.get("responseStatus") or {}).get("message") or d.get("raw") or str(d)[:120]
        return Receipt(approved=ok and str(d.get("actionCode")) == "00", amount=amount, currency="USD",
                       merchant=offer.merchant, last4=self.TEST_PAN[-4:], auth_code=str(d.get("approvalCode", "")),
                       transaction_id=str(d.get("transactionIdentifier", "")), network=self.network,
                       message="Visa sandbox approval (test card, no money moved)." if ok else f"Visa sandbox said {status}: {err}")


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


def slug(text: str, n: int = 40) -> str:
    import re
    t = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    return re.sub(r"[\s_-]+", "-", t)[:n].strip("-") or "item"


def _json(text: str) -> dict:
    import re
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def _opt(v) -> str | None:
    return None if v in (None, "", "null") else str(v)[:60]


def _host(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.removeprefix("www.")


if __name__ == "__main__":       # uv run python -m nimbus_cam.shop  → which payment backend, and is the sandbox reachable
    pay = payments()
    print(f"payments: {pay.network}")
    if isinstance(pay, VisaSandbox):
        with httpx.Client(cert=pay.cert, auth=pay.auth, timeout=30) as c:
            r = c.get("https://sandbox.api.visa.com/vdp/helloworld", headers={"Accept": "application/json"})
        print(f"helloworld: {r.status_code} {r.text[:200]}")
        print(f"MLE: {'configured, key ' + pay.mle_key_id if pay.mle else 'NOT configured (payments will be refused with 9125)'}")
