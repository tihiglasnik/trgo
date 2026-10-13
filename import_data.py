#!/usr/bin/env python3
"""
Importira dnevnu cijene.dev arhivu (stores.csv / products.csv / prices.csv
po svakom lancu) i enrichment podatke iz cijene-api repozitorija
(geolokacije poslovnica + pročišćeni nazivi proizvoda) u jednu lokalnu
SQLite bazu.

Koristenje:
    python import_data.py <arhiva_dir> <enrichment_dir> [--db grocery.db]

<arhiva_dir>      - raspakirana dnevna arhiva s cijene.dev/archive
                    (mapa koja sadrzi podmape po lancu: konzum/, lidl/, ...)
<enrichment_dir>  - mapa "enrichment" iz senko/cijene-api repozitorija
                    (sadrzi stores.csv i products.csv s geolokacijom /
                    pociscenim nazivima)
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

from barcodes import normalize_barcode

SCHEMA_TABLES = """
-- Podaci o samom uvozu (datum arhive, vrijeme uvoza) - prikazuju se u sucelju.
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS stores (
    chain TEXT NOT NULL,
    store_code TEXT NOT NULL,
    type TEXT,
    address TEXT,
    city TEXT,
    zipcode TEXT,
    lat REAL,
    lon REAL,
    phone TEXT,
    PRIMARY KEY (chain, store_code)
);

CREATE TABLE IF NOT EXISTS chain_products (
    chain TEXT NOT NULL,
    product_code TEXT NOT NULL,
    barcode TEXT,
    canonical_ean TEXT,
    name TEXT,
    brand TEXT,
    category TEXT,
    unit TEXT,
    quantity TEXT,
    PRIMARY KEY (chain, product_code)
);

CREATE TABLE IF NOT EXISTS prices (
    chain TEXT NOT NULL,
    store_code TEXT NOT NULL,
    product_code TEXT NOT NULL,
    price REAL,
    unit_price REAL,
    best_price_30 REAL,
    anchor_price REAL,
    special_price REAL,
    PRIMARY KEY (chain, store_code, product_code)
);

CREATE TABLE IF NOT EXISTS canonical_products (
    ean TEXT PRIMARY KEY,
    brand TEXT,
    name TEXT,
    unit TEXT,
    quantity REAL
);
"""

# Sekundarni indeksi se namjerno NE grade odmah, nego tek nakon sto se svi
# podaci ucitaju (vidi run_import) - izgradnja indeksa na vec popunjenoj
# tablici je puno brza (jedan ucinkovit prolaz) nego odrzavanje indeksa
# uzivo tijekom 9 milijuna pojedinacnih INSERT-a.
# Sekundarni indeksi se namjerno NE grade odmah, nego tek nakon sto se svi
# podaci ucitaju (vidi run_import) - izgradnja indeksa na vec popunjenoj
# tablici je puno brza (jedan ucinkovit prolaz) nego odrzavanje indeksa
# uzivo tijekom 9 milijuna pojedinacnih INSERT-a.
SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_chain_products_ean ON chain_products (canonical_ean);
CREATE INDEX IF NOT EXISTS idx_chain_products_name ON chain_products (name);
CREATE INDEX IF NOT EXISTS idx_prices_chain_product ON prices (chain, product_code);
CREATE INDEX IF NOT EXISTS idx_stores_chain ON stores (chain);
"""

# Full-text pretraga (SQLite FTS5) za nazive/brandove proizvoda - LIKE '%rijec%'
# ne moze koristiti indeks (wildcard na pocetku), pa je na cesim rijecima
# (npr. "mlijeko") znao trajati i preko 4 sekunde na 9M+ redaka. FTS5 to
# svodi na jednoznamenkaste milisekunde (izmjereno: mlijeko 4944ms -> 10ms).
# Vanjski sadrzaj (content=) znaci da FTS ne duplicira tekst, samo indeks.
SCHEMA_FTS_CANONICAL = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_canonical USING fts5(
    brand, name, content='canonical_products', content_rowid='rowid'
);
"""

# fts_chain_products indeksira SVE proizvode iz arhive (366k+ redaka) - koristi
# se samo kao fallback kad upit ne nadje nista u pociscenom katalogu
# (otprilike 47% proizvoda nema EAN u katalogu, pa ovaj fallback nije rijedak).
# Izgradnja ove tablice je izmjereno najskuplji dio FTS koraka (~4.5s na brzom
# disku u razvojnom okruzenju) - na sporijem/mehanickom disku moze biti
# neproporcionalno sporije i glasnije, jer FTS5 radi puno sitnih zapisa pri
# izgradnji indeksa. Zato je ovo opcionalno (build_chain_fallback_search).
SCHEMA_FTS_CHAIN = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_chain_products USING fts5(
    name, content='chain_products', content_rowid='rowid'
);
"""


