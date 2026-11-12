"""
Postare automată pe Instagram pentru cabana A-frame.

Pozele le pui în Google Drive, în folderul „De postat”.
La fiecare rulare programul:
  1. alege LA ÎNTÂMPLARE o poză din „De postat”
  2. o pregătește pentru Instagram (JPEG, rotire corectă, format 4:5 ... 1.91:1)
  3. scrie descrierea + hashtag-urile cu AI (Claude), pe baza pozei
  4. o publică pe Instagram
  5. o mută în Drive în folderul „Postate” și o trece în istoric.csv

Opțional, o notă pentru AI (ex. "ofertă de Revelion"): în Drive, la poză,
Detalii/Informații → Descriere; sau un fișier .txt cu același nume ca poza.
"""

import base64
import csv
import datetime
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
from PIL import Image, ImageOps

try:  # poze de pe iPhone (.heic)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent
POSTATE = ROOT / "postate"
ISTORIC = ROOT / "istoric.csv"
SETARI = json.loads((ROOT / "setari.json").read_text(encoding="utf-8"))

EXTENSII = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
FOLDER_DE_POSTAT = "De postat"
FOLDER_POSTATE = "Postate"
DRIVE = "https://www.googleapis.com/drive/v3/files"
LUCRU = ROOT / "_lucru"
GRAPH = "https://graph.instagram.com/v26.0"
LATIME_MAX = 1440
RAPORT_MIN, RAPORT_MAX = 0.8, 1.91  # 4:5 (portret) ... 1.91:1 (peisaj)

DOAR_TEST = os.environ.get("DOAR_TEST", "").lower() in ("1", "true", "da")


# ---------------------------------------------------------------- utilitare
def log(mesaj):
    print(mesaj, flush=True)


def git(*args):
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def commit_si_push(mesaj):
    git("add", "-A")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return  # nimic de salvat
    git("commit", "-m", mesaj)
    git("push")


# ---------------------------------------------------------------- Google Drive
class Drive:
    def __init__(self):
        info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
        cred = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive"]
        )
        self.s = AuthorizedSession(cred)
        self.comun = {"supportsAllDrives": "true"}

    def _verifica(self, r):
        if r.status_code >= 400:
            raise RuntimeError(f"Eroare Google Drive ({r.status_code}): {r.text[:300]}")
        return r

    def listeaza(self, folder_id, extra=""):
        fisiere, token = [], None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed=false {extra}",
                "fields": "nextPageToken,files(id,name,mimeType,description)",
                "pageSize": 1000,
                "includeItemsFromAllDrives": "true",
                **self.comun,
            }
            if token:
                params["pageToken"] = token
            d = self._verifica(self.s.get(DRIVE, params=params)).json()
            fisiere += d.get("files", [])
            token = d.get("nextPageToken")
            if not token:
                return fisiere

    def subfolder(self, parinte, nume):
        q = f"and mimeType='application/vnd.google-apps.folder' and name='{nume}'"
        gasite = self.listeaza(parinte, q)
        if not gasite:
            raise RuntimeError(f"Nu găsesc folderul „{nume}” în folderul principal din Drive.")
        return gasite[0]["id"]

    def descarca(self, file_id, dest: Path):
        r = self._verifica(self.s.get(f"{DRIVE}/{file_id}", params={"alt": "media", **self.comun}))
        dest.write_bytes(r.content)
        return dest

    def text(self, file_id):
        r = self._verifica(self.s.get(f"{DRIVE}/{file_id}", params={"alt": "media", **self.comun}))
        return r.content.decode("utf-8", errors="ignore").strip()

    def muta(self, file_id, din, in_):
        self._verifica(
            self.s.patch(
                f"{DRIVE}/{file_id}",
                params={"addParents": in_, "removeParents": din, **self.comun},
            )
        )


def deja_postate():
    if not ISTORIC.exists():
        return set()
    with ISTORIC.open(encoding="utf-8") as f:
        return {r.get("drive_id", "") for r in csv.DictReader(f)}


def alege_poza(fisiere):
    sarite = deja_postate()
    poze = [
        f for f in fisiere
        if Path(f["name"]).suffix.lower() in EXTENSII and f["id"] not in sarite
    ]
    return random.choice(poze) if poze else None


