"""
Baza za korisnicke racune, spremljene liste i "zapamcene" izbore proizvoda.

NAMJERNO odvojena od grocery.db (cijene/proizvodi/poslovnice) - ta baza se
u potpunosti brise i ponovno gradi svaki put kad se pokrene osvjezavanje
podataka (vidi app.py /api/refresh). Da su korisnicki podaci u istoj bazi,
svako osvjezavanje cijena bi obrisalo i sve racune i liste. Ova baza se
NIKAD ne dira prilikom osvjezavanja cijena.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from product_matcher import normalize_name

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shopping_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shopping_list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id INTEGER NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    ean TEXT,
    name TEXT,
    label TEXT,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS remembered_products (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query_norm TEXT NOT NULL,
    ean TEXT,
    name TEXT,
    label TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, query_norm)
);

-- target_price = NULL znaci "automatski" nacin: koristi se best_price_30
-- iz izvornih podataka (30-dnevni minimum te konkretne poslovnice) umjesto
-- rucno postavljenog praga - vidi alerts.py za potpuno objasnjenje. U
-- users.db (ne grocery.db) jer se grocery.db u potpunosti brise i gradi
-- iznova pri svakom osvjezavanju - alarmi/povijest ne smiju biti tamo.
CREATE TABLE IF NOT EXISTS price_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ean TEXT NOT NULL,
    label TEXT NOT NULL,
    target_price REAL,
    last_checked_price REAL,
    last_checked_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Zabiljezeni "okidaji" (kad cijena prvi put padne na/ispod praga). Jedan
-- zapis po prelasku, ne po svakoj provjeri - da se ne spamira isti alarm
-- dok cijena ostaje niska.
CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL REFERENCES price_alerts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    price REAL NOT NULL,
    chain TEXT,
    triggered_at TEXT NOT NULL DEFAULT (datetime('now')),
    seen INTEGER NOT NULL DEFAULT 0
);
"""

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """Bacaju je registracija/prijava kad podaci nisu valjani ili se ne poklapaju."""


def get_conn(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def create_user(conn: sqlite3.Connection, email: str, password: str) -> int:
    email = _normalize_email(email)
    if not EMAIL_RE.match(email):
        raise AuthError("Adresa e-poste nije u ispravnom formatu.")
    if len(password) < 8:
        raise AuthError("Lozinka mora imati barem 8 znakova.")
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        raise AuthError("Vec postoji racun s tom adresom e-poste.")
    password_hash = generate_password_hash(password)
    cur = conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        (email, password_hash),
    )
    conn.commit()
    return cur.lastrowid


def verify_user(conn: sqlite3.Connection, email: str, password: str) -> sqlite3.Row | None:
    email = _normalize_email(email)
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        return None
    if not check_password_hash(row["password_hash"], password):
        return None
    return row


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


# ---- liste -----------------------------------------------------------------

def create_list(conn: sqlite3.Connection, user_id: int, name: str) -> int:
    name = name.strip()
    if not name:
        raise AuthError("Naziv liste ne smije biti prazan.")
    if len(name) > 100:
        raise AuthError("Naziv liste je predugacak.")
    cur = conn.execute(
        "INSERT INTO shopping_lists (user_id, name) VALUES (?, ?)",
        (user_id, name),
    )
    conn.commit()
    return cur.lastrowid


