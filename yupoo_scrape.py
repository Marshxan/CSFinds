"""
Descarca pozele originale de pe Yupoo (o categorie intreaga sau un singur album).

Folosire:
    python yupoo-scrape.py https://noghost.x.yupoo.com/categories/5058915
    python yupoo-scrape.py <link album>
    python yupoo-scrape.py <link> "C:/Users/kevin/Desktop/poze produse"

Fiecare album ajunge in subfolderul lui, cu numele albumului.
Dupa asta poti da:  python remove-bg.py --full "<folder>" "<folder>-nobg"
"""
import re
import sys
import html as htmlmod
import time
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse, urljoin

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = Path.home() / "Desktop" / "poze produse"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,image/*,*/*;q=0.8",
}
DELAY = 0.4          # pauza intre cereri, ca sa nu ne blocheze
SAFE_NAME = re.compile(r'[<>:"/\|?*\x00-\x1f]')


def fetch(url: str, referer: str = None) -> bytes:
    headers = dict(HEADERS)
    headers["Referer"] = referer or f"https://{urlparse(url).netloc}/"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def fetch_html(url: str, referer: str = None) -> str:
    return fetch(url, referer).decode("utf-8", "replace")


def clean(name: str, fallback: str) -> str:
    name = htmlmod.unescape(name).strip()
    name = SAFE_NAME.sub("", name).strip(" .")
    return name[:80] or fallback


def album_links(page_html: str, base: str) -> list:
    """Linkurile de albume dintr-o pagina de categorie, in ordinea din pagina."""
    hrefs = dict.fromkeys(re.findall(r'href="([^"]*/albums/[^"]+)"', page_html))
    return [urljoin(base, h) for h in hrefs]


# Pagina de categorie are deja titlul si coperta fiecarui album, deci o lista
# completa costa o singura cerere, nu una per album.
ALBUM_BLOCK = re.compile(r'<a\s[^>]*class="album__main"[^>]*>', re.S)
ATTR_TITLE = re.compile(r'title="([^"]*)"')
ATTR_HREF = re.compile(r'href="([^"]+)"')
PAGE_COUNT = re.compile(r'pagination-span"[^>]*>\s*\d+\s*/\s*(\d+)\s*<')


def category_albums(page_html: str, base: str) -> list:
    """-> [{'title', 'url'}] pentru albumele de pe o pagina de categorie."""
    out, seen = [], set()
    for block in ALBUM_BLOCK.findall(page_html):
        href = ATTR_HREF.search(block)
        title = ATTR_TITLE.search(block)
        if not href:
            continue
        url = urljoin(base, href.group(1))
        key = url.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": htmlmod.unescape(title.group(1)).strip() if title else "",
                    "url": url})
    return out


SIDEBAR = re.compile(
    r'<a\s+href="(/categories/\d+)"\s*>\s*<li[^>]*>(.*?)</li>', re.S)


def sidebar_categories(page_html: str, base: str) -> list:
    """Brandurile din meniul lateral: -> [{'name', 'url'}], in ordinea din pagina."""
    out, seen = [], set()
    for href, name in SIDEBAR.findall(page_html):
        url = urljoin(base, href)
        if url in seen:
            continue
        seen.add(url)
        out.append({"name": htmlmod.unescape(re.sub(r"<[^>]+>", "", name)).strip(),
                    "url": url})
    return out


def category_page_count(page_html: str) -> int:
    """Cate pagini are categoria (din indicatorul '1 / 2')."""
    m = PAGE_COUNT.search(page_html)
    return int(m.group(1)) if m else 1


def album_title(page_html: str, album_id: str) -> str:
    for pat in (r'<h1[^>]*>(.*?)</h1>',
                r'class="showalbumheader__gallerytitle"[^>]*>(.*?)<',
                r'<title>(.*?)</title>'):
        m = re.search(pat, page_html, re.S)
        if m:
            text = re.sub(r'<[^>]+>', '', m.group(1))
            name = clean(text, "")
            if name:
                return name
    return f"album-{album_id}"


