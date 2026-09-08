"""
CS Finds - Poze produse.
Lipesti linkuri Yupoo (categorii, albume sau poze directe), apesi Start,
pozele ajung procesate in Desktop\\poze produse.
"""
import os
import queue
import shutil
import sys
import threading
import traceback
from pathlib import Path

# Scriptul e in scripts/, dar modulele importate sunt in radacina proiectului.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image

import brand
import yupoo_scrape as ys

DEFAULT_OUT = Path.home() / "Desktop" / "poze produse"
IS_FROZEN = getattr(sys, "frozen", False)
BASE_DIR = Path(sys.executable).parent if IS_FROZEN else Path(__file__).resolve().parent
brand.LOGO_PATH = BASE_DIR / "logo-lung.png"


class App:
    def __init__(self, root):
        self.root = root
        self.log_q = queue.Queue()
        self.running = False
        root.title("CS Finds - Poze produse")
        root.geometry("720x640")
        root.minsize(600, 540)

        pad = {"padx": 12, "pady": 6}

        ttk.Label(root,
                  text="Linkuri (unul pe linie): categorie Yupoo, album, sau link direct de poza",
                  font=("Segoe UI", 9)).pack(anchor="w", **pad)
        self.urls = tk.Text(root, height=9, wrap="none", font=("Consolas", 9))
        self.urls.pack(fill="x", padx=12)

        # --- folder de iesire ---
        frm = ttk.Frame(root)
        frm.pack(fill="x", **pad)
        ttk.Label(frm, text="Salveaza in:").pack(side="left")
        self.out_var = tk.StringVar(value=str(DEFAULT_OUT))
        ttk.Entry(frm, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(frm, text="Alege...", command=self.pick_folder).pack(side="left")

        # --- optiuni ---
        opts = ttk.LabelFrame(root, text="Optiuni")
        opts.pack(fill="x", **pad)
        self.nobg = tk.BooleanVar(value=True)
        self.main_only = tk.BooleanVar(value=True)
        self.keep_raw = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Scoate fundalul",
                        variable=self.nobg).pack(anchor="w", padx=8)
        ttk.Checkbutton(opts, text="Logo doar pe poza principala (prima din album)",
                        variable=self.main_only).pack(anchor="w", padx=8)
        ttk.Checkbutton(opts, text="Pastreaza si pozele originale, needitate",
                        variable=self.keep_raw).pack(anchor="w", padx=8)

        # --- butoane ---
        btns = ttk.Frame(root)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="Start", command=self.start)
        self.start_btn.pack(side="left")
        ttk.Button(btns, text="Deschide folderul",
                   command=self.open_folder).pack(side="left", padx=6)
        self.progress = ttk.Progressbar(btns, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=6)

        self.log_box = tk.Text(root, height=12, state="disabled", wrap="word",
                               font=("Consolas", 9), bg="#111111", fg="#dddddd")
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.root.after(120, self.drain_log)

    # ---------- helpers UI ----------
    def pick_folder(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get() or str(DEFAULT_OUT))
        if d:
            self.out_var.set(d)

    def open_folder(self):
        out = Path(self.out_var.get())
        out.mkdir(parents=True, exist_ok=True)
        os.startfile(out)

    def log(self, msg):
        self.log_q.put(str(msg))

    def drain_log(self):
        while not self.log_q.empty():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", self.log_q.get() + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(120, self.drain_log)

    # ---------- munca ----------
    def start(self):
        if self.running:
            return
        entries = [l.strip() for l in self.urls.get("1.0", "end").splitlines() if l.strip()]
        if not entries:
            self.log("[!] Nu ai pus niciun link.")
            return
        self.running = True
        self.start_btn.configure(state="disabled")
        self.progress.start(12)
        threading.Thread(target=self.worker, args=(entries,), daemon=True).start()

    def finish(self):
        self.running = False
        self.progress.stop()
        self.start_btn.configure(state="normal")

    def worker(self, entries):
        try:
            self.run_pipeline(entries)
        except Exception:
            self.log("[EROARE]\n" + traceback.format_exc())
        finally:
            self.root.after(0, self.finish)

    def run_pipeline(self, entries):
        out_root = Path(self.out_var.get())
        raw_root = out_root / "_originale"
        out_root.mkdir(parents=True, exist_ok=True)

        # 1. Descarcare
        albums, direct = [], []
        for e in entries:
            if "/albums/" in e:
                albums.append(e)
            elif "/categories/" in e:
                self.log("Citesc categoria: " + e)
                albums.extend(self.expand_category(e))
            else:
                direct.append(e)

        if albums:
            self.log(f"{len(albums)} albume de descarcat.")
        for i, a in enumerate(albums, 1):
            self.log(f"[{i}/{len(albums)}] descarc album...")
            try:
                ys.download_album(a, raw_root)
            except Exception as ex:
                self.log(f"  esuat: {ex}")

        if direct:
            folder = raw_root / "poze-directe"
            folder.mkdir(parents=True, exist_ok=True)
            for i, u in enumerate(direct, 1):
                try:
                    ext = Path(u.split("?")[0]).suffix or ".jpg"
                    (folder / f"{i:03d}{ext}").write_bytes(ys.fetch(u))
                    self.log(f"  poza directa {i} salvata")
                except Exception as ex:
                    self.log(f"  poza directa {i} esuata: {ex}")

        # 2. Procesare
        files = sorted(p for p in raw_root.rglob("*") if p.suffix.lower() in brand.EXTS)
        if not files:
            self.log("[!] Nicio poza descarcata.")
            return

        nobg = self.nobg.get()
        main_only = self.main_only.get()
        if nobg:
            self.log("Pregatesc modelul de scos fundalul (prima data se descarca ~179 MB)...")

        logo = brand.load_logo()
        # Poza principala = prima din fiecare album.
        main_photos, seen_dirs = set(), set()
        for f in files:
            if f.parent not in seen_dirs:
                seen_dirs.add(f.parent)
                main_photos.add(f)

        self.log(f"Procesez {len(files)} poze...")
        done = 0
        for f in files:
            rel = f.relative_to(raw_root).with_suffix(".jpg")
            dst = out_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                with_logo = (not main_only) or (f in main_photos)
                img = brand.brand(Image.open(f), logo if with_logo else None, nobg=nobg)
                img.save(dst, "JPEG", quality=92, optimize=True)
                done += 1
                self.log(f"  [{done}/{len(files)}] {rel}" + (" + logo" if with_logo else ""))
            except Exception as ex:
                self.log(f"  {rel} ESUAT: {ex}")

        if not self.keep_raw.get():
            shutil.rmtree(raw_root, ignore_errors=True)
            self.log("Am sters originalele.")

        self.log(f"\nGATA. {done} poze in {out_root}")

    def expand_category(self, url):
        seen, page_no = [], 1
        while True:
            sep = "&" if "?" in url else "?"
            page_url = url if page_no == 1 else f"{url}{sep}page={page_no}"
            try:
                page = ys.fetch_html(page_url)
            except Exception as ex:
                self.log(f"  pagina {page_no}: {ex}")
                break
            known = {s.split("?")[0] for s in seen}
            found = [a for a in ys.album_links(page, url) if a.split("?")[0] not in known]
            if not found:
                break
            seen.extend(found)
            self.log(f"  pagina {page_no}: {len(found)} albume")
            page_no += 1
        return seen


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
