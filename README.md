# grocery-finder — Faza 0 prototip

CLI koji za tvoju adresu i popis proizvoda vraća koje su okolne trgovine
najjeftinije / najbliže / najbolji kompromis, na temelju cijene.dev dnevne
arhive i geolokacija iz [senko/cijene-api](https://github.com/senko/cijene-api).

Testirano na tvojoj stvarnoj arhivi od 22.9.2026: 27 lanaca, ~8,9 milijuna
zapisa o cijenama, 744 poslovnice s poznatom lokacijom.

## 1. Postava

```bash
pip install --break-system-packages requests   # samo ako koristis geocoder.py, inace nije potrebno

# a) tvoja dnevna arhiva - vec imas raspakiranu (npr. iz cijene.dev/archive)
#    ocekuje strukturu: <archive_dir>/<lanac>/{stores,products,prices}.csv

# b) enrichment podaci (geolokacije + pocisceni katalog) - iz cijene-api repozitorija
git clone https://github.com/senko/cijene-api.git
# koristi mapu cijene-api/enrichment/
```

## 2. Import u lokalnu bazu

```bash
python import_data.py /putanja/do/raspakirane/arhive cijene-api/enrichment --db grocery.db
```

Ovo je jednokratan (ili jednom-dnevno) korak. Na cijeloj arhivi (27 lanaca,
~9M cijena) traje otprilike minutu. Rezultat je jedan `grocery.db` SQLite
fajl koji CLI koristi za sve upite.

Opcionalno, samo par lanaca radi bržeg testiranja:

```bash
python import_data.py /putanja/do/arhive cijene-api/enrichment --db grocery.db --chains konzum lidl spar
```

## 3. Pretraga

```bash
python cli.py --db grocery.db --address "Ilica 1, Zagreb" \
    --product "mlijeko" --product "jaja" --product "kruh" --product "maslac"
```

Bez interneta (geocoder zove Nominatim/OpenStreetMap), koordinate možeš dati ručno:

```bash
python cli.py --db grocery.db --lat 45.8131 --lon 15.9776 \
    --product "mlijeko" --product "kruh"
```

Dodaj `--auto` da program sam odabere najvjerojatniji proizvod za svaki upit
umjesto da te pita (korisno za skripte, manje pouzdano — vidi ispod).

Radijus pretrage mijenjaš s `--radius` (u kilometrima, npr. `--radius 2` ili
`--radius 1.5`). Default je 5 km.

Za svaki proizvod posebno ispisuje se gdje je taj proizvod najjeftiniji i
gdje najbliži (neovisno o ostalim proizvodima na popisu), a zatim i puna
lista SVIH poslovnica gdje su svi traženi proizvodi zajedno dostupni,
sortirano od najjeftinije — ne samo jedan "pobjednik".

## Što stvarno radi (testirano na tvojim podacima)

- Import 27 lanaca, ~9M redaka cijena, u ~60 sekundi.
- Geolokacije rade za 744 poslovnice (uglavnom veliki lanci: Konzum, Lidl,
  Spar, Plodine, Tommy, Eurospin, KTC, Studenac...). Manji/regionalni lanci
  (dm, Metro, Ribola, Stanić...) nemaju koordinate u enrichment/stores.csv
  pa se u ovoj verziji ne pojavljuju u rezultatima "poslovnice u blizini".
- Word-boundary pretraga po nazivu (izbjegava npr. "jaja" pogađati riječ
  "Sjaja" unutar naziva čokoladice — ovo je bio stvaran bug koji sam našao
  i popravio tijekom testiranja).
- Transparentan ranking: najjeftinija (samo ako ima SVE stavke), najbliža,
  kompromis — bez skrivenog "AI zna najbolje".

## Poznata ograničenja (namjerno, za Fazu 0)

1. **Hrvatski padeži.** Pretraga je doslovna — "riža" pronalazi proizvod,
   "rižu" ne. Nema stemminga/lematizacije. Rješenje za kasnije: mali rječnik
   čestih namirnica s padežima, ili knjižnica za hrvatsku lematizaciju.
2. **`--auto` bira po broju lanaca, ne po značenju.** Za upit "jaja" auto-mod
   je u testu odabrao "Mlinci bez jaja" prije pravih jaja, jer se ta riječ
   doslovno pojavljuje i jer je dostupnija. Interaktivni mod (bez `--auto`)
   ti pokaže sve kandidate i točna jaja su bila jasno vidljiva u listi — za
   sad je to pouzdanije rješenje od automatskog nagađanja.
3. **Nema perzistencije korisničkog izbora.** Svaki put biraš proizvod
   ispočetka. Faza 2 (login + spremanje) bi to riješila pamćenjem izbora.
4. **Geocoding treba internet** (Nominatim). U izoliranim okruženjima bez
   pristupa mreži koristi `--lat/--lon`.
5. **Manji lanci bez koordinata** neće se pojaviti kao "obližnja trgovina"
   dok im se ne nađe geolokacija (ili doda ručni geocoding njihovih adresa
   iz `stores.csv`, koje adresu imaju, samo ne i lat/lon).

## Licenca — provjeri prije bilo čega komercijalnog

- Kod u [senko/cijene-api](https://github.com/senko/cijene-api) je pod
  **AGPL-3.0**.
- `enrichment/products.csv` (pročišćeni nazivi proizvoda) je izričito pod
  **CC BY-NC-SA 4.0** — znači **nekomercijalna** upotreba, s obavezom
  navođenja izvora i dijeljenja pod istom licencom. Ovaj prototip ga koristi
  samo za lokalnu pretragu, ali ako ovo ikad postane nešto komercijalno,
  taj dio treba ili ukloniti ili zamijeniti vlastitim katalogom.
- Sami podaci o cijenama su javni temeljem Odluke NN 75/2025, ali to ne
  znači automatski da smiješ raditi bilo što s pročišćenim/enrichment
  slojem — ta razlika je bitna.

## Potpuno automatski način (bez ručnog preuzimanja/raspakiravanja)

Ako ne želiš ručno skidati ZIP s cijene.dev/archive niti klonirati
cijene-api repozitorij, jedna komanda radi sve:

```bash
python update.py
```

Ovo:
1. Povlači `enrichment/stores.csv` i `enrichment/products.csv` izravno s
   GitHuba (bez `git clone`-a).
2. Preko `api.cijene.dev/v0/list` nađe najnoviju dostupnu dnevnu arhivu i
   preuzme je (bez registracije/API ključa — URL shema pročitana izravno
   iz izvornog koda servisa, ne pogađana).
3. Uveze sve u `grocery.db`, isto kao `import_data.py`.

Nakon toga samo pokreneš `cli.py` kao inače.

**Iskreno upozorenje:** korak 2 (preuzimanje s `api.cijene.dev`) nisam mogao
uživo testirati u okruženju u kojem sam ovo pisao — ta domena odatle nije
bila dostupna (dobio sam HTTP 403, vjerojatno zbog restrikcije samog
izlaznog sandboxa, ne nužno servisa). Enrichment dio (GitHub) sam testirao
i radi. Prvi put kad pokreneš `update.py` na svom računalu, provjeri izlaz
— ako korak 2 ne prođe, javi mi grešku pa ću prilagoditi kod, a dotad ti
kao siguran fallback ostaje ručni put opisan gore (skini ZIP u browseru,
`import_data.py` s putanjama).

Ako želiš samo osvježiti podatke za sutra (ne mijenjati ništa u kodu),
opet samo pokreneš `python update.py` — prepoznat će već preuzetu arhivu
za isti dan i neće je preuzimati dvaput, a za novi dan će automatski
povući najnoviju.

## Provjera kvalitete koda (sigurnost, bugovi, robusnost)

Kod je naknadno prošao stvarnu provjeru — ne samo čitanje, nego i testove na
rubnim slučajevima. Nađeno i popravljeno:

1. **Jedan pokvaren lanac u arhivi je rušio cijeli import.** Ako CSV nekog
   lanca nema očekivani stupac (npr. `barcode`), `import_data.py` je prije
   bacao `KeyError` i prekidao uvoz svih preostalih lanaca. Sad se takav
   lanac samo preskoči uz jasnu poruku, a ostatak importa se nastavlja.
   Testirano umjetno pokvarenim lancem umetnutim između dva ispravna.
2. **Mrežna greška u geocoderu je rušila program s Python tracebackom.**
   Ako Nominatim ne odgovori (nema interneta, timeout, blokiran zahtjev),
   sad se ispisuje jasna poruka ("Nominatim nije dostupan...") umjesto
   sirovog stack tracea. Testirano stvarnim pozivom bez pristupa mreži.
3. **Pokretanje CLI-ja bez prethodno napravljene baze** je davalo nejasnu
   `no such table` grešku. Sad se provjerava postoji li baza i ima li
   očekivane tablice, uz jasnu uputu da prvo pokreneš `import_data.py`.
4. **Word-boundary bug u pretrazi** ("jaja" je pogađao "Sjaja") — pronađen
   i popravljen tijekom ranijeg testiranja (vidi commit history razgovora).
5. **Sortiranje rezultata** je prije prikazivalo poslovnice bez ijednog
   pronađenog proizvoda kao "najjeftinije" (jer im je zbroj bio 0,00 €).
   Popravljeno sortiranjem prvo po broju nedostajućih stavki.
6. **SQL injection provjeren ručno** — svi upiti već koriste parametrizirane
   upite (`?` placeholderi), nikad string-konkatenaciju, pa je i namjerno
   zlonamjeran unos (npr. `mlijeko' OR '1'='1`) tretiran kao obična,
   bezopasna tekstualna pretraga. Ovo je bilo ispravno od početka, ali
   vrijedilo je to i stvarno testirati, ne samo pretpostaviti.

### Poznato ograničenje koje NIJE popravljeno (namjerno, izvan dosega Faze 0)

Kad proizvod nema prepoznat EAN (pao je na fallback po točnom nazivu iz
arhive), taj naziv se doslovno traži i u drugim lancima. Kako svaki lanac
piše nazive drukčije, taj fallback u praksi gotovo nikad ne pogodi isti
proizvod u dva različita lanca — realno radi samo unutar lanca koji je i
dao taj točan naziv. Ovo nije bug u smislu da nešto puca ili vraća krive
podatke — vraća "nedostaje" umjesto da nešto pogrešno izmisli — ali znači
da je pokrivenost bez EAN-a namjerno konzervativna, ne lažno optimistična.
Pravo rješenje (fuzzy cross-chain matching s pragovima pouzdanosti) je
tema za Fazu 1+, ne za CLI dokaz koncepta.



## Faza 1 — web frontend (bez logina)

Jednostavno lokalno web sučelje koje poziva **istu** logiku kao `cli.py`
(`basket.py`, `ranking.py`, `product_matcher.py`, `geocoder.py` — ništa
nije duplicirano). Radi u browseru, ali pokreće se na tvom računalu, ne
na claude.ai — jer treba pristup tvojoj lokalnoj `grocery.db` bazi.

```bash
pip install --break-system-packages flask
python app.py --db grocery.db
```

Zatim otvori **http://127.0.0.1:5000** u browseru.

Sučelje: upišeš adresu, dodaš proizvode (pretraga radi dok tipkaš, klikneš
na točan proizvod s liste), postaviš radijus (klizač), i klikneš "Pronađi
najbolju kupnju". Rezultati su isti kao u CLI-ju — po proizvodu posebno
te cijela košarica zajedno — samo u browseru.

**Sigurnosna napomena:** server po defaultu sluša samo na `127.0.0.1`
(samo tvoje računalo), jer alat nema nikakvu prijavu/autentifikaciju. Ne
pokreći ga s `--host 0.0.0.0` na mreži kojoj ne vjeruješ.

Backend (`app.py`) sam testirao uživo — svi API pozivi (`/api/search`,
`/api/geocode`, `/api/find`), sve validacije grešaka (neispravan radijus,
koordinate izvan raspona, prazan popis, previše proizvoda) i XSS-stil unos
u pretrazi (`<script>...`) — sve se ponaša ispravno (vraća čist JSON,
nikad HTML injekciju ni traceback). Frontend (`app.js`) namjerno gradi
DOM ručno (`textContent`, ne `innerHTML` za tekst iz baze) da nema XSS
površine bez obzira koliko su podaci "pouzdani".


## Pregled po cijeni ("kava" → sve varijante, poredano po cijeni)

Novi, drukciji nacin pretrage - umjesto da biraš JEDAN proizvod za košaricu,
dobiješ SVE odgovarajuce proizvode u obližnjim poslovnicama, sortirano od
najjeftinijeg. U web sucelju je to poseban okvir "Pregled po cijeni" ispod
glavne forme; u CLI-ju:

```bash
python cli.py --db grocery.db --address "Ilica 1, Zagreb" --browse "kava -instant" --limit 10
```

- **Isključivanje rijeci:** stavi "-" ispred (npr. `"kava -instant"` trazi
  kavu, ali ne i instant kavu).
- **Raspon težine:** `--min-g` / `--max-g` u CLI-ju, ili polja "od / do g" u
  webu (samo za proizvode s jedinicom g/kg; proizvodi u L/ml/kom se pri
  aktivnom filteru težine izostave).

Implementacija (`browse.py`) prvo nađe sve kandidate FTS5 pretragom, zatim
filtrira po težini (ako je zadano), pa spaja s cijenama SAMO za poslovnice
unutar radijusa (privremena tablica + JOIN) - ranija naivna verzija (spoji
sve kandidate sa svim cijenama u državi, pa odbaci u Pythonu) izmjerena je
na ~30 sekundi za "kava"; ova radi u niskim desetcima milisekundi nakon
prvog (hladnog) poziva.

## Faza 2 — prijava, spremljene liste, zapamćeni izbori

Opcionalno: alat i dalje radi bez prijave, ali prijavljen korisnik dobiva:

- **Više imenovanih lista** ("Tjedna kupovina", "Roštilj"...) — spremiš trenutni
  popis jednim klikom, kasnije ga učitaš i odmah pokreneš pretragu.
- **Zapamćeni izbor proizvoda** — kad jednom kliknеš npr. "Dukat Mlijeko 1L"
  za upit "mlijeko", sljedeći put se za isti upit taj proizvod odabere sam
  (uz link "promijeni"). Ovo rješava problem "koji proizvod korisnik zapravo
  misli" bez pogađanja.

```bash
python app.py --db grocery.db          # users.db se stvori sam pri prvom pokretanju
```

Datoteke koje nastaju uz `app.py`:
- `users.db` — računi, liste, zapamćeni izbori. **Namjerno odvojena od
  `grocery.db`**, koju gumb "Osvježi podatke" briše i ponovno gradi — inače bi
  svako osvježavanje cijena obrisalo i sve račune i liste.
- `users.secret_key` — nasumični ključ kojim se potpisuje sesija (prijava).
  **Ne dijeli ga i ne stavljaj u git.** Ako ga obrišeš, svi će se morati
  ponovno prijaviti (računi ostaju).

Sigurnost (što je napravljeno, a što nije):
- Lozinke se spremaju samo kao hash (scrypt, Werkzeug), nikad kao tekst.
- Svaka lista/stavka provjerava vlasnika — jedan korisnik ne može vidjeti,
  mijenjati ni brisati tuđe liste (testirano).
- Nakon 10 pogrešnih prijava za isti email, prijava se 60 s privremeno
  blokira (HTTP 429).
- Kolačić sesije je `HttpOnly` + `SameSite=Lax` (štiti od većine CSRF napada).
- **Nema HTTPS-a ni potvrde e-pošte.** Namijenjeno lokalnoj uporabi na
  `127.0.0.1`. Ako ga ikad izložiš mreži/internetu, prvo stavi HTTPS
  (reverse proxy) — inače se lozinke šalju nešifrirano.
- Nema "zaboravljene lozinke" (nema slanja e-pošte); račun se ne može
  oporaviti ako se lozinka zaboravi.

- Faza 1: jednostavan web frontend (bez logina) koji poziva istu logiku.
- ~~Faza 2: login + spremanje lista~~ — gotovo, vidi gore ("Faza 2").
- Faza 3: multi-store optimizacija, povijest cijena, price alerts.
