"""Finding Lorcana products on Cardmarket and computing English prices."""
from __future__ import annotations

import os
import re
import statistics
import time
from difflib import SequenceMatcher
from typing import List, Optional
from urllib.parse import urlencode

from .cache import Cache
from .fetcher import Fetcher, NotFound
from .parsers import parse_product, parse_product_list

GAME_URL = "https://www.cardmarket.com/en/Lorcana"
PRICE_TTL = float(os.environ.get("PRICE_CACHE_HOURS", "6")) * 3600
SEARCH_TTL = 14 * 24 * 3600

# Set number printed on the card (bottom line "12/204 • EN • 3") -> Cardmarket expansion name.
SET_NAMES = {
    1: "The First Chapter",
    2: "Rise of the Floodborn",
    3: "Into the Inklands",
    4: "Ursula's Return",
    5: "Shimmering Skies",
    6: "Azurite Sea",
    7: "Archazia's Island",
    8: "Reign of Jafar",
    9: "Fabled",
    10: "Whispers in the Well",
    11: "Winterspell",
    12: "Wilds Unknown",
    13: "Attack of the Vine!",
}


def norm(s: Optional[str]) -> str:
    s = (s or "").lower().replace("’", "'")
    s = re.sub(r"\(v\.\s*\d+\)", " ", s)
    s = re.sub(r"^\d+[a-z]{2,5}\s+", "", s)  # "13ATV Mickey ..." code prefix in some lists
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def num(s) -> Optional[int]:
    if s is None:
        return None
    m = re.search(r"\d+", str(s))
    return int(m.group(0)) if m else None


def version_no(name: Optional[str]) -> int:
    m = re.search(r"\(V\.\s*(\d+)\)", name or "")
    return int(m.group(1)) if m else 1


class Cardmarket:
    def __init__(self, fetcher: Fetcher, cache: Cache) -> None:
        self.fetcher = fetcher
        self.cache = cache

    # ---------------------------------------------------------------- search
    async def search(self, query: str) -> List[dict]:
        query = query.strip()
        key = "search:" + query.lower()
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        url = f"{GAME_URL}/Products/Search?" + urlencode({"searchString": query, "mode": "list", "perSite": 50})
        html = await self.fetcher.get(url)
        rows = parse_product_list(html)
        if not rows:  # a single hit redirects straight to the product page
            p = parse_product(html)
            if p.get("url") and "/Products/Singles/" in p["url"]:
                rows = [{k: p.get(k) for k in ("id", "name", "url", "expansion", "rarity", "number", "available_items",
                                                "from_price", "image")} | {"from_price_foil": None}]
        rows = [r for r in rows if r.get("url") and "/Products/Singles/" in r["url"]]
        self.cache.set(key, rows, SEARCH_TTL)
        return rows

    @staticmethod
    def score(row: dict, card: dict) -> float:
        want = norm(f"{card.get('name', '')} {card.get('version') or ''}")
        s = SequenceMatcher(None, norm(row.get("name")), want).ratio() * 10
        n_card, n_row = num(card.get("collector_number")), num(row.get("number"))
        if n_card is not None and n_row is not None:
            s += 25 if n_card == n_row else -4
        set_name = SET_NAMES.get(num(card.get("set_number")) or -1)
        if set_name and row.get("expansion"):
            s += 8 if norm(set_name) == norm(row["expansion"]) else -2
        rarity = (card.get("rarity") or "").lower()
        row_rarity = (row.get("rarity") or "").lower()
        if rarity and row_rarity:
            s += 4 if rarity == row_rarity else 0
        if rarity in ("enchanted", "epic", "iconic") and version_no(row.get("name")) > 1:
            s += 3
        if rarity and rarity not in ("enchanted", "epic", "iconic", "promo") and version_no(row.get("name")) == 1:
            s += 1
        return s

    async def resolve(self, card: dict) -> dict:
        """Best Cardmarket product for a recognised card + alternatives for the user to pick."""
        name, version = (card.get("name") or "").strip(), (card.get("version") or "").strip()
        queries = []
        if name and version:
            queries.append(f"{name} - {version}")
        if version:
            queries.append(version)
        if name:
            queries.append(name)
        rows: List[dict] = []
        tried = []
        for q in dict.fromkeys(queries):
            tried.append(q)
            rows = await self.search(q)
            if rows:
                break
        ranked = sorted(rows, key=lambda r: self.score(r, card), reverse=True)
        return {"product": ranked[0] if ranked else None, "candidates": ranked[:12], "queries": tried}

    # ----------------------------------------------------------------- price
    async def price(self, url: str, foil: bool, min_condition: Optional[int] = None) -> dict:
        key = f"price:{url}:{int(foil)}:{min_condition or 0}"
        hit = self.cache.get(key)
        if hit is not None:
            hit["cached"] = True
            return hit
        params = {"language": 1, "isFoil": "Y" if foil else "N"}
        if min_condition:
            params["minCondition"] = min_condition
        page = parse_product(await self.fetcher.get(f"{url}?{urlencode(params)}"))
        offers = [o for o in page["offers"] if o["price"] is not None and o["language"] in (None, "English")]
        matching = [o for o in offers if o["is_foil"] == foil]
        foil_filter_ignored = False
        if foil and not matching:
            # Some printings (e.g. Enchanted) are foil-only and sellers do not always tick "foil".
            all_page = parse_product(await self.fetcher.get(f"{url}?{urlencode({k: v for k, v in params.items() if k != 'isFoil'})}"))
            matching = [o for o in all_page["offers"] if o["price"] is not None and o["language"] in (None, "English")]
            foil_filter_ignored = bool(matching)
        matching.sort(key=lambda o: o["price"])
        first5 = matching[:5]
        result = {
            "url": url,
            "foil": foil,
            "name": page.get("name"),
            "expansion": page.get("expansion"),
            "rarity": page.get("rarity"),
            "number": page.get("number"),
            "image": page.get("image"),
            "cm_low_en": first5[0]["price"] if first5 else None,
            "median5_en": round(statistics.median([o["price"] for o in first5]), 2) if first5 else None,
            "first5": [{k: o[k] for k in ("price", "condition", "country", "seller")} for o in first5],
            "offers_en": len(matching),
            "price_trend": page.get("price_trend"),
            "avg_30d": page.get("avg_30d"),
            "foil_filter_ignored": foil_filter_ignored,
            "fetched_at": time.time(),
            "cached": False,
        }
        self.cache.set(key, result, PRICE_TTL)
        return result


__all__ = ["Cardmarket", "NotFound", "SET_NAMES"]
