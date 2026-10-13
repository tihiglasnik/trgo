# grocery-finder — korisnički priručnik

Alat za usporedbu cijena u hrvatskim trgovačkim lancima. Upišeš adresu i što trebaš kupiti, a alat ti kaže gdje je najjeftinije, koliko je trgovina daleko i je li se isplati kupiti u dvije trgovine. Radi na tvom računalu, a podaci o cijenama dolaze iz javne dnevne arhive cijena.

Tehnički detalji su u `SPEC.md`. Ovo je priručnik za svakodnevno korištenje.

---

## 1. Što trebaš prije početka

- Računalo s Linuxom (ili macOS-om) i **Python 3**.
- **Internet** (za preuzimanje cijena i za pretvaranje adrese u koordinate).
- Oko **1,5 GB slobodnog prostora** na disku (baza cijena ima ~1,1 GB).
- Preglednik (Chrome, Firefox...).

---

## 2. Kako mora izgledati mapa s programom

Sve datoteke moraju biti u **jednoj mapi**, a tri se moraju nalaziti u podmapama. To je jedina stvar koja se često pogriješi.

```
trgo/                      ← tvoja mapa (naziv je nebitan)
├── app.py
├── alerts.py
├── auth_db.py
├── barcodes.py
├── basket.py
├── browse.py
├── cli.py
├── fetch_archive.py
├── fetch_enrichment.py
├── geo.py
├── geocoder.py
├── import_data.py
├── optimize.py
├── product_matcher.py
├── ranking.py
├── update.py
├── start.sh
├── templates/             ← podmapa
│   └── index.html
└── static/                ← podmapa
    ├── app.js
    └── style.css
```

Ako vidiš grešku `TemplateNotFound: index.html`, to znači da `index.html` nije u podmapi `templates`. Napravi mapu `templates` i premjesti ga unutra. Isto vrijedi za `static` i datoteke `app.js` i `style.css`.

---

## 3. Prvo pokretanje

### 3.1. Instalacija (jednom)

U terminalu, unutar svoje mape:

```
pip install flask
```

Ako Linux odbije s porukom o "externally managed environment", upiši:

```
pip install --break-system-packages flask
```

### 3.2. Preuzimanje podataka (jednom, pa povremeno)

```
python3 update.py
```

Ovo samo preuzme najnovije cijene i složi lokalnu bazu (`grocery.db`). Traje od otprilike jedne do nekoliko minuta, ovisno o računalu i disku. Za to vrijeme ne zatvaraj terminal. Kad završi, ispiše "Gotovo".

Savjet: ako je tvoj disk spor (računalo se čuje i dugo radi), dodaj `--skip-fallback-search`:

```
python3 update.py --skip-fallback-search
```

Osvježavanje je tada brže i tiše, a jedina cijena je da je pretraga proizvoda koji nisu u glavnom katalogu nešto sporija. Vidi poglavlje 9.

### 3.3. Pokretanje aplikacije

Najlakše: jednom naredbom.

```
./start.sh
```

Skripta provjeri je li sve spremljeno, pokrene aplikaciju i otvori preglednik na adresi **http://127.0.0.1:5000**. Ako ti ispiše "Permission denied", jednom upiši `chmod +x start.sh` i pokušaj ponovno.

Ako `start.sh` ne želiš koristiti:

```
python3 app.py --db grocery.db
```

pa u pregledniku otvori http://127.0.0.1:5000.

Za zaustavljanje aplikacije u terminalu pritisni **Ctrl+C**.

Poruka "This is a development server..." koju Flask ispiše pri pokretanju je normalna i za tvoju upotrebu je možeš zanemariti.

---

## 4. Glavni ekran: pronađi najbolju kupnju

### 4.1. Što upisuješ

1. **Adresa.** Na primjer `Ilica 1, Zagreb`. Treba ti internet jer se adresa pretvara u koordinate. Ako adresa nije pronađena, probaj je napisati s gradom i bez dodataka.
2. **Radijus pretrage.** Klizač od 0,5 do 20 km. Gleda se zračna linija od tvoje adrese, ne stvarna cesta.
3. **Proizvodi.** Počni tipkati naziv, na primjer `mlijeko`. Ispod se pojavi popis proizvoda koji odgovaraju. **Klikni na točan proizvod.** Ako ne odabereš ništa, uzet će se prvi s popisa, što nije uvijek ono što misliš (primjer: za `jaja` prvi je "Mlinci bez jaja"). Zato uvijek klikni na pravi.
4. Klikni **"+ dodaj proizvod"** za novi red, a **×** za brisanje reda.

