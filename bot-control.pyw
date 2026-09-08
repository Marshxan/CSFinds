"""
Panou simplu pentru pornit / oprit botul ChinaSide.
Dublu-click pe bot-control.pyw (sau pe scurtatura Bot Control).
"""
import socket
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

BASE_DIR = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
BOT_FILE = BASE_DIR / "bot.py"
LOG_FILE = BASE_DIR / "bot.log"
LOCK_PORT = 50517            # acelasi port ca lock-ul din bot.py
POLL_MS = 2000

def find_pythonw() -> str:
    """Cand rulam ca .exe, sys.executable e panoul, nu Python -> cautam in sistem."""
    if not getattr(sys, "frozen", False):
        cand = Path(sys.executable).with_name("pythonw.exe")
        return str(cand if cand.exists() else sys.executable)
    for p in (Path(r"C:\Python312\pythonw.exe"), Path(r"C:\Python311\pythonw.exe")):
        if p.exists():
            return str(p)
    from shutil import which
    return which("pythonw") or which("python") or "python"


PYTHONW = find_pythonw()

BG = "#1e1f22"
CARD = "#2b2d31"
FG = "#dbdee1"
MUTED = "#949ba4"
GREEN = "#23a55a"
RED = "#f23f43"
BLUE = "#00a2e8"

NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def is_running() -> bool:
    """Botul tine portul de lock ocupat cat timp ruleaza."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        return False
    except OSError:
        return True
    finally:
        s.close()


def start_bot():
    subprocess.Popen(
        [PYTHONW, str(BOT_FILE)],
        cwd=str(BASE_DIR),
        creationflags=NO_WINDOW | DETACHED,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_bot():
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.CommandLine -like '*start-bot.bat*' -or "
        "$_.CommandLine -like '*launch-hidden.vbs*' -or "
        "(($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') "
        "-and $_.CommandLine -like '*bot.py*') } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   creationflags=NO_WINDOW, capture_output=True)


def last_log_line() -> str:
    try:
        lines = [l.strip() for l in LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        return lines[-1][:90] if lines else ""
    except Exception:
        return ""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ChinaSide Bot")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.geometry("400x235")

        title_f = tkfont.Font(family="Segoe UI", size=13, weight="bold")
        status_f = tkfont.Font(family="Segoe UI", size=15, weight="bold")
        small_f = tkfont.Font(family="Segoe UI", size=8)
        btn_f = tkfont.Font(family="Segoe UI", size=10, weight="bold")

        tk.Label(self, text="ChinaSide Bot", font=title_f, bg=BG, fg=FG).pack(pady=(16, 10))

        card = tk.Frame(self, bg=CARD)
        card.pack(fill="x", padx=18)
        self.dot = tk.Canvas(card, width=14, height=14, bg=CARD, highlightthickness=0)
        self.dot_id = self.dot.create_oval(2, 2, 12, 12, fill=MUTED, outline="")
        self.dot.pack(side="left", padx=(14, 8), pady=14)
        self.status = tk.Label(card, text="Se verifica...", font=status_f, bg=CARD, fg=FG)
        self.status.pack(side="left", pady=14)

        btns = tk.Frame(self, bg=BG)
        btns.pack(pady=16)
        self.start_btn = tk.Button(btns, text="Pornire", font=btn_f, width=11, bd=0,
                                   bg=GREEN, fg="white", activebackground="#1a8245",
                                   activeforeground="white", cursor="hand2",
                                   command=self.on_start)
        self.start_btn.grid(row=0, column=0, padx=5, ipady=6)
        self.stop_btn = tk.Button(btns, text="Oprire", font=btn_f, width=11, bd=0,
                                  bg=RED, fg="white", activebackground="#c02f33",
                                  activeforeground="white", cursor="hand2",
                                  command=self.on_stop)
        self.stop_btn.grid(row=0, column=1, padx=5, ipady=6)
        self.restart_btn = tk.Button(btns, text="Restart", font=btn_f, width=11, bd=0,
                                     bg=BLUE, fg="white", activebackground="#0081ba",
                                     activeforeground="white", cursor="hand2",
                                     command=self.on_restart)
        self.restart_btn.grid(row=0, column=2, padx=5, ipady=6)

        self.log = tk.Label(self, text="", font=small_f, bg=BG, fg=MUTED,
                            wraplength=360, justify="center")
        self.log.pack(padx=12)

        self.refresh()

    def on_start(self):
        if not is_running():
            self.set_busy("Pornesc...")
            start_bot()
        self.after(1500, self.refresh)

    def on_stop(self):
        self.set_busy("Opresc...")
        stop_bot()
        self.after(1200, self.refresh)

    def on_restart(self):
        self.set_busy("Repornesc...")
        stop_bot()
        self.after(1500, self._restart_step2)

    def _restart_step2(self):
        start_bot()
        self.after(2000, self.refresh)

    def set_busy(self, text):
        self.status.config(text=text, fg=MUTED)
        self.dot.itemconfig(self.dot_id, fill=BLUE)
        for b in (self.start_btn, self.stop_btn, self.restart_btn):
            b.config(state="disabled")
        self.update_idletasks()

    def refresh(self):
        running = is_running()
        self.status.config(text="Online" if running else "Offline",
                           fg=GREEN if running else RED)
        self.dot.itemconfig(self.dot_id, fill=GREEN if running else RED)
        self.start_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")
        self.restart_btn.config(state="normal" if running else "disabled")
        self.log.config(text=last_log_line())
        self.after(POLL_MS, self.refresh)


if __name__ == "__main__":
    App().mainloop()
