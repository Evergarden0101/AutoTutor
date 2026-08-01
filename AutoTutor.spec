# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for AutoTutor.

Build with:      pyinstaller AutoTutor.spec --noconfirm
or simply:       python build_exe.py

The resulting executable is self-contained and works without a network
connection: the Open JTalk dictionary (~103 MB) and voice model are bundled,
which is what makes the offline narration possible.  Expect roughly 150-200 MB.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

PROJECT_ROOT = Path(SPECPATH).resolve()

# --- data ------------------------------------------------------------------
datas = [
    # Bundled corpus, keeping the package-relative layout config.data_dir expects.
    (str(PROJECT_ROOT / "autotutor" / "data"), "autotutor/data"),
]

# Open JTalk dictionary, HTS voice and the yomi model must travel with us.
datas += collect_data_files("pyopenjtalk", include_py_files=False)
# pykakasi's fallback dictionaries.
datas += collect_data_files("pykakasi", include_py_files=False)

binaries = collect_dynamic_libs("pyopenjtalk")

hiddenimports = [
    "autotutor",
    "autotutor.ui",
    "autotutor.content",
    "autotutor.tts",
    "pyopenjtalk",
    "pykakasi",
    "numpy",
    "lameenc",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.font",
]

# edge-tts is optional; include it only when it is installed so an
# offline-only build stays small.
try:
    import edge_tts  # noqa: F401

    hiddenimports += ["edge_tts", "aiohttp"]
except ImportError:
    pass

# Things PyInstaller likes to pull in that we never use.
excludes = [
    "matplotlib", "scipy", "pandas", "PIL", "IPython", "notebook",
    "pytest", "setuptools", "pip", "wheel", "sqlite3", "unittest",
    "pydoc_data", "test", "tests", "torch", "sklearn",
]

icon_path = PROJECT_ROOT / "assets" / "autotutor.ico"

a = Analysis(
    # launcher.py, not autotutor/__main__.py: PyInstaller runs the entry script
    # as a top-level module, where relative imports have no parent package.
    [str(PROJECT_ROOT / "launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AutoTutor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # No console window: this is a desktop application.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)
