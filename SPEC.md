# grocery-finder — tehnička i funkcionalna specifikacija

| | |
|---|---|
| Datum dokumenta | 10. 10. 2026. |
| Obuhvaća | Faze 0, 1 i 2 u cijelosti; od Faze 3: optimizacija kroz dvije trgovine, pregled po cijeni, obavijesti o cijenama |
| Nije napravljeno | povijest cijena kroz više dana (ostatak Faze 3), AI sloj (Faza 4) |
| Referentni podaci | cijene.dev dnevna arhiva od 22. 9. 2026. |
| Veličina koda | 4.882 redaka: Python 3.1k, JS 1.0k, CSS 0.6k, HTML 0.2k, shell 36 |
| Korisnički priručnik | `KORISNICKI_PRIRUCNIK.md` (zaseban dokument) |

Dokument je pisan iz stvarnog koda i izmjerenih podataka. Gdje nešto nije testirano ili je nepoznato, to piše izričito (odjeljci 12 i 13).

---

## Dio A — Funkcionalna specifikacija

### A1. Svrha

Korisnik zna što treba kupiti i gdje živi. Alat mu kaže u kojoj obližnjoj trgovini je ta košarica najjeftinija, koliko je trgovina daleko, te omogućuje pregled svih varijanti nekog proizvoda po cijeni i praćenje cijena.

Izvan opsega: nativna mobilna aplikacija, naručivanje/dostava, stvarni put (koristi se zračna linija), cijene u stvarnom vremenu (izvor je dnevna arhiva), javni hosting za više korisnika.

### A2. Načela

- CSV je ulaz, nikad runtime izvor: sve se pretražuje iz lokalne baze.
- Transparentnost umjesto "AI odlučuje": prikazuju se cijene, udaljenosti i što nedostaje; heuristike imaju eksplicitne parametre.
- Ista jezgra za CLI i web (`basket`, `ranking`, `product_matcher`, `browse`, `optimize`, `alerts`).
- Korisnički podaci strogo odvojeni od podataka o cijenama (dvije baze).
- Sigurni zadani parametri: slušanje samo na `127.0.0.1`, parametrizirani SQL, DOM bez `innerHTML` za tekst iz baze.

### A3. Popis funkcija

| ID | Funkcija | Prijava | Gdje |
|---|---|---|---|
| F-01 | Pretraga proizvoda po riječima (AND, prefiks, brojevi) za dodavanje u košaricu | ne | web, CLI |
| F-02 | Cijena košarice po lancu i po poslovnici u radijusu | ne | web, CLI |
| F-03 | Najbliža poslovnica ukupno i "kompromis" cijena/udaljenost | ne | web, CLI |
| F-04 | Cijene po pojedinom proizvodu (po lancu) | ne | web, CLI |
| F-05 | Popis svih poslovnica u radijusu, sortiran po udaljenosti | ne | web, CLI |
| F-06 | Optimizacija kroz dvije trgovine | ne | web, CLI |
| F-07 | Pregled po cijeni: sve varijante proizvoda, filtri, "učitaj još" | ne | web, CLI |
| F-08 | Račun: registracija, prijava, odjava | — | web |
| F-09 | Spremljene imenovane liste | da | web |
| F-10 | Zapamćeni izbor proizvoda po upitu | da | web |
| F-11 | Obavijesti o cijenama (ručni cilj, automatski 30-dnevni minimum) | da | web |
| F-12 | Osvježavanje podataka (gumb, `update.py`) | ne | web, CLI |
| F-13 | Pokretanje jednom naredbom (`start.sh`) | — | shell |

### A4. Poslovna pravila po funkciji

**F-01 Pretraga proizvoda (odabir za košaricu)** — `product_matcher.search`
- Upit se normalizira (mala slova, `,`→`.`, uklanjanje interpunkcije) i razlomi na riječi.
- Svaka riječ mora postojati (AND), u bilo kojem redoslijedu, u marki ili nazivu.
- Riječ je prefiks: `duka` pogađa `dukat`, `mlijek` pogađa `mlijeko`. Ispred riječi ne smije stajati slovo, pa `jaja` ne pogađa `Sjaja`.
- Broj mora biti cijeli token: `2` pogađa `2,8%` ali ne `20`.
- Kandidati su sortirani po broju lanaca koji proizvod imaju (padajuće), vraća se najviše 8.
- Prvo se traži u pročišćenom katalogu (proizvodi s EAN-om). Tek ako katalog ne vrati ništa, traži se među svim nazivima iz arhive (kandidat bez EAN-a). Vidi D-01.
- Ograničenje: pretraga gleda samo marku i naziv, ne kolonu količine.

**F-02 Košarica po poslovnici** — `basket.calculate_store_options`
- Razmatraju se samo poslovnice s koordinatama koje su unutar radijusa (zračna linija, haversine).
- Cijena stavke u poslovnici je najniža `price` za taj EAN u toj poslovnici.
- Stavka koje poslovnica nema ide u `missing`. Zbroj `total` sadrži samo pronađene stavke, pa je za nepotpunu košaricu djelomičan.
- Košarica bez EAN-a (fallback po točnom nazivu) radi samo unutar lanca koji taj naziv ima (D-01).
- Adresa se čisti od Sparovih internih dodataka (`"... 87006 Spar"` → `"..."`).

