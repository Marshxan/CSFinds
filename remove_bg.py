"""
Scoate fundalul de la mai multe poze deodata si le pregateste ca emoji Discord.

Folosire:
    python remove-bg.py                       # ia tot din logos-in/, scoate in emojis/
    python remove-bg.py <url> [<url> ...]     # direct de pe internet
    python remove-bg.py QC=<url>              # forteaza numele fisierului (QC.png)
    python remove-bg.py in_dir out_dir        # foldere custom
    python remove-bg.py --full in_dir out_dir # pastreaza rezolutia (poze de produs)

Fara --full pozele sunt aduse la 128px patrat, gata de emoji Discord.
Cu --full raman la dimensiunea originala, doar fara fundal.

Poti pune si un fisier urls.txt (un link pe linie, optional "Nume=link");
e citit automat la fiecare rulare.

Instalare (o singura data):
    pip install "rembg[cpu]" pillow
"""
import io
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

BASE_DIR = Path(__file__).resolve().parent
URLS_FILE = BASE_DIR / "urls.txt"

args = sys.argv[1:]
FULL = "--full" in args        # pastreaza rezolutia originala
args = [a for a in args if not a.startswith("--")]
url_args = [a for a in args if a.split("=", 1)[-1].startswith("http")]
dir_args = [a for a in args if a not in url_args]

IN_DIR = Path(dir_args[0]) if len(dir_args) > 0 else BASE_DIR / "logos-in"
OUT_DIR = Path(dir_args[1]) if len(dir_args) > 1 else BASE_DIR / "emojis"

# Yupoo & co. blocheaza hotlinking-ul daca nu pari un browser.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
}


def split_named(entry: str):
    """'QC=https://...' -> ('QC', url);  'https://...' -> (None, url)."""
    if "=" in entry and not entry.split("=", 1)[0].startswith("http"):
        name, url = entry.split("=", 1)
        return name.strip(), url.strip()
    return None, entry.strip()


def collect_urls() -> list:
    entries = list(url_args)
    if URLS_FILE.exists():
        for line in URLS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                entries.append(line)
    return entries


def download(url: str) -> Image.Image:
    """Descarca imaginea in memorie, cu Referer setat pe propriul domeniu."""
    host = urlparse(url).netloc
    headers = dict(HEADERS, Referer=f"https://{host}/")
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    return Image.open(io.BytesIO(data))


def name_from_url(url: str, index: int) -> str:
    stem = Path(urlparse(url).path).stem
    return stem or f"image{index}"

EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
EMOJI_SIZE = 128          # Discord afiseaza emoji la 32-48px; 128 e destul si ramane mic
MAX_BYTES = 256 * 1024    # limita Discord per emoji


def fit_square(im: Image.Image, size: int) -> Image.Image:
    """Redimensioneaza pastrand proportiile si centreaza pe o panza patrata transparenta."""
    im = im.convert("RGBA")
    im.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    return canvas


def save_under_limit(im: Image.Image, path: Path):
    """Emoji: patrat 128px sub 256 KB. Cu --full: rezolutia originala, intacta."""
    if FULL:
        im.convert("RGBA").save(path, "PNG", optimize=True)
        return path.stat().st_size

    size = EMOJI_SIZE
    while True:
        fit_square(im, size).save(path, "PNG", optimize=True)
        if path.stat().st_size <= MAX_BYTES or size <= 32:
            return path.stat().st_size
        size //= 2


def main():
    try:
        from rembg import remove, new_session
    except ImportError:
        print('[FATAL] Lipseste rembg. Ruleaza:  pip install "rembg[cpu]" pillow')
        sys.exit(1)

    urls = collect_urls()
    files = sorted(p for p in IN_DIR.iterdir() if p.suffix.lower() in EXTS) if IN_DIR.exists() else []

    if not urls and not files:
        IN_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] Nimic de procesat. Pune poze in {IN_DIR.name}/, linkuri in "
              f"{URLS_FILE.name}, sau da URL-ul ca argument.")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # O singura sesiune pentru tot lotul - altfel modelul se incarca la fiecare poza.
    session = new_session("isnet-general-use")

    # (eticheta afisata, nume fisier iesire, functie care da imaginea)
    jobs = [(src.name, src.stem, (lambda p=src: Image.open(p))) for src in files]
    for i, entry in enumerate(urls, 1):
        name, url = split_named(entry)
        jobs.append((url, name or name_from_url(url, i), (lambda u=url: download(u))))

    print(f"Procesez {len(jobs)} imagini -> {OUT_DIR}/")
    for i, (label, stem, load) in enumerate(jobs, 1):
        dst = OUT_DIR / (stem + ".png")
        try:
            cut = remove(load(), session=session)
            size = save_under_limit(cut, dst)
            print(f"  [{i}/{len(jobs)}] {label} -> {dst.name} ({size // 1024} KB)")
        except Exception as e:
            print(f"  [{i}/{len(jobs)}] {label} ESUAT: {e}")

    print("\nGata. Numeste fisierele ca butoanele (KakoBuy.png, QC.png...) si da .setupemojis in Discord.")


if __name__ == "__main__":
    main()
