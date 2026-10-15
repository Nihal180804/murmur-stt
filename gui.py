"""Murmur's desktop UI, built with customtkinter.

A single window with a sidebar (Home / Models / Settings / About), live status,
a transcription history, a model manager that can load any open-source Whisper
model by Hugging Face id or local path, and all settings. Runs on the main
thread; the engine pushes status/transcript updates through a thread-safe queue
that the UI drains on a timer.
"""

import os
import queue
import sys
import threading
import time
import webbrowser

import customtkinter as ctk

import engine as E

ACCENT = "#8B5CF6"
ACCENT_HOVER = "#7C3AED"
STATUS_COLORS = {
    E.IDLE: ("#9CA3AF", "Idle"),
    E.LOADING: ("#3B82F6", "Loading model…"),
    E.LISTENING: ("#EF4444", "Listening"),
    E.TRANSCRIBING: ("#F59E0B", "Transcribing…"),
}


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def set_startup(enabled):
    """Add/remove a Startup-folder shortcut so Murmur launches with Windows."""
    try:
        startup = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                               "Start Menu", "Programs", "Startup")
        lnk = os.path.join(startup, "Murmur.lnk")
        if not enabled:
            if os.path.exists(lnk):
                os.remove(lnk)
            return True
        if getattr(sys, "frozen", False):
            target, args = sys.executable, "--tray"
        else:
            target = os.path.join(sys.prefix, "pythonw.exe")
            args = f'"{os.path.abspath("murmur.py")}" --tray'
        icon = resource_path("app.ico")
        ps = (
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
            f"$s.TargetPath='{target}';$s.Arguments='{args}';"
            f"$s.WorkingDirectory='{os.path.dirname(target)}';"
            f"$s.IconLocation='{icon}';$s.Save()"
        )
        import subprocess
        subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                        "-Command", ps], check=True)
        return True
    except Exception as exc:
        print("startup toggle failed:", exc)
        return False