def nota_pentru(poza, fisiere, drive):
    if poza.get("description"):
        return poza["description"].strip()
    stem = Path(poza["name"]).stem.lower()
    for f in fisiere:
        if f["name"].lower() == stem + ".txt":
            return drive.text(f["id"])
    return ""


def pregateste_poza(sursa: Path) -> Path:
    """Convertește în JPEG, rotește după EXIF și taie la un format acceptat de Instagram."""
    img = ImageOps.exif_transpose(Image.open(sursa)).convert("RGB")
    w, h = img.size
    raport = w / h
    if raport < RAPORT_MIN:  # prea înaltă -> tăiem sus/jos
        h_nou = int(w / RAPORT_MIN)
        sus = (h - h_nou) // 2
        img = img.crop((0, sus, w, sus + h_nou))
    elif raport > RAPORT_MAX:  # prea lată -> tăiem stânga/dreapta
        w_nou = int(h * RAPORT_MAX)
        st = (w - w_nou) // 2
        img = img.crop((st, 0, st + w_nou, h))
    if img.width > LATIME_MAX:
        img = img.resize((LATIME_MAX, round(img.height * LATIME_MAX / img.width)), Image.LANCZOS)

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    dest = POSTATE / f"{stamp}_{sursa.stem}.jpg".replace(" ", "_")
    img.save(dest, "JPEG", quality=90, optimize=True)
    return dest


# ---------------------------------------------------------------- istoric
def ultimele_descrieri(n=5):
    if not ISTORIC.exists():
        return []
    with ISTORIC.open(encoding="utf-8") as f:
        randuri = list(csv.DictReader(f))
    return [r["descriere"] for r in randuri[-n:]]


def adauga_in_istoric(poza, drive_id, media_id, descriere):
    nou = not ISTORIC.exists()
    with ISTORIC.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if nou:
            w.writerow(["data", "poza", "drive_id", "media_id", "descriere"])
        w.writerow([datetime.datetime.now().isoformat(timespec="minutes"), poza, drive_id, media_id, descriere])


# ---------------------------------------------------------------- descriere AI
def construieste_descriere(text, hashtaguri):
    fixe = SETARI.get("hashtaguri_fixe", [])
    toate, vazute = [], set()
    for h in fixe + hashtaguri:
        h = "#" + h.strip().lstrip("#").replace(" ", "")
        if len(h) > 1 and h.lower() not in vazute:
            vazute.add(h.lower())
            toate.append(h)
    toate = toate[:30]  # limita Instagram
    descriere = text.strip() + "\n\n" + " ".join(toate)
    return descriere[:2200]  # limita Instagram


def descriere_implicita():
    return construieste_descriere(SETARI["descriere_implicita"], [])


def genereaza_descriere(jpg: Path, nota: str) -> str:
    cheie = os.environ.get("ANTHROPIC_API_KEY")
    if not cheie:
        log("! ANTHROPIC_API_KEY lipsește – folosesc descrierea implicită.")
        return descriere_implicita()

    anterioare = "\n---\n".join(ultimele_descrieri()) or "(niciuna încă)"
    link = SETARI.get("link_rezervare", "").strip()
    prompt = f"""Scrii descrierea unei postări de Instagram pentru o cabană de închiriat.

Despre cabană:
- Nume: {SETARI['nume_cabana']}
- Locație: {SETARI['locatie']}
- Detalii: {SETARI['despre_cabana']}
- Contact pentru rezervări: {SETARI['contact']}
- Link rezervare: {link or '(nu există)'}

Reguli:
- Limba: {SETARI.get('limba', 'română')}, cu diacritice. Ton: {SETARI.get('ton', 'cald și primitor')}.
- Descrie ce se vede în poză și ce simte un oaspete acolo. Nu inventa facilități care nu apar în detalii.
- 2–4 fraze scurte, maximum 2–3 emoji, apoi un îndemn la rezervare cu datele de contact.
- {'Scrie „Link în bio” (linkurile din descriere nu sunt clicabile pe Instagram).' if link else ''}
- Ține cont de anotimp și de data de azi: {datetime.date.today():%d.%m.%Y}.
- Nu repeta formulările din postările anterioare:
{anterioare}
{('- Notă de la proprietar pentru această poză: ' + nota) if nota else ''}

Răspunde DOAR cu JSON, fără alt text:
{{"text": "descrierea, fără hashtag-uri", "hashtaguri": [{SETARI.get('numar_hashtaguri', 12)} hashtag-uri relevante, mix română/engleză, fără #]}}"""

    imagine = base64.b64encode(jpg.read_bytes()).decode()
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": cheie,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": SETARI.get("model_ai", "claude-sonnet-5-5"),
            "max_tokens": 1000,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": imagine}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        },
        timeout=120,
    )
    if r.status_code != 200:
        log(f"! Eroare AI ({r.status_code}): {r.text[:300]} – folosesc descrierea implicită.")
        return descriere_implicita()

    raspuns = "".join(b.get("text", "") for b in r.json()["content"]).strip()
    try:
        date = json.loads(raspuns[raspuns.find("{"): raspuns.rfind("}") + 1])
        return construieste_descriere(date["text"], date.get("hashtaguri", []))
    except (ValueError, KeyError):
        log("! Răspunsul AI nu a putut fi citit – îl folosesc ca text simplu.")
        return construieste_descriere(raspuns, [])


