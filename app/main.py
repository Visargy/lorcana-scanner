"""Lorcana Scanner – photo -> card names -> Cardmarket English prices."""
from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import vision
from .cache import Cache
from .cardmarket import Cardmarket
from .fetcher import FetchError, Fetcher, NotFound

logging.basicConfig(level=logging.INFO)
STATIC = Path(__file__).resolve().parent.parent / "static"
PASSWORD = os.environ.get("APP_PASSWORD", "")

app = FastAPI(title="Lorcana Scanner")
fetcher = Fetcher()
cache = Cache()
cm = Cardmarket(fetcher, cache)


@app.middleware("http")
async def password_guard(request: Request, call_next):
    path = request.url.path
    if PASSWORD and path.startswith("/api/") and path != "/api/status":
        given = request.headers.get("x-app-password", "")
        if not hmac.compare_digest(given.encode(), PASSWORD.encode()):
            return JSONResponse({"detail": "Špatné heslo."}, status_code=401)
    return await call_next(request)


def fail(e: Exception):
    if isinstance(e, NotFound):
        raise HTTPException(404, "Produkt na Cardmarketu nenalezen.")
    if isinstance(e, FetchError):
        raise HTTPException(502, f"Cardmarket se nepodařilo načíst: {e}")
    if isinstance(e, vision.VisionError):
        raise HTTPException(502, str(e))
    raise e


@app.get("/api/status")
async def status():
    return {
        "password_required": bool(PASSWORD),
        "vision": vision.provider(),
        "fetch_modes": fetcher.available(),
        "stats": fetcher.stats,
    }


@app.post("/api/scan")
async def scan(image: UploadFile = File(...)):
    data = await image.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Obrázek je moc velký (max 15 MB).")
    media = image.content_type if image.content_type in ("image/jpeg", "image/png", "image/webp", "image/gif") else "image/jpeg"
    try:
        cards = await vision.recognise(data, media)
    except Exception as e:
        fail(e)
    return {"cards": cards}


class LookupIn(BaseModel):
    card: dict
    foil: Optional[bool] = None
    min_condition: Optional[int] = None


@app.post("/api/lookup")
async def lookup(body: LookupIn):
    """Find the product for a recognised card and price it."""
    try:
        res = await cm.resolve(body.card)
        if not res["product"]:
            return {"product": None, "candidates": [], "price": None, "queries": res["queries"]}
        foil = body.card.get("is_foil", False) if body.foil is None else body.foil
        price = await cm.price(res["product"]["url"], bool(foil), body.min_condition)
        return {**res, "price": price}
    except Exception as e:
        fail(e)


class PriceIn(BaseModel):
    url: str
    foil: bool = False
    min_condition: Optional[int] = None
    refresh: bool = False


@app.post("/api/price")
async def price(body: PriceIn):
    if not body.url.startswith("https://www.cardmarket.com/en/Lorcana/Products/"):
        raise HTTPException(400, "Neplatná URL.")
    if body.refresh:
        cache.clear(f"price:{body.url}:")
    try:
        return await cm.price(body.url, body.foil, body.min_condition)
    except Exception as e:
        fail(e)


@app.get("/api/search")
async def search(q: str):
    if len(q.strip()) < 2:
        return {"results": []}
    try:
        return {"results": await cm.search(q)}
    except Exception as e:
        fail(e)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC), name="static")