class MurmurGUI(ctk.CTk):
    def __init__(self, eng: E.MurmurEngine):
        super().__init__()
        self.eng = eng
        self.q = queue.Queue()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(E.APP_NAME)
        self.geometry("860x580")
        self.minsize(760, 520)
        try:
            self.iconbitmap(resource_path("app.ico"))
        except Exception:
            pass

        # engine callbacks -> queue (thread-safe hand-off to the UI thread)
        self.eng.on_status = lambda s: self.q.put(("status", s))
        self.eng.on_transcript = lambda t: self.q.put(("transcript", t))

        self._logo_img = self._load_logo(40)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_pages()
        self.show_page("Home")

        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.after(120, self._drain_queue)
        self._refresh_status(self.eng.status)

    # ---- assets ------------------------------------------------------------

    def _load_logo(self, size):
        try:
            from PIL import Image
            img = Image.open(resource_path(os.path.join("assets", "logo.png")))
            return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
        except Exception:
            return None

    # ---- sidebar -----------------------------------------------------------

    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, width=190, corner_radius=0)
        bar.grid(row=0, column=0, sticky="nsew")
        bar.grid_rowconfigure(6, weight=1)

        head = ctk.CTkFrame(bar, fg_color="transparent")
        head.grid(row=0, column=0, padx=16, pady=(20, 8), sticky="w")
        if self._logo_img:
            ctk.CTkLabel(head, image=self._logo_img, text="").grid(row=0, column=0, padx=(0, 10))
        ctk.CTkLabel(head, text=E.APP_NAME, font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=1)

        self.nav_buttons = {}
        for i, name in enumerate(["Home", "Models", "Settings", "About"], start=1):
            b = ctk.CTkButton(bar, text=name, anchor="w", height=38, corner_radius=8,
                              fg_color="transparent", text_color=("gray10", "gray90"),
                              hover_color=("gray80", "gray25"),
                              command=lambda n=name: self.show_page(n))
            b.grid(row=i, column=0, padx=12, pady=3, sticky="ew")
            self.nav_buttons[name] = b

        # status pill at the bottom
        pill = ctk.CTkFrame(bar, corner_radius=10)
        pill.grid(row=7, column=0, padx=12, pady=16, sticky="ew")
        self.dot = ctk.CTkLabel(pill, text="●", font=ctk.CTkFont(size=16), width=18)
        self.dot.grid(row=0, column=0, padx=(10, 4), pady=8)
        self.status_lbl = ctk.CTkLabel(pill, text="Idle", font=ctk.CTkFont(size=13))
        self.status_lbl.grid(row=0, column=1, padx=(0, 8), pady=8, sticky="w")

    # ---- pages -------------------------------------------------------------

    def _build_pages(self):
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.grid(row=0, column=1, sticky="nsew", padx=18, pady=18)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)
        self.pages = {}
        self.pages["Home"] = self._page_home()
        self.pages["Models"] = self._page_models()
        self.pages["Settings"] = self._page_settings()
        self.pages["About"] = self._page_about()

    def show_page(self, name):
        for p in self.pages.values():
            p.grid_forget()
        self.pages[name].grid(row=0, column=0, sticky="nsew")
        for n, b in self.nav_buttons.items():
            b.configure(fg_color=ACCENT if n == name else "transparent")

    def _title(self, parent, text, sub=None):
        ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w")
        if sub:
            ctk.CTkLabel(parent, text=sub, text_color="gray60",
                         font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(2, 14))

    # Home -------------------------------------------------------------------
    def _page_home(self):
        f = ctk.CTkFrame(self.container, fg_color="transparent")
        self._title(f, "Home", "Hold your hotkey, speak, release — text appears where your cursor is.")

        card = ctk.CTkFrame(f, corner_radius=14)
        card.pack(fill="x", pady=(0, 14))
        self.big_dot = ctk.CTkLabel(card, text="●", font=ctk.CTkFont(size=42))
        self.big_dot.grid(row=0, column=0, rowspan=2, padx=(22, 14), pady=18)
        self.big_status = ctk.CTkLabel(card, text="Idle", font=ctk.CTkFont(size=22, weight="bold"))
        self.big_status.grid(row=0, column=1, sticky="w", pady=(18, 0))
        self.home_hint = ctk.CTkLabel(card, text="", text_color="gray60", font=ctk.CTkFont(size=13))
        self.home_hint.grid(row=1, column=1, sticky="w", pady=(0, 18))

        ctk.CTkLabel(f, text="Recent transcriptions", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
        self.history = ctk.CTkTextbox(f, corner_radius=12, font=ctk.CTkFont(size=13))
        self.history.pack(fill="both", expand=True, pady=(6, 0))
        self.history.configure(state="disabled")
        return f

    # Models -----------------------------------------------------------------
    def _page_models(self):
        f = ctk.CTkFrame(self.container, fg_color="transparent")
        self._title(f, "Models", "Pick a built-in Whisper model, or load any open-source model by name or path.")

        # download progress (hidden text until a download runs)
        prog = ctk.CTkFrame(f, corner_radius=10)
        prog.pack(fill="x", pady=(0, 8))
        prog.grid_columnconfigure(0, weight=1)
        self.model_status = ctk.CTkLabel(prog, text="Click a model to download / select it.",
                                         text_color="gray60", font=ctk.CTkFont(size=12), anchor="w")
        self.model_status.grid(row=0, column=0, padx=14, pady=(10, 2), sticky="w")
        self.model_progress = ctk.CTkProgressBar(prog, progress_color=ACCENT)
        self.model_progress.set(0)
        self.model_progress.grid(row=1, column=0, padx=14, pady=(0, 12), sticky="ew")

        self.models_scroll = ctk.CTkScrollableFrame(f, corner_radius=12)
        self.models_scroll.pack(fill="both", expand=True)
        self.model_rows = {}
        self.model_del_buttons = {}
        for name, note in E.AVAILABLE_MODELS:
            self._model_row(self.models_scroll, name, note)

        custom = ctk.CTkFrame(f, corner_radius=12)
        custom.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(custom, text="Load a custom open-source model",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, columnspan=2, padx=14, pady=(12, 2), sticky="w")
        ctk.CTkLabel(custom, text="A Hugging Face CTranslate2 model id or a local model folder.",
                     text_color="gray60", font=ctk.CTkFont(size=12)).grid(row=1, column=0, columnspan=2, padx=14, sticky="w")
        self.custom_entry = ctk.CTkEntry(custom, placeholder_text="model id or path")
        self.custom_entry.grid(row=2, column=0, padx=(14, 8), pady=12, sticky="ew")
        self.custom_entry.insert(0, self.eng.cfg.get("custom_model", ""))
        custom.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(custom, text="Load", width=90, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._load_custom).grid(row=2, column=1, padx=(0, 14), pady=12)
        return f

    def _model_row(self, parent, name, note):
        row = ctk.CTkFrame(parent, corner_radius=10)
        row.pack(fill="x", padx=6, pady=4)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=name, font=ctk.CTkFont(size=15, weight="bold"), width=140, anchor="w").grid(
            row=0, column=0, padx=(14, 6), pady=10, sticky="w")
        ctk.CTkLabel(row, text=note, text_color="gray60", font=ctk.CTkFont(size=12), anchor="w").grid(
            row=0, column=1, padx=6, pady=10, sticky="w")
        btn = ctk.CTkButton(row, text="Use", width=96, command=lambda n=name: self._select_model(n))
        btn.grid(row=0, column=2, padx=(6, 6), pady=8)
        self.model_rows[name] = btn
        delbtn = ctk.CTkButton(row, text="🗑", width=40, fg_color=("gray75", "gray30"),
                               hover_color="#B91C1C", command=lambda n=name: self._delete_model(n))
        delbtn.grid(row=0, column=3, padx=(0, 14), pady=8)
        self.model_del_buttons[name] = delbtn
        self._refresh_model_buttons()
        return row

    def _refresh_model_buttons(self):
        active = self.eng.active_model_key()
        for name, btn in self.model_rows.items():
            if name == active:
                btn.configure(text="Active ✓", fg_color=ACCENT, hover_color=ACCENT_HOVER)
            elif E.is_downloaded(name):
                btn.configure(text="Use", fg_color=("gray75", "gray30"), hover_color=("gray70", "gray35"))
            else:
                btn.configure(text="Download", fg_color=("gray75", "gray30"), hover_color=("gray70", "gray35"))
            dbtn = self.model_del_buttons.get(name)
            if dbtn is not None:
                dbtn.configure(state="normal" if E.is_downloaded(name) else "disabled")

    def _select_model(self, key, custom=False):
        if custom:
            self.eng.cfg["custom_model"] = key
        else:
            self.eng.cfg["model"] = key
            self.eng.cfg["custom_model"] = ""
            self.custom_entry.delete(0, "end")
        E.save_config(self.eng.cfg)
        self.eng.unload_model()
        self._refresh_model_buttons()
        threading.Thread(target=self._run_download, args=(self.eng.active_model_key(),), daemon=True).start()

    def _load_custom(self):
        val = self.custom_entry.get().strip()
        if val:
            self._select_model(val, custom=True)

    def _delete_model(self, name):
        if not E.is_downloaded(name):
            return
        import tkinter.messagebox as mbox
        mb = E.dir_size_mb(E.cache_dir_for(name))
        if not mbox.askyesno("Delete model",
                             f"Delete '{name}' and free ~{mb:.0f} MB?\n"
                             "You can re-download it later."):
            return
        if self.eng.model_key == name:
            self.eng.unload_model()
        freed = E.delete_model(name)
        self._refresh_model_buttons()
        self.model_progress.set(0)
        self.model_status.configure(text=f"Deleted {name} — freed {freed:.0f} MB")
        self.eng.notify(f"Deleted {name} — freed {freed:.0f} MB")

    def _run_download(self, key):
        if not E.is_downloaded(key):
            self.q.put(("dl", (0.0, f"Downloading {key}…")))
            stop = threading.Event()
            threading.Thread(target=self._poll_download, args=(key, stop), daemon=True).start()
            try:
                E.download_model_blocking(key)
            except Exception as exc:
                stop.set()
                self.q.put(("dl", (-1, f"Download failed: {exc}")))
                return
            stop.set()
        self.q.put(("dl", (0.99, f"Loading {key}…")))
        try:
            self.eng.ensure_model()
            self.q.put(("dl", (1.0, f"{key} ready ✓")))
        except Exception as exc:
            self.q.put(("dl", (-1, f"Load failed: {exc}")))
        self.q.put(("models_refresh", None))

    def _poll_download(self, key, stop):
        approx = E.APPROX_MB.get(key, 0)
        cdir = E.cache_dir_for(key)
        while not stop.is_set():
            mb = E.dir_size_mb(cdir)
            if approx:
                frac = min(mb / approx, 0.99)
                self.q.put(("dl", (frac, f"Downloading {key}…  {mb:.0f} / ~{approx} MB")))
            else:
                self.q.put(("dl", (-2, f"Downloading {key}…  {mb:.0f} MB")))
            time.sleep(0.5)

    # Settings ---------------------------------------------------------------
    def _page_settings(self):
        f = ctk.CTkScrollableFrame(self.container, fg_color="transparent")
        self._title(f, "Settings")
        cfg = self.eng.cfg

        def rowframe(label, desc=None):
            box = ctk.CTkFrame(f, corner_radius=10)
            box.pack(fill="x", pady=5)
            box.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(box, text=label, font=ctk.CTkFont(size=14, weight="bold")).grid(
                row=0, column=0, padx=14, pady=(10, 0), sticky="w")
            if desc:
                ctk.CTkLabel(box, text=desc, text_color="gray60", font=ctk.CTkFont(size=12)).grid(
                    row=1, column=0, padx=14, pady=(0, 10), sticky="w")
            return box

        # Hotkey
        box = rowframe("Hotkey", "Key that starts dictation. e.g. right alt, right ctrl, f9.")
        self.hotkey_entry = ctk.CTkEntry(box, width=160)
        self.hotkey_entry.insert(0, cfg["hotkey"])
        self.hotkey_entry.grid(row=0, column=1, rowspan=2, padx=(0, 8), pady=10)
        ctk.CTkButton(box, text="Save", width=70, command=self._save_hotkey).grid(row=0, column=2, rowspan=2, padx=(0, 14))

        # Activation
        box = rowframe("Activation")
        self.mode_seg = ctk.CTkSegmentedButton(box, values=["hold", "toggle"], command=self._set_mode)
        self.mode_seg.set(cfg["mode"])
        self.mode_seg.grid(row=0, column=1, rowspan=2, padx=(0, 14), pady=10)

        # Output
        box = rowframe("Output", "Where transcribed text goes.")
        self.out_seg = ctk.CTkSegmentedButton(box, values=["type", "clipboard", "both"], command=self._set_output)
        self.out_seg.set(cfg["output"])
        self.out_seg.grid(row=0, column=1, rowspan=2, padx=(0, 14), pady=10)

        # Language
        box = rowframe("Language", "Blank = auto-detect. Or a code like en, es, fr.")
        self.lang_entry = ctk.CTkEntry(box, width=120)
        self.lang_entry.insert(0, cfg["language"])
        self.lang_entry.grid(row=0, column=1, rowspan=2, padx=(0, 8), pady=10)
        ctk.CTkButton(box, text="Save", width=70, command=self._save_lang).grid(row=0, column=2, rowspan=2, padx=(0, 14))

        # Idle unload
        box = rowframe("Free RAM when idle", "Unload the model after inactivity to drop memory.")
        self.idle_menu = ctk.CTkOptionMenu(box, values=[l for l, _ in E.IDLE_CHOICES], command=self._set_idle)
        cur = next((l for l, s in E.IDLE_CHOICES if s == int(cfg["idle_unload_seconds"])), E.IDLE_CHOICES[3][0])
        self.idle_menu.set(cur)
        self.idle_menu.grid(row=0, column=1, rowspan=2, padx=(0, 14), pady=10)

        # Switches
        self.sw_chime = self._switch(f, "Chime when recording starts / ends",
                                     cfg.get("chime", True), self._set_chime)
        self.sw_notify = self._switch(f, "Windows notification on every dictation",
                                      cfg.get("notify_on_dictation", False), self._set_notify)
        self.sw_warm = self._switch(f, "Warm up model at launch (if already downloaded)",
                                    cfg.get("warm_on_launch", True), self._set_warm)
        self.sw_startup = self._switch(f, "Start with Windows", self._startup_present(), self._set_startup)
        self.sw_preload = self._switch(f, "Load model at launch (no first-use wait, more idle RAM)",
                                       cfg["preload_on_start"], self._set_preload)
        self.sw_updates = self._switch(f, "Check for updates at launch", cfg["check_updates_on_start"], self._set_check_updates)

        # Actions
        actions = ctk.CTkFrame(f, fg_color="transparent")
        actions.pack(fill="x", pady=(12, 4))
        ctk.CTkButton(actions, text="Load model now", command=lambda: threading.Thread(
            target=self.eng.ensure_model, daemon=True).start()).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="Unload model", fg_color=("gray75", "gray30"),
                      command=self.eng.unload_model).pack(side="left", padx=8)
        ctk.CTkButton(actions, text="Check for updates", command=lambda: threading.Thread(
            target=self.eng.check_for_updates, daemon=True).start()).pack(side="left", padx=8)
        return f

    def _switch(self, parent, text, on, cmd):
        box = ctk.CTkFrame(parent, corner_radius=10)
        box.pack(fill="x", pady=5)
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text=text, font=ctk.CTkFont(size=14)).grid(row=0, column=0, padx=14, pady=12, sticky="w")
        sw = ctk.CTkSwitch(box, text="", command=lambda: cmd(bool(sw.get())), progress_color=ACCENT)
        sw.grid(row=0, column=1, padx=14)
        sw.select() if on else sw.deselect()
        return sw

    # Settings handlers
    def _save_hotkey(self):
        self.eng.cfg["hotkey"] = self.hotkey_entry.get().strip() or "right alt"
        E.save_config(self.eng.cfg)
        self.eng.notify(f"Hotkey set to '{self.eng.cfg['hotkey']}'")

    def _set_mode(self, v):
        self.eng.cfg["mode"] = v
        E.save_config(self.eng.cfg)

    def _set_output(self, v):
        self.eng.cfg["output"] = v
        E.save_config(self.eng.cfg)

    def _save_lang(self):
        self.eng.cfg["language"] = self.lang_entry.get().strip()
        E.save_config(self.eng.cfg)

    def _set_idle(self, label):
        secs = next(s for l, s in E.IDLE_CHOICES if l == label)
        self.eng.cfg["idle_unload_seconds"] = secs
        E.save_config(self.eng.cfg)
        self.eng._schedule_idle_unload()

    def _set_chime(self, on):
        self.eng.cfg["chime"] = on
        E.save_config(self.eng.cfg)
        if on:
            self.eng.chime(True)  # preview

    def _set_notify(self, on):
        self.eng.cfg["notify_on_dictation"] = on
        E.save_config(self.eng.cfg)

    def _set_warm(self, on):
        self.eng.cfg["warm_on_launch"] = on
        E.save_config(self.eng.cfg)

    def _set_preload(self, on):
        self.eng.cfg["preload_on_start"] = on
        E.save_config(self.eng.cfg)

    def _set_check_updates(self, on):
        self.eng.cfg["check_updates_on_start"] = on
        E.save_config(self.eng.cfg)

    def _startup_present(self):
        p = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                         "Start Menu", "Programs", "Startup", "Murmur.lnk")
        return os.path.exists(p)

    def _set_startup(self, on):
        set_startup(on)

    # About ------------------------------------------------------------------
    def _page_about(self):
        f = ctk.CTkFrame(self.container, fg_color="transparent")
        big = self._load_logo(96)
        if big:
            ctk.CTkLabel(f, image=big, text="").pack(pady=(30, 8))
            self._about_logo = big
        ctk.CTkLabel(f, text=E.APP_NAME, font=ctk.CTkFont(size=30, weight="bold")).pack()
        ctk.CTkLabel(f, text=f"v{E.APP_VERSION}", text_color="gray60").pack(pady=(0, 10))
        ctk.CTkLabel(f, text="Low-RAM speech-to-text for Windows.\nHold a key, speak, and it types for you.",
                     justify="center", text_color="gray70").pack(pady=(0, 16))
        repo = self.eng.cfg.get("update_repo", "")
        if repo:
            ctk.CTkButton(f, text="View on GitHub", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                          command=lambda: webbrowser.open(f"https://github.com/{repo}")).pack()
        return f

    # ---- live updates ------------------------------------------------------

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "status":
                    self._refresh_status(payload)
                elif kind == "transcript":
                    self._append_history(payload)
                elif kind == "dl":
                    self._update_dl(payload)
                elif kind == "models_refresh":
                    self._refresh_model_buttons()
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _update_dl(self, payload):
        frac, text = payload
        if not hasattr(self, "model_status"):
            return
        self.model_status.configure(text=text)
        if frac == -2:  # unknown total -> indeterminate
            self.model_progress.configure(mode="indeterminate")
            self.model_progress.start()
        elif frac == -1:  # error
            self.model_progress.stop()
            self.model_progress.configure(mode="determinate")
            self.model_progress.set(0)
        else:
            self.model_progress.stop()
            self.model_progress.configure(mode="determinate")
            self.model_progress.set(frac)

    def _refresh_status(self, status):
        color, label = STATUS_COLORS.get(status, STATUS_COLORS[E.IDLE])
        loaded = self.eng.model_key if self.eng.is_loaded() else "not loaded"
        self.dot.configure(text_color=color)
        self.status_lbl.configure(text=label)
        if hasattr(self, "big_dot"):
            self.big_dot.configure(text_color=color)
            self.big_status.configure(text=label)
            self.home_hint.configure(
                text=f"Hold  {self.eng.cfg['hotkey'].title()}  to dictate   ·   model: {loaded}")
        if hasattr(self, "model_rows"):
            self._refresh_model_buttons()

    def _append_history(self, text):
        self.history.configure(state="normal")
        self.history.insert("0.0", text.strip() + "\n\n")
        self.history.configure(state="disabled")

    # ---- window/tray -------------------------------------------------------

    def hide_to_tray(self):
        self.withdraw()

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()
