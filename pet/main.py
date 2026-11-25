from __future__ import annotations

import atexit
import ctypes
import logging
import sys
from ctypes import wintypes

INSTANCE_MUTEX = r"Local\pet-Lapland.DesktopInstance"


def claim_instance() -> bool:
    if sys.platform == "darwin":
        import fcntl
        from .config import user_data_dir

        directory = user_data_dir()
        directory.mkdir(parents=True, exist_ok=True)
        lock = (directory / "desktop.lock").open("a+b")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return False
        # Keep the inode in place. Process exit releases the advisory lock.
        atexit.register(lock.close)
        return True

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexExW
    create.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL

    ctypes.set_last_error(0)
    handle = create(None, INSTANCE_MUTEX, 0, 0x00100000)  # SYNCHRONIZE
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:  # ERROR_ALREADY_EXISTS
        close(handle)
        return False
    # Hold the name through shutdown, including any speech worker cleanup.
    atexit.register(close, handle)
    return True


def setup_logging() -> None:
    from .config import LOG_PATH, ensure_dirs

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
    try:
        if not claim_instance():
            return 0
    except OSError:
        setup_logging()
        logging.getLogger("mochi.main").exception("Could not acquire the desktop instance lock")
        return 1

    setup_logging()
    log = logging.getLogger("mochi.main")
    try:
        from .app import MochiApp
        from .config import clean_old_cache, load_config
        from .screen import set_dpi_awareness

        set_dpi_awareness()
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
