"""Murmur entry point: wires the engine, the system-tray icon and the GUI.

The GUI runs on the main thread; the tray icon runs detached in its own thread;
the global hotkey hook runs in the keyboard library's thread. Closing the
window hides it to the tray; Quit (from the tray or the window) stops everything.

Run with --tray to start hidden in the tray (used by the "start with Windows"
shortcut).
"""

import os
import sys
import threading

import pystray
from PIL import Image

import engine as E
from gui import MurmurGUI, resource_path


def _tray_image():
    try:
        return Image.open(resource_path(os.path.join("assets", "logo.png")))
    except Exception:
        img = Image.new("RGB", (64, 64), (99, 102, 241))
        return img


def build_tray(eng, gui):
    def do(fn):
        return lambda icon, item: gui.after(0, fn)

    def quit_all(icon, item):
        eng.stop_hotkey()
        icon.stop()
        gui.after(0, gui.destroy)

    menu = pystray.Menu(
        pystray.MenuItem("Open Murmur", do(gui.show_window), default=True),
        pystray.MenuItem(lambda item: f"Hotkey: {eng.cfg['hotkey']} ({eng.cfg['mode']})", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Check for updates",
                         lambda i, it: threading.Thread(target=eng.check_for_updates, daemon=True).start()),
        pystray.MenuItem("Quit", quit_all),
    )
    return pystray.Icon(E.APP_NAME, _tray_image(), E.APP_NAME, menu=menu)


def main():
    eng = E.MurmurEngine()

    # First run: suggest a sensible model, then let the window be the onboarding.
    if not eng.cfg.get("configured"):
        eng.cfg["model"] = E.suggest_model()
        eng.cfg["configured"] = True
        E.save_config(eng.cfg)

    gui = MurmurGUI(eng)

    icon = build_tray(eng, gui)
    eng.notifier = lambda title, msg: icon.notify(msg, title)
    # Run the tray's Win32 loop in a background thread so it never blocks the
    # GUI's mainloop on the main thread.
    threading.Thread(target=icon.run, daemon=True).start()

    eng.start_hotkey()

    # Warm up the model at launch so the first dictation is instant — but only
    # if it's already downloaded, to avoid a surprise multi-hundred-MB download.
    if eng.cfg.get("preload_on_start") or (
            eng.cfg.get("warm_on_launch", True) and E.is_downloaded(eng.active_model_key())):
        threading.Thread(target=eng.ensure_model, daemon=True).start()
    if eng.cfg.get("check_updates_on_start") and eng.cfg.get("update_repo"):
        threading.Thread(target=lambda: eng.check_for_updates(quiet=True), daemon=True).start()

    if "--tray" in sys.argv:
        gui.withdraw()

    try:
        gui.mainloop()
    finally:
        eng.stop_hotkey()
        try:
            icon.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
