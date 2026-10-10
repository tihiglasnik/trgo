"""
Provjera alarma na cijene. Poziva se nakon svakog uspjesnog osvjezavanja
podataka (vidi app.py /api/refresh i update.py), jer tad cijene stvarno
mogu biti drukcije nego prije.

Dvije vrste alarma (price_alerts.target_price):

1. RUCNI CILJ (target_price postavljen) - trazi se NAJJEFTINIJA cijena za
   taj EAN bilo gdje u drzavi (bez ogranicenja radijusa, jer alarm nije
   vezan za jednu pretragu/adresu). Dogadjaj se biljezi SAMO kad cijena
   PRVI PUT padne na/ispod cilja (ne na svaku provjeru dok ostaje niska).

2. AUTOMATSKI "30-DNEVNI MINIMUM" (target_price = NULL) - koristi vec
   postojece polje best_price_30 iz same cijene.dev arhive: to je
   30-dnevni minimum TE KONKRETNE poslovnice za taj proizvod, izracunat
   kod izvora (mi tu povijest ne racunamo sami). Trazimo retke gdje je
   trenutna cijena <= best_price_30 te poslovnice - znaci da je cijena
   sad jednaka ili niza nego ikad u zadnjih mjesec dana. Dogadjaj se
   biljezi kad se nadje NOVA (niza) takva prilika nego zadnji put.

   VAZNA NAPOMENA: best_price_30 je u arhivi popunjen samo za dio redaka
   - izmjereno izmedju ~1% (Lidl) i 100% (KTC, Eurospin) ovisno o lancu.
   Automatski alarm zato nece podjednako dobro raditi za sve proizvode/
   lance - ako lanac ne salje best_price_30, taj alarm za taj lanac
   jednostavno nema sto uhvatiti.
"""

from __future__ import annotations

import sqlite3

import auth_db


def cheapest_price_nationwide(grocery_conn: sqlite3.Connection, ean: str) -> tuple[float, str] | None:
    row = grocery_conn.execute(
        """
        SELECT p.price, p.chain
        FROM prices p
        JOIN chain_products cp ON cp.chain = p.chain AND cp.product_code = p.product_code
        WHERE cp.canonical_ean = ? AND p.price IS NOT NULL
        ORDER BY p.price ASC
        LIMIT 1
        """,
        (ean,),
    ).fetchone()
    return (row[0], row[1]) if row else None


def best_30day_hit(grocery_conn: sqlite3.Connection, ean: str) -> tuple[float, str] | None:
    """Najniza trenutna cijena medju poslovnicama gdje je price <= njihov
    vlastiti best_price_30 - tj. gdje je proizvod sad na/ispod svoje
    30-dnevne niske. None ako takva poslovnica ne postoji (ili best_price_30
    uopce nije poznat za ovaj proizvod)."""
    row = grocery_conn.execute(
        """
        SELECT p.price, p.chain
        FROM prices p
        JOIN chain_products cp ON cp.chain = p.chain AND cp.product_code = p.product_code
        WHERE cp.canonical_ean = ? AND p.price IS NOT NULL
          AND p.best_price_30 IS NOT NULL AND p.price <= p.best_price_30
        ORDER BY p.price ASC
        LIMIT 1
        """,
        (ean,),
    ).fetchone()
    return (row[0], row[1]) if row else None


def check_all_alerts(users_conn: sqlite3.Connection, grocery_conn: sqlite3.Connection) -> int:
    """Provjerava SVE alarme svih korisnika. Vraca broj novo zabiljezenih dogadjaja."""
    all_alerts = users_conn.execute("SELECT * FROM price_alerts").fetchall()
    triggered = 0

    for alert in all_alerts:
        if alert["target_price"] is not None:
            result = cheapest_price_nationwide(grocery_conn, alert["ean"])
            if result is None:
                continue
            price, chain = result
            was_below = (alert["last_checked_price"] is not None
                         and alert["last_checked_price"] <= alert["target_price"])
            now_below = price <= alert["target_price"]
            crossed = now_below and not was_below
        else:
            result = best_30day_hit(grocery_conn, alert["ean"])
            if result is None:
                auth_db.update_alert_check(users_conn, alert["id"], None)
                continue
            price, chain = result
            # auto: okini na prvi pogodak ikad, ili kad je NOVA niza cijena
            # nego zadnji put zabiljezena (ne ponavljaj isti/visi pogodak)
            crossed = alert["last_checked_price"] is None or price < alert["last_checked_price"]

        if crossed:
            auth_db.record_alert_event(users_conn, alert["id"], alert["user_id"], price, chain)
            triggered += 1
        auth_db.update_alert_check(users_conn, alert["id"], price)

    users_conn.commit()
    return triggered
