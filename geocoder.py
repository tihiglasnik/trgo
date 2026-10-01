"""
Geokodiranje adrese u (lat, lon) preko Nominatim (OpenStreetMap) javnog API-ja.

Ne treba API kljuc ni registracija. Nominatim trazi da se salje razuman
User-Agent i da se ne salje vise od ~1 zahtjeva u sekundi - za nasu potrebu
(par adresa po pokretanju) to nije problem.

Napomena: ovaj modul zove vanjski servis (nominatim.openstreetmap.org) pa
treba raditi na racunalu koje ima pristup internetu prema toj domeni.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "grocery-finder-prototype/0.1 (osobni projekt)"


class GeocodingError(Exception):
    """Baca se kad Nominatim nije dostupan ili je odgovor neocekivan."""


def geocode(address: str, country_codes: str = "hr") -> tuple[float, float] | None:
    """
    Vraca (lat, lon) za danu adresu, ili None ako adresa nije pronadjena.
    Baca GeocodingError ako je sam poziv servisu propao (mreza, timeout,
    HTTP greska) - to je namjerno drugaciji slucaj od "adresa ne postoji".
    """
    if not address or not address.strip():
        raise ValueError("Adresa ne smije biti prazna.")

    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "countrycodes": country_codes,
    }
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise GeocodingError(f"Nominatim je vratio HTTP gresku {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise GeocodingError(f"Nije moguce dosegnuti Nominatim (provjeri internet): {exc.reason}") from exc
    except TimeoutError as exc:
        raise GeocodingError("Nominatim nije odgovorio na vrijeme (timeout).") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeocodingError("Nominatim je vratio odgovor koji nije valjani JSON.") from exc

    if not data:
        return None
    try:
        return float(data[0]["lat"]), float(data[0]["lon"])
    except (KeyError, ValueError, TypeError) as exc:
        raise GeocodingError(f"Neocekivan format odgovora od Nominatima: {exc}") from exc


if __name__ == "__main__":
    import sys

    addr = " ".join(sys.argv[1:]) or "Ilica 1, Zagreb"
    try:
        result = geocode(addr)
    except GeocodingError as exc:
        print(f"Greska: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"{addr} -> {result}")
