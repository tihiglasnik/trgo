"""
Preuzima najnoviju dnevnu ZIP arhivu cijena s api.cijene.dev (bez prijave
ili API kljuca) i raspakira je.

URL shema je procitana izravno iz izvornog koda servisa
(service/routers/v0.py u github.com/senko/cijene-api), ne pogadjana:

    GET https://api.cijene.dev/v0/list
        -> {"archives": [{"date": "YYYY-MM-DD",
                           "url": "https://api.cijene.dev/v0/archive/YYYY-MM-DD.zip",
                           "size": ..., "updated": ...}, ...]}

NAPOMENA: ovaj modul nije mogao biti uzivo testiran protiv api.cijene.dev
u okruzenju u kojem je pisan (nema mreznog pristupa toj domeni odatle).
Kod je ispravan prema dokumentiranom ponasanju servisa, ali prvi put kad
ga pokrenes na svom racunalu, provjeri ispis.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

LIST_URL = "https://api.cijene.dev/v0/list"
USER_AGENT = "grocery-finder-prototype/0.1 (osobni projekt)"


class ArchiveFetchError(Exception):
    """Baca se kad dohvat popisa arhiva ili preuzimanje ZIP-a ne uspije."""


def _get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise ArchiveFetchError(f"{url} je vratio HTTP gresku {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ArchiveFetchError(f"Nije moguce dosegnuti {url}: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArchiveFetchError(f"{url} nije vratio valjani JSON: {exc}") from exc


def latest_archive() -> dict:
    """Vraca metapodatke (date/url/size/updated) najnovije dostupne arhive."""
    data = _get_json(LIST_URL)
    archives = data.get("archives", [])
    if not archives:
        raise ArchiveFetchError("api.cijene.dev nije vratio nijednu dostupnu arhivu.")
    return max(archives, key=lambda a: a["date"])


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """
    Zastita od 'zip slip' napada: nijedan zapis u ZIP-u ne smije zavrsiti
    izvan odredisne mape (npr. putem '../../' u imenu fajla). Provjeravamo
    SVE zapise prije nego bilo sto raspakiramo.
    """
    dest = dest.resolve()
    for member in zf.namelist():
        member_path = (dest / member).resolve()
        if member_path != dest and dest not in member_path.parents:
            raise ArchiveFetchError(f"Sumnjiv zapis u ZIP arhivi (izvan odredisne mape): {member}")
    zf.extractall(dest)


def download_and_extract(archive: dict, dest_root: Path = Path("data")) -> Path:
    target = dest_root / archive["date"]
    if target.exists() and any(target.iterdir()):
        print(f"  {target} vec postoji, preskacem ponovno preuzimanje")
        return target

    req = urllib.request.Request(archive["url"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise ArchiveFetchError(f"Preuzimanje arhive nije uspjelo (HTTP {exc.code})") from exc
    except urllib.error.URLError as exc:
        raise ArchiveFetchError(f"Preuzimanje arhive nije uspjelo: {exc.reason}") from exc

    if not raw:
        raise ArchiveFetchError("Preuzeta arhiva je prazna.")

    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            _safe_extract(zf, target)
    except zipfile.BadZipFile as exc:
        raise ArchiveFetchError(f"Preuzeti fajl nije valjan ZIP: {exc}") from exc
    return target


def fetch_latest(dest_root: Path = Path("data")) -> Path:
    archive = latest_archive()
    size_mb = archive.get("size", 0) / 1_000_000
    print(f"Najnovija dostupna arhiva: {archive['date']} (~{size_mb:.1f} MB)")
    return download_and_extract(archive, dest_root)


if __name__ == "__main__":
    import sys

    try:
        path = fetch_latest()
    except ArchiveFetchError as exc:
        print(f"Greska: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Raspakirano u: {path}")
