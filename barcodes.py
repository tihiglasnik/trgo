"""
Normalizacija EAN/barkod vrijednosti.

Neki lanci spremaju barkod s vodećim nulama (0000054490970), a neki bez
(54490970). Isti GTIN je isti proizvod bez obzira na to, pa vodeće nule
skidamo kako bi se isti proizvod mogao prepoznati kroz različite lance.

Interni kodovi lanaca (npr. "konzum:01310113") i vrijednosti koje nisu
čisti brojevi ostavljamo netaknute - njih ne smijemo tretirati kao EAN.
"""

from __future__ import annotations


def normalize_barcode(barcode: str | None) -> str | None:
    if not barcode:
        return None
    barcode = barcode.strip()
    if not barcode:
        return None
    if ":" in barcode:
        # interni kod lanca, nije pravi EAN
        return None
    if not barcode.isdigit():
        return None
    if len(barcode) < 8:
        # prekratko da bude pravi EAN/GTIN
        return None
    stripped = barcode.lstrip("0")
    return stripped if stripped else "0"
