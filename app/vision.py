"""Recognising Lorcana cards in a photo with a vision model (Claude or Gemini)."""
from __future__ import annotations

import base64
import json
import os
import re
from typing import List

import httpx

PROMPT = """You are an expert Disney Lorcana TCG card identifier. Find EVERY Lorcana card visible in the photo
(single card, a hand of cards, a binder page or a table full of cards). Order them left-to-right, top-to-bottom.

For each card read:
- name: the character/item/location name exactly as printed in English (e.g. "Elsa", "Mickey Mouse", "Dinglehopper").
  If the card is printed in another language, give the official English name.
- version: the subtitle under the name for characters (e.g. "Spirit of Winter"); empty string for items/actions/songs without one.
- collector_number and set_number: from the bottom line formatted like "207/204 • EN • 1"
  (collector number / set size • language • set number). Use null when not readable.
- language: the language code from that line (EN, DE, FR, IT, JA, ZH...) or null.
- rarity: one of Common, Uncommon, Rare, Super Rare, Legendary, Epic, Enchanted, Iconic, Promo (from the rarity symbol
  or from the art style - Enchanted = full-art alternate art with a rainbow/foil look; Epic/Iconic are special full-art).
- is_foil: true if the card is a foil (holographic/rainbow reflective sheen over the art or frame, light glare with
  rainbow colours, "cold foil" shiny name/frame). Enchanted, Epic and Iconic cards are always foil.
- foil_confidence: "high", "medium" or "low".
- position: short human-friendly position like "top-left" or "row 2, card 3".

Only include cards you can actually see. If you cannot read a field, use null rather than guessing wildly,
but do your best to identify the card from its art if text is partly hidden."""

SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "version": {"type": "string"},
                    "collector_number": {"type": ["integer", "null"]},
                    "set_number": {"type": ["integer", "null"]},
                    "language": {"type": ["string", "null"]},
                    "rarity": {"type": ["string", "null"]},
                    "is_foil": {"type": "boolean"},
                    "foil_confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "position": {"type": "string"},
                },
                "required": ["name", "version", "collector_number", "set_number", "language", "rarity",
                             "is_foil", "foil_confidence", "position"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cards"],
    "additionalProperties": False,
}


class VisionError(Exception):
    pass


def provider() -> str:
    forced = os.environ.get("VISION_PROVIDER", "").lower()
    if forced:
        return forced
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return "none"


async def recognise(image: bytes, media_type: str) -> List[dict]:
    p = provider()
    if p == "claude":
        cards = await _claude(image, media_type)
    elif p == "gemini":
        cards = await _gemini(image, media_type)
    else:
        raise VisionError("Chybí API klíč pro rozpoznávání (nastav ANTHROPIC_API_KEY nebo GEMINI_API_KEY).")
    for c in cards:
        if (c.get("rarity") or "").lower() in ("enchanted", "epic", "iconic"):
            c["is_foil"] = True
            c["foil_confidence"] = "high"
    return cards


async def _claude(image: bytes, media_type: str) -> List[dict]:
    import anthropic

    client = anthropic.AsyncAnthropic()
    model = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
    kwargs = dict(
        model=model,
        max_tokens=16000,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.standard_b64encode(image).decode()}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )
    try:
        # Server-side fallback: if the model ever declines, the API retries on a fallback model in the same call.
        resp = await client.beta.messages.create(**kwargs, betas=["server-side-fallback-2026-07-01"],
                                                 fallbacks="default")
    except anthropic.BadRequestError:
        resp = await client.messages.create(**kwargs)  # model/account without fallback support
    except anthropic.AuthenticationError as e:
        raise VisionError("Neplatný ANTHROPIC_API_KEY.") from e
    except anthropic.RateLimitError as e:
        raise VisionError("Claude API je přetížené / limit – zkus to za chvíli.") from e
    except anthropic.APIStatusError as e:
        raise VisionError(f"Claude API chyba {e.status_code}.") from e
    except anthropic.APIConnectionError as e:
        raise VisionError("Nelze se spojit s Claude API.") from e
    if resp.stop_reason == "refusal":
        raise VisionError("Model odmítl zpracovat obrázek.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)["cards"]
    except (ValueError, KeyError) as e:
        raise VisionError("Model vrátil neplatnou odpověď.") from e


def _gemini_schema(s):
    """Gemini accepts an OpenAPI-style subset: nullable instead of type lists, no additionalProperties."""
    s = dict(s)
    s.pop("additionalProperties", None)
    if isinstance(s.get("type"), list):
        types = [t for t in s["type"] if t != "null"]
        s["type"], s["nullable"] = types[0], True
    if "properties" in s:
        s["properties"] = {k: _gemini_schema(v) for k, v in s["properties"].items()}
    if "items" in s:
        s["items"] = _gemini_schema(s["items"])
    s["type"] = s["type"].upper()
    return s


GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
_gemini_model = {"name": os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")}


def _version_key(name: str):
    return [int(n) for n in re.findall(r"\d+", name)]


async def _pick_gemini_model(http: httpx.AsyncClient, error_text: str) -> str:
    """The configured model was retired: take the one Google suggests, else the newest Flash model."""
    m = re.search(r"use models/([\w.\-]+)", error_text)
    if m:
        return m.group(1)
    r = await http.get(f"{GEMINI_API}/models", params={"key": os.environ["GEMINI_API_KEY"], "pageSize": 1000})
    names = [x["name"].split("/", 1)[1] for x in r.json().get("models", [])
             if "generateContent" in x.get("supportedGenerationMethods", [])]
    flash = [n for n in names if re.fullmatch(r"gemini-[\d.]+-flash", n)]
    if not flash:
        raise VisionError(f"Gemini: model {_gemini_model['name']} není dostupný a nenašel jsem náhradu.")
    return max(flash, key=_version_key)


async def _gemini(image: bytes, media_type: str) -> List[dict]:
    body = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": media_type, "data": base64.b64encode(image).decode()}},
            {"text": PROMPT},
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": _gemini_schema(SCHEMA),
                             "temperature": 0},
    }
    async with httpx.AsyncClient(timeout=120) as http:
        for attempt in range(2):
            r = await http.post(f"{GEMINI_API}/models/{_gemini_model['name']}:generateContent",
                                params={"key": os.environ["GEMINI_API_KEY"]}, json=body)
            if r.status_code == 404 and attempt == 0:
                _gemini_model["name"] = await _pick_gemini_model(http, r.text)
                continue
            break
    if r.status_code != 200:
        raise VisionError(f"Gemini API chyba {r.status_code}: {r.text[:200]}")
    try:
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)["cards"]
    except (ValueError, KeyError, IndexError) as e:
        raise VisionError("Gemini vrátil neplatnou odpověď.") from e