### 4.2. Kako tražiš

Pretraga je pametnija od doslovne:

| Što upišeš | Što se događa |
|---|---|
| `dukat mlijeko` | nađe proizvode koji sadrže **obje** riječi, u bilo kojem redoslijedu |
| `duka mlijek` | dovoljan je početak riječi, nađe `Dukat` i `mlijeko` |
| `dukat mlijeko 2` | broj mora biti cijeli, pa nađe `2,8%` ili `2 L`, ali ne `20` |

Ograničenja: ne razumije padeže (`rižu` neće naći `riža`, upiši `riža`) i u ovom izborniku ne pretražuje količinu (to radi Pregled po cijeni, vidi poglavlje 5).

Kad odabereš proizvod, redak dobije **zelenu vertikalnu crtu s desne strane**, a naziv se prikaže podebljano u žutoj boji. Uz njega je veza **"promijeni"** za odabir drugog.

### 4.3. Što klikneš

Klikni **"Pronađi najbolju kupnju"**. Prije toga možeš uključiti kućicu **"Provjeri isplati li se razdijeliti kupnju na 2 trgovine"** (vidi 4.5).

### 4.4. Kako čitaš rezultate

Rezultati se prikažu u ovom redoslijedu:

**1. Kartice "Najbliža" i "Kompromis"**
- *Najbliža poslovnica ukupno* je trgovina najbliža tvojoj adresi. Ako joj nešto nedostaje, piše što (pravim nazivom proizvoda).
- *Kompromis cijena/udaljenost* je trgovina u kojoj imaš **sve** proizvode i koja je najbolja kad se uračuna put. Alat računa da svaki kilometar do trgovine "košta" 0,30 €. To nije stvarni trošak goriva, nego jednostavna procjena kojom se uspoređuju jeftinija daleka i skuplja bliska trgovina.

**2. (Ako si uključila) kartica "Isplati se razdijeliti kupnju"** (vidi 4.5).

**3. "Cijela košarica, sve u istoj trgovini"**
Popis lanaca od najjeftinijeg, s ukupnom cijenom. **Klikni na lanac** i otkrije se najbliža konkretna poslovnica tog lanca s adresom, udaljenošću i cijenom.

Vrijedi znati: cijene nisu iste u svim poslovnicama istog lanca. Zato cijena na vrhu retka (najniža u lancu) može biti niža od cijene u najbližoj poslovnici. Kad otvoriš redak, vidiš oba broja.

**4. "Po pojedinom proizvodu"** (sklopljeno, otvori klikom)
Za svaki proizvod zasebno, cijena po lancu. Naziv je pravi naziv proizvoda, ne ono što si tipkala.

**5. "Sve poslovnice u radijusu"** (sklopljeno, otvori klikom)
Sve trgovine u radijusu, **poredane po udaljenosti** (najbliža prva), bez obzira na lanac i cijenu. Crveno "Nedostaje" znači da ta trgovina nema neki od tvojih proizvoda. Prikazuje se prvih 30.

### 4.5. Isplati li se kupovati u dvije trgovine?

Ako uključiš kućicu, pojavi se polje **"Prikaži samo ako štedi barem ... EUR"** (zadano 5). Alat provjeri sve parove obližnjih trgovina i traži onaj u kojem svaki proizvod kupuješ tamo gdje je jeftiniji, a ipak dobiješ **sve** s popisa.

Prijedlog se prikaže samo ako je ušteda (uz uračunat put do obje trgovine) barem toliko koliko si zadala. Inače se ne prikaže ništa, jer se ne isplati komplicirati radi nekoliko centi. Ako nisi sigurna koliko tražiti, spusti prag na 0,5 € pa vidi što dobiješ.