def photo_urls(page_html: str) -> list:
    """Originalele. data-origin-src = rezolutie maxima; big.jpeg e rezerva."""
    urls = list(dict.fromkeys(
        re.findall(r'data-origin-src="([^"]*photo\.yupoo\.com[^"]+)"', page_html)))
    if not urls:
        urls = list(dict.fromkeys(
            re.findall(r'data-src="([^"]*photo\.yupoo\.com[^"]+/big\.[a-z]+)"', page_html)))
    return [u if u.startswith("http") else "https:" + u for u in urls]


def download_album(url: str, out_root: Path):
    # Yupoo da 404 pe /albums/<id> fara parametri.
    if "?" not in url:
        url += "?uid=1"
    page = fetch_html(url)
    album_id = re.search(r'/albums/(\d+)', url)
    album_id = album_id.group(1) if album_id else "album"
    title = album_title(page, album_id)
    urls = photo_urls(page)

    if not urls:
        print(f"  [!] {title}: nicio poza gasita")
        return 0

    folder = out_root / f"{title} ({album_id})"
    folder.mkdir(parents=True, exist_ok=True)
    print(f"  {title} - {len(urls)} poze -> {folder.name}/")

    saved = 0
    for i, purl in enumerate(urls, 1):
        ext = Path(urlparse(purl).path).suffix or ".jpg"
        dst = folder / f"{i:03d}{ext}"
        if dst.exists():
            saved += 1
            continue
        try:
            dst.write_bytes(fetch(purl, referer=url))
            saved += 1
        except Exception as e:
            print(f"    [{i}] esuat: {e}")
        time.sleep(DELAY)
    return saved


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    target = sys.argv[1]
    out_root = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    out_root.mkdir(parents=True, exist_ok=True)

    if "/albums/" in target:
        total = download_album(target, out_root)
        print(f"\nGata: {total} poze in {out_root}")
        return

    # Categorie: parcurgem paginile pana nu mai apar albume noi.
    seen, page_no = [], 1
    while True:
        sep = "&" if "?" in target else "?"
        page_url = target if page_no == 1 else f"{target}{sep}page={page_no}"
        try:
            page = fetch_html(page_url)
        except Exception as e:
            print(f"[!] pagina {page_no}: {e}")
            break
        found = [a for a in album_links(page, target) if a.split("?")[0] not in
                 {s.split("?")[0] for s in seen}]
        if not found:
            break
        seen.extend(found)
        print(f"Pagina {page_no}: {len(found)} albume")
        page_no += 1
        time.sleep(DELAY)

    print(f"\nTotal {len(seen)} albume. Descarc...\n")
    total = 0
    for i, a in enumerate(seen, 1):
        print(f"[{i}/{len(seen)}]", end=" ")
        try:
            total += download_album(a, out_root)
        except Exception as e:
            print(f"  esuat: {e}")
        time.sleep(DELAY)
    print(f"\nGata: {total} poze in {out_root}")


if __name__ == "__main__":
    main()


# Linkul de magazin sta in descrierea albumului, dar impachetat in redirectul
# Yupoo si encodat de doua ori: /external?url=https%253A%252F%252F...
EXTERNAL = re.compile(r'/external\?url=([^"\'&<\s]+)')
STORE_HOST = re.compile(r'(taobao\.com|tmall\.com|weidian\.com|1688\.com)')


# Albumele au adesea si Taobao, si Weidian. Taobao e preferat: il accepta toti
# agentii si are pagina de produs mai stabila.
STORE_ORDER = ("taobao.com", "tmall.com", "weidian.com", "1688.com")


DIRECT_STORE = re.compile(
    "https?://[^" + chr(34) + chr(39) + "<> ]*"
    "(?:taobao|tmall|weidian|1688)[.]com[^" + chr(34) + chr(39) + "<> ]*")


