# Murmur

A lightweight, low-RAM speech-to-text app for Windows. Hold **Right-Alt**,
speak, release, and the transcribed text is typed into whatever window has
focus. Lives in the system tray and lets you switch Whisper models on the fly.

Uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2).
With int8 quantization the model memory footprint is small:

| Model            | Approx. RAM (int8, CPU) | Notes                          |
|------------------|-------------------------|--------------------------------|
| `tiny`           | ~100 MB                 | fastest, lowest accuracy       |
| `base` (default) | ~200 MB                 | good balance                   |
| `small`          | ~500 MB                 | better accuracy, slower on CPU |
| `medium`         | ~1.5 GB                 | high accuracy                  |
| `large-v3`       | ~3 GB                   | best accuracy                  |
| `distil-small.en`/`distil-large-v3` | lighter English-only, faster |

### Low idle RAM

Memory ramps up only while you use it:

- Heavy libraries (faster-whisper, ctranslate2, numpy, sounddevice) are
  imported **lazily** — they don't load until your first dictation. Before that,
  idle RAM is just the small tray process.
- The model loads on **first use** and is **unloaded after an idle period**
  (default 5 min, configurable in the tray under *Free RAM when idle*), freeing
  the model weights back to the OS. The next dictation reloads it (~0.5–2 s).
- Only **one** model is resident at a time.

So a machine sitting idle overnight isn't holding a model in RAM. The tradeoff:
the first dictation after an idle unload pays a short reload cost. Set *Free RAM
when idle → Never* if you'd rather keep it warm.

## Setup

1. Install Python 3.9–3.12 (64-bit) and tick "Add to PATH".
2. Double-click **`setup.bat`** — creates a `.venv` and installs everything.
3. On first launch a small window suggests a model (based on your detected RAM)
   and lets you pick one. It downloads from Hugging Face on first use into
   `%USERPROFILE%\.cache\huggingface`. Change it any time from the tray.

## Run

Double-click **`run.bat`**. A microphone icon appears in the tray:

- **Grey** = idle
- **Red** = recording (while you hold Right-Alt)
- **Amber** = transcribing

Hold **Right-Alt**, speak, release. The text types itself where your cursor is.

### Tray menu

- **Model** — pick any size; it reloads in the background.
- **Activation** — *Hold to talk* (push-to-talk) or *Toggle* (tap on / tap off).
- **Output** — type into the active window, copy to clipboard, or both.
- **Free RAM when idle** — when to unload the model after inactivity.
- **Load model now** / **Unload model now** — warm it up or free RAM manually.
- **Check for updates** — see below.
- **Quit**.

## Configuration

Settings are saved to `%APPDATA%\Murmur\config.json`. Editable keys:

| Key                 | Default       | Meaning                                              |
|---------------------|---------------|------------------------------------------------------|
| `model`             | `base`        | any entry from the model table                       |
| `hotkey`            | `right alt`   | trigger key (e.g. `right ctrl`, `f9`, `ctrl+space`)  |
| `mode`              | `hold`        | `hold` (push-to-talk) or `toggle`                    |
| `output`            | `type`        | `type`, `clipboard`, or `both`                       |
| `language`          | `""`          | `""` = auto-detect, or a code like `en`              |
| `device`            | `auto`        | `auto`, `cpu`, `cuda`                                |
| `compute_type`      | `auto`        | `int8` (CPU) / `float16` (GPU) chosen automatically  |
| `beam_size`         | `1`           | 1 = fastest; higher = slightly better, slower        |
| `min_record_seconds`| `0.3`         | ignore accidental taps shorter than this             |
| `idle_unload_seconds`| `300`        | free the model after this long idle; `0` = never     |
| `preload_on_start`  | `false`       | load the model at launch (higher idle RAM, no first-use wait) |
| `update_repo`       | `""`          | `owner/repo` on GitHub to check releases against     |
| `check_updates_on_start` | `false`  | silently check for a newer release at launch         |

Setting a fixed `language` (e.g. `en`) skips auto-detection and is a bit faster.

## Build a Windows installer

To turn this into a double-click `Setup.exe` with a Start Menu entry, optional
"start with Windows", and an uninstaller:

1. Install [Inno Setup](https://jrsoftware.org/isdl.php) (free).
2. Double-click **`build.bat`**. It:
   - installs deps + PyInstaller into `.venv`,
   - generates `app.ico`,
   - runs PyInstaller (onedir) → `dist\Murmur\Murmur.exe`,
   - runs Inno Setup → **`Output\Murmur-Setup.exe`**.

Notes:
- **onedir, not onefile** — faster startup and fewer antivirus false positives
  for an ML app. Models are *not* bundled; they download on first use, so the
  installer stays ~150–250 MB instead of multiple GB.
- **SmartScreen**: unsigned installers show an "unknown publisher" warning on
  first run (click *More info → Run anyway*). A code-signing certificate removes
  it; optional for personal use.
- Edit the version/publisher in `installer.iss` and the model list / defaults in
  `murmur.py` as you like.

## Releasing + updates

**Automated release builds.** [`.github/workflows/build.yml`](.github/workflows/build.yml)
builds the installer on GitHub's Windows runners. Push a version tag and it
compiles the app, builds `Murmur-Setup.exe`, and attaches it to the
Release automatically:

```bash
git tag v1.0.0
git push origin v1.0.0
```

(You can also trigger it manually from the repo's **Actions** tab to get the
installer as a downloadable artifact without cutting a release.)

**In-app update check.** Set `update_repo` to your `owner/repo` in
`config.json`. Then **Check for updates** in the tray compares the running
`APP_VERSION` against your latest GitHub Release and, if newer, opens the
release page in your browser. Set `check_updates_on_start: true` to check
silently at launch. It never downloads or runs anything on its own — you click
through to the new installer. (Bump `APP_VERSION` in `murmur.py` and the
version in `installer.iss` together when you release.)

## Start automatically with Windows (optional)

Press `Win+R`, type `shell:startup`, Enter, and drop a shortcut to `run.bat`
in that folder.

## Notes & troubleshooting

- **GPU**: if you have an NVIDIA GPU with CUDA + cuDNN installed, it's used
  automatically (`device: auto`), which is much faster. CPU with int8 is the
  low-RAM default.
- **Global hotkey needs permission**: the `keyboard` library installs a
  low-level hook. If the hotkey doesn't fire, run `run.bat` as Administrator
  (some apps, e.g. elevated windows, only receive keystrokes from an elevated
  listener).
- **Auto-type into certain apps**: elevated/admin windows and some games won't
  accept synthetic keystrokes unless Murmur also runs elevated. Switch
  **Output → Copy to clipboard** as a fallback.
- **Right-Alt as AltGr**: on some non-US keyboard layouts Right-Alt is AltGr.
  The app matches both; if it misbehaves, change `hotkey` to e.g. `right ctrl`
  or `f9` in `config.json`.
- **Run-from-source + very long paths**: if you run from `run.bat` inside a
  deeply nested folder, a Windows 260-char path limit can make a bundled DLL
  (e.g. Pillow's `_imaging`) fail to load. Keep the project in a short path
  (e.g. `C:\Murmur` or `Documents\Murmur`), or just use the installer — the
  installed app lives under a short path and isn't affected.