**F-03 Najbliža i kompromis** — `ranking`
- Najbliža: najmanja udaljenost, i ako poslovnici nešto nedostaje (prikazuje se što).
- Kompromis: među poslovnicama gdje je dostupno **sve**, minimum od `cijena + 0,30 € × udaljenost_km`. Koeficijent je parametar `eur_per_km`, u sučelju nije izložen.
- Poslovnice s nepotpunom košaricom nikad nisu "najjeftinije".

**F-04 Po pojedinom proizvodu**
- Za svaki proizvod posebno, najniža cijena unutar svakog lanca u radijusu. Bez adresa i pojedinačnih poslovnica.
- Prikazuje se pravi naziv odabranog proizvoda, ne ono što je korisnik upisao.

**F-05 Sve poslovnice**
- Svih poslovnica u radijusu, sortirano **isključivo po udaljenosti**, bez obzira na lanac, cijenu ili nedostajuće stavke. Prikazuje se prvih 30, uz broj preostalih. "Nedostaje" navodi prave nazive.

**F-06 Optimizacija kroz dvije trgovine** — `optimize.optimize_two_stores`
- Kandidati: do 25 najbližih poslovnica koje imaju bar jednu traženu stavku. Razmatraju se svi parovi.
- Za par: svaka stavka ide u jeftiniju od dvije poslovnice (gdje je dostupna). Par mora zajedno pokriti **sve** stavke.
- Trošak puta: `0,30 € × (udaljenost A + udaljenost B)`. Pojednostavljenje: svaka poslovnica tretira se kao zaseban put od kuće, ne kao ruta kroz obje.
- Usporedba s najboljom jednom poslovnicom (cijena + njen trošak puta). Prijedlog se prikazuje samo ako ušteda ≥ prag (zadano 5 €, korisnik ga mijenja). Inače: "ne isplati se".

**F-07 Pregled po cijeni** — `browse.browse_by_price`
- Upit se razlaže na ukljuceno (`kava`), isključeno (`-instant`) i brojeve (`0.25`).
- **Filtriranje radi nad prikazanim nazivom** (marka + naziv + količina), jer korisnik vidi i količinu iz zasebne kolone (`0.25 kg`). Zato `-0.25` isključuje proizvode od 0,25 kg, a `kava 0.25` nalazi samo takve. Broj je cijeli token: `0.25` ne pogađa `10.25` ni `0.250`.
- Filter težine (g): proizvodi s jedinicom g/kg. Filter volumena (ml): proizvodi s jedinicom l/ml.
- Ako su aktivna oba filtra, proizvod prolazi ako zadovoljava filtar **svoje** vrste (g **ili** ml). Ako je aktivan samo jedan, ostale vrste (kom, m, par) se izostavljaju.
- Za svaki proizvod uzima se najniža cijena u radijusu i poslovnica u kojoj je. Sortirano po cijeni, stranica od 15, "učitaj još" dodaje još 15; ukupan broj je poznat. Rezultati se mogu sklopiti.
- Prazan rezultat je normalan ishod (poruka), ne greška.

**F-08 do F-10 Račun, liste, zapamćeni izbor**
- Prijava je opcionalna: bez nje sve osim F-08 do F-11 radi.
- Liste čuvaju EAN (ili točan naziv ako EAN-a nema), nikad interne šifre lanca, jer se one mijenjaju s arhivom.
- Zapamćeni izbor: za isti (normalizirani) upit sljedeći put se proizvod sam odabere uz oznaku "(zapamćeno)" i vezu "promijeni".
- Limit 50 lista po korisniku.

**F-11 Obavijesti o cijenama** — `alerts`
- Alarm je vezan za EAN, ne za adresu: gleda se **cijela država**.
- Ručni cilj: obavijest kad najniža cijena bilo gdje padne na ili ispod cilja. Bilježi se samo **prelazak** praga, ne svaka provjera.
- Automatski (cilj prazan): koristi polje `best_price_30` iz arhive (30-dnevni minimum **te poslovnice**, računa ga izvor). Obavijest kad je trenutna cijena ≤ tog minimuma, i to samo za novu nižu cijenu nego zadnji put.
- `best_price_30` je popunjen za samo 16,4 % redaka (od ~1 % Lidl do 100 % KTC/Eurospin), pa automatski alarm ne hvata sve podjednako.
- Provjera se pokreće automatski nakon osvježavanja podataka, i na zahtjev (dodavanje alarma).
- Proizvodi bez EAN-a ne mogu imati alarm. Limit 30 alarma po korisniku.

**F-12 Osvježavanje**
- Briše staru arhivu i enrichment, preuzima najnovije, gradi **privremenu** bazu i tek po potpunom uspjehu zamjenjuje staru. Neuspjeh ne uništava postojeću bazu.
- Opcija "brže" preskače izgradnju FTS indeksa za proizvode izvan kataloga (vidi odjeljak 11).
- **Prikaz kad su podaci ažurirani:** zaglavlje prikazuje datum arhive cijena ("Cijene od 22. 9. 2026. (prije 3 dana)") i vrijeme uvoza. Prikaz postaje žut ako su cijene starije od 2 dana, ako je lanac preskočen pri uvozu (uz popis lanaca) ili ako datum nije zabilježen. Nakon uspješnog osvježavanja prikaz se sam obnovi. Isti podatak ispisuje CLI na početku rada.
- Preskočeni lanac (neispravan ili nepotpun CSV) računa se kao neuspješan uvoz, pa se uvijek vidi.
- Datum arhive izvodi se iz naziva mape (`data/YYYY-MM-DD`, što rade `update.py` i gumb). Kod ručnog uvoza iz mape drukčijeg naziva datum ostaje nepoznat, a prikazuje se samo vrijeme izgradnje baze. Isto vrijedi za baze napravljene prije ove izmjene: dok se ne osvježe, prikazuje se vrijeme zadnje izmjene datoteke i napomena da datum cijena nije zabilježen.

