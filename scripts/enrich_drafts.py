"""
Completeaza drafturile cu datele de pe Kakobuy: pret, poze, greutate.

Importul din Yupoo ia pretul scris de vanzator in titlul albumului (in euro) si
coperta aleasa de el - care e adesea un tabel de marimi, un selfie in oglinda
sau o poza cu text peste. Kakobuy are pretul real in dolari, pe variante, si
pozele de catalog ale magazinului.

    python enrich_drafts.py            # proba pe primele 5, nu scrie
    python enrich_drafts.py --scrie    # toate drafturile

Ce nu vine de la Kakobuy ramane cum era: un produs fara pret acolo isi pastreaza
pretul din Yupoo, nu ramane gol.
"""
import sys
import time
from pathlib import Path

# Scriptul e in scripts/, dar modulele importate sunt in radacina proiectului.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import imagecmd
import poststore
import scrapecmd

PAUZA = 0.3
VERIFICA_POZE = 3         # cate poze masuram pentru tabelul de marimi


def spune(text: str):
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc))


def alege_poza(candidati: list):
    """Prima poza care nu e tabel de marimi."""
    for u in candidati[:VERIFICA_POZE]:
        if not scrapecmd._e_tabel(u):
            return u
    return candidati[0] if candidati else None


def main():
    scrie = "--scrie" in sys.argv
    drafturi = poststore.all_drafts()
    if not scrie:
        drafturi = drafturi[:5]

    schimbate, fara_date, erori = 0, [], []
    for i, d in enumerate(drafturi, 1):
        if not d.get("product_url"):
            continue
        try:
            item = imagecmd.kakobuy_item(d["product_url"])
        except Exception as e:
            erori.append(f"{d.get('name')}: {e}")
            continue
        time.sleep(PAUZA)

        if not item["price"] and not item["images"]:
            fara_date.append(d.get("name") or d.get("id"))
            continue

        vechi_pret, vechi_poza = d.get("price"), d.get("photo")

        # Pozele de la Kakobuy primele, apoi cele din albumul Yupoo ca rezerva.
        poze = list(dict.fromkeys(
            (item["images"] or []) + (d.get("photos") or [])))[:8]
        noua_poza = alege_poza(poze) if poze else vechi_poza

        d["price"] = item["price"] or vechi_pret
        d["photos"] = poze or d.get("photos") or []
        d["photo"] = noua_poza or vechi_poza
        if item["weight"]:
            d["weight"] = item["weight"]

        if scrie:
            poststore.save_draft(d)
        schimbate += 1
        spune(f"  [{i}/{len(drafturi)}] {(d.get('name') or '')[:34]:<36} "
              f"pret {vechi_pret or '-'} -> {d['price'] or '-':<8} "
              f"{'poza noua' if d['photo'] != vechi_poza else 'aceeasi poza'}")

    print(f"\n{schimbate} drafturi completate, {len(fara_date)} fara date pe Kakobuy, "
          f"{len(erori)} erori")
    for n in fara_date[:10]:
        spune(f"  fara date: {n}")
    for e in erori[:5]:
        spune(f"  eroare: {e}")
    if not scrie:
        print("\nProba pe primele 5. Ruleaza cu --scrie pentru toate.")


if __name__ == "__main__":
    main()
