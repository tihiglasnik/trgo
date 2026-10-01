"""
Preuzima enrichment/stores.csv i enrichment/products.csv izravno iz
senko/cijene-api GitHub repozitorija (raw.githubusercontent.com) - bez
potrebe za git-om ili kloniranjem cijelog repozitorija.

Testirano: oba fajla se uspjesno preuzimaju izravnim HTTP zahtjevom.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://raw.githubusercontent.com/senko/cijene-api/main/enrichment"
USER_AGENT = "grocery-finder-prototype/0.1 (osobni projekt)"
FILES = ["stores.csv", "products.csv"]


class EnrichmentFetchError(Exception):
    """Baca se kad preuzimanje enrichment podataka ne uspije."""


def fetch_enrichment(dest_dir: Path = Path("enrichment")) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for fname in FILES:
        url = f"{BASE_URL}/{fname}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            raise EnrichmentFetchError(f"Preuzimanje {fname} nije uspjelo (HTTP {exc.code})") from exc
        except urllib.error.URLError as exc:
            raise EnrichmentFetchError(f"Preuzimanje {fname} nije uspjelo: {exc.reason}") from exc
        if not data:
            raise EnrichmentFetchError(f"{fname} je preuzet prazan - nesto nije u redu s izvorom.")
        (dest_dir / fname).write_bytes(data)
        print(f"  preuzeto {fname} ({len(data):,} bajtova)")
    return dest_dir


if __name__ == "__main__":
    import sys

    try:
        path = fetch_enrichment()
    except EnrichmentFetchError as exc:
        print(f"Greska: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Enrichment podaci spremljeni u: {path}")