Napomena: put se računa kao da je svaka trgovina zaseban izlet od kuće. Ako su dvije trgovine jedna pokraj druge, stvarni put je kraći nego što alat računa.

---

## 5. Pregled po cijeni: sve varijante nekog proizvoda

Drugi okvir na stranici. Namijenjen je pitanju "koliko košta **bilo koja** kava u mojoj blizini i koja je najjeftinija?", a ne odabiru jednog točnog proizvoda.

### 5.1. Korištenje

1. Najprije gore upiši **adresu** i postavi **radijus** (koriste se i ovdje).
2. U polje upiši što tražiš, na primjer `kava`.
3. Klikni **"Traži"** (ili pritisni Enter).

Rezultati su poredani **od najjeftinijeg**. Svaki redak ima naziv proizvoda (puni, ne skraćeni), trgovinu, adresu, udaljenost i cijenu. Prikaže se prvih 15, a gumb **"Učitaj još 15"** dodaje sljedećih 15. Naslov **"Rezultati (257)"** kaže koliko ih ukupno ima. Klikom na njega možeš cijeli popis **sklopiti i raširiti**.

### 5.2. Isključivanje riječi

Ako ne želiš neke proizvode, na dva načina:

- Ispred riječi stavi **minus** u samom upitu: `kava -instant`
- Ili ih upiši u polje **"Isključi riječi (odvojeno zarezom)"**: `instant, 3u1, mlijeko`

Isključuje se **ono što vidiš u rezultatu**, uključujući količinu. To znači da `-0.25` izbacuje sve proizvode od 0,25 kg ili 0,25 L, iako se "0.25" u nazivu pojavljuje samo kao količina.

Brojevi se uspoređuju kao cijeli brojevi: `-0.2` izbacuje proizvode od 0,2, ali **ne** dira one od 0,25.

### 5.3. Samo određeni broj

Možeš i obrnuto, tražiti samo određenu količinu: `kava 0.25` pokaže samo kave od 0,25.

### 5.4. Težina u gramima i volumen u mililitrima

Ispod polja za pretragu su dva para polja:

- **Težina od ... do ... g** (za proizvode koji se mjere u g ili kg, na primjer kava, sir, brašno)
- **Volumen od ... do ... ml** (za proizvode koji se mjere u L ili ml, na primjer sok, mlijeko, ulje)

Pravila:

| Što ispuniš | Što dobiješ |
|---|---|
| Samo težinu | samo proizvode u gramima/kilogramima u tom rasponu |
| Samo volumen | samo proizvode u litrama/mililitrama u tom rasponu |
| Oboje | proizvode koji odgovaraju težini **ili** volumenu (na primjer kava 150–250 g **ili** ledena kava 200–300 ml) |
| Ništa | sve, bez obzira na količinu |

Primjeri: `sok` uz volumen 300–350 ml pokaže pakiranja od 0,33 L. `mlijeko` uz 900–1100 ml pokaže litru mlijeka.

Kad je filter uključen, proizvodi koji se broje na komade ("kom") ili imaju neku drugu mjeru se izostave, jer im težina ni volumen nisu primjenjivi.

### 5.5. Napomene

- Pregled traži samo među proizvodima iz glavnog kataloga (oko polovice svih), pa neki rjeđi proizvod može nedostajati.
- Pretraga je doslovna po riječima: `kava` će uz pravu kavu naći i jogurt s okusom kave, jer i on ima riječ "kava" u nazivu. Takve izbaci s `-jogurt`.
- Ako nema rezultata, dobiješ poruku. Probaj veći radijus ili manje filtera.

---

## 6. Račun: prijava, liste, zapamćeni izbori

Račun **nije obavezan**. Bez njega sve iz poglavlja 4 i 5 radi. Račun donosi tri dodatka.

### 6.1. Registracija i prijava

Gore desno klikni **"Prijava"**. U prozoru imaš dvije kartice, "Prijava" i "Registracija".

- Za novi račun: kartica "Registracija", upiši e-poštu i lozinku (najmanje 8 znakova), klikni "Napravi račun". Odmah si prijavljena.
- Vraćaš se kasnije: kartica "Prijava".
- Za odjavu: gumb **"Odjava"** gore.

