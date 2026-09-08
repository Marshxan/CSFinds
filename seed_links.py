"""
Umple links.json cu produsele din posturile deja facute.

linkstore a aparut tarziu, deci stie doar linkurile aruncate dupa el. Dar
posts.json tine pentru fiecare post platforma, id-ul, linkul, pretul si poza,
adica exact ce trebuie ca evidenta sa porneasca cu tot istoricul - si ca
avertismentul "already posted" sa functioneze din prima zi, nu peste o luna.

Ruleaza o singura data:
    python seed_links.py           # arata ce ar face, fara sa scrie
    python seed_links.py --scrie   # scrie in links.json

Nu cere reteaua: foloseste doar ce e salvat in posts.json.
"""
import sys

import linkstore
import poststore


def main():
    scrie = "--scrie" in sys.argv
    posts = poststore.all_posts()

    fara_link = [p for p in posts if not (p.get("platform") and p.get("item_id"))]
    utile = [p for p in posts if p.get("platform") and p.get("item_id")]

    produse = {}
    for p in utile:
        produse.setdefault((p["platform"], p["item_id"]), []).append(p)

    print(f"{len(posts)} posturi -> {len(produse)} produse distincte"
          + (f" ({len(fara_link)} fara link, sarite)" if fara_link else ""))

    duble = {k: v for k, v in produse.items() if len(v) > 1}
    if duble:
        print(f"\n{len(duble)} produse postate de mai multe ori:")
        for (platform, item_id), lista in duble.items():
            print(f"  {platform}:{item_id} x{len(lista)} -> "
                  + ", ".join(f"{p.get('title')} (#{p.get('forum_id')})" for p in lista))

    if not scrie:
        print("\nProba. Ruleaza cu --scrie ca sa salvezi.")
        return

    for (platform, item_id), lista in produse.items():
        lista.sort(key=lambda p: p.get("created") or "")
        cel_mai_nou = lista[-1]
        linkstore.record(
            platform, item_id,
            url=cel_mai_nou.get("product_url"),
            price=cel_mai_nou.get("price"),
            images=[p["image_url"] for p in lista if p.get("image_url")],
            weight=cel_mai_nou.get("weight"),
        )
        for p in lista:
            linkstore.record_post(platform, item_id, p.get("thread_id"),
                                  str(p.get("forum_id")), p.get("title"),
                                  guild_id=p.get("guild_id"))

    print(f"\nScris: {len(linkstore.all_links())} produse in links.json")


if __name__ == "__main__":
    main()