### A5. Korisnički tokovi (sažeto)

1. *Košarica:* adresa → radijus → proizvodi (tipka se, bira kandidat) → "Pronađi" → kartice, lanci, po proizvodu, sve poslovnice → (opc.) optimizacija.
2. *Pregled:* upit (+ isključene riječi, g/ml) → "Traži" → lista od najjeftinije → "Učitaj još".
3. *Račun:* registracija → liste, zapamćeni izbori i obavijesti postaju dostupni.
4. *Osvježavanje:* gumb ili `python update.py`.

Detaljan opis za krajnjeg korisnika: `KORISNICKI_PRIRUCNIK.md`.

---

## Dio B — Tehnička specifikacija

### B1. Arhitektura

```
 api.cijene.dev ──(dnevni ZIP)──► fetch_archive.py ─┐
 GitHub raw: enrichment CSV ────► fetch_enrichment.py ┤
                                                      ▼
                                              import_data.py
                                                      │
                                                      ▼
                                       grocery.db (SQLite, ~1,1 GB)
                                       cijene · proizvodi · poslovnice · FTS5
                                                      ▲
 Preglednik ◄─JSON/HTTP─► app.py (Flask) ────────────┤
     │                      ├─ product_matcher.py    │
     │                      ├─ basket.py ────────────┤
     │                      ├─ ranking.py            │
     │                      ├─ optimize.py ──────────┤
     │                      ├─ browse.py ────────────┤
     │                      ├─ alerts.py ────────────┤
     │                      ├─ geocoder.py ─► Nominatim (OSM)
     │                      └─ auth_db.py ─► users.db (+ users.secret_key)
 cli.py koristi iste module (bez auth_db i alerts)
```

**Dvije baze su namjerna odluka.** `grocery.db` se pri osvježavanju potpuno gradi iznova. Računi, liste, zapamćeni izbori i alarmi zato žive u `users.db`, koju osvježavanje nikad ne dira.

### B2. Datoteke

| Datoteka | Redaka | Odgovornost |
|---|---|---|
| `app.py` | 775 | Flask: rute, sesije, zaključavanje prijave, osvježavanje |
| `auth_db.py` | 315 | Shema i operacije `users.db` |
| `import_data.py` | 467 | CSV → SQLite, validacija, izolacija grešaka po lancu |
| `browse.py` | 249 | Pregled po cijeni |
| `cli.py` | 280 | Terminalsko sučelje |
| `optimize.py` | 152 | Dvije trgovine |
| `basket.py` | 142 | Cijena košarice po poslovnici |
| `product_matcher.py` | 121 | Pretraga proizvoda (FTS5) |
| `fetch_archive.py` / `fetch_enrichment.py` | 116 / 51 | Preuzimanje podataka |
| `alerts.py` | 101 | Provjera alarma |
| `update.py` | 93 | Preuzimanje + uvoz + provjera alarma jednom naredbom |
| `geocoder.py` / `geo.py` / `barcodes.py` / `ranking.py` | 76 / 29 / 30 / 60 | Pomoćni moduli |
| `templates/index.html`, `static/app.js`, `static/style.css` | 177 / 983 / 629 | Frontend |
| `start.sh` | 36 | Pokretanje jednom naredbom |

Obavezan raspored mapa: `index.html` u `templates/`, `style.css` i `app.js` u `static/`, ostalo izravno u korijenu. Datoteke koje nastaju u radu: `grocery.db`, `users.db`, `users.secret_key`, `data/`, `enrichment/`, `grocery.refreshing.db`.

### B3. Izvori podataka

**cijene.dev dnevna arhiva.** Popis: `GET https://api.cijene.dev/v0/list` → `{"archives":[{date,url,size,updated}]}`. Preuzimanje: `.../v0/archive/YYYY-MM-DD.zip`. Ne traži prijavu ni ključ. Struktura: mapa po lancu s `stores.csv`, `products.csv`, `prices.csv`.

| Datoteka | Stupci |
|---|---|
| `stores.csv` | `store_id, type, address, city, zipcode` (bez koordinata) |
| `products.csv` | `product_id, barcode, name, brand, category, unit, quantity` |
| `prices.csv` | `store_id, product_id, price, unit_price, best_price_30, anchor_price, special_price` |

Osobitosti: `quantity` je tekst tipa `"0.20 kg"`; cijene su **po poslovnici** (isti proizvod u dvije poslovnice istog lanca može imati različitu cijenu); Sparove adrese sadrže interni broj.

**Enrichment** (`github.com/senko/cijene-api`, mapa `enrichment/`): `stores.csv` (koordinate, 798 zapisa) i `products.csv` (pročišćeni katalog, 29.645 proizvoda). Ključ spajanja poslovnica: `(chain_code, code)` = `(lanac, store_id)`.

**Provjera nakon zadnjih izmjena repozitorija (2. 10. 2026.):** `enrichment/*.csv`, `service/routers/v0.py` i `service/db/psql.sql` su bajt-identični onima na kojima je kod razvijen. Promjena u repozitoriju odnosila se na crawler za jedan mali lanac.

