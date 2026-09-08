"""
Pune produsul pe fundal alb si logoul CS Finds centrat dedesubt.

Folosire:
    python brand.py                          # ia din poze-in/, scoate in poze-brand/
    python brand.py in_dir out_dir
    python brand.py in_dir out_dir --recursive   # intra si in subfoldere (albume Yupoo)
    python brand.py in_dir out_dir --nobg        # scoate si fundalul, intr-un singur pas
    python brand.py in_dir out_dir --main-only   # logo doar pe prima poza din fiecare folder

Merge si pe PNG-uri transparente (iesirea din remove-bg.py --full) si pe JPEG-uri
normale. Fluxul complet:
    yupoo-scrape.py  ->  remove-bg.py --full  ->  brand.py
"""
import os
import sys
from pathlib import Path

from PIL import Image

BASE_DIR = Path(__file__).resolve().parent

LOGO_PATH = BASE_DIR / "assets" / "logos" / "logo-lung.png"
EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# --- Layout (proportii luate din modelul de referinta) ---
# Panza are raport fix, ca sa arate la fel indiferent de forma pozei si ca
# Discord sa nu taie logoul din previzualizare.
# Panza e PATRATA: Discord taie previzualizarea la pozele inalte, iar la 1:1
# se vede intreg produsul, fara crop.
CANVAS_W = 1024
CANVAS_H = 1024
MARGIN = 0.025           # margine laterala
PRODUCT_TOP = 0.11       # cat alb ramane deasupra produsului
# Marginea de jos a produsului nu mai e o constanta: o da logoul, care sta
# pironit pe fundul panzei. Produsul umple tot ce ramane deasupra lui.
LOGO_W = 0.30            # latimea logoului fata de panza
PRODUCT_MAX_H = 0.62     # cat din inaltimea panzei poate ocupa produsul
LOGO_GAP = 0.08          # spatiu intre produs si logo (urca produsul)
BG = (255, 255, 255)


_sesiuni = {}          # model -> sesiune rembg, incarcata o data

# isnet taie o poza in ~2s, birefnet-general-lite in ~20s pe CPU. Diferenta de
# calitate nu merita asteptarea la .image, mai ales in bulk, asa ca rulam isnet.
# Pentru poze grele (contralumina, blana) porneste botul cu
#     REMBG_MODEL=birefnet-general-lite
MODEL = os.getenv("REMBG_MODEL", "isnet-general-use")
FALLBACK_MODEL = "isnet-general-use"

# Morfologia pe masca se face pe o versiune mica: conturul e la fel, dar
# inchiderea si infasuratoarea convexa costa de ~20x mai putin.
MASK_WORK_SIZE = 512

# Alpha sub LO devine complet transparent, peste HI complet opac, intre ele se
# intinde liniar. Asa dispar "fantomele" semi-transparente (fundal alb spalacit)
# dar raman marginile antialiasate.
ALPHA_LO = 60
ALPHA_HI = 170
# Cat de mare trebuie sa fie o componenta, fata de cea mai mare, ca sa treaca
# drept obiect de sine statator (al doilea papuc, cutia de langa produs).
# Masurat pe poze reale: produsul e 100%, cioburile de fundal raman sub 1.5%,
# iar intre ele nu e nimic - deci pragul are loc berechet.
BIG_PART = 0.15
TEXT_MARGIN = 0.06       # cat in jurul produselor mai poate sta text
# Sub prag mai scapa doar textul din dreptul produsului. Ca sa nu treaca si
# cioburile drept text, le cerem sa fie destul de mari si de pline: un bloc de
# scris ocupa procente din panza, cioburile masurate stau sub 0.25%.
SPECK_PART = 0.003       # sub atat din panza e ciob, oricat de dens ar fi
TEXT_FILL = 0.30         # o litera isi umple caseta; un ciob e subtire si rupt
CROP_PAD = 0.02          # margine lasata in jurul produsului la decupare
CLOSE_R = 0.012          # raza (din latura mica) pentru astupat muscaturile din contur
HULL_GROWTH = 1.15       # cat poate creste silueta prin infasuratoarea convexa
# ...si cat de "dreapta" trebuie sa fie silueta ca sa merite umpluta. Masurat:
# infasuratoarea unui papuc umple caseta 0.59-0.75, a unei cutii ~0.95. Fara
# conditia asta, umplerea baga fundal in colturile din jurul unei perechi.
HULL_EXTENT = 0.88
EDGE_ERODE = 1           # pixeli mancati din margine, ca sa piara halo-ul luminos

