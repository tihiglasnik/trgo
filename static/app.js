"use strict";

/* ---- mali DOM helper (izbjegava innerHTML za tekst koji dolazi iz baze,
        da nema nikakve XSS povrsine bez obzira koliko je izvor "pouzdan") */
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function fmtPrice(v) {
  return (typeof v === "number" ? v.toFixed(2) : "?") + " EUR";
}
function fmtDist(v) {
  return (v === null || v === undefined) ? "?" : v.toFixed(2) + " km";
}

/* ---- stanje ----------------------------------------------------------- */
let rowSeq = 0;
const rows = new Map(); // id -> { text, candidates, selected, debounceTimer }

const rowsContainer = document.getElementById("product-rows");
const addProductBtn = document.getElementById("add-product");
const form = document.getElementById("search-form");
const submitBtn = document.getElementById("submit-btn");
const formError = document.getElementById("form-error");
const radiusInput = document.getElementById("radius");
const radiusValue = document.getElementById("radius-value");
const addressInput = document.getElementById("address");
const addressHint = document.getElementById("address-hint");

const resultsSection = document.getElementById("results");
const emptyState = document.getElementById("empty-state");
const perItemList = document.getElementById("per-item-list");
const summaryCards = document.getElementById("summary-cards");
const completeList = document.getElementById("complete-list");
const allStoresList = document.getElementById("all-stores-list");
const allStoresDetails = document.getElementById("all-stores-details");

radiusInput.addEventListener("input", () => {
  radiusValue.textContent = radiusInput.value;
});

/* ---- osvjezavanje podataka (brise staru arhivu, povlaci novu) ---------- */
const refreshBtn = document.getElementById("refresh-btn");
const refreshStatus = document.getElementById("refresh-status");

refreshBtn.addEventListener("click", async () => {
  const confirmed = confirm(
    "Ovo briše preuzetu arhivu i bazu, pa povlači i uvozi najnovije podatke. " +
    "Može potrajati oko minute, ovisno o internetskoj vezi. Nastaviti?"
  );
  if (!confirmed) return;

  refreshBtn.disabled = true;
  refreshBtn.textContent = "⏳ Osvježavam...";
  refreshStatus.hidden = false;
  refreshStatus.className = "refresh-status";
  refreshStatus.textContent = "Preuzimam najnoviju arhivu i katalog, ovo može potrajati...";

  try {
    const resp = await fetch("/api/refresh", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) {
      refreshStatus.className = "refresh-status error-text";
      refreshStatus.textContent = data.error || "Osvježavanje nije uspjelo.";
      return;
    }
    refreshStatus.className = "refresh-status ok-text";
    refreshStatus.textContent = data.failed_chains && data.failed_chains.length
      ? `Gotovo, ali neki lanci nisu uvezeni: ${data.failed_chains.join(", ")}`
      : "Gotovo — podaci su ažurirani.";
  } catch (err) {
    refreshStatus.className = "refresh-status error-text";
    refreshStatus.textContent = "Veza je prekinuta prije nego je osvježavanje završilo - provjeri je li server još pokrenut.";
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = "🔄 Osvježi podatke";
  }
});

let currentUser = null;

/* Skuplja proizvode iz redova: odabrani, ili prvi kandidat ako nije eksplicitno odabran. */
function collectItems() {
  const items = [];
  for (const [, state] of rows) {
    const text = state.text.trim();
    if (!text) continue;
    const c0 = state.candidates[0];
    const chosen = state.selected || (c0 ? {
      ean: c0.ean, name: c0.ean ? null : c0.label, label: c0.label,
    } : null);
    if (!chosen) continue; // nema jos rezultata pretrage za ovaj red
    items.push({ query: text, ean: chosen.ean, name: chosen.name, label: chosen.label });
  }
  return items;
}

/* ---- redovi proizvoda --------------------------------------------------*/
function addProductRow(prefill = "", preselected = null) {
  const id = ++rowSeq;
  rows.set(id, { text: prefill, candidates: [], selected: preselected, debounceTimer: null });
  renderRow(id);
  if (preselected) renderCandidates(id);
  else if (prefill) searchForRow(id, prefill);
}