def get_lists(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT sl.id, sl.name, sl.created_at, COUNT(sli.id) AS item_count
        FROM shopping_lists sl
        LEFT JOIN shopping_list_items sli ON sli.list_id = sl.id
        WHERE sl.user_id = ?
        GROUP BY sl.id
        ORDER BY sl.created_at DESC
        """,
        (user_id,),
    ).fetchall()


def get_list_with_items(conn: sqlite3.Connection, user_id: int, list_id: int) -> dict | None:
    lst = conn.execute(
        "SELECT id, name, created_at FROM shopping_lists WHERE id = ? AND user_id = ?",
        (list_id, user_id),
    ).fetchone()
    if not lst:
        return None
    items = conn.execute(
        "SELECT id, query, ean, name, label FROM shopping_list_items WHERE list_id = ? ORDER BY position, id",
        (list_id,),
    ).fetchall()
    return {"id": lst["id"], "name": lst["name"], "created_at": lst["created_at"],
            "items": [dict(i) for i in items]}


def delete_list(conn: sqlite3.Connection, user_id: int, list_id: int) -> bool:
    cur = conn.execute("DELETE FROM shopping_lists WHERE id = ? AND user_id = ?", (list_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def add_item(conn: sqlite3.Connection, user_id: int, list_id: int,
             query: str, ean: str | None, name: str | None, label: str | None = None) -> int | None:
    owned = conn.execute(
        "SELECT id FROM shopping_lists WHERE id = ? AND user_id = ?", (list_id, user_id)
    ).fetchone()
    if not owned:
        return None
    pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM shopping_list_items WHERE list_id = ?", (list_id,)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO shopping_list_items (list_id, query, ean, name, label, position) VALUES (?, ?, ?, ?, ?, ?)",
        (list_id, query.strip(), ean, name, label, pos),
    )
    conn.commit()
    return cur.lastrowid


def delete_item(conn: sqlite3.Connection, user_id: int, list_id: int, item_id: int) -> bool:
    owned = conn.execute(
        "SELECT id FROM shopping_lists WHERE id = ? AND user_id = ?", (list_id, user_id)
    ).fetchone()
    if not owned:
        return False
    cur = conn.execute(
        "DELETE FROM shopping_list_items WHERE id = ? AND list_id = ?", (item_id, list_id)
    )
    conn.commit()
    return cur.rowcount > 0


# ---- zapamceni izbori proizvoda ---------------------------------------------

def remember_product(conn: sqlite3.Connection, user_id: int, query: str,
                      ean: str | None, name: str | None, label: str) -> None:
    query_norm = normalize_name(query)
    if not query_norm:
        return
    conn.execute(
        """
        INSERT INTO remembered_products (user_id, query_norm, ean, name, label, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT (user_id, query_norm)
        DO UPDATE SET ean = excluded.ean, name = excluded.name, label = excluded.label,
                      updated_at = excluded.updated_at
        """,
        (user_id, query_norm, ean, name, label),
    )
    conn.commit()


def get_remembered(conn: sqlite3.Connection, user_id: int, query: str) -> sqlite3.Row | None:
    query_norm = normalize_name(query)
    if not query_norm:
        return None
    return conn.execute(
        "SELECT ean, name, label FROM remembered_products WHERE user_id = ? AND query_norm = ?",
        (user_id, query_norm),
    ).fetchone()


# ---- obavijesti o cijenama ---------------------------------------------

def create_alert(conn: sqlite3.Connection, user_id: int, ean: str, label: str,
                  target_price: float | None) -> int:
    """target_price=None znaci automatski ("30-dnevni minimum") alarm."""
    if not ean:
        raise AuthError("Alarm treba konkretan proizvod (EAN).")
    if target_price is not None and target_price <= 0:
        raise AuthError("Ciljna cijena mora biti veca od 0.")
    cur = conn.execute(
        "INSERT INTO price_alerts (user_id, ean, label, target_price) VALUES (?, ?, ?, ?)",
        (user_id, ean, label, target_price),
    )
    conn.commit()
    return cur.lastrowid


def get_alerts(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM price_alerts WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()


def delete_alert(conn: sqlite3.Connection, user_id: int, alert_id: int) -> bool:
    cur = conn.execute("DELETE FROM price_alerts WHERE id = ? AND user_id = ?", (alert_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def update_alert_check(conn: sqlite3.Connection, alert_id: int, price: float | None) -> None:
    conn.execute(
        "UPDATE price_alerts SET last_checked_price = ?, last_checked_at = datetime('now') WHERE id = ?",
        (price, alert_id),
    )


def record_alert_event(conn: sqlite3.Connection, alert_id: int, user_id: int, price: float, chain: str) -> int:
    cur = conn.execute(
        "INSERT INTO alert_events (alert_id, user_id, price, chain) VALUES (?, ?, ?, ?)",
        (alert_id, user_id, price, chain),
    )
    return cur.lastrowid


def get_events(conn: sqlite3.Connection, user_id: int, unseen_only: bool = False) -> list[sqlite3.Row]:
    where = "ae.user_id = ?" + (" AND ae.seen = 0" if unseen_only else "")
    return conn.execute(
        f"""
        SELECT ae.id, ae.price, ae.chain, ae.triggered_at, ae.seen, pa.ean, pa.label, pa.target_price
        FROM alert_events ae JOIN price_alerts pa ON pa.id = ae.alert_id
        WHERE {where}
        ORDER BY ae.triggered_at DESC
        """,
        (user_id,),
    ).fetchall()


def mark_events_seen(conn: sqlite3.Connection, user_id: int, event_ids: list[int] | None = None) -> None:
    if event_ids is None:
        conn.execute("UPDATE alert_events SET seen = 1 WHERE user_id = ?", (user_id,))
    else:
        placeholders = ",".join("?" for _ in event_ids)
        conn.execute(
            f"UPDATE alert_events SET seen = 1 WHERE user_id = ? AND id IN ({placeholders})",
            (user_id, *event_ids),
        )
    conn.commit()