Što trebaš znati:
- E-pošta se **ne provjerava** (ne šalje se nikakva poruka), služi samo kao korisničko ime.
- **Ne postoji "zaboravljena lozinka".** Ako je zaboraviš, račun se ne može vratiti. Zapiši je negdje.
- Nakon 10 pogrešnih pokušaja za istu adresu, prijava se na minutu blokira.
- Prijava ostaje zapamćena na ovom pregledniku do 30 dana.

### 6.2. Spremljene liste

Kad si prijavljena, iznad polja "Proizvodi" pojavi se **"Moje liste"**.

- **Spremi trenutni popis kao novu listu:** složi proizvode, klikni "💾 Spremi...", upiši naziv (na primjer "Tjedna kupovina").
- **Učitaj:** odaberi listu iz izbornika i klikni "Učitaj". Proizvodi se vrate već odabrani, pa samo klikneš "Pronađi".
- **Obriši:** briše odabranu listu.

Možeš imati do 50 lista.

### 6.3. Zapamćeni izbor

Kad jednom klikneš na točan proizvod (na primjer "Dukat Mlijeko 1 L" za upit `mlijeko`), alat to zapamti. Sljedeći put kad upišeš `mlijeko`, taj se proizvod **sam odabere** uz oznaku **"(zapamćeno)"**. Ako ti ne odgovara, klikni "promijeni".

---

## 7. Obavijesti o cijenama

Dostupno samo prijavljenima, u kartici **"🔔 Obavijesti o cijenama"**.

### 7.1. Postavljanje alarma

1. U polje upiši proizvod koji pratiš, na primjer `kava franck`.
2. Klikni na točan proizvod s popisa. Prikazuju se samo proizvodi koji se mogu pratiti (imaju bar-kod); ako nema ponuđenih, taj proizvod ne može imati alarm.
3. Izaberi vrstu alarma:
   - **Upiši cijenu u EUR:** alat javlja kad ta cijena **negdje** padne na taj iznos ili ispod njega.
   - **Ostavi prazno:** automatski način. Javlja kad je proizvod u nekoj trgovini **jeftin koliko najjeftinije u zadnjih 30 dana** (ili jeftiniji).
4. Klikni **"Dodaj alarm"**.

Alarm se odmah pojavi na popisu, a alat zatim u pozadini provjeri trenutne cijene. Piše "provjeravam trenutne cijene...", a kad završi, "Alarm dodan i provjeren". Prva provjera nakon pokretanja aplikacije zna potrajati i desetak sekundi, što je normalno.

### 7.2. Kako stižu obavijesti

- Obavijesti se pojave na vrhu kartice kao **"🔔 Novo (broj)"**, s nazivom, cijenom i trgovinom.
- Klikni **"označi sve pročitano"** da nestanu.
- Provjera se pokreće sama **nakon svakog osvježavanja podataka** (vidi poglavlje 8) i kad dodaš novi alarm. Cijene se mijenjaju po danu, pa nova obavijest ima smisla tek nakon novog osvježavanja.
- Isti alarm te **ne obavještava svaki put**. Javi se kad cijena prijeđe prag (ili kad se pojavi nova, niža cijena), a ne dok god ostaje niska.
- Alarm obriši vezom **"obriši"** pokraj njega. Možeš imati do 30 alarma.

### 7.3. Što alarmi ne rade

- Gledaju **cijelu državu**, ne samo tvoj radijus. Obavijest može biti za trgovinu na drugom kraju zemlje.
- **Ne šalju e-poštu ni poruku.** Vidiš ih samo kad otvoriš aplikaciju.
- Automatski način ne radi za svaki lanac jednako: podatak o 30-dnevnom minimumu šalju samo neki lanci (neki gotovo svi proizvodi, a neki, na primjer Lidl, vrlo rijetko). Za proizvode tih lanaca automatski alarm često neće imati što uhvatiti. U tom slučaju koristi ručni cilj.
- Provjeravaju se cijene samo iz zadnjeg preuzetog dana, ne vlastita povijest.

---

## 8. Osvježavanje podataka

Cijene se mijenjaju svaki dan, a alat radi s onim što si preuzela. Prije bitnije kupnje vrijedi osvježiti.

