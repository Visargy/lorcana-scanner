"use strict";

const $ = (s, el = document) => el.querySelector(s);
const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

let items = LS.get("items", []);
let settings = LS.get("settings", { password: "", minCondition: "" });
const eur = (v) => (v == null ? "–" : v.toLocaleString("cs-CZ", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " €");
const uid = () => Math.random().toString(36).slice(2, 10);

// ------------------------------------------------------------------ api
async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (settings.password) headers["X-App-Password"] = settings.password;
  if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const res = await fetch(path, { method: opts.body ? "POST" : "GET", headers, body: opts.body });
  let data = null;
  try { data = await res.json(); } catch {}
  if (res.status === 401) { openSettings(); throw new Error("Zadej heslo aplikace v nastavení."); }
  if (!res.ok) throw new Error((data && data.detail) || `Chyba serveru (${res.status})`);
  return data;
}

// Limit parallel price lookups so the server is not flooded.
const queue = [];
let running = 0;
function enqueue(fn) {
  return new Promise((resolve, reject) => { queue.push({ fn, resolve, reject }); pump(); });
}
function pump() {
  while (running < 3 && queue.length) {
    const { fn, resolve, reject } = queue.shift();
    running++;
    fn().then(resolve, reject).finally(() => { running--; pump(); });
  }
}

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), ms);
}

const save = () => LS.set("items", items);
const minCond = () => (settings.minCondition ? Number(settings.minCondition) : null);

// -------------------------------------------------------------- images
async function shrink(file) {
  // Phone photos are huge; 2000px is plenty for reading the cards and uploads much faster.
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return await new Promise((r) => c.toBlob((b) => r(b || file), "image/jpeg", 0.88));
  } catch {
    return file;
  }
}

async function handleFiles(files) {
  for (const file of files) scanPhoto(file);
}

async function scanPhoto(file) {
  const job = document.createElement("div");
  job.className = "job";
  const url = URL.createObjectURL(file);
  job.innerHTML = `<img alt=""><div class="spinner"></div><span>Rozpoznávám karty na fotce…</span>`;
  $("img", job).src = url;
  $("#jobs").prepend(job);
  try {
    const blob = await shrink(file);
    const fd = new FormData();
    fd.append("image", blob, "photo.jpg");
    const { cards } = await api("/api/scan", { body: fd });
    if (!cards.length) throw new Error("Na fotce jsem nenašel žádnou Lorcana kartu. Zkus ostřejší fotku bez odlesků.");
    const created = cards.map((card) => ({
      id: uid(), card, qty: 1, foil: !!card.is_foil, foilUnsure: card.foil_confidence !== "high",
      status: "loading", product: null, candidates: [], price: null,
    }));
    items = [...created, ...items];
    save(); render();
    toast(`Nalezeno karet: ${cards.length}. Hledám ceny…`);
    job.remove();
    created.forEach((it) => lookup(it));
  } catch (e) {
    job.classList.add("error");
    job.innerHTML = `<img alt=""><span>⚠️ ${escapeHtml(e.message)}</span>`;
    $("img", job).src = url;
    job.addEventListener("click", () => job.remove());
  }
}

// --------------------------------------------------------------- lookup
async function lookup(it) {
  it.status = "loading"; it.error = null; renderItem(it);
  try {
    const res = await enqueue(() => api("/api/lookup", { json: { card: it.card, foil: it.foil, min_condition: minCond() } }));
    if (!res.product) { it.status = "notfound"; }
    else {
      it.product = res.product; it.candidates = res.candidates; it.price = res.price; it.status = "ok";
    }
  } catch (e) { it.status = "error"; it.error = e.message; }
  save(); renderItem(it); renderSummary();
}

async function reprice(it, refresh = false) {
  if (!it.product) return lookup(it);
  it.status = "loading"; it.error = null; renderItem(it);
  try {
    it.price = await enqueue(() => api("/api/price", { json: { url: it.product.url, foil: it.foil, min_condition: minCond(), refresh } }));
    it.status = "ok";
  } catch (e) { it.status = "error"; it.error = e.message; }
  save(); renderItem(it); renderSummary();
}

// --------------------------------------------------------------- render
function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

