"""
Za odabranu listu proizvoda (svaki ili s EAN-om ili sa "sirovim" nazivom
iz arhive - vidi product_matcher.py) i skup poslovnica, racuna ukupnu
cijenu kosarice po poslovnici te koji proizvodi nedostaju.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class BasketItem:
    query: str                # sto je korisnik trazio, npr. "mlijeko"
    ean: str | None = None    # cross-chain EAN, ako je poznat
    name: str | None = None   # fallback: tocan naziv iz arhive (ako nema EAN-a)
    qty: int = 1


@dataclass
class StoreResult:
    chain: str
    store_code: str
    address: str
    city: str
    distance_km: float | None
    total: float = 0.0
    available_items: list[str] = field(default_factory=list)
    missing_items: list[str] = field(default_factory=list)


def _price_for_item(conn: sqlite3.Connection, chain: str, store_code: str, item: BasketItem) -> float | None:
    if item.ean:
        row = conn.execute(
            """
            SELECT MIN(p.price)
            FROM prices p
            JOIN chain_products cp ON cp.chain = p.chain AND cp.product_code = p.product_code
            WHERE p.chain = ? AND p.store_code = ? AND cp.canonical_ean = ?
              AND p.price IS NOT NULL
            """,
            (chain, store_code, item.ean),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT MIN(p.price)
            FROM prices p
            JOIN chain_products cp ON cp.chain = p.chain AND cp.product_code = p.product_code
            WHERE p.chain = ? AND p.store_code = ? AND LOWER(cp.name) = LOWER(?)
              AND p.price IS NOT NULL
            """,
            (chain, store_code, item.name),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def calculate_store_options(
    conn: sqlite3.Connection,
    items: list[BasketItem],
    user_lat: float,
    user_lon: float,
    max_distance_km: float = 5.0,
) -> list[StoreResult]:
    from geo import clean_address, haversine_km

    stores = conn.execute(
        "SELECT chain, store_code, address, city, lat, lon FROM stores WHERE lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()

    results: list[StoreResult] = []
    for chain, store_code, address, city, lat, lon in stores:
        dist = haversine_km(user_lat, user_lon, lat, lon)
        if dist > max_distance_km:
            continue

        res = StoreResult(chain=chain, store_code=store_code, address=clean_address(address), city=city,
                           distance_km=round(dist, 2))
        for item in items:
            price = _price_for_item(conn, chain, store_code, item)
            if price is None:
                res.missing_items.append(item.query)
            else:
                res.total += price * item.qty
                res.available_items.append(item.query)
        res.total = round(res.total, 2)
        results.append(res)

    return results
