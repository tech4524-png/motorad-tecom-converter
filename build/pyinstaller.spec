# PyInstaller spec for the Motorad Invoice Converter Windows service.
#
# Build ONCE on a Windows machine with Python + the requirements installed:
#    pyinstaller build/pyinstaller.spec --distpath dist --workpath build/_work --clean
#
# Produces dist/MotoradInvoiceConverter/MotoradInvoiceConverter.exe (a
# one-folder build - simpler and more reliable for pywin32 services than
# --onefile, which has known issues with pywin32's dynamic module loading).
# The Inno Setup script (build/installer.iss) packages this folder into a
# single installer.exe for distribution.
#
# CRITICAL: the "app" package (our own code) is deliberately EXCLUDED from
# the compiled bundle and shipped instead as plain loose files via `datas`.
# This is NOT an oversight - it is what makes the in-app "Updates" screen
# (app/updates.py) actually work: it replaces files under app/ on disk, and
# only a real (non-frozen-into-the-exe) Python package picks up such changes
# on the next service restart. If "app" were compiled in like a normal
# PyInstaller import, update packages would silently have no effect on the
# actual running logic (only on data files) - which is exactly the bug that
# was found and fixed here. Do not remove "app" from `excludes` below, and
# do not move the app.* imports in service_windows.py above its sys.path
# bootstrap.

import sys
from pathlib import Path

block_cipher = None

PROJECT_ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(PROJECT_ROOT / "service_windows.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        # Ship the WHOLE app/ package as real files (code + templates +
        # static + country_codes.json) - see the excludes note above.
        (str(PROJECT_ROOT / "app"), "app"),
    ],
    hiddenimports=[
        "win32timezone",   # common pywin32/PyInstaller gotcha - must be explicit
        "win32serviceutil",
        "servicemanager",
        "waitress",
        # Explicit database drivers needed by app/db.py
        "sqlite3",
        "_sqlite3",
        # These are only imported from within app/*.py, which is excluded
        # from static analysis below (see `excludes`) - so PyInstaller can't
        # auto-discover them by tracing imports. Bundle them explicitly.
        "flask",
        "jinja2",
        "werkzeug",
        "werkzeug.security",
        "lxml.etree",
        "lxml._elementpath",
        "watchdog.observers",
        "watchdog.observers.read_directory_changes",
        "watchdog.events",
        "packaging.version",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["app"],  # see the CRITICAL note above - do not remove
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MotoradInvoiceConverter",
    debug=False,
    strip=False,
    upx=False,
    console=True,  # keep a console for `.exe install/start/stop/remove` output
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="MotoradInvoiceConverter",
)