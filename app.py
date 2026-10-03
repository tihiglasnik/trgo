#!/usr/bin/env python3
"""
Faza 1 + 2: web frontend s prijavom i spremljenim listama, poziva ISTU
logiku kao cli.py (basket.py, ranking.py, product_matcher.py, geocoder.py)
- nista nije duplicirano ni ponovno napisano.

Pokretanje:
    pip install --break-system-packages flask
    python app.py --db grocery.db

Zatim otvori http://127.0.0.1:5000 u browseru.

Namjerno slusa SAMO na 127.0.0.1 (localhost), ne na 0.0.0.0 - ovo je
osobni alat, pa ne bi trebao biti dostupan izvan tvog racunala. Ako bas
zelis pristup s drugog uredjaja u svojoj mrezi, pokreni s --host 0.0.0.0
eksplicitno i budi svjestan da tako svatko na tvojoj mrezi moze koristiti
alat i, sada s prijavom, i vidjeti prometuje li se lozinkama preko obicnog
HTTP-a (bez SSL-a) unutar tvoje mreze - za pristup izvan localhosta bi
trebalo dodati HTTPS (npr. preko reverse proxyja), sto ovaj alat ne radi.

Korisnicki racuni, liste i zapamceni izbori zive u ZASEBNOJ bazi
(users.db po defaultu, vidi auth_db.py) - namjerno odvojenoj od grocery.db,
koja se brise i ponovno gradi kod svakog "Osvjezi podatke".
"""

from __future__ import annotations

import argparse
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request, session

import auth_db
from basket import BasketItem, calculate_store_options
from browse import browse_by_price
from fetch_archive import ArchiveFetchError, fetch_latest
from fetch_enrichment import EnrichmentFetchError, fetch_enrichment
from geocoder import GeocodingError, geocode
from import_data import run_import
from product_matcher import search as search_products
import ranking

MAX_PRODUCTS = 25          # razumna gornja granica po upitu
MAX_RADIUS_KM = 50.0
MAX_LISTS_PER_USER = 50
MAX_BROWSE_LIMIT = 50
LOGIN_LOCKOUT_AFTER = 10   # neuspjelih pokusaja prijave prije privremenog zakljucavanja
LOGIN_LOCKOUT_SECONDS = 60

app = Flask(__name__)
app.config["DB_PATH"] = "grocery.db"
app.config["USERS_DB_PATH"] = "users.db"
_refresh_lock = threading.Lock()
_failed_logins: dict[str, list[float]] = {}  # email -> [timestamps neuspjelih pokusaja]


def _load_or_create_secret_key(path: Path) -> bytes:
    """
    Flaskova sesija (login) je potpisana ovim kljucem - mora biti stabilan
    izmedju pokretanja servera (inace se svi izloguju na svaki restart), ali
    se NIKAD ne smije dijeliti ili commitati u git. Generira se jednom,
    nasumicno, i sprema lokalno.
    """
    if path.exists():
        return path.read_bytes()
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    try:
        path.chmod(0o600)  # citljivo samo vlasniku, gdje OS to podrzava
    except OSError:
        pass
    return key


def get_db() -> sqlite3.Connection:
    """Jedna konekcija po requestu (sqlite3 konekcije nisu thread-safe za dijeljenje)."""
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DB_PATH"])
    return g.db


def get_users_db() -> sqlite3.Connection:
    if "users_db" not in g:
        g.users_db = auth_db.get_conn(app.config["USERS_DB_PATH"])
    return g.users_db


def current_user() -> sqlite3.Row | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return auth_db.get_user(get_users_db(), user_id)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return error("Moraš biti prijavljen/a za ovu radnju.", status=401)
        return view(*args, **kwargs)
    return wrapped


@app.teardown_appcontext
def close_db(_exc) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()
    users_db = g.pop("users_db", None)
    if users_db is not None:
        users_db.close()


def error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _user_public(user: sqlite3.Row) -> dict:
    return {"email": user["email"]}


@app.route("/api/auth/me")
def api_auth_me():
    user = current_user()
    return jsonify({"user": _user_public(user) if user else None})