def to_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def import_enrichment_stores(conn: sqlite3.Connection, path: Path) -> dict[tuple[str, str], tuple[float, float, str]]:
    """Ucitava geolokacije poslovnica; vraca mapu (chain, code) -> (lat, lon, phone)."""
    geo: dict[tuple[str, str], tuple[float, float, str]] = {}
    if not path.exists():
        print(f"  ! nije nadjen {path}, preskacem geolokacije poslovnica", file=sys.stderr)
        return geo
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        if not {"chain_code", "code", "lat", "lon"} <= set(headers):
            print(f"  ! {path} nema ocekivane stupce (ima: {headers}), preskacem geolokacije", file=sys.stderr)
            return geo
        for row in reader:
            lat, lon = to_float(row.get("lat")), to_float(row.get("lon"))
            if lat is None or lon is None:
                continue
            chain_code = (row.get("chain_code") or "").strip().lower()
            code = (row.get("code") or "").strip()
            if not chain_code or not code:
                continue
            key = (chain_code, code)
            geo[key] = (lat, lon, row.get("phone", ""))
    print(f"  ucitano {len(geo)} geolokacija poslovnica iz enrichmenta")
    return geo


def import_enrichment_products(conn: sqlite3.Connection, path: Path) -> None:
    """Ucitava pocisceni katalog proizvoda (barcode -> brand/name/unit/quantity)."""
    if not path.exists():
        print(f"  ! nije nadjen {path}, preskacem pocisceni katalog proizvoda", file=sys.stderr)
        return
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ean = normalize_barcode(row.get("barcode"))
            if not ean:
                continue
            qty = to_float(row.get("quantity"))
            rows.append((ean, row.get("brand", ""), row.get("name", ""), row.get("unit", ""), qty))
    conn.executemany(
        "INSERT OR REPLACE INTO canonical_products (ean, brand, name, unit, quantity) VALUES (?,?,?,?,?)",
        rows,
    )
    print(f"  ucitano {len(rows)} pociscenih proizvoda (enrichment/products.csv)")


