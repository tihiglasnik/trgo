#!/usr/bin/env python3
"""
Jedna komanda koja sve radi automatski, BEZ rucnog preuzimanja/raspakiravanja
bilo cega:

    python update.py

Radi ovo:
    1. Preuzima enrichment podatke (geolokacije + pocisceni katalog) izravno
       s GitHuba (raw.githubusercontent.com) - bez git-a, bez prijave.
    2. Preuzima najnoviju dnevnu arhivu cijena s api.cijene.dev - bez
       registracije, bez API kljuca.
    3. Uvozi sve u lokalnu SQLite bazu (grocery.db).

Nakon ovoga samo pokreni cli.py kao inace:

    python cli.py --db grocery.db --address "Ilica 1, Zagreb" --product mlijeko
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fetch_archive import ArchiveFetchError, fetch_latest
from fetch_enrichment import EnrichmentFetchError, fetch_enrichment
from import_data import run_import


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=Path("grocery.db"), help="izlazna SQLite baza (default: grocery.db)")
    ap.add_argument("--work-dir", type=Path, default=Path("."),
                     help="gdje spremiti preuzete sirove podatke (default: trenutna mapa)")
    ap.add_argument("--chains", nargs="*", default=None,
                     help="uvezi samo odredjene lance, npr. --chains konzum lidl spar")
    args = ap.parse_args()

    print("[1/3] Preuzimam enrichment podatke (geolokacije + pocisceni katalog)...")
    try:
        enrichment_dir = fetch_enrichment(args.work_dir / "enrichment")
    except EnrichmentFetchError as exc:
        print(f"Greska: {exc}", file=sys.stderr)
        sys.exit(1)

    print("\n[2/3] Preuzimam najnoviju dnevnu arhivu cijena...")
    try:
        archive_dir = fetch_latest(args.work_dir / "data")
    except ArchiveFetchError as exc:
        print(f"Greska: {exc}", file=sys.stderr)
        sys.exit(1)

    print("\n[3/3] Uvozim u lokalnu bazu...")
    try:
        failed = run_import(archive_dir, enrichment_dir, args.db, args.chains)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)

    print(f"\nGotovo. Baza spremljena u: {args.db}")
    if failed:
        print(f"Lanci koji NISU uspjesno uvezeni: {', '.join(failed)}", file=sys.stderr)
    print("\nSad mozes pokrenuti npr.:")
    print(f'  python cli.py --db {args.db} --address "Ilica 1, Zagreb" --product mlijeko')


if __name__ == "__main__":
    main()
