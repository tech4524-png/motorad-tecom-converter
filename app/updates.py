"""
Applies an "update package" (a .zip built by the developer) to a running
installation, without a full reinstall.

Package format (a plain zip file):
    manifest.json          {"version": "1.0.1", "description": "..."}
    app/...                files to overwrite under the installed app/ folder

Flow when an admin uploads a package from the Updates screen:
    1. Validate the zip and its manifest; reject if the version is not
       strictly newer than the currently installed version.
    2. Back up the current app/ folder to instance/backups/<timestamp>/.
    3. Copy the package's app/ files over the installed app/ folder.
    4. Record the new version in config.json.
    5. Ask the Windows service to restart so the new code takes effect.

Step 5 needs a real Windows service host to run against (pywin32), which
this Linux development/test environment does not have - restart_service()
degrades gracefully to "log a message telling the admin to restart it by
hand" when pywin32 / the service infrastructure isn't present, which is
exactly what happens if this is ever run outside the packaged Windows EXE.

IMPORTANT - real incident this module caused, and the fix:
The very first packaged build let PyInstaller compile "app" (our own code)
into the EXE's bundle like any other import, instead of shipping it as
loose files. Applying an update in that build silently rewrote the on-disk
templates/static files (real files, so the copy "worked") while the actual
running Python logic - baked into the EXE - never changed, since a frozen
process doesn't re-read .py files from disk. Result: new templates calling
into old (missing) app logic -> 500 errors everywhere.

Fixed in build/pyinstaller.spec (excludes=["app"], plus the sys.path
bootstrap in service_windows.py) so "app" is always real files on disk, in
every future build. is_hot_updatable() below guards against a repeat: it
refuses to apply an update at all against a build old enough to still have
the bug, rather than leaving the install half-updated again.
"""
import json
import logging
import shutil
import threading
import zipfile
from datetime import datetime
from pathlib import Path

from packaging.version import Version, InvalidVersion

from . import config as config_module

logger = logging.getLogger("motorad.updates")

APP_ROOT = Path(__file__).resolve().parent.parent  # the installation root
UPDATES_DIR = config_module.CONFIG_DIR / "updates"
BACKUPS_DIR = config_module.CONFIG_DIR / "backups"

SERVICE_NAME = "MotoradInvoiceConverter"


class UpdateError(ValueError):
    pass


def is_hot_updatable():
    """True only if the running installation ships "app" as real loose
    files (the fixed build). False for the old-style build where "app" was
    compiled into the EXE - applying an update there cannot change the
    running logic and must not be attempted (see module docstring).
    """
    return (APP_ROOT / "app" / "__init__.py").is_file()


def _read_manifest(extract_dir: Path):
    manifest_path = extract_dir / "manifest.json"
    if not manifest_path.exists():
        raise UpdateError("Package is missing manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UpdateError(f"manifest.json is not valid JSON: {exc}") from exc
    if "version" not in manifest:
        raise UpdateError("manifest.json is missing a 'version' field")
    return manifest


def stage_and_validate(uploaded_path: Path, current_version: str):
    """Extracts the uploaded zip to a staging folder and validates it.
    Returns (extract_dir, manifest). Raises UpdateError on any problem.
    """
    if not is_hot_updatable():
        raise UpdateError(
            "This installation was built before in-place updates were supported "
            "(its own code is compiled into the EXE, not shipped as real files). "
            "A one-time full reinstall with the current installer is required "
            "before the Updates screen can be used - after that, updates will "
            "apply normally."
        )

    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    extract_dir = UPDATES_DIR / f"staged_{stamp}"

    try:
        with zipfile.ZipFile(uploaded_path) as zf:
            names = zf.namelist()
            if any(n.startswith("/") or ".." in Path(n).parts for n in names):
                raise UpdateError("Package contains unsafe paths")
            zf.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise UpdateError(f"Not a valid zip file: {exc}") from exc

    manifest = _read_manifest(extract_dir)

    try:
        new_v = Version(str(manifest["version"]))
        cur_v = Version(str(current_version))
    except InvalidVersion as exc:
        raise UpdateError(f"Invalid version string: {exc}") from exc

    if new_v <= cur_v:
        raise UpdateError(
            f"Package version {new_v} is not newer than the installed version {cur_v} - refusing to apply"
        )

    if not (extract_dir / "app").is_dir():
        raise UpdateError("Package does not contain an app/ folder to apply")

    return extract_dir, manifest


def backup_current_app():
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUPS_DIR / f"app_{stamp}"
    shutil.copytree(APP_ROOT / "app", dest)
    return dest


def apply_update(extract_dir: Path, manifest: dict):
    """Backs up the current app/, copies the new files over it, and bumps
    the recorded version in config. Does not restart the service - call
    restart_service() separately so the web request can report progress
    before the process potentially goes away.
    """
    backup_path = backup_current_app()
    logger.info("Backed up current app/ to %s", backup_path)

    src_app = extract_dir / "app"
    dest_app = APP_ROOT / "app"
    for item in src_app.rglob("*"):
        rel = item.relative_to(src_app)
        target = dest_app / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)

    cfg = config_module.load_config()
    cfg["app_version"] = str(manifest["version"])
    config_module.save_config(cfg)

    return backup_path


def can_restart_service():
    """Cheap, safe-to-call-synchronously check for whether an automatic
    restart is even possible here (pywin32 present). Does not touch the
    service - just answers "would restart_service() have a chance of
    working", so the web request can report this to the admin immediately
    without waiting on the restart itself.
    """
    try:
        import win32serviceutil  # noqa: F401 (Windows-only)
        return True
    except ImportError:
        return False


def restart_service():
    """Actually restarts the Windows service. This call blocks until the
    stop completes and the start is issued (a real, if brief, outage) - see
    schedule_restart() for why callers handling an HTTP request should not
    call this directly on the request thread.
    """
    if not can_restart_service():
        logger.warning(
            "pywin32 not available - cannot restart the Windows service automatically. "
            "Please restart the '%s' service manually for the update to take effect.",
            SERVICE_NAME,
        )
        return False
    try:
        import win32serviceutil
        win32serviceutil.RestartService(SERVICE_NAME)
        return True
    except Exception:
        logger.exception("Failed to restart service '%s' automatically", SERVICE_NAME)
        return False


def schedule_restart(delay_seconds=2.0):
    """Restarts the service shortly after returning, instead of on the
    calling thread. Calling win32serviceutil.RestartService(SERVICE_NAME)
    directly from the Flask request thread handling the "apply update" POST
    would race the service's own shutdown against Flask/waitress still
    trying to send that request's HTTP response - the browser could see a
    reset connection instead of the success page. A short delay lets the
    response flush first. The eventual outcome isn't observable from here
    (the process may already be gone by the time it would matter), so this
    only returns whether *scheduling* it succeeded.
    """
    if not can_restart_service():
        return False
    timer = threading.Timer(delay_seconds, restart_service)
    timer.daemon = True
    timer.start()
    return True


def list_backups():
    if not BACKUPS_DIR.exists():
        return []
    return sorted((p.name for p in BACKUPS_DIR.iterdir() if p.is_dir()), reverse=True)
