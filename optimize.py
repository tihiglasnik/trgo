"""
Optimizacija kroz VISE trgovina (do 2) - nasuprot "sve u jednoj trgovini"
iz basket.py/ranking.py.

Pojednostavljenje (namjerno, isto kao ranking.best_compromise): trosak puta
racuna se kao da je svaka poslovnica zaseban put OD KUCE (eur_per_km *
udaljenost), ne stvarna ruta kroz obje poslovnice i natrag. Za vecinu
svakodnevnih slucajeva (dvije poslovnice u istom kvartu) ovo je razumna
aproksimacija, ali za poslovnice u suprotnim smjerovima precjenjuje trosak.

Preporuka za 2 poslovnice se vraca SAMO ako usteda (u odnosu na najbolju
JEDNU poslovnicu, uracunat njen trosak puta) prelazi "min_savings" prag -
inace se jednostavno vrati najbolja jedna poslovnica, da se korisniku ne
predlaze komplicirano cjepkanje kupnje radi par centi.
"""

from __future__ import annotations

from dataclasses import dataclass

from basket import BasketItem, StoreResult, _price_for_item, calculate_store_options
from ranking import complete_only

CANDIDATE_LIMIT = 25  # koliko najblizih poslovnica (koje imaju bar nesto trazeno) razmatramo za parove


@dataclass
class SplitPlan:
    stores: tuple[StoreResult, StoreResult]
    assignment: dict[str, int]      # item.query -> 0 ili 1 (koja poslovnica u 'stores')
    items_cost: float               # zbroj cijena, BEZ troska puta
    travel_cost: float

    @property
    def total_cost(self) -> float:
        return self.items_cost + self.travel_cost


def _price_matrix(conn, items: list[BasketItem], stores: list[StoreResult]) -> dict:
    """
    {(chain, store_code): {query: cijena_ili_None}}.

    Namjerno JEDAN SQL upit za sve (poslovnica x stavka) parove umjesto
    _price_for_item po paru - izmjereno: 232 pojedinacna upita ~1,2s,
    jedan grupni upit ~0,03s (40x brze) na istom stvarnom skupu podataka.
    """
    store_keys = [(s.chain, s.store_code) for s in stores]
    store_values = ",".join("(?,?)" for _ in store_keys)
    store_params = [p for pair in store_keys for p in pair]

    matrix = {key: {item.query: None for item in items} for key in store_keys}

    ean_items = [it for it in items if it.ean]
    if ean_items:
        ean_values = ",".join("?" for _ in ean_items)
        rows = conn.execute(
            f"""
            SELECT p.chain, p.store_code, cp.canonical_ean, MIN(p.price)
            FROM prices p
            JOIN chain_products cp ON cp.chain = p.chain AND cp.product_code = p.product_code
            WHERE (p.chain, p.store_code) IN (VALUES {store_values})
              AND cp.canonical_ean IN ({ean_values})
              AND p.price IS NOT NULL
            GROUP BY p.chain, p.store_code, cp.canonical_ean
            """,
            (*store_params, *[it.ean for it in ean_items]),
        ).fetchall()
        ean_to_query = {it.ean: it.query for it in ean_items}
        for chain, store_code, ean, price in rows:
            matrix[(chain, store_code)][ean_to_query[ean]] = price

    # fallback bez EAN-a (tocan naziv) - rjedak slucaj, zadrzan po-stavci radi jednostavnosti
    for item in items:
        if item.ean:
            continue
        for key in store_keys:
            matrix[key][item.query] = _price_for_item(conn, key[0], key[1], item)

    return matrix


def best_single_store(results: list[StoreResult], eur_per_km: float = 0.30) -> StoreResult | None:
    complete = complete_only(results)
    if not complete:
        return None
    return min(complete, key=lambda r: r.total + eur_per_km * (r.distance_km or 0.0))


def optimize_two_stores(
    conn,
    items: list[BasketItem],
    lat: float,
    lon: float,
    radius_km: float,
    min_savings: float = 5.0,
    eur_per_km: float = 0.30,
) -> tuple[SplitPlan | None, StoreResult | None, list[StoreResult]]:
    """
    Vraca (plan_za_dvije_poslovnice_ili_None, najbolja_jedna_poslovnica_ili_None, svi_rezultati).
    Plan za dvije poslovnice je None ako ne postoji par koji pokriva SVE
    stavke, ili ako usteda ne prelazi min_savings.
    """
    results = calculate_store_options(conn, items, lat, lon, max_distance_km=radius_km)
    baseline = best_single_store(results, eur_per_km)
    baseline_cost = (baseline.total + eur_per_km * (baseline.distance_km or 0.0)) if baseline else None

    # kandidati za parove: poslovnice koje imaju BAR NESTO od trazenog, najblize prve
    candidates = [r for r in results if len(r.missing_items) < len(items)]
    candidates.sort(key=lambda r: r.distance_km if r.distance_km is not None else 1e9)
    candidates = candidates[:CANDIDATE_LIMIT]

    if len(candidates) < 2:
        return None, baseline, results

    matrix = _price_matrix(conn, items, candidates)

    best_plan: SplitPlan | None = None
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            s1, s2 = candidates[i], candidates[j]
            p1 = matrix[(s1.chain, s1.store_code)]
            p2 = matrix[(s2.chain, s2.store_code)]

            items_cost = 0.0
            assignment: dict[str, int] = {}
            covers_all = True
            for item in items:
                c1, c2 = p1[item.query], p2[item.query]
                if c1 is None and c2 is None:
                    covers_all = False
                    break
                if c2 is None or (c1 is not None and c1 <= c2):
                    items_cost += c1 * item.qty
                    assignment[item.query] = 0
                else:
                    items_cost += c2 * item.qty
                    assignment[item.query] = 1
            if not covers_all:
                continue

            travel_cost = eur_per_km * ((s1.distance_km or 0.0) + (s2.distance_km or 0.0))
            plan = SplitPlan(stores=(s1, s2), assignment=assignment, items_cost=items_cost, travel_cost=travel_cost)
            if best_plan is None or plan.total_cost < best_plan.total_cost:
                best_plan = plan

    if best_plan is None:
        return None, baseline, results

    if baseline_cost is not None and (baseline_cost - best_plan.total_cost) < min_savings:
        return None, baseline, results

    return best_plan, baseline, results
