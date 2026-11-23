from __future__ import annotations

import logging
import sys
from pathlib import Path

from .app import MochiApp
from .config import LOG_PATH, clean_old_cache, ensure_dirs, load_config
from .screen import set_dpi_awareness


def setup_logging() -> None:
    ensure_dirs()
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> int:
    set_dpi_awareness()
    setup_logging()
    log = logging.getLogger("mochi.main")
    try:
        cfg = load_config()
        clean_old_cache()
        app = MochiApp(cfg, start_hidden="--start-hidden" in sys.argv[1:])
        app.run()
        return 0
    except Exception:
        log.exception("pet-Lapland failed to start")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
