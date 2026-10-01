"""Haversine udaljenost i ciscenje adresa poslovnica."""

from __future__ import annotations

import re
from math import asin, cos, radians, sin, sqrt


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))


# Spar u arhivi na kraj adrese dopisuje interni broj poslovnice i naziv
# (pod)brenda, npr. "Vlaska 44 87006 Spar", "... 8701 Interspar" ili
# "... 87172 City" - stvarna adresa je npr. "Vlaska 44". Provjereno na
# svih 144 spar adresa da su to jedine tri varijante sufiksa, i da nijedna
# adresa izvan Spara ne prati taj obrazac.
_STORE_CODE_SUFFIX_RE = re.compile(r"\s+\d{3,6}\s+(Spar|Interspar|City)\s*$", re.IGNORECASE)


def clean_address(address: str | None) -> str:
    """Ukloni interni broj poslovnice/naziv lanca dopisan na kraj adrese, ako postoji."""
    if not address:
        return address or ""
    return _STORE_CODE_SUFFIX_RE.sub("", address).strip()
