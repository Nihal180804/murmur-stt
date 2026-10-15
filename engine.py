"""Murmur core engine: audio capture, model lifecycle, transcription, output,
global hotkey, notifications and update checks.

Heavy libraries (faster-whisper, ctranslate2, numpy, sounddevice) are imported
lazily so idle RAM stays low until the first dictation. The model loads on use
and is unloaded again after a configurable idle period.

The engine is UI-agnostic: it exposes callbacks (on_status, on_transcript,
notifier) that the GUI and tray register.
"""

import ctypes
import json
import os
import threading
import time

APP_NAME = "Murmur"
APP_VERSION = "1.1.0"  # keep in sync with installer.iss
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

SAMPLE_RATE = 16000

# Built-in Whisper models with rough resident-RAM estimates (int8 / CPU).
AVAILABLE_MODELS = [
    ("tiny", "~75 MB · fastest"),
    ("base", "~150 MB · balanced"),
    ("small", "~500 MB · accurate"),
    ("medium", "~1.5 GB · high accuracy"),
    ("large-v3", "~3 GB · best"),
    ("distil-small.en", "~250 MB · English, fast"),
    ("distil-large-v3", "~1.5 GB · English, accurate"),
]

IDLE_CHOICES = [
    ("Never (keep in RAM)", 0),
    ("After 30 seconds", 30),
    ("After 1 minute", 60),
    ("After 5 minutes", 300),
    ("After 15 minutes", 900),
]

# Status constants
IDLE = "idle"
LOADING = "loading"
LISTENING = "listening"
TRANSCRIBING = "transcribing"

