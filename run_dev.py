"""
Development / manual-test entry point: runs the web UI + folder watcher in
one process, without the Windows service wrapper. This is also essentially
what service_windows.py does inside the actual Windows service - the only
difference in production is the process is managed by the SCM instead of a
terminal.

Usage:
    python3 run_dev.py [--port 8080]
"""
import argparse
import logging
import sys

from waitress import serve

from app import config as config_module
from app.webapp import create_app
from app.watcher import WatcherManager
from app.core.api_poll import ApiPollManager
from app.engine import process_api_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    config_module.load_config()  # ensure config.json exists

    watcher = WatcherManager(config_module.load_config)
    watcher.start()

    api_poller = ApiPollManager(config_module.load_config, process_api_data)
    api_poller.start()

    app = create_app(watcher_manager=watcher, api_poll_manager=api_poller)
    logging.getLogger("motorad.main").info("Serving on http://%s:%s", args.host, args.port)
    try:
        serve(app, host=args.host, port=args.port)
    finally:
        api_poller.stop()
        watcher.stop()


if __name__ == "__main__":
    main()