@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "")
    password = str(data.get("password") or "")
    try:
        user_id = auth_db.create_user(get_users_db(), email, password)
    except auth_db.AuthError as exc:
        return error(str(exc))
    session.clear()
    session["user_id"] = user_id
    session.permanent = True
    return jsonify({"user": {"email": email.strip().lower()}})


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    if not email or not password:
        return error("Nedostaje email ili lozinka.")

    now = time.time()
    attempts = [t for t in _failed_logins.get(email, []) if now - t < LOGIN_LOCKOUT_SECONDS]
    if len(attempts) >= LOGIN_LOCKOUT_AFTER:
        return error("Previše neuspjelih pokušaja prijave, pričekaj malo pa pokušaj ponovno.", status=429)

    user = auth_db.verify_user(get_users_db(), email, password)
    if not user:
        attempts.append(now)
        _failed_logins[email] = attempts
        return error("Pogrešan email ili lozinka.", status=401)

    _failed_logins.pop(email, None)
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"user": _user_public(user)})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return error("Nedostaje parametar 'q'.")
    if len(q) > 100:
        return error("Upit je predugacak.")
    candidates = search_products(get_db(), q, limit=8)
    result = [
        {"ean": c.ean, "label": c.label, "chains_available": c.chains_available}
        for c in candidates
    ]

    user = current_user()
    remembered = None
    if user:
        row = auth_db.get_remembered(get_users_db(), user["id"], q)
        if row:
            remembered = {"ean": row["ean"], "name": row["name"], "label": row["label"]}

    return jsonify({"candidates": result, "remembered": remembered})


@app.route("/api/browse")
def api_browse():
    """
    Pregled po cijeni: za upit (npr. "kava", ili "kava -instant") vraca SVE
    odgovarajuce proizvode u obliznjim poslovnicama, sortirano od
    najjeftinijeg - za razliku od /api/search koji trazi JEDAN tocno
    odredjen proizvod za dodavanje u kosaricu.
    """
    q = (request.args.get("q") or "").strip()
    if not q:
        return error("Nedostaje parametar 'q'.")
    if len(q) > 100:
        return error("Upit je predugacak.")

    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return error("Nedostaju ili su neispravne koordinate (lat/lon).")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return error("Koordinate su izvan valjanog raspona.")

    try:
        radius = float(request.args.get("radius", 5))
    except (TypeError, ValueError):
        return error("Radijus mora biti broj.")
    if not (0 < radius <= MAX_RADIUS_KM):
        return error(f"Radijus mora biti izmedju 0 i {MAX_RADIUS_KM} km.")

    def parse_optional_float(name: str) -> float | None:
        raw = request.args.get(name)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            raise ValueError(name)

    try:
        min_g = parse_optional_float("min_g")
        max_g = parse_optional_float("max_g")
    except ValueError as exc:
        return error(f"'{exc.args[0]}' mora biti broj.")
    if min_g is not None and min_g < 0:
        return error("min_g ne smije biti negativan.")
    if max_g is not None and max_g < 0:
        return error("max_g ne smije biti negativan.")
    if min_g is not None and max_g is not None and min_g > max_g:
        return error("min_g ne smije biti veci od max_g.")

    try:
        limit = int(request.args.get("limit", 15))
    except ValueError:
        return error("limit mora biti cijeli broj.")
    if not (1 <= limit <= MAX_BROWSE_LIMIT):
        return error(f"limit mora biti izmedju 1 i {MAX_BROWSE_LIMIT}.")

    try:
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return error("offset mora biti cijeli broj.")
    if offset < 0:
        return error("offset ne smije biti negativan.")

    results, total = browse_by_price(get_db(), q, lat, lon, radius, min_grams=min_g, max_grams=max_g,
                                      limit=limit, offset=offset)
    return jsonify({
        "results": [
            {"ean": r.ean, "label": r.label, "price": r.price, "chain": r.chain,
             "address": r.address, "city": r.city, "distance_km": r.distance_km}
            for r in results
        ],
        "count": len(results),
        "total": total,
        "offset": offset,
    })


@app.route("/api/geocode", methods=["POST"])
def api_geocode():
    data = request.get_json(silent=True) or {}
    address = (data.get("address") or "").strip()
    if not address:
        return error("Nedostaje adresa.")
    if len(address) > 300:
        return error("Adresa je predugacka.")
    try:
        result = geocode(address)
    except GeocodingError as exc:
        return error(f"Geokodiranje nije uspjelo: {exc}", status=502)
    if not result:
        return error(f"Adresa nije pronadjena: {address}", status=404)
    lat, lon = result
    return jsonify({"lat": lat, "lon": lon})


