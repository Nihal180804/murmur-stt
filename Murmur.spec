# PyInstaller spec for Murmur.
# Build with:  pyinstaller Murmur.spec
#
# onedir (not onefile): faster startup and fewer antivirus false positives for
# an ML app. Models are NOT bundled — they download to the HF cache on first use.

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

# faster-whisper and its native/runtime deps that PyInstaller misses by default.
for pkg in ("faster_whisper", "ctranslate2", "av", "onnxruntime", "tokenizers"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += collect_submodules("sounddevice")
hiddenimports += ["numpy", "keyboard", "pystray", "PIL.Image", "PIL.ImageDraw",
                  "tkinter", "tkinter.ttk"]

block_cipher = None

a = Analysis(
    ["murmur.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "tensorflow", "matplotlib", "scipy", "pandas"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Murmur",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # tray app: no console window
    icon="app.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Murmur",
)
