"""
Murmur - low-RAM speech-to-text for Windows.

Hold Right-Alt to talk, release to transcribe. Text is auto-typed into
whatever window has focus. Runs from a system-tray icon and lets you switch
Whisper model sizes on the fly.

Design goals:
  * Minimal idle RAM. Heavy libraries (faster-whisper / ctranslate2 / numpy /
    sounddevice) are imported lazily, only when you first dictate. The model
    itself loads on first use and is unloaded again after a configurable idle
    period, so memory ramps up on use and falls back down when you're done.
  * One model resident at a time, int8 quantized on CPU for a small footprint.
"""

import json
import os
import sys
import threading
import time
import ctypes

import keyboard
import pystray
from PIL import Image, ImageDraw

# NOTE: numpy, sounddevice, faster_whisper and ctranslate2 are intentionally
# NOT imported here. They are imported lazily inside the methods that need
# them, so an idle Murmur (before the first dictation) stays light.

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

APP_NAME = "Murmur"
APP_VERSION = "1.0.0"  # keep in sync with installer.iss
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

SAMPLE_RATE = 16000  # Whisper expects 16 kHz mono

# Models offered in the tray menu, with rough resident-RAM estimates (int8/CPU).
AVAILABLE_MODELS = [
    ("tiny", "~75 MB"),
    ("base", "~150 MB"),
    ("small", "~500 MB"),
    ("medium", "~1.5 GB"),
    ("large-v3", "~3 GB"),
    ("distil-small.en", "~250 MB, English"),
    ("distil-large-v3", "~1.5 GB, English"),
]
MODEL_NAMES = [m for m, _ in AVAILABLE_MODELS]

# Idle-unload choices shown in the tray (label -> seconds; 0 = never).
IDLE_CHOICES = [
    ("Never (keep in RAM)", 0),
    ("After 30 seconds", 30),
    ("After 1 minute", 60),
    ("After 5 minutes", 300),
    ("After 15 minutes", 900),
]

