"""Where real prices come from. No LLM here: plain, polite HTTP against the stores' own public web endpoints.

- Nomin (nomin.mn, Vendure + Remix): the grocery collections, page by page, from the route the site's category pages
  load their product grid from. The data is Remix single-fetch (turbo-stream) encoded.
- Emart (emartmall.mn): the site's own product search, one query per catalog item.
- Sansar has no online shop: its prices stay simulated.

One identified user agent, one request at a time, DELAY seconds apart, only what the catalog needs. Run it rarely
(it's a snapshot job, not something a household request triggers)."""
import asyncio
import json
import re

import httpx

UA = "Mozilla/5.0 (compatible; SagsHackathonBot/0.1; +https://github.com/kamaterasu/sags)"
DELAY = 2.0  # seconds between requests to the same site
NOMIN = "https://nomin.mn/discover-products.data"
NOMIN_COLLECTIONS = {"38071": "Шинэ хүнс", "38029": "Боловсруулсан хүнс", "38227": "Шингэн хүнс"}
EMART = "https://restapi.emartmall.mn:10443/mn/api/search/elastic"
EMART_QUERY = {"catId": "0", "store": None, "custId": 0, "attribute": "", "color": "", "brand": "", "promotion": "",
               "minPrice": 0, "maxPrice": 0, "startsWith": 0, "orderColumn": "", "highlight": False, "fashion": 0,
               "posMenu": "", "module": "", "gender": "", "intent": "", "pattern": "", "material": "", "style": "",
               "deliverys": "", "CustomerId": None}

# turbo-stream constants (remix-run/turbo-stream)
_CONST = {-1: None, -2: None, -3: None, -4: 0, -5: None, -6: None, -7: None}


def turbo_decode(text):
    """Remix single-fetch payload -> Python value. The first line is a flat array: objects map "_<key index>" to a
    value index, arrays list value indexes, negative numbers are constants, typed values are ["<code>", ...]."""
    arr = json.loads(text.split("\n", 1)[0])
    memo = {}

    def val(i):
        if i < 0:
            return _CONST.get(i)
        if i in memo:
            return memo[i]
        v = arr[i]
        if isinstance(v, dict):
            memo[i] = out = {}
            for k, vi in v.items():
                out[arr[int(k[1:])]] = val(vi)
            return out
        if isinstance(v, list) and not (v and isinstance(v[0], str)):
            memo[i] = out = []
            out.extend(val(x) for x in v)
            return out
        memo[i] = v
        return v
    return val(0)


def client():
    return httpx.AsyncClient(headers={"User-Agent": UA}, timeout=30, follow_redirects=True)


def nomin_product(it):
    price = it.get("discountedPrice") or it.get("regularPrice")
    return {"store": "nomin", "sku": str(it["sku"]), "name": it["productName"], "price": int(price),
            "list_price": int(it.get("regularPrice") or price), "in_stock": it.get("stockLevel") == "IN_STOCK",
            "stock": int(it.get("stockOnHand") or 0), "product_id": it.get("productId"),
            "by_weight": "(кг)" in it["productName"].lower(),  # "Шинэ гадил (кг)": the price is per kg
            # Nomin often leaves the pack size out of the name ("Ариутгасан цэвэр сүү") but has it in the description
            "pack_hint": " ".join(re.sub(r"<[^>]+>", " ", it.get("description") or "").split())[:160]}


async def crawl_nomin(h, log=print):
    """Every product in Nomin's grocery collections, deduplicated by sku."""
    out = {}
    for slug, name in NOMIN_COLLECTIONS.items():
        page = 1
        while True:
            r = await h.get(NOMIN, params={"collectionSlug": slug, "limit": 100, "page": page, "sort": "regular",
                                           "_routes": "routes/discover-products"})
            r.raise_for_status()
            d = turbo_decode(r.text)["routes/discover-products"]["data"]
            for it in d["result"]["items"]:
                if it.get("regularPrice"):
                    out[str(it["sku"])] = nomin_product(it)
            log(f"nomin {name} page {page}: {len(out)} products so far")
            if not d.get("hasMore"):
                break
            page += 1
            await asyncio.sleep(DELAY)
        await asyncio.sleep(DELAY)
    return list(out.values())


def emart_product(s):
    price = s.get("discountprice") or s.get("currentprice") or s.get("price")
    return {"store": "emart", "sku": str(s["skucd"]), "name": s["title"], "price": int(price),
            "list_price": int(s.get("price") or price), "in_stock": bool(s.get("isavailable")),
            "stock": 50 if s.get("isavailable") else 0,  # Emart doesn't say how many; enough for a household
            "pack_hint": s.get("feature") or "", "url": f"https://emartmall.mn/productdetail/{s['skucd']}",
            "by_weight": bool(s.get("issalekg"))}  # sold by weight: the price is per kg


async def search_emart(h, query, n=15):
    r = await h.post(EMART, json={**EMART_QUERY, "value": query, "rowCount": n})
    r.raise_for_status()
    hits = r.json()["data"]["hits"]
    hits = hits["hits"] if isinstance(hits, dict) else hits
    return [emart_product(x["_source"]) for x in hits if x["_source"].get("skucd") and x["_source"].get("price")]


def words(text):
    return [w for w in re.split(r"[^\w]+", text.lower()) if len(w) > 1]
