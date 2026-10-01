# grocery-finder — tehnička specifikacija

| | |
|---|---|
| Datum dokumenta | 28. 9. 2026. (ažurirano istog dana: FTS5 pretraga, sortiranje po blizini) |
| Stanje koda | Faze 0, 1 i 2 završene; Faza 3 i dalje nije započeta |
| Referentni skup podataka | cijene.dev dnevna arhiva od 22. 9. 2026. |
| Veličina koda | ~3.100 redaka (Python ~1.900, JS ~580, CSS ~510, HTML ~110) |

Dokument opisuje **što je napravljeno**, **kako radi**, **što je stvarno provjereno**, **što je poznato loše** i **što treba napraviti**. Gdje nešto nije testirano ili je nepoznato, to je izričito navedeno.

---

## 0. Sažetak statusa

| Faza | Opseg | Status |
|---|---|---|
| 0 | CLI dokaz koncepta: adresa + popis proizvoda → trgovine, cijene, udaljenosti | Gotovo, testirano na stvarnim podacima |
| 1 | Lokalni web frontend (Flask) bez prijave | Gotovo, testirano (HTTP + Chromium) |
| 1.1 | Automatsko povlačenje podataka (`update.py`, gumb "Osvježi podatke") | Gotovo. Korak preuzimanja s `api.cijene.dev` nisam mogao sam izvesti (domena nedostupna iz mog okruženja, HTTP 403); iz korisnikovih poruka (rezultati za Popovaču, pitanje o trajanju osvježavanja) proizlazi da radi na njegovom računalu, ali ga nisam sam reproducirao |
| 2 | Prijava, spremljene liste, zapamćeni izbor proizvoda | Gotovo, testirano (curl + Chromium end-to-end) |
| 3 | Optimizacija kupnje kroz više trgovina, povijest cijena, upozorenja | **Nije započeto** |
| 4 | AI sloj (slobodan tekst → popis, recepti) | **Nije započeto** |

**Pet najvažnijih otvorenih stvari** (detalji u odjeljku 13):

1. **Nema automatiziranih testova.** Sve provjere bile su ručne/ad hoc skripte.
2. **Pretraga ne vidi ~polovicu proizvoda s EAN-om** (proizvodi izvan pročišćenog kataloga nalaze se samo kad katalog nema nijedan pogodak, i tada bez usporedbe među lancima). Vidi D-01.
3. **24 % poslovnica (240 od 984) nema koordinate**, uključujući cijele lance (Ribola, Stanić, Stridon, Metro, dm...), pa se nikad ne pojave u rezultatima. Vidi D-02.
4. **Količina po stavci** postoji u jezgri (`BasketItem.qty`), ali je nema u sučelju, API-ju ni CLI-ju. Vidi D-03.
5. **Licenca podataka:** `enrichment/products.csv` je CC BY-NC-SA (nekomercijalno). Svaka komercijalna namjera traži zamjenu tog sloja. Vidi odjeljak 3.4.

---

## 1. Cilj i opseg

**Problem.** Korisnik zna što treba kupiti i gdje živi; želi znati u kojoj obližnjoj trgovini je ta košarica najjeftinija i koliko je ta trgovina daleko.

**Osnovni scenarij.**
1. Korisnik unese adresu i radijus pretrage.
2. Doda proizvode (pretraga po dijelu naziva, odabir točnog proizvoda).
3. Dobije: cijenu cijele košarice po lancu (od najjeftinijeg), najbližu poslovnicu tog lanca na zahtjev, najbližu poslovnicu ukupno i "kompromis" cijena/udaljenost, te cijene po pojedinom proizvodu.
4. (Prijavljen) Sprema popis kao imenovanu listu; sustav pamti koji je proizvod odabrao za koji upit.

**Izvan opsega (za sada):** nativna mobilna aplikacija, online naručivanje/dostava, stvarna cesta/pješačka udaljenost (koristi se zračna linija), cijene u stvarnom vremenu (izvor je dnevna arhiva), višekorisnički javni hosting.

**Načela dizajna.**
- CSV je *ulaz*, nikad runtime izvor; sve se pretražuje iz lokalne baze.
- Transparentnost umjesto "AI odlučuje": prikazuju se cijene, udaljenosti i što nedostaje; heuristike imaju eksplicitne parametre.
- Jedna logika za CLI i web (moduli `basket`, `ranking`, `product_matcher`, `geocoder`).
- Korisnički podaci strogo odvojeni od podataka o cijenama (dvije baze).
- Sigurni zadani parametri (slušanje samo na `127.0.0.1`, parametrizirani SQL, DOM bez `innerHTML` za tekst iz baze).

---

## 2. Arhitektura

```
 api.cijene.dev  ──(dnevni ZIP)──►  fetch_archive.py ─┐
 GitHub (raw) enrichment CSV ─────► fetch_enrichment.py ┤
                                                        ▼
                                                 import_data.py
                                                        │
                                                        ▼
                                        grocery.db  (SQLite, ~1,1 GB)
                                        cijene, proizvodi, poslovnice
                                                        ▲
   Preglednik ◄──JSON/HTTP──► app.py (Flask) ──────────┤ čita
        │                        │                     │
        │                        ├─ product_matcher.py │
        │                        ├─ basket.py ─────────┤
        │                        ├─ ranking.py         │
        │                        ├─ geocoder.py ──► Nominatim (OSM)
        │                        └─ auth_db.py ──► users.db (+ users.secret_key)
        │
   cli.py koristi iste module (bez auth_db)
```

**Dvije baze — namjerna odluka.** `grocery.db` se pri osvježavanju podataka potpuno gradi ispočetka u privremenu datoteku i zamjenjuje staru. Da računi/liste žive u istoj bazi, svako osvježavanje cijena obrisalo bi ih. `users.db` se nikad ne dira pri osvježavanju.

### 2.1 Tok zahtjeva `POST /api/find`

1. Validacija (`lat`/`lon` u rasponu, `0 < radius ≤ 50`, 1–25 stavki).
2. Za **svaku stavku posebno**: `calculate_store_options([stavka])` → poslovnice u radijusu koje je imaju → grupiranje po lancu, minimalna cijena unutar lanca → `per_item`.
3. Za **cijelu košaricu**: `calculate_store_options(sve stavke)` → `complete_only` → grupiranje po lancu → `chains_summary` (minimalni ukupni iznos lanca + najbliža konkretna poslovnica tog lanca).
4. `nearest_overall` (najbliža poslovnica, čak i ako joj nešto nedostaje) i `compromise` (vidi 6.4).
5. `all_stores`: prvih 30 poslovnica, sortirano (manje nedostajućih, pa cijena).

### 2.2 Tok osvježavanja podataka (`POST /api/refresh`)

