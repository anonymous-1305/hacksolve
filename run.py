#!/usr/bin/env python3
"""HackSolve launcher.  python run.py [serve|migrate|seed|check]"""
import argparse
import logging
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

if sys.version_info < (3, 9):
    sys.exit("HackSolve needs Python 3.10 or newer (this is %d.%d). Get it from https://www.python.org/downloads/" % sys.version_info[:2])
for _stream in (sys.stdout, sys.stderr):          # Windows consoles default to a legacy code page
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hacksolve.config import ROOT, Config, load_env_file  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="HackSolve")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "migrate", "seed", "check"])
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    load_env_file(ROOT / ".env")
    cfg = Config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from hacksolve import ai
    from hacksolve.server import App, make_server

    if args.command == "check":
        for p in ai.status(cfg):
            print(f"{p['id']:10} configured={p['configured']!s:5} model={p['model']}")
        print("data dir:", cfg.data_dir)
        return 0
    if args.command == "migrate":
        from hacksolve.db import Database
        print("Schema version:", Database(cfg.db_path).migrate())
        return 0
    if args.command == "seed":
        if cfg.env == "production":
            print("Refusing to seed demo data when APP_ENV=production")
            return 1
        from hacksolve.seed import seed
        print(seed(App(cfg)))
        return 0
    server = make_server(cfg)
    port = server.server_address[1]
    if port != cfg.port:
        print(f"Port {cfg.port} is busy, so HackSolve is using {port} instead.")
    url = f"http://{cfg.host}:{port}"
    if not args.no_browser and cfg.host in ("127.0.0.1", "localhost"):
        def opener():
            for _ in range(20):
                try:
                    urllib.request.urlopen(url + "/api/health", timeout=2)
                    webbrowser.open(url)
                    return
                except Exception:
                    time.sleep(0.2)
        threading.Thread(target=opener, daemon=True).start()
    print(f"HackSolve is running at {url}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.server_close()
        server.app.analysis.pool.shutdown(wait=False, cancel_futures=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