def _parse_items(raw_items) -> tuple[list[BasketItem] | None, dict[str, str], str | None]:
    """Vraca (items, labels, greska). 'labels' mapira query -> stvarni naziv
    odabranog proizvoda (npr. "Dukat Mlijeko 1L"), za prikaz umjesto onoga
    sto je korisnik doslovno upisao (npr. "mlijeko")."""
    if not isinstance(raw_items, list) or not raw_items:
        return None, {}, "Popis proizvoda je prazan."
    if len(raw_items) > MAX_PRODUCTS:
        return None, {}, f"Previse proizvoda (max {MAX_PRODUCTS})."
    items: list[BasketItem] = []
    labels: dict[str, str] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            return None, {}, "Neispravan format stavke."
        query = str(raw.get("query") or "").strip()
        if not query:
            return None, {}, "Svaka stavka mora imati 'query'."
        ean = raw.get("ean")
        name = raw.get("name")
        label = raw.get("label")
        items.append(BasketItem(
            query=query,
            ean=str(ean) if ean else None,
            name=str(name) if (name and not ean) else None,
        ))
        if label:
            labels[query] = str(label)[:200]
    return items, labels, None


@app.route("/api/lists", methods=["GET"])
@login_required
def api_lists_get():
    user = current_user()
    lists = auth_db.get_lists(get_users_db(), user["id"])
    return jsonify([
        {"id": r["id"], "name": r["name"], "created_at": r["created_at"], "item_count": r["item_count"]}
        for r in lists
    ])


@app.route("/api/lists", methods=["POST"])
@login_required
def api_lists_create():
    user = current_user()
    existing = auth_db.get_lists(get_users_db(), user["id"])
    if len(existing) >= MAX_LISTS_PER_USER:
        return error(f"Dosegnut je limit od {MAX_LISTS_PER_USER} lista.")
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    try:
        list_id = auth_db.create_list(get_users_db(), user["id"], name)
    except auth_db.AuthError as exc:
        return error(str(exc))
    return jsonify({"id": list_id, "name": name})


@app.route("/api/lists/<int:list_id>", methods=["GET"])
@login_required
def api_list_get(list_id: int):
    user = current_user()
    lst = auth_db.get_list_with_items(get_users_db(), user["id"], list_id)
    if not lst:
        return error("Lista nije pronađena.", status=404)
    return jsonify(lst)


@app.route("/api/lists/<int:list_id>", methods=["DELETE"])
@login_required
def api_list_delete(list_id: int):
    user = current_user()
    ok = auth_db.delete_list(get_users_db(), user["id"], list_id)
    if not ok:
        return error("Lista nije pronađena.", status=404)
    return jsonify({"ok": True})


@app.route("/api/lists/<int:list_id>/items", methods=["POST"])
@login_required
def api_list_add_item(list_id: int):
    user = current_user()
    data = request.get_json(silent=True) or {}
    query = str(data.get("query") or "").strip()
    if not query:
        return error("Nedostaje naziv proizvoda.")
    if len(query) > 100:
        return error("Naziv proizvoda je predugacak.")
    ean = data.get("ean")
    name = data.get("name")
    label = data.get("label")
    item_id = auth_db.add_item(get_users_db(), user["id"], list_id, query,
                                str(ean) if ean else None, str(name) if name else None,
                                str(label)[:200] if label else None)
    if item_id is None:
        return error("Lista nije pronađena.", status=404)
    return jsonify({"id": item_id})


@app.route("/api/lists/<int:list_id>/items/<int:item_id>", methods=["DELETE"])
@login_required
def api_list_delete_item(list_id: int, item_id: int):
    user = current_user()
    ok = auth_db.delete_item(get_users_db(), user["id"], list_id, item_id)
    if not ok:
        return error("Stavka nije pronađena.", status=404)
    return jsonify({"ok": True})


@app.route("/api/remember", methods=["POST"])
@login_required
def api_remember():
    user = current_user()
    data = request.get_json(silent=True) or {}
    query = str(data.get("query") or "").strip()
    label = str(data.get("label") or "").strip()
    if not query or not label:
        return error("Nedostaje query ili label.")
    if len(query) > 100 or len(label) > 200:
        return error("Unos je predugacak.")
    ean = data.get("ean")
    name = data.get("name")
    auth_db.remember_product(get_users_db(), user["id"], query,
                              str(ean) if ean else None, str(name) if name else None, label)
    return jsonify({"ok": True})