**U aplikaciji:** gore desno gumb **"🔄 Osvježi podatke"**. Pita te za potvrdu, jer briše staru arhivu i skida novu. Poruka ispod gumba prati što se događa. Traje oko minute ili dulje.

**Iz terminala:** `python3 update.py`.

Kućica **"Brže (ali sporija pretraga...)"** pod gumbom radi isto što i `--skip-fallback-search`: osvježavanje je brže i tiše, na uštrb sporije pretrage rijetkih proizvoda. Uključi je ako ti se računalo čuje i dugo radi pri osvježavanju.

Ako osvježavanje ne uspije (nema interneta, izvor nedostupan), **stara baza ostaje netaknuta** i sve dalje radi kao prije. Možeš pokušati kasnije.

Tijekom osvježavanja nemoj gasiti aplikaciju ni računalo.

### Kako vidim koliko su podaci svježi?

Ispod naslova, u zaglavlju stranice, uvijek piše na koji se dan odnose cijene, na primjer:

> Cijene od 22. 9. 2026. (prije 3 dana) · uvezeno 25. 9. 2026. 14:21

- Tekst je **bež** kad su cijene današnje ili jučerašnje.
- Postaje **žut** kad su starije od dva dana, ili kad nešto nije u redu. Tada vrijedi osvježiti.
- Ako piše **"⚠ nisu uvezeni: ..."**, neki lanac pri zadnjem osvježavanju nije uspješno učitan i njegovih cijena nema u rezultatima. Pokušaj ponovno osvježiti.
- Ako piše **"datum cijena nije zabilježen"**, baza je napravljena starijom verzijom ili ručnim uvozom. Jedno osvježavanje to rješava.

Nakon uspješnog osvježavanja ovaj redak se sam obnovi. U terminalu isti podatak vidiš prvim redom kad pokreneš `cli.py`.

---

## 9. Brzina: što je normalno

- **Prva pretraga nakon pokretanja ili osvježavanja može biti spora** (od nekoliko do petnaestak sekundi), a sve nakon toga je gotovo trenutno. Računalo tada prvi put čita veliku datoteku s diska.
- **Osvježavanje** traje otprilike od minute do nekoliko minuta. Na sporijem disku i dulje, uz čujan rad diska.
- Ako ti sve ovo smeta, pomaže **brže osvježavanje** (poglavlje 8) i premještanje projekta na SSD disk.

---

## 10. Česta pitanja

**Zašto neka trgovina uopće ne dolazi u rezultatima?**
Za oko četvrtinu poslovnica (24 %) nemamo koordinate, pa se ne mogu staviti u radijus. To su cijeli manji lanci: Ribola, Stanić, Stridon, Metro, dm i drugi. Ne znači da nemaju proizvod, nego da ne znamo gdje točno stoje.

**Zašto je cijena u najbližoj trgovini veća od one na vrhu liste?**
Cijene se razlikuju između poslovnica istog lanca. Na vrhu je najniža u lancu, a pod "najbliža" je stvarna cijena u najbližoj poslovnici.

**Zašto za `jaja` prvo ponudi "Mlinci bez jaja"?**
Pretraga traži riječ, a ne značenje, i ne zna da je "bez jaja" suprotno od "jaja". Klikni na pravi proizvod s popisa (na primjer "Luneta Jaja").

**Zašto proizvoda kojeg znam nema?**
Pretraga i pregled po cijeni gledaju glavni katalog, koji pokriva otprilike polovicu svih proizvoda. Rijeđi se proizvod može naći samo ako katalog za taj upit uopće ništa ne nađe.

**Jesu li cijene jednake onima na polici?**
Ne jamčimo. Podaci su dnevna javna arhiva cijena i mogu kasniti ili odstupati od police. Za veću kupnju provjeri na licu mjesta.

**Što znači žuta/zelena boja?**
Odabrani proizvod u retku ima žuti podebljani naziv i zelenu crtu s desne strane, a istaknute (najjeftinije) cijene su zelene. Crvena označava da nešto nedostaje.

