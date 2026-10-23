---
title: Lorcana Scanner
emoji: 💎
colorFrom: purple
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# 💎 Lorcana Scanner

Vyfotíš karty Disney Lorcana → aplikace pozná, co to je (název, verze, edice, foil / non-foil) → najde je
na Cardmarketu a ukáže **CM low EN** (nejlevnější anglická nabídka) a **medián 5 nejlevnějších EN nabídek**.
Běží na serveru v cloudu, takže stačí mobil. Žádné PC ani rozšíření Chrome.

## Jak to funguje

1. **Fotka → karty.** Fotka jde do vision AI (Claude nebo Gemini). AI přečte jméno, podtitul, číslo karty a sady
   ze spodního řádku (`207/204 • EN • 1`), raritu a jestli je karta foil.
2. **Karta → produkt na Cardmarketu.** Hledání na Cardmarketu a výběr správné verze podle čísla karty, sady a rarity.
   Enchanted a běžná verze se tak nepletou. Když si AI není jistá, v rozbalovacím seznamu přepneš verzi.
3. **Ceny.** Otevře stránku produktu s filtrem `language=English` a `foil=ano/ne` (volitelně i minimální stav),
   vezme 5 nejlevnějších nabídek a spočítá **low** a **medián**. Ceny se 6 hodin cachují, takže se nic nestahuje dvakrát.

**Cloudflare:** Cardmarket blokuje servery v datacentrech. Aplikace to obchází takhle:
nejdřív zkusí stránku stáhnout zadarmo s otiskem skutečného prohlížeče Chrome (`curl_cffi`), případně přes
tvou residential proxy. Když ji Cloudflare zablokuje, použije **Scrape.do**, který Cloudflare obchází přes rezidenční IP.

## Ovládání

- **📷 Vyfotit karty** otevře foťák. **🖼️ Nahrát fotky** nahraje jednu nebo víc fotek najednou.
  Na PC jde fotka i přetáhnout nebo vložit přes Ctrl+V.
- Na jedné fotce může být víc karet (třeba celá stránka binderu).
- **✨ Foil / Non-foil:** klepnutím přepneš a cena se přepočítá. Otazník znamená, že si AI foilem nebyla jistá.
- **− 1 +** nastavuje počet kusů, celkové součty se počítají i s nimi.
- **⬇️ CSV** stáhne tabulku pro Excel nebo Google Sheets. **📋 Kopírovat** zkopíruje text, třeba do zprávy kupci.
- Kartu jde přidat i ručně: napiš název do pole pod tlačítky.
- **⚙️ Nastavení:** heslo aplikace, minimální stav karty (např. jen NM) a stav serveru.
- Na mobilu dej v prohlížeči **„Přidat na plochu“**. Aplikace se pak chová jako normální appka.

## Nasazení zdarma na Hugging Face Spaces (asi 10 minut)

1. Založ si účet na <https://huggingface.co> (zdarma).
2. Klikni na **New → Space**. Zadej jméno (např. `lorcana-scanner`), jako **SDK zvol Docker → Blank** a hardware nech
   **CPU basic (free)**. Viditelnost dej Public (aplikaci chrání heslo, viz krok 4), nebo Private,
   pokud budeš v mobilu přihlášený k HF.
3. Ve Space otevři **Files → Add file → Upload files** a nahraj **obsah** této složky (`app/`, `static/`, `Dockerfile`,
   `requirements.txt`, `README.md`). Složky `tests/` a `.venv/` nahrávat nemusíš.
4. Ve Space otevři **Settings → Variables and secrets → New secret** a přidej:

   | Secret | Co to je | Povinné |
   |---|---|---|
   | `APP_PASSWORD` | heslo do aplikace, ať ti cizí lidi nepálí kredity | doporučeno |
   | `GEMINI_API_KEY` | rozpoznávání karet **zdarma**: <https://aistudio.google.com/apikey> | jeden z těchto dvou |
   | `ANTHROPIC_API_KEY` | rozpoznávání přes Claude, nejpřesnější: <https://console.anthropic.com> | jeden z těchto dvou |
   | `SCRAPEDO_TOKEN` | obcházení Cloudflare: <https://scrape.do> (free tier 1000 kreditů/měsíc) | prakticky ano |
   | `PROXY_URL` | volitelně vlastní residential proxy, `http://user:pass@host:port` | ne |

5. Počkej, až build doběhne (stav **Running**). Aplikace poběží na `https://<tvuj-ucet>-lorcana-scanner.hf.space`.
   Otevři ji v mobilu, zadej heslo v ⚙️ a přidej si ji na plochu.

Space na free tieru po 48 hodinách bez návštěvy usne. Při dalším otevření se do minuty sám probudí.

### Alternativa: Render.com
New → Blueprint → nahraj repozitář s touto složkou (je v ní `render.yaml`) a vyplň stejné proměnné.
Free instance na Renderu usíná už po 15 minutách nečinnosti.

### Lokálně / na vlastním serveru
```bash
pip install -r requirements.txt
GEMINI_API_KEY=... SCRAPEDO_TOKEN=... uvicorn app.main:app --host 0.0.0.0 --port 7860
```
Na domácím PC nebo Raspberry Pi máš rezidenční IP a často pak Scrape.do vůbec nepotřebuješ.

## Náklady (orientačně)

- **Rozpoznávání:** Gemini 2.5 Flash má free tier, který na běžné použití stačí. Claude Opus 5.5 (výchozí model,
  když je nastavený `ANTHROPIC_API_KEY`) vyjde zhruba na 1–3 haléře za fotku. Levnější varianta je
  `CLAUDE_MODEL=claude-haiku-5-5`.
- **Cardmarket přes Scrape.do:** jedna stránka stojí zhruba 10 kreditů. Nová karta potřebuje 2 stránky (hledání + nabídky),
  opakované dotazy jdou z cache. Free tier (1000 kreditů) tak stačí asi na 50 nových karet měsíčně.
  Při obchodování ve větším je potřeba placený plán, nebo levnější **residential proxy** (`PROXY_URL`, platí se za GB;
  stránka má asi 300 kB, takže 1 GB vystačí na tisíce stránek).

## Další nastavení (proměnné prostředí)

| Proměnná | Výchozí | Význam |
|---|---|---|
| `VISION_PROVIDER` | auto | `claude` nebo `gemini`, když máš oba klíče |
| `CLAUDE_MODEL` | `claude-opus-5-5` | model pro Claude |
| `GEMINI_MODEL` | `gemini-2.5-flash` | model pro Gemini |
| `FETCH_MODE` | `auto` | `direct` (jen zdarma), `scrapedo` (jen Scrape.do), `auto` (nejdřív zdarma, pak Scrape.do) |
| `PRICE_CACHE_HOURS` | `6` | jak dlouho platí stažená cena |

## Testy
```bash
pip install pytest && python -m pytest -q
```

Parsery HTML Cardmarketu vycházejí z projektu [batuzyn/cardmarket-api](https://github.com/batuzyn/cardmarket-api) (MIT).