# Pozele de produs sunt aproape toate pe fundal alb de studio, si acolo se poate
# spune sigur ce e fundal si ce nu: pornim de la marginea pozei si ne intindem
# peste tot ce e alb. Ce nu se atinge de marginea alba e produs - inclusiv
# bucatile pe care modelul le-a muscat din umar sau din poale.
WHITE_MIN = 228          # cat de deschis trebuie sa fie un pixel ca sa treaca drept fundal
WHITE_SPREAD = 22        # ...si cat de necolorat (diferenta intre canale)
WHITE_BORDER = 0.90      # cat din chenarul pozei trebuie sa fie alb ca sa avem incredere
WHITE_MAX_GROWTH = 2.2   # cat poate creste masca prin recuperare, ca sa nu ia toata poza
WHITE_MAX_AREA = 0.85    # ...si cat din poza poate ocupa la final

# Modelul rapid rateaza urat hainele gri deschis pe fundal alb: din tot produsul
# ii ramane doar imprimeul, iar postarea iese cu o zdreanta plutind pe alb
# (masurat pe "Pocket War Hoodie": masca 6.5% din poza, fata de 31% cu modelul
# greu). Sub pragurile de aici taietura e considerata ratata si se reia cu
# modelul greu - cateva secunde in plus, dar numai pe pozele care chiar au
# nevoie. RETRY_MIN_GAIN opreste schimbarea cand modelul greu nu aduce nimic.
# Semnalul e cat de mica iese masca, nu cat de faramitata: masurat pe 215
# postari, o masca sparta in bucati e de cele mai multe ori corecta (o pereche
# de papuci, o grila cu 20 de modele), pe cand sub 12% din poza nu mai incape
# nicio haina intreaga.
RETRY_AREA = 0.12        # sub atat din poza, masca e prea mica ca sa fie produsul
# Produsele care CHIAR sunt mici (un breloc, o sapca) trec si ele pragul de sus,
# dar acolo modelul greu da aceeasi masca - deci il pastram doar cand aduce
# vizibil mai mult, si poza ramane cea rapida.
RETRY_MIN_GAIN = 1.25    # de cate ori mai mare trebuie sa fie masca noua


def fill_mask_holes(cut: Image.Image, original: Image.Image) -> Image.Image:
    """Umple golurile din interiorul siluetei, cu pixelii din poza originala.

    Modelul taie uneori imprimeurile gri/deschise de pe haina, crezandu-le
    fundal. Orice zona transparenta care nu atinge marginea imaginii e de fapt
    parte din produs, asa ca ii punem inapoi si culoarea, nu doar opacitatea
    (rembg lasa negru acolo unde a taiat).
    """
    try:
        import numpy as np
        from scipy.ndimage import binary_fill_holes
    except ImportError:
        return cut

    arr = np.array(cut.convert("RGBA"))
    solid = arr[:, :, 3] > 128
    holes = binary_fill_holes(solid) & ~solid
    if holes.any():
        src = np.array(original.convert("RGB").resize(cut.size, Image.LANCZOS))
        arr[:, :, :3][holes] = src[holes]
        arr[:, :, 3][holes] = 255
    return Image.fromarray(arr, "RGBA")


