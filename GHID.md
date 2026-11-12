# Ghid de instalare – postare automată pe Instagram din Google Drive

**Cum funcționează:** pui pozele cabanei într-un folder din Google Drive (direct de pe telefon). În fiecare zi, la ora stabilită, programul:

1. alege **la întâmplare** o poză din folderul „De postat”
2. scrie descrierea și hashtag-urile cu AI, pe baza pozei
3. o publică pe Instagram
4. o mută în folderul „Postate”, ca să nu se repete

Nu trebuie să numerotezi sau să redenumești nimic. Programul rulează gratuit pe GitHub, deci calculatorul tău nu trebuie să fie pornit.

Configurarea o faci o singură dată și ține cam 45–60 de minute. Îți trebuie 4 conturi gratuite: **GitHub**, **Google Cloud** (cu contul tău Gmail), **Meta for Developers** (cu Facebook) și **Anthropic API** (pentru AI; costă câțiva bani pe lună la o postare pe zi).

---

## Pasul 1 – Folderele din Google Drive

1. În Google Drive fă un folder, de ex. **Instagram cabana**.
2. În el fă două subfoldere, cu numele exact:
   - **De postat**
   - **Postate**
3. Deschide folderul **Instagram cabana** și uită-te în bara de adrese:
   `https://drive.google.com/drive/folders/1AbCdEfGh...`
   Partea de după `folders/` e **ID-ul folderului**. Copiaz-o, îți trebuie la pasul 7.

## Pasul 2 – Cheia Google (ca programul să vadă folderul)

1. Intră pe https://console.cloud.google.com cu contul tău Gmail.
2. Sus, lângă logo, apasă pe selectorul de proiect → **New project** → nume `instagram-cabana` → **Create**.
3. În bara de căutare de sus scrie **Google Drive API** → deschide-l → **Enable**.
4. Meniu (☰) → **IAM & Admin → Service Accounts → Create service account**
   - Name: `instagram-bot` → **Create and continue** → **Done** (rolurile nu sunt necesare).
5. Apasă pe contul creat → tab-ul **Keys → Add key → Create new key → JSON → Create**.
   Se descarcă un fișier `.json`. **Păstrează-l secret**, e ca o parolă.
6. Tot acolo copiază adresa contului, care arată ca
   `instagram-bot@instagram-cabana-12345.iam.gserviceaccount.com`
7. Înapoi în Google Drive: click dreapta pe folderul **Instagram cabana → Share** → lipești adresa de mai sus → rol **Editor** → **Send** (bifa de notificare o poți scoate).

## Pasul 3 – Pune programul pe GitHub

1. Fă-ți cont pe https://github.com
2. **New repository** → nume `instagram-cabana` → **Public** → **Create**.
   (Trebuie să fie public ca Instagram să poată descărca poza pregătită. În el ajung doar pozele deja publicate pe Instagram. Cheile și parolele NU se văd, ele stau separat, la pasul 7.)
3. **Add file → Upload files** și trage înăuntru: `publica.py`, `setari.json`, `requirements.txt`, `.gitignore`, `GHID.md` și folderul `postate`. Apasă **Commit changes**.
4. Fișierul de programare îl creezi separat: **Add file → Create new file**, ca nume scrii exact
   `.github/workflows/postare.yml`
   și lipești în el conținutul fișierului `postare.yml` din arhivă (e în folderul `.github/workflows`). Apasă **Commit changes**.

## Pasul 4 – Completează datele cabanei

Pe GitHub deschide `setari.json` și apasă creionul ✏️:

| Câmp | Ce scrii |
|---|---|
| `nume_cabana` | numele cabanei |
| `locatie` | satul / zona, județul |
| `despre_cabana` | ce are cabana: câte persoane, șemineu, ciubăr, priveliște etc. AI-ul **nu inventează** facilități, folosește doar ce scrii aici |
| `contact` | telefonul pentru rezervări |
| `link_rezervare` | link Booking/Airbnb/site, dacă ai (altfel lasă `""`) |
| `hashtaguri_fixe` | hashtag-uri care apar la fiecare postare |
| `descriere_implicita` | textul folosit dacă AI-ul nu merge într-o zi |

Modifici doar textul dintre ghilimele. Lasă ghilimelele și virgulele cum sunt. La final apasă **Commit changes**.

## Pasul 5 – Tokenul Instagram (Meta for Developers)

Contul de Instagram trebuie să fie **Business** sau **Creator**.

