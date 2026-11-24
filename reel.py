"""
Reel automat din poze puse în folderul „Reels” din Google Drive.

Tipar aplicat (din analiza Reels-urilor de cabane):
  - 4-6 cadre a câte 2,5 secunde, tăieturi sec (fără fade), ritm regulat
  - imagini cu zoom lent fără tremur, lumină caldă
  - 3 texte scurte, 5-7 cuvinte, la centru, litere aurii cu umbră, fără bandă
  - muzică din folderul muzica/, dacă există

Mod: setari.json -> "mod_reel": "auto" (publică) sau "manual" (salvează în gata_de_postat).
Rulează separat de postarea zilnică (vezi reel.yml). Folosește funcțiile din publica.py.
"""

import base64
import datetime
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

import publica as p

ROOT = Path(__file__).resolve().parent
LUCRU = ROOT / "_lucru_reel"
MUZICA = ROOT / "muzica"
GATA = ROOT / "gata_de_postat"
FOLDER_REELS = "Reels"
MIN_POZE, MAX_POZE, MAX_CANDIDATI = 3, 6, 8
SECUNDE_PE_CADRU = 2.5       # tipar: cadre de 2-2,5 s
FPS = 30
W, H = 1080, 1920
FACTOR_ZOOM = 3              # imaginea se mărește de 3 ori înainte de zoom -> fără tremur
ZOOM_PAS, ZOOM_MAX = 0.0005, 1.08
TEXTE_PE_CLIP = 3            # tipar: 3 carduri de text
SECUNDE_PE_TEXT = 3.3        # tipar: ~3-3,5 s per card
CULOARE_TEXT = (245, 200, 90, 255)   # auriu, tipar: galben-auriu
FONTURI = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def log(mesaj):
    print(mesaj, flush=True)


# ---------------------------------------------------------------- AI
def ai(content, max_tokens=300):
    cheie = os.environ.get("ANTHROPIC_API_KEY")
    if not cheie:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": cheie, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": p.SETARI.get("model_ai", "claude-sonnet-5-5"),
                  "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": content}]},
            timeout=120,
        )
        if r.status_code != 200:
            log(f"! Eroare AI ({r.status_code}).")
            return None
        return "".join(b.get("text", "") for b in r.json()["content"]).strip()
    except requests.RequestException as e:
        log(f"! Eroare AI: {e}")
        return None


def imagine_b64(cale: Path):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
            "data": base64.b64encode(cale.read_bytes()).decode()}}


def json_din_text(raspuns):
    if not raspuns:
        return None
    try:
        return json.loads(raspuns[raspuns.find("{"): raspuns.rfind("}") + 1])
    except ValueError:
        return None


def alege_pozele_ai(candidati, mici):
    """AI-ul alege și ordonează 4-6 poze. Dacă nu reușește, alege la întâmplare."""
    text = (
        f"Ai {len(candidati)} poze pentru un Reel de Instagram al unei cabane A-frame. "
        "Alege 4 până la 6 poze: bine luminate, fără blur, cu cadre atractive, fără poze aproape identice. "
        "Ordonează-le: întâi un exterior sau o priveliște, apoi interior, apoi detalii (dormitor, baie, "
        "bucătărie, terasă), iar ultima să fie o imagine frumoasă de exterior sau de atmosferă. "
        'Răspunde DOAR cu JSON, fără alt text: {"ordine": [numerele pozelor în ordinea aleasă, începând cu 1]}'
    )
    continut = []
    for i, m in enumerate(mici, 1):
        continut.append({"type": "text", "text": f"Poza {i}:"})
        continut.append(imagine_b64(m))
    continut.append({"type": "text", "text": text})

    date = json_din_text(ai(continut))
    try:
        ordine, vazute = [], set()
        for n in date["ordine"]:
            n = int(n) - 1
            if 0 <= n < len(candidati) and n not in vazute:
                vazute.add(n)
                ordine.append(candidati[n])
        if len(ordine) >= MIN_POZE:
            return ordine[:MAX_POZE]
    except (TypeError, ValueError, KeyError):
        pass
    log("! AI-ul nu a ales poze, aleg la întâmplare.")
    return candidati[:MAX_POZE]


def texte_ecran(cadru0: Path, descriere: str):
    """Trei texte scurte, în tiparul contrast → beneficiu → îndemn. Poate fi lista goală."""
    text = (
        "Pe baza pozei și a descrierii, scrie 3 texte scurte pentru ecranul unui Reel de cabană, "
        "în această ordine: 1) un contrast sau o stare (ex. despre oraș sau liniște), "
        "2) un beneficiu (ce găsești acolo), 3) un îndemn clar la rezervare. "
        "Fiecare text: 3 până la 7 cuvinte, în română cu diacritice, ton cald, fără ghilimele, "
        "fără emoji, fără punct final, fără numere de telefon. "
        'Răspunde DOAR cu JSON: {"texte": ["text1", "text2", "text3"]}\n\n'
        f"Descriere: {descriere[:600]}"
    )
    date = json_din_text(ai([imagine_b64(cadru0), {"type": "text", "text": text}], max_tokens=200))
    try:
        curate = []
        for t in date["texte"]:
            t = str(t).strip().strip('"„”“').strip()
            if t and len(t.split()) <= 7 and "\n" not in t:
                curate.append(t)
        return curate[:TEXTE_PE_CLIP]
    except (TypeError, KeyError):
        return []