def recover_from_white(cut: Image.Image, original: Image.Image) -> Image.Image:
    """Pune la loc bucatile pe care modelul le-a muscat din contur.

    `close_mask` astupa doar scobiturile mai inguste decat raza ei, iar
    `heal_convex` merge doar pe obiecte drepte - o muscatura lata dintr-o haina
    scapa de amandoua, si postarea iese cu jumatate de tricou. Pe fundal alb de
    studio insa nu e nevoie de ghicit: tot ce nu e alb si nu se atinge de
    marginea alba a pozei e produs.

    Se aplica doar cand chenarul pozei chiar e alb, si numai la componentele
    care ating deja masca - altfel ar aduna eticheta de langa produs sau,
    pe un fundal colorat, toata poza.

    Ca la celelalte operatii pe masca, socoteala se face pe o versiune mica:
    o muscatura care se vede cu ochiul liber se vede si la 512px, iar la
    rezolutia intreaga (12 MP) etichetarea componentelor dura zeci de secunde.
    """
    try:
        import numpy as np
        from scipy import ndimage
    except ImportError:
        return cut

    arr = np.array(cut.convert("RGBA"))
    solid = arr[:, :, 3] > 128
    if not solid.any():
        return cut

    mic, _ = _shrink(solid, MASK_WORK_SIZE)
    h, w = mic.shape
    small = original.convert("RGB").resize((w, h), Image.LANCZOS)
    rgb = np.array(small).astype(np.int16)
    alb = ((rgb.min(axis=2) >= WHITE_MIN)
           & (rgb.max(axis=2) - rgb.min(axis=2) <= WHITE_SPREAD))

    # Fundal = albul care se vede din marginea pozei. Albul dinauntrul hainei
    # (un imprimeu alb, o dunga) nu se atinge de margine, deci ramane produs.
    chenar = np.zeros(alb.shape, bool)
    chenar[0, :] = chenar[-1, :] = chenar[:, 0] = chenar[:, -1] = True
    if (alb & chenar).sum() < chenar.sum() * WHITE_BORDER:
        return cut                      # poza nu e pe alb: nu avem de unde sti

    lab, _ = ndimage.label(alb)
    din_margine = set(np.unique(lab[chenar & alb])) - {0}
    if not din_margine:
        return cut
    fundal = np.isin(lab, list(din_margine))

    # Candidatii: tot ce nu e fundal. Pastram doar bucatile care se ating de
    # masca, ca sa nu adunam cartonase si umbre din alt colt al pozei.
    lab2, n2 = ndimage.label(~fundal)
    atinse = set(np.unique(lab2[mic])) - {0}
    if not n2 or not atinse:
        return cut
    produs = np.isin(lab2, list(atinse))

    if produs.sum() > mic.sum() * WHITE_MAX_GROWTH or produs.mean() > WHITE_MAX_AREA:
        return cut                      # ar inghiti prea mult: mai bine lasam cum e

    adaugat = _grow(produs & ~mic, solid.shape) & ~solid
    if adaugat.any():
        src = np.array(original.convert("RGB").resize(cut.size, Image.LANCZOS))
        arr[:, :, :3][adaugat] = src[adaugat]
        arr[:, :, 3][adaugat] = 255
    return Image.fromarray(arr, "RGBA")


def _shrink(solid, size):
    """Masca booleana -> aceeasi masca la latura maxima `size` (bool)."""
    import numpy as np
    h, w = solid.shape
    scale = size / max(h, w)
    if scale >= 1:
        return solid, 1.0
    small = Image.fromarray((solid * 255).astype("uint8")).resize(
        (max(1, round(w * scale)), max(1, round(h * scale))), Image.BILINEAR)
    return np.array(small) > 127, scale


def _grow(small, shape):
    """Masca mica -> inapoi la rezolutia intreaga."""
    import numpy as np
    h, w = shape
    big = Image.fromarray((small * 255).astype("uint8")).resize((w, h), Image.BILINEAR)
    return np.array(big) > 127