function removeProductRow(id) {
  rows.delete(id);
  document.getElementById(`row-${id}`)?.remove();
  if (rows.size === 0) addProductRow();
}

function renderRow(id) {
  const state = rows.get(id);
  const wrap = el("div", { class: "product-row", id: `row-${id}` });

  const top = el("div", { class: "product-row-top" });
  const input = el("input", {
    type: "text", placeholder: "npr. mlijeko", value: state.text,
    oninput: (e) => {
      state.selected = null;
      state.text = e.target.value;
      clearTimeout(state.debounceTimer);
      state.debounceTimer = setTimeout(() => searchForRow(id, state.text), 300);
      renderCandidates(id);
    },
  });
  const removeBtn = el("button", { type: "button", class: "remove-row", title: "Ukloni", onclick: () => removeProductRow(id) }, "\u00d7");
  top.appendChild(input);
  top.appendChild(removeBtn);
  wrap.appendChild(top);

  wrap.appendChild(el("div", { id: `candidates-${id}` }));
  rowsContainer.appendChild(wrap);
}

async function searchForRow(id, query) {
  const state = rows.get(id);
  if (!state) return;
  query = query.trim();
  if (query.length < 2) {
    state.candidates = [];
    renderCandidates(id);
    return;
  }
  let data;
  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    if (!resp.ok) throw new Error("pretraga nije uspjela");
    data = await resp.json();
  } catch (err) {
    state.candidates = [];
    renderCandidates(id, "Pretraga trenutno nije dostupna.");
    return;
  }
  if (!rows.has(id)) return; // red je mozda uklonjen dok je fetch trajao
  if (state.text.trim() !== query) return; // korisnik je u medjuvremenu promijenio tekst
  state.candidates = data.candidates;
  if (data.remembered && !state.selected) {
    state.selected = { ean: data.remembered.ean, name: data.remembered.name,
                       label: data.remembered.label, remembered: true };
  }
  renderCandidates(id);
}

function renderCandidates(id, errorMsg) {
  const state = rows.get(id);
  const host = document.getElementById(`candidates-${id}`);
  if (!host || !state) return;
  host.innerHTML = "";

  if (state.selected) {
    host.appendChild(el(
      "div", { class: "chosen-label" },
      el("span", {}, `\u2713 ${state.selected.label}${state.selected.remembered ? " (zapamćeno)" : ""}`),
      el("button", { type: "button", class: "link-btn", onclick: () => {
        state.selected = null;
        renderCandidates(id);
      } }, "promijeni")
    ));
    return;
  }
  if (errorMsg) {
    host.appendChild(el("p", { class: "hint error-text" }, errorMsg));
    return;
  }
  if (!state.candidates.length) return;

  const list = el("div", { class: "candidate-list" });
  for (const c of state.candidates) {
    const item = el(
      "div",
      {
        class: "candidate-item",
        onclick: () => {
          state.selected = { ean: c.ean, name: c.ean ? null : c.label, label: c.label };
          renderCandidates(id);
          if (currentUser && state.text.trim()) {
            fetch("/api/remember", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ query: state.text.trim(), ean: c.ean, name: state.selected.name, label: c.label }),
            }).catch(() => {});
          }
        },
      },
      el("span", {}, c.label),
      el("span", { class: "n-chains" }, `${c.chains_available} lanaca`)
    );
    list.appendChild(item);
  }
  host.appendChild(list);
}

addProductBtn.addEventListener("click", () => addProductRow());
addProductRow();
addProductRow();
addProductRow();

/* ---- submit ------------------------------------------------------------*/
function setBusy(busy) {
  submitBtn.disabled = busy;
  submitBtn.textContent = busy ? "Tražim..." : "Pronađi najbolju kupnju";
}