**Izmjerena statistika (arhiva 22. 9. 2026.):**

| Veličina | Vrijednost |
|---|---|
| Lanaca / poslovnica | 27 / 984 |
| Poslovnica s koordinatama | 744 (76 %), 240 (24 %) nema |
| `chain_products` / `prices` / `canonical_products` | 366.313 / 8.897.938 / 29.645 |
| EAN-ova iz arhive koji su u katalogu | 181.847 od 344.820 (53 %) |
| Popunjenost polja cijene | `anchor_price` 91,0 %, `best_price_30` 16,4 %, `special_price` 13,5 % |
| Veličina `grocery.db` | ~1,1 GB |

Lanci bez ijedne koordinate (18): Ribola, Stanić, Stridon, Trgovina Krk, Gavranović, Metro, NTL, Roto, Dukat, Vrutak, Boso, Bure, Branka, Žabac, Djelo Vodice, dm, Jadranka trgovina, Lorenco. Oni se ne pojavljuju u rezultatima.

**Licence:** kod `senko/cijene-api` je AGPL-3.0 (ovaj projekt ga ne kopira). `enrichment/products.csv` je **CC BY-NC-SA 4.0 — samo nekomercijalno**. Za `enrichment/stores.csv` licenca nije zasebno navedena. Uvjeti korištenja arhive dopuštaju slobodnu uporabu podataka u vlastitim aplikacijama, bez jamstva točnosti. Nominatim: javna instanca, najviše ~1 zahtjev/s. Projekt nema odabranu licencu (O-01).

### B4. Model podataka

**`grocery.db`** (obnovljiva iz izvora)

| Tablica | Ključ | Stupci |
|---|---|---|
| `stores` | `(chain, store_code)` | `type, address, city, zipcode, lat, lon, phone` |
| `chain_products` | `(chain, product_code)` | `barcode, canonical_ean, name, brand, category, unit, quantity` (tekst) |
| `prices` | `(chain, store_code, product_code)` | `price, unit_price, best_price_30, anchor_price, special_price` |
| `canonical_products` | `ean` | `brand, name, unit, quantity` (broj) |
| `meta` | `key` | `value`; ključevi `archive_date`, `imported_at`, `failed_chains` |
| `fts_canonical` | virtualna FTS5 | `brand, name` (vanjski sadržaj iz `canonical_products`) |
| `fts_chain_products` | virtualna FTS5, **opcionalna** | `name` (vanjski sadržaj iz `chain_products`) |

Sekundarni indeksi (grade se **nakon** uvoza): `chain_products(canonical_ean)`, `chain_products(name)`, `prices(chain, product_code)`, `stores(chain)`. EAN se sprema bez vodećih nula; `canonical_ean` je NULL ako je barkod interni kod ili kraći od 8 znamenki. Jedinice u katalogu: L (9.938), kg (8.839), g (6.548), kom (3.774), ml (404), l (118), m (23), par (1).

**`users.db`** (trajna)

| Tablica | Ključ | Stupci |
|---|---|---|
| `users` | `id` | `email` (UNIQUE, mala slova), `password_hash`, `created_at` |
| `shopping_lists` | `id` | `user_id`→users (CASCADE), `name`, `created_at` |
| `shopping_list_items` | `id` | `list_id`→lists (CASCADE), `query, ean, name, label, position` |
| `remembered_products` | `(user_id, query_norm)` | `ean, name, label, updated_at` |
| `price_alerts` | `id` | `user_id`, `ean, label`, `target_price` (NULL = automatski), `last_checked_price, last_checked_at, created_at` |
| `alert_events` | `id` | `alert_id`, `user_id`, `price, chain, triggered_at, seen` |

`PRAGMA foreign_keys = ON` po svakoj konekciji. Nove tablice se dodaju automatski u postojeću `users.db` pri spajanju (`CREATE TABLE IF NOT EXISTS`).

### B5. Moduli i algoritmi

**Uvoz (`import_data.py`).** Po lancu: validacija da postoje sva tri CSV-a i obavezni stupci prije upisa; neispravan lanac se preskače uz poruku, ostali se uvoze (`try/except` + `rollback` po lancu). Cijene se čitaju `csv.reader`-om s indeksima stupaca, u paketima od 50.000 redaka. `PRAGMA journal_mode=MEMORY, synchronous=OFF` (baza je obnovljiva). Sekundarni indeksi i FTS grade se na kraju. `run_import(..., build_chain_fallback_search=True)`; `False` preskače `fts_chain_products`.

**Pretraga (`product_matcher.py`).** SQLite FTS5. Svaka riječ je citiran termin (`"duka"*`), broj je točan (`"2"`), razmak je AND. Citiranje sprječava injekciju u FTS sintaksu (testirano na unosu s navodnicima). Ako `fts_chain_products` ne postoji, fallback pada na `LIKE` (sporije, ali radi).

**Košarica (`basket.py`).** Cijene stavki s EAN-om dohvaćaju se **jednim** grupnim upitom za sve poslovnice × sve stavke (`WHERE (chain, store_code) IN (VALUES ...) AND canonical_ean IN (...)`). Stavke bez EAN-a i dalje pojedinačno. Izmjereno: 3 stavke u 52 poslovnice ~780 ms → ~3 ms (topli cache).