DEFAULTS = {
    "model": "base",
    # Trigger key. "right alt" avoids clashing with Alt+Tab / menu shortcuts.
    "hotkey": "right alt",
    # "hold" = push-to-talk (hold key, speak, release).
    # "toggle" = tap to start, tap to stop.
    "mode": "hold",
    "output": "type",            # "type", "clipboard", or "both"
    "language": "",              # "" = auto-detect, or e.g. "en"
    "device": "auto",            # "auto", "cpu", "cuda"
    "compute_type": "auto",      # "auto", "int8", "int8_float16", "float16", "float32"
    "beam_size": 1,              # 1 = greedy, fastest; higher = slower, a bit better
    "min_record_seconds": 0.3,   # ignore accidental taps shorter than this
    "idle_unload_seconds": 300,  # free the model after this long idle; 0 = never
    "preload_on_start": False,   # keep idle RAM minimal by NOT loading at launch
    "configured": False,         # set True after the first-run picker
    "update_repo": "Nihal180804/murmur-stt",  # GitHub repo to check releases against
    "check_updates_on_start": False,
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except OSError as exc:
        print(f"[warn] could not save config: {exc}")


# --------------------------------------------------------------------------- #
# Update check (GitHub Releases)
# --------------------------------------------------------------------------- #

def _parse_version(v):
    parts = []
    for chunk in str(v).split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def fetch_latest_release(repo):
    """Return (tag_without_v, html_url) of the latest GitHub release, or None.

    Uses only the standard library; no auth needed for public repos.
    """
    import urllib.request
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"{APP_NAME}/{APP_VERSION}"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.load(resp)
    tag = str(data.get("tag_name", "")).lstrip("vV")
    return tag, data.get("html_url", f"https://github.com/{repo}/releases")


# --------------------------------------------------------------------------- #
# First-run model suggestion
# --------------------------------------------------------------------------- #

def total_ram_gb():
    """Total physical RAM in GB (Windows), or 0 if it can't be determined."""
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys / (1024 ** 3)
    except Exception:
        return 0


def suggest_model():
    ram = total_ram_gb()
    if ram and ram < 6:
        return "tiny"
    if ram and ram >= 16:
        return "small"
    return "base"  # safe default for ~6-16 GB machines (or unknown)


def first_run_pick_model(suggested):
    """A tiny one-time Tk dialog that suggests and lets the user pick a model.

    Falls back to the suggested model if Tk is unavailable.
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        return suggested

    chosen = {"value": suggested}
    root = tk.Tk()
    root.title(f"{APP_NAME} — choose a model")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    ram = total_ram_gb()
    ram_txt = f"{ram:.0f} GB RAM detected. " if ram else ""
    tk.Label(
        root,
        text=f"{ram_txt}Pick a speech model to download on first use.\n"
             f"Bigger = more accurate but more RAM and slower on CPU.\n"
             f"You can change this any time from the tray menu.",
        justify="left",
        padx=16,
        pady=12,
    ).pack(anchor="w")

    var = tk.StringVar(value=suggested)
    frame = tk.Frame(root, padx=16)
    frame.pack(anchor="w")
    for name, note in AVAILABLE_MODELS:
        label = f"{name}   ({note})"
        if name == suggested:
            label += "   ← suggested"
        ttk.Radiobutton(frame, text=label, value=name, variable=var).pack(anchor="w")

    def ok():
        chosen["value"] = var.get()
        root.destroy()

    btns = tk.Frame(root, pady=12)
    btns.pack()
    ttk.Button(btns, text="Use this model", command=ok).pack()

    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 3
    root.geometry(f"+{x}+{y}")
    root.protocol("WM_DELETE_WINDOW", ok)
    root.mainloop()
    return chosen["value"]


# --------------------------------------------------------------------------- #
# Tray icon images
# --------------------------------------------------------------------------- #

def _mic_image(bg):
    img = Image.new("RGB", (64, 64), bg)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([26, 12, 38, 40], radius=6, fill="white")
    d.arc([20, 30, 44, 50], start=0, end=180, fill="white", width=3)
    d.line([32, 50, 32, 56], fill="white", width=3)
    d.line([24, 56, 40, 56], fill="white", width=3)
    return img


ICON_IDLE = _mic_image((40, 44, 52))      # slate grey
ICON_REC = _mic_image((200, 60, 60))      # red while recording
ICON_BUSY = _mic_image((210, 150, 40))    # amber while transcribing


# --------------------------------------------------------------------------- #
# Core engine
# --------------------------------------------------------------------------- #

class Murmur:
    def __init__(self):
        self.cfg = load_config()
        self.model = None
        self.model_name = None
        self.model_lock = threading.Lock()

        self.recording = False
        self.frames = []
        self.stream = None
        self.record_start = 0.0
        self.key_down = False

        self._idle_timer = None
        self._idle_lock = threading.Lock()

        self.icon = pystray.Icon(APP_NAME, ICON_IDLE, self._tooltip(), menu=self._build_menu())

    def _tooltip(self):
        loaded = self.model_name if self.model is not None else "not loaded"
        return f"{APP_NAME} — {self.cfg['hotkey']} ({self.cfg['mode']}) · model: {loaded}"

    def _refresh_icon(self, image=None):
        if image is not None:
            self.icon.icon = image
        try:
            self.icon.title = self._tooltip()
        except Exception:
            pass

    # ---- model lifecycle ---------------------------------------------------

    def _resolve_device(self):
        dev = self.cfg["device"]
        comp = self.cfg["compute_type"]
        if dev == "auto":
            try:
                import ctranslate2
                dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:
                dev = "cpu"
        if comp == "auto":
            comp = "float16" if dev == "cuda" else "int8"
        return dev, comp

    def ensure_model(self):
        with self.model_lock:
            if self.model is not None and self.model_name == self.cfg["model"]:
                return
            from faster_whisper import WhisperModel  # lazy: keeps idle RAM low
            name = self.cfg["model"]
            device, compute_type = self._resolve_device()
            self._notify(f"Loading model '{name}' ({device}/{compute_type})…")
            self.model = None  # drop the old one first to cap peak RAM
            self.model = WhisperModel(name, device=device, compute_type=compute_type)
            self.model_name = name
        self._refresh_icon()
        self._notify(f"Model '{name}' ready")

    def unload_model(self, *_):
        with self.model_lock:
            if self.model is None:
                return
            self.model = None
            self.model_name = None
        # help the native allocator give pages back
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        self._refresh_icon()
        self._notify("Model unloaded (idle) — RAM freed")

    def _schedule_idle_unload(self):
        secs = int(self.cfg.get("idle_unload_seconds", 0) or 0)
        with self._idle_lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            if secs > 0:
                self._idle_timer = threading.Timer(secs, self._idle_unload_if_free)
                self._idle_timer.daemon = True
                self._idle_timer.start()

    def _idle_unload_if_free(self):
        # don't yank the model out mid-dictation
        if not self.recording:
            self.unload_model()

    # ---- audio -------------------------------------------------------------

    def _audio_cb(self, indata, frames, time_info, status):
        if self.recording:
            self.frames.append(indata.copy())

    def start_recording(self):
        if self.recording:
            return
        # cancel any pending idle-unload while we're active
        with self._idle_lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None

        import sounddevice as sd  # lazy import (pulls numpy too)
        self.frames = []
        self.recording = True
        self.record_start = time.time()
        try:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=self._audio_cb,
            )
            self.stream.start()
        except Exception as exc:
            self.recording = False
            self._notify(f"Mic error: {exc}")
            return
        self._refresh_icon(ICON_REC)

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        duration = time.time() - self.record_start
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        self.stream = None
        self._refresh_icon(ICON_IDLE)

        if duration < self.cfg["min_record_seconds"] or not self.frames:
            self._schedule_idle_unload()
            return

        import numpy as np  # lazy import
        audio = np.concatenate(self.frames, axis=0).flatten()
        self.frames = []
        threading.Thread(target=self._transcribe_and_output, args=(audio,), daemon=True).start()

    # ---- transcription + output -------------------------------------------

    def _transcribe_and_output(self, audio):
        try:
            self.ensure_model()
            self._refresh_icon(ICON_BUSY)
            lang = self.cfg["language"] or None
            segments, _info = self.model.transcribe(
                audio,
                language=lang,
                beam_size=int(self.cfg["beam_size"]),
                vad_filter=True,
            )
            text = "".join(seg.text for seg in segments).strip()
        except Exception as exc:
            self._notify(f"Transcription failed: {exc}")
            self._refresh_icon(ICON_IDLE)
            self._schedule_idle_unload()
            return

        self._refresh_icon(ICON_IDLE)
        if text:
            self._deliver(text)
        # start the idle countdown so RAM ramps back down when you stop using it
        self._schedule_idle_unload()

    def _deliver(self, text):
        out = self.cfg["output"]
        if out in ("clipboard", "both"):
            self._set_clipboard(text)
        if out in ("type", "both"):
            time.sleep(0.05)  # let the trigger key finish releasing first
            try:
                keyboard.write(text, delay=0)
            except Exception as exc:
                self._notify(f"Type failed, copied to clipboard: {exc}")
                self._set_clipboard(text)

    def _set_clipboard(self, text):
        try:
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            u = ctypes.windll.user32
            k = ctypes.windll.kernel32
            # Declare 64-bit-safe signatures: without these, ctypes assumes a
            # 32-bit int return and truncates handles/pointers to NULL.
            k.GlobalAlloc.restype = ctypes.c_void_p
            k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
            k.GlobalLock.restype = ctypes.c_void_p
            k.GlobalLock.argtypes = [ctypes.c_void_p]
            k.GlobalUnlock.argtypes = [ctypes.c_void_p]
            u.SetClipboardData.restype = ctypes.c_void_p
            u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            if not u.OpenClipboard(0):
                return
            try:
                u.EmptyClipboard()
                data = text.encode("utf-16-le") + b"\x00\x00"
                handle = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
                ptr = k.GlobalLock(handle)
                ctypes.memmove(ptr, data, len(data))
                k.GlobalUnlock(handle)
                u.SetClipboardData(CF_UNICODETEXT, handle)
            finally:
                u.CloseClipboard()
        except Exception as exc:
            self._notify(f"Clipboard failed: {exc}")

    # ---- hotkey handling ---------------------------------------------------

    def _on_key_event(self, event):
        name = (event.name or "").lower()
        target = self.cfg["hotkey"].lower()
        aliases = {target}
        if target in ("right alt", "alt gr", "altgr"):
            aliases = {"right alt", "alt gr", "altgr"}

        if name not in aliases:
            return

        if self.cfg["mode"] == "hold":
            if event.event_type == "down" and not self.key_down:
                self.key_down = True
                self.start_recording()
            elif event.event_type == "up":
                self.key_down = False
                self.stop_recording()
        else:  # toggle
            if event.event_type == "down" and not self.key_down:
                self.key_down = True
                if self.recording:
                    self.stop_recording()
                else:
                    self.start_recording()
            elif event.event_type == "up":
                self.key_down = False

    # ---- tray menu ---------------------------------------------------------

    def _notify(self, msg):
        print(f"[{APP_NAME}] {msg}")
        try:
            self.icon.notify(msg, APP_NAME)
        except Exception:
            pass

    def _set_model(self, name):
        def handler(icon, item):
            self.cfg["model"] = name
            save_config(self.cfg)
            self.unload_model()  # drop old weights; new one loads on next use
        return handler

    def _set_mode(self, mode):
        def handler(icon, item):
            self.cfg["mode"] = mode
            save_config(self.cfg)
            self._refresh_icon()
        return handler

    def _set_output(self, out):
        def handler(icon, item):
            self.cfg["output"] = out
            save_config(self.cfg)
        return handler

    def _set_idle(self, secs):
        def handler(icon, item):
            self.cfg["idle_unload_seconds"] = secs
            save_config(self.cfg)
            self._schedule_idle_unload()
        return handler

    def _build_menu(self):
        model_items = [
            pystray.MenuItem(
                f"{name}  ({note})",
                self._set_model(name),
                checked=lambda item, name=name: self.cfg["model"] == name,
                radio=True,
            )
            for name, note in AVAILABLE_MODELS
        ]
        mode_items = [
            pystray.MenuItem("Hold to talk (push-to-talk)", self._set_mode("hold"),
                             checked=lambda item: self.cfg["mode"] == "hold", radio=True),
            pystray.MenuItem("Toggle (tap on / tap off)", self._set_mode("toggle"),
                             checked=lambda item: self.cfg["mode"] == "toggle", radio=True),
        ]
        output_items = [
            pystray.MenuItem("Type into active window", self._set_output("type"),
                             checked=lambda item: self.cfg["output"] == "type", radio=True),
            pystray.MenuItem("Copy to clipboard", self._set_output("clipboard"),
                             checked=lambda item: self.cfg["output"] == "clipboard", radio=True),
            pystray.MenuItem("Type + clipboard", self._set_output("both"),
                             checked=lambda item: self.cfg["output"] == "both", radio=True),
        ]
        idle_items = [
            pystray.MenuItem(label, self._set_idle(secs),
                             checked=lambda item, secs=secs: int(self.cfg["idle_unload_seconds"]) == secs,
                             radio=True)
            for label, secs in IDLE_CHOICES
        ]
        return pystray.Menu(
            pystray.MenuItem(lambda item: self._tooltip(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Model", pystray.Menu(*model_items)),
            pystray.MenuItem("Activation", pystray.Menu(*mode_items)),
            pystray.MenuItem("Output", pystray.Menu(*output_items)),
            pystray.MenuItem("Free RAM when idle", pystray.Menu(*idle_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Load model now",
                             lambda i, it: threading.Thread(target=self._preload, daemon=True).start()),
            pystray.MenuItem("Unload model now (free RAM)", self.unload_model),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Check for updates",
                             lambda i, it: threading.Thread(
                                 target=self.check_for_updates, daemon=True).start()),
            pystray.MenuItem(lambda item: f"Version {APP_VERSION}", None, enabled=False),
            pystray.MenuItem("Quit", self._quit),
        )

    def _preload(self):
        self.ensure_model()
        self._schedule_idle_unload()

    def check_for_updates(self, quiet=False):
        """Compare APP_VERSION against the latest GitHub release.

        Opens the release page in the browser if a newer version exists. Does
        NOT download or run anything automatically — the user decides.
        """
        repo = (self.cfg.get("update_repo") or "").strip()
        if not repo:
            if not quiet:
                self._notify("Set 'update_repo' (owner/repo) in config.json to check for updates.")
            return
        try:
            latest, url = fetch_latest_release(repo)
        except Exception as exc:
            if not quiet:
                self._notify(f"Update check failed: {exc}")
            return
        if latest and _parse_version(latest) > _parse_version(APP_VERSION):
            self._notify(f"Update available: v{latest} (you have v{APP_VERSION}). Opening release page…")
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass
        elif not quiet:
            self._notify(f"You're up to date (v{APP_VERSION}).")

    def _quit(self, icon, item):
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        with self._idle_lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
        icon.stop()

    # ---- run ---------------------------------------------------------------

    def run(self):
        keyboard.hook(self._on_key_event)
        if self.cfg.get("preload_on_start"):
            threading.Thread(target=self._preload, daemon=True).start()
        if self.cfg.get("check_updates_on_start") and self.cfg.get("update_repo"):
            threading.Thread(target=self.check_for_updates,
                             kwargs={"quiet": True}, daemon=True).start()
        self.icon.run()


def main():
    # First run: suggest a model and save config before starting the tray.
    if not os.path.exists(CONFIG_PATH):
        cfg = dict(DEFAULTS)
        chosen = first_run_pick_model(suggest_model())
        cfg["model"] = chosen
        cfg["configured"] = True
        save_config(cfg)

    try:
        Murmur().run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