1. Zaključavanje (`threading.Lock`, neblokirajuće; drugi poziv dobije 409).
2. Brisanje `data/` i `enrichment/`, brisanje eventualne privremene baze.
3. Preuzimanje enrichmenta (GitHub raw) i najnovije arhive (`/v0/list` → ZIP, zaštita od zip-slip).
4. `run_import` u **privremenu** bazu (`grocery.refreshing.db`).
5. Tek nakon potpunog uspjeha: atomska zamjena `tmp.replace(grocery.db)`. Ako bilo što ranije pukne, stara baza ostaje netaknuta (provjereno usporedbom MD5 prije/poslije neuspjelog osvježavanja).

---

## 3. Izvori podataka

### 3.1 cijene.dev dnevna arhiva

- Popis: `GET https://api.cijene.dev/v0/list` → `{"archives":[{"date","url","size","updated"}]}`.
- Preuzimanje: `https://api.cijene.dev/v0/archive/YYYY-MM-DD.zip`. Ne traži prijavu ni ključ (shema URL-a pročitana iz izvornog koda servisa, ne pogađana).
- Sadržaj: mapa po lancu, u svakoj `stores.csv`, `products.csv`, `prices.csv`.

| Datoteka | Stupci |
|---|---|
| `stores.csv` | `store_id, type, address, city, zipcode` (**bez koordinata**) |
| `products.csv` | `product_id, barcode, name, brand, category, unit, quantity` |
| `prices.csv` | `store_id, product_id, price, unit_price, best_price_30, anchor_price, special_price` |

Uočene osobitosti podataka:
- `quantity` je tekst tipa `"0.20 kg"`, ne broj.
- `barcode`: većina je EAN-13; ostalo su kraći/duži kodovi ili interni kodovi lanca (`lanac:šifra`).
- Cijene po poslovnici (ne po lancu): isti proizvod u dvije poslovnice istog lanca može imati različitu cijenu istog dana (potvrđeno na Konzumu: 3,94 € vs 4,08 € za istu košaricu).
- Adrese Sparovih poslovnica sadrže interni broj (npr. `"H. Macanovica 29 87064 Spar"`), što se prikazuje korisniku (D-08).
- Popunjenost polja u `prices`: `price` 100 %, `unit_price` 100 %, `anchor_price` 91,0 %, `best_price_30` 16,4 %, `special_price` 13,5 %. **Sustav koristi samo `price`** (D-05).

### 3.2 Enrichment (repozitorij `senko/cijene-api`, mapa `enrichment/`)

| Datoteka | Sadržaj | Upotreba |
|---|---|---|
| `stores.csv` | `id, chain_code, code, type, address, city, zipcode, lat, lon, phone` (798 zapisa s koordinatama) | Koordinate poslovnica. Ključ spajanja: `(chain_code, code)` = `(lanac, store_id)` (provjereno: 183 od 188 Konzumovih poslovnica) |
| `products.csv` | Pročišćeni katalog: barkod → brand, naziv, jedinica, količina (29.645 proizvoda) | "Kanonski" nazivi i cross-chain identitet proizvoda |
| `cities.csv` | ključ → kanonsko ime grada | **Ne koristi se** |

### 3.3 Izmjerena statistika (arhiva 22. 9. 2026.)

| Veličina | Vrijednost |
|---|---|
| Lanaca | 27 |
| Poslovnica | 984 (744 s koordinatama = 76 %) |
| Zapisa `chain_products` | 366.313 (344.820 s valjanim EAN-om) |
| Od toga u pročišćenom katalogu | 181.847 (**53 %** EAN-ova; 47 % nije u katalogu) |
| Zapisa `prices` | 8.897.938 |
| `canonical_products` | 29.645 |
| Veličina `grocery.db` | ~1,1 GB |

Lanci **s** koordinatama: Konzum, Plodine, Spar, Lidl, Tommy, Eurospin, KTC, Studenac, Trgocentar (djelomično). **Bez ijedne koordinate** (18 lanaca): Ribola (61), Stanić (31), Stridon (22), Trgovina Krk (14), Gavranović (10), Metro (10), NTL (10), Roto (9), Dukat (6), Vrutak (4), Boso (3), Bure (3), Branka (2), Žabac (2), Djelo Vodice, dm, Jadranka trgovina, Lorenco (po 1).

### 3.4 Licence i uvjeti (treba pročitati prije bilo čega komercijalnog)

| Izvor | Uvjeti (prema onome što je pročitano) |
|---|---|
| Dnevna arhiva cijene.dev | Uvjeti korištenja: preuzimanje bez registracije; podaci se smiju slobodno koristiti u vlastitim aplikacijama; bez jamstva točnosti. Postoje odvojeni planovi za komercijalno korištenje *API-ja* — za arhivu nije provjereno |
| Kod `senko/cijene-api` | AGPL-3.0. Ovaj projekt **ne kopira** njihov kod (`barcodes.py`, uvoz i dohvat su vlastita implementacija), ali čita njihovu strukturu podataka |
| `enrichment/products.csv` | **CC BY-NC-SA** — samo nekomercijalno, uz navođenje izvora i dijeljenje pod istom licencom. Ovo je jedina komponenta koja izravno blokira komercijalnu uporabu |
| `enrichment/stores.csv` | Licenca nije izričito navedena zasebno (nije potvrđeno je li pod istim uvjetima kao products.csv) |
| Nominatim (OSM) | Javna instanca: najviše ~1 zahtjev/s, obavezan User-Agent, nije za masovnu uporabu |

Projekt sam nema odabranu licencu (O-01).

---

## 4. Struktura repozitorija

| Datoteka | Redaka | Odgovornost |
|---|---|---|
| `app.py` | 510 | Flask aplikacija: rute, sesije, zaključavanje prijave, orkestracija osvježavanja |
| `auth_db.py` | 217 | Shema i operacije nad `users.db`: računi, liste, zapamćeni izbori |
| `import_data.py` | 358 | CSV → SQLite (`run_import`), validacija, izolacija grešaka po lancu |
| `fetch_archive.py` | 116 | Popis i preuzimanje dnevne arhive, sigurno raspakiravanje |
| `fetch_enrichment.py` | 51 | Preuzimanje enrichment CSV-ova s GitHuba |
| `update.py` | 69 | CLI: preuzimanje + uvoz jednom naredbom |
| `product_matcher.py` | 127 | Pretraga proizvoda po riječima (AND, prefiks) |
| `basket.py` | 89 | Cijena košarice po poslovnici u radijusu |
| `ranking.py` | 60 | Najbliža, kompromis (+ dvije nekorištene funkcije, D-09) |
| `geocoder.py` | 76 | Adresa → koordinate (Nominatim) |
| `geo.py` | 13 | Haversine udaljenost |
| `barcodes.py` | 30 | Normalizacija EAN-a |
| `cli.py` | 206 | Terminalsko sučelje |
| `templates/index.html` | 114 | Jedina stranica |
| `static/app.js` | 576 | Cijelo frontend ponašanje |
| `static/style.css` | 508 | Dizajnerski sustav + komponente |
| `README.md` | — | Upute za pokretanje |