# ---------------------------------------------------------------- imagini
def alege_muzica():
    if not MUZICA.exists():
        return None
    piese = [f for f in MUZICA.iterdir() if f.suffix.lower() in {".mp3", ".m4a", ".wav"}]
    return random.choice(piese) if piese else None


def cadru(sursa: Path, dest: Path) -> Path:
    """Taie la 9:16, corectează lumina și culoarea ușor (ton cald), apoi salvează."""
    img = ImageOps.exif_transpose(Image.open(sursa)).convert("RGB")
    img = ImageOps.fit(img, (W, H), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Brightness(img).enhance(1.03)
    img = ImageEnhance.Contrast(img).enhance(1.05)
    img = ImageEnhance.Color(img).enhance(1.10)
    r, g, b = img.split()
    r = r.point(lambda v: min(255, int(v * 1.04)))
    b = b.point(lambda v: int(v * 0.96))
    img = Image.merge("RGB", (r, g, b))
    img.save(dest, "JPEG", quality=92)
    return dest


def font(marime):
    for f in FONTURI:
        if Path(f).exists():
            return ImageFont.truetype(f, marime)
    return ImageFont.load_default(size=marime)


def marime_comuna(texte):
    """Cea mai mare mărime la care TOATE textele încap în ~74% din lățime (aceeași pentru toate)."""
    marime = 72
    while marime > 30:
        f = font(marime)
        if all(
            (lambda bb: bb[2] - bb[0])(ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), t, font=f))
            <= W * 0.74
            for t in texte
        ):
            break
        marime -= 2
    return marime


def deseneaza_text(text: str, marime: int, dest: Path) -> Path:
    """Text auriu cu umbră, la centru, fără bandă de fundal."""
    f = font(marime)
    l, t, r_, b_ = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), text, font=f)
    strat = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(strat)
    x = (W - (r_ - l)) / 2
    y = H * 0.50 - (b_ - t) / 2 - t
    d.text((x + 3, y + 3), text, font=f, fill=(0, 0, 0, 170))   # umbră discretă
    d.text((x, y), text, font=f, fill=CULOARE_TEXT)
    strat.save(dest, "PNG")
    return dest


# ---------------------------------------------------------------- video
def construieste_video(cadre, muzica, texte, iesire: Path) -> Path:
    n = len(cadre)
    durata = round(n * SECUNDE_PE_CADRU, 2)
    cadri = int(SECUNDE_PE_CADRU * FPS)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for c in cadre:
        cmd += ["-framerate", str(FPS), "-loop", "1", "-t", str(SECUNDE_PE_CADRU), "-i", str(c)]
    for t in texte:
        cmd += ["-framerate", str(FPS), "-loop", "1", "-t", str(durata), "-i", str(t)]

    filtre = []
    for i in range(n):  # zoom lent pe imagine mărită, fără tremur
        filtre.append(
            f"[{i}:v]scale={W * FACTOR_ZOOM}:{H * FACTOR_ZOOM},setsar=1,"
            f"zoompan=z='min(zoom+{ZOOM_PAS},{ZOOM_MAX})':d={cadri}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={W}x{H}:fps={FPS},format=yuv420p[v{i}]"
        )
    # tăieturi sec, fără fade
    filtre.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[base]")

    ultimul = "base"
    K = len(texte)
    for k in range(K):  # fiecare text apare pe ecran într-o fereastră de timp
        start = round(k * durata / K + 0.3, 2)
        end = round(min(start + SECUNDE_PE_TEXT, durata - 0.2), 2)
        filtre.append(
            f"[{ultimul}][{n + k}:v]overlay=0:0:enable='between(t,{start},{end})'[o{k}]"
        )
        ultimul = f"o{k}"

    harti = ["-map", f"[{ultimul}]"]
    audio = []
    if muzica:
        idx = n + K
        cmd += ["-stream_loop", "-1", "-i", str(muzica)]
        filtre.append(f"[{idx}:a]atrim=0:{durata},afade=t=out:st={durata - 1.5:.2f}:d=1.5[a]")
        harti += ["-map", "[a]"]
        audio = ["-c:a", "aac", "-b:a", "128k"]

    cmd += ["-filter_complex", ";".join(filtre), *harti,
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", str(durata),
            "-movflags", "+faststart", *audio, str(iesire)]
    subprocess.run(cmd, check=True)
    return iesire