**Optimizacija (`optimize.py`).** Matrica cijena (poslovnica × stavka) računa se jednim upitom; zatim O(P²·M) petlja po parovima u Pythonu (P ≤ 25).

**Pregled (`browse.py`).** (1) FTS kandidati (pool 3000, tokeni sa znamenkama ne idu u FTS jer su često samo u koloni količine); (2) filtri nad prikazanim nazivom i g/ml; (3) poslovnice u radijusu u privremenu tablicu; (4) jedan JOIN za cijene; (5) minimum po EAN-u, sort po cijeni, `OFFSET/LIMIT`. Uvijek vraća `(lista, ukupno)`, i za prazan rezultat `([], 0)`.

**Alarmi (`alerts.py`).** `check_all_alerts(users_conn, grocery_conn)` prolazi sve alarme svih korisnika i vraća broj novih događaja. Za svaki alarm pamti `last_checked_price` da bi prepoznao *prelazak* praga, a ne svaku provjeru.

**Geokodiranje (`geocoder.py`).** Nominatim, `countrycodes=hr`, timeout 10 s, razlikuje "adresa ne postoji" (`None`) od "servis nedostupan" (`GeocodingError`). Nema cachea.

**Autentikacija.** Lozinka: Werkzeug `generate_password_hash`, najmanje 8 znakova; e-pošta format `x@y.z`, spremljena malim slovima, bez potvrde. Sesija: potpisani kolačić, `HttpOnly`, `SameSite=Lax`, 30 dana, `session.clear()` pri prijavi. Ključ: 32 nasumična bajta u `users.secret_key` (chmod 600). Zaključavanje: 10 neuspjelih pokušaja za isti email u 60 s → HTTP 429 (u memoriji procesa). Svaka operacija nad listom/alarmom provjerava vlasnika u SQL-u; tuđi zapis se ponaša kao nepostojeći (404).

### B6. HTTP API

Sve rute vraćaju JSON osim `GET /`. Greške: `{"error": "poruka"}`. Ukupno 25 ruta.

| Ruta | Metoda | Prijava | Ulaz → izlaz (sažeto) |
|---|---|---|---|
| `/` | GET | ne | HTML |
| `/api/search?q=` | GET | ne | `q` ≤ 100 → `{candidates:[{ean,label,chains_available}], remembered}` |
| `/api/browse` | GET | ne | `q, lat, lon, radius, limit(1–50), offset, min_g, max_g, min_ml, max_ml` → `{results:[{ean,label,price,chain,address,city,distance_km}], count, total, offset}` |
| `/api/geocode` | POST | ne | `{address}` ≤ 300 → `{lat,lon}`; 404 nije pronađena; 502 servis |
| `/api/find` | POST | ne | `{lat,lon,radius,items:[{query,ean\|name,label}]}` (1–25) → `{per_item, all_stores(≤30), all_stores_total, chains_summary, nearest_overall, compromise}` |
| `/api/optimize` | POST | ne | isto + `min_savings` → `{plan\|null, baseline\|null}` |
| `/api/refresh` | POST | **ne** | `?skip_fallback_search=1` → `{ok, failed_chains, alerts_triggered}`; 409 u tijeku; 502 preuzimanje |
| `/api/status` | GET | ne | → `{archive_date, days_old, imported_at, failed_chains, source}`; `source`: `meta` ili `file` (stara baza bez `meta`) |
| `/api/auth/me` · `register` · `login` · `logout` | GET/POST | ne | `{user}` + kolačić; 401, 429 |
| `/api/lists` | GET, POST | da | popis / `{name}` ≤ 100 (limit 50) |
| `/api/lists/<id>` | GET, DELETE | da | lista sa stavkama / brisanje |
| `/api/lists/<id>/items` · `/items/<item_id>` | POST · DELETE | da | dodaj / ukloni stavku |
| `/api/remember` | POST | da | `{query,ean,name,label}` |
| `/api/alerts` | GET, POST | da | popis / `{ean,label,target_price?}` (limit 30) |
| `/api/alerts/<id>` | DELETE | da | brisanje |
| `/api/alerts/events` | GET | da | `?unseen=1` → događaji |
| `/api/alerts/events/mark-seen` | POST | da | `{event_ids?}` (bez = sve) |
| `/api/alerts/check` | POST | da | ručna provjera → `{triggered}` |

Validacija: radijus (0, 50] km; koordinate u rasponu; `min_* ≤ max_*` i nenegativni; prazan upit/lista → 400. Preslikano u `/api/find`: `chains_summary[i] = {chain, min_total, stores_available, nearest:{...,total,missing}}`; `nearest.total` može biti veći od `min_total` jer se cijene razlikuju među poslovnicama istog lanca (sučelje to prikazuje).

### B7. Frontend

Jedna HTML stranica, vanilla JS bez build koraka, CSS s tokenima. Dinamički tekst gradi se pomoćnom funkcijom `el()` (`createElement` + `textContent`); `innerHTML` se koristi samo za pražnjenje kontejnera. Stanje u pregledniku: redovi proizvoda (`rows`), `browseState` (za "učitaj još"), `alertChoice`, `currentUser`. Nema `localStorage`; sve trajno je na serveru.

