"""
Pregled po cijeni: za dani upit (npr. "kava") vraca SVE odgovarajuce
proizvode dostupne u obliznjim poslovnicama, sortirano od najjeftinijeg -
za razliku od product_matcher.search() koji trazi JEDAN tocno odredjen
proizvod za dodavanje u kosaricu.

Podrzava:
- iskljucivanje s predznakom "-" (npr. "kava -instant -0.25")
- ukljucivanje brojeva (npr. "kava 0.25")
- filtriranje po tezini u gramima (proizvodi s jedinicom g/kg)
- filtriranje po volumenu u mililitrima (proizvodi s jedinicom l/ml)
  Ako su aktivna OBA filtera, proizvod prolazi ako zadovoljava filter svoje
  vrste (npr. "150-250 g ILI 200-300 ml"). Ako je aktivan samo jedan, prolaze
  samo proizvodi te vrste - ostalima (kom, drugacija jedinica) mjera nije
  primjenjiva pa se izostavljaju.

VAZNO o brojevima i iskljucivanju: FTS indeks pokriva samo marku i naziv, a
korisnik u rezultatima vidi i KOLICINU ("... 0.25 kg", iz kolone quantity).
Zato se svi tokeni koji sadrze znamenku te sva iskljucivanja provjeravaju i
nad PRIKAZANIM nazivom (marka + naziv + kolicina) - sto vidis, to se i
filtrira. Ranije je "-0.25" bio bez ucinka jer 0.25 postoji samo u koloni
kolicine, ne u nazivu.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from geo import clean_address, haversine_km
from product_matcher import normalize_name

_LETTERS = "a-zčćžšđ"  # slova (uklj. hrvatska) za granicu rijeci pri prefiks-poklapanju

# koliko razlicitih proizvoda (EAN-ova) razmatramo prije filtera i cijena.
# Ranije 500 je za siroke upite ("sir" ima ~860 kandidata) tiho odbacivalo dio
# proizvoda; broj ostaje ogranicen da upit cijena ostane brz.
CANDIDATE_POOL = 3000


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


def _has_digit(t: str) -> bool:
    return any(ch.isdigit() for ch in t)


def _fts_term(t: str) -> str:
    t = t.replace('"', '""')
    return f'"{t}"' if t.isdigit() else f'"{t}"*'


def _fts_expr(include: list[str], exclude: list[str]) -> str | None:
    """FTS izraz samo od RIJECI. Tokeni sa znamenkama (npr. 0.25, 3u1) ne idu u
    FTS jer se brojevi mogu nalaziti u koloni kolicine koju FTS ne indeksira -
    njih provjerava _label_matches nad prikazanim nazivom. Iznimka: ako nema
    nijedne rijeci, FTS dobiva brojeve (kao prije), da upit i dalje nesto vrati."""
    inc_words = [t for t in include if not _has_digit(t)]
    exc_words = [t for t in exclude if not _has_digit(t)]
    inc_fts = inc_words if inc_words else include
    if not inc_fts:
        return None
    expr = " ".join(_fts_term(t) for t in inc_fts)
    if exc_words:
        expr = f"({expr}) NOT ({' OR '.join(_fts_term(t) for t in exc_words)})"
    return expr


def _token_in_label(token: str, label_norm: str) -> bool:
    if _has_digit(token):
        # cijeli broj: "0.25" ne smije pogoditi "10.25" ni "0.250"
        pattern = rf"(?<![\d.]){re.escape(token)}(?!\d)"
    else:
        pattern = rf"(?<![{_LETTERS}]){re.escape(token)}"
    return re.search(pattern, label_norm) is not None


def _label_matches(label: str, include: list[str], exclude: list[str]) -> bool:
    label_norm = normalize_name(label)
    for t in include:
        if _has_digit(t) and not _token_in_label(t, label_norm):
            return False
    for t in exclude:
        if _token_in_label(t, label_norm):
            return False
    return True


def _to_grams(quantity: float | None, unit: str | None) -> float | None:
    if quantity is None or not unit:
        return None
    unit = unit.strip().lower()
    if unit == "kg":
        return quantity * 1000
    if unit == "g":
        return quantity
    return None


def _to_ml(quantity: float | None, unit: str | None) -> float | None:
    if quantity is None or not unit:
        return None
    unit = unit.strip().lower()
    if unit == "l":
        return quantity * 1000
    if unit == "ml":
        return quantity
    return None


def _in_range(value: float, lo: float | None, hi: float | None) -> bool:
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def _make_label(brand: str | None, name: str | None, unit: str | None, qty: float | None) -> str:
    qty_txt = f"{qty:g} {unit}" if qty else (unit or "")
    return " ".join(part for part in (brand, name, qty_txt) if part)


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
    min_ml: float | None = None,
    max_ml: float | None = None,
) -> tuple[list[BrowseResult], int]:
    """Vraca (stranica_rezultata, ukupan_broj_rezultata) - potonje omogucuje
    "ucitaj jos" u sucelju bez ponovnog pogadjanja koliko ih ukupno ima.
    Uvijek vraca tuple, i kad nema rezultata: ([], 0)."""
    empty: tuple[list[BrowseResult], int] = ([], 0)

    include, exclude = _split_tokens(query)
    match = _fts_expr(include, exclude)
    if not match:
        return empty

    rows = conn.execute(
        """
        SELECT cp.ean, cp.brand, cp.name, cp.unit, cp.quantity
        FROM fts_canonical f
        JOIN canonical_products cp ON cp.rowid = f.rowid
        WHERE fts_canonical MATCH ?
        LIMIT ?
        """,
        (match, CANDIDATE_POOL),
    ).fetchall()

    weight_active = min_grams is not None or max_grams is not None
    volume_active = min_ml is not None or max_ml is not None

    info: dict[str, str] = {}  # ean -> prikazani naziv
    for ean, brand, name, unit, qty in rows:
        label = _make_label(brand, name, unit, qty)
        if not _label_matches(label, include, exclude):
            continue
        if weight_active or volume_active:
            grams = _to_grams(qty, unit)
            ml = _to_ml(qty, unit)
            ok = (weight_active and grams is not None and _in_range(grams, min_grams, max_grams)) or \
                 (volume_active and ml is not None and _in_range(ml, min_ml, max_ml))
            if not ok:
                continue
        info[ean] = label
    if not info:
        return empty

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
        return empty

    # Privremena tablica poslovnica u radijusu, pa JOIN protiv nje - umjesto
    # dovlacenja cijena iz CIJELE drzave i odbacivanja u Pythonu (taj naivan
    # pristup izmjeren je na ~30 s; ovaj dotice samo retke obliznjih poslovnica).
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS radius_stores "
        "(chain TEXT, store_code TEXT, distance_km REAL, address TEXT, city TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS temp_idx_radius_stores ON radius_stores(chain, store_code)")
    conn.execute("DELETE FROM radius_stores")
    conn.executemany("INSERT INTO radius_stores VALUES (?,?,?,?,?)", nearby)

    placeholders = ",".join("?" for _ in ean_list)
    price_rows = conn.execute(
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
    for ean, chain, store_code, price, dist, address, city in price_rows:
        cur = best.get(ean)
        if cur is None or price < cur[0]:
            best[ean] = (price, chain, store_code, dist, address, city)

    results = [
        BrowseResult(ean=ean, label=info[ean], price=price, chain=chain,
                     address=address, city=city, distance_km=dist)
        for ean, (price, chain, store_code, dist, address, city) in best.items()
    ]
    results.sort(key=lambda r: r.price)
    return results[offset:offset + limit], len(results)