Datoteke koje nastaju u radu (ne verzionirati): `grocery.db`, `users.db`, `users.secret_key`, `data/`, `enrichment/`, `grocery.refreshing.db`.

---

## 5. Model podataka

### 5.1 `grocery.db` (potpuno obnovljiva iz izvora)

| Tablica | Ključ | Stupci |
|---|---|---|
| `stores` | `(chain, store_code)` | `type, address, city, zipcode, lat, lon, phone` |
| `chain_products` | `(chain, product_code)` | `barcode` (sirovi), `canonical_ean` (normaliziran ili NULL), `name, brand, category, unit, quantity` (tekst) |
| `prices` | `(chain, store_code, product_code)` | `price, unit_price, best_price_30, anchor_price, special_price` |
| `canonical_products` | `ean` | `brand, name, unit, quantity` (broj) |

Sekundarni indeksi (grade se **nakon** uvoza): `chain_products(canonical_ean)`, `chain_products(name)`, `prices(chain, product_code)`, `stores(chain)`.

Pravila: EAN se sprema bez vodećih nula; `canonical_ean` je NULL ako barkod sadrži `:`, nije samo znamenke ili ima manje od 8 znamenki; `INSERT OR REPLACE` daje idempotentan uvoz unutar iste baze.

### 5.2 `users.db` (trajna, **nikad se ne briše pri osvježavanju**)

| Tablica | Ključ | Stupci |
|---|---|---|
| `users` | `id` | `email` (UNIQUE, lowercase), `password_hash`, `created_at` |
| `shopping_lists` | `id` | `user_id` → users (CASCADE), `name`, `created_at` |
| `shopping_list_items` | `id` | `list_id` → lists (CASCADE), `query`, `ean`, `name`, `label`, `position` |
| `remembered_products` | `(user_id, query_norm)` | `ean, name, label, updated_at` |

Stavke lista i zapamćeni izbori čuvaju **EAN (ili točan naziv kad EAN-a nema)**, a ne interne šifre lanca — jer se `product_code` i sadržaj arhive mijenjaju, a EAN je stabilan.

Pri spajanju: `PRAGMA foreign_keys = ON` po svakoj konekciji (SQLite to inače ne uključuje).

---

## 6. Moduli i algoritmi

### 6.1 Uvoz (`import_data.py`)

- Po lancu: validacija da postoje sva tri CSV-a i obavezni stupci **prije** upisa; inače se lanac preskače uz poruku.
- Jedan pokvaren lanac ne prekida uvoz ostalih (`try/except` + `rollback` po lancu; ispis popisa neuvezenih).
- Cijene: `csv.reader` s indeksima stupaca (ne `DictReader`), paketi od 50.000 redaka, jedna transakcija po lancu.
- `PRAGMA journal_mode=MEMORY`, `synchronous=OFF` (baza je u cijelosti obnovljiva; pad usred uvoza znači ponovni uvoz).
- Sekundarni indeksi grade se jednom na kraju.
- **Izmjereno** (27 lanaca, 8,9 M redaka): 66 s → 41 s. Profiliranje je pokazalo da dominiraju izgradnja Python n-torki i `executemany`; sam `fsync` nije bio usko grlo (PRAGMA-e same dale su tek ~6 s).

### 6.2 Pretraga proizvoda (`product_matcher.search`)

1. Upit se normalizira (mala slova, `,`→`.`, uklanjanje interpunkcije) i razlomi na tokene.
2. **Svi tokeni moraju postojati** (AND), u bilo kojem redoslijedu, u nazivu ili brandu.
3. Riječ (nebroj): mora biti **početak** neke riječi (prefiks) — `duka` pogađa `dukat`, `mlijek` pogađa `mlijeko`; ali `jaja` **ne** pogađa `Sjaja` (ispred tokena ne smije stajati slovo).
4. Broj: mora biti **cijeli broj** (`2` pogađa `2,8%` i `2L`, ali ne `20` ni `12`).
5. SQL `LIKE` je grubi predfilter (pool 300), točna provjera granica riječi radi se u Pythonu.
6. Izvor kandidata: `canonical_products ⋈ chain_products` (poredano po broju lanaca koji ga imaju, padajuće; limit 8). **Samo ako to vrati 0 rezultata**, fallback na `chain_products.name` grupirano po `LOWER(name)` (kandidat bez EAN-a).

Poznati problemi: vidi D-01 (fallback), D-04 (padeži: `rižu` ne pogađa `riža`, a `riž` pogađa), te da je poredak po dostupnosti, ne po relevantnosti (upit `jaja` prvo vraća "Mlinci bez jaja").

### 6.3 Košarica po poslovnici (`basket.calculate_store_options`)

Za svaku poslovnicu **s koordinatama**: haversine udaljenost; ako je izvan radijusa, preskoči; inače za svaku stavku `MIN(price)` po poslovnici — preko EAN-a (`canonical_ean = ?`) ili, bez EAN-a, točnog naziva (`LOWER(name) = LOWER(?)`). Nedostajuće stavke idu u `missing_items`; zbroj `total` uključuje samo pronađene stavke.

- Složenost: (broj poslovnica × broj stavki) SQL upita po pozivu; `/api/find` poziva funkciju (1 + broj stavki) puta, pa se cijene računaju dvaput. **Latencija nije mjerena** za veće košarice (D-10).
- Zbroj s nedostajućim stavkama je **djelomičan** — zato ga rangiranje nikad ne prikazuje kao "najjeftiniji" osim ako `missing_items` nije prazan.

### 6.4 Rangiranje (`ranking.py`)

- `complete_only`: poslovnice gdje su dostupne **sve** stavke.
- `nearest`: najmanja udaljenost (i uz nedostajuće stavke; prikazuje se što nedostaje).
- `best_compromise`: minimum od `total + eur_per_km × distance_km`, zadano `eur_per_km = 0.30` (udaljenost je jednosmjerna zračna). Apsolutna formula je zamijenila prijašnju normalizaciju po min/max skupa, koja je znala preporučiti dalju i skuplju opciju (potvrđeno na stvarnom primjeru Studenac 3,35 € / 2,03 km protiv Spara 2,84 € / 2,31 km). Parametar nije izložen u sučelju (R-12).

### 6.5 Geokodiranje (`geocoder.py`)