def heal_convex(cut: Image.Image, original: Image.Image) -> Image.Image:
    """Repara conturul obiectelor drepte (cutii, plachete, telefoane).

    Inchiderea morfologica astupa doar scobiturile mai inguste decat raza ei;
    o muscatura lata din marginea unei plachete ii scapa. La un obiect convex
    insa, orice zona din infasuratoarea convexa e de fapt produs, deci o umplem
    toata. Se aplica numai daca infasuratoarea nu creste silueta cu mai mult de
    HULL_GROWTH, altfel s-ar umple si golurile reale (spatiul dintre doi papuci,
    manerul unei genti).
    """
    try:
        import numpy as np
        from skimage.morphology import convex_hull_image
    except ImportError:
        return cut

    arr = np.array(cut.convert("RGBA"))
    solid = arr[:, :, 3] > 128
    if not solid.any():
        return cut

    small, _ = _shrink(solid, MASK_WORK_SIZE)
    area = small.sum()
    if not area:
        return cut

    hull = convex_hull_image(small)
    if hull.sum() > area * HULL_GROWTH:
        return cut                      # produs cu scobituri reale - il lasam in pace

    ys, xs = np.where(hull)
    caseta = (ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1)
    if hull.sum() < caseta * HULL_EXTENT:
        return cut                      # silueta nu e dreapta: nu e cutie, e produs

    added = _grow(hull & ~small, solid.shape) & ~solid
    if added.any():
        src = np.array(original.convert("RGB").resize(cut.size, Image.LANCZOS))
        arr[:, :, :3][added] = src[added]
        arr[:, :, 3][added] = 255
    return Image.fromarray(arr, "RGBA")


def close_mask(cut: Image.Image, original: Image.Image) -> Image.Image:
    """Astupa muscaturile din contur si redreseaza marginile drepte.

    Pe fundal luminos (fereastra, blitz) modelul rupe bucati din marginea
    produsului. Inchiderea morfologica umple scobiturile mai inguste decat raza,
    fara sa umfle silueta; culoarea vine din original, ca la golurile interioare.
    """
    try:
        import numpy as np
        from scipy import ndimage
    except ImportError:
        return cut

    arr = np.array(cut.convert("RGBA"))
    solid = arr[:, :, 3] > 128
    if not solid.any():
        return cut

    small, _ = _shrink(solid, MASK_WORK_SIZE)
    r = max(2, round(min(small.shape) * CLOSE_R))
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    disk = x * x + y * y <= r * r

    closed = ndimage.binary_closing(small, structure=disk)
    added = _grow(closed & ~small, solid.shape) & ~solid
    if added.any():
        src = np.array(original.convert("RGB").resize(cut.size, Image.LANCZOS))
        arr[:, :, :3][added] = src[added]
        arr[:, :, 3][added] = 255
    return Image.fromarray(arr, "RGBA")


def shave_edge(cut: Image.Image) -> Image.Image:
    """Mananca un pixel din margine: acolo raman culorile fundalului amestecate.

    Fara asta, o poza in contralumina lasa o dunga alba/argintie pe conturul
    produsului cand il punem pe alb.
    """
    if EDGE_ERODE <= 0:
        return cut
    try:
        import numpy as np
        from scipy import ndimage
    except ImportError:
        return cut

    arr = np.array(cut.convert("RGBA"))
    size = 2 * EDGE_ERODE + 1
    arr[:, :, 3] = ndimage.grey_erosion(arr[:, :, 3], size=(size, size))
    return Image.fromarray(arr, "RGBA")


