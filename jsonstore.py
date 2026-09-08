"""
Citire si scriere pentru fisierele JSON ale botului (posts, drafts, links).

Scrierea era deja atomica, deci o cadere la mijlocul salvarii nu strica nimic.
Gaura era la citire: orice eroare - inclusiv un OSError trecator, cand fisierul
e tinut o clipa de antivirus sau de un sync de backup - insemna "porneste de la
zero", iar prima salvare de dupa scria tot fisierul gol. Un singur citit ratat
stergea in tacere tot istoricul.

Aici citirea nu mai minte: daca fisierul exista dar nu poate fi citit, ridica
StoreUnreadable, si atunci NU se scrie peste el. Mai bine o postare nesalvata
decat evidenta stearsa.
"""
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("welcome-bot.jsonstore")

BACKUPS = 3               # cate copii tinem in urma (fisier.json.1 ... .3)
READ_RETRIES = 3          # citiri ratate la rand inainte sa ne dam batuti
RETRY_WAIT = 0.15         # secunde intre incercari


class StoreUnreadable(Exception):
    """Fisierul exista dar nu a putut fi citit. Nu-l suprascrie."""


def read_json(path: Path) -> dict:
    """Continutul fisierului, {} daca nu exista inca.

    Ridica StoreUnreadable daca fisierul e acolo dar nu iese: asa apelantul
    poate refuza sa scrie peste el, in loc sa-l goleasca.
    """
    last = None
    for attempt in range(READ_RETRIES):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError) as e:
            last = e
            # Fisierul ocupat o clipa se elibereaza; unul stricat nu se repara.
            if isinstance(e, json.JSONDecodeError):
                break
            time.sleep(RETRY_WAIT)

    # Il punem deoparte cu tot cu continut, ca sa poata fi recuperat manual.
    if isinstance(last, json.JSONDecodeError):
        broken = path.with_suffix(path.suffix + ".corupt")
        try:
            os.replace(path, broken)
            log.error(f"{path.name} e stricat ({last}); l-am mutat in "
                      f"{broken.name}. Cea mai recenta copie buna e "
                      f"{path.name}.1 - redenumeste-o daca vrei sa o folosesti.")
            return {}          # fisierul stricat nu mai e in drum: putem porni curat
        except OSError as e:
            log.error(f"{path.name} e stricat si nu l-am putut muta ({e})")

    raise StoreUnreadable(f"{path.name}: {last}")


def _rotate(path: Path):
    """fisier.json -> .1, .1 -> .2, .2 -> .3. Copia .3 se pierde."""
    for i in range(BACKUPS, 0, -1):
        src = path.with_suffix(path.suffix + (f".{i - 1}" if i > 1 else ""))
        if i == 1:
            src = path
        dst = path.with_suffix(path.suffix + f".{i}")
        if src.exists():
            try:
                os.replace(src, dst) if i > 1 else _copy(src, dst)
            except OSError as e:
                log.warning(f"nu am putut roti {src.name} ({e})")


def _copy(src: Path, dst: Path):
    dst.write_bytes(src.read_bytes())


def write_json(path: Path, data: dict):
    """Salvare atomica, cu copiile de siguranta rotite inainte."""
    _rotate(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