function render() {
  const list = $("#list");
  const existing = new Map([...list.children].map((el) => [el.dataset.id, el]));
  items.forEach((it, i) => {
    let el = existing.get(it.id);
    if (!el) el = buildItem(it);
    existing.delete(it.id);
    if (list.children[i] !== el) list.insertBefore(el, list.children[i] || null);
    fillItem(el, it);
  });
  existing.forEach((el) => el.remove());
  renderSummary();
}

function renderItem(it) {
  const el = $(`#list [data-id="${it.id}"]`);
  if (el) fillItem(el, it); else render();
}

function buildItem(it) {
  const el = $("#itemTpl").content.firstElementChild.cloneNode(true);
  el.dataset.id = it.id;
  const get = () => items.find((x) => x.id === el.dataset.id);
  $(".remove", el).onclick = () => { items = items.filter((x) => x.id !== el.dataset.id); save(); render(); };
  $(".foil-toggle", el).onclick = () => { const x = get(); x.foil = !x.foil; x.foilUnsure = false; reprice(x); };
  $(".plus", el).onclick = () => { const x = get(); x.qty++; save(); fillItem(el, x); renderSummary(); };
  $(".minus", el).onclick = () => { const x = get(); if (x.qty > 1) { x.qty--; save(); fillItem(el, x); renderSummary(); } };
  $(".reload", el).onclick = () => reprice(get(), true);
  $(".variant", el).onchange = (ev) => {
    const x = get(); const c = x.candidates.find((c) => c.url === ev.target.value);
    if (c) { x.product = c; reprice(x); }
  };
  return el;
}