DEFAULTS = {
    "model": "base",
    "custom_model": "",          # HF id or local path; overrides `model` when set
    "hotkey": "right alt",
    "mode": "hold",              # "hold" or "toggle"
    "output": "type",            # "type", "clipboard", "both"
    "language": "",              # "" = auto-detect
    "device": "auto",
    "compute_type": "auto",
    "beam_size": 1,
    "min_record_seconds": 0.3,
    "idle_unload_seconds": 300,
    "preload_on_start": False,
    "notify_on_dictation": True,  # toast on each dictation
    "configured": False,
    "update_repo": "Nihal180804/murmur-stt",
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


def total_ram_gb():
    try:
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys / (1024 ** 3)
    except Exception:
        return 0


def suggest_model():
    ram = total_ram_gb()
    if ram and ram < 6:
        return "tiny"
    if ram and ram >= 16:
        return "small"
    return "base"


def _parse_version(v):
    out = []
    for chunk in str(v).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def fetch_latest_release(repo):
    import urllib.request
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.load(resp)
    return str(data.get("tag_name", "")).lstrip("vV"), data.get("html_url", f"https://github.com/{repo}/releases")


class MurmurEngine:
    def __init__(self):
        self.cfg = load_config()
        self.model = None
        self.model_key = None          # the model id/path currently loaded
        self.model_lock = threading.Lock()

        self.recording = False
        self.frames = []
        self.stream = None
        self.record_start = 0.0
        self.key_down = False

        self._idle_timer = None
        self._idle_lock = threading.Lock()

        # callbacks (registered by GUI / tray / main)
        self.on_status = None          # fn(status_str)
        self.on_transcript = None      # fn(text)
        self.notifier = None           # fn(title, message)
        self.status = IDLE

    # ---- small helpers -----------------------------------------------------

    def active_model_key(self):
        return (self.cfg.get("custom_model") or "").strip() or self.cfg["model"]

    def _set_status(self, status):
        self.status = status
        if self.on_status:
            try:
                self.on_status(status)
            except Exception:
                pass

    def notify(self, message, title=APP_NAME):
        print(f"[{APP_NAME}] {message}")
        if self.notifier:
            try:
                self.notifier(title, message)
            except Exception:
                pass

    # ---- model lifecycle ---------------------------------------------------

    def _resolve_device(self):
        dev, comp = self.cfg["device"], self.cfg["compute_type"]
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
        key = self.active_model_key()
        with self.model_lock:
            if self.model is not None and self.model_key == key:
                return
            from faster_whisper import WhisperModel
            device, compute_type = self._resolve_device()
            self._set_status(LOADING)
            self.notify(f"Loading model '{key}' ({device}/{compute_type})…")
            self.model = None
            self.model = WhisperModel(key, device=device, compute_type=compute_type)
            self.model_key = key
        self._set_status(IDLE if not self.recording else self.status)

    def unload_model(self):
        with self.model_lock:
            if self.model is None:
                return
            self.model = None
            self.model_key = None
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        self._set_status(self.status)
        self.notify("Model unloaded (idle) — RAM freed")

    def is_loaded(self):
        return self.model is not None

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
        if not self.recording:
            self.unload_model()

    # ---- audio -------------------------------------------------------------

    def _audio_cb(self, indata, frames, time_info, status):
        if self.recording:
            self.frames.append(indata.copy())

    def start_recording(self):
        if self.recording:
            return
        with self._idle_lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
        import sounddevice as sd
        self.frames = []
        self.recording = True
        self.record_start = time.time()
        try:
            self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                         dtype="float32", callback=self._audio_cb)
            self.stream.start()
        except Exception as exc:
            self.recording = False
            self.notify(f"Mic error: {exc}")
            return
        self._set_status(LISTENING)
        if self.cfg.get("notify_on_dictation"):
            self.notify("Listening…", f"{APP_NAME} 🎤")

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

        if duration < self.cfg["min_record_seconds"] or not self.frames:
            self._set_status(IDLE)
            self._schedule_idle_unload()
            return

        import numpy as np
        audio = np.concatenate(self.frames, axis=0).flatten()
        self.frames = []
        threading.Thread(target=self._transcribe_and_output, args=(audio,), daemon=True).start()

    # ---- transcription + output -------------------------------------------

    def _transcribe_and_output(self, audio):
        try:
            self.ensure_model()
            self._set_status(TRANSCRIBING)
            lang = self.cfg["language"] or None
            segments, _info = self.model.transcribe(
                audio, language=lang, beam_size=int(self.cfg["beam_size"]), vad_filter=True)
            text = "".join(seg.text for seg in segments).strip()
        except Exception as exc:
            self.notify(f"Transcription failed: {exc}")
            self._set_status(IDLE)
            self._schedule_idle_unload()
            return

        self._set_status(IDLE)
        if text:
            if self.on_transcript:
                try:
                    self.on_transcript(text)
                except Exception:
                    pass
            self._deliver(text)
            if self.cfg.get("notify_on_dictation"):
                preview = text if len(text) <= 120 else text[:117] + "…"
                self.notify(preview, f"{APP_NAME} ✓")
        self._schedule_idle_unload()

    def _deliver(self, text):
        out = self.cfg["output"]
        if out in ("clipboard", "both"):
            self.set_clipboard(text)
        if out in ("type", "both"):
            time.sleep(0.05)
            try:
                import keyboard
                keyboard.write(text, delay=0)
            except Exception as exc:
                self.notify(f"Type failed, copied to clipboard: {exc}")
                self.set_clipboard(text)

    def set_clipboard(self, text):
        try:
            CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
            u, k = ctypes.windll.user32, ctypes.windll.kernel32
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
            self.notify(f"Clipboard failed: {exc}")

    # ---- hotkey ------------------------------------------------------------

    def on_key(self, event):
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
                self.stop_recording() if self.recording else self.start_recording()
            elif event.event_type == "up":
                self.key_down = False

    def start_hotkey(self):
        import keyboard
        keyboard.hook(self.on_key)

    def stop_hotkey(self):
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass

    # ---- updates -----------------------------------------------------------

    def check_for_updates(self, quiet=False):
        repo = (self.cfg.get("update_repo") or "").strip()
        if not repo:
            if not quiet:
                self.notify("Set a GitHub repo for updates in Settings.")
            return None
        try:
            latest, url = fetch_latest_release(repo)
        except Exception as exc:
            if not quiet:
                self.notify(f"Update check failed: {exc}")
            return None
        if latest and _parse_version(latest) > _parse_version(APP_VERSION):
            self.notify(f"Update available: v{latest} (you have v{APP_VERSION}).")
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass
            return latest
        if not quiet:
            self.notify(f"You're up to date (v{APP_VERSION}).")
        return None