Nominatim, `countrycodes=hr`, `limit=1`, timeout 10 s, vlastiti `User-Agent`. Razlikuje "adresa ne postoji" (`None`) od "servis nije dostupan" (`GeocodingError`). Nema cachea (svaka pretraga = jedan poziv).

### 6.6 Autentikacija, liste, zapamćeni izbori

- Lozinka: Werkzeug `generate_password_hash` (zadano scrypt u Werkzeug 3.x), najmanje 8 znakova. E-pošta: format `x@y.z`, spremljeno malim slovima. Nema verifikacije e-pošte.
- Sesija: Flask potpisani kolačić (`user_id`), `HttpOnly`, `SameSite=Lax`, trajanje 30 dana; `session.clear()` prije prijave. Tajni ključ: 32 nasumična bajta, generiran jednom, u `users.secret_key` (chmod 600).
- Zaključavanje: nakon 10 neuspjelih prijava za isti email u 60 s → HTTP 429 (u memoriji procesa, D-11).
- Autorizacija: svaka operacija nad listom provjerava `user_id` vlasnika u SQL-u; tuđa lista se ponaša kao nepostojeća (404). Limit 50 lista po korisniku.
- Zapamćeni izbor: normalizirani upit → `(ean|name, label)`; `/api/search` ga vraća uz kandidate, frontend ga sam odabere uz oznaku "(zapamćeno)" i vezu "promijeni".

---

## 7. HTTP API

Sve rute vraćaju JSON osim `GET /`. Greške: `{"error": "poruka"}` uz odgovarajući status. "Prijava" = potrebna sesija (inače 401).

| Metoda i put | Prijava | Ulaz | Izlaz (200) | Greške |
|---|---|---|---|---|
| `GET /` | ne | — | HTML | — |
| `GET /api/search?q=` | ne (zapamćeni izbor samo ako je prijavljen) | `q` ≤ 100 znakova | `{candidates:[{ean,label,chains_available}], remembered:{ean,name,label}\|null}` | 400 |
| `POST /api/geocode` | ne | `{address}` ≤ 300 | `{lat,lon}` | 400, 404 nije pronađena, 502 servis |
| `POST /api/find` | ne | `{lat,lon,radius,items:[{query,ean\|name}]}` | `{per_item, all_stores(≤30), all_stores_total, chains_summary, nearest_overall, compromise}` | 400 (koordinate, radijus 0–50, 1–25 stavki) |
| `POST /api/refresh` | **ne** (D-12) | — | `{ok, failed_chains}` | 409 u tijeku, 502 preuzimanje, 500 |
| `GET /api/auth/me` | ne | — | `{user:{email}\|null}` | — |
| `POST /api/auth/register` | ne | `{email,password}` | `{user}` + kolačić | 400 |
| `POST /api/auth/login` | ne | `{email,password}` | `{user}` + kolačić | 400, 401, 429 |
| `POST /api/auth/logout` | ne | — | `{ok}` | — |
| `GET /api/lists` | da | — | `[{id,name,created_at,item_count}]` | 401 |
| `POST /api/lists` | da | `{name}` ≤ 100 | `{id,name}` | 400, 401 |
| `GET /api/lists/<id>` | da | — | `{id,name,created_at,items:[{id,query,ean,name,label}]}` | 401, 404 |
| `DELETE /api/lists/<id>` | da | — | `{ok}` | 401, 404 |
| `POST /api/lists/<id>/items` | da | `{query,ean?,name?,label?}` | `{id}` | 400, 401, 404 |
| `DELETE /api/lists/<id>/items/<item_id>` | da | — | `{ok}` | 401, 404 |
| `POST /api/remember` | da | `{query,ean?,name?,label}` | `{ok}` | 400, 401 |

Oblik `chains_summary[i]`: `{chain, min_total, stores_available, nearest:{chain,address,city,distance_km,total,missing}}`. Napomena: `nearest.total` može biti **veći** od `min_total` jer se cijene razlikuju među poslovnicama istog lanca; sučelje to prikazuje, ne skriva.

---

## 8. Frontend

**Tehnologija:** jedna HTML stranica (Jinja predložak), vanilla JS bez build koraka i bez biblioteka, CSS s tokenima. Fontovi (Space Grotesk, IBM Plex Mono) učitavaju se s Google Fonts; bez interneta pada se na sistemske fontove (D-16).

**Stanje u pregledniku:** `rows: Map<id, {text, candidates, selected, debounceTimer}>` (redovi proizvoda) i `currentUser`. Nema `localStorage`; sve trajno je na serveru.

**Ključni tokovi:**
1. *Unos proizvoda:* tipkanje → debounce 300 ms → `/api/search` → popis kandidata → klik odabire. Ako korisnik ne odabere, koristi se prvi kandidat (`collectItems`). Zastarjeli odgovori se odbacuju (`state.text.trim() !== query`).
2. *Pretraga:* `/api/geocode` → `/api/find` → `renderResults`.
3. *Rezultati (redoslijed na stranici):* kartice "najbliža ukupno" i "kompromis" (cijena istaknuta) → lista lanaca za cijelu košaricu (klik na lanac otkriva najbližu poslovnicu) → sklopljeno "Po pojedinom proizvodu" (samo lanac + cijena) → sklopljeno "Sve poslovnice u radijusu" (**sortirano isključivo po udaljenosti od adrese pretrage**, bez obzira na lanac, cijenu ili nedostajuće stavke — namjerna izmjena; ranije je bilo sortirano po broju nedostajućih stavki pa cijeni).
4. *Prijava:* nativni `<dialog>` (prijava/registracija u jednom); nakon prijave prikazuje se panel "Moje liste".
5. *Liste:* spremi trenutni popis kao novu listu (`prompt` za naziv), učitaj (zamjenjuje redove, stavke dolaze unaprijed odabrane), obriši (`confirm`).
6. *Osvježi podatke:* `confirm` → `/api/refresh` → poruka o uspjehu/grešci u zaglavlju.

**Renderiranje i XSS:** dinamički tekst gradi se helperom `el()` (`createElement` + `textContent`). `innerHTML` se koristi isključivo za pražnjenje kontejnera (`= ""`), nikad za umetanje podataka.

**Dizajnerski sustav ("račun / cjenovnik"):** pozadina papir `#EEE7D6`, kartice `#FBF8F0`, tekst `#1E2A1F`, naglasak gorušica `#C98A1E` (cjenovna naljepnica), pozitivno `#2F6B3C`, greška `#A23B2E`. Cijene i udaljenosti u IBM Plex Mono. Signaturni element: redovi s točkastom vodilicom između naziva i cijene. Raspored: jedan stupac, `max-width 640px`, prilagođen uskim zaslonima (`viewport-fit=cover`, safe-area razmaci).