function fillItem(el, it) {
  const p = it.price || {};
  const prod = it.product || {};
  el.classList.toggle("is-foil", !!it.foil);
  const img = $(".thumb img", el);
  const src = p.image || prod.image || "";
  if (img.getAttribute("src") !== src) { if (src) img.src = src; else img.removeAttribute("src"); }
  $(".name", el).textContent = it.card.name || prod.name || "?";
  $(".version", el).textContent = it.card.version || "";
  const meta = [p.expansion || prod.expansion, (p.number || prod.number) && `#${p.number || prod.number}`, p.rarity || prod.rarity || it.card.rarity,
    it.card.language && it.card.language !== "EN" ? `na fotce: ${it.card.language}` : null].filter(Boolean);
  $(".meta", el).textContent = meta.join(" · ");
  const ft = $(".foil-toggle", el);
  ft.textContent = it.foil ? "✨ Foil" : "Non-foil";
  ft.classList.toggle("on", !!it.foil);
  ft.classList.toggle("unsure", !!it.foilUnsure);
  ft.title = it.foilUnsure ? "Nejisté rozpoznání foilu – klepnutím přepneš" : "Klepnutím přepneš foil / non-foil";
  $(".q", el).textContent = it.qty;

  const sel = $(".variant", el);
  sel.hidden = !(it.candidates && it.candidates.length > 1);
  const opts = (it.candidates || []).map((c) =>
    `<option value="${escapeHtml(c.url)}">${escapeHtml(c.name)} · ${escapeHtml(c.expansion || "")}${c.number ? " #" + escapeHtml(c.number) : ""}${c.from_price != null ? " · od " + eur(c.from_price) : ""}</option>`).join("");
  if (sel.dataset.opts !== opts) { sel.innerHTML = opts; sel.dataset.opts = opts; }
  if (prod.url) sel.value = prod.url;

  $(".low b", el).textContent = eur(p.cm_low_en);
  $(".med b", el).textContent = eur(p.median5_en);
  $(".trend b", el).textContent = eur(p.price_trend);

  const st = $(".state", el);
  st.className = "state";
  if (it.status === "loading") st.innerHTML = `<div class="spinner"></div> Hledám na Cardmarketu…`;
  else if (it.status === "error") { st.classList.add("err"); st.textContent = "⚠️ " + it.error + " (klepni na 🔄 obnovit)"; }
  else if (it.status === "notfound") { st.classList.add("err"); st.textContent = "Kartu se nepodařilo najít na Cardmarketu – zkus ruční hledání nahoře."; }
  else if (p.offers_en === 0) { st.classList.add("warn"); st.textContent = `Žádná anglická ${it.foil ? "foil " : ""}nabídka.`; }
  else {
    const notes = [];
    if (p.offers_en != null) notes.push(`${p.offers_en}${p.offers_en >= 50 ? "+" : ""} EN nabídek`);
    if (p.foil_filter_ignored) { st.classList.add("warn"); notes.push("prodejci nemají označeno jako foil – ceny bez foil filtru"); }
    if (p.fetched_at) notes.push("aktualizováno " + new Date(p.fetched_at * 1000).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" }));
    st.textContent = notes.join(" · ");
  }
  const ol = $(".offers ol", el);
  ol.innerHTML = (p.first5 || []).map((o) => `<li><b>${eur(o.price)}</b> · ${escapeHtml(o.condition || "")} · ${escapeHtml(o.country || "")} · ${escapeHtml(o.seller || "")}</li>`).join("");
  $(".offers", el).hidden = !(p.first5 && p.first5.length);
  const link = $(".cm-link", el);
  if (prod.url) {
    link.href = prod.url + `?language=1&isFoil=${it.foil ? "Y" : "N"}` + (minCond() ? `&minCondition=${minCond()}` : "");
    link.hidden = false;
  } else link.hidden = true;
}

function renderSummary() {
  $("#summary").hidden = !items.length;
  $("#empty").hidden = !!items.length;
  const count = items.reduce((s, x) => s + x.qty, 0);
  const sum = (k) => items.reduce((s, x) => s + (x.price && x.price[k] != null ? x.price[k] * x.qty : 0), 0);
  $("#tCount").textContent = count;
  $("#tLow").textContent = eur(sum("cm_low_en"));
  $("#tMed").textContent = eur(sum("median5_en"));
}

// --------------------------------------------------------------- export
function rows() {
  return items.map((x) => {
    const p = x.price || {}, pr = x.product || {};
    return {
      "Název": x.card.name + (x.card.version ? " - " + x.card.version : ""),
      "Edice": p.expansion || pr.expansion || "",
      "Číslo": p.number || pr.number || x.card.collector_number || "",
      "Foil": x.foil ? "ano" : "ne",
      "Ks": x.qty,
      "CM low EN": p.cm_low_en ?? "",
      "Medián 5 EN": p.median5_en ?? "",
      "Trend": p.price_trend ?? "",
      "Odkaz": pr.url || "",
    };
  });
}

function exportCsv() {
  const r = rows();
  if (!r.length) return;
  const head = Object.keys(r[0]);
  const fmt = (v) => typeof v === "number" ? String(v).replace(".", ",") : `"${String(v).replace(/"/g, '""')}"`;
  const csv = "﻿" + [head.join(";"), ...r.map((o) => head.map((h) => fmt(o[h])).join(";"))].join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  a.download = `lorcana-ceny-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
}

async function copyText() {
  const lines = items.map((x) => {
    const p = x.price || {};
    return `${x.qty}× ${x.card.name}${x.card.version ? " - " + x.card.version : ""}${x.foil ? " (foil)" : ""}: low ${eur(p.cm_low_en)}, medián ${eur(p.median5_en)}`;
  });
  lines.push(`Celkem: low ${$("#tLow").textContent}, medián ${$("#tMed").textContent}`);
  try { await navigator.clipboard.writeText(lines.join("\n")); toast("Zkopírováno do schránky"); }
  catch { toast("Kopírování se nepovedlo"); }
}

// ---------------------------------------------------------- manual add
async function manualSearch(q) {
  const box = $("#manualResults");
  box.innerHTML = `<div class="job"><div class="spinner"></div><span>Hledám „${escapeHtml(q)}“…</span></div>`;
  try {
    const { results } = await api("/api/search?q=" + encodeURIComponent(q));
    if (!results.length) { box.innerHTML = `<div class="job error">Nic nenalezeno. Zkus jen jméno postavy nebo podtitul (anglicky).</div>`; return; }
    box.innerHTML = "";
    results.slice(0, 12).forEach((r) => {
      const b = document.createElement("button");
      b.type = "button";
      b.innerHTML = `<img alt=""><span><b>${escapeHtml(r.name)}</b><small>${escapeHtml(r.expansion || "")}${r.number ? " #" + escapeHtml(r.number) : ""}${r.from_price != null ? " · od " + eur(r.from_price) : ""}</small></span>`;
      if (r.image) $("img", b).src = r.image;
      b.onclick = () => {
        const [name, ...rest] = (r.name || "").replace(/\s*\(V\.\d+\)\s*$/, "").split(" - ");
        const it = { id: uid(), card: { name, version: rest.join(" - "), rarity: r.rarity }, qty: 1, foil: false, foilUnsure: false,
          status: "loading", product: r, candidates: results, price: null };
        items.unshift(it); save(); render(); reprice(it);
        box.innerHTML = ""; $("#manualInput").value = "";
      };
      box.appendChild(b);
    });
    const close = document.createElement("button");
    close.type = "button"; close.textContent = "Zavřít výsledky"; close.style.justifyContent = "center";
    close.onclick = () => (box.innerHTML = "");
    box.appendChild(close);
  } catch (e) {
    box.innerHTML = `<div class="job error">⚠️ ${escapeHtml(e.message)}</div>`;
  }
}

// ------------------------------------------------------------ settings
async function openSettings() {
  $("#pwInput").value = settings.password || "";
  $("#condSelect").value = settings.minCondition || "";
  const d = $("#settings");
  if (!d.open) d.showModal();
  try {
    const s = await api("/api/status");
    const v = { claude: "Claude", gemini: "Gemini", none: "<b class=bad>CHYBÍ API klíč</b>" }[s.vision] || s.vision;
    const f = s.fetch_modes.length ? s.fetch_modes.map((m) => ({ direct: "přímo", scrapedo: "Scrape.do" }[m] || m)).join(" → ") : "<b class=bad>žádné</b>";
    $("#statusBox").innerHTML = `Server: <b class=ok>běží</b><br>Rozpoznávání: ${v}<br>Stahování Cardmarketu: ${f}` +
      `<br><small>Načteno přímo: ${s.stats.direct_ok}, přes Scrape.do: ${s.stats.scrapedo_ok} (kredity ${s.stats.scrapedo_credits})</small>`;
  } catch (e) { $("#statusBox").textContent = "Server nedostupný: " + e.message; }
}

$("#settings").addEventListener("close", () => {
  const changed = settings.minCondition !== $("#condSelect").value;
  settings = { password: $("#pwInput").value, minCondition: $("#condSelect").value };
  LS.set("settings", settings);
  if (changed && items.length) items.forEach((x) => reprice(x));
});

// --------------------------------------------------------------- wiring
$("#camInput").onchange = (e) => { handleFiles([...e.target.files]); e.target.value = ""; };
$("#fileInput").onchange = (e) => { handleFiles([...e.target.files]); e.target.value = ""; };
$("#manualForm").onsubmit = (e) => { e.preventDefault(); const q = $("#manualInput").value.trim(); if (q) manualSearch(q); };
$("#settingsBtn").onclick = openSettings;
$("#csvBtn").onclick = exportCsv;
$("#copyBtn").onclick = copyText;
$("#refreshAllBtn").onclick = () => items.forEach((x) => reprice(x, true));
$("#clearBtn").onclick = () => { if (confirm("Opravdu vymazat celý seznam?")) { items = []; save(); render(); } };
document.addEventListener("paste", (e) => {
  const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
  if (files.length) handleFiles(files);
});
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => {
  e.preventDefault();
  const files = [...(e.dataTransfer?.files || [])].filter((f) => f.type.startsWith("image/"));
  if (files.length) handleFiles(files);
});

// Card images come from Cardmarket's CDN; hide the broken-image icon if one does not load.
document.addEventListener("error", (e) => { if (e.target.tagName === "IMG") e.target.classList.add("broken"); }, true);
document.addEventListener("load", (e) => { if (e.target.tagName === "IMG") e.target.classList.remove("broken"); }, true);

// Items interrupted mid-lookup (page closed) get finished now.
items.forEach((x) => { if (x.status === "loading") lookup(x); });
render();
api("/api/status").then((s) => { if (s.vision === "none") toast("Server nemá nastavený API klíč pro rozpoznávání – viz README.", 6000); }).catch(() => {});