def import_chain(
    conn: sqlite3.Connection,
    chain_dir: Path,
    geo: dict[tuple[str, str], tuple[float, float, str]],
) -> bool:
    """True ako je lanac uvezen, False ako je preskocen (nedostaje CSV ili stupac)."""
    chain = chain_dir.name.strip().lower()

    stores_path = chain_dir / "stores.csv"
    products_path = chain_dir / "products.csv"
    prices_path = chain_dir / "prices.csv"
    if not (stores_path.exists() and products_path.exists() and prices_path.exists()):
        print(f"  ! {chain}: nedostaje jedan od CSV fajlova, preskacem", file=sys.stderr)
        return False

    # --- provjera da svi fajlovi imaju ocekivane stupce PRIJE nego bilo sto upisemo ---
    # (izbjegava da lanac ostane napola uvezen ako mu format odstupa)
    required = {
        stores_path: ["store_id"],
        products_path: ["product_id", "barcode"],
        prices_path: ["store_id", "product_id", "price"],
    }
    for path, cols in required.items():
        with open(path, encoding="utf-8") as f:
            headers = csv.DictReader(f).fieldnames or []
        missing = [c for c in cols if c not in headers]
        if missing:
            print(f"  ! {chain}: {path.name} nema ocekivane stupce {missing} "
                  f"(ima: {headers}) - preskacem cijeli lanac", file=sys.stderr)
            return False

    # --- stores ---
    store_rows = []
    with open(stores_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = (row.get("store_id") or "").strip()
            if not code:
                continue
            lat = lon = None
            phone = ""
            hit = geo.get((chain, code))
            if hit:
                lat, lon, phone = hit
            store_rows.append(
                (chain, code, row.get("type", ""), row.get("address", ""),
                 row.get("city", ""), row.get("zipcode", ""), lat, lon, phone)
            )
    conn.executemany(
        "INSERT OR REPLACE INTO stores (chain, store_code, type, address, city, zipcode, lat, lon, phone) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        store_rows,
    )
    geocoded = sum(1 for r in store_rows if r[6] is not None)

    # --- products ---
    product_rows = []
    with open(products_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            product_id = (row.get("product_id") or "").strip()
            if not product_id:
                continue
            barcode = row.get("barcode", "")
            ean = normalize_barcode(barcode)
            product_rows.append(
                (chain, product_id, barcode, ean,
                 row.get("name", ""), row.get("brand", ""), row.get("category", ""),
                 row.get("unit", ""), row.get("quantity", ""))
            )
    conn.executemany(
        "INSERT OR REPLACE INTO chain_products "
        "(chain, product_code, barcode, canonical_ean, name, brand, category, unit, quantity) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        product_rows,
    )

    # --- prices (najveci fajl - ovdje odlazi vecina vremena importa, pa
    # koristimo csv.reader + indekse stupaca umjesto DictReadera: DictReader
    # pravi po jedan rjecnik za SVAKI redak, sto je na 9 milijuna redaka
    # mjerljivo sporije. Izmjereno ubrzanje: ~66s -> ~24s na cijeloj arhivi) ---
    price_count = 0
    skipped = 0
    batch = []
    with open(prices_path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        col = {name: i for i, name in enumerate(header)}
        i_store = col["store_id"]      # provjereno da postoji ranije (required check)
        i_product = col["product_id"]
        i_price = col.get("price")
        i_unit_price = col.get("unit_price")
        i_best30 = col.get("best_price_30")
        i_anchor = col.get("anchor_price")
        i_special = col.get("special_price")

        def field(row: list, i: int | None) -> str | None:
            return row[i] if (i is not None and i < len(row)) else None

        for row in reader:
            if len(row) <= max(i_store, i_product):
                skipped += 1
                continue
            store_code = row[i_store].strip()
            product_code = row[i_product].strip()
            if not store_code or not product_code:
                skipped += 1
                continue
            batch.append((
                chain, store_code, product_code,
                to_float(field(row, i_price)), to_float(field(row, i_unit_price)),
                to_float(field(row, i_best30)), to_float(field(row, i_anchor)),
                to_float(field(row, i_special)),
            ))
            if len(batch) >= 50_000:
                conn.executemany(
                    "INSERT OR REPLACE INTO prices "
                    "(chain, store_code, product_code, price, unit_price, best_price_30, anchor_price, special_price) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    batch,
                )
                price_count += len(batch)
                batch.clear()
    if batch:
        conn.executemany(
            "INSERT OR REPLACE INTO prices "
            "(chain, store_code, product_code, price, unit_price, best_price_30, anchor_price, special_price) "
            "VALUES (?,?,?,?,?,?,?,?)",
            batch,
        )
        price_count += len(batch)

    extra = f", preskoceno {skipped} redaka bez store_id/product_id" if skipped else ""
    print(f"  {chain}: {len(store_rows)} poslovnica ({geocoded} s koordinatama), "
          f"{len(product_rows)} proizvoda, {price_count} cijena{extra}")
    return True


def run_import(
    archive_dir: Path,
    enrichment_dir: Path,
    db_path: Path,
    chains: list[str] | None = None,
    build_chain_fallback_search: bool = True,
) -> list[str]:
    """
    Puni import: enrichment + arhiva -> SQLite baza.
    Vraca listu naziva lanaca koji NISU uspjesno uvezeni (prazna lista = sve OK).
    Koristi ju i CLI (main() ispod) i update.py (automatski download+import).
    """
    if not archive_dir.is_dir():
        raise FileNotFoundError(f"Mapa s arhivom ne postoji: {archive_dir}")
    if not enrichment_dir.is_dir():
        raise FileNotFoundError(f"Enrichment mapa ne postoji: {enrichment_dir}")

    conn = sqlite3.connect(db_path)
    # ovo je lokalna baza koja se u potpunosti generira iz CSV arhive - ako se
    # proces prekine usred importa, jednostavno se pokrene ponovno, pa nema
    # potrebe za punom trajnosti (fsync) na svaki commit.
    conn.execute("PRAGMA journal_mode = MEMORY")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    try:
        conn.executescript(SCHEMA_TABLES)

        print("Ucitavam enrichment podatke...")
        geo = import_enrichment_stores(conn, enrichment_dir / "stores.csv")
        import_enrichment_products(conn, enrichment_dir / "products.csv")
        conn.commit()

        print("Ucitavam arhivu po lancima...")
        chain_dirs = sorted(p for p in archive_dir.iterdir() if p.is_dir())
        if chains:
            wanted = {c.lower() for c in chains}
            chain_dirs = [p for p in chain_dirs if p.name.lower() in wanted]

        failed = []
        for chain_dir in chain_dirs:
            try:
                if import_chain(conn, chain_dir, geo):
                    conn.commit()
                else:
                    conn.rollback()
                    failed.append(chain_dir.name)
            except Exception as exc:  # jedan los lanac ne smije srusiti ostatak importa
                conn.rollback()
                failed.append(chain_dir.name)
                print(f"  ! {chain_dir.name}: greska pri importu ({exc}), preskacem i nastavljam dalje",
                      file=sys.stderr)

        print("Gradim indekse...")
        conn.executescript(SCHEMA_INDEXES)
        conn.commit()

        print("Gradim full-text pretragu (glavni katalog)...")
        conn.executescript(SCHEMA_FTS_CANONICAL)
        conn.execute("INSERT INTO fts_canonical(rowid, brand, name) SELECT rowid, brand, name FROM canonical_products")
        conn.commit()

        if build_chain_fallback_search:
            print("Gradim full-text pretragu (fallback za proizvode izvan kataloga)...")
            conn.executescript(SCHEMA_FTS_CHAIN)
            conn.execute("INSERT INTO fts_chain_products(rowid, name) SELECT rowid, name FROM chain_products")
            conn.commit()
        else:
            print("  preskoceno (--skip-fallback-search) - pretraga za proizvode izvan"
                  " glavnog kataloga bit ce sporija (LIKE umjesto FTS5), ali osvjezavanje je brze.")

        # zapisi kad su podaci uvezeni i na koji se dan odnose. Datum arhive se
        # izvodi iz naziva mape (update.py/osvjezavanje je nazivaju YYYY-MM-DD);
        # za rucni uvoz iz mape drukcijeg naziva datum ostaje nepoznat.
        m = re.fullmatch(r"\d{4}-\d{2}-\d{2}", archive_dir.name)
        meta = {
            "archive_date": m.group(0) if m else "",
            "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "failed_chains": ",".join(failed),
        }
        conn.executemany("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", list(meta.items()))
        conn.commit()

        return failed
    finally:
        conn.close()


def get_data_info(conn: sqlite3.Connection, db_path: str | Path) -> dict:
    """
    Kada su podaci azurirani. Cita tablicu `meta` (ako je baza napravljena
    novijom verzijom). Za STARE baze bez nje vraca samo vrijeme zadnje izmjene
    datoteke baze (source="file") - datum cijena tada nije poznat.
    Vraca: archive_date (YYYY-MM-DD ili None), days_old (int ili None),
    imported_at (ISO), failed_chains (lista), source ("meta"|"file"|"unknown").
    """
    info = {"archive_date": None, "days_old": None, "imported_at": None,
            "failed_chains": [], "source": "unknown"}
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
    if has_meta:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        info["archive_date"] = meta.get("archive_date") or None
        info["imported_at"] = meta.get("imported_at") or None
        info["failed_chains"] = [c for c in (meta.get("failed_chains") or "").split(",") if c]
        if info["imported_at"]:
            info["source"] = "meta"
    if not info["imported_at"]:
        try:
            info["imported_at"] = datetime.fromtimestamp(os.path.getmtime(db_path)).astimezone() \
                .isoformat(timespec="seconds")
            info["source"] = "file"
        except OSError:
            pass
    if info["archive_date"]:
        try:
            info["days_old"] = (date.today() - date.fromisoformat(info["archive_date"])).days
        except ValueError:
            info["archive_date"] = None
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive_dir", type=Path, help="raspakirana cijene.dev/archive mapa (podmape po lancu)")
    ap.add_argument("enrichment_dir", type=Path, help="enrichment mapa iz senko/cijene-api repozitorija")
    ap.add_argument("--db", type=Path, default=Path("grocery.db"), help="izlazna SQLite baza (default: grocery.db)")
    ap.add_argument("--chains", nargs="*", default=None,
                     help="ako zelis importirati samo odredjene lance (npr. --chains konzum lidl spar)")
    ap.add_argument("--skip-fallback-search", action="store_true",
                     help="preskoci izgradnju FTS pretrage za proizvode izvan glavnog kataloga "
                          "(~366k redaka, izmjereno najskuplji dio FTS koraka). Osvjezavanje je brze i "
                          "tise, ali pretraga za ~47%% proizvoda bez EAN-a u katalogu ostaje sporija "
                          "(LIKE upit umjesto FTS5) - dobro za spore/mehanicke diskove.")
    args = ap.parse_args()

    try:
        failed = run_import(args.archive_dir, args.enrichment_dir, args.db, args.chains,
                             build_chain_fallback_search=not args.skip_fallback_search)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    print(f"\nGotovo. Baza spremljena u: {args.db}")
    if failed:
        print(f"Lanci koji NISU uspjesno uvezeni: {', '.join(failed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
