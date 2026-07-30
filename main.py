"""Moveshelf Report Tracker entry point.

Starts a loopback web server, opens the default browser, and waits. Everything
the therapist sees is in the browser; this console window only reports what is
happening and stays open so an error is readable rather than flashing past.
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

from tracker import APP_NAME, __version__
from tracker.config import app_dir
from tracker.server import AppState, build_server, start_idle_watchdog


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # The SDK is chatty at INFO and drowns out anything useful.
    logging.getLogger("moveshelf_api").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--port", type=int, default=0,
                        help="Port to bind on 127.0.0.1. Default asks the OS for a free one.")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open a browser. Prints the URL instead.")
    parser.add_argument("--verbose", action="store_true", help="Debug logging.")
    parser.add_argument("--folder", default=None,
                        help="Folder holding the key and settings. Defaults to the "
                             "folder containing the app. Useful for testing and support.")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    # A frozen build buffers stdout whenever it is not attached to a console,
    # so anything printed here (including the address to open) can sit unseen in
    # a buffer. Line buffering makes the output usable when piped or logged.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    folder = Path(args.folder).expanduser().resolve() if args.folder else app_dir()
    print(f"{APP_NAME} {__version__}")
    print(f"Folder: {folder}")

    state = AppState(folder)
    try:
        httpd, token, port = build_server(state, args.port)
    except OSError as exc:
        print(f"\nCould not start the local service: {exc}")
        print("If a port was specified with --port, try leaving it off.")
        return 1

    url = f"http://127.0.0.1:{port}/?t={token}"

    # Fetch in the background so the browser opens immediately rather than
    # waiting on the first API round trip.
    threading.Thread(target=state.refresh, daemon=True).start()
    start_idle_watchdog(httpd, state)

    print(f"Serving on 127.0.0.1:{port} (this machine only)")
    if args.no_browser:
        print(f"\nOpen this address in your browser:\n  {url}\n")
    else:
        print("Opening your browser…")
        try:
            webbrowser.open(url)
        except webbrowser.Error as exc:
            print(f"Could not open a browser automatically ({exc}).")
            print(f"Open this address yourself:\n  {url}\n")

    print("Close this window, or click Quit in the browser, to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
