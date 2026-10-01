"""
Trazi proizvod po slobodnom tekstu ("mlijeko") i vraca kandidate koje
korisnik moze odabrati. Prioritet: pocisceni katalog (canonical_products,
iz enrichment/products.csv) jer ima uredne nazive i pouzdan EAN koji radi
kroz sve lance. Ako tamo nema pogodaka, pada na chain_products (izvorni
nazivi iz arhive), grupirano po normaliziranom nazivu.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass


def normalize_name(name: str) -> str:
    name = name.lower()
    name = name.replace(",", ".")
    name = re.sub(r"[^a-z0-9čćžšđ.%\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


@dataclass
class Candidate:
    ean: str | None          # None ako nemamo pouzdan cross-chain EAN
    label: str               # "Dukat trajno mlijeko 2.8% 1 L"
    chains_available: int    # u koliko lanaca postoji (informativno)


def _tokens(query: str) -> list[str]:
    return [t for t in normalize_name(query).split(" ") if t]


def _fts_match_expr(tokens: list[str]) -> str:
    """
    Gradi FTS5 MATCH izraz. Svaki token je citiran (izbjegava FTS5 posebne
    znakove poput navodnika/crtica u samom upitu - sigurno i za zlonamjeran
    unos, testirano). Brojevi trazimo TOCNO (bez *), rijeci kao prefiks (*)
    - "duka"* pogadja "dukat", ali brojevni "2" (bez *) ne pogadja "20".
    Razmak izmedju termina znaci logicki AND (svi tokeni moraju postojati).
    """
    parts = []
    for t in tokens:
        t = t.replace('"', '""')
        parts.append(f'"{t}"' if t.isdigit() else f'"{t}"*')
    return " ".join(parts)


def search(conn: sqlite3.Connection, query: str, limit: int = 8, fetch_pool: int = 300) -> list[Candidate]:
    tokens = _tokens(query)
    if not tokens:
        return []
    match = _fts_match_expr(tokens)

    results: list[Candidate] = []

    rows = conn.execute(
        """
        SELECT cp.ean, cp.brand, cp.name, cp.unit, cp.quantity,
               COUNT(DISTINCT chp.chain) AS n_chains
        FROM fts_canonical f
        JOIN canonical_products cp ON cp.rowid = f.rowid
        JOIN chain_products chp ON chp.canonical_ean = cp.ean
        WHERE fts_canonical MATCH ?
        GROUP BY cp.ean
        ORDER BY n_chains DESC
        LIMIT ?
        """,
        (match, fetch_pool),
    ).fetchall()
    for ean, brand, name, unit, qty, n_chains in rows:
        qty_txt = f"{qty:g} {unit}" if qty else (unit or "")
        label = " ".join(part for part in (brand, name, qty_txt) if part)
        results.append(Candidate(ean=ean, label=label, chains_available=n_chains))
        if len(results) >= limit:
            break

    if results:
        return results

    # fallback: nema u pociscenom katalogu, trazimo direktno po nazivima iz arhive
    rows = conn.execute(
        """
        SELECT cp2.name, COUNT(DISTINCT cp2.chain) AS n_chains
        FROM fts_chain_products f
        JOIN chain_products cp2 ON cp2.rowid = f.rowid
        WHERE fts_chain_products MATCH ?
        GROUP BY LOWER(cp2.name)
        ORDER BY n_chains DESC
        LIMIT ?
        """,
        (match, fetch_pool),
    ).fetchall()
    for name, n_chains in rows:
        results.append(Candidate(ean=None, label=name, chains_available=n_chains))
        # ean=None znaci: koristit cemo naziv izravno (bez cross-chain matchinga)
        if len(results) >= limit:
            break

    return results