# ---------------------------------------------------------------- Instagram
def publica_reel(url_video, descriere):
    token = os.environ["IG_ACCESS_TOKEN"]
    cont = p.ig("GET", "me", token, fields="user_id,username")
    ig_id = cont["user_id"]
    log(f"Cont Instagram: @{cont.get('username')}")

    container = p.ig(
        "POST", f"{ig_id}/media", token,
        media_type="REELS", video_url=url_video, caption=descriere, share_to_feed="true",
    )["id"]
    for _ in range(72):
        stare = p.ig("GET", container, token, fields="status_code").get("status_code")
        if stare == "FINISHED":
            break
        if stare == "ERROR":
            raise RuntimeError("Instagram nu a putut procesa clipul.")
        time.sleep(5)
    else:
        raise RuntimeError("Clipul nu a fost procesat la timp.")
    return p.ig("POST", f"{ig_id}/media_publish", token, creation_id=container)["id"]


# ---------------------------------------------------------------- program
def main():
    mod = p.SETARI.get("mod_reel", "auto")
    drive = p.Drive()
    principal = os.environ["DRIVE_FOLDER_ID"].strip()
    id_reels = drive.subfolder(principal, FOLDER_REELS)
    id_postate = drive.subfolder(principal, p.FOLDER_POSTATE)

    fisiere = drive.listeaza(id_reels)
    poze = [f for f in fisiere if Path(f["name"]).suffix.lower() in p.EXTENSII]
    if len(poze) < MIN_POZE:
        log(f"Sunt mai puțin de {MIN_POZE} poze în „{FOLDER_REELS}”. Nu fac clipul acum.")
        return
    candidati_drive = random.sample(poze, min(len(poze), MAX_CANDIDATI))

    LUCRU.mkdir(exist_ok=True)
    cadre_cand, mici = [], []
    for i, f in enumerate(candidati_drive):
        src = drive.descarca(f["id"], LUCRU / f"orig_{i}_{f['name']}".replace("/", "_"))
        c = cadru(src, LUCRU / f"cadru_{i}.jpg")
        cadre_cand.append((f, c))
        m = LUCRU / f"mic_{i}.jpg"
        im = Image.open(c)
        im.thumbnail((512, 512))
        im.save(m, "JPEG", quality=80)
        mici.append(m)

    alese_perechi = alege_pozele_ai(cadre_cand, mici)
    alese = [f for f, _ in alese_perechi]
    cadre = [c for _, c in alese_perechi]
    log("Poze alese: " + ", ".join(f["name"] for f in alese))

    note = [x for x in (p.nota_pentru(f, fisiere, drive) for f in alese) if x]
    descriere = p.genereaza_descriere(cadre[0], " ".join(note))
    log("\n----- DESCRIERE -----\n" + descriere + "\n---------------------\n")

    texte = texte_ecran(cadre[0], descriere)
    imagini_text = []
    if texte:
        marime = marime_comuna(texte)
        for k, t in enumerate(texte):
            imagini_text.append(deseneaza_text(t, marime, LUCRU / f"text_{k}.png"))
    if texte:
        log("Texte pe ecran: " + " | ".join(texte))

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    video = construieste_video(cadre, alege_muzica(), imagini_text, LUCRU / f"{stamp}_reel.mp4")
    log(f"Clip creat: {video.name}")

    if p.DOAR_TEST:
        log("Mod TEST: nu s-a salvat și nu s-a publicat nimic.")
        return

    if mod == "manual":
        GATA.mkdir(exist_ok=True)
        (GATA / video.name).write_bytes(video.read_bytes())
        (GATA / (video.stem + "_descriere.txt")).write_text(descriere, encoding="utf-8")
        log(f"Salvat în repository, în „gata_de_postat”: {video.name} și descrierea.")
        p.commit_si_push(f"Reel pregătit manual: {video.name}")
        for f in alese:
            try:
                drive.muta(f["id"], id_reels, id_postate)
            except Exception as e:
                log(f"! Nu am putut muta {f['name']} în „Postate”: {e}")
        p.adauga_in_istoric(video.name, ",".join(f["id"] for f in alese), "manual", descriere)
        p.commit_si_push(f"Istoric reel: {video.name}")
        return

    dest = p.POSTATE / video.name
    dest.write_bytes(video.read_bytes())
    video = dest
    p.commit_si_push(f"Pregătire reel: {video.name}")
    repo = os.environ["GITHUB_REPOSITORY"]
    ramura = os.environ.get("GITHUB_REF_NAME", "main")
    url = f"https://raw.githubusercontent.com/{repo}/{ramura}/postate/{video.name}"

    try:
        media_id = publica_reel(url, descriere)
    except Exception as e:
        log(f"EȘEC: {e}")
        video.unlink()
        p.commit_si_push(f"Reel eșuat, pozele rămân în Drive: {video.name}")
        sys.exit(1)

    log(f"Reel publicat cu succes! ID: {media_id}")
    p.adauga_in_istoric(video.name, ",".join(f["id"] for f in alese), media_id, descriere)
    p.commit_si_push(f"Reel postat pe Instagram: {video.name}")
    for f in alese:
        try:
            drive.muta(f["id"], id_reels, id_postate)
        except Exception as e:
            log(f"! Nu am putut muta {f['name']} în „Postate”: {e}")


if __name__ == "__main__":
    main()