function showFormError(msg) {
  formError.textContent = msg;
  formError.hidden = !msg;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  showFormError("");
  addressHint.textContent = "";
  addressHint.className = "hint";

  const address = addressInput.value.trim();
  if (!address) {
    showFormError("Unesi adresu.");
    return;
  }

  const items = collectItems();

  if (items.length === 0) {
    showFormError("Dodaj barem jedan prepoznat proizvod.");
    return;
  }

  setBusy(true);
  try {
    addressHint.textContent = "Tražim adresu...";
    const geoResp = await fetch("/api/geocode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address }),
    });
    const geoData = await geoResp.json();
    if (!geoResp.ok) {
      showFormError(geoData.error || "Geokodiranje nije uspjelo.");
      addressHint.textContent = "";
      return;
    }
    addressHint.textContent = `\u2713 ${geoData.lat.toFixed(5)}, ${geoData.lon.toFixed(5)}`;
    addressHint.className = "hint ok-text";

    const findResp = await fetch("/api/find", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: geoData.lat, lon: geoData.lon, radius: parseFloat(radiusInput.value), items }),
    });
    const findData = await findResp.json();
    if (!findResp.ok) {
      showFormError(findData.error || "Pretraga nije uspjela.");
      return;
    }
    renderResults(findData, parseFloat(radiusInput.value));
  } catch (err) {
    showFormError("Nesto je posla po zlu - provjeri internetsku vezu i pokusaj ponovno.");
  } finally {
    setBusy(false);
  }
});

/* ---- prikaz rezultata ---------------------------------------------------*/
function receiptRow(name, addr, priceText, distText, opts = {}) {
  const fullName = addr ? `${name} — ${addr}` : name;
  return el(
    "div",
    { class: `receipt-row${opts.best ? " best" : ""}${opts.missing ? " missing" : ""}` },
    el("span", { class: "name", title: fullName }, fullName),
    el("span", { class: "leader" }),
    distText ? el("span", { class: "meta" }, distText) : null,
    el("span", { class: "price" }, priceText)
  );
}

/* Za redove gdje je naziv predugacak da stane u jedan redak (npr. puni naziv
   proizvoda + trgovina + adresa) - naziv ide u svoj red i slobodno prelama,
   ne rezemo ga trotockom. Koristi se za "Pregled po cijeni" i "Sve poslovnice". */
function wideReceiptRow(name, storeText, priceText, distText, opts = {}) {
  return el(
    "div",
    { class: `receipt-row-wide${opts.best ? " best" : ""}${opts.missing ? " missing" : ""}` },
    el("div", { class: "rw-name" }, name),
    el(
      "div", { class: "rw-meta" },
      el("span", { class: "rw-store" }, [storeText, distText].filter(Boolean).join(" · ")),
      el("span", { class: "rw-price" }, priceText)
    ),
    opts.missingText ? el("div", { class: "rw-missing" }, opts.missingText) : null
  );
}

function summaryCard(icon, label, store) {
  return el(
    "div", { class: "summary-card" },
    el("div", { class: "summary-card-label" }, `${icon} ${label}`),
    el("div", { class: "summary-card-store" }, `${store.chain} — ${store.address}, ${store.city}`),
    el(
      "div", { class: "summary-card-figures" },
      el("span", { class: "summary-card-price" }, fmtPrice(store.total)),
      el("span", { class: "summary-card-dist" }, fmtDist(store.distance_km)),
      store.missing.length ? el("span", { class: "summary-card-missing" }, `nedostaje: ${store.missing.join(", ")}`) : null
    )
  );
}

