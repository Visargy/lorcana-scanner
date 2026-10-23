"""Cardmarket HTML parsers.

Adapted from batuzyn/cardmarket-api (MIT License, Copyright (c) 2026 batuzyn),
trimmed to what this app needs: product pages (offers + price info) and search lists.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

BASE_URL = "https://www.cardmarket.com"
_PRICE_RE = re.compile(r"(-?[\d.,]+)")

PRICE_KEYS = {
    "From": "from_price",
    "Price Trend": "price_trend",
    "30-days average price": "avg_30d",
    "7-days average price": "avg_7d",
    "1-day average price": "avg_1d",
}


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def text(el: Optional[Tag]) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True).replace("\xa0", " ")).strip()


def parse_price(value: Optional[str]) -> Optional[float]:
    """'1.234,56 €' -> 1234.56"""
    if not value:
        return None
    m = _PRICE_RE.search(value.replace("\xa0", " "))
    if not m:
        return None
    num = m.group(1)
    if "," in num:
        num = num.replace(".", "").replace(",", ".")
    try:
        return float(num)
    except ValueError:
        return None


def parse_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    m = re.search(r"\d[\d.,]*", value)
    if not m:
        return None
    try:
        return int(re.sub(r"[.,]", "", m.group(0)))
    except ValueError:
        return None


def absolute(href: Optional[str], keep_query: bool = False) -> Optional[str]:
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = BASE_URL + href
    if not keep_query:
        p = urlsplit(href)
        href = urlunsplit((p.scheme, p.netloc, p.path, "", ""))
    return href


def tooltip_img(el: Optional[Tag]) -> Optional[str]:
    if el is None:
        return None
    raw = el.get("data-bs-title") or el.get("title") or ""
    m = re.search(r'src="([^"]+)"', raw)
    return absolute(m.group(1), keep_query=True) if m else None


def svg_title(el: Optional[Tag]) -> Optional[str]:
    if el is None:
        return None
    svg = el if el.name == "svg" else el.find("svg")
    if svg is None:
        return None
    return svg.get("title") or svg.get("aria-label") or svg.get("data-bs-original-title")


def page_url(doc: BeautifulSoup) -> Optional[str]:
    link = doc.select_one('link[rel="canonical"][href]')
    if link:
        return absolute(link["href"])
    meta = doc.select_one('meta[property="og:url"][content]')
    return absolute(meta["content"]) if meta else None


def is_challenge(html: str) -> bool:
    """Cloudflare interstitial instead of the real page."""
    head = html[:6000]
    return ("Just a moment" in head or "cf-browser-verification" in head or "challenge-platform" in head
            or "Attention Required! | Cloudflare" in head) and "article-row" not in html


# ---------------------------------------------------------------- offers
def parse_offer_row(row: Tag) -> dict:
    offer = {"seller": None, "country": None, "condition": None, "language": None,
             "is_foil": False, "price": None, "quantity": None}
    seller = row.select_one('.col-seller a[href*="/Users/"]')
    if seller:
        offer["seller"] = text(seller)
    loc = row.select_one('.col-seller [title^="Item location:"]')
    if loc:
        offer["country"] = loc["title"].split(":", 1)[1].strip()
    attrs = row.select_one(".product-attributes")
    if attrs:
        cond = attrs.select_one(".article-condition")
        if cond:
            offer["condition"] = text(cond) or None
        for el in attrs.select("[title]"):
            if el is cond or "expansion-symbol" in el.get("class", []):
                continue
            title = (el.get("title") or "").strip()
            if title.lower() == "foil":
                offer["is_foil"] = True
            elif el.get("onmouseover", "").startswith("showMsgBox") and offer["language"] is None:
                offer["language"] = title
        if offer["language"] is None:
            for el in attrs.select("[aria-label], [data-bs-original-title]"):
                label = el.get("aria-label") or el.get("data-bs-original-title") or ""
                if label in LANGUAGES:
                    offer["language"] = label
                    break
    box = row.select_one(".col-offer .price-container") or row.select_one(".price-container")
    if box:
        prices = [parse_price(text(s)) for s in box.select("span.color-primary, span.text-nowrap")]
        prices = [p for p in prices if p is not None]
        if prices:
            offer["price"] = prices[0]
    count = row.select_one(".col-offer .item-count") or row.select_one(".item-count")
    if count:
        offer["quantity"] = parse_int(text(count))
    return offer


LANGUAGES = {"English", "French", "German", "Spanish", "Italian", "Japanese", "Portuguese", "Russian",
             "Korean", "S-Chinese", "T-Chinese", "Dutch", "Polish", "Czech"}


def parse_offers(doc: BeautifulSoup) -> List[dict]:
    rows = doc.select('div.article-row[id^="articleRow"]')
    return [parse_offer_row(r) for r in rows]


# --------------------------------------------------------------- product
def parse_product(html: str) -> dict:
    doc = soup(html)
    h1 = doc.select_one(".page-title-container h1") or doc.select_one("h1")
    sub = h1.select_one("span") if h1 else None
    name = text(h1)
    if sub:
        name = name[: len(name) - len(text(sub))].strip()
    id_input = doc.select_one('form.article-filter-form input[name="idProduct"]')
    out = {
        "id": parse_int(id_input["value"]) if id_input else None,
        "name": name or None,
        "url": page_url(doc),
        "expansion": None,
        "rarity": None,
        "number": None,
        "image": None,
        "available_items": None,
        "from_price": None,
        "price_trend": None,
        "avg_30d": None,
        "avg_7d": None,
        "avg_1d": None,
    }
    dl = doc.select_one("#tabContent-info dl") or doc.select_one(".info-list-container dl") or doc.select_one("dl.labeled")
    if dl:
        for dt in dl.select("dt"):
            dd = dt.find_next_sibling("dd")
            label, value = text(dt), text(dd)
            if label == "Rarity":
                out["rarity"] = svg_title(dd) or value or None
            elif label == "Number":
                out["number"] = value or None
            elif label == "Printed in":
                out["expansion"] = value or None
            elif label == "Available items":
                out["available_items"] = parse_int(value)
            elif label in PRICE_KEYS:
                out[PRICE_KEYS[label]] = parse_price(value)
    img = doc.select_one("#image img.is-front:not(.lazy)") or doc.select_one("#image img.is-front")
    if img is not None:
        out["image"] = absolute(img.get("src") or img.get("data-echo"), keep_query=True)
    out["offers"] = parse_offers(doc)
    return out


# ------------------------------------------------------------ search list
def _list_row(row: Tag) -> dict:
    cell = lambda n: row.select_one(f'[data-testid="{n}"]')
    link = cell("name").select_one("a") if cell("name") else None
    exp = cell("expansion")
    exp_a = exp.select_one("[title]") if exp else None
    return {
        "id": parse_int(row.get("id")),
        "name": text(link) or None,
        "url": absolute(link["href"]) if link else None,
        "expansion": exp_a.get("title") if exp_a else None,
        "rarity": svg_title(cell("rarity")),
        "number": text(cell("collector_number")).lstrip("# ").strip() or None,
        "available_items": parse_int(text(cell("availability"))),
        "from_price": parse_price(text(cell("from_price"))),
        "from_price_foil": parse_price(text(cell("price_from_foils"))),
        "image": tooltip_img(row.select_one(".thumbnail-icon")),
    }


def _gallery_box(a: Tag) -> dict:
    title = a.select_one(".card-title")
    exp = title.select_one("[title]") if title else None
    img = a.select_one("img")
    src = (img.get("data-echo") or img.get("src")) if img else None
    return {
        "id": None,
        "name": text(title) or a.get("title") or None,
        "url": absolute(a["href"]),
        "expansion": exp["title"] if exp else None,
        "rarity": None,
        "number": None,
        "available_items": None,
        "from_price": None,
        "from_price_foil": None,
        "image": absolute(src, keep_query=True) if src and not src.endswith("transparent.gif") else None,
    }


def parse_product_list(html: str) -> List[dict]:
    doc = soup(html)
    rows = doc.select('[id^="productRow"]')
    if rows:
        return [_list_row(r) for r in rows]
    return [_gallery_box(a) for a in doc.select("a.galleryBox[href]")]