1. https://developers.facebook.com → **My Apps → Create App**.
2. La tipul de utilizare alege opțiunea pentru **Instagram** („Manage messaging & content on Instagram”).
3. În aplicație: **Instagram → API setup with Instagram login**.
4. La **Generate access tokens** → **Add account** → te loghezi cu contul Instagram al cabanei și accepți permisiunile (inclusiv publicarea de conținut).
5. **Generate token** → copiază tokenul (începe de obicei cu `IGAA...`).

## Pasul 6 – Cheia AI (Anthropic) și tokenul GitHub

**Anthropic:** https://console.anthropic.com → cont → adaugi credit (5 $ ajung pentru multe luni) → **API Keys → Create Key** → copiază (`sk-ant-...`).

**Token GitHub** (ca accesul la Instagram să se reînnoiască singur, altfel expiră la 60 de zile):
GitHub → poza de profil → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**

- Expiration: cea mai lungă durată posibilă
- Repository access: **Only select repositories** → `instagram-cabana`
- Permissions → Repository permissions → **Secrets: Read and write**
- **Generate** → copiază (`github_pat_...`)

## Pasul 7 – Pune cheile în GitHub (secrete)

În repository: **Settings → Secrets and variables → Actions → New repository secret**. Creezi 5 secrete:

| Name | Value |
|---|---|
| `IG_ACCESS_TOKEN` | tokenul Instagram (pasul 5) |
| `ANTHROPIC_API_KEY` | cheia Anthropic (pasul 6) |
| `GH_PAT` | tokenul GitHub (pasul 6) |
| `GOOGLE_SERVICE_ACCOUNT` | **tot conținutul** fișierului `.json` de la pasul 2: îl deschizi cu Notepad, Ctrl+A, Ctrl+C și îl lipești |
| `DRIVE_FOLDER_ID` | ID-ul folderului de la pasul 1 |

După asta poți șterge fișierul `.json` de pe calculator.

## Pasul 8 – Test

1. Pune 2–3 poze în **De postat** în Drive.
2. Pe GitHub: tab-ul **Actions → Postare Instagram → Run workflow**.
   Căsuța „Doar test” e bifată: îți arată descrierea, fără să publice.
3. Apasă pe rulare → **posteaza → Publicare** și citește descrierea.
4. Dacă îți place, rulează din nou cu căsuța **debifată**: poza apare pe Instagram și se mută în **Postate** în Drive.

Gata. De acum nu mai intri pe GitHub, doar pui poze în Drive.

---

## Utilizare zilnică

- **Pui poze:** din aplicația Google Drive de pe telefon → folderul **De postat** → **+** → **Încarcă**. Merg JPG, PNG, HEIC (iPhone), WEBP, cu orice nume.
- **Ordinea:** la întâmplare, câte una pe zi.
- **Notă pentru o anumită poză** (ex. „ofertă de Revelion, mai avem 2 nopți libere”): în Drive, la poză → **⋮ → Detalii / Informații despre fișier → Descriere**. AI-ul o include în text. Dacă nu găsești opțiunea pe telefon, merge și un fișier `.txt` cu același nume ca poza.
- **Pozele prea înalte sau prea late** sunt tăiate automat pe centru la formatul acceptat de Instagram (între 4:5 și 1.91:1). Poza originală din Drive rămâne neatinsă.
- **Ce s-a postat:** pozele ajung în **Postate**, iar pe GitHub, în `istoric.csv`, găsești data și descrierea fiecărei postări.
- **Dacă o postare eșuează,** poza rămâne în **De postat** și se încearcă din nou a doua zi. GitHub îți trimite un e-mail când o rulare dă eroare.

## Schimbarea orei

În `.github/workflows/postare.yml`, linia `cron: "7 16 * * *"` înseamnă zilnic la 16:07 **ora UTC**, adică 18:07 iarna și 19:07 vara la noi.

- 10:07 ora României (iarna) → `"7 8 * * *"`
- doar luni, miercuri, vineri la 18:07 → `"7 16 * * 1,3,5"`

GitHub poate porni rularea cu câteva minute întârziere. E normal.

## Bine de știut

- Dacă trec 60 de zile fără nicio postare (de ex. folderul **De postat** rămâne gol), GitHub oprește programarea automată. O repornești din **Actions → Enable workflow**.
- Când folderul **De postat** e gol, programul nu face nimic. Nu se blochează.