**Mogu li koristiti na mobitelu?**
Aplikacija radi na računalu na kojem je pokrenuta. Preglednik na istoj mreži bi je mogao otvoriti samo ako se pokrene s `--host 0.0.0.0`, ali to **ne preporučujemo** (vidi poglavlje 11).

---

## 11. Privatnost i sigurnost

- Aplikacija **radi samo na tvom računalu** (adresa 127.0.0.1) i ne vidi se izvana.
- **Što izlazi s računala:** adresa koju upišeš šalje se servisu OpenStreetMap (Nominatim) radi pretvaranja u koordinate; preglednik učitava slova (fontove) s Googlea; podaci o cijenama se preuzimaju iz javne arhive. Računi, liste i alarmi **ostaju na tvom računalu**.
- Lozinke se spremaju samo kao šifrirani otisak, nikad kao tekst.
- **Nemoj** pokretati aplikaciju s `--host 0.0.0.0` na mreži kojoj ne vjeruješ: nema šifriranja veze, pa bi se lozinke prenosile otvoreno.
- Dvije datoteke čuvaj privatnima i nemoj ih nikamo slati niti stavljati na GitHub: **`users.db`** (računi i liste) i **`users.secret_key`** (ključ za prijavu). Za sigurnosnu kopiju računa kopiraj **obje**.
- Datoteku `grocery.db` ne treba kopirati, uvijek se može ponovno složiti naredbom `python3 update.py`.

---

## 12. Rješavanje problema

| Problem | Što napraviti |
|---|---|
| `TemplateNotFound: index.html` | `index.html` nije u podmapi `templates` (poglavlje 2) |
| Stranica je bez stilova ili ništa ne reagira | `style.css` i `app.js` moraju biti u podmapi `static` |
| `Baza 'grocery.db' ne postoji` | najprije pokreni `python3 update.py` |
| `ModuleNotFoundError: flask` | `pip install flask` (poglavlje 3.1) |
| "Geokodiranje nije uspjelo" / adresa nije pronađena | provjeri internet; probaj adresu s gradom, na primjer `Ilica 1, Zagreb` |
| "Osvježavanje je već u tijeku" | pričekaj da završi prethodno |
| Osvježavanje javlja grešku | stara baza je netaknuta; provjeri internet i pokušaj kasnije |
| Stranica se ne mijenja nakon zamjene datoteka | zaustavi aplikaciju (Ctrl+C), pokreni ponovno, pa u pregledniku pritisni Ctrl+F5 |
| Prijava javlja "previše pokušaja" | pričekaj minutu |
| Računalo se čuje i sporo osvježava | koristi brže osvježavanje (poglavlje 8) |

---

## 13. Rječnik

| Pojam | Značenje |
|---|---|
| Lanac | trgovački lanac (Konzum, Lidl, Spar...) |
| Poslovnica | jedna konkretna trgovina na određenoj adresi |
| Radijus | koliko kilometara zračne linije od tvoje adrese se gleda |
| Kompromis | najbolja trgovina kad se uračuna cijena i put (0,30 € po kilometru) |
| Katalog | pročišćeni popis proizvoda s bar-kodom; po njemu se traži |
| EAN / bar-kod | broj na pakiranju koji jednoznačno određuje proizvod, isti je u svim lancima |
| 30-dnevni minimum | najniža cijena koju je proizvod imao u toj trgovini u zadnjih mjesec dana |
| Alarm | tvoja zabilješka "javi mi kad ovaj proizvod pojeftini" |

---

## 14. Kratki podsjetnik

```
Jednom:     pip install flask
            python3 update.py
Svaki put:  ./start.sh                (pa u pregledniku http://127.0.0.1:5000)
Povremeno:  gumb "Osvježi podatke"    (ili python3 update.py)
            datum cijena piše u zaglavlju; žuto = osvježi
```

1. Upiši **adresu**, namjesti **radijus**.
2. Dodaj **proizvode** i **klikni na točan** s popisa.
3. **Pronađi najbolju kupnju**, pa pogledaj najjeftiniji lanac i klikni na njega za adresu.
4. Za "sve kave od najjeftinije" koristi **Pregled po cijeni** (`kava -instant`, uz težinu ili volumen).
5. Za praćenje cijena prijavi se i postavi **alarm**.