def clean_alpha(cut: Image.Image) -> Image.Image:
    """Intinde alpha si scoate cioburile ramase departe de produs.

    Fara asta, fundalul taiat pe jumatate ramane ca o ceata gri peste alb.
    """
    try:
        import numpy as np
        from scipy import ndimage
    except ImportError:
        return cut

    arr = np.array(cut.convert("RGBA"))
    a = arr[:, :, 3].astype(np.float32)
    a = (a - ALPHA_LO) * (255.0 / (ALPHA_HI - ALPHA_LO))
    a = np.clip(a, 0, 255)

    # Aruncam cioburile de fundal, dar NU si textul: literele si caracterele
    # sunt componente mici si separate, exact ca zgomotul. Le deosebim dupa
    # pozitie - textul sta in cadrul produselor, cioburile raman pe margini.
    solid = a > 127
    if solid.any():
        lab, n = ndimage.label(solid)
        if n > 1:
            sizes = ndimage.sum(solid, lab, range(1, n + 1))
            big = sizes >= sizes.max() * BIG_PART
            keep = np.zeros(n + 1, bool)
            keep[1:] = big

            if big.any():
                rows, cols = np.where(np.isin(lab, np.flatnonzero(big) + 1))
                pad_y = round((rows.max() - rows.min() + 1) * TEXT_MARGIN)
                pad_x = round((cols.max() - cols.min() + 1) * TEXT_MARGIN)
                y0, y1 = rows.min() - pad_y, rows.max() + pad_y
                x0, x1 = cols.min() - pad_x, cols.max() + pad_x

                centers = ndimage.center_of_mass(solid, lab, range(1, n + 1))
                boxes = ndimage.find_objects(lab)
                speck = solid.size * SPECK_PART
                for i, (cy, cx) in enumerate(centers):
                    if keep[i + 1] or not (y0 <= cy <= y1 and x0 <= cx <= x1):
                        continue
                    if sizes[i] < speck:
                        continue                    # praf, oriunde ar sta
                    ys, xs = boxes[i]
                    caseta = (ys.stop - ys.start) * (xs.stop - xs.start)
                    if caseta and sizes[i] / caseta < TEXT_FILL:
                        continue                    # subtire si rupt: ciob, nu litera
                    keep[i + 1] = True              # e text, nu ciob
            a[~keep[lab]] = 0

    arr[:, :, 3] = a.astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def crop_to_subject(cut: Image.Image) -> Image.Image:
    """Taie albul din jur, ca produsul sa umple panza in loc sa pluteasca."""
    box = cut.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
    if not box:
        return cut
    pad = round(max(cut.width, cut.height) * CROP_PAD)
    l, t, r, b = box
    return cut.crop((max(0, l - pad), max(0, t - pad),
                     min(cut.width, r + pad), min(cut.height, b + pad)))


# Modelul greu, pentru cand cel rapid lasa resturi lipite de produs (suporturi,
# umbre, cartonase). Taie mult mai curat, dar ~20s fata de ~2s pe CPU, deci se
# cere anume, de la butonul din panou - nu se foloseste in bulk.
HEAVY_MODEL = os.getenv("REMBG_HEAVY_MODEL", "birefnet-general-lite")


def _sesiune(model: str):
    """Sesiunea rembg pentru un model, incarcata o singura data pe rulare."""
    from rembg import new_session
    if model not in _sesiuni:
        try:
            _sesiuni[model] = new_session(model)
        except Exception as e:
            print(f"[WARN] {model} indisponibil ({e}); folosesc {FALLBACK_MODEL}")
            _sesiuni[model] = new_session(FALLBACK_MODEL)
    return _sesiuni[model]


# Modelul greu vede oricum poza redimensionata la 1024px, dar rembg ii cere
# rezultatul la rezolutia intreaga - iar pe o poza de 12 MP asta a picat cu
# "bad allocation" chiar si cu 10 GB liberi. Ii dam o copie mica si intindem
# masca inapoi: conturul e acelasi, memoria e de zeci de ori mai putina. Conteaza
# mai ales pe VPS-ul mic, unde botul si modelul stau in acelasi proces.
HEAVY_MAX_SIDE = 1600