@app.route("/api/find", methods=["POST"])
def api_find():
    data = request.get_json(silent=True) or {}

    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
    except (TypeError, ValueError):
        return error("Nedostaju ili su neispravne koordinate (lat/lon).")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return error("Koordinate su izvan valjanog raspona.")

    try:
        radius = float(data.get("radius", 5))
    except (TypeError, ValueError):
        return error("Radijus mora biti broj.")
    if not (0 < radius <= MAX_RADIUS_KM):
        return error(f"Radijus mora biti izmedju 0 i {MAX_RADIUS_KM} km.")

    items, labels, err = _parse_items(data.get("items"))
    if err:
        return error(err)

    conn = get_db()

    per_item = []
    for item in items:
        single = calculate_store_options(conn, [item], lat, lon, max_distance_km=radius)
        available = ranking.complete_only(single)
        entry = {"query": item.query, "label": labels.get(item.query, item.query), "found": bool(available)}
        if available:
            # grupirano po lancu - samo najniza cijena unutar tog lanca, bez adresa/poslovnica
            best_per_chain: dict[str, float] = {}
            for r in available:
                if r.chain not in best_per_chain or r.total < best_per_chain[r.chain]:
                    best_per_chain[r.chain] = r.total
            entry["chains"] = sorted(
                [{"chain": c, "price": p} for c, p in best_per_chain.items()],
                key=lambda x: x["price"],
            )
        per_item.append(entry)

    combined = calculate_store_options(conn, items, lat, lon, max_distance_km=radius)
    # "Sve poslovnice u radijusu" - sortirano iskljucivo po udaljenosti od
    # adrese pretrage, bez obzira na cijenu/lanac/nedostajuce stavke
    combined_sorted = sorted(combined, key=lambda r: r.distance_km if r.distance_km is not None else float("inf"))
    complete = ranking.complete_only(combined)
    near = ranking.nearest(combined)
    comp = ranking.best_compromise(combined)

    def as_dict(r):
        return {
            "chain": r.chain, "address": r.address, "city": r.city,
            "distance_km": r.distance_km, "total": r.total,
            "missing": [labels.get(q, q) for q in r.missing_items],
        }

    # po lancu: najniza ukupna cijena unutar lanca + najbliza konkretna poslovnica tog
    # lanca (adresa se otkriva tek na klik u frontendu, ne prikazuje se automatski za sve)
    by_chain: dict[str, list] = {}
    for r in complete:
        by_chain.setdefault(r.chain, []).append(r)

    chains_summary = []
    for chain, group in by_chain.items():
        min_total = min(r.total for r in group)
        nearest_in_chain = min(group, key=lambda r: r.distance_km if r.distance_km is not None else float("inf"))
        chains_summary.append({
            "chain": chain,
            "min_total": min_total,
            "stores_available": len(group),
            "nearest": as_dict(nearest_in_chain),
        })
    chains_summary.sort(key=lambda x: x["min_total"])

    return jsonify({
        "per_item": per_item,
        "all_stores": [as_dict(r) for r in combined_sorted[:30]],
        "all_stores_total": len(combined_sorted),
        "chains_summary": chains_summary,
        "nearest_overall": as_dict(near) if near else None,
        "compromise": as_dict(comp) if comp else None,
    })


