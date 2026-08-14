import os
import traceback
from datetime import datetime
from pathlib import Path


PROJECT = Path(__file__).resolve().parent
RUNTIME_LOG = PROJECT / "aura-runtime.log"
ERROR_LOG = PROJECT / "aura-startup-error.log"


def runtime_log(message: str, details: str = "") -> None:
    """Keep a small startup trail even when Aura is launched through pythonw."""
    try:
        if RUNTIME_LOG.exists() and RUNTIME_LOG.stat().st_size > 500_000:
            old = RUNTIME_LOG.read_text(encoding="utf-8", errors="replace")[-200_000:]
            RUNTIME_LOG.write_text(old, encoding="utf-8")
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with RUNTIME_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
            if details:
                handle.write(details.rstrip() + "\n")
    except OSError:
        pass


def main() -> None:
    os.chdir(PROJECT)
    runtime_log("Starting HTML-only localhost interface.")
    from aura.http_app import run
    run()
    runtime_log("HTML-only localhost interface stopped normally.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        details = traceback.format_exc()
        runtime_log("Aura HTML server failed.", details)
        try:
            ERROR_LOG.write_text(details, encoding="utf-8")
        except OSError:
            pass
        if os.name == "nt" and ERROR_LOG.exists():
            try:
                os.startfile(ERROR_LOG)  # type: ignore[attr-defined]
            except OSError:
                pass
        raise