def _taie(im: Image.Image, model: str):
    """Masca unui model, peste poza originala. -> None daca modelul a picat."""
    from rembg import remove
    try:
        if model == HEAVY_MODEL and max(im.size) > HEAVY_MAX_SIDE:
            mic = im.copy()
            mic.thumbnail((HEAVY_MAX_SIDE, HEAVY_MAX_SIDE), Image.LANCZOS)
            taiat = remove(mic, session=_sesiune(model), post_process_mask=True)
            alpha = taiat.getchannel("A").resize(im.size, Image.BILINEAR)
            intreg = im.convert("RGBA")
            intreg.putalpha(alpha)
            return intreg
        return remove(im, session=_sesiune(model), post_process_mask=True)
    except Exception as e:
        # Fara memorie, fara model descarcat, fara retea: nu are rost sa cada
        # toata postarea pentru un pas care oricum e o imbunatatire.
        print(f"[BG] {model} nu a mers ({e}); raman la ce am")
        return None


def _mask_stats(cut: Image.Image) -> tuple:
    """(cat din poza e masca, cat din masca e cea mai mare bucata)."""
    try:
        import numpy as np
        from scipy import ndimage
    except ImportError:
        return 1.0, 1.0
    m = np.array(cut.convert("RGBA"))[:, :, 3] > 128
    if not m.any():
        return 0.0, 0.0
    lab, n = ndimage.label(m)
    sizes = ndimage.sum(m, lab, range(1, n + 1))
    return float(m.mean()), float(sizes.max() / sizes.sum())


def _taietura_ratata(cut: Image.Image) -> bool:
    """Masca e prea mica ca sa fie produsul intreg."""
    return _mask_stats(cut)[0] < RETRY_AREA


def diagnostic(im: Image.Image) -> tuple:
    """(cat din poza prinde modelul rapid, cat i-ar mai adauga recuperarea de pe alb).

    Cele doua numere spun ce fel de taietura iese fara sa o compunem de tot:
    aria mica inseamna produs facut ferfenita, iar o crestere mare inseamna
    muscaturi din contur. `.fixbg` le foloseste ca sa stie ce postari sa refaca.
    """
    try:
        import numpy as np
    except ImportError:
        return 1.0, 1.0
    from rembg import remove
    raw = remove(im, session=_sesiune(MODEL), post_process_mask=True)
    baza = clean_alpha(fill_mask_holes(raw, im))
    a0 = (np.array(baza.convert("RGBA"))[:, :, 3] > 128).sum()
    a1 = (np.array(recover_from_white(baza, im).convert("RGBA"))[:, :, 3] > 128).sum()
    return float(np.array(raw.convert("RGBA"))[:, :, 3].__gt__(128).mean()),         float(a1 / max(a0, 1))


# O poza buna de produs are o silueta singura si limpede. Un banner sau un
# colaj de catalog are aria la fel de mare, dar sparta in bucati si scris - de
# aceea nu ajunge sa ne uitam la arie cand alegem intre mai multe poze.
PRODUS_ARIE_MIN = 0.10
PRODUS_ARIE_MAX = 0.85


def nota_produs(im: Image.Image) -> float:
    """Cat de mult arata poza a produs fotografiat. 0 = banner sau colaj.

    Nota e cat din masca ocupa cea mai mare bucata: o poza de studio da ~1,
    un colaj cu scris si mai multe produse da 0.6 sau mai putin (masurat pe
    Yeezy Foam: colajul 0.64, pozele adevarate 1.00). O pereche de papuci da
    ~0.5 pe drept, de aceea nota se foloseste ca sa alegem intre poze, nu ca
    prag absolut.
    """
    cut = _taie(im, MODEL)
    if cut is None:
        return 0.0
    aria, mare = _mask_stats(cut)
    if not (PRODUS_ARIE_MIN <= aria <= PRODUS_ARIE_MAX):
        return 0.0
    return mare


