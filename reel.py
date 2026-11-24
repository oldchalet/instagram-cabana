"""
Reel automat din poze puse în folderul „Reels” din Google Drive.

La fiecare rulare:
  1. ia până la 8 poze din „Reels”, iar AI-ul alege și ordonează cele mai bune 3-4
  2. le corectează ușor (lumină, culoare) și le taie la format vertical 9:16
  3. face un clip cu tranziții și zoom lent, fără tremur
  4. adaugă o melodie din folderul muzica/ (dacă există)
  5. adaugă un text scurt, discret, scris de AI, pe baza pozelor și a descrierii
  6. scrie descrierea și hashtag-urile cu AI
  7. dacă mod_reel = "auto": publică clipul ca Reel și mută pozele în „Postate”
     dacă mod_reel = "manual": salvează clipul și descrierea în folderul
     gata_de_postat din repository, iar tu le postezi din aplicația Instagram

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
MIN_POZE, MAX_POZE, MAX_CANDIDATI = 3, 4, 8
SECUNDE_PE_POZA = 3.0
TRANZITIE = 0.5
FPS = 30
W, H = 1080, 1920
FACTOR_ZOOM = 3          # imaginea se mărește de 3 ori înainte de zoom -> fără tremur
ZOOM_PAS, ZOOM_MAX = 0.0005, 1.08
FONTURI = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def log(mesaj):
    print(mesaj, flush=True)


# ---------------------------------------------------------------- AI
def ai(content, max_tokens=300):
    """Trimite un mesaj către Claude și întoarce textul răspunsului, sau None la eroare."""
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


def alege_pozele_ai(candidati, mici):
    """AI-ul alege și ordonează 3-4 poze. Dacă nu reușește, alege la întâmplare."""
    text = (
        f"Ai {len(candidati)} poze pentru un clip de Instagram al unei cabane A-frame. "
        "Alege cele mai bune 3 sau 4: bine luminate, fără blur, cu cadre atractive, "
        "fără poze aproape identice. Ordonează-le astfel: întâi un exterior sau o priveliște, "
        "apoi interior, apoi un detaliu, iar ultima să fie una frumoasă de final. "
        'Răspunde DOAR cu JSON, fără alt text: {"ordine": [numerele pozelor în ordinea aleasă, începând cu 1]}'
    )
    continut = []
    for i, m in enumerate(mici, 1):
        continut.append({"type": "text", "text": f"Poza {i}:"})
        continut.append(imagine_b64(m))
    continut.append({"type": "text", "text": text})

    raspuns = ai(continut)
    try:
        date = json.loads(raspuns[raspuns.find("{"): raspuns.rfind("}") + 1])
        ordine, vazute = [], set()
        for n in date["ordine"]:
            n = int(n) - 1
            if 0 <= n < len(candidati) and n not in vazute:
                vazute.add(n)
                ordine.append(candidati[n])
        if len(ordine) >= MIN_POZE:
            return ordine[:MAX_POZE]
    except (TypeError, ValueError, KeyError, AttributeError):
        pass
    log("! AI-ul nu a ales poze, aleg la întâmplare.")
    return candidati[:MAX_POZE]


def text_ecran(cadru0: Path, descriere: str):
    """Un text scurt pentru ecran, pe baza primei poze și a descrierii. Poate fi None."""
    text = (
        "Pe baza pozei și a descrierii de mai jos, scrie un text scurt pentru ecranul clipului: "
        "2 până la 5 cuvinte, în română cu diacritice, cald, care invită la vizită. "
        "Fără ghilimele, fără emoji, fără punct final. Răspunde doar cu textul.\n\n"
        f"Descriere: {descriere[:600]}"
    )
    raspuns = ai([imagine_b64(cadru0), {"type": "text", "text": text}], max_tokens=40)
    if not raspuns:
        return None
    raspuns = raspuns.strip().strip('"„”“').strip()
    if not raspuns or len(raspuns) > 40 or "\n" in raspuns:
        return None
    return raspuns


# ---------------------------------------------------------------- imagini
def alege_muzica():
    if not MUZICA.exists():
        return None
    piese = [f for f in MUZICA.iterdir() if f.suffix.lower() in {".mp3", ".m4a", ".wav"}]
    return random.choice(piese) if piese else None


def cadru(sursa: Path, dest: Path) -> Path:
    """Taie la 9:16, corectează lumina și culoarea ușor, apoi salvează."""
    img = ImageOps.exif_transpose(Image.open(sursa)).convert("RGB")
    img = ImageOps.fit(img, (W, H), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Brightness(img).enhance(1.03)
    img = ImageEnhance.Contrast(img).enhance(1.05)
    img = ImageEnhance.Color(img).enhance(1.10)
    r, g, b = img.split()
    r = r.point(lambda v: min(255, int(v * 1.03)))   # un pic mai cald
    b = b.point(lambda v: int(v * 0.97))
    img = Image.merge("RGB", (r, g, b))
    img.save(dest, "JPEG", quality=92)
    return dest


def font(marime):
    for f in FONTURI:
        if Path(f).exists():
            return ImageFont.truetype(f, marime)
    return ImageFont.load_default(size=marime)


def deseneaza_text(text: str, dest: Path) -> Path:
    """O bandă semi-transparentă, discretă, în treimea de jos, cu textul centrat."""
    strat = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(strat)
    y1, y2 = int(H * 0.80), int(H * 0.80) + 130
    d.rectangle([0, y1, W, y2], fill=(0, 0, 0, 110))
    f = font(46)
    l, t, r_, b_ = d.textbbox((0, 0), text, font=f)
    d.text(((W - (r_ - l)) / 2, y1 + (130 - (b_ - t)) / 2 - t), text,
           font=f, fill=(255, 255, 255, 235))
    strat.save(dest, "PNG")
    return dest


# ---------------------------------------------------------------- video
def construieste_video(cadre, muzica, overlay, iesire: Path) -> Path:
    n = len(cadre)
    durata = round(n * (SECUNDE_PE_POZA - TRANZITIE) + TRANZITIE, 2)
    cadri = int(SECUNDE_PE_POZA * FPS)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for c in cadre:
        cmd += ["-framerate", str(FPS), "-loop", "1", "-t", str(SECUNDE_PE_POZA), "-i", str(c)]
    if overlay:
        cmd += ["-framerate", str(FPS), "-loop", "1", "-t", str(durata), "-i", str(overlay)]

    filtre = []
    for i in range(n):  # zoom lent pe imagine mărită, deci fără tremur
        filtre.append(
            f"[{i}:v]scale={W * FACTOR_ZOOM}:{H * FACTOR_ZOOM},setsar=1,"
            f"zoompan=z='min(zoom+{ZOOM_PAS},{ZOOM_MAX})':d={cadri}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={W}x{H}:fps={FPS},format=yuv420p[v{i}]"
        )
    ultimul = "v0"
    for i in range(1, n):
        decalaj = round(i * (SECUNDE_PE_POZA - TRANZITIE), 3)
        filtre.append(f"[{ultimul}][v{i}]xfade=transition=fade:duration={TRANZITIE}:offset={decalaj}[x{i}]")
        ultimul = f"x{i}"
    if overlay:
        filtre.append(f"[{ultimul}][{n}:v]overlay=0:0:format=auto[ovl]")
        ultimul = "ovl"

    harti = ["-map", f"[{ultimul}]"]
    audio = []
    if muzica:
        idx = n + (1 if overlay else 0)
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

    alese_perechi = alege_pozele_ai([(f, c) for f, c in cadre_cand], mici)
    alese = [f for f, _ in alese_perechi]
    cadre = [c for _, c in alese_perechi]
    log("Poze alese: " + ", ".join(f["name"] for f in alese))

    note = [p.nota_pentru(f, fisiere, drive) for f in alese]
    note = [x for x in note if x]

    descriere = p.genereaza_descriere(cadre[0], " ".join(note))
    log("\n----- DESCRIERE -----\n" + descriere + "\n---------------------\n")

    text = text_ecran(cadre[0], descriere)
    overlay = None
    if text:
        overlay = deseneaza_text(text, LUCRU / "text.png")
        log(f"Text pe ecran: {text}")

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    video = construieste_video(cadre, alege_muzica(), overlay, LUCRU / f"{stamp}_reel.mp4")
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

    # mod automat: Instagram descarcă clipul de la o adresă publică -> îl urcăm în repository
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
