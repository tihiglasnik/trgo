"""
Pregled po cijeni: za dani upit (npr. "kava") vraca SVE odgovarajuce
proizvode dostupne u obliznjim poslovnicama, sortirano od najjeftinijeg -
za razliku od product_matcher.search() koji trazi JEDAN tocno odredjen
proizvod za dodavanje u kosaricu.

Podrzava:
- iskljucivanje rijeci s predznakom "-" (npr. "kava -instant" trazi kavu,
  ali ne i instant kavu)
- filtriranje po tezini u gramima (samo za proizvode s jedinicom g/kg -
  proizvodi u L/ml/kom se pri aktivnom filteru tezine izostave, jer im
  tezina nije primjenjiva)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from geo import clean_address, haversine_km
from product_matcher import normalize_name

CANDIDATE_POOL = 500  # koliko razlicitih proizvoda (EAN-ova) razmatramo prije filtera tezine


def _split_tokens(query: str) -> tuple[list[str], list[str]]:
    """Vraca (ukljuceni tokeni, iskljuceni tokeni). Token s predznakom '-' je iskljucen."""
    include: list[str] = []
    exclude: list[str] = []
    for raw in query.split():
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith("-") and len(raw) > 1:
            t = normalize_name(raw[1:])
            if t:
                exclude.append(t)
        else:
            t = normalize_name(raw)
            if t:
                include.append(t)
    return include, exclude


def _fts_term(t: str) -> str:
    t = t.replace('"', '""')
    return f'"{t}"' if t.isdigit() else f'"{t}"*'


def _fts_expr(include: list[str], exclude: list[str]) -> str | None:
    if not include:
        return None
    expr = " ".join(_fts_term(t) for t in include)
    if exclude:
        excl = " OR ".join(_fts_term(t) for t in exclude)
        expr = f"({expr}) NOT ({excl})"
    return expr


def _to_grams(quantity: float | None, unit: str | None) -> float | None:
    if quantity is None or not unit:
        return None
    unit = unit.strip().lower()
    if unit == "kg":
        return quantity * 1000
    if unit == "g":
        return quantity
    return None  # L, ml, kom, m, par... - tezina nije primjenjiva


@dataclass
class BrowseResult:
    ean: str
    label: str
    price: float
    chain: str
    address: str
    city: str
    distance_km: float


def browse_by_price(
    conn: sqlite3.Connection,
    query: str,
    lat: float,
    lon: float,
    radius_km: float,
    min_grams: float | None = None,
    max_grams: float | None = None,
    limit: int = 15,
    offset: int = 0,
) -> tuple[list[BrowseResult], int]:
    """Vraca (stranica_rezultata, ukupan_broj_rezultata) - potonje omogucuje
    "ucitaj jos" u sucelju bez ponovnog pogadjanja koliko ih ukupno ima."""
    include, exclude = _split_tokens(query)
    match = _fts_expr(include, exclude)
    if not match:
        return []

    candidates = conn.execute(
        """
        SELECT cp.ean, cp.brand, cp.name, cp.unit, cp.quantity
        FROM fts_canonical f
        JOIN canonical_products cp ON cp.rowid = f.rowid
        WHERE fts_canonical MATCH ?
        LIMIT ?
        """,
        (match, CANDIDATE_POOL),
    ).fetchall()
    if not candidates:
        return []

    if min_grams is not None or max_grams is not None:
        kept = []
        for row in candidates:
            grams = _to_grams(row[4], row[3])
            if grams is None:
                continue
            if min_grams is not None and grams < min_grams:
                continue
            if max_grams is not None and grams > max_grams:
                continue
            kept.append(row)
        candidates = kept
    if not candidates:
        return []

    info = {row[0]: row for row in candidates}
    ean_list = list(info.keys())

    stores = conn.execute(
        "SELECT chain, store_code, address, city, lat, lon FROM stores WHERE lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()
    nearby = []
    for chain, store_code, address, city, slat, slon in stores:
        d = haversine_km(lat, lon, slat, slon)
        if d <= radius_km:
            nearby.append((chain, store_code, round(d, 2), clean_address(address), city))
    if not nearby:
        return []

    # Privremena tablica poslovnica u radijusu, pa JOIN protiv nje - umjesto
    # dovlacenja cijena iz CIJELE drzave i odbacivanja u Pythonu. Raniji,
    # naivan pristup (300 kandidata x cijena iz svih poslovnica u drzavi)
    # izmjeren je na ~30 sekundi; ovaj dotice samo redove za stvarno
    # obliznjih poslovnica (u testu ~50-700, ovisno o radijusu).
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS radius_stores "
        "(chain TEXT, store_code TEXT, distance_km REAL, address TEXT, city TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS temp_idx_radius_stores ON radius_stores(chain, store_code)")
    conn.execute("DELETE FROM radius_stores")
    conn.executemany("INSERT INTO radius_stores VALUES (?,?,?,?,?)", nearby)

    placeholders = ",".join("?" for _ in ean_list)
    rows = conn.execute(
        f"""
        SELECT cp.canonical_ean, rs.chain, rs.store_code, p.price, rs.distance_km, rs.address, rs.city
        FROM radius_stores rs
        JOIN prices p ON p.chain = rs.chain AND p.store_code = rs.store_code
        JOIN chain_products cp ON cp.chain = p.chain AND cp.product_code = p.product_code
        WHERE cp.canonical_ean IN ({placeholders}) AND p.price IS NOT NULL
        """,
        ean_list,
    ).fetchall()

    best: dict[str, tuple] = {}
    for ean, chain, store_code, price, dist, address, city in rows:
        cur = best.get(ean)
        if cur is None or price < cur[0]:
            best[ean] = (price, chain, store_code, dist, address, city)

    results = []
    for ean, (price, chain, store_code, dist, address, city) in best.items():
        _, brand, name, unit, qty = info[ean]
        qty_txt = f"{qty:g} {unit}" if qty else (unit or "")
        label = " ".join(part for part in (brand, name, qty_txt) if part)
        results.append(BrowseResult(ean=ean, label=label, price=price, chain=chain,
                                     address=address, city=city, distance_km=dist))

    results.sort(key=lambda r: r.price)
    return results[offset:offset + limit], len(results)