**Poznati nedostaci (D-13):** redovi lanaca imaju `role="button"` i `tabindex`, ali nemaju obradu tipki Enter/Space ni `aria-expanded`; koriste se nativni `prompt`/`confirm`; kontrast boja nije mjeren; nije testirano na stvarnom mobilnom uređaju niti u drugim preglednicima osim Chromiuma.

---

## 9. Sigurnost

| Prijetnja | Mjera | Provjereno |
|---|---|---|
| SQL injection | Svi upiti parametrizirani (`?`); dinamički SQL u pretrazi gradi samo niz `?` placeholdera | Da (unos `mlijeko' OR '1'='1` ponaša se kao obična pretraga) |
| XSS | DOM gradnja preko `textContent`; nema `innerHTML` s podacima | Da za unos `<script>` u pretrazi (bezopasan JSON); DOM put pregledan, ne fuzzan |
| Zip-slip u arhivi | Prije raspakiravanja svaki zapis mora završiti unutar odredišta | Da (normalan ZIP prolazi, `../../` zapis odbijen) |
| Izlaganje mreži | Zadano slušanje samo na `127.0.0.1`; `debug=False` | Da |
| Pohrana lozinki | Samo hash (Werkzeug), min. 8 znakova | Da (prijava ispravna/neispravna) |
| Krađa/krivotvorenje sesije | Potpisani kolačić, `HttpOnly`, `SameSite=Lax`, nasumični ključ u datoteci `600`, `session.clear()` pri prijavi | Ključ i prava provjereni; krivotvorenje nije testirano |
| Pogađanje lozinki | 10 pokušaja/60 s po emailu → 429 | Da (11. pokušaj vraća 429) |
| Pristup tuđim listama | Provjera vlasnika u svakom SQL upitu | Da (drugi korisnik dobiva `None`/`False`/404) |
| Zlouporaba veličinom unosa | Ograničenja: upit 100, adresa 300, naziv liste 100, label 200, 25 stavki, 50 lista, radijus ≤ 50 km | Da (26 stavki odbijeno) |
| Preuzimanje podataka | Samo `https`, timeouti, provjera da odgovor nije prazan/valjan ZIP/JSON | Da (osim uspješnog puta s `api.cijene.dev`) |

**Otvoreni sigurnosni rizici:**
- **Nema HTTPS-a** — lozinke putuju nešifrirano ako se `--host` promijeni na nešto različito od `127.0.0.1`.
- **`/api/refresh` i `/api/geocode` nemaju autentikaciju** (D-12): svatko s pristupom serveru može pokrenuti brisanje i ponovno gradnju baze ili slati zahtjeve Nominatimu s IP-a servera.
- **Odjava je samo brisanje kolačića na klijentu** (stateless sesija): ukradeni kolačić vrijedi do isteka (30 dana). Nema server-side opoziva.
- **Nema CSRF tokena** — oslanja se isključivo na `SameSite=Lax`.
- **Nema sigurnosnih zaglavlja** (CSP, `X-Content-Type-Options`, `Referrer-Policy`).
- **Privatnost:** adrese korisnika šalju se Nominatimu (OSM), a preglednik dohvaća fontove s Googlea.
- Nema promjene/brisanja računa ni oporavka lozinke; zaključavanje prijave živi u memoriji procesa (D-11).
- Flask razvojni poslužitelj nije produkcijski (upozorenje koje ispisuje je točno za javni hosting, a irelevantno za lokalnu uporabu).

---

## 10. Performanse

**Izmjereno** (ovaj sandbox, arhiva 22. 9. 2026.):

| Operacija | Rezultat |
|---|---|
| Puni uvoz 27 lanaca, 8,9 M cijena | 66 s → 52 s (`csv.reader`) → 41 s (odgođeni indeksi) → **49 s** (+ FTS5 izgradnja) |
| Pretraga proizvoda, prije FTS5 (`LIKE`, hladna konekcija) | mlijeko 4944 ms, ko 3922 ms, dukat mlijeko 21 ms (ovisno o OS cacheu stranica) |
| Pretraga proizvoda, poslije FTS5 (stvarni HTTP poziv) | mlijeko 8,7 ms, ko 36 ms, duka mlijek 2 → 2,9 ms |
| Veličina baze | ~1,1 GB |

**Nije izmjereno:** latencija `/api/find` za veće košarice (npr. 25 stavki × radijus 20 km), vrijeme preuzimanja ZIP-a, memorija procesa, ponašanje pod istodobnim zahtjevima. Analitički je poznato da se broj SQL upita u `/api/find` skalira kao *poslovnice u radijusu × stavke × (1 + stavke)* (D-10) pa to treba izmjeriti prije bilo kakvog javnog hostinga.

---

## 11. Testiranje

**Automatizirani testovi: ne postoje.** Sve niže navedeno izvedeno je ručno ili jednokratnim skriptama koje nisu spremljene u repozitorij (R-01).

Što je provjereno i kako:

| Područje | Metoda | Rezultat |
|---|---|---|
| Uvoz | Puni uvoz stvarne arhive; usporedba brojeva redaka i jedne poznate cijene prije/poslije optimizacija | Identični podaci (8.897.938 cijena, 744 poslovnice s koordinatama, cijena 1,69 € za kontrolni proizvod) |
| Robusnost uvoza | Umjetno pokvaren lanac (nedostaje stupac) između dva ispravna | Preskočen s porukom, ostali uvezeni |
| Pretraga | Stvarni upiti (`dukat mlijeko 2`, `duka mlijek 2`, `jaja`, prazan, redoslijed riječi) | Ispravno; regresija `jaja`/`Sjaja` čista |
| Web API | curl na sve rute, uključujući neispravne ulaze | Statusi i poruke kako je specificirano |
| Paritet CLI ↔ web | Isti upit kroz oba | Identične brojke |
| Autentikacija | curl s cookie jarom: registracija, `me`, liste, 401 bez prijave, lockout (11. pokušaj = 429) | Ispravno |
| Autorizacija | Drugi korisnik pokušava čitati/brisati tuđu listu | Odbijeno |
| Sučelje | **Playwright + Chromium** (headless): registracija, odabir proizvoda, spremanje liste, reload (sesija), učitavanje liste, zapamćeni izbor, odjava, kriva lozinka; klik na red lanca (vidljivost prije/poslije/opet sakriveno) | Prolazi, bez JS grešaka u konzoli |
| Neuspjeh osvježavanja | Simuliran (arhiva nedostupna) | Stara baza netaknuta (isti MD5) |
| Sigurno raspakiravanje | Normalan i zlonamjeran ZIP | Ispravno |