function renderResults(data, radius) {
  emptyState.hidden = true;
  resultsSection.hidden = false;

  summaryCards.innerHTML = "";
  if (data.nearest_overall) {
    summaryCards.appendChild(summaryCard("🏠", "Najbliža poslovnica ukupno", data.nearest_overall));
  }
  if (data.compromise) {
    summaryCards.appendChild(summaryCard("⚖️", "Kompromis cijena/udaljenost", data.compromise));
  }

  perItemList.innerHTML = "";
  for (const item of data.per_item) {
    const block = el("div", { class: "item-block" });
    block.appendChild(el("div", { class: "item-name" }, `\ud83d\udce6 ${item.label || item.query}`));
    if (!item.found) {
      block.appendChild(el("div", { class: "not-found" }, `Nije pronadjen ni u jednoj poslovnici unutar ${radius} km.`));
    } else {
      for (const c of item.chains) {
        block.appendChild(el(
          "div", { class: "sub-row" },
          el("span", { class: "label" }, c.chain),
          el("span", { class: "val" }, fmtPrice(c.price))
        ));
      }
    }
    perItemList.appendChild(block);
  }

  completeList.innerHTML = "";
  if (data.chains_summary.length === 0) {
    completeList.appendChild(el("div", { class: "receipt-row" }, el("span", {}, "Nijedna poslovnica u radijusu nema sve tra\u017eene proizvode zajedno.")));
  } else {
    data.chains_summary.forEach((cs, i) => {
      const rowId = `chain-detail-${i}`;
      const row = el(
        "div", { class: `receipt-row chain-row${i === 0 ? " best" : ""}`, role: "button", tabindex: "0",
          onclick: () => toggleChainDetail(rowId) },
        el("span", { class: "name" }, `${cs.chain} ${cs.stores_available > 1 ? `(${cs.stores_available} poslovnica)` : ""}`),
        el("span", { class: "leader" }),
        el("span", { class: "meta chain-toggle-hint" }, "prika\u017ei najbli\u017eu \u25be"),
        el("span", { class: "price" }, fmtPrice(cs.min_total))
      );
      completeList.appendChild(row);

      const n = cs.nearest;
      const detail = el(
        "div", { class: "chain-detail", id: rowId, hidden: "hidden" },
        el("span", { class: "chain-detail-addr" }, `\ud83d\udccd ${n.address}, ${n.city}`),
        el("span", { class: "chain-detail-figures" },
          `${fmtDist(n.distance_km)} \u00b7 ${fmtPrice(n.total)}${n.missing.length ? " \u00b7 nedostaje: " + n.missing.join(", ") : ""}`)
      );
      completeList.appendChild(detail);
    });
  }

  allStoresList.innerHTML = "";
  for (const r of data.all_stores) {
    const missingText = r.missing.length ? `Nedostaje: ${r.missing.join(", ")}` : null;
    allStoresList.appendChild(wideReceiptRow(
      r.chain, `${r.address}, ${r.city}`, fmtPrice(r.total), fmtDist(r.distance_km),
      { missing: r.missing.length > 0, missingText }
    ));
  }
  if (data.all_stores_total > data.all_stores.length) {
    allStoresList.appendChild(el("div", { class: "receipt-row" },
      el("span", { class: "meta" }, `... i jo\u0161 ${data.all_stores_total - data.all_stores.length} poslovnica`)));
  }
}

function toggleChainDetail(rowId) {
  const detail = document.getElementById(rowId);
  if (!detail) return;
  detail.hidden = !detail.hidden;
}


/* ---- prijava / registracija --------------------------------------------- */
const authArea = document.getElementById("auth-area");
const authDialog = document.getElementById("auth-dialog");
const authForm = document.getElementById("auth-form");
const authEmail = document.getElementById("auth-email");
const authPassword = document.getElementById("auth-password");
const authPassHint = document.getElementById("auth-pass-hint");
const authError = document.getElementById("auth-error");
const authSubmit = document.getElementById("auth-submit");
const tabLogin = document.getElementById("tab-login");
const tabRegister = document.getElementById("tab-register");
let authMode = "login";

function setAuthMode(mode) {
  authMode = mode;
  tabLogin.classList.toggle("active", mode === "login");
  tabRegister.classList.toggle("active", mode === "register");
  authSubmit.textContent = mode === "login" ? "Prijavi se" : "Napravi račun";
  authPassword.setAttribute("autocomplete", mode === "login" ? "current-password" : "new-password");
  authPassHint.textContent = mode === "register" ? "(najmanje 8 znakova)" : "";
  authError.hidden = true;
}

function openAuthDialog(mode) {
  setAuthMode(mode);
  authForm.reset();
  authDialog.showModal();
  authEmail.focus();
}

tabLogin.addEventListener("click", () => setAuthMode("login"));
tabRegister.addEventListener("click", () => setAuthMode("register"));
document.getElementById("auth-cancel").addEventListener("click", () => authDialog.close());

authForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  authError.hidden = true;
  authSubmit.disabled = true;
  try {
    const resp = await fetch(`/api/auth/${authMode === "login" ? "login" : "register"}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: authEmail.value, password: authPassword.value }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      authError.textContent = data.error || "Nešto je pošlo po zlu.";
      authError.hidden = false;
      return;
    }
    currentUser = data.user;
    authForm.reset();
    authDialog.close();
    renderAuthArea();
    loadLists();
  } catch (err) {
    authError.textContent = "Veza sa serverom nije uspjela.";
    authError.hidden = false;
  } finally {
    authSubmit.disabled = false;
  }
});

async function doLogout() {
  try { await fetch("/api/auth/logout", { method: "POST" }); } catch (err) { /* svejedno */ }
  currentUser = null;
  renderAuthArea();
}

const listsPanel = document.getElementById("lists-panel");
const listSelect = document.getElementById("list-select");
const listsHint = document.getElementById("lists-hint");

function renderAuthArea() {
  authArea.innerHTML = "";
  if (currentUser) {
    authArea.appendChild(el("span", { class: "auth-email", title: currentUser.email }, currentUser.email));
    authArea.appendChild(el("button", { type: "button", class: "btn-refresh", onclick: doLogout }, "Odjava"));
  } else {
    authArea.appendChild(el("button", { type: "button", class: "btn-refresh", onclick: () => openAuthDialog("login") }, "Prijava"));
  }
  listsPanel.hidden = !currentUser;
}

/* ---- spremljene liste ---------------------------------------------------- */
function setListsHint(msg, kind) {
  listsHint.textContent = msg || "";
  listsHint.className = "hint" + (kind === "ok" ? " ok-text" : kind === "err" ? " error-text" : "");
}

async function loadLists(selectId) {
  try {
    const resp = await fetch("/api/lists");
    if (!resp.ok) return;
    const lists = await resp.json();
    listSelect.innerHTML = "";
    listSelect.appendChild(el("option", { value: "" }, lists.length ? "— odaberi listu —" : "(nema spremljenih lista)"));
    for (const l of lists) {
      listSelect.appendChild(el("option", { value: String(l.id) }, `${l.name} (${l.item_count})`));
    }
    if (selectId) listSelect.value = String(selectId);
  } catch (err) {
    setListsHint("Liste trenutno nisu dostupne.", "err");
  }
}

document.getElementById("list-load").addEventListener("click", async () => {
  const id = listSelect.value;
  if (!id) { setListsHint("Odaberi listu koju želiš učitati.", "err"); return; }
  try {
    const resp = await fetch(`/api/lists/${id}`);
    const lst = await resp.json();
    if (!resp.ok) { setListsHint(lst.error || "Lista nije učitana.", "err"); return; }
    for (const [, st] of rows) clearTimeout(st.debounceTimer);
    rows.clear();
    rowsContainer.innerHTML = "";
    for (const item of lst.items) {
      addProductRow(item.query, { ean: item.ean, name: item.name, label: item.label || item.query });
    }
    if (lst.items.length === 0) addProductRow();
    setListsHint(`Učitana lista „${lst.name}“.`, "ok");
  } catch (err) {
    setListsHint("Učitavanje liste nije uspjelo.", "err");
  }
});

document.getElementById("list-save").addEventListener("click", async () => {
  const items = collectItems();
  if (items.length === 0) { setListsHint("Dodaj barem jedan prepoznat proizvod prije spremanja.", "err"); return; }
  const name = (prompt("Naziv nove liste:") || "").trim();
  if (!name) return;
  try {
    const resp = await fetch("/api/lists", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const created = await resp.json();
    if (!resp.ok) { setListsHint(created.error || "Lista nije spremljena.", "err"); return; }
    for (const it of items) {
      const r = await fetch(`/api/lists/${created.id}/items`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(it),
      });
      if (!r.ok) { setListsHint("Lista je stvorena, ali nisu sve stavke spremljene.", "err"); break; }
    }
    await loadLists(created.id);
    setListsHint(`Spremljeno kao „${name}“.`, "ok");
  } catch (err) {
    setListsHint("Spremanje nije uspjelo.", "err");
  }
});

document.getElementById("list-delete").addEventListener("click", async () => {
  const id = listSelect.value;
  if (!id) { setListsHint("Odaberi listu koju želiš obrisati.", "err"); return; }
  if (!confirm("Obrisati odabranu listu?")) return;
  try {
    const resp = await fetch(`/api/lists/${id}`, { method: "DELETE" });
    if (!resp.ok) { setListsHint("Brisanje nije uspjelo.", "err"); return; }
    await loadLists();
    setListsHint("Lista obrisana.", "ok");
  } catch (err) {
    setListsHint("Brisanje nije uspjelo.", "err");
  }
});

/* ---- pocetno stanje: je li korisnik vec prijavljen (sesija u kolacicu) ---- */
(async function initAuth() {
  try {
    const resp = await fetch("/api/auth/me");
    const data = await resp.json();
    currentUser = data.user;
  } catch (err) {
    currentUser = null;
  }
  renderAuthArea();
  if (currentUser) loadLists();
})();

/* ---- pregled po cijeni (sve varijante, ne samo jedan odabrani proizvod) --- */
const browseQuery = document.getElementById("browse-query");
const browseBtn = document.getElementById("browse-btn");
const browseMinG = document.getElementById("browse-min-g");
const browseMaxG = document.getElementById("browse-max-g");
const browseExclude = document.getElementById("browse-exclude");
const browseHint = document.getElementById("browse-hint");
const browseResults = document.getElementById("browse-results");

function setBrowseHint(msg, kind) {
  browseHint.textContent = msg || "Koristi adresu i radijus postavljene gore. Riječ s \"-\" ispred isključuje (npr. \"-instant\").";
  browseHint.className = "hint" + (kind === "err" ? " error-text" : "");
}

async function runBrowse() {
  const q = browseQuery.value.trim();
  if (!q) { setBrowseHint("Upiši što tražiš (npr. \"kava\").", "err"); return; }
  const address = addressInput.value.trim();
  if (!address) { setBrowseHint("Prvo upiši adresu gore.", "err"); return; }

  browseBtn.disabled = true;
  browseBtn.textContent = "Tražim...";
  setBrowseHint("Tražim adresu...");
  browseResults.hidden = true;

  try {
    const geoResp = await fetch("/api/geocode", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address }),
    });
    const geoData = await geoResp.json();
    if (!geoResp.ok) { setBrowseHint(geoData.error || "Geokodiranje nije uspjelo.", "err"); return; }

    const exclWords = browseExclude.value.split(/[,\s]+/).map((w) => w.trim()).filter(Boolean);
    const fullQuery = [q, ...exclWords.map((w) => `-${w}`)].join(" ");

    const params = new URLSearchParams({
      q: fullQuery, lat: geoData.lat, lon: geoData.lon, radius: parseFloat(radiusInput.value), limit: 15,
    });
    if (browseMinG.value) params.set("min_g", browseMinG.value);
    if (browseMaxG.value) params.set("max_g", browseMaxG.value);

    const resp = await fetch(`/api/browse?${params.toString()}`);
    const data = await resp.json();
    if (!resp.ok) { setBrowseHint(data.error || "Pretraga nije uspjela.", "err"); return; }

    browseResults.innerHTML = "";
    if (data.results.length === 0) {
      browseResults.appendChild(el("div", { class: "receipt-row" }, el("span", {}, "Nema rezultata u ovom radijusu.")));
    } else {
      data.results.forEach((r, i) => {
        browseResults.appendChild(wideReceiptRow(
          r.label, `${r.chain} — ${r.address}, ${r.city}`,
          fmtPrice(r.price), fmtDist(r.distance_km), { best: i === 0 }
        ));
      });
    }
    browseResults.hidden = false;
    setBrowseHint(`${data.count} rezultata.`, data.count ? undefined : "err");
  } catch (err) {
    setBrowseHint("Veza sa serverom nije uspjela.", "err");
  } finally {
    browseBtn.disabled = false;
    browseBtn.textContent = "Traži";
  }
}

browseBtn.addEventListener("click", runBrowse);
browseQuery.addEventListener("keydown", (e) => { if (e.key === "Enter") runBrowse(); });