Redoslijed rezultata: kartice (najbliža, kompromis) → kartica optimizacije (ako je tražena i isplati se) → lanci (klik otkriva najbližu poslovnicu) → sklopljeno "Po pojedinom proizvodu" → sklopljeno "Sve poslovnice". Odabran proizvod u retku: podebljan tekst boje `--accent` i zelena vertikalna crta s desne strane retka. Dugi nazivi se ne režu (dvoredni prikaz `receipt-row-wide`). Vizualni identitet: papir/račun, Space Grotesk + IBM Plex Mono, cijene u monospaceu.

### B8. CLI

| Naredba | Zastavice |
|---|---|
| `cli.py` | `--db --address\|--lat --lon --product(×n) --radius --auto --optimize --min-savings --browse --min-g --max-g --min-ml --max-ml --limit` |
| `update.py` | `--db --work-dir --chains --skip-fallback-search --users-db --skip-alerts` |
| `import_data.py` | `<arhiva> <enrichment> --db --chains --skip-fallback-search` |
| `app.py` | `--db --users-db --host --port` |
| `start.sh` | bez zastavica: provjerava Flask i bazu, otvara preglednik |

### B9. Sigurnost

| Prijetnja | Mjera | Provjereno |
|---|---|---|
| SQL injection | svi upiti parametrizirani | da (`mlijeko' OR '1'='1`) |
| FTS injekcija | citirani termini | da (unos s navodnicima) |
| XSS | `textContent`, nema `innerHTML` s podacima | pregled koda; `<script>` u pretrazi bezopasan |
| Zip-slip | provjera svih zapisa prije raspakiravanja | da |
| Izlaganje mreži | zadano `127.0.0.1`, `debug=False` | da |
| Lozinke | samo hash, min. 8 znakova | da |
| Sesija | potpisani kolačić, HttpOnly, SameSite=Lax, ključ `600` | ključ i prava da; krivotvorenje nije testirano |
| Pogađanje lozinki | 10 pokušaja / 60 s po emailu → 429 | da |
| Pristup tuđim zapisima | vlasnik u svakom SQL upitu | da (liste); alarmi isti obrazac, brisanje testirano kroz sučelje |
| Veličina unosa | upit 100, adresa 300, 25 stavki, 50 lista, 30 alarma, radijus ≤ 50 | da |

**Otvoreni rizici:** nema HTTPS-a (lozinke putuju nešifrirano ako se `--host` promijeni); `/api/refresh` i `/api/geocode` nemaju autentikaciju; `/api/alerts/check` pokreće provjeru za **sve** korisnike (svaki prijavljeni korisnik može je okinuti); odjava je samo brisanje kolačića (nema opoziva na serveru); nema CSRF tokena (oslanja se na SameSite=Lax) ni sigurnosnih zaglavlja; nema promjene/brisanja računa ni oporavka lozinke; adrese idu Nominatimu, a fontovi se učitavaju s Googlea; Flaskov razvojni poslužitelj nije produkcijski.

### B10. Performanse

Izmjereno u razvojnom okruženju (arhiva 22. 9. 2026.):

| Operacija | Rezultat |
|---|---|
| Puni uvoz 8,9 M cijena | 66 s (početno) → 41 s (`csv.reader`, odgođeni indeksi) → 49–81 s s FTS-om (varira s opterećenjem) |
| FTS izgradnja | `fts_canonical` 0,06 s; `fts_chain_products` ~4,5 s |
| Pretraga proizvoda (FTS5) | topla: 1–40 ms; ranije s `LIKE`: do ~5 s |
| `/api/find` (3 stavke) | ~12 ms (topli cache) |
| Pregled po cijeni | `kava` ~40 ms, `mlijeko` ~100 ms, `sir` (577 rezultata) ~380 ms (topli cache) |
| Optimizacija kroz dvije trgovine | ~5 ms (topli cache) |

**Hladni cache.** Prvi upit nad bazom nakon pokretanja ili osvježavanja može trajati do ~14 s (izmjereno), svaki sljedeći milisekunde. Uzrok je učitavanje stranica 1,1 GB datoteke s diska, ne algoritam (plan upita je ispravan, koristi oba indeksa). Na sporijem ili mehaničkom disku efekt je veći. Posljedica: prva provjera alarma i prvo osvježavanje mogu biti vidljivo sporiji.

Osvježavanje je za korisnika sporije nego u razvojnom okruženju (prijavljeno ~5×, uz buku diska). Najvjerojatniji uzrok je izgradnja FTS indeksa (puno sitnih zapisa), što je razlog zastavici "brže". To nisam mogla izmjeriti na njegovom računalu.

### B11. Testiranje

**Automatizirani testovi ne postoje.** Sve je provjereno ručno ili jednokratnim skriptama koje nisu u repozitoriju (R-01).

| Područje | Metoda | Rezultat |
|---|---|---|
| Uvoz | puni uvoz stvarne arhive; kontrolne brojke i cijena | identično kroz sve optimizacije |
| Robusnost uvoza | umjetno pokvaren lanac | preskočen, ostali uvezeni |
| Pretraga | stvarni upiti, redoslijed riječi, prefiks, prazan upit | ispravno |
| Pregled | isključivanje `0.25` / `0,25` / `0.2`, uključivanje broja, ml, g+ml, prazni rezultati, široki upiti | ispravno (zbroj g+ml točan) |
| API | curl na rute, uključujući neispravne ulaze | statusi i poruke kako je specificirano |
| Paritet CLI ↔ web | isti upit | identične brojke |
| Autentikacija i autorizacija | curl s cookie jarom; drugi korisnik | ispravno; 11. neuspjeli pokušaj = 429 |
| Alarmi | obje vrste; ponovna provjera; prelazak praga; nova niža cijena | ispravno, bez dupliranja |
| Sučelje | Playwright + Chromium: košarica, prijava, liste, zapamćeni izbor, optimizacija, pregled (g/ml/isključivanje/učitaj još/sklapanje), alarmi | prolazi, bez JS grešaka |
| Neuspjeh osvježavanja | simuliran | stara baza netaknuta |

