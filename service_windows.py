"""
Windows Service wrapper. This is what the packaged EXE registers with the
Service Control Manager, so the whole system (web UI + folder watchers)
runs in the background and survives server reboots without anyone needing
to stay logged in or keep a console window open.

This module only imports pywin32 (win32serviceutil, win32service, win32event,
servicemanager) - all Windows-only. It cannot be run or unit-tested on the
Linux machine this project was developed on; it was written carefully against
the standard pywin32 ServiceFramework pattern and exercises the exact same
create_app()/WatcherManager code that IS tested (via run_dev.py) on Linux.
Test it for real on a Windows machine before relying on it in production -
see BUILD_INSTRUCTIONS.md.

IMPORTANT - why the sys.path bootstrap below exists:
PyInstaller normally compiles every pure-Python module your entry point
imports into a bundled archive inside the EXE. If the "app" package were
bundled that way, the in-app update mechanism (app/updates.py) would be
unable to actually change any Python logic: it can only overwrite plain
files on disk, and a restarted frozen process reads its compiled code from
inside the EXE, not from loose .py files sitting next to it. This bit us
for real - see the note in app/updates.py and CHANGELOG.md.

The fix: build/pyinstaller.spec deliberately excludes "app" from the
compiled bundle and instead ships it as plain files (via `datas`) next to
the EXE. For that to import correctly, this file must add the EXE's own
directory to sys.path *before* the first `from app... import` - which is
exactly what this block does. Do not move the app.* imports above this
block, and do not remove "app" from pyinstaller.spec's `excludes`.

Usage (once built into MotoradInvoiceConverter.exe via PyInstaller):
    MotoradInvoiceConverter.exe install     - registers the service
    MotoradInvoiceConverter.exe start       - starts it
    MotoradInvoiceConverter.exe stop        - stops it
    MotoradInvoiceConverter.exe remove      - unregisters it
    (installer.iss runs "install" + "start" automatically after copying files)
"""
import logging
import sys
import threading
from pathlib import Path

if getattr(sys, "frozen", False):
    # Running as the PyInstaller-built EXE: make sure Python finds the real,
    # loose "app" folder shipped beside the executable (see module docstring)
    # instead of failing to import it (it is intentionally NOT compiled in).
    sys.path.insert(0, str(Path(sys.executable).resolve().parent))

try:
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
except ImportError:  # pragma: no cover - expected on non-Windows dev machines
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None

from waitress import serve

from app import config as config_module
from app.webapp import create_app
from app.watcher import WatcherManager

SERVICE_NAME = "MotoradInvoiceConverter"
SERVICE_DISPLAY_NAME = "Motorad Invoice Converter"
SERVICE_DESCRIPTION = (
    "Watches configured folders for Priority invoice exports and converts "
    "them to partner XML formats (TECOM and future formats). Includes the "
    "local admin web UI."
)
HTTP_PORT = 8080


def _configure_logging():
    log_dir = config_module.CONFIG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        filename=str(log_dir / "service.log"),
    )


class MotoradService(win32serviceutil.ServiceFramework if win32serviceutil else object):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.watcher = None
        self.server_thread = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        if self.watcher:
            self.watcher.stop()
        win32event.SetEvent(self.stop_event)

    def SvcDoRun(self):
        _configure_logging()
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        self.main()

    def main(self):
        config_module.load_config()
        self.watcher = WatcherManager(config_module.load_config)
        self.watcher.start()

        app = create_app(watcher_manager=self.watcher)

        def run_server():
            serve(app, host="0.0.0.0", port=HTTP_PORT)

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()

        # Block until SvcStop signals us.
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)


if __name__ == "__main__":
    if win32serviceutil is None:
        print("pywin32 is required to run/install this service (Windows only).")
        sys.exit(1)

    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(MotoradService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(MotoradService)