@app.route("/api/optimize", methods=["POST"])
def api_optimize():
    """
    Provjerava isplati li se razdijeliti kupnju na DVIJE poslovnice umjesto
    jedne. Vraca prijedlog SAMO ako postoji par koji pokriva sve stavke I
    stedi barem 'min_savings' u odnosu na najbolju jednu poslovnicu.
    """
    data = request.get_json(silent=True) or {}

    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
    except (TypeError, ValueError):
        return error("Nedostaju ili su neispravne koordinate (lat/lon).")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return error("Koordinate su izvan valjanog raspona.")

    try:
        radius = float(data.get("radius", 5))
    except (TypeError, ValueError):
        return error("Radijus mora biti broj.")
    if not (0 < radius <= MAX_RADIUS_KM):
        return error(f"Radijus mora biti izmedju 0 i {MAX_RADIUS_KM} km.")

    try:
        min_savings = float(data.get("min_savings", 5.0))
    except (TypeError, ValueError):
        return error("min_savings mora biti broj.")
    if min_savings < 0:
        return error("min_savings ne smije biti negativan.")

    items, labels, err = _parse_items(data.get("items"))
    if err:
        return error(err)

    from optimize import optimize_two_stores
    plan, baseline, _ = optimize_two_stores(get_db(), items, lat, lon, radius, min_savings=min_savings)

    def store_dict(r):
        return {"chain": r.chain, "address": r.address, "city": r.city,
                "distance_km": r.distance_km, "total": r.total}

    baseline_out = None
    if baseline:
        baseline_out = store_dict(baseline)
        baseline_out["total_with_travel"] = round(baseline.total + 0.30 * (baseline.distance_km or 0), 2)

    plan_out = None
    if plan:
        s1, s2 = plan.stores
        plan_out = {
            "stores": [store_dict(s1), store_dict(s2)],
            "items_cost": round(plan.items_cost, 2),
            "travel_cost": round(plan.travel_cost, 2),
            "total_cost": round(plan.total_cost, 2),
            "assignment": [
                {"query": q, "label": labels.get(q, q), "store_index": idx}
                for q, idx in plan.assignment.items()
            ],
            "savings": round((baseline_out["total_with_travel"] - plan.total_cost), 2) if baseline_out else None,
        }

    return jsonify({"plan": plan_out, "baseline": baseline_out})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    if not _refresh_lock.acquire(blocking=False):
        return error("Osvježavanje je već u tijeku, pričekaj da završi.", status=409)

    try:
        db_path = Path(app.config["DB_PATH"])
        work_dir = Path(".")
        enrichment_dir = work_dir / "enrichment"
        data_dir = work_dir / "data"
        tmp_db_path = db_path.with_suffix(".refreshing.db")

        # obrisi stare preuzete podatke (arhivu + enrichment) da se stvarno
        # povuku svjezi, ne samo ponovno iskoriste stari cache
        for old_dir in (enrichment_dir, data_dir):
            if old_dir.exists():
                shutil.rmtree(old_dir)
        if tmp_db_path.exists():
            tmp_db_path.unlink()

        try:
            fresh_enrichment_dir = fetch_enrichment(enrichment_dir)
        except EnrichmentFetchError as exc:
            return error(f"Preuzimanje enrichment podataka nije uspjelo: {exc}", status=502)

        try:
            fresh_archive_dir = fetch_latest(data_dir)
        except ArchiveFetchError as exc:
            return error(f"Preuzimanje dnevne arhive nije uspjelo: {exc}", status=502)

        try:
            skip_fallback = str(request.args.get("skip_fallback_search", "")).lower() in ("1", "true", "yes")
            failed = run_import(fresh_archive_dir, fresh_enrichment_dir, tmp_db_path, chains=None,
                                 build_chain_fallback_search=not skip_fallback)
        except FileNotFoundError as exc:
            return error(str(exc), status=500)

        # tek SAD, kad je novi import u potpunosti gotov i uspio, zamijeni staru
        # bazu novom - ako bilo sto gore pukne, stara baza ostaje netaknuta i
        # dalje radi normalno
        tmp_db_path.replace(db_path)

        return jsonify({"ok": True, "failed_chains": failed})
    finally:
        _refresh_lock.release()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="grocery.db")
    ap.add_argument("--users-db", default="users.db",
                     help="zasebna baza za racune/liste/zapamcene izbore (default: users.db)")
    ap.add_argument("--host", default="127.0.0.1",
                     help="default 127.0.0.1 (samo ovo racunalo). Promijeni na 0.0.0.0 samo ako svjesno zelis "
                          "izloziti alat ostatku svoje mreze - nema HTTPS-a, pa se lozinke tada prenose "
                          "nesifrirano unutar tvoje mreze.")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"Baza '{args.db}' ne postoji. Prvo pokreni: python import_data.py ... ili python update.py",
              file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    if not {"stores", "chain_products", "prices"} <= tables:
        print(f"Baza '{args.db}' postoji ali nije ispravno napunjena. Pokreni import_data.py.", file=sys.stderr)
        sys.exit(1)

    app.config["DB_PATH"] = args.db
    app.config["USERS_DB_PATH"] = args.users_db
    app.secret_key = _load_or_create_secret_key(Path(args.users_db).with_suffix(".secret_key"))
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 30  # 30 dana
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # inicijaliziraj users.db odmah (stvara fajl+tablice ako ne postoje)
    auth_db.get_conn(args.users_db).close()

    print(f"grocery-finder web frontend: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