**Nije testirano:** uspješan put `fetch_archive.py` / `/api/refresh` iz mog okruženja (domena nedostupna; prema izvješćima korisnika radi kod njega); kuka za provjeru alarma nakon osvježavanja (napisana, sintaksa provjerena, put izvršen samo ručnom provjerom); istodobno osvježavanje (409); Windows/macOS; preglednici osim Chromiuma; mobilni uređaji; čitači zaslona; opterećenje.

---

## 12. Poznata ograničenja i tehnički dug

| ID | Ozbiljnost | Opis |
|---|---|---|
| D-01 | **Visoka** | Samo 53 % proizvoda s EAN-om je u pročišćenom katalogu. Ostali se traže samo ako katalog ne vrati ništa, i tada bez usporedbe među lancima. Proizvod izvan kataloga može biti nevidljiv. Pregled po cijeni koristi isključivo katalog. |
| D-02 | **Visoka** | 240 od 984 poslovnica (24 %) nema koordinate; 18 lanaca ih nema nijednu, pa se nikad ne pojave. 54 koordinate iz enrichmenta (798→744) nisu se spojile (uzrok nije istražen). |
| D-03 | Srednja | Količina po stavci ne postoji u sučelju/API-ju/CLI-ju (uvijek 1), iako `BasketItem.qty` postoji. |
| D-04 | Srednja | Padeži ne rade (`rižu` ≠ `riža`); poredak kandidata je po dostupnosti, ne relevantnosti (`jaja` prvo daje "Mlinci bez jaja"); `--auto` zato pogađa krivo. Pretraga za odabir proizvoda ne gleda količinu. |
| D-05 | Srednja | `special_price` (13,5 %) i `anchor_price` (91 %) se ne koriste; semantika nije potvrđena. `best_price_30` se koristi samo u alarmima. |
| D-06 | Srednja | Zračna linija umjesto puta; trošak puta je jedan fiksni parametar (0,30 €/km). |
| D-07 | Srednja | Osvježavanje: stari `data/` i `enrichment/` brišu se prije potvrđenog preuzimanja; sinkrono, bez prikaza napretka; zaključavanje samo unutar jednog procesa; Windows nepoznat. |
| D-08 | Srednja | Alarmi gledaju cijelu državu, ne radijus; nema kanala obavijesti izvan aplikacije (e-pošta); prva provjera na hladnoj bazi traje do ~14 s. |
| D-09 | Niska | Zastarjeli README (djelomično); `ranking.cheapest` i `all_complete_by_price` bez poziva. |
| D-10 | Niska | `app.py` je monolit; agregacija `/api/find` živi u view funkciji. |
| D-11 | Niska | Brojač pokušaja prijave u memoriji, raste bez čišćenja, gubi se pri restartu. |
| D-12 | Niska | Pristupačnost: redovi lanaca nemaju tipkovničku obradu ni ARIA; nativni `prompt`/`confirm`. |
| D-13 | Niska | Vanjski zahtjev prema Google Fonts (privatnost, offline). |

## 13. Roadmap

Trud: **S** do pola dana, **M** 1–2 dana, **L** 3+ dana.

| ID | Zadatak | Kriterij prihvaćanja | Trud |
|---|---|---|---|
| R-01 | Automatizirani testovi (pytest + mini-arhiva kao fixture; Playwright smoke) | prolaze na čistom checkoutu; pokrivaju tablicu u B11 | M |
| R-02 | Pretraga izvan kataloga (D-01): spojiti kandidate iz kataloga i iz `chain_products` po `canonical_ean` | proizvod u ≥2 lanca s istim EAN-om, a izvan kataloga, nalazi se i ima cijene iz oba | M |
| R-03 | Geokodirati preostale poslovnice (D-02) u trajnu tablicu koja preživljava osvježavanje | ≥ 95 % poslovnica ima koordinate | M |
| R-04 | Količina po stavci (D-03), uključujući liste | 2× mlijeko računa dvostruku cijenu | S–M |
| R-05 | Povijest cijena kroz dane (ostatak Faze 3) u trajnoj bazi, samo za EAN-ove u listama/alarmima, uz retenciju | graf ≥ 30 dana; rast baze izmjeren | L |
| R-06 | Robusnije osvježavanje: preuzimanje u privremenu mapu, pozadinska nit i prikaz napretka | prekid mreže ne uništava cache | M |
| R-07 | Alarmi: opcionalni radijus, kanal obavijesti (e-pošta, traži SMTP), autentikacija ručne provjere | alarm se može vezati za adresu | M |
| R-08 | Cijena po kg/L (`unit_price`) u pregledu i sortiranje po njoj | "najjeftinija kava po kg" | M |
| R-09 | Odabir proizvoda u košarici: padeži, količina u pretrazi | `rižu` pronalazi `riža` | M |
| R-10 | Sigurnost: opoziv sesija, CSRF token, sigurnosna zaglavlja, autentikacija na `/api/refresh`, trajno zaključavanje prijave | vidi B9 | M–L |
| R-11 | Javni hosting: WSGI + HTTPS, sigurnosne kopije `users.db`+ključa, vlastiti geokoder s cacheom | tek ako se odluči za javnu uporabu | L |
| R-12 | Odabrati licencu projekta; po potrebi zamijeniti `enrichment/products.csv` vlastitim katalogom | odluka zapisana | M |

