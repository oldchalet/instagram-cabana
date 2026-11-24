"""
Reel automat din 3-4 poze puse în folderul „Reels” din Google Drive.

La fiecare rulare:
  1. ia 3-4 poze din „Reels” (alese la întâmplare)
  2. le taie la format vertical 9:16 și face un clip cu tranziții și zoom lent
  3. adaugă o melodie din folderul muzica/ (dacă există)
  4. scrie descrierea și hashtag-urile cu AI
  5. publică clipul ca Reel și mută pozele în „Postate”

Rulează separat de postarea zilnică (vezi reel.yml). Folosește funcțiile din publica.py.
"""

import datetime
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageOps

import publica as p

ROOT = Path(__file__).resolve().parent
LUCRU = ROOT / "_lucru_reel"
MUZICA = ROOT / "muzica"
FOLDER_REELS = "Reels"
MIN_POZE, MAX_POZE = 3, 4
SECUNDE_PE_POZA = 3.0
TRANZITIE = 0.5
FPS = 30
W, H = 1080, 1920


def log(mesaj):
    print(mesaj, flush=True)


def alege_pozele(fisiere):
    poze = [f for f in fisiere if Path(f["name"]).suffix.lower() in p.EXTENSII]
    if len(poze) < MIN_POZE:
        return None
    return random.sample(poze, min(len(poze), MAX_POZE))


def cadru(sursa: Path, dest: Path) -> Path:
    """Taie poza la 9:16 pe centru (1080x1920)."""
    img = ImageOps.exif_transpose(Image.open(sursa)).convert("RGB")
    img = ImageOps.fit(img, (W, H), Image.LANCZOS)
    img.save(dest, "JPEG", quality=92)
    return dest


def alege_muzica():
    if not MUZICA.exists():
        return None
    piese = [f for f in MUZICA.iterdir() if f.suffix.lower() in {".mp3", ".m4a", ".wav"}]
    return random.choice(piese) if piese else None


def construieste_video(cadre, muzica, iesire: Path) -> Path:
    n = len(cadre)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for c in cadre:
        cmd += ["-framerate", str(FPS), "-loop", "1", "-t", str(SECUNDE_PE_POZA), "-i", str(c)]

    cadri = int(SECUNDE_PE_POZA * FPS)
    filtre = []
    for i in range(n):  # zoom lent pe fiecare poză
        filtre.append(
            f"[{i}:v]zoompan=z='min(zoom+0.0007,1.08)':d={cadri}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={W}x{H}:fps={FPS},format=yuv420p,setsar=1[v{i}]"
        )
    ultimul = "v0"
    for i in range(1, n):  # tranziție fade între poze
        decalaj = round(i * (SECUNDE_PE_POZA - TRANZITIE), 3)
        filtre.append(
            f"[{ultimul}][v{i}]xfade=transition=fade:duration={TRANZITIE}:offset={decalaj}[x{i}]"
        )
        ultimul = f"x{i}"
    durata = round(n * (SECUNDE_PE_POZA - TRANZITIE) + TRANZITIE, 2)

    harti = ["-map", f"[{ultimul}]"]
    audio = []
    if muzica:
        cmd += ["-stream_loop", "-1", "-i", str(muzica)]
        filtre.append(f"[{n}:a]atrim=0:{durata},afade=t=out:st={durata - 1.5:.2f}:d=1.5[a]")
        harti += ["-map", "[a]"]
        audio = ["-c:a", "aac", "-b:a", "128k"]

    cmd += ["-filter_complex", ";".join(filtre), *harti,
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-t", str(durata),
            "-movflags", "+faststart", *audio, str(iesire)]
    subprocess.run(cmd, check=True)
    return iesire


def publica_reel(url_video, descriere):
    token = os.environ["IG_ACCESS_TOKEN"]
    cont = p.ig("GET", "me", token, fields="user_id,username")
    ig_id = cont["user_id"]
    log(f"Cont Instagram: @{cont.get('username')}")

    container = p.ig(
        "POST", f"{ig_id}/media", token,
        media_type="REELS", video_url=url_video, caption=descriere, share_to_feed="true",
    )["id"]
    for _ in range(72):  # până la ~6 minute, clipurile durează mai mult
        stare = p.ig("GET", container, token, fields="status_code").get("status_code")
        if stare == "FINISHED":
            break
        if stare == "ERROR":
            raise RuntimeError("Instagram nu a putut procesa clipul.")
        time.sleep(5)
    else:
        raise RuntimeError("Clipul nu a fost procesat la timp.")
    return p.ig("POST", f"{ig_id}/media_publish", token, creation_id=container)["id"]


def main():
    drive = p.Drive()
    principal = os.environ["DRIVE_FOLDER_ID"].strip()
    id_reels = drive.subfolder(principal, FOLDER_REELS)
    id_postate = drive.subfolder(principal, p.FOLDER_POSTATE)

    fisiere = drive.listeaza(id_reels)
    alese = alege_pozele(fisiere)
    if not alese:
        log(f"Sunt mai puțin de {MIN_POZE} poze în „{FOLDER_REELS}”. Nu fac clipul acum.")
        return
    log("Poze alese: " + ", ".join(f["name"] for f in alese))

    LUCRU.mkdir(exist_ok=True)
    cadre, note = [], []
    for i, f in enumerate(alese):
        src = drive.descarca(f["id"], LUCRU / f"orig_{i}_{f['name']}".replace("/", "_"))
        cadre.append(cadru(src, LUCRU / f"cadru_{i}.jpg"))
        nota = p.nota_pentru(f, fisiere, drive)
        if nota:
            note.append(nota)

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    video = construieste_video(cadre, alege_muzica(), p.POSTATE / f"{stamp}_reel.mp4")
    log(f"Clip creat: {video.name}")

    descriere = p.genereaza_descriere(cadre[0], " ".join(note))
    log("\n----- DESCRIERE -----\n" + descriere + "\n---------------------\n")

    if p.DOAR_TEST:
        video.unlink()
        log("Mod TEST: nu s-a publicat nimic.")
        return

    # Instagram descarcă clipul de la o adresă publică -> îl urcăm în repository
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