def cut_background(im: Image.Image, model: str = None) -> Image.Image:
    """Scoate fundalul. `model` gol -> cel rapid; HEAVY_MODEL -> cel bun si lent.

    Daca modelul rapid da o taietura vizibil ratata, se reia singur cu cel greu:
    mai bine cateva secunde in plus decat o postare cu produsul facut ferfenita.
    """
    model = model or MODEL
    cut = _taie(im, model)
    if cut is None:
        # Modelul cerut n-a pornit. Incercam celalalt; daca nici el, lasam poza
        # intreaga - cu fundal, dar intreaga, nu o postare fara imagine.
        cut = _taie(im, HEAVY_MODEL if model != HEAVY_MODEL else MODEL)
        if cut is None:
            return im.convert("RGBA")
    elif model != HEAVY_MODEL and _taietura_ratata(cut):
        greu = _taie(im, HEAVY_MODEL)
        # Modelul greu poate rata la fel de urat (produse mici, fundal colorat)
        # sau poate sa nu porneasca deloc pe o masina fara memorie: il pastram
        # doar daca a iesit si a prins vizibil mai mult decat cel rapid.
        if greu is not None and _mask_stats(greu)[0] > _mask_stats(cut)[0] * RETRY_MIN_GAIN:
            print(f"[BG] taietura ratata cu {model}; refacuta cu {HEAVY_MODEL}")
            cut = greu
    cut = fill_mask_holes(cut, im)
    # Cioburile se arunca INAINTE de inchiderea conturului: close_mask lipeste
    # de produs orice ciob ramas langa el (masurat: o fasie separata de 4.7% se
    # unea cu talpa), si dupa aceea niciun filtru de componente nu o mai poate
    # deosebi de produs.
    cut = clean_alpha(cut)
    # Recuperarea de pe alb inainte de inchiderea conturului: ce pune ea la loc
    # e produs sigur, iar close_mask are dupa aceea mai putine scobituri de ghicit.
    cut = recover_from_white(cut, im)
    cut = heal_convex(close_mask(cut, im), im)
    return crop_to_subject(clean_alpha(shave_edge(cut)))


def flatten(im: Image.Image) -> Image.Image:
    """Transparenta -> alb, ca sa nu iasa fundal negru la salvare."""
    im = im.convert("RGBA")
    bg = Image.new("RGBA", im.size, BG + (255,))
    return Image.alpha_composite(bg, im).convert("RGB")


def scale_to_width(im: Image.Image, width: int) -> Image.Image:
    h = max(1, round(im.height * width / im.width))
    return im.resize((width, h), Image.LANCZOS)