# ---------------------------------------------------------------- Instagram
def ig(metoda, cale, token, **params):
    r = requests.request(
        metoda, f"{GRAPH}/{cale}", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60
    )
    date = r.json()
    if r.status_code != 200 or "error" in date:
        raise RuntimeError(f"Eroare Instagram la {cale}: {date}")
    return date


def publica_pe_instagram(url_poza, descriere):
    token = os.environ["IG_ACCESS_TOKEN"]
    cont = ig("GET", "me", token, fields="user_id,username")
    ig_id = cont["user_id"]
    log(f"Cont Instagram: @{cont.get('username')}")

    container = ig("POST", f"{ig_id}/media", token, image_url=url_poza, caption=descriere)["id"]
    for _ in range(30):  # așteptăm până Instagram descarcă și procesează poza
        stare = ig("GET", container, token, fields="status_code").get("status_code")
        if stare == "FINISHED":
            break
        if stare == "ERROR":
            raise RuntimeError("Instagram nu a putut procesa poza.")
        time.sleep(4)
    return ig("POST", f"{ig_id}/media_publish", token, creation_id=container)["id"]


# ---------------------------------------------------------------- program
def main():
    drive = Drive()
    principal = os.environ["DRIVE_FOLDER_ID"].strip()
    id_de_postat = drive.subfolder(principal, FOLDER_DE_POSTAT)
    id_postate = drive.subfolder(principal, FOLDER_POSTATE)

    fisiere = drive.listeaza(id_de_postat)
    poza = alege_poza(fisiere)
    if not poza:
        log("Nu mai sunt poze în „De postat”. Nimic de făcut azi.")
        return

    log(f"Poza aleasă: {poza['name']}")
    LUCRU.mkdir(exist_ok=True)
    sursa = drive.descarca(poza["id"], LUCRU / poza["name"].replace("/", "_"))
    jpg = pregateste_poza(sursa)
    descriere = genereaza_descriere(jpg, nota_pentru(poza, fisiere, drive))
    log("\n----- DESCRIERE -----\n" + descriere + "\n---------------------\n")

    if DOAR_TEST:
        jpg.unlink()
        log("Mod TEST: nu s-a publicat nimic.")
        return

    # Instagram descarcă poza de la o adresă publică -> o urcăm întâi în repository
    commit_si_push(f"Pregătire postare: {poza['name']}")
    repo = os.environ["GITHUB_REPOSITORY"]
    ramura = os.environ.get("GITHUB_REF_NAME", "main")
    url = f"https://raw.githubusercontent.com/{repo}/{ramura}/postate/{jpg.name}"

    try:
        media_id = publica_pe_instagram(url, descriere)
    except Exception as e:
        log(f"EȘEC: {e}")
        jpg.unlink()
        commit_si_push(f"Postare eșuată, poza rămâne în Drive: {poza['name']}")
        sys.exit(1)

    log(f"Publicat cu succes! ID postare: {media_id}")
    adauga_in_istoric(jpg.name, poza["id"], media_id, descriere)
    commit_si_push(f"Postat pe Instagram: {poza['name']}")
    try:
        drive.muta(poza["id"], id_de_postat, id_postate)
        log("Poza a fost mutată în „Postate” în Drive.")
    except Exception as e:  # e trecută în istoric, deci nu se va posta de două ori
        log(f"! Nu am putut muta poza în „Postate”: {e}")


if __name__ == "__main__":
    main()
