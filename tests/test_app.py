import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main, vision
from app.cache import Cache
from app.cardmarket import Cardmarket
from app.parsers import is_challenge, parse_product, parse_product_list

FIX = Path(__file__).parent / "fixtures"
PRODUCT = (FIX / "pokemon_product.html").read_text()
SEARCH = (FIX / "magic_search.html").read_text()


def test_parse_product_offers_and_prices():
    p = parse_product(PRODUCT)
    assert p["name"] and p["url"].startswith("https://www.cardmarket.com/")
    assert p["price_trend"] == 6.47
    assert len(p["offers"]) == 50
    first = p["offers"][0]
    assert first["price"] == 1.95 and first["language"] == "English" and first["seller"] == "Lajski"
    assert not is_challenge(PRODUCT)


def test_parse_search_list():
    rows = parse_product_list(SEARCH)
    assert rows and rows[0]["url"].startswith("https://www.cardmarket.com/en/Magic/Products/Singles/")
    assert rows[0]["number"] == "806" and rows[0]["expansion"]


def test_challenge_detection():
    assert is_challenge("<html><title>Just a moment...</title></html>")


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    async def get(self, url):
        self.calls.append(url)
        for key, html in self.pages.items():
            if key in url:
                return html
        raise AssertionError(url)


def lorcana_rows():
    return [
        {"name": "Elsa - Spirit of Winter (V.1)", "url": "https://www.cardmarket.com/en/Lorcana/Products/Singles/The-First-Chapter/Elsa-Spirit-of-Winter-V1",
         "expansion": "The First Chapter", "number": "42", "rarity": "Legendary"},
        {"name": "Elsa - Spirit of Winter (V.2)", "url": "https://www.cardmarket.com/en/Lorcana/Products/Singles/The-First-Chapter/Elsa-Spirit-of-Winter-V2",
         "expansion": "The First Chapter", "number": "207", "rarity": "Enchanted"},
        {"name": "Elsa - Snow Queen (V.1)", "url": "https://www.cardmarket.com/en/Lorcana/Products/Singles/The-First-Chapter/Elsa-Snow-Queen-V1",
         "expansion": "The First Chapter", "number": "41", "rarity": "Rare"},
    ]


def test_resolve_prefers_collector_number(tmp_path):
    cm = Cardmarket(FakeFetcher({}), Cache(str(tmp_path / "c.db")))
    cm.cache.set("search:elsa - spirit of winter", lorcana_rows(), 100)
    card = {"name": "Elsa", "version": "Spirit of Winter", "collector_number": 207, "set_number": 1, "rarity": "Enchanted"}
    res = asyncio.run(cm.resolve(card))
    assert res["product"]["number"] == "207"
    card.update(collector_number=42, rarity="Legendary")
    assert asyncio.run(cm.resolve(card))["product"]["number"] == "42"
    card.update(collector_number=None, rarity=None)  # unreadable bottom line -> name match
    assert "Spirit of Winter (V.1)" in asyncio.run(cm.resolve(card))["product"]["name"]


def test_price_low_and_median(tmp_path):
    f = FakeFetcher({"Snorlax": PRODUCT})
    cm = Cardmarket(f, Cache(str(tmp_path / "c.db")))
    url = "https://www.cardmarket.com/en/Pokemon/Products/Singles/Legendary-Collection/Snorlax-LC64"
    r = asyncio.run(cm.price(url, foil=False))
    prices = sorted(o["price"] for o in parse_product(PRODUCT)["offers"])[:5]
    assert r["cm_low_en"] == prices[0]
    assert r["median5_en"] == sorted(prices)[2]
    assert "language=1" in f.calls[0] and "isFoil=N" in f.calls[0]
    # second call comes from cache
    assert asyncio.run(cm.price(url, foil=False))["cached"] and len(f.calls) == 1


def test_foil_fallback_when_no_foil_marked(tmp_path):
    f = FakeFetcher({"Snorlax": PRODUCT})
    cm = Cardmarket(f, Cache(str(tmp_path / "c.db")))
    r = asyncio.run(cm.price("https://www.cardmarket.com/en/Pokemon/Products/Singles/X/Snorlax", foil=True))
    assert r["foil_filter_ignored"] and r["cm_low_en"] is not None and len(f.calls) == 2


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def fake_recognise(data, media):
        return [{"name": "Elsa", "version": "Spirit of Winter", "collector_number": 42, "set_number": 1,
                 "language": "EN", "rarity": "Legendary", "is_foil": False, "foil_confidence": "high", "position": "left"}]

    monkeypatch.setattr(vision, "recognise", fake_recognise)
    fake = FakeFetcher({"Products/Search": SEARCH, "Elsa-Spirit-of-Winter": PRODUCT})
    main.cm = Cardmarket(fake, Cache(str(tmp_path / "c.db")))
    main.cache = main.cm.cache
    return TestClient(main.app)


def test_api_flow(client, monkeypatch):
    rows = lorcana_rows()
    monkeypatch.setattr(main.cm, "search", lambda q: _async(rows))
    r = client.post("/api/scan", files={"image": ("a.jpg", b"xx", "image/jpeg")})
    assert r.status_code == 200
    card = r.json()["cards"][0]
    r = client.post("/api/lookup", json={"card": card})
    body = r.json()
    assert r.status_code == 200, body
    assert body["product"]["number"] == "42"
    assert body["price"]["cm_low_en"] == 1.95
    r = client.post("/api/price", json={"url": rows[0]["url"], "foil": False, "refresh": True})
    assert r.status_code == 200
    assert client.post("/api/price", json={"url": "https://evil.example/", "foil": False}).status_code == 400
    assert client.get("/").status_code == 200 and client.get("/app.js").status_code == 200


def test_password(client, monkeypatch):
    monkeypatch.setattr(main, "PASSWORD", "tajne")
    assert client.get("/api/search?q=elsa").status_code == 401
    assert client.get("/api/status").json()["password_required"]


async def _async(v):
    return v