**Nije testirano / nepoznato:**
- Uspješan put `fetch_archive.py` i cijelog `/api/refresh` prema `api.cijene.dev` (moja okolina nema pristup; vidi status 1.1).
- Živo geokodiranje iz mog okruženja (403); na korisnikovom računalu očito radi.
- Istodobno osvježavanje (`409`): pokušaj testa nije bio konkluzivan (pozivi se nisu preklopili); logika je standardna `Lock`, ali nije dokazana testom.
- Windows/macOS; zamjena datoteke baze dok je drugi proces drži otvorenom.
- Preglednici osim Chromiuma; stvarni mobilni uređaji; čitači zaslona.
- Opterećenje i dugotrajni rad.

Minimalna verzija Pythona nije utvrđena; razvijeno i testirano na 3.12. Ovisnosti: `flask` (uz Werkzeug, Jinja2); ostalo je standardna biblioteka. Playwright je potreban samo za testove.

---

## 12. Poznata ograničenja i tehnički dug

| ID | Ozbiljnost | Opis |
|---|---|---|
| D-01 | **Visoka** | **Pretraga zaobilazi proizvode izvan kataloga.** Samo 53 % proizvoda s EAN-om nalazi se u pročišćenom katalogu. Ostali se pretražuju *isključivo ako katalog za upit vrati 0 rezultata*, i tada kao kandidat bez EAN-a s točnim poklapanjem naziva — što u praksi radi samo unutar jednog lanca. Posljedice: proizvod izvan kataloga je nevidljiv ako postoji ijedan pogodak u katalogu; cross-chain usporedba za njih ne radi iako EAN postoji. |
| D-02 | **Visoka** | **240 od 984 poslovnica (24 %) nema koordinate**; 18 od 27 lanaca ih nema nijednu (popis u 3.3), pa se nikad ne pojave u rezultatima. Uz to, 54 koordinate iz enrichmenta (798 → 744) nisu se spojile na poslovnice iz arhive (uzrok nije istražen). |
| D-03 | Srednja | **Količina po stavci ne postoji u sučelju/API-ju/CLI-ju** (uvijek 1), iako `BasketItem.qty` postoji i množi cijenu. |
| D-04 | Srednja | Pretraga: padeži ne rade (`rižu` ≠ `riža`); poredak je po dostupnosti u lancima, ne po relevantnosti (npr. `jaja` prvo daje "Mlinci bez jaja"); `--auto` u CLI-ju zato pogađa krivo. |
| D-05 | Srednja | **Neiskorištena polja cijena** (`special_price` 13,5 %, `anchor_price` 91 %, `best_price_30` 16,4 %, `unit_price` 100 %). Semantika (npr. je li `price` već akcijska cijena kad postoji `special_price`) **nije provjerena** u dokumentaciji izvora, pa se koristi samo `price`. |
| D-06 | Srednja | Udaljenost je zračna linija, ne stvarni put; trošak puta je jedan fiksni parametar (0,30 €/km). |
| D-07 | Srednja | **Osvježavanje:** stari `data/` i `enrichment/` brišu se *prije* nego je novo preuzimanje potvrđeno (baza je sigurna, ali sirovi cache nije); sinkrono je bez prikaza napretka (~1 min); zaključavanje vrijedi samo unutar jednog procesa; privremena baza može ostati nakon greške (briše se pri sljedećem pokretanju); Windows ponašanje nepoznato. |
| D-08 | Niska | Adrese Sparovih poslovnica sadrže interni broj (`"... 87064 Spar"`) — prikazuje se kako dolazi iz izvora. |
| D-09 | Niska | Mrtav kod: `ranking.cheapest` i `ranking.all_complete_by_price` nemaju poziva. |
| D-10 | Srednja | Učinkovitost `/api/find`: cijene se računaju dvaput (po stavci i za cijelu košaricu), N×M pojedinačnih SQL upita; **nije mjereno**. Rješenje: jednom izračunati matricu poslovnica × stavki. |
| D-11 | Niska | Brojač neuspjelih prijava je rječnik u memoriji: raste po emailu bez čišćenja, gubi se pri restartu, ne dijeli se među procesima. |
| D-12 | **Visoka** za javni hosting | `/api/refresh` i `/api/geocode` bez autentikacije. |
| D-13 | Srednja | Pristupačnost: nema tipkovničke obrade/ARIA na retcima lanaca; nativni `prompt`/`confirm`. |
| D-14 | Niska | `app.py` je monolit; logika agregacije `/api/find` živi u view funkciji i zato je teško testirati bez HTTP-a. |
| D-15 | Niska | README je djelomično zastario (numeracija, popis sljedećih koraka). |
| D-16 | Niska | Vanjski zahtjev prema Google Fonts (privatnost, offline). |
| D-17 | Niska | Odjava ne poništava sesiju na serveru (vidi 9). |

---

## 13. Što treba napraviti (roadmap)

Procjena truda: **S** ≈ do pola dana, **M** ≈ 1–2 dana, **L** ≈ 3+ dana (za jednog programera koji poznaje kod).

### Milestone A — stabilizacija (prije novih funkcija)

| ID | Zadatak | Kriterij prihvaćanja | Trud |
|---|---|---|---|
| R-00 | ~~Spora pretraga (LIKE bez indeksa)~~ | **Gotovo** — zamijenjeno SQLite FTS5-om, vidi 6.2 i 10 | — |
| R-01 | **Automatizirani testovi** (pytest): mini-arhiva kao fixture (2–3 lanca, ~50 proizvoda); testovi za `normalize_barcode`, `product_matcher`, `basket`, `ranking`, uvoz (uklj. pokvaren CSV), sve API rute i autorizaciju listi; Playwright smoke test sučelja | `pytest` prolazi na čistom checkoutu; pokriva sve retke iz odjeljka 11 koji su sada ručni | M |
| R-02 | **Pretraga izvan kataloga (D-01):** spojiti kandidate iz kataloga i iz `chain_products` grupiranih po `canonical_ean` (naziv birati npr. najčešći među lancima); fallback bez EAN-a samo za proizvode koji EAN stvarno nemaju | Proizvod koji je u ≥2 lanca s istim EAN-om, a nije u katalogu, pojavljuje se u pretrazi i ima cijene iz oba lanca | M |
| R-03 | **Geokodiranje preostalih poslovnica (D-02):** jednokratni batch (~240 adresa, ≈ 4–5 min uz 1 zahtjev/s), rezultat u tablici `store_geo(chain, store_code, lat, lon, source)` koja **preživljava osvježavanje**; istražiti 54 nespojene koordinate; ručni override | ≥ 95 % poslovnica ima koordinate; lanci poput Ribole i Stanića pojavljuju se u rezultatima; osvježavanje ne briše geokodirane koordinate | M |
| R-04 | **Količina po stavci (D-03):** polje u sučelju, API-ju, CLI-ju i spremljenim listama (`shopping_list_items.qty`) | Košarica 2× mlijeko računa dvostruku cijenu; lista pamti količinu | S–M |
| R-05 | **Semantika cijena (D-05):** provjeriti dokumentaciju izvora; ako je primjereno, prikazati akcijsku cijenu i usporedbu s `anchor_price`/`best_price_30` | Odluka zapisana u odjeljku 3; prikaz odgovara dokumentiranoj semantici | S–M |
| R-06 | **Robusnost osvježavanja (D-07):** preuzeti u privremenu mapu pa tek nakon uspjeha zamijeniti; pozadinska nit + `GET /api/refresh/status` s napretkom; čišćenje privremenih datoteka | Prekid mreže usred preuzimanja ne uništava postojeći cache; sučelje prikazuje korake | M |
| R-07 | Čišćenje: ukloniti mrtav kod (D-09), tipkovnica/ARIA na redcima lanaca (D-13), osvježiti README (D-15), premjestiti agregaciju `/api/find` u servisni modul (D-14) | Testovi iz R-01 prolaze; Enter/Space otvara red lanca | S |

