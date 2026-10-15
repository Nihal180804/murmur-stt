<p align="center">
  <img src="assets/logo.png" width="112" alt="Murmur logo">
</p>

<h1 align="center">Murmur</h1>

<p align="center">A lightweight, low-RAM speech-to-text app for Windows with a clean desktop UI.</p>

Hold your hotkey (**Right-Alt** by default), speak, release — the transcribed
text is typed straight into whatever window has focus. Murmur lives in the
system tray, has a modern settings window, lets you load any open-source Whisper
model, and can notify you on every dictation.

Powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(CTranslate2). With int8 quantization the memory footprint stays small, and the
model unloads itself when idle so memory ramps up only while you use it.

## Features

- **Push-to-talk dictation** — hold a key, speak, release; types into any app.
- **Modern UI** (customtkinter): live status, recent-transcription history, a
  model manager, and all settings in one window.
- **Any open-source model** — pick a built-in Whisper size, or load a custom
  Hugging Face model id / local CTranslate2 folder.
- **Low idle RAM** — heavy libraries import lazily; the model loads on first use
  and unloads after an idle timeout you choose.
- **Per-dictation notifications** — a toast when it starts listening and when the
  text is ready (toggle in Settings).
- **System tray** — closing the window hides to tray; right-click to reopen/quit.
- **Start with Windows**, **in-app update check**, **one-click installer**.

## Models & RAM

| Model            | Approx. RAM (int8, CPU) | Notes                          |
|------------------|-------------------------|--------------------------------|
| `tiny`           | ~75 MB                  | fastest, lowest accuracy       |
| `base`           | ~150 MB                 | balanced                       |
| `small`          | ~500 MB                 | more accurate, slower on CPU   |
| `medium`         | ~1.5 GB                 | high accuracy                  |
| `large-v3`       | ~3 GB                   | best accuracy                  |
| `distil-small.en` / `distil-large-v3` | lighter English-only, faster |
| *custom*         | varies                  | any HF id or local folder      |

Only one model is resident at a time, and it unloads when idle (Settings →
*Free RAM when idle*), so an idle Murmur isn't holding a model in memory.

## Install (easy path)

1. Install [Inno Setup](https://jrsoftware.org/isdl.php) (free), then double-click
   **`build.bat`** — it builds **`Output\Murmur-Setup.exe`**.
2. Run the installer. No Python needed on the target machine.

Or grab a prebuilt `Murmur-Setup.exe` from the repo's
[Releases](../../releases) once a `v*` tag has been pushed.

## Run from source

1. Install Python 3.9–3.12 (64-bit), "Add to PATH".
2. Double-click **`setup.bat`** (creates `.venv`, installs deps, generates the logo).
3. Double-click **`run.bat`**.

On first launch Murmur picks a model based on your RAM; change it any time in the
**Models** tab. Models download from Hugging Face on first use into
`%USERPROFILE%\.cache\huggingface`.

## Using it

- Hold **Right-Alt**, speak, release. Text types where your cursor is.
- Grey = idle, red = listening, amber = transcribing, blue = loading a model.
- Close the window → hides to tray. Tray → **Open Murmur** / **Quit**.

Settings live in `%APPDATA%\Murmur\config.json` and mirror the Settings tab
(hotkey, activation mode, output, language, idle-unload, notifications, start
with Windows, preload, update checks).

## Build an installer / release

- **Local:** `build.bat` → PyInstaller (onedir) → Inno Setup → `Output\Murmur-Setup.exe`.
- **CI:** push a tag and `.github/workflows/build.yml` builds the installer on a
  Windows runner and attaches it to the GitHub Release:
  ```bash
  git tag v1.1.0
  git push origin v1.1.0
  ```

When releasing, bump `APP_VERSION` in `engine.py` and the version in
`installer.iss` together.

## Troubleshooting

- **Hotkey doesn't fire / can't type into some apps**: the `keyboard` hook and
  synthetic typing don't reach elevated (admin) windows unless Murmur also runs
  elevated. Run as Administrator, or switch **Output → Copy to clipboard**.
- **GPU**: an NVIDIA GPU with CUDA + cuDNN is used automatically (`device: auto`).
- **Right-Alt is AltGr** on some layouts — Murmur matches both; otherwise set the
  hotkey to e.g. `right ctrl` or `f9`.
- **Run-from-source in a very deep folder** can trip Windows' 260-char path limit
  (a bundled DLL fails to load). Keep the project in a short path like
  `C:\Murmur`, or use the installer.
