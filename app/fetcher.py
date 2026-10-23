"""Downloads Cardmarket pages despite Cloudflare.

Order of attempts (FETCH_MODE=auto, the default):
  1. direct  - curl_cffi impersonating real Chrome TLS/HTTP2 fingerprint (free).
               Optionally through PROXY_URL (e.g. a residential proxy).
  2. scrapedo - Scrape.do API with super=true (residential IPs, solves Cloudflare).
If direct gets a Cloudflare challenge it is paused for a while, so credits are not
wasted on retries and the user does not wait for a request that will fail anyway.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

from .parsers import is_challenge

log = logging.getLogger("fetcher")


class FetchError(Exception):
    pass


class NotFound(FetchError):
    pass


class Fetcher:
    def __init__(self) -> None:
        self.mode = os.environ.get("FETCH_MODE", "auto").lower()  # auto | direct | scrapedo
        self.scrapedo_token = os.environ.get("SCRAPEDO_TOKEN", "").strip()
        self.proxy = os.environ.get("PROXY_URL", "").strip() or None
        self.direct_blocked_until = 0.0
        self.sem = asyncio.Semaphore(int(os.environ.get("FETCH_CONCURRENCY", "4")))
        self.stats = {"direct_ok": 0, "direct_blocked": 0, "scrapedo_ok": 0, "scrapedo_credits": 0, "errors": 0}
        self._session = None
        self._http = httpx.AsyncClient(timeout=90)

    # ---------------------------------------------------------------- direct
    async def _direct(self, url: str) -> str:
        from curl_cffi.requests import AsyncSession  # imported lazily: optional native dependency

        if self._session is None:
            self._session = AsyncSession(impersonate="chrome", timeout=30,
                                         proxies={"https": self.proxy, "http": self.proxy} if self.proxy else None)
        r = await self._session.get(url, headers={"Accept-Language": "en-US,en;q=0.9"})
        if r.status_code == 404:
            raise NotFound(url)
        html = r.text
        if r.status_code in (403, 429, 503) or is_challenge(html):
            raise FetchError(f"direct blocked ({r.status_code})")
        if r.status_code != 200:
            raise FetchError(f"direct HTTP {r.status_code}")
        return html

    # -------------------------------------------------------------- scrapedo
    async def _scrapedo(self, url: str) -> str:
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                r = await self._http.get("https://api.scrape.do/",
                                         params={"token": self.scrapedo_token, "url": url, "super": "true"})
            except httpx.HTTPError as e:
                last = FetchError(f"scrape.do network error: {type(e).__name__}")
            else:
                cost = r.headers.get("Scrape.do-Request-Cost")
                if cost and cost.isdigit():
                    self.stats["scrapedo_credits"] += int(cost)
                if r.status_code == 200 and not is_challenge(r.text):
                    self.stats["scrapedo_ok"] += 1
                    return r.text
                if r.status_code == 404:
                    raise NotFound(url)
                if r.status_code in (401, 402, 403):
                    raise FetchError(f"Scrape.do odmítl požadavek ({r.status_code}) – zkontroluj token / kredity")
                last = FetchError(f"scrape.do HTTP {r.status_code}")
            await asyncio.sleep(1.5 * (attempt + 1))
        raise last or FetchError("scrape.do failed")

    # ------------------------------------------------------------------ main
    def available(self) -> list:
        modes = []
        if self.mode in ("auto", "direct"):
            modes.append("direct")
        if self.mode in ("auto", "scrapedo") and self.scrapedo_token:
            modes.append("scrapedo")
        return modes

    async def get(self, url: str) -> str:
        async with self.sem:
            errors = []
            for mode in self.available():
                if mode == "direct" and self.mode == "auto" and self.scrapedo_token and time.time() < self.direct_blocked_until:
                    continue
                try:
                    if mode == "direct":
                        html = await self._direct(url)
                        self.stats["direct_ok"] += 1
                        return html
                    return await self._scrapedo(url)
                except NotFound:
                    raise
                except Exception as e:  # try the next method
                    errors.append(f"{mode}: {str(e).split('. See https')[0][:160]}")
                    if mode == "direct":
                        self.stats["direct_blocked"] += 1
                        self.direct_blocked_until = time.time() + 30 * 60
            self.stats["errors"] += 1
            if not self.available():
                raise FetchError("Není nastaven žádný způsob stahování (nastav SCRAPEDO_TOKEN).")
            hint = "" if self.scrapedo_token else " – Cardmarket blokuje server, nastav SCRAPEDO_TOKEN."
            raise FetchError("; ".join(errors) + hint)
