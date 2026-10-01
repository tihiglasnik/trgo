"""
Rangira StoreResult liste po tri transparentna kriterija - ne izmislja
jedan "najbolji" rezultat, nego pokazuje sve tri opcije s brojkama da
korisnik sam odluci (cijena, udaljenost, dostupnost).
"""

from __future__ import annotations

from basket import StoreResult


def complete_only(results: list[StoreResult]) -> list[StoreResult]:
    """Samo poslovnice gdje su svi trazeni proizvodi dostupni."""
    return [r for r in results if not r.missing_items]


def all_complete_by_price(results: list[StoreResult]) -> list[StoreResult]:
    """
    Sve poslovnice gdje su SVE trazene stavke dostupne, sortirano od
    najjeftinije. Kod izjednacene cijene, sortira se dalje po udaljenosti
    (najbliza prva) - inace bi poredak kod istih cijena bio nasumican.
    """
    return sorted(complete_only(results), key=lambda r: (r.total, r.distance_km if r.distance_km is not None else float("inf")))


def cheapest(results: list[StoreResult], require_complete: bool = True) -> StoreResult | None:
    pool = complete_only(results) if require_complete else results
    if not pool:
        return None
    return min(pool, key=lambda r: r.total)


def nearest(results: list[StoreResult]) -> StoreResult | None:
    if not results:
        return None
    return min(results, key=lambda r: (r.distance_km if r.distance_km is not None else float("inf")))


def best_compromise(results: list[StoreResult], require_complete: bool = True,
                     eur_per_km: float = 0.30) -> StoreResult | None:
    """
    Bira poslovnicu koja minimizira UKUPNI trosak = cijena kosarice +
    procijenjeni trosak puta (eur_per_km * udaljenost).

    Ovo je namjerno apsolutna formula, ne normalizacija u odnosu na
    min/max cijelog skupa rezultata. Normalizacija je znala dati
    neintuitivne rezultate: ako je jedna poslovnica u skupu bila jako
    udaljena, ona bi "razvukla" skalu udaljenosti tako da razlika od par
    stotina metara izmedju dvije bliske poslovnice postane tezinski
    dominantna nad razlikom od pola eura u cijeni - pa bi algoritam znao
    preporuciti dalju i skuplju opciju umjesto blize i jeftinije.

    eur_per_km je namjerno eksplicitan, mijenjiv parametar (gruba procjena
    troska goriva/vremena po km jednosmjerne udaljenosti), ne skrivena
    "AI" konstanta.
    """
    pool = complete_only(results) if require_complete else results
    if not pool:
        return None
    return min(pool, key=lambda r: r.total + eur_per_km * (r.distance_km or 0.0))