### Milestone B — Faza 3 iz izvornog plana

| ID | Zadatak | Opis i kriterij | Trud |
|---|---|---|---|
| R-08 | **Optimizacija kroz više trgovina** | Korisnik bira "najviše N trgovina" (1 ili 2) i "minimalna ušteda" (npr. 5 €). Algoritam: kandidati = najbolje poslovnice po lancu u radijusu (ograničiti na ~30); za svaki par svaku stavku dodijeliti jeftinijoj uz dostupnost; cilj = zbroj cijena + `eur_per_km` × zbroj udaljenosti od kuće; prikazati samo ako je ušteda ≥ prag u odnosu na najbolju jednu trgovinu. Složenost O(S²·M), zanemariva. Prikaz mora navesti što se kupuje gdje. *Prihvaćanje:* na ručno konstruiranom primjeru rezultat se poklapa s brute-force provjerom | M–L |
| R-09 | **Povijest cijena** | Sirovi obujam je ~8,9 M redaka dnevno, pa naivno spremanje svih dana nije izvedivo (~3 milijarde godišnje). Opcije: (a) pohrana po (lanac, EAN) minimalne/medijan cijene dnevno samo za EAN-ove koji se nalaze u korisničkim listama i zapamćenim izborima, uz retenciju (npr. 180 dana); (b) početno koristiti `best_price_30`/`anchor_price` iz izvora. Starije arhive dostupne su preko `/v0/list` za povratno popunjavanje. Grafikon cijene po lancu u sučelju. *Prihvaćanje:* za proizvod s listom prikazan graf ≥ 30 dana; rast baze izmjeren i dokumentiran | L |
| R-10 | **Upozorenja na cijene** | Korisnik postavlja ciljnu cijenu ili "pad ≥ X %" za proizvod. Ovisi o R-09, R-11 i kanalu dostave (unutar aplikacije bez ovisnosti; e-pošta traži SMTP i verifikaciju adrese — R-15) | M–L |
| R-11 | **Automatsko osvježavanje** | Zakazani posao (cron/systemd timer/APScheduler) koji koristi isti postupak kao gumb; zadržavanje zadnjih N dana; jasan zapis uspjeha/neuspjeha | S–M |
| R-12 | **Postavke i adrese** | `eur_per_km` u sučelju; spremljene adrese uz račun (i predmemorija geokodiranja da se ne zove Nominatim za svaku pretragu) | M |

### Milestone C — proizvod i infrastruktura

| ID | Zadatak | Opis | Trud |
|---|---|---|---|
| R-13 | **Generička stavka** | "Najjeftinije mlijeko 1 L bilo koje marke" preko kategorije, količine i `unit_price`; traži pravila normalizacije jedinica (`quantity` je tekst) | L |
| R-14 | **Stvarna udaljenost** | Vrijeme/put (OSRM ili slično) umjesto zračne linije; pješice/automobilom | M |
| R-15 | **Račun i sigurnost** | Promjena i brisanje računa, oporavak lozinke, verifikacija e-pošte (SMTP), server-side opoziv sesija, CSRF token, sigurnosna zaglavlja, autentikacija na `/api/refresh` (D-12), trajno zaključavanje prijave (D-11) | M–L |
| R-16 | **Javni hosting** | Produkcijski WSGI (gunicorn/waitress) iza HTTPS reverse proxyja; Docker; strategija sigurnosnih kopija `users.db` + ključa; vlastiti Nominatim ili plaćeni geokoder s cacheom; PWA manifest + service worker; selidba na Postgres/PostGIS samo ako mjerenja (R-10/D-10) to opravdaju | L |
| R-17 | **Licence i vlastiti katalog** | Odabrati licencu projekta; zamijeniti `enrichment/products.csv` vlastitim katalogom izvedenim iz `chain_products` (EAN + najčešći naziv/brand) ako je namjera komercijalna; provjeriti uvjete arhive za komercijalnu uporabu | M |

### Milestone D — Faza 4 (AI sloj)

| ID | Zadatak | Opis | Trud |
|---|---|---|---|
| R-18 | Slobodan tekst → popis | "2 mlijeka, kruh, 10 jaja" → stavke s količinama; LLM samo predlaže, a korisnik potvrđuje kandidate (zadržati transparentnost) | M |
| R-19 | Recepti/jelovnik → popis | "Trebam za palačinke" → predložene stavke povezane na kandidate iz kataloga | M–L |

**Preporučeni redoslijed:** R-01 → R-02 → R-03 → R-04 → R-05 → R-06/R-07 → R-11 → R-08 → R-12 → R-09 → R-10; Milestone C prema tome hoće li alat ostati osobni ili ići javno (O-03).

---

## 14. Otvorena pitanja (potrebna odluka)

| ID | Pitanje | Zašto je važno |
|---|---|---|
| O-01 | Koja licenca za projekt, i je li namjera ikad komercijalna? | Određuje smije li se koristiti `enrichment/products.csv` (CC BY-NC-SA) i treba li R-17 |
| O-02 | Što točno znače `price`, `special_price`, `anchor_price`, `best_price_30` u izvoru? | Tiče se ispravnosti svih prikazanih iznosa (D-05) |
| O-03 | Tko će koristiti alat: samo autor, obitelj/mreža ili javnost? | Određuje HTTPS, autentikaciju osvježavanja, hosting, Nominatim politiku |
| O-04 | Treba li podrška za Windows/macOS? | Zamjena datoteka, putanje, upute; nije testirano |
| O-05 | Kako prikazati košaricu kojoj nešto nedostaje: sakriti, ili prikazati kao djelomičnu s upozorenjem? | Sada su takve poslovnice odvojene od "potpunih" i vide se u "Sve poslovnice" |
| O-06 | Zadana vrijednost troška puta (0,30 €/km jednosmjerno) — je li prihvatljiva? | Izravno mijenja što je "kompromis" |
| O-07 | Koliko povijesti čuvati i za koje proizvode? | Veličina baze (R-09) |
| O-08 | Treba li verifikacija e-pošte i oporavak lozinke? | Traži SMTP; bez toga zaboravljena lozinka = izgubljen račun |
| O-09 | Kako tretirati različita pakiranja iste marke pri generičkoj stavci? | R-13 |

