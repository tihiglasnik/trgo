#!/usr/bin/env python3
"""
Glavni CLI prototip.

Primjer:
    python cli.py --db grocery.db --address "Ilica 1, Zagreb" \\
        --product "mlijeko" --product "jaja" --product "kruh" --product "maslac"

Za testiranje bez geokodiranja (npr. offline ili u sandboxu bez pristupa
internetu), zaobidji geocoder direktno koordinatama:

    python cli.py --db grocery.db --lat 45.815 --lon 15.982 \\
        --product "mlijeko" --product "jaja"

Ako za neki proizvod postoji vise kandidata, u ne-interaktivnom nacinu
(--auto) automatski se uzima kandidat dostupan u najvise lanaca (najsigurniji
izbor za usporedbu cijena kroz vise trgovina). U interaktivnom nacinu (default)
program pita korisnika da odabere.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from basket import BasketItem, calculate_store_options
import ranking
from product_matcher import search as search_products


def resolve_item(conn: sqlite3.Connection, query: str, auto: bool, labels: dict[str, str]) -> BasketItem | None:
    candidates = search_products(conn, query)
    if not candidates:
        print(f"  ✗ Nije pronadjen nijedan proizvod za '{query}'")
        return None

    if auto or len(candidates) == 1:
        chosen = candidates[0]
    else:
        print(f"\n  Vise rezultata za '{query}':")
        for i, c in enumerate(candidates, 1):
            tag = f"[{c.chains_available} lanaca]"
            print(f"    {i}. {c.label} {tag}")
        while True:
            raw = input(f"  Odaberi broj (1-{len(candidates)}, Enter za 1): ").strip()
            if raw == "":
                chosen = candidates[0]
                break
            if raw.isdigit() and 1 <= int(raw) <= len(candidates):
                chosen = candidates[int(raw) - 1]
                break
            print("  Nevazeci unos, pokusaj ponovno.")

    print(f"  ✓ {query} -> {chosen.label}")
    labels[query] = chosen.label
    return BasketItem(query=query, ean=chosen.ean, name=None if chosen.ean else chosen.label)


def print_per_item_report(conn, items, lat: float, lon: float, radius: float, labels: dict[str, str]) -> None:
    """Za svaki proizvod POSEBNO - cijena po lancu (spar, konzum, lidl...), bez adresa/poslovnica."""
    print(f"\nPo pojedinom proizvodu (radijus {radius} km):")
    for item in items:
        single_results = calculate_store_options(conn, [item], lat, lon, max_distance_km=radius)
        available = ranking.complete_only(single_results)
        print(f"  📦 {labels.get(item.query, item.query)}:")
        if not available:
            print(f"     nije pronadjen ni u jednoj poslovnici unutar {radius} km")
            continue

        best_per_chain: dict[str, float] = {}
        for r in available:
            if r.chain not in best_per_chain or r.total < best_per_chain[r.chain]:
                best_per_chain[r.chain] = r.total
        for chain, price in sorted(best_per_chain.items(), key=lambda x: x[1]):
            print(f"     {chain:<12} {price:.2f} EUR")


def print_results(results, radius: float, labels: dict[str, str], top_n: int = 15) -> None:
    if not results:
        print(f"\nNema poslovnica u radijusu od {radius} km s poznatom lokacijom.")
        return

    # "Sve poslovnice u radijusu" - sortirano iskljucivo po udaljenosti od
    # adrese pretrage, bez obzira na cijenu/lanac/nedostajuce stavke
    results_sorted = sorted(results, key=lambda r: r.distance_km if r.distance_km is not None else float("inf"))

    print(f"\nSve poslovnice u radijusu od {radius} km:")
    print(f"{'Lanac':<10}{'Adresa':<32}{'Udaljenost':<12}{'Ukupno':<10}{'Nedostaje'}")
    print("-" * 90)
    for r in results_sorted[:top_n]:
        missing = ", ".join(labels.get(q, q) for q in r.missing_items) if r.missing_items else "-"
        dist = f"{r.distance_km} km" if r.distance_km is not None else "?"
        addr = f"{r.address}, {r.city}"[:30]
        print(f"{r.chain:<10}{addr:<32}{dist:<12}{r.total:<10.2f}{missing}")
    if len(results_sorted) > top_n:
        print(f"... i jos {len(results_sorted) - top_n} poslovnica (povecaj --radius ili smanji da vidis manje)")

    print("\nCijela kosarica zajedno (svi trazeni proizvodi u ISTOJ poslovnici), po lancu od najjeftinijeg:")

    complete = ranking.complete_only(results)
    if complete:
        by_chain: dict[str, list] = {}
        for r in complete:
            by_chain.setdefault(r.chain, []).append(r)

        chain_summaries = []
        for chain, group in by_chain.items():
            min_total = min(r.total for r in group)
            nearest_in_chain = min(group, key=lambda r: r.distance_km if r.distance_km is not None else float("inf"))
            chain_summaries.append((chain, min_total, len(group), nearest_in_chain))
        chain_summaries.sort(key=lambda x: x[1])

        for i, (chain, min_total, n_stores, nearest_in_chain) in enumerate(chain_summaries):
            suffix = f" ({n_stores} poslovnica)" if n_stores > 1 else ""
            print(f"  {chain:<10} {min_total:>6.2f} EUR{suffix}")
            if i == 0:
                # samo za najjeftiniji lanac odmah pokazujemo i najblizu konkretnu poslovnicu
                print(f"             \u2192 najbliza: {nearest_in_chain.address}, {nearest_in_chain.city} "
                      f"({nearest_in_chain.distance_km} km, {nearest_in_chain.total:.2f} EUR)")
    else:
        print("  Nijedna poslovnica u radijusu nema baš sve trazene proizvode zajedno.")

    near = ranking.nearest(results)
    comp = ranking.best_compromise(results)
    if near:
        print(f"\n  🏠 Najbliža poslovnica ukupno: {near.chain} - {near.address}, {near.city} "
              f"({near.distance_km} km, {near.total:.2f} EUR, nedostaje: "
              f"{', '.join(labels.get(q, q) for q in near.missing_items) or '-'})")
    if comp:
        print(f"  ⚖️  Kompromis cijena/udaljenost (sve dostupno): {comp.chain} - {comp.address}, {comp.city} "
              f"({comp.total:.2f} EUR, {comp.distance_km} km)")


def positive_float(value: str) -> float:
    f = float(value)
    if f <= 0:
        raise argparse.ArgumentTypeError("radijus mora biti pozitivan broj (npr. 2 ili 3.5)")
    return f


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="grocery.db")
    ap.add_argument("--address", help="adresa za geokodiranje (treba pristup internetu)")
    ap.add_argument("--lat", type=float, help="alternativa --address: geografska sirina")
    ap.add_argument("--lon", type=float, help="alternativa --address: geografska duzina")
    ap.add_argument("--product", action="append", dest="products", default=[],
                     help="proizvod za trazenje, moze se ponoviti vise puta")
    ap.add_argument("--radius", type=positive_float, default=5.0,
                     help="radijus pretrage u kilometrima (default 5, npr. --radius 2 ili --radius 1.5)")
    ap.add_argument("--auto", action="store_true", help="ne pitaj korisnika, uzmi najbolji kandidat automatski")
    ap.add_argument("--browse", metavar="UPIT",
                     help="umjesto kosarice, ispisi SVE proizvode za dani upit u obliznjim poslovnicama, "
                          "sortirano po cijeni (npr. --browse \"kava\" ili --browse \"kava -instant\")")
    ap.add_argument("--min-g", type=float, help="samo uz --browse: minimalna tezina u gramima")
    ap.add_argument("--max-g", type=float, help="samo uz --browse: maksimalna tezina u gramima")
    ap.add_argument("--min-ml", type=float, help="samo uz --browse: minimalni volumen u mililitrima")
    ap.add_argument("--max-ml", type=float, help="samo uz --browse: maksimalni volumen u mililitrima")
    ap.add_argument("--limit", type=int, default=15, help="samo uz --browse: broj rezultata (default 15)")
    ap.add_argument("--optimize", action="store_true",
                     help="uz --product: provjeri isplati li se razdijeliti kupnju na DVIJE poslovnice")
    ap.add_argument("--min-savings", type=float, default=5.0,
                     help="samo uz --optimize: minimalna usteda u EUR da se prijedlog uopce pokaze (default 5)")
    args = ap.parse_args()

    if not args.products and not args.browse:
        print("Moras zadati barem jedan --product ili --browse.", file=sys.stderr)
        sys.exit(1)

    if args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
    elif args.address:
        from geocoder import GeocodingError, geocode
        try:
            result = geocode(args.address)
        except GeocodingError as exc:
            print(f"Geokodiranje nije uspjelo: {exc}", file=sys.stderr)
            sys.exit(1)
        if not result:
            print(f"Nisam uspio geokodirati adresu: {args.address}", file=sys.stderr)
            sys.exit(1)
        lat, lon = result
        print(f"Adresa '{args.address}' -> {lat}, {lon}")
    else:
        print("Zadaj --address ili --lat/--lon.", file=sys.stderr)
        sys.exit(1)

    import os
    if not os.path.exists(args.db):
        print(f"Baza '{args.db}' ne postoji. Prvo pokreni import_data.py da je napravis.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"stores", "chain_products", "prices"} <= tables:
        print(f"Baza '{args.db}' postoji ali nije ispravno napunjena (nedostaju tablice). "
              f"Pokreni import_data.py.", file=sys.stderr)
        sys.exit(1)

    from import_data import get_data_info
    di = get_data_info(conn, args.db)
    def _nice(iso: str) -> str:
        from datetime import datetime
        try:
            return datetime.fromisoformat(iso).strftime("%d.%m.%Y. %H:%M")
        except ValueError:
            return iso

    if di["archive_date"]:
        y, m, d = di["archive_date"].split("-")
        age = {0: "danas", 1: "jucer"}.get(di["days_old"], f"prije {di['days_old']} dana")
        print(f"Podaci: cijene od {int(d)}.{int(m)}.{y}. ({age}), uvezeno {_nice(di['imported_at'])}")
    elif di["imported_at"]:
        print(f"Podaci: baza izgradjena {_nice(di['imported_at'])} (datum cijena nije zabiljezen)")
    if di["failed_chains"]:
        print(f"UPOZORENJE: ovi lanci nisu uvezeni: {', '.join(di['failed_chains'])}")

    print("Trazim proizvode...")
    items = []
    labels: dict[str, str] = {}
    for q in args.products:
        item = resolve_item(conn, q, auto=args.auto, labels=labels)
        if item:
            items.append(item)

    if args.browse:
        from browse import browse_by_price
        results, total = browse_by_price(conn, args.browse, lat, lon, args.radius,
                                          min_grams=args.min_g, max_grams=args.max_g,
                                          min_ml=args.min_ml, max_ml=args.max_ml, limit=args.limit)
        print(f"\nPregled po cijeni za '{args.browse}' (radijus {args.radius} km):")
        if not results:
            print("  Nema rezultata.")
        else:
            for r in results:
                print(f"  {r.price:6.2f} EUR  {r.chain:<10} {r.distance_km:5.2f} km  "
                      f"{r.address}, {r.city:<15} {r.label}")
            if total > len(results):
                print(f"  ... i jos {total - len(results)} (povecaj --limit da vidis vise)")
        if not items:
            return

    if not items:
        print("Nijedan proizvod nije prepoznat, prekidam.", file=sys.stderr)
        sys.exit(1)

    print(f"\nRacunam kosaricu u radijusu od {args.radius} km...")
    results = calculate_store_options(conn, items, lat, lon, max_distance_km=args.radius)

    print_per_item_report(conn, items, lat, lon, args.radius, labels)
    print_results(results, args.radius, labels)

    if args.optimize:
        from optimize import optimize_two_stores
        plan, baseline, _ = optimize_two_stores(conn, items, lat, lon, args.radius, min_savings=args.min_savings)
        print(f"\nOptimizacija kroz DVIJE poslovnice (prag ustede: {args.min_savings:.2f} EUR):")
        if not baseline:
            print("  Nema nijedne poslovnice gdje je sve dostupno - nema ni osnove za usporedbu.")
        elif plan:
            s1, s2 = plan.stores
            single_total = baseline.total + 0.30 * (baseline.distance_km or 0)
            saving = single_total - plan.total_cost
            print(f"  Isplati se! {s1.chain} ({s1.address}, {s1.city}, {s1.distance_km} km) + "
                  f"{s2.chain} ({s2.address}, {s2.city}, {s2.distance_km} km)")
            print(f"  Cijena stavki: {plan.items_cost:.2f} EUR, procijenjeni trosak puta: {plan.travel_cost:.2f} EUR, "
                  f"ukupno: {plan.total_cost:.2f} EUR")
            print(f"  Usteda u odnosu na najbolju jednu poslovnicu ({baseline.chain}, {single_total:.2f} EUR): "
                  f"{saving:.2f} EUR")
            print("  Raspodjela:")
            for q, idx in plan.assignment.items():
                store = s1 if idx == 0 else s2
                print(f"    {labels.get(q, q)} -> {store.chain}")
        else:
            print(f"  Ne isplati se - najbolja jedna poslovnica ({baseline.chain}, {baseline.total:.2f} EUR) "
                  f"je dovoljno dobra, nijedan par ne stedi barem {args.min_savings:.2f} EUR.")


if __name__ == "__main__":
    main()