def album_product_link(page_html: str, prefer: tuple = STORE_ORDER):
    """Linkul de magazin din descrierea albumului, in ordinea de preferinta.

    Doua surse: redirectul /external din corpul paginii (dublu encodat) si meta
    description din antet, unde linkurile stau in clar. Taobao apare adesea doar
    in a doua, asa ca fara ea am fi luat mereu Weidian.
    """
    found = {}

    def add(url: str):
        m = STORE_HOST.search(url)
        if m:
            found.setdefault(m.group(1), url)

    for raw in EXTERNAL.findall(page_html):
        add(htmlmod.unescape(unquote(unquote(raw))).split("&amp;")[0])
    for url in DIRECT_STORE.findall(htmlmod.unescape(page_html)):
        add(url)

    for host in prefer:
        if host in found:
            return found[host]
    return None


# Titlurile vin in doua forme: "¥209 /€27Nume" si "89¥/11€Nume".
# Ordinea conteaza: in "¥99 €12.3 Nume" un tipar lacom ar citi "99 €" si ar
# lua pretul in yuani. Deci intai cerem simbolul lipit de numar, fara spatiu,
# si abia daca nu iese incercam forma cu numarul inaintea simbolului.
NUM = "([0-9]+(?:[.][0-9]+)?)"
PRICE_AFTER = re.compile("[" + chr(8364) + "$]" + NUM)
PRICE_BEFORE = re.compile(NUM + "[ ]*[" + chr(8364) + "$]")


def _fmt(value: float) -> str:
    """65.0 -> '65', 12.5 -> '12.5'. Fara zecimale inutile in titlu."""
    return str(int(value)) if value == int(value) else str(value)


def _price_cluster(title: str):
    """Toate preturile din capul titlului, in ordine, plus unde se termina.

    Vanzatorul pune uneori mai multe preturi ("$65 $80 $108" sau "65-108€"),
    cate unul pe varianta. Le luam pe toate cat timp stau lipite unele de
    altele, ca sa nu inghitim un numar din nume (ex. "Air Max 90").
    """
    spans = []
    for rx in (PRICE_AFTER, PRICE_BEFORE):
        spans += [(m.start(), m.end(), float(m.group(1))) for m in rx.finditer(title)]
    spans.sort()

    kept, cut = [], None
    for start, end, value in spans:
        if kept and start < cut:      # suprapunere intre cele doua tipare
            continue
        if kept and start > cut + 4:  # prea departe: deja suntem in nume
            break
        kept.append(value)
        cut = end
    return kept, (cut or 0)


def split_album_title(title: str):
    """'89¥/11€Boycott Greed T-shirt' -> ('Boycott Greed T-shirt', '11').

    Cu mai multe preturi iese intervalul: '$65 $108 Bulldozer' -> '65-108'.
    """
    values, cut = _price_cluster(title)
    if values:
        low, high = min(values), max(values)
        price = _fmt(low) if low == high else _fmt(low) + "-" + _fmt(high)
    else:
        price = ""
    name = title[cut:] if values else title
    name = re.sub("^[^A-Za-z]+", "", name)    # preturi si semne ramase in fata
    name = re.sub("[0-9]+$", "", name)        # numarul de poze lipit in coada
    return name.strip() or title.strip(), price


# Coperta albumului (miniatura din antet) nu e neaparat prima poza din grila:
# vanzatorul o alege separat, si e cea pe care o vezi in lista de categorii.
COVER = re.compile(r"photo\.yupoo\.com/[^/\"']+/([0-9a-f]+)/(?:medium|square)\.jpg")
ORIGIN = re.compile(r'data-origin-src="([^"]*?/%s/[^"]+)"')


def album_cover(page_html: str):
    """Linkul copertei, la rezolutie originala daca o gasim. None daca lipseste."""
    m = COVER.search(page_html)
    if not m:
        return None
    full = re.search(ORIGIN.pattern % re.escape(m.group(1)), page_html)
    if full:
        return full.group(1) if full.group(1).startswith("http") else "https:" + full.group(1)
    return m.group(0) if m.group(0).startswith("http") else "https://" + m.group(0)