---

## 15. Operativne upute

**Instalacija:** Python 3 (testirano 3.12), `pip install flask`.

**Raspored mapa (obavezan):** `app.py` i ostali `.py` fajlovi izravno u korijenu projekta; `index.html` u podmapi `templates/`; `style.css` i `app.js` u podmapi `static/`. (Ako `index.html` nije u `templates/`, Flask javlja `TemplateNotFound: index.html` — to se stvarno dogodilo pri prvom pokretanju.)

**Prvo punjenje podataka:** `python update.py` (preuzima enrichment i najnoviju arhivu i gradi `grocery.db`). Ručna alternativa: `python import_data.py <mapa_arhive> <mapa_enrichment> --db grocery.db`.

**Pokretanje weba:** `python app.py --db grocery.db` → http://127.0.0.1:5000

| Zastavica | Zadano | Značenje |
|---|---|---|
| `app.py --db` | `grocery.db` | Baza cijena |
| `app.py --users-db` | `users.db` | Baza korisnika (ključ sesije se sprema kao `users.secret_key`) |
| `app.py --host` | `127.0.0.1` | Ne mijenjati bez HTTPS-a (odjeljak 9) |
| `app.py --port` | `5000` | |
| `cli.py --address / --lat --lon` | — | Adresa ili koordinate |
| `cli.py --product` | — | Ponovljivo; proizvod za traženje |
| `cli.py --radius` | `5` | Radijus u km, mora biti > 0 |
| `cli.py --auto` | isključeno | Bez pitanja bira kandidata (D-04: nepouzdano) |
| `update.py / import_data.py --chains` | svi | Uvezi samo navedene lance |

**Sigurnosna kopija:** `users.db` **i** `users.secret_key` zajedno (bez ključa se računi zadrže, ali se svi moraju ponovno prijaviti). `grocery.db` ne treba kopirati — obnovljiva je iz izvora.

**Česte greške:**
- `TemplateNotFound: index.html` → pogrešan raspored mapa (gore).
- Geokodiranje javlja HTTP 403/nedostupno → nema pristupa Nominatimu; privremeno koristiti `--lat/--lon` u CLI-ju.
- "Osvježi podatke" javlja grešku preuzimanja → stara baza ostaje netaknuta; provjeriti internet i pokušati ponovno.
- Nakon zamjene datoteka ponovno pokrenuti `app.py`.

---

## Prilog A — Log arhitekturalnih odluka

| Odluka | Razlog | Odbačena alternativa |
|---|---|---|
| SQLite + haversine u Pythonu | ~1.000 poslovnica, jedan korisnik, nula infrastrukture | Postgres + PostGIS (razmotriti tek uz mjerenja/više korisnika) |
| Flask + vanilla JS | Jedan jezik za jezgru i web, bez build koraka, ista logika za CLI i web | Next.js + FastAPI (dva stoga, više dijelova prije dokaza vrijednosti) |
| Dnevna arhiva umjesto živog API-ja | Bez ključa i registracije, bez dnevnog ograničenja zahtjeva, brzi lokalni upiti | Pozivanje `/v1` API-ja po pretrazi |
| Dvije baze | Osvježavanje cijena ne smije dirati račune | Jedna baza s tablicama korisnika |
| EAN kao identitet proizvoda | Jedini pouzdan cross-chain ključ; stabilan kroz dane | Fuzzy poklapanje naziva kao primarno |
| Liste/izbori pamte EAN ili naziv, ne interne šifre | Šifre i sadržaj arhive mijenjaju se svaki dan | Spremanje `(lanac, product_code)` |
| Apsolutna formula kompromisa | Stabilna i objašnjiva; normalizacija po skupu davala je neintuitivne izbore | Min/max normalizacija s težinama |
| Grupiranje po lancu, adresa na klik | Korisnik traži pregled po trgovini, ne 13 identičnih Sparova | Ispis svake poslovnice |
| Slušanje samo na localhostu | Nema HTTPS-a; osobni alat | `0.0.0.0` po defaultu |
| Uvoz u privremenu bazu pa zamjena | Neuspjeh usred uvoza ne smije uništiti radnu bazu | Brisanje pa punjenje na mjestu |

## Prilog B — Greške pronađene testiranjem tijekom razvoja (za kontekst)

| Greška | Uzrok | Popravak |
|---|---|---|
| `jaja` pogađalo "Sjaja" | Podniz umjesto granice riječi | Provjera granice riječi (ispred tokena ne smije biti slovo) |
| Poslovnice bez ijedne pronađene stavke prikazane kao "najjeftinije" (0,00 €) | Zbroj 0 sortiran prvi | Sortiranje prvo po broju nedostajućih |
| Cijeli uvoz pao zbog jednog pokvarenog lanca | `KeyError` bez izolacije | Validacija stupaca + `try/except` po lancu |
| Traceback pri nedostupnom geokoderu / nepostojećoj bazi | Neuhvaćeni izuzeci | Vlastite iznimke i jasne poruke |
| Klik "prikaži najbližu" bez učinka | CSS `.chain-detail{display:flex}` nadjačao atribut `hidden` | `.chain-detail[hidden]{display:none}` (potvrđeno Playwrightom) |
| "Kompromis" birao dalju/skuplju trgovinu | Normalizacija po min/max skupa | Apsolutna formula cijena + km × trošak |
| Uvoz sporiji od očekivanog | `DictReader` na 9 M redaka; indeksi građeni uživo | `csv.reader` + odgođeni indeksi (66 s → 41 s) |
| Zamjenom teksta obrisan naslov u README-u i dio funkcije u `app.js` | Nepažljiva `str_replace` zamjena | Uočeno i ispravljeno prije isporuke |
| `_tokens()` nestala pri refaktoru na FTS5 (`NameError`) | Zamjena velikog bloka koda izbrisala je pomoćnu funkciju bez zamjene | Uočeno testom odmah nakon izmjene, vraćena |
| `INSERT ... VALUES('rebuild')` za FTS5 sporiji nego ručni `INSERT...SELECT` na 366k redaka | `rebuild` interno radi dodatnu provjeru konzistentnosti koja nije potrebna na praznoj, tek stvorenoj tablici | Zamijenjeno ručnim punjenjem (izmjereno: uvoz 60,7 s → 49,4 s) |