def fit_box(im: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Incadreaza in cutie pastrand proportiile (nu deformeaza, nu taie)."""
    scale = min(box_w / im.width, box_h / im.height)
    return im.resize((max(1, round(im.width * scale)),
                      max(1, round(im.height * scale))), Image.LANCZOS)


def brand(product: Image.Image, logo: Image.Image = None, nobg: bool = False,
          model: str = None) -> Image.Image:
    """logo=None -> doar fundal alb, fara logo (pentru pozele secundare)."""
    if nobg:
        product = cut_background(product, model)

    margin = round(CANVAS_W * MARGIN)
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BG)

    # Logoul se pironeste jos de tot, la o margine de fundul panzei.
    gap = round(CANVAS_H * LOGO_GAP)
    lg = scale_to_width(logo, round(CANVAS_W * LOGO_W)) if logo is not None else None
    logo_y = CANVAS_H - margin - lg.height if lg is not None else CANVAS_H - margin

    # Produsul primeste tot ce ramane deasupra logoului si sta lipit de el,
    # nu centrat - asa coboara si el, iar albul ramane doar sus.
    top = round(CANVAS_H * PRODUCT_TOP)
    box_w = CANVAS_W - 2 * margin
    # Spatiul de deasupra logoului, dar nu mai mult de PRODUCT_MAX_H: altfel
    # produsul umple tot cardul din forum si arata sufocat.
    box_h = min(logo_y - gap - top, round(CANVAS_H * PRODUCT_MAX_H))
    prod = fit_box(flatten(product), box_w, box_h)
    y_prod = logo_y - gap - prod.height

    canvas.paste(prod, ((CANVAS_W - prod.width) // 2, y_prod))
    if lg is None:
        return canvas

    canvas.paste(lg, ((CANVAS_W - lg.width) // 2, logo_y),
                 lg if lg.mode == "RGBA" else None)
    return canvas


WHITE_CUTOFF = 245       # peste asta consideram pixelul "fundal alb" de logo


def load_logo() -> Image.Image:
    if not LOGO_PATH.exists():
        print(f"[FATAL] Lipseste {LOGO_PATH.name} langa script.")
        sys.exit(1)
    logo = Image.open(LOGO_PATH).convert("RGBA")
    if logo.getchannel("A").getextrema()[0] == 255:
        # Logo fara transparenta: albul aproape-pur devine transparent, altfel
        # se vede o cutie usor gri peste panza alba.
        px = logo.load()
        for y in range(logo.height):
            for x in range(logo.width):
                r, g, b, a = px[x, y]
                if r >= WHITE_CUTOFF and g >= WHITE_CUTOFF and b >= WHITE_CUTOFF:
                    px[x, y] = (255, 255, 255, 0)

    # Fisierul are padding gol in jur (~43% din inaltimea casetei). Fara trim,
    # LOGO_W scaleaza caseta, nu logoul: iesea mai mic decat pare si lasa spatiu
    # mort intre el si produs.
    box = logo.getchannel("A").getbbox()
    return logo.crop(box) if box else logo


def main():
    args = sys.argv[1:]
    recursive = "--recursive" in args
    nobg = "--nobg" in args
    main_only = "--main-only" in args
    args = [a for a in args if not a.startswith("--")]

    in_dir = Path(args[0]) if args else BASE_DIR / "poze-in"
    out_dir = Path(args[1]) if len(args) > 1 else BASE_DIR / "poze-brand"

    if not in_dir.exists():
        in_dir.mkdir(parents=True)
        print(f"[INFO] Am creat {in_dir} - pune pozele acolo si ruleaza din nou.")
        return

    pattern = "**/*" if recursive else "*"
    files = sorted(p for p in in_dir.glob(pattern) if p.suffix.lower() in EXTS)
    if not files:
        print(f"[INFO] Nicio poza in {in_dir}")
        return

    if nobg:
        try:
            import rembg  # noqa: F401
        except ImportError:
            print('[FATAL] --nobg cere rembg. Ruleaza:  pip install "rembg[cpu]" pillow')
            sys.exit(1)

    logo = load_logo()
    # Poza principala = prima din fiecare folder. Doar ea primeste logo.
    main_photos = set()
    if main_only:
        seen_dirs = set()
        for f in files:
            if f.parent not in seen_dirs:
                seen_dirs.add(f.parent)
                main_photos.add(f)

    print(f"Procesez {len(files)} poze{' (fara fundal)' if nobg else ''} -> {out_dir}")

    for i, src in enumerate(files, 1):
        # pastreaza structura de subfoldere cand e --recursive
        rel = src.relative_to(in_dir).with_suffix(".jpg")
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            with_logo = (not main_only) or (src in main_photos)
            out = brand(Image.open(src), logo if with_logo else None, nobg=nobg)
            out.save(dst, "JPEG", quality=92, optimize=True)
            print(f"  [{i}/{len(files)}] {rel} ({out.width}x{out.height})")
        except Exception as e:
            print(f"  [{i}/{len(files)}] {rel} ESUAT: {e}")

    print(f"\nGata. Pozele branduite sunt in {out_dir}")


if __name__ == "__main__":
    main()
