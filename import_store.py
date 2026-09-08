"""
Trage toate albumele unui magazin Yupoo in drafturi, dintr-o singura rulare.

.import face o categorie odata si e plafonat; asta le ia pe toate, cu pauze
intre cereri ca sa nu supere Yupoo. Nu posteaza nimic pe Discord: drafturile
raman in panou, unde le dai nume, pret si categorie inainte de publicare.

    python import_store.py <link magazin>            # proba, nu scrie nimic
    python import_store.py <link magazin> --scrie    # salveaza drafturile

Albumele fara link de magazin in descriere sunt sarite: fara ele nu se pot face
butoanele de agenti, adica tocmai rostul postarii.
"""
import re
import sys
import time

def spune(text: str):
    """Consola Windows e pe cp1252 si crapa pe nume chinezesti; le inlocuim."""
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, "replace").decode(enc))


import poststore
import scrapecmd
import yupoo_scrape as ys

PAUZA = 0.4               # secunde intre albume
CATEGORII_RE = re.compile(r'href="/categories/(\d+)')


def categorii(magazin: str) -> list:
    """Id-urile categoriilor din pagina /categories a magazinului."""
    baza = magazin.split("/categories")[0].rstrip("/")
    html = ys.fetch_html(baza + "/categories")
    return [(cid, f"{baza}/categories/{cid}")
            for cid in dict.fromkeys(CATEGORII_RE.findall(html))]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    scrie = "--scrie" in sys.argv
    if not args:
        print(__doc__)
        return

    magazin = args[0]
    cats = categorii(magazin)
    spune(f"{len(cats)} categorii in {magazin}")

    total, noi, deja, fara_link, erori = 0, 0, 0, [], []
    for cid, url in cats:
        try:
            albume, _, _ = scrapecmd.scrape_category(url)
        except Exception as e:
            erori.append(f"categoria {cid}: {e}")
            continue
        print(f"\ncategoria {cid}: {len(albume)} albume")

        for album in albume:
            total += 1
            try:
                d = scrapecmd.album_details(album["url"])
            except Exception as e:
                erori.append(f"{album['title'][:30]}: {e}")
                continue
            if not d["product_url"] or not d["photo"]:
                fara_link.append(d["name"][:38] or album["title"][:38])
                continue

            if scrie:
                nou = poststore.save_draft({
                    "id": album["url"].rstrip("/").split("/")[-1].split("?")[0],
                    "name": d["name"],
                    "price": d["price"],
                    "product_url": d["product_url"],
                    "photo": d["photo"],
                    "photos": d.get("photos") or [],
                    "album_url": album["url"].split("?")[0],
                    "forum_id": None,
                    "forum_name": "",
                })
                noi += nou
                deja += not nou
            else:
                noi += 1
            spune(f"  {d['price'] or '-':>7} {d['name'][:52]}")
            time.sleep(PAUZA)

    print(f"\n{total} albume: {noi} {'salvate' if scrie else 'gata de salvat'}, "
          f"{deja} deja in drafturi, {len(fara_link)} fara link de magazin, "
          f"{len(erori)} erori")
    if fara_link:
        print("\nFara link de magazin (nu se pot posta):")
        for n in fara_link[:15]:
            spune(f"  - {n}")
    if erori:
        print("\nErori:")
        for e in erori[:10]:
            spune(f"  - {e}")
    if not scrie:
        print("\nProba. Ruleaza cu --scrie ca sa salvezi drafturile.")


if __name__ == "__main__":
    main()