Preporučeni redoslijed: R-01 → R-02 → R-03 → R-04 → R-06 → R-08; ostalo prema odluci o namjeni (O-03).

## 14. Otvorena pitanja

| ID | Pitanje | Zašto je važno |
|---|---|---|
| O-01 | Koja licenca i je li namjera ikad komercijalna? | `products.csv` je CC BY-NC-SA |
| O-02 | Što točno znače `special_price` i `anchor_price` u izvoru? | ispravnost prikazanih iznosa |
| O-03 | Samo osobno, obitelj/mreža ili javno? | HTTPS, autentikacija osvježavanja, hosting |
| O-04 | Treba li podrška za Windows/macOS? | `start.sh` je za Linux/macOS; ostalo nije testirano |
| O-05 | Je li 0,30 €/km prihvatljiv? | izravno mijenja "kompromis" i optimizaciju |
| O-06 | Treba li obavijest izvan aplikacije? | traži SMTP i potvrdu adrese |

## 15. Operativne upute

**Instalacija:** Python 3 (razvijeno na 3.12), `pip install flask`. Sve ostalo je standardna biblioteka; Playwright je potreban samo za testiranje.

**Prvo punjenje:** `python3 update.py` (preuzima i uvozi). **Pokretanje:** `./start.sh` ili `python3 app.py --db grocery.db` → http://127.0.0.1:5000. Detalji i rješavanje problema: `KORISNICKI_PRIRUCNIK.md`.

**Sigurnosna kopija:** `users.db` i `users.secret_key` zajedno. `grocery.db` ne treba kopirati.

**Česte greške:** `TemplateNotFound: index.html` → krivi raspored mapa. Geokodiranje 403/nedostupno → nema pristupa Nominatimu (u CLI-ju koristiti `--lat/--lon`). Osvježavanje javlja grešku → stara baza je netaknuta, provjeriti internet. Nakon zamjene datoteka ponovno pokrenuti `app.py`.

## Prilog A — Ključne odluke

| Odluka | Razlog |
|---|---|
| SQLite + haversine u Pythonu | ~1.000 poslovnica, jedan korisnik, bez infrastrukture |
| Flask + vanilla JS | jedan jezik za jezgru i web, bez build koraka, ista logika za CLI i web |
| Dnevna arhiva umjesto živog API-ja | bez ključa i dnevnog limita, brzi lokalni upiti |
| Dvije baze | osvježavanje ne smije dirati račune, liste i alarme |
| EAN kao identitet proizvoda | jedini pouzdan ključ među lancima; stabilan kroz dane |
| Liste/alarmi pamte EAN, ne interne šifre | šifre i sadržaj arhive se mijenjaju |
| Apsolutna formula kompromisa | stabilna i objašnjiva; normalizacija po skupu davala je neintuitivne izbore |
| Filtri pregleda nad prikazanim nazivom | što korisnik vidi, to se filtrira (količina nije u FTS-u) |
| `best_price_30` iz izvora za automatski alarm | povijest već postoji, ne čeka se na vlastitu |
| Slušanje samo na localhostu | nema HTTPS-a; osobni alat |

## Prilog B — Greške pronađene testiranjem (za kontekst)

| Greška | Uzrok | Popravak |
|---|---|---|
| `jaja` pogađalo "Sjaja" | podniz umjesto granice riječi | granica riječi (FTS5 token) |
| Poslovnice bez ijedne stavke prikazane kao "najjeftinije" (0,00 €) | zbroj 0 sortiran prvi | sortiranje po broju nedostajućih |
| Cijeli uvoz pao zbog jednog lanca | `KeyError` bez izolacije | validacija stupaca + `try/except` po lancu |
| Klik "prikaži najbližu" bez učinka | CSS `display:flex` nadjačao `hidden` | `[hidden]{display:none}` |
| "Kompromis" birao dalju i skuplju trgovinu | normalizacija po min/max skupa | apsolutna formula |
| Pretraga do ~5 s | `LIKE '%x%'` ne koristi indeks | FTS5 |
| Pregled po cijeni ~30 s | cijene iz cijele države pa odbacivanje | privremena tablica poslovnica + JOIN |
| `/api/browse` vraćao HTTP 500 za upit bez pogodaka | rani `return []` umjesto `([], 0)` | uvijek tuple |
| `-0.25` u pregledu bez učinka | 0.25 postoji samo u koloni količine, FTS je ne indeksira | filtriranje nad prikazanim nazivom |
| Široki upiti ("sir", 856 kandidata) tiho gubili proizvode | pool od 500 | pool 3000 |
| Alarm: sučelje "zamrznuto" nakon dodavanja | prva provjera na hladnoj bazi do ~14 s | alarm se prikaže odmah, provjera poslije uz poruku |
| Preskočen lanac (neispravan CSV) nije se vidio u popisu neuspjelih | `import_chain` je samo ispisao upozorenje i vratio se | vraća `True/False`; preskočeni lanci idu u `failed_chains` i u prikaz |
